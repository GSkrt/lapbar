import QtQuick

// A small rounded button with a blue background, for the popup's "open ..." actions (the charts, the fitness chart).
Rectangle {
  id: btn

  property string label: ""
  property string fontFamily: "monospace"
  property real fontSize: 12
  property color fillColor: "#2a78d6"            // blue, with white text on both light and dark themes
  property color hoverColor: "#3987e5"
  property color textColor: "#ffffff"
  property bool busy: false                      // shown dimmed while what it opened is still starting
  signal clicked()

  implicitWidth: caption.implicitWidth + 18
  implicitHeight: caption.implicitHeight + 6
  radius: height / 2
  color: mouse.containsMouse && !btn.busy ? btn.hoverColor : btn.fillColor
  opacity: btn.busy ? 0.6 : 1

  Text {
    id: caption
    anchors.centerIn: parent
    text: btn.label
    color: btn.textColor
    font.family: btn.fontFamily
    font.pixelSize: btn.fontSize
    font.bold: true
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: if (!btn.busy) btn.clicked()
  }
}
