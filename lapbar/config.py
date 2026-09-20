"""Paths and credential loading."""
import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "lapbar"


def cache_path() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "lapbar" / "cache.json"


def user_env_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "lapbar" / "env"


def private_dir(path: Path) -> Path:
    """Create a directory only the current user can enter."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def write_env_file(path: Path, values: dict[str, str], remove: list[str] = ()) -> None:
    """Update KEY=value lines in place (keeping comments and other keys); file mode 0600."""
    lines = path.read_text().splitlines() if path.is_file() else []
    out, done = [], set()
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in remove:
            continue
        if key in values and "=" in line and not line.lstrip().startswith("#"):
            out.append(f"{key}={values[key]}")
            done.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in done]
    private_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out) + "\n")
    tmp.replace(path)


def get(name: str) -> str:
    """Look up a setting: the environment first, then ~/.config/lapbar/env.

    Only non-secret settings (the Client ID) are kept in that file; secrets live in the vault.
    """
    if os.environ.get(name):
        return os.environ[name]
    for path in (user_env_path(),):
        value = read_env_file(path).get(name)
        if value:
            return value
    return ""
