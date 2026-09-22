"""private_dir() must lock down every directory it creates, not only the deepest one.

`Path.mkdir(parents=True, mode=...)` applies its `mode` only to the final component it creates; any intermediate
parent it has to make along the way gets the default, umask-derived mode instead. Left unfixed, the first-ever
write under a fresh XDG base (e.g. the very first activity a new install archives) would leave directories such as
~/.local/share/lapbar and ~/.local/share/lapbar/raw -- which hold the complete GPS routes -- at whatever the
umask allowed, even though the deepest folder came out correctly locked down.
"""
import os

import pytest

from lapbar import config


@pytest.fixture(autouse=True)
def loose_umask():
    """A permissive umask (022): the failure mode only shows up when the default would otherwise leak."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_every_level_created_along_the_way_is_locked_down(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    target = config.data_dir() / "raw" / "2026"          # three new levels: lapbar/, raw/, 2026/
    config.private_dir(target)
    for level in (tmp_path / "share" / "lapbar", tmp_path / "share" / "lapbar" / "raw", target):
        assert oct(os.stat(level).st_mode & 0o777) == "0o700", level


def test_the_shared_xdg_base_itself_is_never_touched(tmp_path, monkeypatch):
    base = tmp_path / "share"
    base.mkdir(mode=0o755)
    monkeypatch.setenv("XDG_DATA_HOME", str(base))
    config.private_dir(config.data_dir() / "details")
    assert oct(os.stat(base).st_mode & 0o777) == "0o755"                    # other apps live here too


def test_an_already_existing_top_level_directory_from_before_this_fix_is_repaired(tmp_path, monkeypatch):
    base = tmp_path / "share"
    top = base / "lapbar"
    top.mkdir(parents=True, mode=0o755)
    os.chmod(top, 0o755)                                                    # simulate the pre-fix leftover state
    monkeypatch.setenv("XDG_DATA_HOME", str(base))
    config.private_dir(config.data_dir() / "history")
    assert oct(os.stat(top).st_mode & 0o777) == "0o700"


def test_each_of_lapbars_own_roots_is_repaired_regardless_of_which_one_a_call_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for top in (config.data_dir(), config.cache_path().parent, config.state_dir(), config.user_env_path().parent):
        top.mkdir(parents=True, mode=0o755)
        os.chmod(top, 0o755)
    config.private_dir(config.data_dir() / "raw")
    config.private_dir(config.cache_path().parent / "streams")
    config.private_dir(config.state_dir())
    config.private_dir(config.user_env_path().parent)
    for top in (config.data_dir(), config.cache_path().parent, config.state_dir(), config.user_env_path().parent):
        assert oct(os.stat(top).st_mode & 0o777) == "0o700", top


def test_a_custom_lapbar_data_dir_is_secured_without_reaching_above_it(tmp_path, monkeypatch):
    outside = tmp_path / "external-drive"          # stands in for a mount point LapBar must never touch
    outside.mkdir(mode=0o755)
    monkeypatch.setenv("LAPBAR_DATA_DIR", str(outside / "lapbar-data"))
    config.private_dir(config.data_dir() / "raw" / "2026")
    assert oct(os.stat(config.data_dir()).st_mode & 0o777) == "0o700"
    assert oct(os.stat(outside).st_mode & 0o777) == "0o755"               # untouched: not LapBar's to secure


def test_already_correct_permissions_are_left_alone_without_erroring(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    target = config.data_dir() / "details"
    config.private_dir(target)
    config.private_dir(target)                                            # idempotent: called again, no crash
    assert oct(os.stat(target).st_mode & 0o777) == "0o700"
