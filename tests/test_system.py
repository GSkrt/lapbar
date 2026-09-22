"""tool() must never let a bare command name be resolved by a $PATH search when Omarchy's own copy is right
there, since that search is exactly what a malicious binary earlier in $PATH would exploit.
"""
import os
import stat

from lapbar import system


def make_executable(path):
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_prefers_the_system_location_over_a_path_search(monkeypatch, tmp_path):
    real = tmp_path / "usr_bin" / "secret-tool"
    real.parent.mkdir()
    make_executable(real)
    monkeypatch.setattr(system, "SYSTEM_DIR", str(tmp_path / "usr_bin"))
    assert system.tool("secret-tool") == str(real)


def test_a_path_hijack_attempt_is_ignored_when_the_real_tool_is_in_its_usual_place(monkeypatch, tmp_path):
    """The actual attack this defends against: a malicious binary of the same name, earlier in $PATH, must lose."""
    real_dir = tmp_path / "usr_bin"
    real_dir.mkdir()
    make_executable(real_dir / "secret-tool")

    evil_dir = tmp_path / "evil_early_in_path"
    evil_dir.mkdir()
    make_executable(evil_dir / "secret-tool")

    monkeypatch.setattr(system, "SYSTEM_DIR", str(real_dir))
    monkeypatch.setenv("PATH", f"{evil_dir}:{os.environ.get('PATH', '')}")
    resolved = system.tool("secret-tool")
    assert resolved == str(real_dir / "secret-tool")
    assert resolved != str(evil_dir / "secret-tool")


def test_falls_back_to_a_path_search_when_the_system_location_does_not_have_it(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "SYSTEM_DIR", str(tmp_path / "nowhere"))
    only_dir = tmp_path / "only_here"
    only_dir.mkdir()
    make_executable(only_dir / "quickshell")
    monkeypatch.setenv("PATH", str(only_dir))
    assert system.tool("quickshell") == str(only_dir / "quickshell")


def test_a_tool_that_exists_nowhere_returns_its_bare_name_so_the_caller_still_gets_a_clear_not_found_error(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "SYSTEM_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("PATH", str(tmp_path / "also-nowhere"))
    assert system.tool("no-such-tool-anywhere") == "no-such-tool-anywhere"


def test_every_bare_external_tool_name_in_the_codebase_now_goes_through_tool(monkeypatch, tmp_path):
    """Regression guard: a new subprocess call that reintroduces a bare, unresolved tool name should fail this."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in (root / "lapbar").glob("*.py"):
        if path.name == "system.py":
            continue
        text = path.read_text()
        for name in ("secret-tool", "notify-send", "hyprctl", "quickshell", "xdg-open", "zenity", "kdialog", "yad"):
            for quote in ('"', "'"):
                literal = f"{quote}{name}{quote}"
                if literal in text and f"system.tool({literal})" not in text:
                    # shutil.which(name) as an existence check (not the command actually run) is fine.
                    for line in text.splitlines():
                        if literal in line and "shutil.which(" not in line and f"system.tool({literal})" not in line:
                            offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []
