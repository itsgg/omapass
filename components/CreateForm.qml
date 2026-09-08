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
  property bool vaultsLoading: false
  // Set while editing an existing item, which changes the wording and stops
  // the category chooser: an item's category cannot be changed after the fact.
  property string editingId: ""
  readonly property bool editing: editingId.length > 0

  signal vaultsRequested()
  property string submitError: ""

  // Field id to value, and field id to message.
  property var values: ({})
  property var errors: ({})
  // What the item held when the form opened. An edit sends the difference
  // against this, never the whole form.
  property var originalValues: ({})
  property bool generatePassword: true

  signal cancelled()
  signal submitted(var payload)

  readonly property var spec: Model.createSpec(root.category)
  readonly property string generateField: Model.generatedFieldFor(root.category)
  readonly property bool canGenerate: root.generateField.length > 0
  property alias firstField: fieldRepeater

  function selectCategory(id) {
    // Inert while a save is in flight. The save carries the values as they
    // were when it was submitted, so anything changed under it would be
    // discarded when it lands, with nothing on screen to say so.
    if (root.busy) return
    if (id === root.category) return
    var keepTitle = root.values.title || ""
    root.category = id
    root.reset(keepTitle)
    Qt.callLater(function() { root.focusFirst() })
  }

  // Fills the form from an existing item.
  function loadValues(values) {
    var original = {}
    for (var k in values) original[k] = values[k]
    root.originalValues = original
    root.values = values
    root.errors = ({})
    root.generatePassword = false
    root.submitError = ""
  }

  function reset(title) {
    var v = {}
    for (var i = 0; i < root.spec.fields.length; i++) v[root.spec.fields[i].id] = ""
    // Searching for something that is not there and then creating it is the
    // common path, so the query becomes the title.
    if (title) v.title = title
    root.values = v
    root.originalValues = ({})
    root.errors = ({})
    root.generatePassword = root.canGenerate
    root.submitError = ""
  }

  // Both maps are replaced, never mutated in place: assigning the same object
  // reference back to a QML property notifies nothing, so the field would keep
  // showing an error the user had already corrected.
  function setValue(id, value) {
    if (root.busy) return
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
    if (Model.urlClearedByEdit(root.values, root.originalValues, root.editing)) {
      var urlErr = {}
      urlErr.url = "Removing a website needs the 1Password app"
      root.errors = urlErr
      return
    }

    var generating = root.canGenerate && root.generatePassword
    var payloadFields = Model.submitFields(
      root.spec, root.values, root.originalValues,
      generating ? root.generateField : "", root.editing)
    root.submitted({
      category: root.category,
      title: Model.submitText(root.values, root.originalValues, "title", root.editing),
      url: Model.submitText(root.values, root.originalValues, "url", root.editing),
      vault: root.vault,
      // What to call the item in a toast. `title` above is empty when the
      // title did not change, which is how the helper is told to leave it be.
      displayTitle: root.values.title || "",
      generateField: root.canGenerate && root.generatePassword ? root.generateField : "",
      fields: payloadFields
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

      // Category chooser. Which kind of item this is changes the whole form,
      // so it comes first and reads as a choice rather than a setting.
      RowLayout {
        visible: !root.editing
        enabled: !root.busy
        opacity: root.busy ? 0.55 : 1.0
        Layout.fillWidth: true
        spacing: Style.space(4)

        Repeater {
          model: Model.createCategories()
          delegate: Rectangle {
            required property var modelData
            readonly property bool isSelected: root.category === modelData.id

            Layout.preferredHeight: Style.space(26)
            Layout.preferredWidth: catRow.implicitWidth + Style.space(16)
            radius: Style.cornerRadius
            color: isSelected
              ? Qt.rgba(root.theme.accent.r, root.theme.accent.g, root.theme.accent.b, 0.22)
              : (catMouse.containsMouse
                  ? Qt.rgba(root.theme.foreground.r, root.theme.foreground.g, root.theme.foreground.b, 0.08)
                  : "transparent")
            border.color: isSelected ? root.theme.accent : "transparent"
            border.width: 1

            Row {
              id: catRow
              anchors.centerIn: parent
              spacing: Style.space(5)

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.icon
                font.family: root.theme.fontFamily
                font.pixelSize: Style.font.caption
                color: isSelected ? root.theme.accent : root.theme.dim
              }

              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.label
                font.family: root.theme.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: isSelected
                color: isSelected ? root.theme.accent : root.theme.foreground
              }
            }

            MouseArea {
              id: catMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.selectCategory(modelData.id)
            }
          }
        }
      }

      Repeater {
        id: fieldRepeater
        model: root.spec.fields
        delegate: FormField {
          required property var modelData
          required property int index

          theme: root.theme
          spec: modelData
          readOnly: root.busy
          value: root.values[modelData.id] || ""
          error: root.errors[modelData.id] || ""
          generated: root.generatePassword
          generatable: root.canGenerate && modelData.id === root.generateField
          onEdited: function(v) { root.setValue(modelData.id, v) }
          onGenerateToggled: function(g) {
            root.generatePassword = g
            if (g) root.setValue("password", "")
          }
          onSubmitted: root.submit()
        }
      }

      // Vault picker. Which vault a credential lands in is worth deciding
      // explicitly, so this is always shown when creating: hiding it when the
      // list could not be fetched left the user with no picker and no reason
      // why. Editing cannot move an item between vaults, so it is not offered.
      ColumnLayout {
        visible: !root.editing
        enabled: !root.busy
        opacity: root.busy ? 0.55 : 1.0
        Layout.fillWidth: true
        spacing: Style.space(3)

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)

          Text {
            text: "VAULT"
            font.family: root.theme.fontFamily
            font.pixelSize: Style.font.caption - 1
            font.bold: true
            color: root.theme.dim
          }

          Item { Layout.fillWidth: true }

          Text {
            visible: root.vaults.length === 0
            text: root.vaultsLoading ? "loading..." : "using your default vault"
            font.family: root.theme.fontFamily
            font.pixelSize: Style.font.caption - 1
            color: root.theme.dim
          }

          Button {
            visible: root.vaults.length === 0 && !root.vaultsLoading
            text: "Retry"
            accent: root.theme.accent
            horizontalPadding: Style.space(8)
            verticalPadding: Style.space(2)
            onClicked: root.vaultsRequested()
          }
        }

        Dropdown {
          visible: root.vaults.length > 0
          Layout.fillWidth: true
          options: root.vaults.map(function(v) { return v.name })
          value: root.vault
          foreground: root.theme.foreground
          accent: root.theme.accent
          fontFamily: root.theme.fontFamily
          onValueChanged: root.vault = value
        }

        // Stands in when the list is unavailable, so the row keeps its shape
        // and the form still explains where the item will go.
        BorderSurface {
          visible: root.vaults.length === 0
          Layout.fillWidth: true
          Layout.preferredHeight: Style.spacing.controlHeight
          radius: Style.cornerRadius
          color: Qt.rgba(root.theme.foreground.r, root.theme.foreground.g, root.theme.foreground.b, 0.04)
          borderSpec: Border.controlSpec("normal", root.theme.foreground, root.theme.accent)

          Text {
            anchors.left: parent.left
            anchors.leftMargin: Style.spacing.controlPaddingX
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - Style.spacing.controlPaddingX * 2
            text: root.vaultsLoading
              ? "Reading your vaults..."
              : "1Password will use your default vault"
            font.family: root.theme.fontFamily
            font.pixelSize: Style.font.body
            color: root.theme.dim
            elide: Text.ElideRight
          }
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
      text: root.busy
        ? "Saving..."
        : (root.editing ? "Save changes" : ("Create " + root.spec.label.toLowerCase()))
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
