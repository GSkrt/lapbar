import QtQuick
import Quickshell

// Colours and font from the bar's theme (the launcher passes them in the environment), shared by the parts of the window.
QtObject {
  readonly property string fg: Quickshell.env("LAPBAR_FG") || "#cacccc"
  readonly property string surface: Quickshell.env("LAPBAR_BG") || "#1f1f28"
  readonly property string accent: Quickshell.env("LAPBAR_ACCENT") || "#7aa2f7"
  readonly property string fontName: Quickshell.env("LAPBAR_FONT") || "monospace"
  readonly property string warn: "#ff8a4c"
  readonly property string good: "#4caf50"

  function mix(a, b, t) {
    var ca = Qt.color(a), cb = Qt.color(b)
    return Qt.rgba(ca.r * t + cb.r * (1 - t), ca.g * t + cb.g * (1 - t), ca.b * t + cb.b * (1 - t), 1)
  }

  readonly property color dim: mix(fg, surface, 0.62)
  readonly property color line: mix(fg, surface, 0.18)
  readonly property color card: mix(fg, surface, 0.05)
}
