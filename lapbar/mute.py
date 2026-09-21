"""Persistent mute switch for kudos and comment alerts (a flag file, so it survives shell restarts)."""
from . import config


def _flag():
    return config.state_dir() / "kudos-muted"


def is_muted() -> bool:
    return _flag().exists()


def set_muted(muted: bool) -> bool:
    flag = _flag()
    if muted:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    else:
        flag.unlink(missing_ok=True)
    return muted
