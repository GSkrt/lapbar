import QtQuick

// A checkbox with a wrapped label. Click anywhere on the row to toggle; `toggled` is emitted, the caller decides.
Item {
  id: check

  property bool checked: false
  property string label: ""
  signal toggled()

  Theme { id: th }

  implicitHeight: Math.max(18, text.implicitHeight)
  opacity: enabled ? 1 : 0.5

  Rectangle {
    id: box
    width: 18
    height: 18
    radius: 4
    y: 1
    color: check.checked ? th.accent : "transparent"
    border.width: 1
    border.color: check.checked ? th.accent : th.dim

    Text {
      anchors.centerIn: parent
      visible: check.checked
      text: "✓"
      color: th.surface
      font.pixelSize: 13
      font.bold: true
    }
  }

  Text {
    id: text
    anchors.left: box.right
    anchors.leftMargin: 8
    anchors.right: parent.right
    wrapMode: Text.WordWrap
    text: check.label
    color: th.fg
    font.family: th.fontName
    font.pixelSize: 13
  }

  MouseArea {
    anchors.fill: parent
    enabled: check.enabled
    cursorShape: Qt.PointingHandCursor
    onClicked: check.toggled()
  }
}
