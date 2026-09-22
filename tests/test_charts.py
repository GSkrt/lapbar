import os
import subprocess
from pathlib import Path

from lapbar import charts


def test_window_is_centred_and_sized_to_the_screen():
    w, h, x, y = charts.window_geometry({"width": 1920, "height": 1080, "scale": 1, "x": 0, "y": 0})
    assert (w, h) == (1240, 820) and x == (1920 - 1240) // 2 and y == (1080 - 820) // 2


def test_window_shrinks_on_small_and_scaled_screens():
    w, h, x, y = charts.window_geometry({"width": 2880, "height": 1800, "scale": 2, "x": 0, "y": 0})   # 1440x900 logical
    assert (w, h) == (1240, 810)
    w, h, _, _ = charts.window_geometry({"width": 1280, "height": 720, "scale": 1, "x": 0, "y": 0})
    assert w == int(1280 * 0.9) and h == int(720 * 0.9)


def test_window_is_placed_on_the_focused_monitor():
    _, _, x, _ = charts.window_geometry({"width": 1920, "height": 1080, "scale": 1, "x": 1920, "y": 0})
    assert x > 1920


def fake_clients(monkeypatch, appear_after=1):
    clients = iter([[]] * appear_after + [[{"pid": 99}]] * 5)

    def fake_json(*args):
        if args[0] == "clients":
            return next(clients)
        return [{"focused": True, "width": 1920, "height": 1080, "scale": 1, "x": 0, "y": 0}]

    monkeypatch.setattr(charts, "_json", fake_json)


def test_floating_uses_hyprlands_lua_api_by_pid(monkeypatch):
    fake_clients(monkeypatch)
    lua = []
    monkeypatch.setattr(charts, "_lua", lambda code: lua.append(code) or True)
    monkeypatch.setattr(charts, "_hyprctl", lambda *a: (_ for _ in ()).throw(AssertionError("classic syntax used")))
    assert charts.float_window(99, wait=5, sleep=lambda s: None) is True
    joined = "\n".join(lua)
    assert 'hl.dsp.window.float({ action = "enable", window = "pid:99" })' in joined
    assert 'hl.dsp.window.resize({ x = 1240, y = 820, window = "pid:99" })' in joined
    assert 'hl.dsp.window.move({ x = 340, y = 130, window = "pid:99" })' in joined
    assert '"-default-opacity"' in joined
    assert 'set_prop({ prop = "opaque", value = "true", window = "pid:99" })' in joined   # what actually makes it opaque


def test_floating_falls_back_to_classic_dispatch_on_older_hyprland(monkeypatch):
    fake_clients(monkeypatch)
    calls = []
    monkeypatch.setattr(charts, "_lua", lambda code: False)      # no `hyprctl eval`
    monkeypatch.setattr(charts, "_hyprctl", lambda *a: calls.append(a))
    assert charts.float_window(99, wait=5, sleep=lambda s: None) is True
    batch = calls[0][1]
    assert "setfloating pid:99" in batch and "resizewindowpixel exact 1240 820,pid:99" in batch
    assert "movewindowpixel exact 340 130,pid:99" in batch


def test_lua_helper_only_trusts_an_ok_reply(monkeypatch):
    monkeypatch.setattr(charts, "_hyprctl", lambda *a: "ok\n")
    assert charts._lua("x") is True
    for reply in (None, "", "error: nope"):
        monkeypatch.setattr(charts, "_hyprctl", lambda *a, r=reply: r)
        assert charts._lua("x") is False


def test_floating_gives_up_quietly_when_the_window_never_appears(monkeypatch):
    monkeypatch.setattr(charts, "_json", lambda *a: [])
    assert charts.float_window(99, wait=0.05, sleep=lambda s: None) is False


def test_missing_hyprctl_is_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, "run", boom)
    assert charts._hyprctl("clients", "-j") is None


def test_the_window_process_gets_the_data_path_and_theme_and_outlives_the_command(monkeypatch, tmp_path):
    seen = {}

    class FakeProc:
        pid = 4242

    def popen(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(charts, "float_window", lambda pid, **kw: seen.update(floated=pid))
    pid = charts.open_window(tmp_path / "1.json", {"LAPBAR_FG": "#fff"})
    assert pid == 4242 and seen["floated"] == 4242
    assert os.path.basename(seen["cmd"][0]) == "quickshell" and seen["cmd"][1] == "-p" and seen["cmd"][2].endswith("charts")
    assert seen["env"]["LAPBAR_CHART_FILE"].endswith("1.json") and seen["env"]["LAPBAR_FG"] == "#fff"
    assert seen["start_new_session"] is True


def test_the_window_is_told_where_the_logos_are_because_quickshell_only_loads_images_from_its_own_folder(monkeypatch, tmp_path):
    seen = {}

    class FakeProc:
        pid = 1

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or FakeProc())
    monkeypatch.setattr(charts, "float_window", lambda pid, **kw: None)
    charts.open_window(tmp_path / "1.json")
    assets = Path(seen["env"]["LAPBAR_ASSETS"])
    assert assets.is_absolute() and (assets / "strava" / "api_logo_pwrdBy_strava_horiz_white.svg").is_file()
    assert (assets / "logo" / "lapbar_horiz_white.svg").is_file()


def test_the_chart_windows_title_is_never_rendered_as_markup():
    # The header shows the activity's own title the same way Panel.qml's does; Qt's default text rendering
    # (Text.AutoText) auto-detects and interprets anything that looks like HTML, so this must say PlainText
    # explicitly, the same as every other place LapBar shows a Strava-echoed name.
    shell = (Path(__file__).resolve().parent.parent / "charts" / "shell.qml").read_text()
    header = shell[:shell.index("text: win.doc ? win.doc.name")]
    assert "textFormat: Text.PlainText" in header[-200:]
