import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// lapbar: the bar button and the popup behind it, showing the latest Strava activity.
//
// All Strava access happens out-of-process in bin/lapbar (stdlib-only Python, no venv).
// `lapbar fetch --print` prints one JSON object, either the summary described in the
// README or {"error": code, "message": text}; this file only ever parses that.
Panel {
  id: root

  moduleName: "io.github.gskrt.lapbar"
  ipcTarget: "io.github.gskrt.lapbar"

  // ---------------------------------------------------------------- settings

  readonly property int refreshIntervalSec: Math.max(60, Number(setting("refreshIntervalSec", 900)))
  readonly property int downloadHistory: Math.max(0, Math.min(30, Number(setting("downloadHistory", 12))))  // older activities archived per refresh (scaled down as the day's allowance fills up)
  readonly property int historyYears: Math.max(0, Math.min(99, Number(setting("historyYears", 99))))   // earlier years kept for the calendar
  readonly property int ftp: Math.max(0, Math.min(600, Number(setting("ftp", 0))))   // cycling FTP in watts; 0 = unknown
  readonly property int cycleIntervalSec: Math.max(0, Number(setting("cycleIntervalSec", 6)))   // 0 = no cycling
  readonly property string loadMetric: String(setting("loadMetric", "time"))                    // time | distance | effort

  // ------------------------------------------------------------------- paths

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")
  readonly property string launcher: root.pluginDir + "bin/lapbar"
  readonly property string stravaOrange: "#FC5200"

  function passthroughEnv(names, base) {
    var env = Object.assign({}, base)
    for (var i = 0; i < names.length; i++) {
      var v = Quickshell.env(names[i])
      if (v) env[names[i]] = v
    }
    return env
  }

  // Background fetches need only the interpreter and the user's directories.
  readonly property var fetchEnvironment: root.passthroughEnv(
    ["HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"],
    { PATH: "/usr/bin" })

  // Launching a terminal or the browser is a real desktop hand-off.
  readonly property var desktopEnvironment: root.passthroughEnv(
    ["HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
     "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_DATA_DIRS",
     "XDG_CONFIG_DIRS", "HYPRLAND_INSTANCE_SIGNATURE", "OMARCHY_PATH"],
    { PATH: "/usr/bin:/usr/share/omarchy/bin", OMARCHY_PATH: "/usr/share/omarchy" })   // `omarchy bar set` fails without OMARCHY_PATH

  // ------------------------------------------------------------------- state

  property var summary: null
  property bool setupWatching: false
  property int setupWatchTicks: 0
  property bool muted: false       // kudos notifications off (persisted by the CLI)
  property int frameIndex: 0       // which bar frame is showing
  property string errorCode: ""
  property string errorMessage: ""
  property double lastUpdatedAt: 0
  readonly property bool busy: fetchProcess.running

  readonly property bool wide: !!root.summary
  readonly property var latest: root.summary ? root.summary.latest : null
  readonly property var activities: (root.summary && root.summary.activities) ? root.summary.activities : []

  // What the detail view shows: the activity picked in the calendar, else the latest one.
  property var selected: null
  property string selectedDay: ""
  property bool calendarOpen: true
  readonly property var shown: root.selected || root.latest
  readonly property var shownId: root.shown ? root.shown.id : 0
  readonly property bool showingOlder: !!(root.selected && root.latest && root.selected.id !== root.latest.id)
  readonly property var route: (root.shown && root.shown.route) ? root.shown.route : []
  readonly property bool needsSetup: root.errorCode === "not_configured" || root.errorCode === "not_authorized"

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var familyLabels: ({
    ride: "Ride", run: "Run", walk: "Walk", swim: "Swim", paddle: "Paddle",
    winter: "Winter", skate: "Skate", gym: "Gym", other: "Other"
  })

  // -------------------------------------------------------------- formatting

  function pad2(n) { return n < 10 ? "0" + n : "" + n }

  function fmtDuration(seconds) {
    var h = Math.floor(seconds / 3600)
    var m = Math.floor((seconds % 3600) / 60)
    if (h > 0) return h + "h " + pad2(m) + "m"
    return m + " min"
  }

  function fmtPace(seconds, unit) {
    return Math.floor(seconds / 60) + ":" + pad2(seconds % 60) + " /" + unit
  }

  // Swimmers think in metres; everyone else in kilometres.
  function fmtDistance(km, family) {
    if (family === "swim") return Math.round(km * 1000) + " m"
    return (km >= 10 ? km.toFixed(1) : km.toFixed(2)) + " km"
  }

  function sportName(sport) {
    return sport ? sport.replace(/([a-z])([A-Z])/g, "$1 $2") : ""
  }

  function startDate(l) {
    if (!l || !l.start) return null
    return new Date(l.start.replace("Z", ""))  // wall-clock time in the activity's timezone
  }

  // The sport decides which numbers matter: pace where people think in pace, speed and
  // power for cycling, time only for sessions without distance (gym, yoga...).
  function statCells(l) {
    var cells = []
    if (l.distance_km > 0) cells.push({ label: "Distance", value: fmtDistance(l.distance_km, l.family) })
    cells.push({ label: "Time", value: fmtDuration(l.moving_time_s) })
    if (l.elevation_m > 0) cells.push({ label: "Climb", value: l.elevation_m + " m" })
    if (l.pace_s) cells.push({ label: "Pace", value: fmtPace(l.pace_s, l.pace_unit) })
    else if (l.avg_speed_kmh && l.distance_km > 0) cells.push({ label: "Speed", value: l.avg_speed_kmh.toFixed(1) + " km/h" })
    if (l.avg_watts) cells.push({ label: "Power", value: Math.round(l.avg_watts) + " W" })
    if (l.avg_heartrate) cells.push({ label: "Heart rate", value: l.avg_heartrate + " bpm" })
    if (l.avg_cadence) cells.push({ label: "Cadence", value: l.avg_cadence + " " + l.cadence_unit })
    return cells
  }

  function fmtClimb(m) { return "\u2191 " + Math.round(m).toLocaleString(Qt.locale("en_US"), "f", 0) + " m" }

  // [{label, value}] per sport family, most time spent first (the CLI already sorts them).
  // Today (hidden while empty), this week, this month and this year, shown in the third column.
  readonly property var totalsBlocks: {
    if (!root.summary) return []
    var all = [{ title: "Today", t: root.summary.today, hideEmpty: true }, { title: "This week", t: root.summary.week },
               { title: "This month", t: root.summary.month }, { title: "This year", t: root.summary.year }]
    return all.filter(function(b) { return !(b.hideEmpty && root.totalsRows(b.t).length === 0) })
  }

  // The popup is as tall as its tallest column; the fitness plot takes up whatever the middle column has left over.
  readonly property real creditHeight: Style.space(16) + Style.spacing.panelGap
  readonly property real popupHeight: Math.max(leftCol.implicitHeight, root.wide ? Math.max(rightCol.implicitHeight, thirdCol.implicitHeight + root.creditHeight) : 0)
  readonly property real plotMinHeight: Style.space(130)

  function fitPlot() {
    if (!root.wide) return
    var others = Math.max(leftCol.implicitHeight, thirdCol.implicitHeight + root.creditHeight)
    var natural = rightCol.implicitHeight - fitnessPlot.height + root.plotMinHeight      // the middle column with the smallest plot
    var wanted = root.plotMinHeight + Math.max(0, others - natural)
    fitnessPlot.height = Math.min(wanted, Math.max(root.plotMinHeight, fitnessPlot.width * 0.9))   // keep it in proportion
  }

  function totalsRows(t) {
    var rows = []
    if (!t || !t.by_sport) return rows
    for (var fam in t.by_sport) {
      var s = t.by_sport[fam]
      rows.push({
        label: root.familyLabels[fam] || fam,
        time: s.moving_time_s,
        value: s.distance_km > 0 ? fmtDistance(s.distance_km, fam) + "  ·  " + fmtDuration(s.moving_time_s) : fmtDuration(s.moving_time_s)
      })
    }
    // Don't rely on JS object key order: most time spent first.
    rows.sort(function(a, b) { return b.time - a.time })
    // Total climb over every sport, when there was any.
    if (t.elevation_m > 0) rows.push({ label: "Climb", time: -1, value: root.fmtClimb(t.elevation_m) })
    return rows
  }

  // ---------------------------------------------------------------- calendar

  property var today: new Date()
  property int viewYear: today.getFullYear()
  property int viewMonth: today.getMonth()   // 0-11

  readonly property var monthNames: ["January", "February", "March", "April", "May", "June", "July",
                                     "August", "September", "October", "November", "December"]
  readonly property var activeDays: (root.summary && root.summary.days) ? root.summary.days : ({})

  function dayKey(y, m, d) { return y + "-" + pad2(m + 1) + "-" + pad2(d) }

  // Activities whose complete time series (with GPS) is stored on this computer: marked with a dot in the calendar.
  readonly property var archivedSet: {
    var set = ({})
    var ids = (root.summary && root.summary.archived_ids) ? root.summary.archived_ids : []
    for (var i = 0; i < ids.length; i++) set[ids[i]] = true
    return set
  }

  function dayArchived(key) {
    var list = root.activitiesOn(key)
    for (var i = 0; i < list.length; i++) if (root.archivedSet[list[i].id] === true) return true
    return false
  }

  // Weeks start on Monday, like Strava's own weekly totals. Empty cells pad the first and last row.
  function calendarCells(y, m) {
    var lead = (new Date(y, m, 1).getDay() + 6) % 7
    var count = new Date(y, m + 1, 0).getDate()
    var cells = []
    for (var i = 0; i < lead; i++) cells.push({ day: 0 })
    for (var d = 1; d <= count; d++) {
      var key = dayKey(y, m, d)
      cells.push({
        day: d,
        key: key,
        info: root.activeDays[key] || null,
        local: root.dayArchived(key),
        excuse: root.excuses[key] || "",
        isToday: y === root.today.getFullYear() && m === root.today.getMonth() && d === root.today.getDate(),
        isFuture: new Date(y, m, d) > root.today
      })
    }
    while (cells.length % 7 !== 0) cells.push({ day: 0 })
    return cells
  }

  function activeCount(y, m) {
    var n = 0
    var count = new Date(y, m + 1, 0).getDate()
    for (var d = 1; d <= count; d++) if (root.activeDays[dayKey(y, m, d)]) n++
    return n
  }

  // Darker fill for longer days: under 30 min, under 1 h, under 2 h, 2 h or more.
  function dayAlpha(info) {
    if (!info) return 0
    var t = info.moving_time_s
    return t < 1800 ? 0.3 : (t < 3600 ? 0.5 : (t < 7200 ? 0.72 : 0.92))
  }

  // Paging back stops at the month of the earliest stored activity (January of this year if no older years are stored).
  readonly property int earliestIdx: {
    var from = (root.summary && root.summary.history) ? root.summary.history.from : null
    if (from && from.length >= 7) return parseInt(from.slice(0, 4)) * 12 + parseInt(from.slice(5, 7)) - 1
    return root.today.getFullYear() * 12
  }
  readonly property bool canGoBack: root.viewYear * 12 + root.viewMonth > root.earliestIdx
  readonly property bool canGoForward: root.viewYear * 12 + root.viewMonth < root.today.getFullYear() * 12 + root.today.getMonth()

  // Moves the calendar by months, clamped to what exists (the earliest stored month, and this month).
  function shiftMonth(delta) {
    var idx = root.viewYear * 12 + root.viewMonth + delta
    idx = Math.max(root.earliestIdx, Math.min(root.today.getFullYear() * 12 + root.today.getMonth(), idx))
    root.viewYear = Math.floor(idx / 12)
    root.viewMonth = idx % 12
  }

  // ---------------------------------------------- older years: stored on disk, read when the calendar reaches them

  property var historyByYear: ({})       // {year: [activities]}, filled by `lapbar history <year>`
  property string historySignature: ""    // which years were stored last time: a change means the loaded ones may be stale

  function activitiesOfYear(year) {
    if (year === root.today.getFullYear()) return root.activities
    return root.historyByYear[year] || []
  }

  function activitiesOn(key) {
    var out = []
    var list = root.activitiesOfYear(parseInt(key.slice(0, 4)))
    for (var i = 0; i < list.length; i++) {
      var a = list[i]
      if (a.start && a.start.slice(0, 10) === key) out.push(a)
    }
    return out
  }

  function loadHistoryYear(year) {
    if (year >= root.today.getFullYear() || root.historyByYear[year] !== undefined || historyProcess.running) return
    if (!root.summary || !root.summary.history || root.summary.history.years.indexOf(year) < 0) return
    historyProcess.wantedYear = year
    historyProcess.command = ["/usr/bin/python3", "-I", root.launcher, "history", String(year)]
    historyProcess.running = true
  }

  function handleHistory(year, text) {
    try {
      var p = JSON.parse(text)
      if (!p.error) {
        var map = Object.assign({}, root.historyByYear)
        map[year] = p.activities || []
        root.historyByYear = map
      }
    } catch (e) { }
    if (root.viewYear !== year) root.loadHistoryYear(root.viewYear)
  }

  onViewYearChanged: root.loadHistoryYear(root.viewYear)

  // Clicking an active day takes you to its activity; with several, the newest is shown and the
  // others are listed under the calendar.
  function pickDay(key) {
    var list = root.activitiesOn(key)
    if (list.length === 0) return
    root.selectedDay = key
    root.selected = list[0]
  }

  onShownChanged: { root.chartsMessage = ""; root.loadDetails() }
  onShownIdChanged: { root.achievementsPref = 0; root.achievementsAll = false }

  // ------------------------------------------------ records (PRs, KOMs) and kudos names of the ride shown

  property var detailsById: ({})        // {activity id: {records, kudoers, failed}}, filled by `lapbar details` for rides other than the latest
  property int achievementsPref: 0      // 0 = open only when short, 1 = the user opened it, -1 = the user closed it
  property bool achievementsAll: false

  function recordsOf(l) {
    if (!l) return []
    if (l.records) return l.records
    var lat = root.summary ? root.summary.latest : null
    if (lat && lat.id === l.id && lat.records) return lat.records
    var d = root.detailsById[String(l.id)]
    return d && d.records ? d.records : []
  }

  function kudoersOf(l) {
    if (!l || !(l.kudos > 0)) return []
    var known = root.summary ? (root.summary.kudoers || {})[String(l.id)] : null
    if (known && known.length > 0) return known
    var d = root.detailsById[String(l.id)]
    return d && d.kudoers ? d.kudoers : []
  }

  readonly property var shownRecords: root.recordsOf(root.shown)
  readonly property var shownKudoers: root.kudoersOf(root.shown)
  readonly property bool wantsAchievements: !!root.shown && (root.shown.prs > 0 || root.shown.achievements > 0 || root.shown.kudos > 0)
  readonly property int achievementRows: root.shownRecords.length + Math.ceil(root.shownKudoers.length / 2)
  // Short lists start open; a long one starts folded so the popup does not grow past the screen (like the calendar).
  readonly property bool achievementsOpen: root.achievementsPref === 0 ? root.achievementRows <= 8 : root.achievementsPref > 0

  readonly property string achievementsSummary: {
    var n = root.shownRecords.length          // Strava's word for PRs of any rank and top-10 places
    var parts = []
    if (n > 0) parts.push(n + (n === 1 ? " achievement" : " achievements"))
    var k = root.shown ? root.shown.kudos : 0
    if (k > 0) parts.push(k + (k === 1 ? " kudo" : " kudos"))
    return parts.join(" · ")
  }

  function recordLabel(r) {
    if (r.kind === "kom") return r.rank === 1 ? "KOM/QOM" : "Top 10 (#" + r.rank + ")"
    return r.rank === 1 ? "PR" : (r.rank === 2 ? "2nd fastest" : "3rd fastest")
  }

  function medalColor(r) {
    return r.rank === 1 ? "#e6b422" : (r.rank === 2 ? "#b8bcc4" : (r.rank === 3 ? "#cd7f32" : root.dim))
  }

  // Time an effort took: m:ss, or h:mm:ss.
  function fmtEffort(seconds) {
    if (seconds === undefined || seconds === null) return ""
    var h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.round(seconds % 60)
    return h > 0 ? h + ":" + pad2(m) + ":" + pad2(s) : m + ":" + pad2(s)
  }

  function needsDetails(l) {
    if (!l || !l.id) return false
    var d = root.detailsById[String(l.id)]
    if (d && !d.failed) return false
    return ((l.prs > 0 || l.achievements > 0) && root.recordsOf(l).length === 0) || (l.kudos > 0 && root.kudoersOf(l).length === 0)
  }

  // Only while the popup is open, and one ride at a time; a stored answer costs no request to Strava.
  function loadDetails() {
    if (!root.opened || detailsProcess.running || !root.needsDetails(root.shown)) return
    detailsProcess.wantedId = root.shown.id
    detailsProcess.command = ["/usr/bin/python3", "-I", root.launcher, "details", String(root.shown.id)]
    detailsProcess.running = true
  }

  function handleDetails(id, text) {
    var entry = { records: [], kudoers: [], failed: true }
    try {
      var p = JSON.parse(text)
      if (!p.error) entry = { records: p.records || [], kudoers: p.kudoers || [], failed: false }
    } catch (e) { }
    var map = Object.assign({}, root.detailsById)
    map[String(id)] = entry
    root.detailsById = map
    if (root.shown && String(root.shown.id) !== String(id)) root.loadDetails()   // the ride shown changed meanwhile
  }

  function backToLatest() {
    root.selected = null
    root.selectedDay = ""
  }

  function dayTooltip(cell) {
    var date = new Date(root.viewYear, root.viewMonth, cell.day)
    var head = Qt.formatDate(date, "ddd d MMM")
    var skipped = cell.excuse ? " · skipped: " + root.excuseLabel(cell.excuse) : ""
    if (!cell.info) return head + (skipped !== "" ? skipped : " · rest day")
    var parts = [cell.info.count + (cell.info.count === 1 ? " activity" : " activities")]
    if (cell.info.distance_km > 0) parts.push(cell.info.distance_km.toFixed(1) + " km")
    parts.push(fmtDuration(cell.info.moving_time_s))
    if (cell.info.elevation_m > 0) parts.push(root.fmtClimb(cell.info.elevation_m))
    if (cell.local) parts.push("full data stored")
    var names = []
    for (var i = 0; i < cell.info.families.length; i++) names.push(root.familyLabels[cell.info.families[i]] || cell.info.families[i])
    return head + " · " + names.join(" + ") + " · " + parts.join(" · ") + skipped
  }

  readonly property string socialText: {
    if (!root.shown) return ""
    var parts = []
    parts.push(root.shown.kudos + (root.shown.kudos === 1 ? " kudo" : " kudos"))
    if (root.shown.prs > 0) parts.push(root.shown.prs + (root.shown.prs === 1 ? " PR" : " PRs"))
    else if (root.shown.achievements > 0) parts.push(root.shown.achievements + " achievements")
    if (root.shown.comments > 0) parts.push(root.shown.comments + (root.shown.comments === 1 ? " comment" : " comments"))
    return parts.join("  ·  ")
  }

  // ------------------------------------------------------------------ account menu

  // The version shown in "About", read from this plugin's own manifest.
  FileView {
    id: manifestFile
    path: root.pluginDir + "manifest.json"
    blockLoading: true
  }

  readonly property string version: {
    try { return String(JSON.parse(manifestFile.text()).version || "") } catch (e) { return "" }
  }

  property bool menuOpen: false
  property bool confirmReset: false
  property bool aboutOpen: false
  property bool intervalOpen: false
  property bool ftpOpen: false
  property int ftpDraft: 0                      // the value being edited in the FTP menu; saved only on request

  readonly property var intervalChoices: [
    { sec: 60, label: "Every minute" }, { sec: 120, label: "Every 2 minutes" }, { sec: 300, label: "Every 5 minutes" },
    { sec: 600, label: "Every 10 minutes" }, { sec: 900, label: "Every 15 minutes" },
    { sec: 1800, label: "Every 30 minutes" }, { sec: 3600, label: "Every hour" }
  ]

  function intervalLabel(sec) {
    for (var i = 0; i < intervalChoices.length; i++) if (intervalChoices[i].sec === sec) return intervalChoices[i].label.replace("Every ", "")
    return Math.round(sec / 60) + " min"
  }

  // Roughly how many of the day's 1,000 read requests the timer alone would use (about 1.2 per refresh).
  function intervalCost(sec) { return Math.round(86400 / sec * 1.2) }

  function setRefreshInterval(sec) {
    root.intervalOpen = false
    root.menuOpen = false
    Quickshell.execDetached({
      command: ["/usr/share/omarchy/bin/omarchy", "bar", "set", root.moduleName, "refreshIntervalSec", String(sec), "--json"],
      clearEnvironment: true,
      environment: root.desktopEnvironment
    })
  }

  // ------------------------------------------------ nudges to get off the chair (`lapbar coach`, see coach.py)

  property var coachInfo: null            // {tone, card, paused, paused_until, ...}: read locally, no request to Strava
  property string coachDismissed: ""      // the card that was closed with the cross; a new one shows again

  // "Motivational quotes": the radio group in the popup. Popups only come with a tone chosen; Silent is the default.
  readonly property var coachModes: [
    { id: "off", label: "Silent" },
    { id: "motivational", label: "Motivational" },
    { id: "drill", label: "Drill sergeant" }
  ]
  readonly property string coachTone: root.coachInfo ? root.coachInfo.tone : "off"

  // Days you skipped, with the reason ("I'm tired"), marked on the calendar; the coach leaves you alone on them.
  readonly property var excuses: (root.coachInfo && root.coachInfo.excuses) ? root.coachInfo.excuses : ({})
  readonly property var excuseLabels: (root.coachInfo && root.coachInfo.excuse_labels) ? root.coachInfo.excuse_labels : ({})
  readonly property string todayExcuse: root.excuses[Qt.formatDate(new Date(), "yyyy-MM-dd")] || ""
  function excuseLabel(key) { return root.excuseLabels[key] || key }

  readonly property var coachCard: root.coachInfo ? root.coachInfo.card : (root.summary ? root.summary.coach : null)
  readonly property bool coachVisible: !!root.coachCard && !!root.coachCard.text && root.coachDismissed !== (root.coachCard.id + root.coachCard.text)
  readonly property color coachColor: {
    var tone = root.coachCard ? root.coachCard.tone : "motivational"
    return tone === "drill" ? "#ff5a3c" : Color.accent
  }

  function loadCoach() {
    if (coachInfoProc.running) return
    coachInfoProc.command = ["/usr/bin/python3", "-I", root.launcher, "coach"]
    coachInfoProc.running = true
  }

  function coachSet(args) {
    if (coachSetProc.running) return
    coachSetProc.command = ["/usr/bin/python3", "-I", root.launcher].concat(args)
    coachSetProc.running = true
  }

  Process {
    id: coachInfoProc
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: coachInfoOut; waitForEnd: true }
    onExited: { try { root.coachInfo = JSON.parse(coachInfoOut.text) } catch (e) { } }
  }

  Process {                                             // "try one": shows a sample popup (it waits for it to close)
    id: coachTryProc
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
  }

  Process {
    id: coachSetProc
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    onExited: root.loadCoach()
  }

  // The FTP submenu: a stepper, an estimate from ride history (offered, never applied on its own), and Save.
  readonly property var ftpEstimate: (root.fitnessData && root.fitnessData.ftp_estimate) ? root.fitnessData.ftp_estimate : null

  function setFtp(watts) {
    root.ftpOpen = false
    root.menuOpen = false
    ftpProcess.command = ["/usr/share/omarchy/bin/omarchy", "bar", "set", root.moduleName, "ftp", String(watts), "--json"]
    ftpProcess.pendingFtp = watts
    ftpProcess.running = true
  }

  Process {
    id: ftpProcess
    property int pendingFtp: 0
    running: false
    command: []
    clearEnvironment: true
    environment: root.desktopEnvironment
    stderr: StdioCollector { id: ftpErr; waitForEnd: true }
    onExited: function(exitCode, exitStatus) {
      if (exitCode !== 0) { console.warn("lapbar: saving the FTP failed (" + exitCode + "): " + ftpErr.text); return }
      root.refresh(true, ftpProcess.pendingFtp)                  // recalculate at once, with the value just saved
    }
  }

  // LapBar's own logo (assets/logo), white on dark themes and black on light ones. It is shown larger than Strava's,
  // as Strava's guidelines ask (their logo must not be more prominent than the app's own).
  readonly property url lapbarLogo: Qt.resolvedUrl("assets/logo/lapbar_horiz_" + (Color.background.hslLightness < 0.5 ? "white" : "black") + ".svg")

  readonly property string repoUrl: "https://github.com/GSkrt/lapbar"
  // Strava's own "Powered by Strava" logo, white on dark themes and black on light ones (unmodified, see assets/strava/NOTICE.md).
  readonly property url stravaLogo: Qt.resolvedUrl("assets/strava/api_logo_pwrdBy_strava_horiz_"
                                                   + (Color.background.hslLightness < 0.5 ? "white" : "black") + ".svg")

  readonly property var menuItems: {
    var out = []
    if (root.needsSetup) {
      out.push({ label: root.errorCode === "not_configured" ? "Start setup" : "Sign in with Strava", action: "setup" })
      out.push({ label: "Open Strava API settings", action: "api" })
      out.push({ label: "How to use\u2026", action: "howto" })
      out.push({ label: "About LapBar", action: "about" })
      return out
    }
    out.push({ label: "Refresh now", action: "refresh" })
    out.push({ label: root.muted ? "Unmute kudos alerts" : "Mute kudos alerts", action: "mute" })
    out.push({ label: "Refresh every " + root.intervalLabel(root.refreshIntervalSec) + "\u2026", action: "interval" })
    out.push({ label: "FTP: " + (root.ftp > 0 ? root.ftp + " W" : "not set") + "\u2026", action: "ftp" })
    out.push({ label: "Manage data\u2026", action: "manage" })
    out.push({ label: "How to use\u2026", action: "howto" })
    out.push({ label: "Update credentials or sign in\u2026", action: "setup" })
    out.push({ label: "Open Strava API settings", action: "api" })
    out.push({ label: "About LapBar", action: "about" })
    out.push({ label: "Reset account\u2026", action: "reset", danger: true })
    return out
  }

  function runMenuAction(action) {
    if (action === "reset") { root.confirmReset = true; return }   // asks first; the menu stays open
    if (action === "about") { root.aboutOpen = true; return }
    if (action === "interval") { root.intervalOpen = true; return }
    if (action === "ftp") { root.ftpDraft = root.ftp > 0 ? root.ftp : (root.ftpEstimate ? root.ftpEstimate.watts : 200); root.ftpOpen = true; return }
    root.menuOpen = false
    if (action === "refresh") root.refresh(true)
    else if (action === "mute") root.setMuted("toggle")
    else if (action === "manage") root.openManage()
    else if (action === "howto") root.openHowto()
    else if (action === "setup") root.openSetup()
    else if (action === "api") root.openLink("https://www.strava.com/settings/api")
  }

  // Removes the saved Client ID/Secret, sign-in and cache (`lapbar reset`), then shows the welcome screen.
  function resetAccount() {
    root.menuOpen = false
    root.confirmReset = false
    resetProcess.command = ["/usr/bin/python3", "-I", root.launcher, "reset", "--yes"]
    resetProcess.running = true
  }

  Process {
    id: resetProcess
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: resetOut; waitForEnd: true }
    onExited: {
      root.summary = null
      root.selected = null
      root.selectedDay = ""
      root.errorCode = ""
      root.muted = false
      root.frameIndex = 0
      root.refresh(true)   // now reports "not set up", which shows the welcome screen
    }
  }

  // -------------------------------------------------------------- icons (Nerd Font, Material Design)

  // As on Strava's own pages: a medal for a personal record (PR) and a cup for a top place on a segment (KOM/QOM).
  // Kudos get a thumbs up, as on Strava.
  readonly property var glyphs: ({
    ride: 0xF00A3, run: 0xF070E, walk: 0xF0583, swim: 0xF04E3, paddle: 0xF08AF, winter: 0xF0717,
    skate: 0xF0D35, gym: 0xF01E6, other: 0xF140B,
    kudos: 0xF0513, pr: 0xF0987, kom: 0xF0538, up: 0xF005D, down: 0xF0045, even: 0xF01FC, week: 0xF00ED,
    bell: 0xF009A, bellOff: 0xF009B
  })

  function icon(name) { return String.fromCodePoint(root.glyphs[name] || root.glyphs.other) }

  // ------------------------------------------------------- training load vs last week
  //
  // This week so far is compared with last week up to the same moment (so on Wednesday you are
  // measured against last Wednesday, not a finished week), plus what is still needed to beat
  // last week's total. Measured in moving time by default, which works for every sport.

  readonly property string loadMetricEffective: {
    var hasEffort = !!(root.summary && root.summary.load && root.summary.load.has_effort)
    if (root.loadMetric === "effort" && hasEffort) return "effort"
    return root.loadMetric === "distance" ? "distance" : "time"
  }

  function loadValue(sums) {
    var m = root.loadMetricEffective
    return m === "distance" ? sums.distance_km : (m === "effort" ? sums.effort : sums.moving_time_s)
  }

  function fmtLoad(v) {
    var m = root.loadMetricEffective
    if (m === "distance") return v.toFixed(1) + " km"
    if (m === "effort") return Math.round(v) + " effort"
    return fmtDuration(v)
  }

  readonly property var loadInfo: {
    if (!root.summary || !root.summary.load) return null
    var L = root.summary.load
    var cur = root.loadValue(L.this_week)
    var same = root.loadValue(L.last_week_same_point)
    var total = root.loadValue(L.last_week)
    var delta = cur - same
    var state = (cur === 0 && total === 0) ? "idle"
              : (Math.abs(delta) <= same * 0.05 ? "even" : (delta > 0 ? "ahead" : "behind"))
    return { state: state, cur: cur, same: same, total: total, delta: delta,
             toBeat: Math.max(0, total - cur), beaten: total > 0 && cur >= total, daysLeft: L.days_left }
  }

  readonly property color loadColor: {
    var st = root.loadInfo ? root.loadInfo.state : ""
    return st === "ahead" ? "#4caf50" : (st === "behind" ? "#ff8a4c" : root.foreground)
  }

  function loadHeadline(info) {
    if (!info || info.state === "idle") return ""
    if (info.state === "even") return "On par with last week"
    return root.fmtLoad(Math.abs(info.delta)) + (info.state === "ahead" ? " ahead of" : " behind")
           + " last week at this point"
  }

  function loadDetail(info) {
    if (!info) return ""
    if (info.total === 0) return "No training last week to compare"
    if (info.beaten) return "Last week's total (" + root.fmtLoad(info.total) + ") already beaten"
    return root.fmtLoad(info.toBeat) + " to beat last week's " + root.fmtLoad(info.total) + " · "
           + info.daysLeft + (info.daysLeft === 1 ? " day" : " days") + " left"
  }

  // ------------------------------------------------------------------ bar button frames
  //
  // The bar button cycles through these; each frame is a glyph plus a short value.

  readonly property var frames: {
    var out = []
    var l = root.latest
    if (l) out.push(root.icon(l.family) + "  " + (l.distance_km > 0 ? fmtDistance(l.distance_km, l.family) : fmtDuration(l.moving_time_s)))
    var wk = root.summary ? root.summary.week : null
    if (wk && wk.count > 0) out.push(root.icon("week") + "  " + (wk.distance_km > 0 ? fmtDistance(wk.distance_km, "") : fmtDuration(wk.moving_time_s)))
    if (l && (l.kudos > 0 || l.prs > 0))
      out.push(root.icon("kudos") + " " + l.kudos + (l.prs > 0 ? "  " + root.icon("pr") + " " + l.prs : ""))
    var li = root.loadInfo
    if (li && li.state !== "idle")
      out.push(root.icon(li.state === "ahead" ? "up" : (li.state === "behind" ? "down" : "even")) + "  "
               + (li.state === "even" ? "on par" : root.fmtLoad(Math.abs(li.delta))))
    var fn = root.fitnessNow
    if (fn && fn.status !== "starting") out.push(root.icon(fn.form >= 0 ? "up" : "down") + "  Form " + root.signed(fn.form))
    return out
  }

  readonly property string barText: {
    if (root.frames.length === 0) return root.needsSetup ? "LapBar: set up" : "…"
    return root.frames[root.frameIndex % root.frames.length]
  }

  readonly property var fitnessData: (root.summary && root.summary.fitness) ? root.summary.fitness : null
  readonly property var fitnessNow: root.fitnessData ? root.fitnessData.current : null
  readonly property bool darkTheme: Color.background.hslLightness < 0.5
  readonly property color fitnessColor: root.darkTheme ? "#3987e5" : "#2a78d6"
  readonly property color fatigueColor: root.darkTheme ? "#d95926" : "#eb6834"

  readonly property var fitnessStatusText: ({
    starting: "Just getting started: a few weeks of activity are needed before this means much",
    very_fresh: "Very fresh: rested and ready for a hard effort",
    fresh: "Fresh: a good time for a quality session",
    balanced: "Balanced: neither rested nor worn down",
    building: "Building: a little tired, which is normal in a training block",
    overreaching: "Very tired: an easy day or a rest day would help"
  })

  readonly property var fitnessStatusName: ({
    starting: "Getting started", very_fresh: "Very fresh", fresh: "Fresh", balanced: "Balanced",
    building: "Building", overreaching: "Very tired"
  })

  function signed(v) {
    var n = Math.round(v)
    return n > 0 ? "+" + n : (n < 0 ? "\u2212" + Math.abs(n) : "0")
  }

  readonly property var budget: root.summary ? root.summary.budget : null

  readonly property string budgetLine: {
    if (!root.budget) return ""
    var line = "Strava requests today: " + root.budget.daily_used + " of " + root.budget.daily_limit
    if (root.budget.auto_paused) line += " \u00b7 auto-refresh paused, manual refresh still works"
    return line
  }

  // How far the background download of every activity's full time series has got.
  readonly property string archiveLine: {
    var a = root.summary ? root.summary.archive : null
    if (!a || a.total === 0) return ""
    if (a.known >= a.total) return "\u25cf All " + a.total + " activities are stored on this computer"
    return "\u25cf Full time series stored: " + a.stored + " of " + a.total + " activities" + (root.downloadHistory > 0 ? " \u00b7 downloading in the background" : "")
  }

  readonly property string barTooltip: {
    if (root.needsSetup) return "LapBar · click to set up"
    if (!root.latest) return root.errorCode ? "LapBar · " + root.errorMessage : "LapBar · Loading…"
    var l = root.latest
    var lines = []
    var when = new Date(root.summary.updated)
    lines.push("Strava updated " + (isNaN(when.getTime()) ? "" : Qt.formatDateTime(when, "ddd HH:mm")))
    lines.push(l.name + " · " + root.sportName(l.sport))
    lines.push(l.kudos + (l.kudos === 1 ? " kudo" : " kudos") + "  ·  " + l.prs + (l.prs === 1 ? " PR" : " PRs"))
    var headline = root.loadHeadline(root.loadInfo)
    if (headline) lines.push(headline)
    if (root.fitnessNow && root.fitnessNow.status !== "starting")
      lines.push("Form " + root.signed(root.fitnessNow.form) + " (" + (root.fitnessStatusName[root.fitnessNow.status] || "").toLowerCase()
                 + ") \u00b7 fitness " + Math.round(root.fitnessNow.fitness) + " \u00b7 fatigue " + Math.round(root.fitnessNow.fatigue))
    if (root.budgetLine !== "") lines.push(root.budgetLine)
    if (root.muted) lines.push("Kudos alerts muted")
    if (root.errorCode) lines.push("Last refresh failed: " + root.errorMessage)
    return lines.join("\n")
  }

  // ------------------------------------------------------------------ kudos notifications
  //
  // Sent through the shell's own notification service, so Omarchy's do-not-disturb silences them
  // too; the mute switch below is a lapbar-only version of the same thing.

  function kudosNotification(ev) {
    var names = ev.from || []
    var who = names.length ? names.slice(0, 4).join(", ") + (names.length > 4 ? " and " + (names.length - 4) + " more" : "") : ""
    return {
      title: root.icon("kudos") + "  " + ev.count + (ev.count === 1 ? " new kudo" : " new kudos"),
      body: (who ? who + " on " : "On ") + "\u201c" + ev.name + "\u201d \u00b7 " + ev.total + " total"
    }
  }

  function notifyKudos(events) {
    if (root.muted) return
    for (var i = 0; i < events.length; i++) {
      var n = root.kudosNotification(events[i])
      Quickshell.execDetached({
        command: ["/usr/bin/notify-send", "-a", "lapbar", "-u", "normal", "-t", "10000", n.title, n.body],
        clearEnvironment: true,
        environment: root.desktopEnvironment
      })
    }
  }

  function setMuted(state) {  // "on" | "off" | "toggle"
    if (muteProcess.running) return
    muteProcess.command = ["/usr/bin/python3", "-I", root.launcher, "mute", state]
    muteProcess.running = true
  }

  // ---------------------------------------------------------------- fetching

  // `manual` is true for anything the user triggered (button, menu, opening the popup) and false for the timer.
  // Manual refreshes may use more of Strava's daily allowance than the timer, so there is always room for them.
  // `ftpOverride` is for the moment right after the FTP was saved, before the setting has been read back.
  function refresh(manual, ftpOverride) {
    if (fetchProcess.running) return
    var cmd = ["/usr/bin/python3", "-I", root.launcher, "fetch", "--print", "--backfill", String(root.downloadHistory)]
    var ftpNow = ftpOverride !== undefined ? ftpOverride : root.ftp
    if (ftpNow > 0) cmd.push("--ftp", String(ftpNow))
    if (root.historyYears > 0) cmd.push("--history-years", String(root.historyYears))
    if (manual !== false) cmd.push("--manual")
    fetchProcess.command = cmd
    fetchProcess.running = true
  }

  function handleResult(text) {
    var parsed = null
    try { parsed = JSON.parse(text) } catch (e) { parsed = null }
    if (!parsed) {
      root.errorCode = "unexpected"
      root.errorMessage = "Unexpected output from LapBar"
      return
    }
    if (parsed.error) {
      // Keep showing the last good data; only the status line changes.
      root.errorCode = parsed.error
      root.errorMessage = parsed.message || parsed.error
      return
    }
    root.summary = parsed
    var signature = (parsed.history && parsed.history.years) ? parsed.history.years.join(",") : ""
    if (signature !== root.historySignature) {     // more years were stored since: read them again when needed
      root.historySignature = signature
      root.historyByYear = ({})
      root.loadHistoryYear(root.viewYear)
    }
    if (root.selected) {  // re-resolve by id so kudos etc. update, and drop it if it vanished
      var fresh = null
      for (var i = 0; i < parsed.activities.length; i++)
        if (parsed.activities[i].id === root.selected.id) { fresh = parsed.activities[i]; break }
      // an activity from an older year is not in this year's list and does not change: keep showing it
      if (!fresh && String(root.selected.start).slice(0, 4) !== String(root.today.getFullYear())) fresh = root.selected
      root.selected = fresh
      if (!fresh) root.selectedDay = ""
    }
    root.errorCode = ""
    root.errorMessage = ""
    root.lastUpdatedAt = Date.now()
    root.muted = !!parsed.muted
    root.setupWatching = false
    if (parsed.kudos_events && parsed.kudos_events.length > 0) root.notifyKudos(parsed.kudos_events)
    root.loadCoach()
  }

  function openSetup() {
    root.close()
    Quickshell.execDetached({
      command: ["/usr/share/omarchy/bin/omarchy-launch-floating-terminal-with-presentation",
                "/usr/bin/python3", "-I", root.launcher, "setup"],
      clearEnvironment: true,
      environment: root.desktopEnvironment
    })
    // The terminal launcher returns immediately, so poll until setup has finished.
    root.setupWatchTicks = 0
    root.setupWatching = true
  }

  function openLink(url) {
    Quickshell.execDetached({
      command: ["/usr/bin/xdg-open", url],
      clearEnvironment: true,
      environment: root.desktopEnvironment
    })
  }

  property string chartsMessage: ""

  // Opens the chart window (its own process) for the shown activity; data is downloaded once, then read from disk.
  function openCharts() {
    if (!root.shown || chartsProcess.running) return
    root.chartsMessage = ""
    chartsProcess.command = ["/usr/bin/python3", "-I", root.launcher, "charts", String(root.shown.id),
      "--fg", String(root.foreground), "--bg", String(Color.background), "--font", root.fontFamily]
    chartsProcess.running = true
  }

  Process {
    id: chartsProcess
    running: false
    command: []
    clearEnvironment: true
    environment: root.desktopEnvironment
    stdout: StdioCollector { id: chartsOut; waitForEnd: true }
    onExited: {
      var out = null
      try { out = JSON.parse(chartsOut.text) } catch (e) { out = null }
      root.chartsMessage = (out && out.error) ? (out.message || out.error) : (out ? "" : "Could not open the charts")
    }
  }

  // The full-size fitness chart window (same window as the activity charts, with a date axis). Reads what was
  // already fetched, so it never costs a request.
  function openFitnessChart() {
    if (chartsProcess.running) return
    root.chartsMessage = ""
    chartsProcess.command = ["/usr/bin/python3", "-I", root.launcher, "fitness",
      "--fg", String(root.foreground), "--bg", String(Color.background), "--font", root.fontFamily]
    chartsProcess.running = true
  }

  // The data window: history stored, fetching by day, and the DuckDB export (its own process, like the charts).
  function openManage() {
    if (chartsProcess.running) return
    chartsProcess.command = ["/usr/bin/python3", "-I", root.launcher, "manage",
      "--fg", String(root.foreground), "--bg", String(Color.background), "--accent", String(Color.accent), "--font", root.fontFamily]
    chartsProcess.running = true
  }

  // The how-to window: the guide from docs/help.md, drawn inside the app (its own process, like the charts).
  function openHowto() {
    if (chartsProcess.running) return
    chartsProcess.command = ["/usr/bin/python3", "-I", root.launcher, "howto",
      "--fg", String(root.foreground), "--bg", String(Color.background), "--accent", String(Color.accent), "--font", root.fontFamily]
    chartsProcess.running = true
  }

  function openOnStrava() {
    if (root.shown && root.shown.url) root.openLink(root.shown.url)
  }

  Process {
    id: fetchProcess
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: fetchOut; waitForEnd: true }
    onExited: root.handleResult(fetchOut.text)
  }

  Process {
    id: historyProcess
    property int wantedYear: 0
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: historyOut; waitForEnd: true }
    onExited: root.handleHistory(historyProcess.wantedYear, historyOut.text)
  }

  Process {
    id: detailsProcess
    property var wantedId: 0
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: detailsOut; waitForEnd: true }
    onExited: root.handleDetails(detailsProcess.wantedId, detailsOut.text)
  }

  Process {
    id: muteProcess
    running: false
    command: []
    clearEnvironment: true
    environment: root.fetchEnvironment
    stdout: StdioCollector { id: muteOut; waitForEnd: true }
    onExited: {
      try { root.muted = !!JSON.parse(muteOut.text).muted } catch (e) { }
    }
  }

  Timer {
    // Cycles the bar button through its frames; paused while the popup is open.
    interval: Math.max(1, root.cycleIntervalSec) * 1000
    running: root.cycleIntervalSec > 0 && root.frames.length > 1 && !root.opened
    repeat: true
    onTriggered: root.frameIndex = (root.frameIndex + 1) % root.frames.length
  }

  Timer {
    // Backstop for a hung request; a normal fetch takes a second or two.
    interval: 45000
    running: fetchProcess.running
    repeat: false
    onTriggered: fetchProcess.signal(15)
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh(false)
  }

  Timer {
    // While the setup terminal is open, check every few seconds whether it has finished (up to 20 min).
    interval: 4000
    running: root.setupWatching && root.needsSetup
    repeat: true
    onTriggered: {
      root.refresh(true)
      if (++root.setupWatchTicks > 300) root.setupWatching = false
    }
  }

  onOpenedChanged: {
    if (opened) {
      root.today = new Date()
      root.viewYear = root.today.getFullYear()
      root.viewMonth = root.today.getMonth()
      root.backToLatest()
      root.menuOpen = false
      root.confirmReset = false
      root.aboutOpen = false
      root.intervalOpen = false
      root.ftpOpen = false
      root.loadDetails()
      root.loadCoach()
      if (Date.now() - root.lastUpdatedAt > 120000) root.refresh(true)
    }
  }

  // -------------------------------------------------------------- bar button

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText
    dimmed: !root.latest
    active: !!root.latest
    useActiveColor: true
    activeColor: Color.accent
    tooltipText: root.barTooltip

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh(true)
      else if (b === Qt.RightButton) root.setMuted("toggle")
      else root.toggle()
    }
  }

  // ------------------------------------------------------------------- panel

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(root.wide ? 1000 : 340))
    contentHeight: panel.fittedContentHeight(root.popupHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Row {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(24)

        readonly property real colWidth: root.wide ? (width - spacing * 2) / 3 : width

        // ---- left: the activity itself
        Column {
          id: leftCol
          onImplicitHeightChanged: Qt.callLater(root.fitPlot)
          width: layout.colWidth
          spacing: Style.spacing.panelGap

        // ---------- brand ----------

        Image {                                   // fills the left column; the logo's viewBox is 285 x 48
          source: root.lapbarLogo
          width: parent.width
          height: width * 48 / 285
          sourceSize.width: 900
          fillMode: Image.PreserveAspectFit
          horizontalAlignment: Image.AlignLeft
        }

        // ---------- header ----------

        Item {
          z: 20   // so the dropdown draws over the content underneath
          width: parent.width
          height: Math.max(headerText.implicitHeight, refreshButton.implicitHeight)

          Column {
            id: headerText
            anchors.left: parent.left
            anchors.right: menuButton.left
            anchors.rightMargin: Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter

            Text {
              width: parent.width
              textFormat: Text.PlainText
              elide: Text.ElideRight
              text: root.shown ? root.shown.name : "LapBar"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.heading
              font.bold: true
            }

            Text {
              width: parent.width
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              text: {
                if (!root.shown) return root.errorCode ? "" : "Loading…"
                var d = root.startDate(root.shown)
                var when = d ? Qt.formatDateTime(d, "ddd d MMM · HH:mm") : ""
                var where = root.shown.indoor ? " · indoor" : ""
                return root.sportName(root.shown.sport) + where + (when ? " · " + when : "")
              }
            }

            Text {
              visible: root.showingOlder
              text: "↩ Back to latest activity"
              color: root.stravaOrange
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.backToLatest()
              }
            }
          }

          PanelActionButton {
            id: menuButton
            anchors.right: refreshButton.left
            anchors.rightMargin: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            iconText: String.fromCodePoint(0xF01D9)
            tooltipText: "Menu"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: { root.confirmReset = false; root.aboutOpen = false; root.intervalOpen = false; root.ftpOpen = false; root.menuOpen = !root.menuOpen }
          }

          Rectangle {
            id: menuPanel
            visible: root.menuOpen
            z: 50
            anchors.top: parent.bottom
            anchors.right: parent.right
            anchors.topMargin: Style.space(4)
            width: root.aboutOpen ? Style.space(350) : ((root.intervalOpen || root.ftpOpen) ? Style.space(320) : Style.space(270))
            height: menuColumn.implicitHeight + Style.space(12)
            radius: Style.space(6)
            color: Color.background
            border.width: 1
            border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.3)

            Column {
              id: menuColumn
              anchors.fill: parent
              anchors.margins: Style.space(6)

              Repeater {
                model: (root.confirmReset || root.aboutOpen || root.intervalOpen || root.ftpOpen) ? [] : root.menuItems

                Item {
                  id: menuRow
                  required property var modelData
                  width: menuColumn.width
                  height: Style.space(28)

                  Rectangle {
                    anchors.fill: parent
                    radius: Style.space(4)
                    color: rowMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent"
                  }

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    text: menuRow.modelData.label
                    color: menuRow.modelData.danger ? "#ff6b5e" : root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  MouseArea {
                    id: rowMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.runMenuAction(menuRow.modelData.action)
                  }
                }
              }

              // Refresh interval: pick how often lapbar asks Strava. The daily allowance is protected either way.
              Column {
                visible: root.intervalOpen
                width: menuColumn.width
                spacing: Style.space(2)
                topPadding: Style.space(4)
                bottomPadding: Style.space(6)

                Text {
                  x: Style.space(8)
                  text: "Refresh Strava data"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  bottomPadding: Style.space(4)
                }

                Repeater {
                  model: root.intervalChoices

                  Item {
                    id: intervalRow
                    required property var modelData
                    readonly property bool current: modelData.sec === root.refreshIntervalSec
                    width: menuColumn.width
                    height: Style.space(28)

                    Rectangle {
                      anchors.fill: parent
                      radius: Style.space(4)
                      color: intervalMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent"
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.space(8)
                      anchors.verticalCenter: parent.verticalCenter
                      text: (intervalRow.current ? "\u2713  " : "    ") + intervalRow.modelData.label
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: intervalRow.current
                    }

                    Text {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.space(8)
                      anchors.verticalCenter: parent.verticalCenter
                      text: "~" + root.intervalCost(intervalRow.modelData.sec) + " requests/day"
                      color: root.intervalCost(intervalRow.modelData.sec) > 600 ? Qt.rgba(1, 0.55, 0.4, 1) : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }

                    MouseArea {
                      id: intervalMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.setRefreshInterval(intervalRow.modelData.sec)
                    }
                  }
                }

                Text {
                  x: Style.space(8)
                  width: parent.width - Style.space(16)
                  topPadding: Style.space(6)
                  wrapMode: Text.WordWrap
                  text: "Strava allows about 1,000 read requests a day. Automatic refreshes pause when 60% of that is used, so refreshing by hand (the button, or opening this panel) always has room. Orange means the timer alone would reach that point before the day ends."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  x: Style.space(8)
                  topPadding: Style.space(4)
                  text: "\u2039 Back"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall

                  MouseArea {
                    anchors.fill: parent
                    anchors.margins: -Style.space(4)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.intervalOpen = false
                  }
                }
              }

              // FTP: your best hour of power. Type it in with the stepper, or ask for an estimate from your rides.
              Column {
                visible: root.ftpOpen
                width: menuColumn.width
                spacing: Style.space(6)
                topPadding: Style.space(4)
                bottomPadding: Style.space(6)

                Text {
                  x: Style.space(8)
                  text: "Functional threshold power (FTP)"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Row {
                  x: Style.space(8)
                  spacing: Style.space(6)

                  Repeater {
                    model: [{ t: "\u221210", d: -10 }, { t: "\u22121", d: -1 }]
                    Rectangle {
                      required property var modelData
                      width: Style.space(40); height: Style.space(30); radius: Style.space(4)
                      color: minusMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.2) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.1)
                      Text { anchors.centerIn: parent; text: parent.modelData.t; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                      MouseArea { id: minusMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                  onClicked: root.ftpDraft = Math.max(0, root.ftpDraft + parent.modelData.d) }
                    }
                  }

                  Text {
                    width: Style.space(84)
                    height: Style.space(30)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: root.ftpDraft > 0 ? root.ftpDraft + " W" : "not set"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.display
                    font.bold: true
                  }

                  Repeater {
                    model: [{ t: "+1", d: 1 }, { t: "+10", d: 10 }]
                    Rectangle {
                      required property var modelData
                      width: Style.space(40); height: Style.space(30); radius: Style.space(4)
                      color: plusMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.2) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.1)
                      Text { anchors.centerIn: parent; text: parent.modelData.t; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                      MouseArea { id: plusMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                  onClicked: root.ftpDraft = Math.min(600, root.ftpDraft + parent.modelData.d) }
                    }
                  }
                }

                Rectangle {                                         // estimate from ride history
                  x: Style.space(8)
                  width: parent.width - Style.space(16)
                  height: Style.space(30)
                  radius: Style.space(4)
                  opacity: root.ftpEstimate ? 1 : 0.5
                  color: estMouse.containsMouse && root.ftpEstimate ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.2) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.1)
                  Text {
                    anchors.centerIn: parent
                    text: root.ftpEstimate ? "Estimate from my rides: " + root.ftpEstimate.watts + " W" : "Estimate from my rides"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }
                  MouseArea { id: estMouse; anchors.fill: parent; hoverEnabled: true; enabled: !!root.ftpEstimate
                              cursorShape: Qt.PointingHandCursor; onClicked: root.setFtp(root.ftpEstimate.watts) }
                }

                Text {
                  x: Style.space(8)
                  width: parent.width - Style.space(16)
                  wrapMode: Text.WordWrap
                  text: root.ftpEstimate
                    ? "Sets your FTP to the highest weighted power in your " + root.ftpEstimate.rides + " power rides of 40 minutes or more. A ballpark: for an exact figure, do an FTP test. You can adjust it afterwards."
                    : "Needs at least 5 rides of 40 minutes or more recorded with a power meter. Not enough of them yet: type your FTP if you know it."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Row {
                  x: Style.space(8)
                  spacing: Style.space(6)

                  Rectangle {
                    width: Style.space(90); height: Style.space(30); radius: Style.space(4)
                    color: saveMouse.containsMouse ? Qt.rgba(1, 0.32, 0.01, 1) : Qt.rgba(1, 0.32, 0.01, 0.85)
                    Text { anchors.centerIn: parent; text: "Save"; color: "#ffffff"; font.family: root.fontFamily; font.pixelSize: Style.font.body; font.bold: true }
                    MouseArea { id: saveMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: root.setFtp(root.ftpDraft) }
                  }

                  Rectangle {
                    visible: root.ftp > 0
                    width: Style.space(110); height: Style.space(30); radius: Style.space(4)
                    color: clearMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.2) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.1)
                    Text { anchors.centerIn: parent; text: "Clear FTP"; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                    MouseArea { id: clearMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: root.setFtp(0) }
                  }
                }

                Text {
                  x: Style.space(8)
                  topPadding: Style.space(2)
                  text: "\u2039 Back"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall

                  MouseArea {
                    anchors.fill: parent
                    anchors.margins: -Style.space(4)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.ftpOpen = false
                  }
                }
              }

              // About: what this is, where to find the project, and an invitation to contribute.
              Column {
                visible: root.aboutOpen
                width: menuColumn.width
                spacing: Style.space(8)
                topPadding: Style.space(6)
                bottomPadding: Style.space(6)
                leftPadding: Style.space(8)
                rightPadding: Style.space(8)
                readonly property real inner: width - leftPadding - rightPadding

                Text {
                  text: "LapBar" + (root.version ? "  v" + root.version : "")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                }

                Text {
                  width: parent.inner
                  wrapMode: Text.WordWrap
                  text: "Your Strava activity in the Omarchy bar. Free software under the GPL-3.0-or-later."
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Text {
                  width: parent.inner
                  wrapMode: Text.WordWrap
                  text: "Improvements are welcome! Send a pull request or an idea. Sports other than cycling especially: if a number looks wrong for your run or swim, that is a bug worth reporting."
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Row {
                  spacing: Style.space(14)

                  Repeater {
                    model: [
                      { label: "Project on GitHub", url: root.repoUrl },
                      { label: "Contribute", url: root.repoUrl + "/blob/main/CONTRIBUTING.md" },
                      { label: "Report an issue", url: root.repoUrl + "/issues/new/choose" }
                    ]

                    Text {
                      required property var modelData
                      text: modelData.label + " \u2197"
                      color: root.stravaOrange
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      font.bold: true

                      MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.openLink(parent.modelData.url)
                      }
                    }
                  }
                }

                Text {
                  width: parent.inner
                  wrapMode: Text.WordWrap
                  text: "Thanks to Strava for the platform and its API, and to the stravalib developers for their open-source Python client for that API, a great resource for anyone building on Strava."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  text: "stravalib on GitHub \u2197"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.openLink("https://github.com/stravalib/stravalib")
                  }
                }

                Image {
                  source: root.stravaLogo
                  sourceSize.height: 48
                  height: Style.space(18)
                  fillMode: Image.PreserveAspectFit
                  horizontalAlignment: Image.AlignLeft
                }

                Text {
                  width: parent.inner
                  wrapMode: Text.WordWrap
                  text: "LapBar is not affiliated with or endorsed by Strava."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                Text {
                  text: "\u2039 Back"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall

                  MouseArea {
                    anchors.fill: parent
                    anchors.margins: -Style.space(4)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.aboutOpen = false
                  }
                }
              }

              // Reset asks for confirmation before removing anything.
              Column {
                visible: root.confirmReset
                width: menuColumn.width
                spacing: Style.space(8)
                topPadding: Style.space(4)
                bottomPadding: Style.space(4)

                Text {
                  width: parent.width - Style.space(16)
                  x: Style.space(8)
                  wrapMode: Text.WordWrap
                  text: "Remove your saved Client ID, Client Secret and sign-in from this computer? You can set up again afterwards. Your Strava app and activities are not touched."
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Row {
                  x: Style.space(8)
                  spacing: Style.space(20)

                  Text {
                    text: "Reset account"
                    color: "#ff6b5e"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.resetAccount()
                    }
                  }

                  Text {
                    text: "Cancel"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    MouseArea {
                      anchors.fill: parent
                      cursorShape: Qt.PointingHandCursor
                      onClicked: { root.confirmReset = false; root.menuOpen = false }
                    }
                  }
                }
              }
            }
          }

          PanelActionButton {
            id: refreshButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: String.fromCodePoint(0xF0450)
            tooltipText: "Refresh"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.refresh(true)

            RotationAnimation on rotation {
              running: fetchProcess.running
              from: 0
              to: 360
              duration: 900
              loops: Animation.Infinite
              onRunningChanged: if (!running) rotation = 0
            }
          }
        }

        PanelSeparator { foreground: root.foreground }

        // ---------- records and kudos, right under the ride's description ----------

        Column {
          id: achievementsBox
          visible: root.wantsAchievements
          width: parent.width
          spacing: Style.spacing.sm

          Item {
            width: parent.width
            height: achievementsToggle.implicitHeight + Style.space(4)

            Text {
              id: achievementsToggle
              anchors.verticalCenter: parent.verticalCenter
              text: (root.achievementsOpen ? "\u25be  " : "\u25b8  ") + "Records & kudos"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Text {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              visible: !root.achievementsOpen
              text: root.achievementsSummary
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.achievementsPref = root.achievementsOpen ? -1 : 1
            }
          }

          Text {
            visible: root.achievementsOpen && detailsProcess.running && root.shownRecords.length === 0 && root.shownKudoers.length === 0
            text: "Loading\u2026"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          // PRs (a medal) and top places (a cup), with the time each was achieved in
          Column {
            id: recordsTable
            visible: root.achievementsOpen && root.shownRecords.length > 0
            width: parent.width
            spacing: Style.space(2)

            Repeater {
              model: root.achievementsAll ? root.shownRecords : root.shownRecords.slice(0, 6)

              Item {
                id: recordRow
                required property var modelData
                width: recordsTable.width
                height: Style.space(22)

                Text {
                  id: recordIcon
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(22)
                  text: root.icon(recordRow.modelData.kind)
                  color: root.medalColor(recordRow.modelData)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  id: recordLabel
                  anchors.left: recordIcon.right
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(92)
                  elide: Text.ElideRight
                  text: root.recordLabel(recordRow.modelData)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }

                Text {
                  id: recordTime
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.fmtEffort(recordRow.modelData.seconds)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }

                Text {
                  anchors.left: recordLabel.right
                  anchors.right: recordTime.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  text: recordRow.modelData.name
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }
          }

          // the kudos, with a large thumbs up
          KudosBadge {
            visible: root.achievementsOpen && !!root.shown && root.shown.kudos > 0
            count: root.shown ? root.shown.kudos : 0
            glyph: root.icon("kudos")
            foreground: root.foreground
            dim: root.dim
            fontFamily: root.fontFamily
            iconSize: Style.space(40)
            numberSize: Style.font.display
            captionSize: Style.font.caption
          }

          // who gave them, as Strava names people (first name and last initial)
          Column {
            id: kudosTable
            visible: root.achievementsOpen && root.shownKudoers.length > 0
            width: parent.width
            spacing: Style.space(2)

            Grid {
              columns: 2
              columnSpacing: Style.space(12)
              rowSpacing: Style.space(2)
              width: parent.width

              Repeater {
                model: root.achievementsAll ? root.shownKudoers : root.shownKudoers.slice(0, 12)

                Text {
                  required property var modelData
                  width: (kudosTable.width - Style.space(12)) / 2
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  text: modelData
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }

            Text {
              visible: !!root.shown && root.shown.kudos > root.shownKudoers.length
              text: "and " + (root.shown ? root.shown.kudos - root.shownKudoers.length : 0) + " more that Strava does not list"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            visible: root.achievementsOpen && (root.shownRecords.length > 6 || root.shownKudoers.length > 12)
            text: root.achievementsAll ? "Show fewer" : "Show all (" + (root.shownRecords.length + root.shownKudoers.length) + ")"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.underline: true

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.achievementsAll = !root.achievementsAll
            }
          }
        }

        // ---------- welcome / setup ----------

        Column {
          id: onboarding
          visible: root.needsSetup
          width: parent.width
          spacing: Style.spacing.lg

          Text {
            width: parent.width
            text: root.errorCode === "not_configured" ? "Welcome to LapBar" : "One more step"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: root.errorCode === "not_configured"
              ? "LapBar shows your latest Strava activity in the bar. Strava makes every app use its own API credentials, so setup registers a small private app under your own Strava account. It takes about two minutes.\n\nYou need a Strava account with an active subscription (Strava's rule for creating API apps)."
              : "Your Strava app is registered. Sign in once so LapBar can read your activities."
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            text: (root.errorCode === "not_configured" ? "Start setup" : "Sign in with Strava") + "  \u2192"
            color: root.stravaOrange
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openSetup()
            }
          }

          Text {
            visible: root.errorCode === "not_configured"
            text: "Open Strava API settings  \u2197"
            color: root.stravaOrange
            font.family: root.fontFamily
            font.pixelSize: Style.font.body

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.openLink("https://www.strava.com/settings/api")
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "The guided setup opens in a terminal and tells you exactly what to click and paste. Your secret is stored in the system keyring."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Column {
          visible: root.errorCode !== "" && !root.needsSetup
          width: parent.width

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: root.errorMessage
            color: Qt.rgba(1, 0.55, 0.4, 1)
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }
        }

        // ---------- route trace ----------

        Item {
          id: routeBox
          visible: root.route.length > 1
          width: parent.width
          height: Style.space(170)

          readonly property real inset: Style.space(10)

          function toPoints(side) {
            var out = []
            var span = side - 2 * routeBox.inset
            for (var i = 0; i < root.route.length; i++)
              out.push(Qt.point(routeBox.inset + root.route[i][0] * span, routeBox.inset + root.route[i][1] * span))
            return out
          }

          // Start/finish markers; (0, 0) while the route is still empty so bindings never fault.
          function pointAt(side, index) {
            if (root.route.length < 2) return Qt.point(0, 0)
            var i = index < 0 ? root.route.length + index : index
            var span = side - 2 * routeBox.inset
            return Qt.point(routeBox.inset + root.route[i][0] * span, routeBox.inset + root.route[i][1] * span)
          }

          Shape {
            id: routeShape
            anchors.centerIn: parent
            width: parent.height
            height: parent.height
            antialiasing: true

            ShapePath {
              strokeColor: Color.accent
              strokeWidth: 3
              fillColor: "transparent"
              capStyle: ShapePath.RoundCap
              joinStyle: ShapePath.RoundJoin

              PathPolyline { path: routeBox.toPoints(routeShape.width) }
            }

            // start (green) and finish (orange)
            Rectangle {
              readonly property var p: routeBox.pointAt(routeShape.width, 0)
              x: p.x - width / 2; y: p.y - height / 2
              width: Style.space(15); height: width; radius: width / 2
              color: "#4caf50"
            }

            Rectangle {
              readonly property var p: routeBox.pointAt(routeShape.width, -1)
              x: p.x - width / 2; y: p.y - height / 2
              width: Style.space(9); height: width; radius: width / 2
              color: root.stravaOrange
            }
          }
        }

        // ---------- links, right below the route ----------

        Item {
          visible: !!(root.shown && root.shown.url)
          width: parent.width
          height: linkRow.implicitHeight

          Row {
            id: linkRow
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(18)

            Text {
              text: "View on Strava"
              color: root.stravaOrange
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openOnStrava()
              }
            }

            Text {
              text: chartsProcess.running ? "Opening charts\u2026" : "Open charts  \u2197"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openCharts()
              }
            }
          }
        }

        Text {
          visible: root.chartsMessage !== ""
          width: parent.width
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.WordWrap
          text: root.chartsMessage
          color: Qt.rgba(1, 0.55, 0.4, 1)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        // ---------- stats ----------

        Grid {
          visible: !!root.shown
          width: parent.width
          columns: 2
          columnSpacing: Style.spacing.lg
          rowSpacing: Style.spacing.lg

          Repeater {
            model: root.shown ? root.statCells(root.shown) : []

            Column {
              required property var modelData
              width: (leftCol.width - Style.spacing.lg) / 2

              Text {
                text: modelData.label
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              Text {
                text: modelData.value
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
              }
            }
          }
        }

        Text {
          visible: root.socialText !== ""
          width: parent.width
          text: root.socialText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        Text {
          visible: root.archiveLine !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          text: root.archiveLine
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          visible: root.budgetLine !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          text: root.budgetLine
          color: (root.budget && root.budget.auto_paused) ? Qt.rgba(1, 0.55, 0.4, 1) : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          visible: !root.needsSetup
          text: root.icon(root.muted ? "bellOff" : "bell") + "  Kudos alerts: " + (root.muted ? "muted" : "on")
          color: root.muted ? root.dim : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.setMuted("toggle")
          }
        }


        }

        // ---- right: how you are doing
        Column {
          id: rightCol
          onImplicitHeightChanged: Qt.callLater(root.fitPlot)
          visible: root.wide
          width: layout.colWidth
          spacing: Style.spacing.panelGap

          Rectangle {                                            // a nudge, with the data behind it
            visible: root.coachVisible
            width: parent.width
            height: coachBody.implicitHeight + Style.space(16)
            radius: Style.space(6)
            color: Qt.rgba(root.coachColor.r, root.coachColor.g, root.coachColor.b, 0.10)
            border.width: 1
            border.color: root.coachColor

            Column {
              id: coachBody
              x: Style.space(10)
              y: Style.space(8)
              width: parent.width - Style.space(34)
              spacing: Style.space(2)

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.coachCard ? root.coachCard.text : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.coachCard ? root.coachCard.reason + "." : ""
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Text {                                               // dismiss until a different nudge comes
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.margins: Style.space(8)
              text: "\u00d7"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body

              MouseArea {
                anchors.fill: parent
                anchors.margins: -Style.space(6)
                cursorShape: Qt.PointingHandCursor
                onClicked: root.coachDismissed = root.coachCard.id + root.coachCard.text
              }
            }
          }


        // ---------- fitness, fatigue and form: the plot the rest is built on ----------

        Column {
          id: fitnessBlock
          visible: !!root.fitnessData
          width: parent.width
          spacing: Style.spacing.md

          Item {
            width: parent.width
            height: fitnessTitle.implicitHeight

            Text {
              id: fitnessTitle
              text: "Fitness, fatigue & form"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Text {
              anchors.right: parent.right
              text: "Open chart  \u2197"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.openFitnessChart()
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(16)

            Repeater {
              model: [
                { label: "Fitness", key: "fitness", color: root.fitnessColor },
                { label: "Fatigue", key: "fatigue", color: root.fatigueColor },
                { label: "Form", key: "form", color: "transparent" }
              ]

              Column {
                required property var modelData
                readonly property var day: fitnessPlot.shownDay

                Row {
                  spacing: Style.space(5)
                  Rectangle {                                   // line key (form has none: its bars are coloured by sign)
                    visible: modelData.key !== "form"
                    anchors.verticalCenter: parent.verticalCenter
                    width: Style.space(10); height: 2; radius: 1
                    color: modelData.color
                  }
                  Text {
                    text: modelData.label
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                Text {
                  text: day ? (modelData.key === "form" ? root.signed(day.form) : String(Math.round(day[modelData.key]))) : "\u2013"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.display
                  font.bold: true
                }
              }
            }

            Column {                                            // a large FTP number for cyclists who have set one
              visible: root.ftp > 0
              Text {
                text: "FTP"
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                text: root.ftp + " W"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                font.bold: true
              }
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: {
              var d = fitnessPlot.shownDay
              if (fitnessPlot.hoverIndex >= 0 && d)
                return Qt.formatDate(new Date(d.date + "T12:00:00"), "ddd d MMM") + " \u00b7 load " + Math.round(d.load)
              return root.fitnessNow ? (root.fitnessStatusText[root.fitnessNow.status] || "") : ""
            }
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            font.bold: fitnessPlot.hoverIndex < 0
          }

          FitnessPlot {
            id: fitnessPlot
            width: parent.width
            height: root.plotMinHeight
            onWidthChanged: Qt.callLater(root.fitPlot)
            days: root.fitnessData ? root.fitnessData.days.slice(-90) : []
            fitnessColor: root.fitnessColor
            fatigueColor: root.fatigueColor
            textColor: String(root.foreground)
            dimColor: String(root.dim)
            gridColor: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)
            surfaceColor: String(Color.background)
            fontFamily: root.fontFamily
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: {
              var f = root.fitnessData
              if (!f) return ""
              var basis = []
              var s = f.sources
              if (s.power) basis.push("power")
              if (s.effort) basis.push("Strava effort")
              if (s.time) basis.push("duration")
              var line = "Estimated from " + basis.join(", ") + ". Not medical advice."
              if (f.effort_scale) line = "Strava effort scaled \u00d7" + f.effort_scale + " to match your power. " + line
              if (f.warming_up) line = "Still warming up: needs about six weeks of history. " + line
              return line
            }
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        PanelSeparator { visible: !!root.fitnessData; foreground: root.foreground }

        // ---------- training load vs last week ----------

        Column {
          id: loadBlock
          visible: !!root.loadInfo && root.loadInfo.state !== "idle"
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            text: "Training load vs last week  ·  " + (root.loadMetricEffective === "effort" ? "effort" : root.loadMetricEffective)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.loadHeadline(root.loadInfo)
            color: root.loadColor
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
          }

          Item {
            visible: !!root.loadInfo && root.loadInfo.total > 0
            width: parent.width
            height: Style.space(6)

            Rectangle {
              anchors.fill: parent
              radius: height / 2
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.15)
            }

            Rectangle {
              width: parent.width * Math.min(1, root.loadInfo ? root.loadInfo.cur / Math.max(1, root.loadInfo.total) : 0)
              height: parent.height
              radius: height / 2
              color: root.loadColor
            }

            // where last week stood at this point of the week
            Rectangle {
              width: 2
              height: parent.height + Style.space(4)
              y: -Style.space(2)
              x: parent.width * Math.min(1, root.loadInfo ? root.loadInfo.same / Math.max(1, root.loadInfo.total) : 0) - width / 2
              color: root.foreground
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.loadDetail(root.loadInfo)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        PanelSeparator { visible: !!root.summary; foreground: root.foreground }


        PanelSeparator { foreground: root.foreground }

        Column {                                              // "skipping today?": the reason is marked on the calendar
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.todayExcuse !== "" ? "Skipping today: " + root.excuseLabel(root.todayExcuse) : "Skipping today?"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Repeater {
            model: [["tired", "weather", "time"], ["unwell", "rest"]]

            Item {
              required property var modelData
              width: parent.width
              height: chipsRow.height

              Row {
                id: chipsRow
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.space(6)

                Repeater {
                  model: parent.parent.modelData

                  Rectangle {
                    id: chip
                    required property string modelData
                    readonly property bool on: root.todayExcuse === modelData
                    width: chipText.implicitWidth + Style.space(16)
                    height: Style.space(24)
                    radius: height / 2
                    color: chip.on ? Qt.rgba(0.90, 0.71, 0.13, 0.28) : (chipMouse.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent")
                    border.width: 1
                    border.color: chip.on ? "#e6b422" : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.3)

                    Text {
                      id: chipText
                      anchors.centerIn: parent
                      text: root.excuseLabel(chip.modelData)
                      color: chip.on ? root.foreground : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }

                    MouseArea {
                      id: chipMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.coachSet(["excuse", chip.modelData])
                    }
                  }
                }
              }
            }
          }
        }

        Column {                                              // "Motivational quotes": Silent, Motivational or Drill sergeant
          width: parent.width
          spacing: Style.spacing.sm

          Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Motivational quotes"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Item {
            width: parent.width
            height: radioRow.height

            Row {
              id: radioRow
              anchors.horizontalCenter: parent.horizontalCenter
              spacing: Style.space(10)

              Repeater {
                model: root.coachModes

                Item {
                  id: radio
                  required property var modelData
                  readonly property bool on: root.coachTone === modelData.id
                  width: radioDot.width + radioLabel.implicitWidth + Style.space(6)
                  height: Style.space(20)

                  Rectangle {
                    id: radioDot
                    width: Style.space(14)
                    height: Style.space(14)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: "transparent"
                    border.width: 1
                    border.color: radio.on ? root.foreground : root.dim

                    Rectangle {
                      anchors.centerIn: parent
                      width: Style.space(8)
                      height: Style.space(8)
                      radius: width / 2
                      color: root.foreground
                      visible: radio.on
                    }
                  }

                  Text {
                    id: radioLabel
                    anchors.left: radioDot.right
                    anchors.leftMargin: Style.space(6)
                    anchors.verticalCenter: parent.verticalCenter
                    text: radio.modelData.label
                    color: radio.on ? root.foreground : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: radio.on
                  }

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.coachSet(["prefs", "--coach-tone", radio.modelData.id])
                  }
                }
              }

            }
          }

          Item {
            visible: root.coachTone !== "off"
            width: parent.width
            height: tryText.height

          Text {
    id: tryText
            visible: root.coachTone !== "off"
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Try one  \u2197"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption

            MouseArea {
              anchors.fill: parent
              anchors.margins: -Style.space(4)
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                if (coachTryProc.running) return
                coachTryProc.command = ["/usr/bin/python3", "-I", root.launcher, "coach", "--test"]
                coachTryProc.running = true
              }
            }
          }
          }
        }
        }

        // ---- third column: the calendar, the totals, and what to do when you skip a day
        Column {
          id: thirdCol
          onImplicitHeightChanged: Qt.callLater(root.fitPlot)
          visible: root.wide
          width: layout.colWidth
          spacing: Style.spacing.panelGap

        // ---------- calendar of active days ----------

        Item {
          visible: !!root.summary
          width: parent.width
          height: calendarToggle.implicitHeight + Style.space(4)

          Text {
            id: calendarToggle
            anchors.verticalCenter: parent.verticalCenter
            text: (root.calendarOpen ? "▾  " : "▸  ") + "Calendar"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
          }

          Text {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.calendarOpen
            text: {
              var n = root.activeCount(root.today.getFullYear(), root.today.getMonth())
              return n + " active this month"
            }
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.calendarOpen = !root.calendarOpen
          }
        }

        Column {
          id: calendar
          visible: !!root.summary && root.calendarOpen
          width: parent.width
          spacing: Style.spacing.md

          readonly property real gap: Style.space(3)
          readonly property real cellWidth: (width - 6 * gap) / 7
          readonly property real cellHeight: Style.space(24)

          Item {
            width: parent.width
            height: monthTitle.implicitHeight

            Row {
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)
              opacity: root.canGoBack ? 1 : 0.25

              Text {
                visible: root.earliestIdx < root.today.getFullYear() * 12       // older years exist: allow jumping by a year
                text: "«"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                MouseArea {
                  anchors.fill: parent
                  anchors.margins: -Style.space(6)
                  enabled: root.canGoBack
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.shiftMonth(-12)
                }
              }

              Text {
                text: "‹"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                MouseArea {
                  anchors.fill: parent
                  anchors.margins: -Style.space(6)
                  enabled: root.canGoBack
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.shiftMonth(-1)
                }
              }
            }

            Text {
              id: monthTitle
              anchors.centerIn: parent
              text: root.monthNames[root.viewMonth] + " " + root.viewYear
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }

            Row {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)
              opacity: root.canGoForward ? 1 : 0.25

              Text {
                text: "›"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                MouseArea {
                  anchors.fill: parent
                  anchors.margins: -Style.space(6)
                  enabled: root.canGoForward
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.shiftMonth(1)
                }
              }

              Text {
                visible: root.earliestIdx < root.today.getFullYear() * 12
                text: "»"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                MouseArea {
                  anchors.fill: parent
                  anchors.margins: -Style.space(6)
                  enabled: root.canGoForward
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.shiftMonth(12)
                }
              }
            }
          }

          Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: {
              var n = root.activeCount(root.viewYear, root.viewMonth)
              return n + (n === 1 ? " active day" : " active days")
            }
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Row {
            spacing: calendar.gap

            Repeater {
              model: ["M", "T", "W", "T", "F", "S", "S"]

              Text {
                required property string modelData
                width: calendar.cellWidth
                horizontalAlignment: Text.AlignHCenter
                text: modelData
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }

          Grid {
            columns: 7
            columnSpacing: calendar.gap
            rowSpacing: calendar.gap

            Repeater {
              model: root.calendarCells(root.viewYear, root.viewMonth)

              Item {
                id: cell
                required property var modelData
                width: calendar.cellWidth
                height: calendar.cellHeight

                readonly property real alpha: root.dayAlpha(modelData.info)
                readonly property bool picked: modelData.day > 0 && modelData.key === root.selectedDay

                Rectangle {
                  anchors.fill: parent
                  visible: cell.modelData.day > 0
                  radius: Style.space(4)
                  color: cell.alpha > 0 ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, cell.alpha) : "transparent"
                  border.width: cell.picked ? 2 : (cell.modelData.isToday ? 1 : 0)
                  border.color: cell.picked ? root.stravaOrange : root.foreground
                }

                Text {
                  anchors.centerIn: parent
                  visible: cell.modelData.day > 0
                  text: cell.modelData.day
                  color: cell.alpha >= 0.6 ? Color.background : root.foreground
                  opacity: cell.modelData.isFuture ? 0.35 : (cell.alpha > 0 || cell.modelData.isToday ? 1 : 0.7)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: cell.alpha > 0
                }

                Rectangle {                       // a ring: you marked this day as skipped, with a reason
                  visible: cell.modelData.day > 0 && !!cell.modelData.excuse
                  anchors.top: parent.top
                  anchors.right: parent.right
                  anchors.margins: Style.space(3)
                  width: Style.space(7)
                  height: Style.space(7)
                  radius: width / 2
                  color: "transparent"
                  border.width: 2
                  border.color: cell.alpha >= 0.6 ? Color.background : "#e6b422"
                }

                Rectangle {                       // a dot: the full time series (with GPS) of a ride this day is stored here
                  visible: cell.modelData.day > 0 && cell.modelData.local === true
                  anchors.horizontalCenter: parent.horizontalCenter
                  anchors.bottom: parent.bottom
                  anchors.bottomMargin: Style.space(2)
                  width: Style.space(4)
                  height: Style.space(4)
                  radius: width / 2
                  color: cell.alpha >= 0.6 ? Color.background : Color.accent
                }

                MouseArea {
                  id: cellMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  enabled: cell.modelData.day > 0 && !cell.modelData.isFuture
                  cursorShape: cell.modelData.info ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: if (cell.modelData.info) root.pickDay(cell.modelData.key)
                }

                PanelToolTip {
                  visible: cellMouse.containsMouse && cell.modelData.day > 0
                  text: root.dayTooltip(cell.modelData)
                  fontFamily: root.fontFamily
                }
              }
            }
          }

          // Several activities on the picked day: choose which one the detail view shows.
          Column {
            id: dayList
            readonly property var items: root.selectedDay !== "" ? root.activitiesOn(root.selectedDay) : []
            visible: dayList.items.length > 1
            width: parent.width
            spacing: Style.spacing.sm

            Repeater {
              model: dayList.items

              Item {
                id: dayRow
                required property var modelData
                readonly property bool chosen: !!root.selected && root.selected.id === modelData.id
                width: dayList.width
                height: rowName.implicitHeight + Style.space(8)

                Rectangle {
                  anchors.fill: parent
                  radius: Style.space(4)
                  color: dayRow.chosen ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent"
                }

                Text {
                  id: rowName
                  anchors.left: parent.left
                  anchors.leftMargin: Style.space(6)
                  anchors.right: rowMeta.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  elide: Text.ElideRight
                  text: dayRow.modelData.name
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: dayRow.chosen
                }

                Text {
                  id: rowMeta
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(6)
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.sportName(dayRow.modelData.sport) + (dayRow.modelData.distance_km > 0
                        ? " · " + root.fmtDistance(dayRow.modelData.distance_km, dayRow.modelData.family)
                        : " · " + root.fmtDuration(dayRow.modelData.moving_time_s))
                        + (dayRow.modelData.elevation_m > 0 ? " · " + root.fmtClimb(dayRow.modelData.elevation_m) : "")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.selected = dayRow.modelData
                }
              }
            }
          }
        }

        PanelSeparator { foreground: root.foreground }


        Column {                                              // Today, this week, this month, this year
          id: totalsRow
          width: parent.width
          spacing: Style.spacing.panelGap

          Repeater {
            model: root.totalsBlocks

            Column {
              id: totalsBlock
              required property var modelData
              width: thirdCol.width
              spacing: Style.spacing.sm

              Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: totalsBlock.modelData.title
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }

              Text {
                visible: root.totalsRows(totalsBlock.modelData.t).length === 0
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: "Nothing yet"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }

              Repeater {
                model: root.totalsRows(totalsBlock.modelData.t)

                Item {
                  required property var modelData
                  width: totalsBlock.width
                  height: rowLabel.implicitHeight

                  Text {
                    id: rowLabel
                    text: parent.modelData.label
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }

                  Text {
                    anchors.right: parent.right
                    text: parent.modelData.value
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }
                }
              }
            }
          }
        }
        }
      }

      Image {                                                 // the required credit, in the popup's bottom-right corner
        visible: root.wide
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Style.space(16)
        source: root.stravaLogo
        sourceSize.height: 48
        fillMode: Image.PreserveAspectFit
        horizontalAlignment: Image.AlignRight
      }
    }
  }
}
