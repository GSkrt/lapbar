import QtQuick
import Quickshell
import Quickshell.Io
import "logic.js" as Logic

// lapbar chart window: one plot per measure, all sharing one x axis (kilometres). A single vertical
// line runs across every plot at the same position and each plot shows its value there; the legend on
// the right lists every measure's value at that position. Scroll to zoom, drag to pan, arrow keys to step.
//
// Run by `lapbar charts <activity>` as its own Quickshell process (LAPBAR_CHART_FILE points at the data).
FloatingWindow {
  id: win

  title: doc ? "LapBar · " + doc.name : "LapBar charts"
  implicitWidth: 1240
  implicitHeight: 820
  minimumSize: Qt.size(820, 520)
  color: surface
  visible: true
  onClosed: Qt.quit()

  // ------------------------------------------------------------------ theme (from the bar's theme)

  readonly property string fg: Quickshell.env("LAPBAR_FG") || "#cacccc"
  readonly property string surface: Quickshell.env("LAPBAR_BG") || "#1f1f28"
  readonly property string fontName: Quickshell.env("LAPBAR_FONT") || "monospace"
  readonly property string assetsDir: Quickshell.env("LAPBAR_ASSETS") || ""
  // Canvas font strings need family names with spaces in quotes ("JetBrainsMono Nerd Font").
  readonly property string cssFont: "\"" + fontName + "\""
  readonly property bool dark: Logic.isDark(surface)
  readonly property string dim: Logic.mix(fg, surface, 0.62)
  readonly property string grid: Logic.mix(fg, surface, 0.16)
  readonly property string wash: Logic.mix(fg, surface, 0.10)

  // ------------------------------------------------------------------ data

  FileView {
    id: file
    path: Quickshell.env("LAPBAR_CHART_FILE")
    blockLoading: true
  }

  property var doc: null
  property string loadError: ""

  Component.onCompleted: {
    try {
      doc = JSON.parse(file.text())
      if (!doc || !doc.x || !doc.series || doc.series.length === 0) throw new Error("no series")
      viewStart = xs[0]
      viewEnd = xs[xs.length - 1]
      if (doc.kind === "fitness") cursor = xs.length - (doc.tomorrow ? 2 : 1)   // "how am I now": start on today
    } catch (e) {
      doc = null
      loadError = "Could not read the chart data."
    }
    content.forceActiveFocus()
  }

  readonly property var xs: doc ? doc.x.values : []
  readonly property real xLo: xs.length ? xs[0] : 0
  readonly property real xHi: xs.length ? xs[xs.length - 1] : 1
  readonly property real minSpan: (doc && doc.x.unit === "date") ? 7 : Math.max((xHi - xLo) / 300, 0.001)

  property var hidden: ({})           // series key -> true when switched off in the legend
  readonly property var shownSeries: doc ? doc.series.filter(function(s) { return !hidden[s.key] }) : []

  property real viewStart: 0
  property real viewEnd: 1
  property int cursor: 0
  property string mode: "chart"       // "chart" | "table"

  // Series that name the same `panel` share one panel and one y axis (fitness and fatigue); the rest get one each.
  readonly property var panels: {
    var out = [], byName = {}
    for (var i = 0; i < shownSeries.length; i++) {
      var s = shownSeries[i]
      if (s.panel && byName[s.panel] !== undefined) out[byName[s.panel]].overlay.push(s)
      else {
        if (s.panel) byName[s.panel] = out.length
        out.push({ primary: s, overlay: [] })
      }
    }
    return out
  }

  function colorFor(series) {
    if (series.key === "form") return "#4caf50"                        // the bars' fresh colour
    if (series.key === "load") return Logic.mix(fg, surface, 0.6)      // daily load: neutral bars, not a measure of its own hue
    return Logic.seriesColor(series.key, win.dark)
  }

  function toggleSeries(key) {
    var next = Object.assign({}, hidden)
    if (next[key]) delete next[key]; else next[key] = true
    // never hide the last visible series
    if (doc && doc.series.filter(function(s) { return !next[s.key] }).length === 0) return
    hidden = next
  }

  function resetZoom() { viewStart = xLo; viewEnd = xHi }

  function setCursorAt(x) { cursor = Math.max(0, Math.min(xs.length - 1, Logic.indexAt(xs, x))) }

  function stepCursor(n) {
    cursor = Math.max(0, Math.min(xs.length - 1, cursor + n))
    // keep the cursor inside the view
    var cx = xs[cursor]
    if (cx < viewStart || cx > viewEnd) {
      var r = Logic.pan(viewStart, viewEnd, xLo, xHi, cx < viewStart ? cx - viewStart : cx - viewEnd)
      viewStart = r[0]; viewEnd = r[1]
    }
  }

  function zoomAt(focus, factor) {
    var r = Logic.zoom(viewStart, viewEnd, xLo, xHi, focus, factor, minSpan)
    viewStart = r[0]; viewEnd = r[1]
  }

  function xText(x) {
    if (!doc) return ""
    if (doc.x.unit === "date") return Logic.fmtDate(x, true) + (doc.tomorrow && x === xHi ? " (tomorrow)" : "")
    return Logic.fmtX(doc.x, x) + " " + doc.x.unit
  }

  function legendStats(series) {
    var r = Logic.visibleRange(xs, viewStart, viewEnd)
    return Logic.stats(series.values, r[0], r[1])
  }

  // ------------------------------------------------------------------ layout

  FocusScope {
    id: content
    anchors.fill: parent
    focus: true

    Keys.onPressed: function(e) {
      var big = (e.modifiers & Qt.ShiftModifier) ? 10 : 1
      if (e.key === Qt.Key_Escape) Qt.quit()
      else if (e.key === Qt.Key_Left) win.stepCursor(-big)
      else if (e.key === Qt.Key_Right) win.stepCursor(big)
      else if (e.key === Qt.Key_Home) win.stepCursor(-win.xs.length)
      else if (e.key === Qt.Key_End) win.stepCursor(win.xs.length)
      else if (e.key === Qt.Key_Plus || e.key === Qt.Key_Equal) win.zoomAt(win.xs[win.cursor], 0.7)
      else if (e.key === Qt.Key_Minus) win.zoomAt(win.xs[win.cursor], 1 / 0.7)
      else if (e.key === Qt.Key_0 || e.key === Qt.Key_R) win.resetZoom()
      else if (e.key === Qt.Key_T) win.mode = win.mode === "chart" ? "table" : "chart"
      else return
      e.accepted = true
    }

    // ---------- header
    Item {
      id: header
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: 16
      height: 52

      Column {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3

        Text {
          text: win.doc ? win.doc.name : "LapBar"
          color: win.fg
          font.family: win.fontName
          font.pixelSize: 18
          font.bold: true
        }

        Text {
          color: win.dim
          font.family: win.fontName
          font.pixelSize: 12
          text: {
            if (!win.doc) return ""
            if (win.doc.kind === "fitness") {
              var src = win.doc.sources || {}, parts = []
              if (src.power) parts.push("power")
              if (src.effort) parts.push("Strava effort")
              if (src.time) parts.push("duration")
              return "Estimated from " + parts.join(", ") + (win.doc.warming_up ? "  ·  still warming up (needs about six weeks of history)" : "") + "  ·  not medical advice"
            }
            var d = win.doc.start ? new Date(win.doc.start.replace("Z", "")) : null
            var when = d ? Qt.formatDateTime(d, "ddd d MMM yyyy · HH:mm") : ""
            var sport = win.doc.sport ? win.doc.sport.replace(/([a-z])([A-Z])/g, "$1 $2") : ""
            return [sport, when, win.doc.distance_km > 0 ? win.doc.distance_km + " km" : ""].filter(Boolean).join("  ·  ")
          }
        }
      }

      Row {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Text {
          anchors.verticalCenter: parent.verticalCenter
          visible: !!win.doc && win.doc.kind !== "fitness"
          text: "View on Strava"
          color: "#FC5200"
          font.family: win.fontName
          font.pixelSize: 12
          font.bold: true
          rightPadding: 8

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: Quickshell.execDetached(["/usr/bin/xdg-open", "https://www.strava.com/activities/" + win.doc.id])
          }
        }

        Repeater {
          model: [
            { label: "Chart", active: win.mode === "chart", act: "chart" },
            { label: "Table", active: win.mode === "table", act: "table" },
            { label: "Reset zoom", active: false, act: "reset" }
          ]

          Rectangle {
            required property var modelData
            width: btnLabel.implicitWidth + 24
            height: 28
            radius: 6
            color: modelData.active ? win.wash : "transparent"
            border.width: 1
            border.color: modelData.active ? win.dim : win.grid

            Text {
              id: btnLabel
              anchors.centerIn: parent
              text: parent.modelData.label
              color: win.fg
              font.family: win.fontName
              font.pixelSize: 12
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                if (parent.modelData.act === "reset") win.resetZoom()
                else win.mode = parent.modelData.act
              }
            }
          }
        }
      }
    }

    // ---------- footer hint
    Text {
      id: footer
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.margins: 12
      horizontalAlignment: Text.AlignHCenter
      color: win.dim
      font.family: win.fontName
      font.pixelSize: 11
      text: "Move the mouse (or ← →, Shift for bigger steps) to read values · scroll or + − to zoom · drag to pan · double-click or R to reset · T for the table · Esc to close"
    }

    // ---------- error state
    Text {
      visible: !win.doc
      anchors.centerIn: parent
      text: win.loadError
      color: win.fg
      font.family: win.fontName
      font.pixelSize: 14
    }

    // ---------- body
    Item {
      id: body
      visible: !!win.doc
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: header.bottom
      anchors.bottom: footer.top
      anchors.margins: 16
      anchors.topMargin: 8

      readonly property real legendW: 290

      // ===== plots
      Item {
        id: plots
        visible: win.mode === "chart"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width - body.legendW - 16

        readonly property real axisH: 30
        readonly property real panelH: Math.max(84, (height - axisH) / Math.max(1, win.panels.length))

        Column {
          id: stack
          width: parent.width
          clip: true

          Repeater {
            model: win.panels

            SeriesPanel {
              required property var modelData
              width: stack.width
              height: plots.panelH
              series: modelData.primary
              overlay: modelData.overlay
              overlayColors: modelData.overlay.map(function(o) { return win.colorFor(o) })
              xs: win.xs
              xMeta: win.doc ? win.doc.x : ({})
              lineColor: win.colorFor(modelData.primary)
              textColor: win.fg
              dimColor: win.dim
              gridColor: win.grid
              surfaceColor: win.surface
              fontFamily: win.fontName
              viewStart: win.viewStart
              viewEnd: win.viewEnd
              cursorIndex: win.cursor
              projectedIndex: win.doc && win.doc.tomorrow ? win.xs.length - 1 : -1
            }
          }
        }

        // shared x axis: nice kilometre ticks and the cursor position
        Canvas {
          id: axis
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: stack.bottom
          height: plots.axisH

          Connections {
            target: win
            function onViewStartChanged() { axis.requestPaint() }
            function onViewEndChanged() { axis.requestPaint() }
            function onCursorChanged() { axis.requestPaint() }
            function onDocChanged() { axis.requestPaint() }
          }
          onWidthChanged: requestPaint()

          onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            if (!win.doc) return
            var g = 56, pw = width - g - 12
            if (pw <= 10) return
            function px(x) { return g + (x - win.viewStart) / (win.viewEnd - win.viewStart) * pw }
            ctx.font = "12px " + win.cssFont
            ctx.textBaseline = "top"
            ctx.textAlign = "center"
            ctx.lineWidth = 1
            var isDate = win.doc.x.unit === "date"
            var ts = isDate ? Logic.dateTicks(win.viewStart, win.viewEnd)
                            : Logic.ticks(win.viewStart, win.viewEnd, Math.max(3, pw / 90)).map(function(v) {
                                return { x: v, label: String(Math.round(v * 1000) / 1000) } })
            for (var i = 0; i < ts.length; i++) {
              var x = Math.round(px(ts[i].x)) + 0.5
              ctx.strokeStyle = win.grid
              ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 5); ctx.stroke()
              ctx.fillStyle = win.dim
              ctx.fillText(ts[i].label, x, 8)
            }
            if (!isDate) {
              ctx.textAlign = "right"
              ctx.fillStyle = win.dim
              ctx.fillText(win.doc.x.unit, g - 10, 8)      // in the gutter, clear of the last tick label
            }

            // cursor marker: a small pill with the exact position
            var cx = px(win.xs[win.cursor])
            if (cx >= g && cx <= g + pw) {
              var label = win.xText(win.xs[win.cursor])
              ctx.font = "bold 12px " + win.cssFont
              var tw = ctx.measureText(label).width
              var lx = Math.max(g, Math.min(g + pw - tw - 12, cx - tw / 2 - 6))
              ctx.fillStyle = win.wash
              ctx.fillRect(lx, 4, tw + 12, 20)
              ctx.fillStyle = win.fg
              ctx.textAlign = "left"
              ctx.fillText(label, lx + 6, 8)
            }
          }
        }

        // pointer handling over the whole plot column (the crosshair finds the x for you)
        MouseArea {
          id: pointer
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: parent.top
          height: stack.height
          hoverEnabled: true
          cursorShape: pressed ? Qt.ClosedHandCursor : Qt.CrossCursor

          property real pressX: 0
          property real pressStart: 0
          property real pressEnd: 0
          property bool moved: false

          function xAt(px) { return win.viewStart + (px - 56) / (width - 56 - 12) * (win.viewEnd - win.viewStart) }

          onPressed: function(m) { pressX = m.x; pressStart = win.viewStart; pressEnd = win.viewEnd; moved = false }
          onPositionChanged: function(m) {
            if (pressed) {
              var dx = m.x - pressX
              if (Math.abs(dx) > 3) moved = true
              if (moved) {
                var per = (pressEnd - pressStart) / (width - 56 - 12)
                var r = Logic.pan(pressStart, pressEnd, win.xLo, win.xHi, -dx * per)
                win.viewStart = r[0]; win.viewEnd = r[1]
              }
            } else {
              win.setCursorAt(xAt(m.x))
            }
          }
          onClicked: function(m) { if (!moved) win.setCursorAt(xAt(m.x)) }
          onDoubleClicked: win.resetZoom()
          onWheel: function(w) { win.zoomAt(xAt(w.x), w.angleDelta.y > 0 ? 0.8 : 1.25); w.accepted = true }
        }
      }

      // ===== table view: every stored value, browsable; click a row to move the cursor there
      Item {
        id: tableView
        visible: win.mode === "table"
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: parent.width - body.legendW - 16

        readonly property real colW: width / (1 + win.shownSeries.length)

        Row {
          id: tableHead
          height: 28
          Text {
            width: tableView.colW; height: 28; verticalAlignment: Text.AlignVCenter
            text: win.doc ? (win.doc.x.unit === "date" ? "Date" : win.doc.x.label + " (" + win.doc.x.unit + ")") : ""
            color: win.fg; font.family: win.fontName; font.pixelSize: 12; font.bold: true
          }
          Repeater {
            model: win.shownSeries
            Text {
              required property var modelData
              width: tableView.colW; height: 28; verticalAlignment: Text.AlignVCenter
              text: modelData.label + (modelData.unit ? " (" + modelData.unit + ")" : "")
              color: win.fg; font.family: win.fontName; font.pixelSize: 12; font.bold: true
              elide: Text.ElideRight
            }
          }
        }

        Rectangle { id: tableRule; anchors.top: tableHead.bottom; width: parent.width; height: 1; color: win.grid }

        ListView {
          id: rows
          anchors.top: tableRule.bottom
          anchors.bottom: parent.bottom
          width: parent.width
          clip: true
          model: win.xs.length
          currentIndex: win.cursor
          boundsBehavior: Flickable.StopAtBounds
          onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)

          delegate: Rectangle {
            id: row
            required property int index
            width: rows.width
            height: 24
            color: row.index === win.cursor ? win.wash : "transparent"

            Row {
              Text {
                width: tableView.colW; height: 24; verticalAlignment: Text.AlignVCenter
                text: win.doc ? Logic.fmtX(win.doc.x, win.xs[row.index]) : ""
                color: win.fg; font.family: win.fontName; font.pixelSize: 12
              }
              Repeater {
                model: win.shownSeries
                Text {
                  required property var modelData
                  width: tableView.colW; height: 24; verticalAlignment: Text.AlignVCenter
                  text: Logic.fmtValue(modelData, modelData.values[row.index])
                  color: win.fg; font.family: win.fontName; font.pixelSize: 12
                }
              }
            }

            MouseArea { anchors.fill: parent; onClicked: win.cursor = row.index }
          }
        }
      }

      // ===== legend: every measure's value at the cursor, plus min / avg / max of what is in view
      Item {
        id: legend
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: body.legendW

        Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: win.grid }

        // LapBar's own logo on top: Strava's rules say their logo must not be more prominent than ours
        Image {
          anchors.left: parent.left
          anchors.leftMargin: 16
          anchors.top: parent.top
          anchors.topMargin: 14
          visible: win.assetsDir !== ""
          source: win.assetsDir === "" ? "" : "file://" + win.assetsDir + "/logo/lapbar_horiz_" + (win.dark ? "white" : "black") + ".svg"
          sourceSize.height: 96
          height: 30
          fillMode: Image.PreserveAspectFit
        }

        // Strava's own "Powered by Strava" logo (unmodified; see assets/strava/NOTICE.md), smaller than ours
        Image {
          anchors.left: parent.left
          anchors.leftMargin: 16
          anchors.bottom: parent.bottom
          visible: win.assetsDir !== ""
          source: win.assetsDir === "" ? "" : "file://" + win.assetsDir + "/strava/api_logo_pwrdBy_strava_horiz_" + (win.dark ? "white" : "black") + ".svg"
          sourceSize.height: 48
          height: 16
          fillMode: Image.PreserveAspectFit
        }

        Column {
          anchors.fill: parent
          anchors.leftMargin: 16
          anchors.topMargin: win.assetsDir !== "" ? 58 : 0
          spacing: 12

          Column {
            spacing: 2
            Text {
              text: win.xText(win.xs[win.cursor])
              color: win.fg; font.family: win.fontName; font.pixelSize: 20; font.bold: true
            }
            Text {
              text: "click a measure to show or hide it"
              color: win.dim; font.family: win.fontName; font.pixelSize: 11
            }
          }

          Repeater {
            model: win.doc ? win.doc.series : []

            Item {
              id: lrow
              required property var modelData
              readonly property bool off: !!win.hidden[modelData.key]
              readonly property var st: win.legendStats(modelData)
              width: legend.width - 16
              height: 58
              opacity: off ? 0.4 : 1

              Rectangle {                                  // line key in the series colour
                x: 0; y: 9; width: 14; height: 2; radius: 1
                color: win.colorFor(lrow.modelData)
              }

              Text {
                x: 22; y: 0
                text: lrow.modelData.label
                color: win.dim; font.family: win.fontName; font.pixelSize: 12
              }

              Row {
                x: 22; y: 15; spacing: 6
                Text {
                  text: Logic.fmtValue(lrow.modelData, lrow.modelData.values[win.cursor])
                  color: win.fg; font.family: win.fontName; font.pixelSize: 20; font.bold: true
                }
                Text {
                  anchors.baseline: parent.children[0].baseline
                  text: lrow.modelData.unit
                  color: win.dim; font.family: win.fontName; font.pixelSize: 12
                }
              }

              Text {
                x: 22; y: 42
                visible: !!lrow.st
                text: lrow.st ? "min " + Logic.fmtValue(lrow.modelData, lrow.st.min) + "  ·  avg "
                                + Logic.fmtValue(lrow.modelData, lrow.st.avg) + "  ·  max "
                                + Logic.fmtValue(lrow.modelData, lrow.st.max) : ""
                color: win.dim; font.family: win.fontName; font.pixelSize: 11
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: win.toggleSeries(lrow.modelData.key)
              }
            }
          }
        }
      }
    }
  }
}
