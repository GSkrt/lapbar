import QtQuick
import Quickshell
import Quickshell.Io
import "logic.js" as Logic

// LapBar's how-to window: the guide in docs/help.md, drawn in the app, pictures included. Qt Quick can render
// Markdown by itself (Text.MarkdownText), so nothing needs installing. Pictures are pulled out of the text by
// logic.js and shown in between, scaled to fit.
//
// Run by `lapbar howto` as its own Quickshell process (LAPBAR_HELP_FILE, LAPBAR_DOCS and LAPBAR_ASSETS are paths).
FloatingWindow {
  id: win

  title: "LapBar · How to use"
  implicitWidth: 980
  implicitHeight: 820
  minimumSize: Qt.size(560, 420)
  color: th.surface
  visible: true
  onClosed: Qt.quit()

  Theme { id: th }

  readonly property string docsDir: Quickshell.env("LAPBAR_DOCS") || ""
  readonly property string assetsDir: Quickshell.env("LAPBAR_ASSETS") || ""
  readonly property bool dark: Qt.color(th.surface).hslLightness < 0.5

  FileView {
    id: guide
    path: Quickshell.env("LAPBAR_HELP_FILE")
    blockLoading: true
  }

  readonly property var blocks: Logic.splitMarkdown(guide.text())

  Item {
    id: content
    anchors.fill: parent
    anchors.margins: 20
    focus: true
    Keys.onEscapePressed: Qt.quit()
    Keys.onPressed: function(event) {
      var top = 0
      var bottom = Math.max(0, scroll.contentHeight - scroll.height)
      var page = scroll.height * 0.85
      var y = scroll.contentY
      if (event.key === Qt.Key_PageDown || event.key === Qt.Key_Space) y += page
      else if (event.key === Qt.Key_PageUp) y -= page
      else if (event.key === Qt.Key_Down) y += 60
      else if (event.key === Qt.Key_Up) y -= 60
      else if (event.key === Qt.Key_Home) y = top
      else if (event.key === Qt.Key_End) y = bottom
      else return
      scroll.contentY = Math.max(top, Math.min(bottom, y))
      event.accepted = true
    }

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
        text: "How to use  ·  Esc to close"
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
      anchors.bottom: parent.bottom
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
          x: 28
          y: 24
          width: Math.min(scroll.width - 56, 860)
          spacing: 14

          Repeater {
            model: win.blocks

            Item {
              id: block
              required property var modelData
              readonly property bool isImage: modelData.type === "image"
              width: column.width
              height: isImage ? picture.height + 6 + (caption.visible ? caption.implicitHeight : 0) : words.implicitHeight

              Text {
                id: words
                visible: !block.isImage
                width: parent.width
                textFormat: Text.MarkdownText
                wrapMode: Text.WordWrap
                text: block.isImage ? "" : block.modelData.text
                color: th.fg
                font.family: th.fontName
                font.pixelSize: 14
              }

              Image {
                id: picture
                visible: block.isImage
                anchors.horizontalCenter: parent.horizontalCenter
                source: block.isImage ? Logic.imageSource(win.docsDir, block.modelData.src) : ""
                asynchronous: true
                smooth: true
                fillMode: Image.PreserveAspectFit
                // portrait pictures (the popup) are shown narrower so they do not run off the screen
                readonly property real maxWidth: implicitHeight > implicitWidth * 1.2 ? Math.min(column.width, 520) : column.width
                width: implicitWidth > 0 ? Math.min(implicitWidth, maxWidth) : 0
                height: implicitWidth > 0 ? width * implicitHeight / implicitWidth : 0
              }

              Text {
                id: caption
                visible: block.isImage && block.modelData.alt !== ""
                anchors.top: picture.bottom
                anchors.topMargin: 6
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: block.isImage ? block.modelData.alt : ""
                color: th.dim
                font.family: th.fontName
                font.pixelSize: 12
              }
            }
          }
        }
      }
    }
  }
}
