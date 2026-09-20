import QtQuick

// A month calendar for choosing a day, drawn inline (it takes its own space, so nothing overlaps or gets clipped).
// Days before `minDate` or after `maxDate` (ISO "YYYY-MM-DD") are dimmed and cannot be picked. Weeks start on
// Monday. « and » move by a year, ‹ and › by a month. Call show(iso) to open it on a date's month.
Item {
  id: picker

  property string selected: ""
  property string minDate: ""
  property string maxDate: ""
  property int viewYear: new Date().getFullYear()
  property int viewMonth: new Date().getMonth()      // 0-11
  signal picked(string date)

  Theme { id: th }

  readonly property var monthNames: ["January", "February", "March", "April", "May", "June", "July", "August",
                                     "September", "October", "November", "December"]
  readonly property var weekdays: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

  implicitHeight: column.implicitHeight

  function pad(n) { return n < 10 ? "0" + n : "" + n }
  function iso(y, m, d) { return y + "-" + pad(m + 1) + "-" + pad(d) }
  function todayIso() { var t = new Date(); return iso(t.getFullYear(), t.getMonth(), t.getDate()) }
  function last() { return picker.maxDate !== "" ? picker.maxDate : picker.todayIso() }
  function monthIndex(isoDate) { return parseInt(isoDate.slice(0, 4)) * 12 + parseInt(isoDate.slice(5, 7)) - 1 }

  // Open on the month of `isoDate` (or the selected day, or the earliest allowed day).
  function show(isoDate) {
    var d = isoDate || picker.selected || picker.minDate || picker.todayIso()
    picker.viewYear = parseInt(d.slice(0, 4))
    picker.viewMonth = parseInt(d.slice(5, 7)) - 1
  }

  function shift(months) {
    var idx = picker.viewYear * 12 + picker.viewMonth + months
    if (picker.minDate !== "") idx = Math.max(idx, picker.monthIndex(picker.minDate))
    idx = Math.min(idx, picker.monthIndex(picker.last()))
    picker.viewYear = Math.floor(idx / 12)
    picker.viewMonth = idx % 12
  }

  readonly property bool canGoBack: picker.minDate === "" || picker.viewYear * 12 + picker.viewMonth > monthIndex(picker.minDate)
  readonly property bool canGoForward: picker.viewYear * 12 + picker.viewMonth < monthIndex(picker.last())

  function cells() {
    var lead = (new Date(picker.viewYear, picker.viewMonth, 1).getDay() + 6) % 7
    var count = new Date(picker.viewYear, picker.viewMonth + 1, 0).getDate()
    var out = []
    for (var i = 0; i < lead; i++) out.push({ day: 0, key: "", allowed: false, isToday: false, isSelected: false })
    var today = picker.todayIso()
    for (var d = 1; d <= count; d++) {
      var key = picker.iso(picker.viewYear, picker.viewMonth, d)
      out.push({
        day: d,
        key: key,
        allowed: (picker.minDate === "" || key >= picker.minDate) && key <= picker.last(),
        isToday: key === today,
        isSelected: key === picker.selected
      })
    }
    return out
  }

  Column {
    id: column
    width: picker.width
    spacing: 6

    Item {
      width: parent.width
      height: 26

      Row {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12
        opacity: picker.canGoBack ? 1 : 0.3

        Text {
          text: "«"
          color: th.fg
          font.family: th.fontName
          font.pixelSize: 16
          MouseArea { anchors.fill: parent; anchors.margins: -6; enabled: picker.canGoBack; cursorShape: Qt.PointingHandCursor; onClicked: picker.shift(-12) }
        }

        Text {
          text: "‹"
          color: th.fg
          font.family: th.fontName
          font.pixelSize: 16
          MouseArea { anchors.fill: parent; anchors.margins: -6; enabled: picker.canGoBack; cursorShape: Qt.PointingHandCursor; onClicked: picker.shift(-1) }
        }
      }

      Text {
        anchors.centerIn: parent
        text: picker.monthNames[picker.viewMonth] + " " + picker.viewYear
        color: th.fg
        font.family: th.fontName
        font.pixelSize: 13
        font.bold: true
      }

      Row {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12
        opacity: picker.canGoForward ? 1 : 0.3

        Text {
          text: "›"
          color: th.fg
          font.family: th.fontName
          font.pixelSize: 16
          MouseArea { anchors.fill: parent; anchors.margins: -6; enabled: picker.canGoForward; cursorShape: Qt.PointingHandCursor; onClicked: picker.shift(1) }
        }

        Text {
          text: "»"
          color: th.fg
          font.family: th.fontName
          font.pixelSize: 16
          MouseArea { anchors.fill: parent; anchors.margins: -6; enabled: picker.canGoForward; cursorShape: Qt.PointingHandCursor; onClicked: picker.shift(12) }
        }
      }
    }

    Row {
      spacing: 3
      Repeater {
        model: picker.weekdays
        Text {
          required property string modelData
          width: (column.width - 6 * 3) / 7
          horizontalAlignment: Text.AlignHCenter
          text: modelData
          color: th.dim
          font.family: th.fontName
          font.pixelSize: 11
        }
      }
    }

    Grid {
      id: grid
      columns: 7
      columnSpacing: 3
      rowSpacing: 3

      Repeater {
        model: picker.cells()

        Item {
          id: cell
          required property var modelData
          width: (column.width - 6 * 3) / 7
          height: 26

          Rectangle {
            anchors.fill: parent
            visible: cell.modelData.day > 0
            radius: 4
            color: cell.modelData.isSelected ? th.accent : (dayMouse.containsMouse && cell.modelData.allowed ? th.mix(th.fg, th.surface, 0.18) : "transparent")
            border.width: cell.modelData.isToday && !cell.modelData.isSelected ? 1 : 0
            border.color: th.fg
          }

          Text {
            anchors.centerIn: parent
            visible: cell.modelData.day > 0
            text: cell.modelData.day
            color: cell.modelData.isSelected ? th.surface : th.fg
            opacity: cell.modelData.allowed ? 1 : 0.3
            font.family: th.fontName
            font.pixelSize: 12
            font.bold: cell.modelData.isSelected
          }

          MouseArea {
            id: dayMouse
            anchors.fill: parent
            hoverEnabled: true
            enabled: cell.modelData.day > 0 && cell.modelData.allowed
            cursorShape: Qt.PointingHandCursor
            onClicked: picker.picked(cell.modelData.key)
          }
        }
      }
    }
  }
}
