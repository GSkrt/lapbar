import QtQuick

// A progress bar: value is 0 to 1.
Rectangle {
  id: meter

  property real value: 0
  property color fillColor: th.accent

  Theme { id: th }

  implicitHeight: 10
  radius: 5
  color: th.mix(th.fg, th.surface, 0.12)

  Rectangle {
    width: Math.max(meter.value > 0 ? 6 : 0, meter.width * Math.min(1, meter.value))
    height: parent.height
    radius: 5
    color: meter.fillColor
  }
}
