import QtQuick
import qs.Commons

// The colours and font every OmaPass component needs, in one object, so a
// component takes `theme` rather than four look-alike properties.
//
// Named Theme, not Palette: Qt 6 already has a Palette type, and a property
// typed against it silently refuses a QtObject and stays null.
//
// Defaults come from the Omarchy theme, which is what lets each component be
// instantiated on its own by a test or a preview without a bar to inherit from.
QtObject {
  id: root

  property QtObject bar: null

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: bar ? Qt.darker(bar.foreground, 1.45) : Color.muted
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent
  readonly property color background: Color.background
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
}
