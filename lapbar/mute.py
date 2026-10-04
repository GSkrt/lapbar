"""Persistent mute switch for kudos and comment alerts (a flag file, so it survives shell restarts)."""
import os

from . import config


def _flag():
    return config.state_dir() / "kudos-muted"


def is_muted() -> bool:
    return _flag().exists()


def set_muted(muted: bool) -> bool:
    flag = _flag()
    if muted:
        config.private_dir(flag.parent)
        os.close(os.open(flag, os.O_WRONLY | os.O_CREAT, 0o600))
    else:
        flag.unlink(missing_ok=True)
    return muted
