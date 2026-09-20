<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/lapbar_horiz_white.svg">
    <img alt="LapBar" src="assets/logo/lapbar_horiz_black.svg" height="56">
  </picture>
</h1>

A Quickshell status bar widget for Omarchy that shows your Strava activity in the bar: the latest
activity, totals for today, this week, this month and this year (per sport, with total climb), a route trace, kudos and PRs (with a notification and who
gave them), a calendar of active days, and how this week compares with last week.

Not affiliated with or endorsed by Strava.

## Why LapBar?

**Lap** is one loop of a ride, a run or a swim. Every sport has laps, so the name is about no pace, no speed and
no single activity, and it suits cyclists, runners, swimmers and walkers alike. "One more lap" is also what LapBar
nudges you towards: it shows what you did the last time you got up and moved, and (over time) encourages you to do
it again.

**Bar** is where it lives today: the Omarchy status bar. The Strava sign-in, request budgeting and fitness
calculations run as a separate command-line tool (`lapbar`), so nothing about the name ties the project to one
desktop.

Names it deliberately is not:

- **Not "...Strava".** Strava's brand guidelines say you must not use their name in an app's name, so the
  connection is shown the way they ask instead: the "Powered by Strava" logo, and "View on Strava" links.
  LapBar is not affiliated with or endorsed by Strava.
- **Not "pace..." or "AFK...".** Earlier working names, *pacebar* and *AFKbar*, were dropped: pace leans towards
  runners (and clashed with an existing Mac app), and AFK reads as a gaming status.

It is written in lowercase (`lapbar`) for the command, the plugin id and file paths, and **LapBar** in prose.

## Getting started

About five minutes, most of it creating your Strava app.

**You need:** a Strava account **with an active subscription** (see [the subscription question](#do-i-need-a-paid-strava-subscription)),
Omarchy, and Python 3.11+ (already on Omarchy). No pip packages are required.

### 1. Install the plugin

    omarchy plugin add <repository-url> --enable

(The repository address will be added here when LapBar is published.) The bar now shows a
**LapBar: set up** button.

### 2. Run the guided setup

Click the button and choose **Start setup**. A terminal opens and walks you through everything; the same
steps are listed below in case you prefer to do it by hand. You can also run it yourself:

    ~/.config/omarchy/plugins/io.github.gskrt.lapbar/bin/lapbar setup

### 3. What the setup asks you to do

Strava does not give out a shared key. Instead, every user registers a small **private app** under their
own account, which takes about two minutes and means your data never passes through anyone else.

1. Open **https://www.strava.com/settings/api** (log in if asked).
2. Fill in the form:

   | Field | Enter |
   |---|---|
   | Application Name | `lapbar` (anything you like) |
   | Category | `Visualizer` |
   | Club | leave empty |
   | Website | `http://localhost` |
   | Application Description | `Personal status bar widget` |
   | **Authorization Callback Domain** | **`localhost`**, exactly this: no `http://`, no port |
   | Icon | Strava asks for an image. Any picture works; `assets/icon.png` in this repository is provided |

3. Click **Create**. Strava now shows your **Client ID** and **Client Secret** (click *Show*).
4. Back in the setup terminal, paste them when asked:

   | Paste this | Where | Careful |
   |---|---|---|
   | **Client ID** (a number, about 5-6 digits) | first prompt | |
   | **Client Secret** (40 letters and digits) | second prompt, typing stays hidden | **Not** the *Access Token* or *Refresh Token* shown lower on the same page: those only have `read` access, which cannot see activities. You never paste a token; step 5 (Authorize) makes the right ones and LapBar stores them in the keyring |

5. Your browser opens Strava's authorization page. Click **Authorize** and leave every permission ticked
   (LapBar needs to read your activities).
6. Done. Within a few seconds the bar shows your latest activity. If the widget is not in the bar yet:
   `omarchy plugin enable io.github.gskrt.lapbar`.

### Do I need a paid Strava subscription?

**Yes, to create the app.** Strava's own documentation says: *"A Strava subscription is a prerequisite for
creating an app."* ([Getting started](https://developers.strava.com/docs/getting-started/)). Strava's
API FAQ adds that the subscription gives you API access *"there is no additional fee"*
([FAQ](https://communityhub.strava.com/developers-knowledge-base-14/strava-api-faq-12906)), and their
announcement says Standard Tier developers *"will need a Strava subscription to keep API access"* from
30 June 2026 ([announcement](https://communityhub.strava.com/developers-api-7/new-strava-api-update-what-the-message-means-13433)).

What Strava has **not** said, as far as I can find: a price for this purpose, or what happens to an
existing single-user app if the subscription later lapses. Check Strava's pages for the current rules;
they change. LapBar itself is free and asks for nothing else.

### What the "own app" model means for you

- **The app is private to you.** A new Strava app starts in *single-player mode*: only the account that
  created it can sign in. That is exactly what LapBar needs. Two people on one computer each need their
  own app.
- **Rate limits are yours alone.** A new app gets 200 requests per 15 minutes and 2,000 per day overall
  (100 and 1,000 for reads). LapBar uses roughly 2-5 requests per refresh, every 15 minutes by default.
- **Nobody else sees your data.** LapBar talks only to `strava.com` from your own machine.

## Where your data and secrets live

| What | Where | Protection |
|---|---|---|
| Client Secret, sign-in tokens | your **system keyring** (GNOME Keyring / libsecret) | encrypted by the keyring, unlocked with your login |
| ... if there is no keyring | `~/.local/state/lapbar/secrets.json` | mode 600 in a mode 700 folder; the setup tells you when this is used |
| Client ID (not secret) | `~/.config/lapbar/env` | mode 600 |
| Cached activities (route shapes, names of people who gave kudos) | `~/.cache/lapbar/cache.json` | mode 600 |
| Downloaded time series (heart rate, power, speed... per km) | `~/.cache/lapbar/streams/` | mode 600 in a mode 700 folder |

Secrets are never written to the cache, to logs, or to the terminal. Older plain-text files from earlier
versions are moved into the keyring automatically and then deleted. Set `LAPBAR_NO_KEYRING=1` to use the
private file instead of the keyring.

To remove everything again (credentials, sign-in, cache and downloaded charts): `lapbar reset` (add `--yes` to skip the question), then
`omarchy plugin remove io.github.gskrt.lapbar`. You can also delete the app on Strava's API settings page.

## Troubleshooting

| You see | Cause and fix |
|---|---|
| The Strava page after clicking Authorize says the redirect is invalid, or setup times out | The **Authorization Callback Domain** is not exactly `localhost`. Fix it at strava.com/settings/api |
| "Strava rejected the Client ID / Client Secret" | You pasted an *Access Token* or *Refresh Token* instead of the Client Secret, or the ID belongs to another app. Click *Show* next to **Client Secret** |
| "Missing 'activity:read_all' permission" | You unticked a box on Strava's page. Run `lapbar setup` again and leave every box ticked |
| "Port 8734 is already in use" | Another setup is still open. Close it and retry |
| "Cannot reach your keyring" | The keyring is locked or not running. Log in again (or unlock it) and refresh. `lapbar status` shows details |
| Widget stays on "set up" | Run `lapbar status` and `lapbar fetch --print` in a terminal to see the exact message |
| "rate limit" | Strava's limits were hit; LapBar keeps showing the last data and retries |

## Planned: official Strava registration

Right now everyone creates their own Strava app, which is a few extra steps. My plan is to apply to
[Strava's Developer Program](https://share.hsforms.com/1VXSwPUYqSH6IxK0y51FjHwcnkd8) once LapBar has a public
release and some real-world use, so a future version can offer a one-click *Log in with Strava* with no app
creation. I can't promise approval or timing, since that is Strava's decision, but I will keep this section
up to date, and the own-app mode described above will keep working either way.

## What the widget does

- **Bar button** cycles (every 6 s, `cycleIntervalSec`; 0 turns cycling off) through: the latest activity with
  a sport icon, this week's total, kudos and PRs of the latest activity (only when there are any), and your
  training load compared with last week. Hover shows when Strava was last polled, the latest activity's name,
  kudos and PRs, and the load comparison.
- **Records and kudos** sit right under the ride's description, in a section that folds like the calendar (it
  starts folded when the list is long, so the popup never outgrows the screen). A **medal** marks a personal record
  (PR: your 1st, 2nd or 3rd fastest time on a segment, or a run's best efforts such as your fastest 5k), a **cup** a
  top-10 place among everyone on a segment (1st is the KOM/QOM), each with the segment's name and the time it was
  achieved in. Below that is who gave **kudos**, as Strava names them: first name and last initial, which is all
  Strava's API returns for other people. Everything is fetched once per ride (two requests at most) and stored, and
  for rides other than the latest only when you look at them.
- **Training load** compares this week so far with last week *up to the same moment of the week*, and shows
  what is still needed to beat last week's total. `loadMetric` chooses the measure: `time` (default, works for
  every sport), `distance`, or `effort` (Strava's Relative Effort; needs a heart-rate device, falls back to time).
- **Kudos notifications** are sent after a refresh finds new kudos, as a cup-icon notification naming who gave
  them (Strava returns first name and last initial). Only the 10 newest activities are watched; the first run
  only records a baseline. The names are kept in the local cache (`kudoers`) and never leave your machine.
- **Do not disturb:** notifications go through the shell's notification service, so Omarchy's own do-not-disturb
  silences them. A lapbar-only mute is under "Kudos alerts" in the popup, or right-click the bar button
  (`lapbar mute on|off|toggle|status`). While muted, events are consumed without notifying.

Kudos are checked on each refresh (default 15 minutes), so an alert can arrive up to that much later.

### Fitness, fatigue and form

At the top of the popup's right column is the plot everything else is meant to be built on: **fitness** (a slow
average of your training, about six weeks), **fatigue** (a fast average, about a week) and **form** (fitness minus
fatigue as it stood yesterday: above zero you are fresh, below zero you are tired). Hover any day to read it, or
click **Open chart** for the full-size version with the daily load underneath. The bar cycles a "Form +8" frame too.

Strava's API has **no** fitness or freshness data (I checked its public spec), so LapBar works it out itself with the
standard impulse-response model, from activities it has already fetched. Each activity needs one number, its
*load*, taken from the best data available:

| Load from | When | Notes |
|---|---|---|
| **Power** | you set your FTP and the ride has a power meter | TSS = hours x (weighted watts / FTP)^2 x 100 |
| **Strava's Relative Effort** | Strava recorded one (a heart-rate device) | used as is, or rescaled to match power (below) |
| **Duration** | nothing else exists | hours x a modest typical rate for the sport, so every activity counts |

TSS and Relative Effort are on different scales (one ride can be 76 TSS and 164 effort). When you set an FTP,
LapBar compares your own rides that have both and rescales effort-only activities to match, and says so under the
plot ("Strava effort scaled x0.74 to match your power"). The result is an **estimate**: good for the shape of
your training and for comparing yourself with yourself, not for comparing with anyone else's chart. The first six
weeks of history are marked as warming up. It is **not medical advice**.

**FTP.** Strava does not give it to LapBar with the permissions it asks for, so you set it yourself: **⋮ → FTP …**
(a stepper, in watts; 0 means unknown), or the `ftp` setting. It then appears as a large number next to fitness,
fatigue and form.

*How "Estimate from my rides" works.* FTP is roughly the power you can hold for an hour, and a hard ride of about
that length has a weighted (normalised) power close to it. So LapBar:

1. takes your rides from the history it has already fetched (at least the last 130 days, or since 1 January if
   that is longer; virtual rides count; e-bike rides and other sports do not),
2. keeps those with **power data** and a **moving time of 40 minutes or more** (shorter rides say little about an
   hour, and Strava's weighted power for them runs high),
3. needs **at least five** such rides, so one lucky ride or a glitching power meter cannot decide it,
4. takes the **highest weighted power** among them and rounds it to the nearest 5 W.

That is all: no extra requests to Strava, and no curve fitting. It is a conservative ballpark, usually a little
under a proper test, because not every long ride is an all-out effort. The best-20-minute-power method (95% of it)
needs each ride's second-by-second data, which does not fit the request allowance across dozens of rides. Clicking
the button sets your FTP to the estimate and recalculates at once; use the stepper (or **Clear FTP**) to change it
afterwards. The estimate is never applied on its own, only when you click.

### Refresh interval and Strava's request allowance

Change how often LapBar asks Strava from the menu: **⋮ → Refresh every …** (1 minute to 1 hour, default 15
minutes). It is also the `refreshIntervalSec` setting.

A new Strava app is allowed about **1,000 read requests a day** (100 per 15 minutes). LapBar reads Strava's own
usage counters from every response, so it always knows how much is left, and it shares that out so a short
interval can never use it all up:

| What | Allowed until | Why |
|---|---|---|
| Extras: downloading older activities' charts, looking up who gave old kudos | 40% used | never worth spending scarce requests on |
| **Automatic** refreshes (the timer) | 60% used | leaves 40% of the day for you |
| **Manual** refreshes (refresh button, menu, opening the popup) | 90% used | there is always room to refresh by hand |
| Downloading the charts of an activity you opened | 95% used | you asked for it by name; stored charts always open |

When the timer is paused, the popup says so and manual refreshing still works. The counters reset at midnight
UTC (daily) and on the quarter hour. The menu lists the estimated requests per day for each interval and marks in
orange the ones where the timer alone would reach the 60% line before the day ends.

### Charts window

Click **Open charts** under the route in the popup (it works for any activity you have selected, including
older ones picked from the calendar). A separate window opens with **one plot per measure, all sharing one
x axis in kilometres**: elevation, speed (or pace for running, walking and swimming), heart rate, power,
cadence, temperature and grade, whichever the activity recorded. It is its own process, so a problem in the
charts can never affect the bar.

- **Read values at any km.** A single vertical line runs across every plot at the same distance, each plot
  marks its value there, and the panel on the right lists every measure at that km with min / avg / max of
  what is in view. Move the mouse, or use the arrow keys (Shift for bigger steps, Home / End for the ends).
- **Browse.** Scroll or `+` `-` to zoom around the pointer, drag to pan, double-click or `R` to reset.
  Click a measure in the panel to hide or show it. `T` (or the Table button) switches to a table of every
  stored value, and clicking a row moves the line there.
- **No map** (yet). Indoor activities and ones without distance use elapsed time as the x axis instead.
- Colours follow your bar theme; each measure always keeps its own colour.

Time series are downloaded **once** and stored in `~/.cache/lapbar/streams/` (about 60 KB per activity, mode
600), so reopening a chart never asks Strava again. The newest ride is fetched automatically; older
activities are added a few per refresh, newest first, until your history is complete (`downloadHistory`, 0
turns it off; charts you open are downloaded on demand either way). Each download is one Strava request.
Stored data is re-downloaded once if a later version changes how it is processed.

## Summary shape

    {
      "provider": "strava",
      "updated": "2026-09-19T12:00:00Z",
      "latest": { ...see below... },
      "week": {"distance_km": 0.0, "elevation_m": 0, "moving_time_s": 0, "count": 0,
               "by_sport": {"ride": {"distance_km": 0.0, "elevation_m": 0, "moving_time_s": 0, "count": 0}}},
      "year": { same shape as week },
      "load": {"this_week": {"moving_time_s": 0, "distance_km": 0.0, "effort": 0}, "last_week": {...},
               "last_week_same_point": {...}, "days_left": 2, "has_effort": true},
      "kudos_seen": {"<activity id>": 3}, "kudoers": {"<activity id>": ["Ana K.", "Bo M."]},
      "kudos_events": [{"activity_id": 1, "name": "...", "count": 1, "total": 3, "from": ["Ana K."]}],
      "days": {"2026-09-18": {"count": 1, "distance_km": 36.54, "moving_time_s": 4409, "families": ["ride"]}},
      "activities": [ {same fields as latest, minus nulls, route of 60 points, no elevation_profile}, ... ]
    }

`days` (active days this year, keyed by local date) drives the calendar shading; `activities` (newest
first) lets a click on a calendar day show that activity's details in the popup.

`by_sport` is keyed by sport family (`ride`, `run`, `walk`, `swim`, `paddle`, `winter`, `skate`,
`gym`, `other`), most time spent first. Unknown Strava sport types land in `other`.

`latest` (fields the activity doesn't have are `null`, never invented):

| Group | Fields |
|---|---|
| Identity | `id`, `start`, `sport` (exact Strava type), `family`, `name`, `device`, `indoor`, `commute`, `athletes` |
| Size | `distance_km`, `elevation_m`, `moving_time_s`, `elapsed_time_s`, `elev_high_m`, `elev_low_m` |
| Speed | `avg_speed_kmh`, `max_speed_kmh`, and `pace_s` + `pace_unit` (`km` for run/walk/hike, `100m` for swim, `500m` for rowing; `null` for other sports, so show speed) |
| Body | `avg_heartrate`, `max_heartrate`, `suffer_score` |
| Power | `avg_watts`, `max_watts`, `weighted_watts`, `kilojoules` |
| Cadence | `avg_cadence` + `cadence_unit` (`rpm` cycling; `spm` running/walking, doubled because Strava stores it per leg) |
| Other | `avg_temp_c` |
| Social | `kudos`, `comments`, `prs`, `achievements`, `photos` |
| Graphics | `route` (about 150 `[x, y]` points in a 0-1 square: the bare shape, no coordinates), `elevation_profile` (100 metres values evenly spaced by distance; costs one extra request per new activity) |

The widget decides what to show from `family`: pace for runners and swimmers, speed and watts for
riders, duration only for gym sessions.

## Development

    scripts/dev-sync.sh          # copy the repo into ~/.config/omarchy/plugins/ as a real folder
    scripts/dev-sync.sh --watch  # ...and again on every change

Do not symlink the repo into the plugins directory: the shell does not reload symlinked plugins.

Tests never touch your real keyring: an autouse fixture replaces it with an in-memory fake, and any attempt
to run the real `secret-tool` fails the test. Run them with a venv that has `pytest`.

### Trying the chart window without the bar

    lapbar charts <activity id>          # downloads once if needed, then opens the window
    lapbar streams <activity id> --refresh   # download the series again
    lapbar details <activity id>         # the ride's records and kudos names, as the popup asks for them (JSON)

Its plotting logic lives in `charts/logic.js` (ticks, cursor lookup, zoom, formatting) and is unit-tested with
`node`; the window itself is `charts/shell.qml` + `charts/SeriesPanel.qml`, drawn with Qt Quick's built-in
`Canvas`, so it needs no plotting library. On Hyprland 0.55+ the window is floated and centred through the Lua
API (`hyprctl eval`); older versions use the classic dispatchers.

## Notes for widget authors

- Never declare a property called `data` on an Item: it is the built-in default property that holds the
  item's children, and assigning to it silently removes them.
- After any change to `Panel.qml` or `manifest.json`, run `omarchy restart shell`. The shell logs
  "Local plugin changed, reloading" but keeps showing the old code of a widget already mounted in the bar.

## Thanks and attribution

**Powered by Strava.** LapBar exists because Strava offers an open API, and I am grateful for it. The popup
and the chart window carry Strava's official "Powered by Strava" logo, and every activity links back with "View
on Strava", as Strava's [brand guidelines](https://developers.strava.com/guidelines/) ask. The logos in
`assets/strava/` are Strava's trademarks, used unmodified and **not** covered by LapBar's license (see the
notice next to them). LapBar is not affiliated with or endorsed by Strava.

**stravalib.** Thank you to the developers of [stravalib](https://github.com/stravalib/stravalib), the
open-source Python client for Strava's API (Apache-2.0, maintained by the stravalib community). LapBar does
not use it: it talks to the API directly with only Python's standard library, so there is nothing to install.
But if you are building your own Strava tools in Python, stravalib is the place to start, and its docs are a
good way to learn what the API offers.

**Omarchy and Quickshell.** LapBar is a small plugin running on two open-source projects it could not exist
without.

## License and contributing

LapBar is free software, licensed under the **GNU General Public License v3.0 or later** (see `LICENSE`).
You can use, study, change and share it. If you distribute a modified version, you must make its source
available under the same license, so improvements stay open for everyone.

The best way to get a change to everyone, including the people who use the original, is to send it back as a
pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up, what a good pull request looks like, and
the sign-off (`git commit -s`) that goes with contributions. Security-relevant reports go through
[SECURITY.md](SECURITY.md). Sports other than cycling are first-class here: if a number or panel looks wrong
for running, swimming or anything else, that is a bug worth reporting.

## Status

Done: guided setup with keyring storage, Strava provider (all sports), bar widget (cycling bar button and tooltip,
load vs last week, kudos notifications with mute, route trace, calendar, sport-aware stats), 98 tests.
Next: publish the repository, then apply to Strava's Developer Program for one-click sign-in.
