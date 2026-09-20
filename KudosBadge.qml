import QtQuick

// The kudos count with a large thumbs up next to it: the popup's small celebration for a ride that got kudos.
Row {
  id: badge

  property int count: 0
  property string glyph: ""
  property color foreground: "#cacccc"
  property color dim: "#888888"
  property string fontFamily: "monospace"
  property real iconSize: 40
  property real numberSize: 26
  property real captionSize: 12

  spacing: iconSize * 0.3

  Text {
    anchors.verticalCenter: parent.verticalCenter
    text: badge.glyph
    color: badge.foreground
    font.family: badge.fontFamily
    font.pixelSize: badge.iconSize
  }

  Column {
    anchors.verticalCenter: parent.verticalCenter

    Text {
      text: String(badge.count)
      color: badge.foreground
      font.family: badge.fontFamily
      font.pixelSize: badge.numberSize
      font.bold: true
    }

    Text {
      text: badge.count === 1 ? "kudo" : "kudos"
      color: badge.dim
      font.family: badge.fontFamily
      font.pixelSize: badge.captionSize
    }
  }
}
