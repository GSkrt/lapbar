import QtQuick

// Compact fitness / fatigue / form plot for the popup: fitness and fatigue as two lines sharing one axis, and
// form as bars underneath (above zero = fresh, below zero = tired). Hover reads any day.
Item {
  id: plot

  property var days: []                       // [{date, load, fitness, fatigue, form, warmup}]
  property var tomorrow: null                 // {date, form}: one more bar after today, drawn as tomorrow's
  property color fitnessColor: "#3987e5"
  property color fatigueColor: "#d95926"
  property color positiveColor: "#4caf50"
  property color negativeColor: "#ff8a4c"
  property string textColor: "#cacccc"
  property string dimColor: "#888888"
  property string gridColor: "#333333"
  property string surfaceColor: "#1f1f28"
  property string fontFamily: "monospace"
  readonly property string cssFamily: "\"" + fontFamily + "\""

  readonly property int slots: days.length + (tomorrow ? 1 : 0)            // days, and tomorrow's bar
  property int hoverIndex: -1                  // -1 = not hovering: today is "shown"; days.length is tomorrow
  readonly property int shownIndex: hoverIndex >= 0 ? hoverIndex : days.length - 1
  readonly property var shownDay: {
    if (days.length === 0) return null
    if (tomorrow && shownIndex >= days.length) return { date: tomorrow.date, form: tomorrow.form, tomorrow: true }
    return days[Math.max(0, Math.min(days.length - 1, shownIndex))]
  }

  implicitHeight: 130

  readonly property var months: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

  onDaysChanged: canvas.requestPaint()
  onTomorrowChanged: canvas.requestPaint()
  onHoverIndexChanged: canvas.requestPaint()
  onWidthChanged: canvas.requestPaint()
  onHeightChanged: canvas.requestPaint()

  function rgba(hex, a) {
    var s = String(hex).replace("#", "")
    if (s.length === 8) s = s.slice(2)
    return "rgba(" + parseInt(s.slice(0, 2), 16) + "," + parseInt(s.slice(2, 4), 16) + "," + parseInt(s.slice(4, 6), 16) + "," + a + ")"
  }

  Canvas {
    id: canvas
    anchors.fill: parent
    antialiasing: true

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var n = plot.days.length
      if (n < 2) return
      var slots = plot.slots                                   // one more than the days when tomorrow's bar is there
      var left = 26, right = 4, top = 4, bottomLabels = 14
      var pw = width - left - right
      var linesH = Math.round((height - top - bottomLabels) * 0.62)
      var formTop = top + linesH + 8
      var formH = height - bottomLabels - formTop
      function px(i) { return left + i / (slots - 1) * pw }

      // ---- scales
      var top1 = 1
      var maxAbs = 1
      for (var i = 0; i < n; i++) {
        top1 = Math.max(top1, plot.days[i].fitness, plot.days[i].fatigue)
        maxAbs = Math.max(maxAbs, Math.abs(plot.days[i].form))
      }
      if (plot.tomorrow) maxAbs = Math.max(maxAbs, Math.abs(plot.tomorrow.form))
      top1 = Math.ceil(top1 / 10) * 10
      function py(v) { return top + linesH - v / top1 * linesH }
      var zeroY = formTop + formH / 2

      // ---- grid and labels (hairline, recessive)
      ctx.font = "10px " + plot.cssFamily
      ctx.textBaseline = "middle"
      ctx.textAlign = "right"
      ctx.lineWidth = 1
      ctx.strokeStyle = plot.gridColor
      ctx.fillStyle = plot.dimColor
      var gridAt = [0, top1]
      for (var g = 0; g < gridAt.length; g++) {
        var gy = Math.round(py(gridAt[g])) + 0.5
        ctx.beginPath(); ctx.moveTo(left, gy); ctx.lineTo(left + pw, gy); ctx.stroke()
        ctx.fillText(String(gridAt[g]), left - 4, gy)
      }
      var zy = Math.round(zeroY) + 0.5
      ctx.beginPath(); ctx.moveTo(left, zy); ctx.lineTo(left + pw, zy); ctx.stroke()
      ctx.fillText("0", left - 4, zy)

      // ---- warm-up: the first weeks of data are less reliable, so wash them out
      var warm = 0
      while (warm < n && plot.days[warm].warmup) warm++
      if (warm > 0) {
        ctx.fillStyle = plot.rgba(plot.textColor, 0.05)
        ctx.fillRect(left, top, px(Math.min(warm, n - 1)) - left, linesH)
      }

      // ---- form bars (polarity: above zero fresh, below zero tired)
      var bw = Math.max(1, pw / slots - 0.6)
      for (var k = 0; k < n; k++) {
        var f = plot.days[k].form
        var h = Math.abs(f) / maxAbs * (formH / 2)
        ctx.fillStyle = plot.rgba(f >= 0 ? plot.positiveColor : plot.negativeColor, 0.85)
        if (f >= 0) ctx.fillRect(px(k) - bw / 2, zeroY - h, bw, h)
        else ctx.fillRect(px(k) - bw / 2, zeroY, bw, h)
      }
      if (plot.tomorrow) {                                     // tomorrow: decided by today's work, drawn as an outline
        var ft = plot.tomorrow.form
        var ht = Math.abs(ft) / maxAbs * (formH / 2)
        var tc = ft >= 0 ? plot.positiveColor : plot.negativeColor
        var ty = ft >= 0 ? zeroY - ht : zeroY
        var tw = Math.max(3, bw)
        ctx.fillStyle = plot.rgba(tc, 0.28)
        ctx.fillRect(px(n) - tw / 2, ty, tw, Math.max(1, ht))
        ctx.strokeStyle = plot.rgba(tc, 0.95)
        ctx.lineWidth = 1.5
        ctx.strokeRect(px(n) - tw / 2 + 0.75, ty + 0.75, tw - 1.5, Math.max(1, ht) - 1.5)
      }

      // ---- fitness and fatigue lines, 2px
      ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.lineWidth = 2
      var series = [["fatigue", plot.fatigueColor], ["fitness", plot.fitnessColor]]
      for (var s = 0; s < series.length; s++) {
        ctx.strokeStyle = series[s][1]
        ctx.beginPath()
        for (var j = 0; j < n; j++) {
          var y = py(plot.days[j][series[s][0]])
          if (j === 0) ctx.moveTo(px(j), y); else ctx.lineTo(px(j), y)
        }
        ctx.stroke()
      }

      // ---- month ticks
      ctx.font = "10px " + plot.cssFamily
      ctx.textAlign = "center"
      ctx.textBaseline = "top"
      ctx.fillStyle = plot.dimColor
      var lastLabel = -100
      for (var m = 0; m < n; m++) {
        if (plot.days[m].date.slice(8, 10) === "01" && px(m) - lastLabel > 30) {
          ctx.fillText(plot.months[parseInt(plot.days[m].date.slice(5, 7)) - 1], px(m), height - bottomLabels + 2)
          lastLabel = px(m)
        }
      }

      // ---- the day being read: a hairline and end-dots with a surface ring
      var c = Math.max(0, Math.min(slots - 1, plot.shownIndex))
      var cx = px(c)
      ctx.strokeStyle = plot.rgba(plot.textColor, 0.35)
      ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, top); ctx.lineTo(Math.round(cx) + 0.5, formTop + formH); ctx.stroke()
      var dots = c < n ? [[plot.days[c].fatigue, plot.fatigueColor], [plot.days[c].fitness, plot.fitnessColor]] : []
      for (var d = 0; d < dots.length; d++) {
        ctx.fillStyle = plot.surfaceColor
        ctx.beginPath(); ctx.arc(cx, py(dots[d][0]), 5.5, 0, Math.PI * 2); ctx.fill()
        ctx.fillStyle = dots[d][1]
        ctx.beginPath(); ctx.arc(cx, py(dots[d][0]), 3.5, 0, Math.PI * 2); ctx.fill()
      }
    }
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    onPositionChanged: function(m) {
      var n = plot.slots
      if (n < 2) return
      var frac = (m.x - 26) / (plot.width - 26 - 4)
      plot.hoverIndex = Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1))))
    }
    onExited: plot.hoverIndex = -1
  }
}
