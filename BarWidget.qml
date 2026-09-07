import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

BarWidget {
  id: root
  moduleName: "gg.omapass"

  // Open / Close lifecycle
  property bool popupOpen: false
  function close() { popupOpen = false }
  function open() {
    popupOpen = true
    checkStatus()
    if (unlocked) refreshItems()
  }
  function toggle() { popupOpen ? close() : open() }
  readonly property bool opened: popupOpen

  // Settings from shell.json configuration
  readonly property int clipboardTimeout: setting("clipboardTimeout", 30)
  readonly property string defaultAction: setting("defaultAction", "password")

  // Vault state
  property bool installed: true
  property bool unlocked: false
  property string account: ""
  property int itemCount: 0
  property bool busy: false
  property string lastRunAction: ""

  // Search & Navigation
  property string searchQuery: ""
  property string selectedCategory: "ALL"
  property int selectedIndex: 0
  property var items: []

  // Absolute path to helper script
  readonly property string helperPath: Qt.resolvedUrl("omapass-agent.py").toString().replace(/^file:\/\//, "")

  readonly property color colForeground: bar ? bar.foreground : Color.foreground
  readonly property color colDim: bar ? Qt.darker(bar.foreground, 1.45) : Color.dim
  readonly property color colAccent: Color.accent
  readonly property color colSurface: Color.popups.background
  readonly property color colBorder: Color.popups.border
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Status check process
  Process {
    id: statusProc
    running: false
    command: ["python3", root.helperPath, "request", JSON.stringify({ action: "status" })]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.busy = false
        try {
          var resp = JSON.parse(text)
          root.installed = resp.installed !== false
          var wasUnlocked = root.unlocked
          root.unlocked = !!resp.unlocked
          root.account = resp.account || ""
          if (resp.itemCount !== undefined) root.itemCount = resp.itemCount
          if (!wasUnlocked && root.unlocked && root.popupOpen) {
            root.refreshItems()
          }
        } catch (e) {}
      }
    }
  }

  function checkStatus() {
    if (!statusProc.running) {
      statusProc.running = true
    }
  }

  // Items search process
  Process {
    id: searchProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.busy = false
        try {
          var resp = JSON.parse(text)
          if (resp.ok && resp.items) {
            root.items = resp.items
            if (resp.totalItems !== undefined) root.itemCount = resp.totalItems
            if (root.selectedIndex >= root.items.length) {
              root.selectedIndex = Math.max(0, root.items.length - 1)
            }
          }
        } catch (e) {}
      }
    }
  }

  function refreshItems() {
    if (searchProc.running) return
    root.busy = true
    searchProc.command = [
      "python3",
      root.helperPath,
      "request",
      JSON.stringify({
        action: "list",
        query: root.searchQuery,
        category: root.selectedCategory,
        limit: 60
      })
    ]
    searchProc.running = true
  }

  // Generic action runner
  Process {
    id: actionProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.busy = false
        var completedAction = root.lastRunAction
        root.lastRunAction = ""
        checkStatus()
        // If sync finished, reload the fresh items
        if (completedAction === "sync") {
          root.refreshItems()
        }
      }
    }
  }

  function runAction(payload) {
    if (actionProc.running) return
    root.busy = true
    root.lastRunAction = payload.action || ""
    actionProc.command = [
      "python3",
      root.helperPath,
      "request",
      JSON.stringify(payload)
    ]
    actionProc.running = true
  }

  function unlock() {
    runAction({ action: "unlock" })
    checkTimer.restart()
  }

  function lockVault() {
    runAction({ action: "lock" })
    root.unlocked = false
    root.items = []
  }

  function syncVault() {
    runAction({ action: "sync" })
  }

  function copyField(itemId, field, title) {
    runAction({
      action: "copy",
      id: itemId,
      field: field || root.defaultAction,
      title: title || "",
      timeout: root.clipboardTimeout
    })
    root.close()
  }

  function openUrl(url) {
    if (!url) return
    runAction({ action: "open_url", url: url })
    root.close()
  }

  onSearchQueryChanged: {
    selectedIndex = 0
    searchTimer.restart()
  }

  onSelectedCategoryChanged: {
    selectedIndex = 0
    refreshItems()
  }

  Timer {
    id: searchTimer
    interval: 150
    repeat: false
    onTriggered: root.refreshItems()
  }

  // Periodic status poll (slow when closed, faster when open)
  Timer {
    id: checkTimer
    interval: root.popupOpen ? 2500 : 8000
    running: true
    repeat: true
    onTriggered: root.checkStatus()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---------------------------------------------------------------- Status Bar Button
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.unlocked ? "󰌆" : "󰌏"
    active: root.unlocked
    horizontalMargin: 7.5
    tooltipText: !root.installed
      ? "1Password — CLI (op) not found"
      : root.unlocked
        ? ("1Password — Unlocked" + (root.itemCount > 0 ? " (" + root.itemCount + " items)" : ""))
        : "1Password — Locked (Click to open)"

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.RightButton) {
        if (root.unlocked) root.lockVault()
        else root.unlock()
      } else {
        root.toggle()
      }
    }
  }

  // ---------------------------------------------------------------- Popup Card
  PopupCard {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.popupOpen
    focusTarget: root.unlocked ? searchInput : unlockBtn
    contentWidth: popup.fittedContentWidth(Style.space(440))
    contentHeight: popup.cappedContentHeight(Style.space(520))

    ColumnLayout {
      anchors.fill: parent
      spacing: Style.space(8)

      // ========================================== HEADER
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)

        Text {
          text: "󰌆 1Password"
          font.family: root.fontFamily
          color: root.colForeground
          font.pixelSize: Style.font.titleSmall
          font.bold: true
        }

        Rectangle {
          visible: root.unlocked && root.itemCount > 0
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.2)
          radius: Style.space(8)
          Layout.preferredHeight: Style.space(18)
          Layout.preferredWidth: countText.implicitWidth + Style.space(12)

          Text {
            id: countText
            anchors.centerIn: parent
            text: String(root.itemCount)
            color: root.colAccent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
        }

        Item { Layout.fillWidth: true }

        // Sync button
        Rectangle {
          visible: root.unlocked
          width: Style.space(26)
          height: Style.space(26)
          radius: Style.space(6)
          color: syncMouse.containsMouse ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1) : "transparent"

          Text {
            anchors.centerIn: parent
            text: "󰑐"
            color: root.busy ? root.colAccent : root.colDim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          MouseArea {
            id: syncMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.syncVault()
          }
        }

        // Lock / Unlock toggle button
        Rectangle {
          visible: root.unlocked
          width: Style.space(26)
          height: Style.space(26)
          radius: Style.space(6)
          color: lockMouse.containsMouse ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1) : "transparent"

          Text {
            anchors.centerIn: parent
            text: "󰌏"
            color: root.colDim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          MouseArea {
            id: lockMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.lockVault()
          }
        }
      }

      // ========================================== LOCKED STATE
      ColumnLayout {
        visible: !root.unlocked
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(12)

        Item { Layout.fillHeight: true }

        Text {
          Layout.alignment: Qt.AlignHCenter
          text: "󰌏"
          font.family: root.fontFamily
          font.pixelSize: Style.space(48)
          color: root.colDim
        }

        Text {
          Layout.alignment: Qt.AlignHCenter
          text: "1Password Vault Locked"
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          color: root.colForeground
        }

        Text {
          Layout.alignment: Qt.AlignHCenter
          text: root.account ? root.account : "Unlock with fingerprint or master password"
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          color: root.colDim
        }

        Item { Layout.preferredHeight: Style.space(8) }

        Rectangle {
          id: unlockBtn
          Layout.alignment: Qt.AlignHCenter
          Layout.preferredWidth: Style.space(160)
          Layout.preferredHeight: Style.space(36)
          radius: Style.space(8)
          color: unlockMouse.containsMouse ? Qt.darker(root.colAccent, 1.15) : root.colAccent

          Row {
            anchors.centerIn: parent
            spacing: Style.space(6)
            Text {
              text: "󰌆"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: Color.background
              anchors.verticalCenter: parent.verticalCenter
            }
            Text {
              text: "Unlock Vault"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              color: Color.background
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          MouseArea {
            id: unlockMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.unlock()
          }

          Keys.onReturnPressed: root.unlock()
          Keys.onEnterPressed: root.unlock()
        }

        Item { Layout.fillHeight: true }
      }

      // ========================================== UNLOCKED STATE
      ColumnLayout {
        visible: root.unlocked
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(8)

        // ------------------ SEARCH FIELD
        Rectangle {
          Layout.fillWidth: true
          Layout.preferredHeight: Style.space(34)
          radius: Style.space(6)
          color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.06)
          border.color: searchInput.activeFocus ? root.colAccent : root.colBorder
          border.width: searchInput.activeFocus ? 1.5 : 1

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(8)
            anchors.rightMargin: Style.space(8)
            spacing: Style.space(6)

            Text {
              text: "󰍉"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: searchInput.activeFocus ? root.colAccent : root.colDim
            }

            TextInput {
              id: searchInput
              Layout.fillWidth: true
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: root.colForeground
              selectByMouse: true
              text: root.searchQuery
              onTextChanged: root.searchQuery = text

              Text {
                text: "Search 1Password..."
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.colDim
                visible: !searchInput.text && !searchInput.inputMethodComposing
              }

              Keys.onEscapePressed: root.close()
              Keys.onDownPressed: {
                if (root.items.length > 0) {
                  root.selectedIndex = (root.selectedIndex + 1) % root.items.length
                  itemList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
                }
              }
              Keys.onUpPressed: {
                if (root.items.length > 0) {
                  root.selectedIndex = (root.selectedIndex - 1 + root.items.length) % root.items.length
                  itemList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
                }
              }
              Keys.onReturnPressed: function(event) {
                if (root.items.length === 0) return
                var item = root.items[root.selectedIndex]
                if (!item) return

                if (event.modifiers & Qt.ShiftModifier) {
                  root.copyField(item.id, "username", item.title)
                } else if (event.modifiers & Qt.ControlModifier) {
                  root.copyField(item.id, "otp", item.title)
                } else if (event.modifiers & Qt.AltModifier) {
                  root.openUrl(item.url)
                } else {
                  root.copyField(item.id, root.defaultAction, item.title)
                }
              }
            }

            Text {
              visible: searchInput.text.length > 0
              text: "󰅖"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: root.colDim

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  searchInput.text = ""
                  searchInput.forceActiveFocus()
                }
              }
            }
          }
        }

        // ------------------ CATEGORY FILTER CHIPS
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)

          Repeater {
            model: Model.CATEGORIES
            delegate: Rectangle {
              required property var modelData
              required property int index

              readonly property bool isSelected: root.selectedCategory === modelData.id
              Layout.preferredHeight: Style.space(22)
              Layout.preferredWidth: chipRow.implicitWidth + Style.space(12)
              radius: Style.space(11)
              color: isSelected
                ? Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.25)
                : chipMouse.containsMouse
                  ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.08)
                  : "transparent"
              border.color: isSelected ? root.colAccent : root.colBorder
              border.width: 1

              Row {
                id: chipRow
                anchors.centerIn: parent
                spacing: Style.space(4)

                Text {
                  text: modelData.icon
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  color: isSelected ? root.colAccent : root.colDim
                  anchors.verticalCenter: parent.verticalCenter
                }

                Text {
                  text: modelData.label
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: isSelected
                  color: isSelected ? root.colAccent : root.colForeground
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              MouseArea {
                id: chipMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.selectedCategory = modelData.id
                  searchInput.forceActiveFocus()
                }
              }
            }
          }
        }

        // ------------------ ITEMS LIST
        ListView {
          id: itemList
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.items
          spacing: Style.space(2)

          delegate: Rectangle {
            required property var modelData
            required property int index

            readonly property bool isSelected: root.selectedIndex === index
            width: itemList.width
            height: Style.space(42)
            radius: Style.space(6)
            color: isSelected
              ? Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.18)
              : itemMouse.containsMouse
                ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.06)
                : "transparent"

            // Row click area placed behind action buttons
            MouseArea {
              id: itemMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onEntered: root.selectedIndex = index
              onClicked: root.copyField(modelData.id, root.defaultAction, modelData.title)
            }

            RowLayout {
              anchors.fill: parent
              anchors.leftMargin: Style.space(8)
              anchors.rightMargin: Style.space(6)
              spacing: Style.space(8)

              // Category icon
              Text {
                text: Model.categoryIcon(modelData.category)
                font.family: root.fontFamily
                font.pixelSize: Style.space(16)
                color: isSelected ? root.colAccent : root.colDim
                Layout.preferredWidth: Style.space(20)
                horizontalAlignment: Text.AlignHCenter
              }

              // Title and subtitle
              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  Layout.fillWidth: true
                  text: modelData.title || "Untitled"
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: isSelected
                  color: root.colForeground
                  elide: Text.ElideRight
                }

                Text {
                  Layout.fillWidth: true
                  text: Model.subtitleText(modelData)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  color: root.colDim
                  elide: Text.ElideRight
                  visible: text.length > 0
                }
              }

              // Action buttons on top of itemMouse (higher Z)
              Row {
                z: 10
                spacing: Style.space(4)
                visible: isSelected || itemMouse.containsMouse

                // Copy Password
                Rectangle {
                  width: Style.space(24)
                  height: Style.space(24)
                  radius: Style.space(4)
                  color: pwMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)

                  Text {
                    anchors.centerIn: parent
                    text: "󰌆"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    color: pwMouse.containsMouse ? Color.background : root.colForeground
                  }

                  MouseArea {
                    id: pwMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.copyField(modelData.id, "password", modelData.title)
                  }
                }

                // Copy Username
                Rectangle {
                  width: Style.space(24)
                  height: Style.space(24)
                  radius: Style.space(4)
                  color: userMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)

                  Text {
                    anchors.centerIn: parent
                    text: "󰋽"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    color: userMouse.containsMouse ? Color.background : root.colForeground
                  }

                  MouseArea {
                    id: userMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.copyField(modelData.id, "username", modelData.title)
                  }
                }

                // Copy TOTP
                Rectangle {
                  width: Style.space(24)
                  height: Style.space(24)
                  radius: Style.space(4)
                  color: otpMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)

                  Text {
                    anchors.centerIn: parent
                    text: "󰄬"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    color: otpMouse.containsMouse ? Color.background : root.colForeground
                  }

                  MouseArea {
                    id: otpMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.copyField(modelData.id, "otp", modelData.title)
                  }
                }

                // Open URL
                Rectangle {
                  visible: !!modelData.url
                  width: Style.space(24)
                  height: Style.space(24)
                  radius: Style.space(4)
                  color: urlMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)

                  Text {
                    anchors.centerIn: parent
                    text: "󰖟"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    color: urlMouse.containsMouse ? Color.background : root.colForeground
                  }

                  MouseArea {
                    id: urlMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.openUrl(modelData.url)
                  }
                }
              }
            }
          }
        }

        // ------------------ EMPTY STATE
        ColumnLayout {
          visible: root.items.length === 0 && !root.busy
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: Style.space(6)

          Item { Layout.fillHeight: true }

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "󰅖"
            font.family: root.fontFamily
            font.pixelSize: Style.space(32)
            color: root.colDim
          }

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: root.searchQuery ? "No matching items found" : "No items in vault"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
          }

          Item { Layout.fillHeight: true }
        }

        // ------------------ FOOTER HINT
        RowLayout {
          Layout.fillWidth: true
          Text {
            text: "󰌑 Enter: " + root.defaultAction + "  ·  󰘶 Shift+Enter: User  ·  󰘵 Ctrl+Enter: OTP"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
          }
        }
      }
    }
  }
}
