import QtQuick
import "logic.js" as Logic

// One panel of the chart window, drawn against the shared x axis. Panels are stacked; they share the view range
// and the cursor, so the crosshair lines up across all of them.
//
// A panel draws its primary series and, for measures that share units (fitness and fatigue), any `overlay`
// series on the same y axis. `series.draw` selects the look: "line" (default), "bars" (daily load) or "form"
// (signed bars around zero: fresh above, tired below).
Item {
  id: panel

  required property var series
  property var overlay: []                      // more series on the same axis
  property var overlayColors: []
  required property var xs
  required property var xMeta
  property color lineColor: "#3987e5"
  property string positiveColor: "#4caf50"
  property string negativeColor: "#ff8a4c"
  property string textColor: "#cacccc"
  property string dimColor: "#888888"
  property string gridColor: "#333333"
  property string surfaceColor: "#1f1f28"
  property string fontFamily: "monospace"
  readonly property string cssFamily: "\"" + fontFamily + "\""   // canvas needs spaces in family names quoted
  property real viewStart: 0
  property real viewEnd: 1
  property int cursorIndex: 0
  property bool showCursor: true

  readonly property real gutter: 56            // room for the y-axis labels
  readonly property real rightPad: 12
  readonly property real plotW: width - gutter - rightPad
  readonly property bool inverted: series.key === "pace"   // faster (smaller) pace sits higher
  readonly property string kind: series.draw || "line"

  onViewStartChanged: canvas.requestPaint()
  onViewEndChanged: canvas.requestPaint()
  onCursorIndexChanged: canvas.requestPaint()
  onWidthChanged: canvas.requestPaint()
  onHeightChanged: canvas.requestPaint()
  onLineColorChanged: canvas.requestPaint()
  onSeriesChanged: canvas.requestPaint()
  onOverlayChanged: canvas.requestPaint()

  Canvas {
    id: canvas
    anchors.fill: parent
    antialiasing: true

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var W = width, H = height
      var x0 = panel.gutter, pw = panel.plotW
      var top = 22, bottom = H - 6, ph = bottom - top
      if (pw <= 10 || ph <= 10 || !panel.xs || panel.xs.length === 0) return

      var all = [panel.series].concat(panel.overlay)
      var colors = [panel.lineColor].concat(panel.overlayColors)
      var range = Logic.visibleRange(panel.xs, panel.viewStart, panel.viewEnd)
      var font = "12px " + panel.cssFamily
      var kind = panel.kind

      // ---------- y range: the data extent of everything in this panel (form is symmetric, bars start at 0)
      var lo = Infinity, hi = -Infinity
      for (var q = 0; q < all.length; q++) {
        var st = Logic.stats(all[q].values, range[0], range[1])
        if (st) { lo = Math.min(lo, st.min); hi = Math.max(hi, st.max) }
      }
      var yr = null
      if (isFinite(lo)) {
        if (kind === "form") {
          var m = Math.max(Math.abs(lo), Math.abs(hi), 1)
          yr = { min: -m * 1.12, max: m * 1.12, dataMin: -m, dataMax: m }
        } else if (kind === "bars") {
          yr = { min: 0, max: Math.max(hi, 1) * 1.12, dataMin: 0, dataMax: Math.max(hi, 1) }
        } else {
          var pad = (hi - lo) * 0.08
          if (pad === 0) pad = Math.max(1, Math.abs(hi) * 0.05)
          yr = { min: lo - pad, max: hi + pad, dataMin: lo, dataMax: hi }
        }
      }

      // ---------- title: a short line key per series in its colour, names in ordinary text colour
      ctx.font = "bold 12px " + panel.cssFamily
      ctx.textBaseline = "middle"
      var tx = x0
      for (var t = 0; t < all.length; t++) {
        ctx.fillStyle = kind === "form" ? panel.positiveColor : colors[t]
        ctx.fillRect(tx, 8, 14, 2)
        ctx.fillStyle = panel.textColor
        ctx.fillText(all[t].label, tx + 20, 9)
        tx += 20 + ctx.measureText(all[t].label).width + 14
      }
      if (all[0].unit) ctx.fillText("(" + all[0].unit + ")", tx - 4, 9)

      if (!yr) {
        ctx.font = font; ctx.fillStyle = panel.dimColor
        ctx.fillText("no data in this range", x0 + 8, top + ph / 2)
        return
      }

      function px(x) { return x0 + (x - panel.viewStart) / (panel.viewEnd - panel.viewStart) * pw }
      function py(v) {
        var f = (v - yr.min) / (yr.max - yr.min)
        return panel.inverted ? top + f * ph : bottom - f * ph
      }

      // ---------- grid: hairline, solid, recessive; y labels in secondary text
      ctx.font = font
      ctx.textBaseline = "middle"
      ctx.lineWidth = 1
      var yt = panel.series.format === "pace"
        ? Logic.ticks(yr.dataMin / 60, yr.dataMax / 60, 3).map(function(mm) { return mm * 60 })   // whole minutes
        : Logic.ticks(yr.dataMin, yr.dataMax, kind === "form" ? 4 : 3)
      for (var k = 0; k < yt.length; k++) {
        var gy = Math.round(py(yt[k])) + 0.5
        ctx.strokeStyle = (kind === "form" && yt[k] === 0) ? Logic.rgba(panel.textColor, 0.35) : panel.gridColor
        ctx.beginPath(); ctx.moveTo(x0, gy); ctx.lineTo(x0 + pw, gy); ctx.stroke()
        ctx.fillStyle = panel.dimColor
        ctx.textAlign = "right"
        ctx.fillText(Logic.fmtTick(panel.series, yt[k]), x0 - 8, gy)
      }
      ctx.textAlign = "left"

      // ---------- clip to the plot area
      ctx.save()
      ctx.beginPath(); ctx.rect(x0, top - 2, pw, ph + 4); ctx.clip()

      if (kind === "form" || kind === "bars") {
        // one bar per sample; the width follows the zoom so bars neither overlap nor vanish
        var visible = Math.max(1, range[1] - range[0] + 1)
        var bw = Math.max(1, pw / visible * 0.8)
        var base = py(0)
        var vals0 = panel.series.values
        for (var b = range[0]; b <= range[1]; b++) {
          var v0 = vals0[b]
          if (v0 === null || v0 === undefined) continue
          var bx = px(panel.xs[b]) - bw / 2
          var by = py(v0)
          ctx.fillStyle = kind === "bars" ? Logic.rgba(panel.lineColor, 0.8)
                                          : Logic.rgba(v0 >= 0 ? panel.positiveColor : panel.negativeColor, 0.85)
          ctx.fillRect(bx, Math.min(by, base), bw, Math.max(1, Math.abs(by - base)))
        }
      } else {
        // lines, 2px, gaps where data is missing; the elevation profile also gets a 10% wash
        var stride = Math.max(1, Math.floor((range[1] - range[0]) / (pw * 1.5)))
        for (var sIdx = all.length - 1; sIdx >= 0; sIdx--) {           // primary drawn last, so it sits on top
          var vals = all[sIdx].values
          var segments = []
          var run = []
          for (var i = range[0]; i <= range[1]; i += stride) {
            var v = vals[i]
            if (v === null || v === undefined) {
              if (run.length) { segments.push(run); run = [] }
            } else run.push([px(panel.xs[i]), py(v)])
          }
          if (run.length) segments.push(run)

          ctx.lineJoin = "round"; ctx.lineCap = "round"
          for (var sg = 0; sg < segments.length; sg++) {
            var seg = segments[sg]
            if (seg.length > 1 && all[sIdx].key === "altitude") {
              ctx.beginPath()
              ctx.moveTo(seg[0][0], bottom)
              for (var a = 0; a < seg.length; a++) ctx.lineTo(seg[a][0], seg[a][1])
              ctx.lineTo(seg[seg.length - 1][0], bottom)
              ctx.closePath()
              ctx.fillStyle = Logic.rgba(colors[sIdx], 0.10)
              ctx.fill()
            }
            ctx.beginPath()
            ctx.moveTo(seg[0][0], seg[0][1])
            if (seg.length === 1) ctx.lineTo(seg[0][0] + 0.1, seg[0][1])
            for (var bb = 1; bb < seg.length; bb++) ctx.lineTo(seg[bb][0], seg[bb][1])
            ctx.strokeStyle = colors[sIdx]
            ctx.lineWidth = 2
            ctx.stroke()
          }
        }
      }
      ctx.restore()

      // ---------- crosshair: a hairline at the nearest sample, an end-dot per series with a surface ring, and values
      var ci = panel.cursorIndex
      if (panel.showCursor && ci >= 0 && ci < panel.xs.length) {
        var cx = px(panel.xs[ci])
        if (cx >= x0 - 1 && cx <= x0 + pw + 1) {
          ctx.strokeStyle = Logic.rgba(panel.textColor, 0.35)
          ctx.lineWidth = 1
          ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, top - 2); ctx.lineTo(Math.round(cx) + 0.5, bottom); ctx.stroke()
          for (var n = 0; n < all.length; n++) {
            var cv = all[n].values[ci]
            if (cv === null || cv === undefined) continue
            var dotColor = kind === "form" ? (cv >= 0 ? panel.positiveColor : panel.negativeColor) : colors[n]
            var cy = py(cv)
            ctx.fillStyle = panel.surfaceColor
            ctx.beginPath(); ctx.arc(cx, cy, 6.5, 0, Math.PI * 2); ctx.fill()        // 2px ring in the surface colour
            ctx.fillStyle = dotColor
            ctx.beginPath(); ctx.arc(cx, cy, 4.5, 0, Math.PI * 2); ctx.fill()
            var label = Logic.fmtValue(all[n], cv)
            ctx.font = "bold 12px " + panel.cssFamily
            var tw = ctx.measureText(label).width
            var lx = cx + 10
            if (lx + tw > x0 + pw) lx = cx - 10 - tw            // flip to the left near the right edge
            var ly = Math.max(top + 8, Math.min(bottom - 8, cy + (n === 0 ? -14 : 14)))
            ctx.fillStyle = panel.surfaceColor
            ctx.fillRect(lx - 3, ly - 8, tw + 6, 16)
            ctx.fillStyle = panel.textColor
            ctx.textAlign = "left"
            ctx.fillText(label, lx, ly)
          }
        }
      }
    }
  }
}
