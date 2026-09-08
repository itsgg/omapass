import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// The create view: a form built from Model.createSpec(), not from hand-written
// rows. Owns its own values and validation and reports one thing upward, so
// the widget does not grow a second state machine.
ColumnLayout {
  id: root

  required property Theme theme
  property string category: "LOGIN"
  property var vaults: []
  property string vault: ""
  property bool busy: false
  property string submitError: ""

  // Field id to value, and field id to message.
  property var values: ({})
  property var errors: ({})
  property bool generatePassword: true

  signal cancelled()
  signal submitted(var payload)

  readonly property var spec: Model.createSpec(root.category)
  property alias firstField: fieldRepeater

  function reset(title) {
    var v = {}
    for (var i = 0; i < root.spec.fields.length; i++) v[root.spec.fields[i].id] = ""
    // Searching for something that is not there and then creating it is the
    // common path, so the query becomes the title.
    if (title) v.title = title
    root.values = v
    root.errors = ({})
    root.generatePassword = true
    root.submitError = ""
  }

  // Both maps are replaced, never mutated in place: assigning the same object
  // reference back to a QML property notifies nothing, so the field would keep
  // showing an error the user had already corrected.
  function setValue(id, value) {
    var next = {}
    for (var k in root.values) next[k] = root.values[k]
    next[id] = value
    root.values = next

    if (root.errors[id]) {
      var e = {}
      for (var k2 in root.errors) { if (k2 !== id) e[k2] = root.errors[k2] }
      root.errors = e
    }
  }

  function focusFirst() {
    if (fieldRepeater.count > 0) {
      var f = fieldRepeater.itemAt(0)
      if (f && f.input) f.input.forceActiveFocus()
    }
  }

  function submit() {
    if (root.busy) return
    var errs = Model.validateCreate(root.spec, root.values)
    root.errors = errs
    if (Model.hasErrors(errs)) {
      // Put the caret on the first thing that is wrong.
      for (var i = 0; i < root.spec.fields.length; i++) {
        if (errs[root.spec.fields[i].id]) {
          var f = fieldRepeater.itemAt(i)
          if (f && f.input) f.input.forceActiveFocus()
          break
        }
      }
      return
    }
    root.submitted({
      title: root.values.title || "",
      username: root.values.username || "",
      url: root.values.url || "",
      vault: root.vault,
      password: root.generatePassword ? "" : (root.values.password || "")
    })
  }

  Layout.fillWidth: true
  Layout.fillHeight: true
  spacing: Style.space(10)

  Keys.onEscapePressed: root.cancelled()
  Keys.onPressed: function(event) {
    if ((event.modifiers & Qt.ControlModifier)) {
      if (event.key === Qt.Key_BracketLeft || event.key === Qt.Key_B) {
        root.cancelled()
        event.accepted = true
      } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
        root.submit()
        event.accepted = true
      }
    }
  }

  Flickable {
    Layout.fillWidth: true
    Layout.fillHeight: true
    contentWidth: width
    contentHeight: formColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds

    ColumnLayout {
      id: formColumn
      width: parent.width
      spacing: Style.space(10)

      Repeater {
        id: fieldRepeater
        model: root.spec.fields
        delegate: FormField {
          required property var modelData
          required property int index

          theme: root.theme
          spec: modelData
          value: root.values[modelData.id] || ""
          error: root.errors[modelData.id] || ""
          generated: root.generatePassword
          onEdited: function(v) { root.setValue(modelData.id, v) }
          onGenerateToggled: function(g) {
            root.generatePassword = g
            if (g) root.setValue("password", "")
          }
          onSubmitted: root.submit()
        }
      }

      // Vault picker. Which vault a credential lands in is a decision worth
      // making explicitly, not defaulting silently.
      ColumnLayout {
        visible: root.vaults.length > 0
        Layout.fillWidth: true
        spacing: Style.space(3)

        Text {
          text: "VAULT"
          font.family: root.theme.fontFamily
          font.pixelSize: Style.font.caption - 1
          font.bold: true
          color: root.theme.dim
        }

        Dropdown {
          Layout.fillWidth: true
          options: root.vaults.map(function(v) { return v.name })
          value: root.vault
          foreground: root.theme.foreground
          accent: root.theme.accent
          fontFamily: root.theme.fontFamily
          onValueChanged: root.vault = value
        }
      }

      Text {
        visible: root.submitError.length > 0
        Layout.fillWidth: true
        text: root.submitError
        font.family: root.theme.fontFamily
        font.pixelSize: Style.font.caption
        color: root.theme.urgent
        wrapMode: Text.Wrap
      }
    }
  }

  Rectangle {
    Layout.fillWidth: true
    Layout.preferredHeight: 1
    color: Qt.rgba(root.theme.foreground.r, root.theme.foreground.g, root.theme.foreground.b, 0.1)
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: Style.space(6)

    Button {
      text: "Cancel"
      iconText: "\u{f0156}"
      accent: root.theme.accent
      horizontalPadding: Style.space(10)
      verticalPadding: Style.space(4)
      onClicked: root.cancelled()
    }

    Item { Layout.fillWidth: true }

    Text {
      text: "⌃↵ to save"
      font.family: root.theme.fontFamily
      font.pixelSize: Style.font.caption
      color: root.theme.dim
    }

    Button {
      text: root.busy ? "Saving..." : "Create login"
      iconText: "\u{f0306}"
      accent: root.theme.accent
      selected: true
      bordered: true
      horizontalPadding: Style.space(12)
      verticalPadding: Style.space(4)
      onClicked: root.submit()
    }
  }
}
