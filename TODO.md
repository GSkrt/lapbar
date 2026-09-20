# LapBar TODO

Plans and next steps, roughly in the order I would tackle them. Nothing here is started unless it says so.
Checkboxes are meant to be ticked as things land. Effort guesses: **S** = an evening, **M** = a few evenings,
**L** = a couple of weekends.

## 0. First: test it as a user (about a week from 2026-09-19)

Nothing below should start before this, because it may change the priorities.

- [ ] Install the way a user would: remove the dev copy, then `omarchy plugin add https://github.com/GSkrt/lapbar.git --enable`.
- [ ] Start from nothing (menu: Reset account, or a brand-new Strava app) and follow the setup using only the on-screen text.
- [ ] Use it for a few days: refreshes, kudos alerts (none has fired for real yet), the request budget over a full day.
- [ ] Things I could not verify: clicking every menu item, About links, Reset confirmation, light theme, a second monitor, older Hyprland.
- [ ] Sports I do not do: have someone who runs or swims check pace, cadence and the charts against Strava.
- [ ] Write down what felt clumsy. That list beats this one.

## 0b. Quick wins for cycling (asked for later)

Small additions to the quick stats in the popup. Some of the data is already there, so these are mostly display work.

- [x] **Total climb in the totals** (done 2026-09-19): Today, This week, This month and This year each end with a
      "Climb" row summing elevation over every sport, and each calendar day shows its climb in the tooltip and the
      day's activity list.
- [ ] Still open: a bar-cycle frame ("Wk climb 620 m"), and a climb figure per sport so a walk's climb is not
      mixed into the ride's (the per-sport data already exists in `by_sport`).  S
- [ ] **Total training load of the week**, as an absolute number and not only "ahead / behind last week".
      - First step (no new data needed): the week's **Relative Effort** total (`load.this_week.effort`) and
        moving time, both already computed. Label it clearly, and hide it for weeks with no heart-rate data.  S
      - Later, when the load ladder in section 1.2 exists: **TSS** for rides with power (needs FTP), otherwise a
        heart-rate estimate, with a note on what it is based on. Show it next to last week's total for context.  M
- [ ] Decide where they go: the "This week" block on the right, the stats grid on the left, or a new small
      "Cycling week" tile that only appears when the sport is a ride.  S

## 1. Engagement, fitness status and motivation (the big one)

**Goal.** Make people want to move, and celebrate what they do *more warmly than Strava does*: say how their
fitness is, suggest one small sensible next step, and be funny and kind when they have been on the chair too long.
For everyone, not only cyclists.

### 1.1 Principles (decide once, keep forever)

- **Local and private.** All numbers are computed on the user's machine from data already fetched. No telemetry.
- **Rules, not AI.** Suggestions come from simple transparent rules. Do **not** send Strava data to an external
  AI or LLM service. I recall Strava restricting AI use of API data, but the agreement text I read on 2026-09-19 did
  not mention it, so verify the current terms (or ask developers@strava.com) before building anything smart. (Local rules are also easier to test and to explain.)
- **Not medical advice.** Say so wherever suggestions appear. Never mention weight, body, or "should". Prefer
  "you might enjoy". Always allow "I am injured / ill / on holiday": a pause mode that silences everything.
- **Celebrate first, nudge second.** Praise is unlimited, nudges are capped (see 1.5).
- **Sport-inclusive.** Nothing may assume cycling or power meters. Fall back gracefully when data is missing.
- **Quiet by default.** Opt-in, respects Omarchy's do-not-disturb, has quiet hours and a snooze.
- **Every message is data, not code**, so it can be translated and tuned without touching logic (see section 2).

### 1.2 How to check current fitness status (the plan)

**Done (v1, 2026-09-19):** the model (`lapbar/fitness.py`: fitness 42 d, fatigue 7 d, form = yesterday's fitness minus
fatigue), the load ladder (power TSS with a manual FTP, else Strava's Relative Effort, else duration x a typical
rate), calibration of effort against power, a 130-day fetch window, the popup block with a hover plot and status,
a full-size fitness chart (`lapbar fitness`), and a bar frame and tooltip line. Checked against the real API: it
has no fitness/freshness data, and no FTP with our current permissions.

**Still to do in this section:** the bullets below that are not ticked. Everything else in the project (suggestions,
achievements, the coach) should read from `fitness.status` and the series rather than recompute anything.

**Risk to check before applying to Strava's Developer Program:** the API Agreement says apps "may not ... compete
with or replicate Strava functionality", and Fitness & Freshness is one of Strava's own (paid) features. Own-app
mode is unaffected, but for the reviewed app: keep the wording generic ("fitness, fatigue, form"), keep it clearly
local and for the athlete's own data, and ask developers@strava.com whether it is acceptable before submitting.


The classic model (Banister impulse-response, popularised as the "performance manager") needs one number per
activity, called training load, then two running averages:

| Name | Meaning | Definition |
|---|---|---|
| **Load** (per activity) | how hard it was | see the fallback ladder below |
| **Fitness** (CTL) | what you have built up | exponentially weighted average of daily load, about 42 days |
| **Fatigue** (ATL) | what you did lately | same, about 7 days |
| **Form** (TSB) | fresh or tired | Fitness minus Fatigue (yesterday's values) |

Per-activity load, using the best data available, in this order:

1. **Power** (cycling): TSS needs the athlete's FTP.
2. **Heart rate**: TRIMP (Banister, or zone-based Edwards) or hrTSS. Needs resting and max HR, or HR zones.
3. **Strava's Relative Effort** (`suffer_score`): already in our data when a heart-rate device was used. Cheap, but
   missing for many activities and Strava's own formula.
4. **Time x a sport-typical intensity** (easy = 1.0, tempo = 1.5, race = 2.0, gym 0.7...), when nothing else exists.
   Works for every sport, including gym sessions and yoga, so it is the guaranteed floor.

Tasks:

- [x] **Decide the load ladder** above and how it is labelled in the UI ("Estimated from power, Strava effort, duration").  S
- [x] **Data window.** CTL needs about 42 days of warm-up plus history. Today we fetch the current year plus one
      week: extend `since` to at least 90 days before "now" (early January needs last year's tail). Costs one or
      two extra list requests, not one per activity.  S
- [ ] **HR zones and FTP from the API.** (FTP can be typed in now: the `ftp` setting.) Checked: `GET /athlete/zones` is refused
      (401) without `profile:read_all`, and Strava's public spec does not list `ftp` on the athlete at all, so even with
      the permission FTP may not come back. Try it once with a re-authorised token before building an opt-in flow.
      The original note follows. Strava serves them from `GET /athlete/zones` and the profile, which need the extra
      scope `profile:read_all`. Adding a scope means everyone re-authorises once (the setup already handles
      "sign in again"). Alternative: ask for max/resting HR in the setup. Decide which.  S (decision), M (work)
- [x] `lapbar/fitness.py`: pure functions `load(activity, profile)`, `daily_series(activities)`, `ctl_atl_tsb(series)`.
      Fully unit-tested with synthetic weeks (a rest week must lower fitness, a hard week must raise fatigue).  M
- [ ] **Sanity signals that need no profile at all** (good enough for v1, and honest):
      4-week volume vs the 4 weeks before; active days per week; longest gap; ramp rate (this week vs the 3-week
      average); share of easy vs hard days from average HR relative to the athlete's own median.  M
- [ ] **Efficiency trend** from the streams we already store: pace (or power) per heartbeat on comparable steady
      efforts, and aerobic decoupling (first half vs second half). A real fitness signal with no formulas from
      Strava.  L
- [x] **Show it** (popup plot + full chart done; the "why do you say this?" explanation is still open): one honest line in the popup and the bar cycle ("Form: fresh, fitness up 6% in 4 weeks"),
      a small trend chart in the charts window, and the reasoning behind it one click away ("why do you say this?").  M
- [ ] Validate against real accounts (mine, plus a runner and a swimmer) and against Strava's own Fitness &
      Freshness graph for a rough sanity check, not to match it.  M

Do not build: VO2max, race predictors or anything that claims accuracy LapBar cannot have.

### 1.3 Achievements, celebrated more than Strava does

Strava celebrates PRs and segment crowns. LapBar can also celebrate what Strava ignores, mostly about
consistency and comebacks, and compare people **only with themselves**.

- [ ] Milestone engine `lapbar/achievements.py`: pure rules over the activity history, each with a stable id so a
      celebration fires once.  M
- [ ] First batch of achievements (ideas, prune later):
  - [ ] First activity of the week / month / year; first time above X km in a week
  - [ ] Best week (or month) since a named date ("best week since March")
  - [ ] Longest streak of weeks with at least 3 active days
  - [ ] **Comeback**: first activity after 14+ days off ("welcome back", never "finally")
  - [ ] Easy-day discipline: a genuinely easy session after a hard one (recovery counts)
  - [ ] Variety: three different sports in one week
  - [ ] Personal seasons: totals with fun equivalences ("you have climbed Triglav 11 times this year")
  - [ ] Round numbers: 100 activities, 1,000 km, 10,000 m climbed, 100 hours
  - [ ] Route explorer: a route shape you have never done (compare the polylines we already store)
  - [ ] Kudos milestones ("your 100th kudo")
- [ ] **Weekly recap** notification (Monday morning, respects do-not-disturb): what you did, one thing to be proud
  of, one gentle idea.  M
- [ ] A small "trophies" list in the About or a new menu entry, so past celebrations can be revisited.  S
- [ ] Goals: optional weekly and monthly target in time (default), distance or count, with a progress bar next to
      the existing last-week comparison. Never a red "failed" state.  M

### 1.4 Simple suggestions

A small, transparent rule set. Every suggestion shows its reason and can be dismissed. Draft rules (thresholds live
in settings, and the numbers below are starting guesses to tune, not science):

| If | Then suggest |
|---|---|
| Form very negative and fatigue rising for 2+ weeks | an easy day or a rest day; say why |
| Ramp rate high (a weekly jump well above the recent average) | hold this week steady; the "10% rule" is a folk rule, treat it as a soft hint |
| 3+ days without activity, feeling fine | a short easy session; "10 minutes counts" |
| 14+ days off | an easy comeback, half of what you used to do, no guilt |
| Lots of the same hard effort, no easy days | mix in an easy one |
| Fitness rising steadily | tell them, and suggest keeping the routine |
| Only one sport for weeks | offer a variety idea, optional *(parked: not sure it is wanted; decide after the test week)* |

**Decided 2026-09-19:** the first six rules are approved; the seventh (variety) is parked. "Fitness rising
steadily" is praise, so it never becomes a notification.

**Where suggestions appear: both, with different jobs.**

- **In the popup (main place, always there).** A small "Coach" card under the fitness block: one suggestion at a time,
  with its reason ("Form is -24 and fatigue has risen for 16 days") and a dismiss / snooze. It is pull, not push:
  it is there when you look, and costs no attention otherwise.
- **As a desktop notification (opt-in, off by default).** Only for the nudges you can act on (3+ days off, a
  comeback after 14+, form very negative), at most one a day, never within an hour of an activity, never in quiet
  hours, do-not-disturb, mute or pause mode. Praise is shown in the card only.
- **In the bar (optional, later).** At most a short frame such as "Form: tired" that the card explains; no text nudges
  in the bar itself.

- [ ] `lapbar/coach.py`: rules return `(suggestion_id, reason_facts)`, never text; text comes from the message
      catalog so it can be translated.  M
- [ ] Guardrails: "not medical advice" line, pause mode (injury / illness / holiday), snooze, at most one
      suggestion visible at a time.  S
- [ ] Tests: every rule with a passing and a failing case, and a test that no rule can fire during pause mode.  S

### 1.5 Funny but positive notes ("how to get up from the chair")

Short lines shown in the popup and, at most once a day and only if enabled, as a notification.

Tone rules (write these into `CONTRIBUTING.md`):

- Laugh **with** the person, about the chair, the couch, the excuse or the door, never about their body, pace,
  age, weight or how long they have been away.
- No shame, no streak guilt, no "you should", no emoji spam. One small joke, then one small step.
- Rest is respected: no nudges on flagged rest days or in pause mode.
- Humour is per-language, not translated word for word (see section 2).

Draft lines (English, to be tuned):

- "Day three on the sofa. Nice sofa. It will not run your 10k for you, though."
- "The hardest part is the door. After that it is just walking."
- "Ten minutes counts. Your future self already said thanks."
- "Your bike has not said anything. It is just... looking at you from the hallway."
- "Legs remember even when motivation forgets. Let them show you."
- "Plot twist: you will feel better after. It happens every single time."
- "Rest days are training. A very long rest day is a very long training session. Shall we end it?"
- After a comeback: "Welcome back. The road missed you. It will not mention the gap."
- Achievement: "Fifth ride this month. Strava gives you a badge. We give you a very large virtual high-five."

Tasks:

- [ ] Message pack format: `id`, `trigger`, `tone` (gentle / cheeky), `cooldown_days`, `text`; loaded from data
      files, never hard-coded.  S
- [ ] Triggers and caps: max one nudge per day, none within an hour of an activity, quiet hours, never during
      do-not-disturb or pause mode, rotate so the same line does not repeat within N days.  M
- [ ] Settings: coach on/off, tone (gentle / cheeky / off), quiet hours, "not today", "never show these".  S
- [ ] Write 30+ lines in English first, review them together for tone, then translate (section 2).  M
- [ ] UI: the "Coach" card in the popup (today's suggestion, its reason and a line), then the opt-in notification, then
      a bar frame ("Form: fresh"). See the placement decision in 1.4.  M

### 1.6 Suggested order for section 1

1. Data window and the no-profile signals (1.2), because everything else stands on them.
2. Achievements and the weekly recap (1.3): cheap, joyful, low risk.
3. Message pack, caps and settings (1.5), with English only.
4. Fitness model with heart rate / power (1.2 later half) and the suggestions (1.4).
5. Everything above translated (section 2).

Open questions for me: is the popup the right home, or a separate "Coach" window like the charts? Which of the
scopes / setup questions am I willing to ask users for?

## 2. Languages

Goal: the whole experience (setup wizard, errors, popup, menu, notifications, motivational notes, README basics)
in the major languages, with the right units and formats. English is the source. Slovenian goes early because it is
my language, and it is a good stress test (Slovenian has a dual plural form).

**Suggested set**: English, Slovenian, Spanish, French, German, Portuguese (Brazil), Italian, Chinese (Simplified),
Japanese, Korean, Russian, Arabic (RTL), Hindi. Better: look at which languages Strava's own app offers and
start with those, since that is where the users are. Tasks:

- [ ] **Extract every user-facing string.** They live in `Panel.qml`, `charts/shell.qml`, `lapbar/setup.py`,
      `cli.py`, `ratelimit.py`, `kudos` notifications, tooltips and the README. Inventory first (a script that lists
      candidates), then move them.  M
- [ ] **One catalog, two consumers.** JSON files `locales/<lang>.json` read by Python and, through a small helper
      `t(key, vars, count)`, by QML. Chosen over Qt's `qsTr` + `.ts` because that needs the Qt translation
      toolchain and Python cannot share it. Check with a spike that it is enough.  M
- [ ] **Plurals with real rules**, not "add an s": use CLDR categories. English has 2 forms, Slovenian 4
      (one, two, few, other), Russian 3, Arabic 6, Chinese / Japanese / Korean 1. A message needs the forms its
      language needs. Tests fail on a missing form.  M
- [ ] **Units and formats, independent of language:** km or miles, m or feet, deg C or F, pace per km or per mile,
      week starting Monday or Sunday, 12 or 24 hour clock, decimal comma. Detect from the locale, allow override
      in settings. Today these are hard-coded (km, Monday, `.`).  M
- [ ] **Language choice:** detect from `LANG` / `LC_MESSAGES`, override with a setting and a menu entry.  S
- [ ] **Right-to-left** (Arabic, Hebrew): mirror the popup and menu; keep the charts left-to-right on purpose (time
      and distance run left to right) and say so.  M
- [ ] **Fonts:** the Nerd Font used for icons has no CJK glyphs. Check fallback rendering for zh / ja / ko in the
      popup, the canvas text in the charts, and notifications.  S
- [ ] **Do not translate brand wording blindly.** Strava's guidelines require the exact text "View on Strava" and
      "Powered by Strava". Check whether they allow translations before localising those two strings.  S
- [ ] **Humour is transcreated** by a native speaker, not translated: each language gets its own line set with the
      same tone rules. Machine drafts are fine as a start if flagged "needs review".  L (ongoing)
- [ ] **Quality checks in CI:** every key exists in every language, placeholders (`{count}`, `{name}`) match the
      English ones, plural forms complete, no leftover English in a finished language, plus a pseudo-language
      (accented and 30% longer) to expose text that does not fit.  M
- [ ] **How people contribute:** a translation section in `CONTRIBUTING.md`, a template for adding a language,
      and a "needs review" marker so a rough translation can still ship.  S
- [ ] Setup wizard first (the most important text a new user sees), then popup and menu, then notes.  (order)

## 3. Before opening the repository

- [x] **Name decided: LapBar** (2026-09-20; earlier working names *pacebar* and *AFKbar* were dropped). Reasons are in
      the README ("Why LapBar?"): a lap belongs to every sport, the name has no Strava in it (their brand rules), and
      "bar" is where it lives today while the command-line core is not tied to one desktop.
- [ ] Do a proper **trademark search** (EUIPO / USPTO) for LapBar. I only checked exact repository names on GitHub
      (none other than ours: `lapbar` has 0 repositories), and package names on PyPI, AUR and npm were free for the
      earlier names, so re-check those for `lapbar`. Also decide on a domain.
- [ ] **Rename my own Strava app** to LapBar at strava.com/settings/api: the authorization page shows its name.

- [ ] Put the real install URL in the README (currently `<repository-url>`).
- [ ] Screenshots in the README: popup and chart window, cropped so nothing personal shows (route near home, name,
      a revealed Client Secret).
- [ ] Delete the old plaintext `.env` in the project folder (git-ignored, unused).
- [ ] Re-read README, CONTRIBUTING and the About card as a stranger.
- [ ] Tag `v0.1.0` and write short release notes.
- [ ] Enable GitHub private vulnerability reporting (SECURITY.md points to it) and check the issue templates render.
- [ ] Then flip the repository to public.

## 4. Bigger ideas, in no particular order

- [x] **Map view: decided against.** Strava's own activity page already has a good map and LapBar links to it
      ("View on Strava"), so a map here would only duplicate it. What it would cost: a tile provider (privacy,
      usage terms, attribution), plus either an embedded web view (QtWebEngine, heavy, not on every install) or our
      own tile loader. The route outline we draw is enough. Strava also offers `latlng` in the streams request
      (positions, in step with every other series), so a cursor-linked route could be added cheaply later without any
      basemap, but it is not planned.
- [ ] **One-click sign-in**: a small Cloudflare Worker that holds the client secret and does the OAuth exchange,
      then apply to Strava's Developer Program. Own-app mode must keep working. Details are in the README's plan.
- [ ] **Strava Developer Program** application once there is a public release and some use.
- [ ] Packaging: an AUR package or a one-line installer, if people ask.
- [ ] Charts: compare two activities, a route overlay, laps and splits, more series when Strava adds them.
- [ ] More celebration surfaces: an end-of-year "wrapped" page generated locally.

## 5. Known limits worth remembering

- Fitness numbers are estimates and get worse the less data an activity has; always say what they are based on.
- A new Strava app is single-user and rate-limited (about 1,000 read requests a day); the request budget already
  protects this. Anything that needs more requests per refresh must be justified against that budget.
- Strava requires a subscription to create an API app; the wizard and README say so.
- The chart window relies on Hyprland's Lua API for floating and centring (with a classic fallback); the plugin
  folder must never contain symlinks; QML changes need `omarchy restart shell`.
