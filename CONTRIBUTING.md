# Contributing to LapBar

Thanks for wanting to improve LapBar. Contributions of every size are welcome: bug reports, a fix for a sport
I do not do myself, better wording in the setup, a new panel in the popup.

LapBar is meant to work for **runners, swimmers and everyone else, not only cyclists**. If something looks
wrong for your sport, that is a bug, and real (anonymised) numbers from your activities are the most useful
thing you can bring.

## Before you start

- For anything bigger than a small fix, **open an issue first** so we can agree on the shape before you spend
  time on it.
- Keep within [Strava's API Agreement](https://www.strava.com/legal/api) and brand guidelines. When unsure
  whether something is allowed, ask in the issue.
- LapBar is not affiliated with Strava.

## Setting up

You need Omarchy (Quickshell), Python 3.11+, `node` (for the chart logic tests) and `pytest`.

    git clone https://github.com/GSkrt/lapbar
    cd lapbar
    python -m venv ~/.local/share/lapbar-dev/venv      # outside the repo: the plugin folder must contain no symlinks
    ~/.local/share/lapbar-dev/venv/bin/pip install pytest
    ~/.local/share/lapbar-dev/venv/bin/python -m pytest

    scripts/dev-sync.sh          # copy the repo into ~/.config/omarchy/plugins/ as a real folder
    omarchy restart shell        # needed after every change to Panel.qml or manifest.json

Try the chart window without the bar: `lapbar charts <activity id>`. See the README's Development section and
"Notes for widget authors" for the traps I already fell into (a property called `data`, symlinked plugin
folders, hot reload not updating a mounted widget).

## Tests

- **Every change comes with a test** where a test is possible. The suite is fast (a few seconds) and
  hermetic: an autouse fixture replaces the keyring and blocks the network, so tests can never touch your real
  credentials or call Strava. Do not work around that.
- Provider changes are tested with payloads shaped like Strava's. For a sport you do, a fixture based on a
  real activity (with names, coordinates and ids removed) is ideal.
- Chart logic lives in `charts/logic.js` and is tested under `node` (`tests/test_charts_logic.py`).
- QML you cannot unit test: describe how you checked it, and attach a screenshot.

Continuous integration runs the same tests on every pull request.

## Ground rules

- **Never commit secrets or personal data**: no Client Secret, tokens, `.env` files, real activity JSON, and no
  screenshots showing your route near home or your name. If you think you leaked something, say so in the PR so
  it can be removed from history before it is merged.
- **Secrets stay in the keyring.** Nothing secret goes into the cache, logs, terminal output or the plain-text
  config file. `tests/test_vault.py` guards this; extend it if you touch storage.
- **Do not add dependencies** without discussing it. The Python side is standard library only on purpose, and
  the chart window draws with Qt Quick's own `Canvas`.
- **Match the surrounding code**: naming, comment density, idiom. Comments say *why*, not what.
- Small, focused pull requests are reviewed and merged faster than large ones.

## Licensing of contributions

LapBar is licensed under the **GNU General Public License v3.0 or later** (see `LICENSE`). By contributing you
agree that your contribution is released under the same license, and you keep the copyright to your own work.

Please sign off your commits to say you have the right to contribute them, following the
[Developer Certificate of Origin](https://developercertificate.org/):

    git commit -s -m "Fix pace for swims longer than an hour"

which adds a `Signed-off-by: Your Name <you@example.com>` line.
