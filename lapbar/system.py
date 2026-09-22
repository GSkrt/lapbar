"""Resolving the external programs LapBar shells out to: prefer the system's own copy over whatever a PATH
search would turn up first.

Every subprocess call in this codebase passes an argument list, never `shell=True`, so none of them is
vulnerable to shell metacharacter injection. But a bare command name in that list -- `["secret-tool", ...]` -- is
still resolved by searching $PATH at exec time, the same way a shell would (Python does this through
`posix_spawnp`/`execvp`). That means anything placed earlier in $PATH than the real system binary would run
instead, silently: a stray writable directory ahead of `/usr/bin` in $PATH, or a same-named file dropped into
`~/.local/bin`, are both real (if not universal) Linux misconfigurations, not exotic ones. `secret-tool` is
trusted with the Strava client secret and the sign-in tokens, so a malicious one earlier in $PATH could read and
rewrite both; `notify-send` and `xdg-open` decide what a popup says and what a click opens.

Every one of these tools is a normal part of the base Omarchy install, installed and verified by pacman like
everything else under /usr/bin (Arch is usr-merged, so /usr/bin is the one real location). `tool()` checks that
exact path first and uses it whenever it is there -- which is every real Omarchy install -- so $PATH order never
enters into it at all. It only falls back to an ordinary $PATH search if that specific tool turns out not to be
where Omarchy puts it, so a call still works on a less usual setup rather than failing outright.
"""
import shutil

SYSTEM_DIR = "/usr/bin"


def tool(name: str) -> str:
    """The absolute path to run `name` at: its normal Omarchy location if it is there, else a $PATH search."""
    system_path = f"{SYSTEM_DIR}/{name}"
    return system_path if shutil.which(system_path) else (shutil.which(name) or name)
