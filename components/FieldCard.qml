import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// One credential row in the details view.
//
// Keyboard focus and mouse hover paint the same state, and reveal follows the
// focused row, so a revealed password re-conceals the moment focus moves off
// it. The card never acts on the value itself: it names the field and asks the
// widget, which is what keeps the plaintext out of any command line.
BorderSurface {
  id: root

  required property Theme theme
  required property var modelData
  required property int index

  // Which row the keyboard is on, and whether that row is revealed.
  required property int focusedIndex
  required property bool revealed

  signal focusRequested(int index)
  signal toggleRevealRequested()
  signal copyRequested(string fieldId, string label)
  signal typeRequested(string fieldId, string label)
  signal visibilityRequested(real y, real h)

  readonly property bool focused: root.focusedIndex === root.index
  readonly property bool hot: focused || fieldHover.hovered
  readonly property bool showingValue: !(modelData.concealed && !root.revealed)

  onFocusedChanged: if (focused) root.visibilityRequested(y, height)

  // Any field with no content is not a row worth showing.
  visible: !!(modelData && modelData.value && String(modelData.value).trim().length > 0)
  Layout.fillWidth: true
  Layout.preferredHeight: visible ? Style.space(52) : 0
  radius: Style.cornerRadius
  color: hot
    ? Qt.rgba(theme.foreground.r, theme.foreground.g, theme.foreground.b, 0.08)
    : Qt.rgba(theme.foreground.r, theme.foreground.g, theme.foreground.b, 0.04)
  borderSpec: Border.controlSpec(
    focused ? "focus" : (fieldHover.hovered ? "hover-cursor" : "normal"),
    theme.foreground, theme.accent)

  HoverHandler { id: fieldHover }
  TapHandler { onTapped: root.focusRequested(root.index) }

  RowLayout {
    anchors.fill: parent
    anchors.leftMargin: Style.space(10)
    anchors.rightMargin: Style.space(8)
    spacing: Style.space(8)

    Text {
      text: Model.fieldIcon(root.modelData)
      font.family: root.theme.fontFamily
      font.pixelSize: Style.space(16)
      color: root.theme.accent
    }

    ColumnLayout {
      Layout.fillWidth: true
      spacing: 1

      Text {
        text: Model.fieldDisplayName(root.modelData).toUpperCase()
        font.family: root.theme.fontFamily
        font.pixelSize: Style.font.caption - 1
        font.bold: true
        color: root.theme.dim
        elide: Text.ElideRight
      }

      Text {
        Layout.fillWidth: true
        // Masked with a fixed run of dots, never one per character: matching
        // the length says how long the password is, and that a CVV is three
        // digits. Formatting is display only; a copy takes the raw value.
        text: root.showingValue
          ? Model.displayValue(root.modelData)
          : Model.maskText(root.modelData.value)
        // The shell's own family, never a hardcoded one: Omarchy aliases it
        // through fontconfig so `omarchy font set` moves every widget at
        // once, and naming a family here would leave these values behind.
        font.family: root.theme.fontFamily
        font.pixelSize: Style.font.body
        color: root.theme.foreground
        elide: Text.ElideRight
      }
    }

    RowLayout {
      spacing: Style.space(4)

      Button {
        visible: !!root.modelData.concealed
        iconText: root.revealed ? "\u{f0209}" : "\u{f0208}"
        tooltipText: root.revealed ? "Conceal (r)" : "Reveal (r)"
        accent: root.theme.accent
        horizontalPadding: Style.space(6)
        verticalPadding: Style.space(4)
        onClicked: {
          root.focusRequested(root.index)
          root.toggleRevealRequested()
        }
      }

      Button {
        visible: !!root.modelData.value
        iconText: "\u{f030c}"
        tooltipText: "Auto-type into active window (t)"
        accent: root.theme.accent
        horizontalPadding: Style.space(6)
        verticalPadding: Style.space(4)
        onClicked: root.typeRequested(root.modelData.id, Model.fieldDisplayName(root.modelData))
      }

      Button {
        visible: !!root.modelData.value
        iconText: "\u{f018f}"
        tooltipText: "Copy " + Model.fieldDisplayName(root.modelData) + " (c)"
        accent: root.theme.accent
        horizontalPadding: Style.space(6)
        verticalPadding: Style.space(4)
        onClicked: root.copyRequested(root.modelData.id, Model.fieldDisplayName(root.modelData))
      }
    }
  }
}
