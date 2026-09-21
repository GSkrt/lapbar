import QtQuick
import Quickshell
import Quickshell.Io

// LapBar's details window for one activity: its records (PRs and top places), who gave kudos, and the comments.
// The comments come first and can be folded. LapBar cannot answer a comment (Strava's API only lets it read them),
// so the way to reply is the View on Strava link.
//
// Run by `lapbar activity <id>` as its own Quickshell process (LAPBAR_ACTIVITY is the id, LAPBAR_BIN the launcher,
// LAPBAR_PYTHON the interpreter, LAPBAR_ASSETS the folder with the logos).
FloatingWindow {
  id: win

  title: "LapBar · Records, kudos and comments"
  implicitWidth: 760
  implicitHeight: 820
  minimumSize: Qt.size(480, 420)
  color: th.surface
  visible: true
  onClosed: Qt.quit()

  Theme { id: th }

  readonly property string assetsDir: Quickshell.env("LAPBAR_ASSETS") || ""
  readonly property bool dark: Qt.color(th.surface).hslLightness < 0.5
  readonly property string stravaOrange: "#FC5200"

  property var info: null           // {records, kudoers, comments, activity}
  property string error: ""
  property bool commentsOpen: true

  readonly property var activity: win.info ? win.info.activity : null
  readonly property var records: win.info ? win.info.records : []
  readonly property var kudoers: win.info ? win.info.kudoers : []
  readonly property var comments: win.info ? win.info.comments : []

  function glyph(code) { return String.fromCodePoint(code) }

  function medalColor(r) {
    return r.rank === 1 ? "#e6b422" : (r.rank === 2 ? "#b8bcc4" : (r.rank === 3 ? "#cd7f32" : th.dim))
  }

  function recordLabel(r) {
    if (r.kind === "kom") return r.rank === 1 ? "KOM/QOM" : "Top 10 (#" + r.rank + ")"
    return r.rank === 1 ? "PR" : (r.rank === 2 ? "2nd fastest" : "3rd fastest")
  }

  function pad2(n) { return n < 10 ? "0" + n : "" + n }

  function fmtEffort(seconds) {
    if (seconds === undefined || seconds === null) return ""
    var h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.round(seconds % 60)
    return h > 0 ? h + ":" + pad2(m) + ":" + pad2(s) : m + ":" + pad2(s)
  }

  function when(iso) {
    if (!iso) return ""
    var d = new Date(iso)
    return isNaN(d.getTime()) ? "" : Qt.formatDateTime(d, "ddd d MMM · HH:mm")
  }

  function openStrava() {
    if (win.activity && win.activity.url)
      Quickshell.execDetached(["/usr/bin/xdg-open", win.activity.url])
  }

  Process {
    id: loader
    running: true
    command: [Quickshell.env("LAPBAR_PYTHON") || "/usr/bin/python3", "-I", Quickshell.env("LAPBAR_BIN") || "lapbar",
              "details", Quickshell.env("LAPBAR_ACTIVITY") || "0"]
    stdout: StdioCollector { id: loaded; waitForEnd: true }
    onExited: {
      try {
        var r = JSON.parse(loaded.text)
        if (r.error) win.error = r.message || r.error
        else win.info = r
      } catch (e) { win.error = "Could not read the details." }
    }
  }

  Item {
    id: content
    anchors.fill: parent
    anchors.margins: 20
    focus: true
    Keys.onEscapePressed: Qt.quit()

    Item {
      id: header
      width: parent.width
      height: 36

      Image {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        visible: win.assetsDir !== ""
        source: win.assetsDir === "" ? "" : "file://" + win.assetsDir + "/logo/lapbar_horiz_" + (win.dark ? "white" : "black") + ".svg"
        sourceSize.height: 96
        height: 30
        fillMode: Image.PreserveAspectFit
      }

      Text {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "Esc to close"
        color: th.dim
        font.family: th.fontName
        font.pixelSize: 13
      }
    }

    Rectangle {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: header.bottom
      anchors.topMargin: 10
      anchors.bottom: footer.top
      anchors.bottomMargin: 10
      radius: 8
      color: th.card
      border.width: 1
      border.color: th.line
      clip: true

      Flickable {
        id: scroll
        anchors.fill: parent
        anchors.margins: 1
        contentWidth: width
        contentHeight: column.implicitHeight + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: column
          x: 24
          y: 20
          width: scroll.width - 48
          spacing: 16

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: win.activity ? (win.activity.name || "Activity") : (win.error !== "" ? "" : "Loading…")
            color: th.fg
            font.family: th.fontName
            font.pixelSize: 20
            font.bold: true
          }

          Row {
            visible: !!win.activity
            spacing: 18

            Text {
              text: win.activity ? (win.activity.sport || "") + "  ·  " + win.when(win.activity.start) : ""
              color: th.dim
              font.family: th.fontName
              font.pixelSize: 13
              anchors.verticalCenter: parent.verticalCenter
            }

            Text {
              text: "View on Strava"
              color: win.stravaOrange
              font.family: th.fontName
              font.pixelSize: 13
              font.bold: true
              anchors.verticalCenter: parent.verticalCenter

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: win.openStrava()
              }
            }
          }

          Text {
            visible: win.error !== ""
            width: parent.width
            wrapMode: Text.WordWrap
            text: win.error
            color: th.warn
            font.family: th.fontName
            font.pixelSize: 13
          }

          // ---------- comments: first, because they are what you came for (click the title to fold them) ----------

          Column {
            visible: !!win.activity && win.activity.comments > 0
            width: parent.width
            spacing: 10

            Item {
              width: parent.width
              height: commentsTitle.implicitHeight + 4

              Text {
                id: commentsTitle
                anchors.verticalCenter: parent.verticalCenter
                text: (win.commentsOpen ? "▾  " : "▸  ") + "Comments (" + (win.activity ? win.activity.comments : 0) + ")"
                color: th.fg
                font.family: th.fontName
                font.pixelSize: 15
                font.bold: true
              }

              Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                visible: !win.commentsOpen
                text: "hidden · click to read"
                color: th.dim
                font.family: th.fontName
                font.pixelSize: 12
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: win.commentsOpen = !win.commentsOpen
              }
            }

            Repeater {
              model: win.commentsOpen ? win.comments : []

              Column {
                id: comment
                required property var modelData
                width: column.width
                spacing: 2

                Row {
                  spacing: 10

                  Text {
                    textFormat: Text.PlainText
                    text: comment.modelData.who
                    color: th.fg
                    font.family: th.fontName
                    font.pixelSize: 13
                    font.bold: true
                  }

                  Text {
                    text: win.when(comment.modelData.at)
                    color: th.dim
                    font.family: th.fontName
                    font.pixelSize: 12
                  }
                }

                Text {
                  width: parent.width
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText        // other people's words: never read as markup
                  text: comment.modelData.text
                  color: th.fg
                  font.family: th.fontName
                  font.pixelSize: 13
                }
              }
            }

            Text {                                    // the way to answer: the activity's page on Strava
              visible: win.commentsOpen
              text: "Comment on Strava  \u2197"
              color: win.stravaOrange
              font.family: th.fontName
              font.pixelSize: 13
              font.bold: true

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: win.openStrava()
              }
            }

            Text {
              visible: win.commentsOpen
              width: parent.width
              wrapMode: Text.WordWrap
              text: "LapBar can read comments but not write them (Strava's API has no call for that), so the reply is written on Strava."
              color: th.dim
              font.family: th.fontName
              font.pixelSize: 12
            }
          }

          // ---------- records: a medal for a PR, a cup for a top place ----------

          Column {
            visible: win.records.length > 0
            width: parent.width
            spacing: 6

            Text {
              text: "Records"
              color: th.fg
              font.family: th.fontName
              font.pixelSize: 15
              font.bold: true
            }

            Repeater {
              model: win.records

              Item {
                id: row
                required property var modelData
                width: column.width
                height: 24

                Text {
                  id: rowIcon
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  width: 26
                  text: win.glyph(row.modelData.kind === "kom" ? 0xF0538 : 0xF0987)
                  color: win.medalColor(row.modelData)
                  font.family: th.fontName
                  font.pixelSize: 15
                }

                Text {
                  id: rowLabel
                  anchors.left: rowIcon.right
                  anchors.verticalCenter: parent.verticalCenter
                  width: 110
                  elide: Text.ElideRight
                  text: win.recordLabel(row.modelData)
                  color: th.fg
                  font.family: th.fontName
                  font.pixelSize: 13
                  font.bold: true
                }

                Text {
                  id: rowTime
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: win.fmtEffort(row.modelData.seconds)
                  color: th.fg
                  font.family: th.fontName
                  font.pixelSize: 13
                }

                Text {
                  anchors.left: rowLabel.right
                  anchors.right: rowTime.left
                  anchors.rightMargin: 10
                  anchors.verticalCenter: parent.verticalCenter
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  text: row.modelData.name
                  color: th.dim
                  font.family: th.fontName
                  font.pixelSize: 13
                }
              }
            }
          }

          // ---------- kudos: a large thumbs up, then who gave them (Strava names people First L.) ----------

          Column {
            visible: !!win.activity && win.activity.kudos > 0
            width: parent.width
            spacing: 8

            Text {
              text: "Kudos"
              color: th.fg
              font.family: th.fontName
              font.pixelSize: 15
              font.bold: true
            }

            Row {
              spacing: 12

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: win.glyph(0xF0513)
                color: th.fg
                font.family: th.fontName
                font.pixelSize: 40
              }

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: win.activity ? String(win.activity.kudos) : ""
                color: th.fg
                font.family: th.fontName
                font.pixelSize: 26
                font.bold: true
              }
            }

            Grid {
              id: kudosGrid
              columns: 3
              columnSpacing: 12
              rowSpacing: 3
              width: parent.width

              Repeater {
                model: win.kudoers

                Text {
                  required property var modelData
                  width: (kudosGrid.width - 24) / 3
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                  text: modelData
                  color: th.fg
                  font.family: th.fontName
                  font.pixelSize: 13
                }
              }
            }

            Text {
              visible: !!win.activity && win.activity.kudos > win.kudoers.length
              text: "and " + (win.activity ? win.activity.kudos - win.kudoers.length : 0) + " more that Strava does not list"
              color: th.dim
              font.family: th.fontName
              font.pixelSize: 12
            }
          }

          Text {
            visible: !!win.activity && win.records.length === 0 && win.activity.kudos === 0 && win.activity.comments === 0
            text: "No records, kudos or comments on this activity yet."
            color: th.dim
            font.family: th.fontName
            font.pixelSize: 13
          }
        }
      }
    }

    // ---------- Strava's credit, below the window's content ----------

    Item {
      id: footer
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: 24

      Text {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: "LapBar is not affiliated with or endorsed by Strava."
        color: th.dim
        font.family: th.fontName
        font.pixelSize: 12
      }

      Image {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: win.assetsDir !== ""
        source: win.assetsDir === "" ? "" : "file://" + win.assetsDir + "/strava/api_logo_pwrdBy_strava_horiz_" + (win.dark ? "white" : "black") + ".svg"
        sourceSize.height: 48
        height: 16
        fillMode: Image.PreserveAspectFit
        horizontalAlignment: Image.AlignRight
      }
    }
  }
}
