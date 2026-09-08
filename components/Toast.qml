import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// One-line confirmation of the last action, shown at the popup's root so a
// copy made from the list is confirmed too. Nested inside the details view it
// was invisible for every action taken on the list.
BorderSurface {
  id: root

  required property Theme theme
  property string message: ""

  visible: message.length > 0
  Layout.fillWidth: true
  Layout.preferredHeight: Style.space(28)
  radius: Style.cornerRadius
  color: Qt.rgba(theme.accent.r, theme.accent.g, theme.accent.b, 0.18)
  borderSpec: Border.controlSpec("normal", theme.foreground, theme.accent)

  RowLayout {
    anchors.fill: parent
    anchors.leftMargin: Style.space(10)
    anchors.rightMargin: Style.space(10)
    spacing: Style.space(6)

    Text {
      text: "\u{f012c}"
      font.family: root.theme.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      color: root.theme.accent
    }

    Text {
      Layout.fillWidth: true
      text: root.message
      font.family: root.theme.fontFamily
      font.pixelSize: Style.font.caption
      color: root.theme.foreground
      elide: Text.ElideRight
    }
  }
}
