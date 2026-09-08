import QtQuick
import QtQuick.Layouts
import qs.Commons

// What the list shows when it has nothing to show. Three cases the user can
// tell apart: still loading, a query that matched nothing, and an empty vault.
ColumnLayout {
  id: root

  required property Theme theme
  property bool loading: false
  property string query: ""

  spacing: Style.space(6)

  Text {
    Layout.alignment: Qt.AlignHCenter
    text: root.loading ? "\u{f0450}" : "\u{f0349}"
    font.family: root.theme.fontFamily
    font.pixelSize: Style.space(32)
    color: root.loading ? root.theme.accent : root.theme.dim
  }

  Text {
    Layout.alignment: Qt.AlignHCenter
    text: root.loading
      ? "Loading vault..."
      : (root.query ? "No matching items found" : "No items in vault")
    font.family: root.theme.fontFamily
    font.pixelSize: Style.font.body
    color: root.theme.foreground
  }

  Text {
    visible: !root.loading && root.query.length > 0
    Layout.alignment: Qt.AlignHCenter
    text: "Try a different search query or category"
    font.family: root.theme.fontFamily
    font.pixelSize: Style.font.caption
    color: root.theme.dim
  }
}
