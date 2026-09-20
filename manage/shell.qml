import QtQuick
import Quickshell
import Quickshell.Io

// LapBar data window: how much of your history is stored, what fetching did each day, and the DuckDB export
// with a description of its schema right beside it. Everything is read with `lapbar manage --status` and changed
// with `lapbar prefs` / `lapbar export`, so this file only draws and never touches your data itself.
//
// Run by `lapbar manage` as its own Quickshell process (LAPBAR_BIN is the launcher).
FloatingWindow {
  id: win

  title: "LapBar · Data"
  implicitWidth: 1240
  implicitHeight: 820
  minimumSize: Qt.size(900, 560)
  color: surface
  visible: true
  onClosed: Qt.quit()

  // ------------------------------------------------------------------ theme (from the bar's theme)

  Theme { id: th }

  readonly property string fg: th.fg
  readonly property string surface: th.surface
  readonly property string accent: th.accent
  readonly property string fontName: th.fontName
  readonly property string assetsDir: Quickshell.env("LAPBAR_ASSETS") || ""
  readonly property string bin: Quickshell.env("LAPBAR_BIN") || ""
  readonly property string python: Quickshell.env("LAPBAR_PYTHON") || "/usr/bin/python3"
  readonly property string warn: th.warn
  readonly property string good: th.good

  function mix(a, b, t) { return th.mix(a, b, t) }

  readonly property color dim: th.dim
  readonly property color line: th.line
  readonly property color card: th.card
  readonly property bool dark: Qt.color(surface).hslLightness < 0.5

  function fmtInt(n) { return Number(n).toLocaleString(Qt.locale("en_US"), "f", 0) }
  function fmtBytes(b) {
    if (b === null || b === undefined) return "–"
    if (b < 1024 * 1024) return Math.round(b / 1024) + " KB"
    if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + " MB"
    return (b / 1024 / 1024 / 1024).toFixed(2) + " GB"
  }

  // ------------------------------------------------------------------ data

  property var status: null
  property string message: ""
  property bool messageIsError: false
  property bool pickerOpen: false

  function refresh() {
    if (statusProc.running) return
    statusProc.command = [win.python, "-I", win.bin, "manage", "--status"]
    statusProc.running = true
  }

  // Runs one `lapbar ...` command at a time and shows what it said.
  function act(args, label) {
    if (actionProc.running) return
    win.message = label
    win.messageIsError = false
    actionProc.command = [win.python, "-I", win.bin].concat(args)
    actionProc.running = true
  }

  // The desktop's own folder dialog, through `lapbar pick-folder`; the file keeps its name inside the chosen folder.
  function pickFolder() {
    if (pickProc.running || !win.status) return
    win.message = "Choose a folder in the dialog\u2026"
    win.messageIsError = false
    pickProc.command = [win.python, "-I", win.bin, "pick-folder", "--start", win.status.export.dir,
                        "--title", "Folder for the DuckDB file"]
    pickProc.running = true
  }

  function copyText(text) {
    copyProc.command = ["wl-copy", text]
    copyProc.running = true
    win.message = "Copied: " + text
    win.messageIsError = false
  }

  Process {
    id: statusProc
    running: false
    command: []
    stdout: StdioCollector { id: statusOut; waitForEnd: true }
    onExited: { try { win.status = JSON.parse(statusOut.text) } catch (e) { } }
  }

  Process {
    id: actionProc
    running: false
    command: []
    stdout: StdioCollector { id: actionOut; waitForEnd: true }
    onExited: {
      try {
        var r = JSON.parse(actionOut.text)
        if (r.error) { win.message = r.message || r.error; win.messageIsError = true }
        else if (r.path && r.sample_rows !== undefined) {
          win.message = "Exported " + win.fmtInt(r.activities) + " activities and " + win.fmtInt(r.sample_rows) + " samples to " + r.path
          win.messageIsError = false
        } else win.message = "Saved."
      } catch (e) { win.message = "Something went wrong."; win.messageIsError = true }
      win.refresh()
    }
  }

  Process {
    id: pickProc
    running: false
    command: []
    stdout: StdioCollector { id: pickOut; waitForEnd: true }
    onExited: {
      try {
        var r = JSON.parse(pickOut.text)
        if (r.error) { win.message = r.message || r.error; win.messageIsError = true }
        else if (r.cancelled) win.message = "No folder chosen."
        else win.act(["prefs", "--export-path", r.path + "/" + win.status.export.filename], "Saving the folder\u2026")
      } catch (e) { win.message = "The folder dialog did not answer."; win.messageIsError = true }
    }
  }

  Process { id: copyProc; running: false; command: [] }

  Timer {
    interval: 2000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: win.refresh()
  }

  // ------------------------------------------------------------------ layout

  Item {
    id: content
    anchors.fill: parent
    anchors.margins: 20
    focus: true
    Keys.onEscapePressed: Qt.quit()

    // ---- header
    Item {
      id: header
      width: parent.width
      height: 36

      Image {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        visible: win.assetsDir !== ""
        source: win.assetsDir === "" ? "" : "file://" + win.assetsDir + "/logo/lapbar_horiz_" + (win.dark ? "white" : "black") + ".svg"
        sourceSize.height: 96
        height: 30
        fillMode: Image.PreserveAspectFit
      }

      Text {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Data  ·  Esc to close"
        color: win.dim
        font.family: win.fontName
        font.pixelSize: 13
      }
    }

    // ---- left column: history and fetching by day
    Flickable {
      id: leftScroll
      anchors.left: parent.left
      anchors.top: header.bottom
      anchors.topMargin: 14
      anchors.bottom: statusBar.top
      anchors.bottomMargin: 10
      width: 430
      contentHeight: leftColumn.implicitHeight
      clip: true

      Column {
        id: leftColumn
        width: leftScroll.width
        spacing: 14

        // ---------- History
        Rectangle {
          width: parent.width
          height: historyBox.implicitHeight + 28
          radius: 8
          color: win.card
          border.width: 1
          border.color: win.line

          Column {
            id: historyBox
            x: 14
            y: 14
            width: parent.width - 28
            spacing: 10

            Heading { text: "History" }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: win.status
                ? win.fmtInt(win.status.history.activities) + " activities" + (win.status.history.oldest_day ? ", " + win.status.history.oldest_day + " to " + win.status.history.newest_day : "")
                : "Loading…"
              color: win.fg
              font.family: win.fontName
              font.pixelSize: 14
            }

            Column {
              width: parent.width
              spacing: 5
              visible: !!win.status

              Meter {
                width: parent.width
                value: win.status && win.status.history.activities > 0 ? win.status.history.stored / win.status.history.activities : 0
              }

              Caption {
                width: parent.width
                text: {
                  if (!win.status) return ""
                  var h = win.status.history
                  var pct = h.activities > 0 ? Math.round(100 * h.stored / h.activities) : 0
                  var line = "Full data stored for " + win.fmtInt(h.stored) + " of " + win.fmtInt(h.activities) + " (" + pct + "%) · " + win.fmtBytes(h.raw_bytes)
                  if (h.estimated_total_bytes) line += ", about " + win.fmtBytes(h.estimated_total_bytes) + " when complete"
                  if (h.no_data > 0) line += " · " + h.no_data + " have no data on Strava"
                  return line
                }
              }

              Caption {
                width: parent.width
                text: {
                  if (!win.status) return ""
                  var h = win.status.history
                  return h.years_stored.length + " earlier years listed" + (h.years_complete ? " (all of them)" : ", more to come")
                }
              }
            }

            Rectangle { width: parent.width; height: 1; color: win.line }

            Text {
              text: "Fetch history back to"
              color: win.fg
              font.family: win.fontName
              font.pixelSize: 13
              font.bold: true
            }

            Row {
              spacing: 8

              Btn {
                label: win.status && win.status.history.limit ? "From " + win.status.history.limit + "  \u25be" : "Choose a date\u2026  \u25be"
                primary: !!win.status && !!win.status.history.limit
                onClicked: {
                  if (!win.pickerOpen && win.status) datePicker.show(win.status.history.limit || win.status.history.first_day)
                  win.pickerOpen = !win.pickerOpen
                }
              }

              Btn {
                label: "No limit"
                enabled: !!win.status && !!win.status.history.limit
                onClicked: { win.pickerOpen = false; win.act(["prefs", "--history-from", "none"], "Removing the limit\u2026") }
              }
            }

            DatePicker {
              id: datePicker
              visible: win.pickerOpen
              width: parent.width
              selected: win.status && win.status.history.limit ? win.status.history.limit : ""
              minDate: win.status && win.status.history.first_day ? win.status.history.first_day : ""
              onPicked: function(date) { win.pickerOpen = false; win.act(["prefs", "--history-from", date], "Saving the limit\u2026") }
            }

            Caption {
              width: parent.width
              text: win.status && win.status.history.limit
                ? "Only activities from " + win.status.history.limit + " on are downloaded and shown in the calendar. What is already stored stays on disk."
                : "No limit: LapBar fetches everything Strava has. Set a day to stop it going further back in the past."
            }

            Caption {
              width: parent.width
              text: win.status ? "Stored in " + win.status.history.data_dir : ""
            }
          }
        }

        // ---------- Fetching by day
        Rectangle {
          width: parent.width
          height: dayBox.implicitHeight + 28
          radius: 8
          color: win.card
          border.width: 1
          border.color: win.line

          Column {
            id: dayBox
            x: 14
            y: 14
            width: parent.width - 28
            spacing: 8

            Heading { text: "Fetching, day by day" }

            Caption {
              width: parent.width
              text: win.status
                ? "Today: " + win.status.budget.daily_used + " of " + win.status.budget.daily_limit + " Strava requests used. Days are UTC, as Strava counts them. Background downloads slow down as the day fills up and stop at 40%."
                : ""
            }

            Repeater {
              model: win.status ? win.status.fetch_days.slice().reverse() : []

              Item {
                id: dayRow
                required property var modelData
                required property int index
                width: dayBox.width
                height: 20

                readonly property real maxActivities: {
                  var m = 1
                  var d = win.status.fetch_days
                  for (var i = 0; i < d.length; i++) m = Math.max(m, d[i].activities)
                  return m
                }

                Text {
                  id: dayLabel
                  width: 96
                  anchors.verticalCenter: parent.verticalCenter
                  text: dayRow.modelData.date.slice(5) + (dayRow.index === 0 ? "  today" : "")
                  color: dayRow.index === 0 ? win.fg : win.dim
                  font.family: win.fontName
                  font.pixelSize: 12
                }

                Rectangle {
                  x: dayLabel.width
                  anchors.verticalCenter: parent.verticalCenter
                  width: Math.max(dayRow.modelData.activities > 0 ? 3 : 0, (parent.width - dayLabel.width - 176) * dayRow.modelData.activities / dayRow.maxActivities)
                  height: 10
                  radius: 3
                  color: win.accent
                }

                Text {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: dayRow.modelData.activities + " stored · " + dayRow.modelData.requests + " req"
                  color: dayRow.modelData.activities > 0 || dayRow.modelData.requests > 0 ? win.fg : win.dim
                  font.family: win.fontName
                  font.pixelSize: 12
                }
              }
            }

            Caption {
              width: parent.width
              text: "\"stored\" is complete activities downloaded that day; \"req\" is the most requests Strava reported that day (everything the widget asked, not only these)."
            }
          }
        }
      }
    }

    // ---- right: the DuckDB export, with the schema beside it
    Rectangle {
      id: exportCard
      anchors.left: leftScroll.right
      anchors.leftMargin: 16
      anchors.right: parent.right
      anchors.top: header.bottom
      anchors.topMargin: 14
      anchors.bottom: statusBar.top
      anchors.bottomMargin: 10
      radius: 8
      color: win.card
      border.width: 1
      border.color: win.line
      clip: true

      // controls
      Flickable {
        id: controlsScroll
        x: 14
        y: 14
        width: 310
        height: parent.height - 28
        contentHeight: controls.implicitHeight
        clip: true

        Column {
          id: controls
          width: controlsScroll.width - 4
          spacing: 10

          Heading { text: "Export to DuckDB" }

          Caption {
            width: parent.width
            text: "A database file with your activities, routes and every second of data, for your own SQL. LapBar never reads it back."
          }

          // DuckDB missing: say so, and how to install it
          Rectangle {
            width: parent.width
            visible: !!win.status && !win.status.export.available
            height: missingBox.implicitHeight + 20
            radius: 6
            color: win.mix(win.warn, win.surface, 0.12)
            border.width: 1
            border.color: win.warn

            Column {
              id: missingBox
              x: 10
              y: 10
              width: parent.width - 20
              spacing: 6

              Text {
                text: "DuckDB is not installed"
                color: win.warn
                font.family: win.fontName
                font.pixelSize: 13
                font.bold: true
              }

              Caption { width: parent.width; text: "Install its Python package, then reopen this window:" }

              Repeater {
                model: win.status ? win.status.export.install : []

                Row {
                  id: installRow
                  required property string modelData
                  spacing: 6
                  Text {
                    width: missingBox.width - 60
                    anchors.verticalCenter: parent.verticalCenter
                    wrapMode: Text.WrapAnywhere
                    text: installRow.modelData
                    color: win.fg
                    font.family: win.fontName
                    font.pixelSize: 12
                  }
                  Btn { anchors.verticalCenter: parent.verticalCenter; label: "Copy"; onClicked: win.copyText(installRow.modelData) }
                }
              }

              Caption { width: parent.width; text: "Also install `duckdb` if you want its command line to query the file." }
            }
          }

          Caption {
            width: parent.width
            visible: !!win.status && win.status.export.available
            text: "DuckDB " + (win.status ? win.status.export.version : "") + " found."
            color: win.good
          }

          Text {
            text: "Save the database in"
            color: win.fg
            font.family: win.fontName
            font.pixelSize: 13
            font.bold: true
          }

          Rectangle {
            width: parent.width
            height: 30
            radius: 5
            color: win.mix(win.fg, win.surface, 0.06)
            border.width: 1
            border.color: win.line

            Text {
              anchors.fill: parent
              anchors.leftMargin: 8
              anchors.rightMargin: 8
              verticalAlignment: Text.AlignVCenter
              elide: Text.ElideMiddle
              text: win.status ? win.status.export.dir : ""
              color: win.fg
              font.family: win.fontName
              font.pixelSize: 12
            }
          }

          Row {
            spacing: 8
            Btn { label: "Choose folder\u2026"; primary: true; enabled: !pickProc.running; onClicked: win.pickFolder() }
            Btn { label: "Default"; onClicked: win.act(["prefs", "--export-path", ""], "Using the default folder\u2026") }
          }

          Caption {
            width: parent.width
            text: win.status ? "The file is called " + win.status.export.filename + "." : ""
          }

          Row {
            spacing: 8
            Btn {
              label: "Export now"
              primary: true
              enabled: !!win.status && win.status.export.available && !win.status.export.state.running && !actionProc.running
              onClicked: win.act(["export"], "Exporting\u2026")
            }
            Btn {
              label: "Rebuild"
              enabled: !!win.status && win.status.export.available && !win.status.export.state.running && !actionProc.running
              onClicked: win.act(["export", "--rebuild"], "Rebuilding\u2026")
            }
          }

          Caption {
            width: parent.width
            text: "Export now updates the file: only activities that are not in it yet are added. Rebuild writes a fresh file."
          }

          Check {
            width: parent.width
            enabled: !!win.status && win.status.export.available
            checked: !!win.status && win.status.export.continuous
            label: "Keep it up to date: append new activities after every refresh"
            onToggled: win.act(["prefs", "--continuous", win.status.export.continuous ? "off" : "on"], "Saving\u2026")
          }

          Check {
            width: parent.width
            enabled: !!win.status && win.status.export.available
            checked: !!win.status && win.status.export.spatial_wanted
            label: "Add real geometry (routes.geom): downloads DuckDB's spatial extension once (about 80 MB) from DuckDB's servers"
            onToggled: win.act(["prefs", "--spatial", win.status.export.spatial_wanted ? "off" : "on"], "Saving\u2026")
          }

          Caption {
            width: parent.width
            visible: !!win.status && win.status.export.available
            text: !win.status ? "" : (win.status.export.spatial
              ? "Spatial extension: installed. routes.geom is filled on the next export."
              : (win.status.export.spatial_wanted ? "Spatial extension: not installed yet; it is downloaded during the next export."
                                                  : "Spatial extension: not installed. Without it the routes are still stored as text (wkt) and as map cells."))
          }

          Caption {
            width: parent.width
            visible: !!win.status && !!win.status.export.state.spatial_problem
            text: win.status && win.status.export.state.spatial_problem ? win.status.export.state.spatial_problem : ""
            color: win.warn
          }

          Caption {
            width: parent.width
            visible: !!win.status && !win.status.export.available
            text: "Exporting needs DuckDB: see above."
          }

          // progress and last result
          Column {
            width: parent.width
            spacing: 5
            visible: !!win.status

            Meter {
              width: parent.width
              visible: !!win.status && win.status.export.state.running
              value: win.status && win.status.export.state.total > 0 ? win.status.export.state.done / win.status.export.state.total : 0
            }

            Caption {
              width: parent.width
              visible: !!win.status && win.status.export.state.running
              text: win.status ? "Exporting " + win.status.export.state.done + " of " + win.status.export.state.total + " activities…" : ""
            }

            Caption {
              width: parent.width
              visible: !!win.status && !win.status.export.state.running && !!win.status.export.state.finished
              text: {
                if (!win.status) return ""
                var s = win.status.export.state
                if (!s.finished) return ""
                return "Last export: " + s.finished.replace("T", " ").replace("Z", " UTC") + " · " + win.fmtInt(s.activities) + " activities, " + win.fmtInt(s.sample_rows) + " samples · " + win.fmtBytes(win.status.export.file_bytes)
              }
            }

            Caption {
              width: parent.width
              visible: !!win.status && !!win.status.export.state.error
              text: win.status && win.status.export.state.error ? win.status.export.state.error : ""
              color: win.warn
            }
          }
        }
      }

      Rectangle { x: 14 + 310 + 10; y: 14; width: 1; height: parent.height - 28; color: win.line }

      // schema, right beside the export settings
      Flickable {
        id: schemaScroll
        x: 14 + 310 + 22
        y: 14
        width: parent.width - x - 14
        height: parent.height - 28
        contentHeight: schemaColumn.implicitHeight
        clip: true

        Column {
          id: schemaColumn
          width: schemaScroll.width - 12
          spacing: 12

          Heading { text: "What is in the database" }

          Column {
            width: parent.width
            spacing: 6

            Repeater {
              model: win.status ? win.status.relationships : []

              Column {
                id: relation
                required property var modelData
                width: schemaColumn.width
                spacing: 1

                Text {
                  width: parent.width
                  wrapMode: Text.WrapAnywhere
                  text: relation.modelData.link
                  color: win.fg
                  font.family: win.fontName
                  font.pixelSize: 11
                }

                Caption { width: parent.width; text: relation.modelData.about; font.pixelSize: 11 }
              }
            }
          }

          Repeater {
            model: win.status ? win.status.schema : []

            Column {
              id: tableBox
              required property var modelData
              width: schemaColumn.width
              spacing: 3

              Text {
                text: tableBox.modelData.table
                color: win.accent
                font.family: win.fontName
                font.pixelSize: 14
                font.bold: true
              }

              Caption { width: parent.width; text: tableBox.modelData.about }

              Repeater {
                model: tableBox.modelData.columns

                Item {
                  id: colRow
                  required property var modelData
                  width: tableBox.width
                  height: Math.max(colName.implicitHeight, colAbout.implicitHeight) + 2

                  Text {
                    id: colName
                    width: 118
                    text: colRow.modelData.name
                    color: win.fg
                    font.family: win.fontName
                    font.pixelSize: 11
                    elide: Text.ElideRight
                  }

                  Text {
                    id: colType
                    x: 122
                    width: 74
                    text: colRow.modelData.type
                    color: win.dim
                    font.family: win.fontName
                    font.pixelSize: 11
                  }

                  Text {
                    id: colAbout
                    x: 200
                    width: parent.width - 200
                    wrapMode: Text.WordWrap
                    text: colRow.modelData.about
                    color: win.dim
                    font.family: win.fontName
                    font.pixelSize: 11
                  }
                }
              }
            }
          }

          Heading { text: "Example queries" }

          Repeater {
            model: win.status ? win.status.examples : []

            Column {
              id: exampleBox
              required property var modelData
              width: schemaColumn.width
              spacing: 3

              Text {
                text: exampleBox.modelData.title
                color: win.fg
                font.family: win.fontName
                font.pixelSize: 12
                font.bold: true
              }

              Rectangle {
                width: parent.width
                height: sqlText.implicitHeight + 12
                radius: 5
                color: win.mix(win.fg, win.surface, 0.07)

                Text {
                  id: sqlText
                  x: 8
                  y: 6
                  width: parent.width - 16
                  wrapMode: Text.WrapAnywhere
                  text: exampleBox.modelData.sql
                  color: win.fg
                  font.family: win.fontName
                  font.pixelSize: 11
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: win.copyText(exampleBox.modelData.sql)
                }
              }
            }
          }

          Heading { text: "Notes" }

          Repeater {
            model: win.status ? win.status.notes : []

            Column {
              id: noteBox
              required property var modelData
              width: schemaColumn.width
              spacing: 3

              Text {
                text: noteBox.modelData.title
                color: win.fg
                font.family: win.fontName
                font.pixelSize: 12
                font.bold: true
              }

              Caption { width: parent.width; text: noteBox.modelData.about; font.pixelSize: 11 }
            }
          }

          Caption { width: parent.width; text: "Click an example to copy it. Open the file with the duckdb command, Python (import duckdb), R, or DBeaver." }
        }
      }
    }

    // ---- bottom line: what the last action said
    Text {
      id: statusBar
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: 18
      elide: Text.ElideRight
      text: win.message
      color: win.messageIsError ? win.warn : win.dim
      font.family: win.fontName
      font.pixelSize: 12
    }
  }
}
