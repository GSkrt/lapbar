import QtQuick

// A one-line text field. `typing` is true while it has the keyboard, so a refresh does not overwrite what you type.
Rectangle {
  id: field

  property alias text: input.text
  property string placeholder: ""
  readonly property bool typing: input.activeFocus
  signal accepted()

  Theme { id: th }

  implicitHeight: 30
  radius: 5
  color: th.mix(th.fg, th.surface, 0.06)
  border.width: 1
  border.color: input.activeFocus ? th.accent : th.line
  clip: true

  TextInput {
    id: input
    anchors.fill: parent
    anchors.leftMargin: 8
    anchors.rightMargin: 8
    verticalAlignment: TextInput.AlignVCenter
    color: th.fg
    selectionColor: th.accent
    selectedTextColor: th.surface
    font.family: th.fontName
    font.pixelSize: 13
    selectByMouse: true
    clip: true
    onAccepted: field.accepted()
    onTextChanged: if (!activeFocus) cursorPosition = 0        // show the start of a long path, not its end

    Text {
      anchors.fill: parent
      verticalAlignment: Text.AlignVCenter
      visible: input.text === "" && !input.activeFocus
      text: field.placeholder
      color: th.dim
      font: input.font
    }
  }
}
