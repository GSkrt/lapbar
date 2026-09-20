import QtQuick

// A button. Disabled (enabled: false) it is dimmed and ignores clicks.
Rectangle {
  id: btn

  property string label: ""
  property bool primary: false
  signal clicked()

  Theme { id: th }

  implicitWidth: btnText.implicitWidth + 24
  implicitHeight: 30
  radius: 5
  opacity: enabled ? 1 : 0.4
  color: primary ? th.accent : (btnMouse.containsMouse && enabled ? th.mix(th.fg, th.surface, 0.2) : th.mix(th.fg, th.surface, 0.1))

  Text {
    id: btnText
    anchors.centerIn: parent
    text: btn.label
    color: btn.primary ? th.surface : th.fg
    font.family: th.fontName
    font.pixelSize: 13
    font.bold: btn.primary
  }

  MouseArea {
    id: btnMouse
    anchors.fill: parent
    hoverEnabled: true
    enabled: btn.enabled
    cursorShape: Qt.PointingHandCursor
    onClicked: btn.clicked()
  }
}
