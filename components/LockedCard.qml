import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// The whole popup while the vault is locked.
//
// Centred on the card rather than inside its own column: this column sits at
// its implicit width inside a wider parent, so centring its children alone put
// everything in the left half of the card.
ColumnLayout {
  id: root

  required property Theme theme
  property string account: ""

  signal unlockRequested()
  signal closeRequested()

  // Focus target for the panel while locked, so Escape has somewhere to land.
  property alias unlockButton: unlockBtn

  Layout.fillWidth: true
  Layout.alignment: Qt.AlignHCenter
  Layout.maximumWidth: parent ? parent.width : 0
  spacing: Style.space(14)

  Keys.onEscapePressed: root.closeRequested()
  Keys.onPressed: function(event) {
    if ((event.modifiers & Qt.ControlModifier)
        && (event.key === Qt.Key_BracketLeft || event.key === Qt.Key_B)) {
      root.closeRequested()
      event.accepted = true
    }
  }

  Item { Layout.preferredHeight: Style.space(8) }

  Rectangle {
    Layout.alignment: Qt.AlignHCenter
    width: Style.space(56)
    height: Style.space(56)
    radius: Style.space(28)
    color: Qt.rgba(root.theme.accent.r, root.theme.accent.g, root.theme.accent.b, 0.12)
    border.color: Qt.rgba(root.theme.accent.r, root.theme.accent.g, root.theme.accent.b, 0.3)
    border.width: 1

    Text {
      anchors.centerIn: parent
      text: ""
      font.family: root.theme.fontFamily
      font.pixelSize: Style.space(22)
      color: root.theme.accent
    }
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.space(4)

    Text {
      Layout.fillWidth: true
      horizontalAlignment: Text.AlignHCenter
      text: "1Password Vault Locked"
      font.family: root.theme.fontFamily
      font.pixelSize: Style.font.heading
      font.bold: true
      color: root.theme.foreground
      elide: Text.ElideRight
    }

    Text {
      Layout.fillWidth: true
      horizontalAlignment: Text.AlignHCenter
      text: root.account ? root.account : "Unlock with fingerprint or master password"
      font.family: root.theme.fontFamily
      font.pixelSize: Style.font.caption
      color: root.theme.dim
      wrapMode: Text.Wrap
      elide: Text.ElideRight
      maximumLineCount: 2
    }
  }

  Item { Layout.preferredHeight: Style.space(4) }

  Button {
    id: unlockBtn
    Layout.alignment: Qt.AlignHCenter
    text: "Unlock Vault"
    iconText: ""
    accent: root.theme.accent
    selected: true
    bordered: true
    focusable: true
    horizontalPadding: Style.space(16)
    verticalPadding: Style.space(8)
    onClicked: root.unlockRequested()
    Keys.onReturnPressed: root.unlockRequested()
    Keys.onEnterPressed: root.unlockRequested()
  }

  Item { Layout.preferredHeight: Style.space(8) }
}
