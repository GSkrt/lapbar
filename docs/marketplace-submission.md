# Omarchy plugin marketplace submission

The text to paste (or send with `gh issue create`) when LapBar is submitted to
[omacom/omarchy-plugin-marketplace](https://github.com/omacom/omarchy-plugin-marketplace). The rules are in that
repository's `SUBMISSION.md`, `VERIFICATION.md` and `SECURITY.md`. Update this file with each release.

## Before submitting

- [ ] The repository is **public** (`https://github.com/GSkrt/lapbar`). It is private until the owner flips it.
- [x] `manifest.json` in the root: unique id `io.github.gskrt.lapbar`, description, homepage, license.
- [x] Root `README.md` with installation ("Getting started") and removal ("Removing LapBar").
- [x] Root `LICENSE` (GPL-3.0-or-later) and the dependencies stated in the README (none to install).
- [x] Root `preview.png`, a screenshot of the popup with no third-party names in it.
- [ ] Ask the owner to confirm the ownership statement and every checklist item (the marketplace's own rule).
- [ ] After a change to the repository, edit the issue so the validation runs again on the new commit.

## Listing

- **Title:** `[Plugin]: LapBar`
- **Repository URL:** `https://github.com/GSkrt/lapbar`
- **Category:** `Widgets`
- **Tags:** `bar, quickshell`
- **Suggested tag:** `fitness`

**Listing text** (the manifest's `description`: plain text, at most 500 characters, shown on the card and the detail page):

> Your Strava in the Omarchy bar: the latest activity with route, stats, kudos and records by name, totals per sport
> for the day, week, month and year, a calendar of all your years, and fitness, fatigue and form worked out on your
> own computer, with optional motivational nudges. Needs a paid Strava subscription (Strava requires one to create an
> API app) and your own free API app; a guided setup walks you through it. Credentials stay in your keyring; nothing
> is sent anywhere but to Strava.

## Issue body

```markdown
### Repository URL

https://github.com/GSkrt/lapbar

### Category

Widgets

### Tags

bar, quickshell

### Suggest a missing tag

fitness

### Maintainer notes

LapBar shows your own Strava activity in the bar: the latest activity, kudos and records, totals, a calendar,
charts and fitness/fatigue/form. It is a bar widget (`Panel.qml`) plus a Python command-line tool (`bin/lapbar`).

**Dependencies:** none to install. Python 3.11+ standard library only (no pip, no virtualenv), plus programs Omarchy
already has: `quickshell`, `notify-send`, `hyprctl` and `secret-tool` (for the keyring; a private 0600 file is the
fallback). There is no install script, no build step and no compiled or bundled binary. It needs no root rights, adds
no sudoers rule and no systemd unit, and the README does not ask for any.

**Network:** only https://www.strava.com (the API and OAuth). Every call is read-only and is listed in the README
("Which Strava API calls LapBar makes") and in `lapbar/stravaapi.py`, and a monthly workflow compares the list with
Strava's published spec. During the one-time sign-in it listens on `127.0.0.1:8734` for the OAuth redirect and stops
afterwards. Each user creates their own Strava API app (the guided `lapbar setup` walks through it).

**Credentials:** the client secret and tokens go to the system keyring through `secret-tool`. They are never written
to the cache, to logs or to the config file.

**Files it writes:** only its own folders: `~/.config/lapbar`, `~/.cache/lapbar`, `~/.local/share/lapbar` and
`~/.local/state/lapbar`. The one exception is the widget's own settings (refresh interval, FTP), written with
`omarchy bar set` only when the user changes them from the widget's menu. Nothing is overwritten without the user
asking. `lapbar reset` (and `--all`) and `omarchy plugin remove io.github.gskrt.lapbar` remove everything (see
"Removing LapBar" in the README).

**Processes it starts:** `quickshell -p <folder inside the plugin>` for its own chart, data and help windows,
`notify-send` for notifications, and the plugin's own `bin/lapbar` in the background. It starts nothing else.

**Not affiliated with Strava.** Strava's "Powered by Strava" logo is used unmodified, as their brand guidelines ask.
LapBar needs a Strava account with an active subscription to create the API app (the README explains why).

### Submission checklist

- [x] The repository is public and contains installation and removal instructions.
- [x] I have documented the plugin license and any external dependencies.
- [x] I confirm that I own or have permission to submit this plugin and its preview assets.
- [x] The plugin does not overwrite user configuration without explicit consent.
- [x] I understand that approval is for listing and is not a security review.
```

## What the automated checks will probably say

The security baseline reads files statically and never runs code. Checked by hand against the documented patterns:

- No `curl | sh`, no unpinned `git` source, no sudoers rule, no `/tmp` PID file: none of the blocking findings apply.
- No `sudo`/`pkexec`, no `systemctl`, no package-manager command and no binary in the repository (the two
  install hints that used to say `sudo pacman -S` now say `omarchy pkg add`).
- `lapbar/setup.py` has "setup" in its name, which the scanner treats as an installer candidate, and it is
  not one: it is the guided sign-in for the Strava app. If the report lists an `installer` capability for it, that
  is the explanation, and renaming the file would remove it.
- The README says `omarchy plugin add <repository>`, which the scanner may list as `remote-build` ("installs from
  the submitted repository"). That is Omarchy's own install path, the same as every plugin.

The validation comment can only be checked after the issue is opened, so re-read it and fix anything it lists in a
new commit rather than arguing with it.
