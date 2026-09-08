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
  function close() {
    popupOpen = false
  }
  function open() {
    root.currentView = "list"
    popupOpen = true
    checkStatus()
    refreshItems()
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

  // View state: "list" or "details"
  property string currentView: "list"
  property var selectedItem: null
  property var itemDetails: null
  property bool loadingDetails: false
  property string detailsError: ""
  property string statusToast: ""

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

  Component.onCompleted: {
    checkStatus()
    refreshItems()
  }

  // Toast feedback timer
  Timer {
    id: toastTimer
    interval: 3200
    repeat: false
    onTriggered: root.statusToast = ""
  }

  function showToast(msg) {
    root.statusToast = msg
    toastTimer.restart()
  }

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
          root.account = resp.account || resp.name || ""
          if (resp.itemCount !== undefined) root.itemCount = resp.itemCount
          if (!wasUnlocked && root.unlocked) {
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
        limit: 80
      })
    ]
    searchProc.running = true
  }

  // Item details process
  Process {
    id: itemDetailsProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loadingDetails = false
        try {
          var resp = JSON.parse(text)
          if (resp.ok && resp.item) {
            root.itemDetails = resp.item
            root.detailsError = ""
          } else {
            root.detailsError = resp.error || "Failed to retrieve item details"
          }
        } catch (e) {
          root.detailsError = "Error loading item details"
        }
      }
    }
  }

  function showItemDetails(item) {
    if (!item) return
    root.selectedItem = item
    root.itemDetails = null
    root.detailsError = ""
    root.loadingDetails = true
    root.currentView = "details"

    itemDetailsProc.command = [
      "python3",
      root.helperPath,
      "request",
      JSON.stringify({
        action: "get_item",
        id: item.id
      })
    ]
    itemDetailsProc.running = true
  }

  function backToList() {
    root.currentView = "list"
    root.selectedItem = null
    root.itemDetails = null
    root.detailsError = ""
    Qt.callLater(function() {
      if (root.unlocked) {
        searchInput.forceActiveFocus()
      }
    })
  }

  function openDesktop(itemId) {
    if (!itemId) return
    runAction({ action: "open_desktop", id: itemId })
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
    root.currentView = "list"
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
    showToast("Copied " + (field || "password") + " (clears in " + root.clipboardTimeout + "s)")
  }

  function copyRawValue(val, label, title) {
    if (!val) return
    runAction({
      action: "copy",
      value: val,
      title: title || "",
      field: label || "",
      timeout: root.clipboardTimeout
    })
    showToast("Copied " + label + " (clears in " + root.clipboardTimeout + "s)")
  }

  function typeFieldValue(val, label) {
    if (!val) return
    runAction({
      action: "type",
      value: val,
      field: label || ""
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

  // Periodic status poll
  Timer {
    id: checkTimer
    interval: root.popupOpen ? 2500 : 15000
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
    text: root.unlocked ? "󰌆" : ""
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
    contentWidth: popup.fittedContentWidth(Style.space(480))
    contentHeight: root.unlocked
      ? popup.cappedContentHeight(Style.space(520))
      : popup.fittedContentHeight(lockedColumn.implicitHeight + Style.space(48))

    onOpenChanged: {
      if (open) {
        root.currentView = "list"
        root.checkStatus()
        root.refreshItems()
        Qt.callLater(function() {
          if (root.unlocked) {
            searchInput.forceActiveFocus()
          } else {
            unlockBtn.forceActiveFocus()
          }
        })
      }
    }

    ColumnLayout {
      anchors.fill: parent
      spacing: Style.space(10)

      // ========================================== LIST HEADER
      RowLayout {
        visible: root.currentView === "list"
        Layout.fillWidth: true
        spacing: Style.space(8)

        Text {
          text: "󰌆 1Password"
          font.family: root.fontFamily
          color: root.colForeground
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Rectangle {
          visible: root.unlocked && root.itemCount > 0
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.18)
          radius: Style.space(9)
          Layout.preferredHeight: Style.space(20)
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
        Button {
          visible: root.unlocked
          iconText: "󰑐"
          tooltipText: "Sync vault"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: root.syncVault()
        }

        // Lock button
        Button {
          visible: root.unlocked
          iconText: ""
          tooltipText: "Lock vault"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: root.lockVault()
        }
      }

      // ========================================== DETAILS HEADER
      RowLayout {
        visible: root.currentView === "details"
        Layout.fillWidth: true
        spacing: Style.space(8)

        Button {
          iconText: "󰁍"
          text: "Back"
          accent: root.colAccent
          horizontalPadding: Style.space(8)
          verticalPadding: Style.space(4)
          onClicked: root.backToList()
        }

        Rectangle {
          width: Style.space(28)
          height: Style.space(28)
          radius: Style.space(6)
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.2)

          Text {
            anchors.centerIn: parent
            text: root.selectedItem ? Model.categoryIcon(root.selectedItem.category) : "󰌆"
            font.family: root.fontFamily
            font.pixelSize: Style.space(14)
            color: root.colAccent
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 1

          Text {
            Layout.fillWidth: true
            text: (root.itemDetails && root.itemDetails.title) ? root.itemDetails.title : (root.selectedItem ? root.selectedItem.title : "Item Details")
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            color: root.colForeground
            elide: Text.ElideRight
          }

          RowLayout {
            spacing: Style.space(6)

            Rectangle {
              visible: !!(root.itemDetails && root.itemDetails.vault) || !!(root.selectedItem && root.selectedItem.vault)
              color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.08)
              radius: Style.space(4)
              Layout.preferredHeight: Style.space(16)
              Layout.preferredWidth: vaultText.implicitWidth + Style.space(10)

              Text {
                id: vaultText
                anchors.centerIn: parent
                text: (root.itemDetails && root.itemDetails.vault) || (root.selectedItem && root.selectedItem.vault) || ""
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption - 1
                color: root.colDim
              }
            }

            Rectangle {
              visible: !!(root.itemDetails && root.itemDetails.category) || !!(root.selectedItem && root.selectedItem.category)
              color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.15)
              radius: Style.space(4)
              Layout.preferredHeight: Style.space(16)
              Layout.preferredWidth: catBadgeText.implicitWidth + Style.space(10)

              Text {
                id: catBadgeText
                anchors.centerIn: parent
                text: (root.itemDetails && root.itemDetails.category) || (root.selectedItem && root.selectedItem.category) || ""
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption - 1
                font.bold: true
                color: root.colAccent
              }
            }
          }
        }

        Button {
          iconText: "󱔐"
          tooltipText: "Open in 1Password desktop app"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: {
            var id = (root.itemDetails && root.itemDetails.id) || (root.selectedItem && root.selectedItem.id)
            if (id) root.openDesktop(id)
          }
        }
      }

      // ========================================== LOCKED STATE
      ColumnLayout {
        id: lockedColumn
        visible: !root.unlocked
        Layout.fillWidth: true
        spacing: Style.space(14)

        Item { Layout.preferredHeight: Style.space(8) }

        Rectangle {
          Layout.alignment: Qt.AlignHCenter
          width: Style.space(56)
          height: Style.space(56)
          radius: Style.space(28)
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.12)
          border.color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.3)
          border.width: 1

          Text {
            anchors.centerIn: parent
            text: ""
            font.family: root.fontFamily
            font.pixelSize: Style.space(22)
            color: root.colAccent
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "1Password Vault Locked"
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
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
        }

        Item { Layout.preferredHeight: Style.space(4) }

        Button {
          id: unlockBtn
          Layout.alignment: Qt.AlignHCenter
          text: "Unlock Vault"
          iconText: ""
          accent: root.colAccent
          selected: true
          bordered: true
          focusable: true
          horizontalPadding: Style.space(16)
          verticalPadding: Style.space(8)
          onClicked: root.unlock()
          Keys.onReturnPressed: root.unlock()
          Keys.onEnterPressed: root.unlock()
        }

        Item { Layout.preferredHeight: Style.space(8) }
      }

      // ========================================== UNLOCKED: LIST VIEW
      ColumnLayout {
        visible: root.unlocked && root.currentView === "list"
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(8)

        // Search Bar
        BorderSurface {
          id: searchBox
          Layout.fillWidth: true
          Layout.preferredHeight: Style.space(36)
          radius: Style.cornerRadius
          color: Style.controlFill(searchInput.activeFocus, searchBoxHover.hovered, root.colForeground, root.colAccent)
          borderSpec: Border.controlSpec(searchInput.activeFocus ? "focus" : (searchBoxHover.hovered ? "hover-cursor" : "normal"), root.colForeground, root.colAccent)

          HoverHandler { id: searchBoxHover }

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.spacing.controlPaddingX
            anchors.rightMargin: Style.spacing.controlPaddingX
            spacing: Style.space(8)

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
                text: "Search items, logins, tags..."
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.colDim
                visible: !searchInput.text && !searchInput.inputMethodComposing
              }

              Keys.onEscapePressed: {
                if (searchInput.text.length > 0) {
                  searchInput.text = ""
                } else {
                  root.close()
                }
              }
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
              Keys.onRightPressed: {
                if (root.items.length > 0 && root.selectedIndex >= 0 && root.selectedIndex < root.items.length) {
                  root.showItemDetails(root.items[root.selectedIndex])
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
                  root.showItemDetails(item)
                }
              }
            }

            Text {
              visible: searchInput.text.length > 0
              text: "󰅖"
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: clearMouse.containsMouse ? root.colAccent : root.colDim

              MouseArea {
                id: clearMouse
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

        // Category Filter Chips
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)

          Repeater {
            model: Model.CATEGORIES
            delegate: Rectangle {
              required property var modelData
              required property int index

              readonly property bool isSelected: root.selectedCategory === modelData.id
              Layout.preferredHeight: Style.space(24)
              Layout.preferredWidth: chipRow.implicitWidth + Style.space(16)
              radius: Style.cornerRadius
              color: isSelected
                ? Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.22)
                : chipMouse.containsMouse
                  ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.08)
                  : "transparent"
              border.color: isSelected
                ? root.colAccent
                : (chipMouse.containsMouse ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.2) : "transparent")
              border.width: 1

              Row {
                id: chipRow
                anchors.centerIn: parent
                spacing: Style.space(5)

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
                  root.refreshItems()
                  searchInput.forceActiveFocus()
                }
              }
            }
          }
        }

        // Items List
        Item {
          Layout.fillWidth: true
          Layout.fillHeight: true

          ListView {
            id: itemList
            anchors.fill: parent
            clip: true
            visible: root.items.length > 0
            model: root.items
            spacing: Style.space(2)

            delegate: Rectangle {
              required property var modelData
              required property int index

              readonly property bool isSelected: root.selectedIndex === index
              width: itemList.width
              height: Style.space(48)
              radius: Style.cornerRadius
              color: isSelected
                ? Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.18)
                : itemMouse.containsMouse
                  ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.06)
                  : "transparent"

              MouseArea {
                id: itemMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: root.selectedIndex = index
                onClicked: root.showItemDetails(modelData)
              }

              RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Style.space(8)
                anchors.rightMargin: Style.space(6)
                spacing: Style.space(8)

                // Category Icon
                Rectangle {
                  width: Style.space(30)
                  height: Style.space(30)
                  radius: Style.space(6)
                  color: isSelected
                    ? Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.25)
                    : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.06)

                  Text {
                    anchors.centerIn: parent
                    text: Model.categoryIcon(modelData.category)
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(14)
                    color: isSelected ? root.colAccent : root.colForeground
                  }
                }

                // Title and Subtitle
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

                // Quick Action Buttons
                Row {
                  z: 10
                  spacing: Style.space(4)
                  visible: isSelected || itemMouse.containsMouse

                  // Copy Password
                  Rectangle {
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: pwMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

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
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: userMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

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
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: otpMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

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
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: urlMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

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

                  // View Details (Chevron)
                  Rectangle {
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: detBtnMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

                    Text {
                      anchors.centerIn: parent
                      text: "󰅂"
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      color: detBtnMouse.containsMouse ? Color.background : root.colForeground
                    }

                    MouseArea {
                      id: detBtnMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.showItemDetails(modelData)
                    }
                  }
                }
              }
            }
          }

          // Empty State
          ColumnLayout {
            anchors.centerIn: parent
            visible: root.items.length === 0
            spacing: Style.space(6)

            Text {
              Layout.alignment: Qt.AlignHCenter
              text: root.busy ? "󰑐" : "󰍉"
              font.family: root.fontFamily
              font.pixelSize: Style.space(32)
              color: root.busy ? root.colAccent : root.colDim
            }

            Text {
              Layout.alignment: Qt.AlignHCenter
              text: root.busy ? "Syncing vault..." : (root.searchQuery ? "No matching items found" : "No items in vault")
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: root.colForeground
            }

            Text {
              visible: !root.busy && root.searchQuery.length > 0
              Layout.alignment: Qt.AlignHCenter
              text: "Try a different search query or category"
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              color: root.colDim
            }
          }
        }

        // Footer Separator
        Rectangle {
          Layout.fillWidth: true
          Layout.preferredHeight: 1
          color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)
        }

        // List View Footer
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          Text {
            Layout.fillWidth: true
            text: "Click Details   ↵ Details   󰌆 Quick Copy   ⇧↵ Username   ⌃↵ TOTP"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
          }

          Text {
            visible: !!root.account
            text: root.account
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
            elide: Text.ElideMiddle
            Layout.maximumWidth: Style.space(160)
          }
        }
      }

      // ========================================== UNLOCKED: DETAILS VIEW
      ColumnLayout {
        id: detailsViewArea
        visible: root.unlocked && root.currentView === "details"
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Style.space(8)

        // Loading Indicator (Pixel-Perfect Mathematical Centering)
        Item {
          visible: root.loadingDetails
          Layout.fillWidth: true
          Layout.fillHeight: true

          ColumnLayout {
            anchors.centerIn: parent
            spacing: Style.space(14)

            Item {
              Layout.alignment: Qt.AlignHCenter
              width: Style.space(40)
              height: Style.space(40)

              Text {
                anchors.centerIn: parent
                text: "󰦖"
                font.family: root.fontFamily
                font.pixelSize: Style.space(32)
                color: root.colAccent
                transformOrigin: Item.Center

                RotationAnimator on rotation {
                  from: 0
                  to: 360
                  duration: 900
                  loops: Animation.Infinite
                  running: root.loadingDetails
                }
              }
            }

            Text {
              Layout.alignment: Qt.AlignHCenter
              text: "Loading item details..."
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              color: root.colForeground
            }
          }
        }

        // Error State (Centered)
        Item {
          visible: !root.loadingDetails && !!root.detailsError
          Layout.fillWidth: true
          Layout.fillHeight: true

          ColumnLayout {
            anchors.centerIn: parent
            spacing: Style.space(10)

            Text {
              Layout.alignment: Qt.AlignHCenter
              text: "󰅙"
              font.family: root.fontFamily
              font.pixelSize: Style.space(34)
              color: Color.negative || "#e06c75"
            }

            Text {
              Layout.alignment: Qt.AlignHCenter
              text: root.detailsError
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              color: root.colForeground
              wrapMode: Text.Wrap
              horizontalAlignment: Text.AlignHCenter
              Layout.maximumWidth: Style.space(380)
            }

            Button {
              Layout.alignment: Qt.AlignHCenter
              text: "Retry"
              iconText: "󰑐"
              accent: root.colAccent
              onClicked: {
                if (root.selectedItem) root.showItemDetails(root.selectedItem)
              }
            }
          }
        }

        // Scrollable Details Body
        Flickable {
          id: detailsFlick
          visible: !root.loadingDetails && !root.detailsError && !!root.itemDetails
          Layout.fillWidth: true
          Layout.fillHeight: true
          contentWidth: width
          contentHeight: detailsContentCol.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds

          ColumnLayout {
            id: detailsContentCol
            width: parent.width
            spacing: Style.space(8)

            // TOTP Highlight Banner (Only if TOTP exists)
            BorderSurface {
              visible: !!(root.itemDetails && root.itemDetails.totp && String(root.itemDetails.totp).trim().length > 0)
              Layout.fillWidth: true
              Layout.preferredHeight: Style.space(56)
              radius: Style.cornerRadius
              color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.12)
              borderSpec: Border.controlSpec("normal", root.colForeground, root.colAccent)

              RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(10)
                spacing: Style.space(10)

                Text {
                  text: "󰄬"
                  font.family: root.fontFamily
                  font.pixelSize: Style.space(20)
                  color: root.colAccent
                }

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: 1

                  Text {
                    text: "ONE-TIME PASSWORD (TOTP)"
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption - 1
                    font.bold: true
                    color: root.colAccent
                  }

                  Text {
                    text: (root.itemDetails && root.itemDetails.totp) ? root.itemDetails.totp : ""
                    font.family: "JetBrainsMono Nerd Font, monospace"
                    font.pixelSize: Style.font.heading
                    font.bold: true
                    color: root.colForeground
                  }
                }

                Button {
                  iconText: "󰆏"
                  text: "Copy"
                  accent: root.colAccent
                  horizontalPadding: Style.space(10)
                  verticalPadding: Style.space(4)
                  onClicked: {
                    if (root.itemDetails && root.itemDetails.totp) {
                      root.copyRawValue(root.itemDetails.totp, "TOTP", root.itemDetails.title)
                    }
                  }
                }
              }
            }

            // Fields Header (Only if fields with content exist)
            PanelSectionHeader {
              visible: !!(root.itemDetails && root.itemDetails.fields && root.itemDetails.fields.length > 0)
              text: "CREDENTIALS & FIELDS"
              foreground: root.colForeground
              fontFamily: root.fontFamily
            }

            // Fields Repeater (Only fields with valid content)
            Repeater {
              model: (root.itemDetails && root.itemDetails.fields) ? root.itemDetails.fields : []
              delegate: BorderSurface {
                id: fieldCard
                required property var modelData
                required property int index
                property bool revealed: false

                // Strictly filter out any field with empty content
                visible: !!(modelData && modelData.value && String(modelData.value).trim().length > 0)
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? Style.space(52) : 0
                radius: Style.cornerRadius
                color: fieldHover.hovered
                  ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.08)
                  : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.04)
                borderSpec: Border.controlSpec(fieldHover.hovered ? "hover-cursor" : "normal", root.colForeground, root.colAccent)

                HoverHandler { id: fieldHover }

                RowLayout {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(8)
                  spacing: Style.space(8)

                  Text {
                    text: Model.fieldIcon(modelData)
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(16)
                    color: root.colAccent
                  }

                  ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    Text {
                      text: Model.fieldDisplayName(modelData).toUpperCase()
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption - 1
                      font.bold: true
                      color: root.colDim
                      elide: Text.ElideRight
                    }

                    Text {
                      id: valText
                      Layout.fillWidth: true
                      text: (modelData.concealed && !fieldCard.revealed)
                        ? Model.maskText(modelData.value)
                        : (modelData.value || "")
                      font.family: (modelData.concealed && !fieldCard.revealed)
                        ? root.fontFamily
                        : "JetBrainsMono Nerd Font, monospace"
                      font.pixelSize: Style.font.body
                      color: root.colForeground
                      elide: Text.ElideRight
                    }
                  }

                  RowLayout {
                    spacing: Style.space(4)

                    // Eye reveal toggle
                    Button {
                      visible: !!modelData.concealed
                      iconText: fieldCard.revealed ? "󰈉" : "󰈈"
                      tooltipText: fieldCard.revealed ? "Conceal" : "Reveal"
                      accent: root.colAccent
                      horizontalPadding: Style.space(6)
                      verticalPadding: Style.space(4)
                      onClicked: fieldCard.revealed = !fieldCard.revealed
                    }

                    // Auto-type button
                    Button {
                      visible: !!modelData.value
                      iconText: "󰌌"
                      tooltipText: "Auto-type into active window"
                      accent: root.colAccent
                      horizontalPadding: Style.space(6)
                      verticalPadding: Style.space(4)
                      onClicked: root.typeFieldValue(modelData.value, Model.fieldDisplayName(modelData))
                    }

                    // Copy button
                    Button {
                      visible: !!modelData.value
                      iconText: "󰆏"
                      tooltipText: "Copy " + Model.fieldDisplayName(modelData)
                      accent: root.colAccent
                      horizontalPadding: Style.space(6)
                      verticalPadding: Style.space(4)
                      onClicked: root.copyRawValue(modelData.value, Model.fieldDisplayName(modelData), root.itemDetails.title)
                    }
                  }
                }
              }
            }

            // Websites Header (Only if URLs with content exist)
            PanelSectionHeader {
              visible: !!(root.itemDetails && root.itemDetails.urls && root.itemDetails.urls.length > 0)
              text: "WEBSITES"
              foreground: root.colForeground
              fontFamily: root.fontFamily
            }

            // Websites Repeater
            Repeater {
              model: (root.itemDetails && root.itemDetails.urls) ? root.itemDetails.urls : []
              delegate: BorderSurface {
                required property var modelData
                required property int index

                visible: !!(modelData && modelData.href && String(modelData.href).trim().length > 0)
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? Style.space(42) : 0
                radius: Style.cornerRadius
                color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.04)
                borderSpec: Border.controlSpec("normal", root.colForeground, root.colAccent)

                RowLayout {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(8)
                  spacing: Style.space(8)

                  Text {
                    text: "󰖟"
                    font.family: root.fontFamily
                    font.pixelSize: Style.space(15)
                    color: root.colAccent
                  }

                  Text {
                    Layout.fillWidth: true
                    text: modelData.href || ""
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    color: root.colForeground
                    elide: Text.ElideRight
                  }

                  Button {
                    iconText: "󰆏"
                    tooltipText: "Copy URL"
                    accent: root.colAccent
                    horizontalPadding: Style.space(6)
                    verticalPadding: Style.space(4)
                    onClicked: root.copyRawValue(modelData.href, "URL", root.itemDetails.title)
                  }

                  Button {
                    iconText: "󰌹"
                    tooltipText: "Open in Browser"
                    accent: root.colAccent
                    horizontalPadding: Style.space(6)
                    verticalPadding: Style.space(4)
                    onClicked: root.openUrl(modelData.href)
                  }
                }
              }
            }

            // Notes Header (Only if non-empty notes exist)
            PanelSectionHeader {
              visible: !!(root.itemDetails && root.itemDetails.notes && String(root.itemDetails.notes).trim().length > 0)
              text: "SECURE NOTES"
              foreground: root.colForeground
              fontFamily: root.fontFamily
            }

            // Notes Card
            BorderSurface {
              visible: !!(root.itemDetails && root.itemDetails.notes && String(root.itemDetails.notes).trim().length > 0)
              Layout.fillWidth: true
              Layout.preferredHeight: Math.min(Style.space(130), Math.max(Style.space(64), notesText.implicitHeight + Style.space(24)))
              radius: Style.cornerRadius
              color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.04)
              borderSpec: Border.controlSpec("normal", root.colForeground, root.colAccent)

              ColumnLayout {
                anchors.fill: parent
                anchors.margins: Style.space(8)
                spacing: Style.space(4)

                RowLayout {
                  Layout.fillWidth: true
                  Item { Layout.fillWidth: true }
                  Button {
                    iconText: "󰆏"
                    text: "Copy Notes"
                    accent: root.colAccent
                    horizontalPadding: Style.space(6)
                    verticalPadding: Style.space(2)
                    onClicked: root.copyRawValue(root.itemDetails.notes, "Notes", root.itemDetails.title)
                  }
                }

                Flickable {
                  Layout.fillWidth: true
                  Layout.fillHeight: true
                  contentHeight: notesText.implicitHeight
                  contentWidth: width
                  clip: true

                  TextEdit {
                    id: notesText
                    width: parent.width
                    text: (root.itemDetails && root.itemDetails.notes) ? root.itemDetails.notes : ""
                    font.family: "JetBrainsMono Nerd Font, monospace"
                    font.pixelSize: Style.font.caption
                    color: root.colForeground
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                  }
                }
              }
            }

            // Fallback if item has literally no fields, no notes, no urls
            ColumnLayout {
              visible: !!(root.itemDetails && (!root.itemDetails.fields || root.itemDetails.fields.length === 0) && !root.itemDetails.notes && (!root.itemDetails.urls || root.itemDetails.urls.length === 0) && !root.itemDetails.totp)
              Layout.fillWidth: true
              Layout.preferredHeight: Style.space(100)
              spacing: Style.space(8)

              Item { Layout.fillHeight: true }

              Text {
                Layout.alignment: Qt.AlignHCenter
                text: "󰋽"
                font.family: root.fontFamily
                font.pixelSize: Style.space(28)
                color: root.colDim
              }

              Text {
                Layout.alignment: Qt.AlignHCenter
                text: "No credentials or fields recorded for this item"
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                color: root.colDim
              }

              Item { Layout.fillHeight: true }
            }

            Item { Layout.preferredHeight: Style.space(4) }
          }
        }

        // Toast feedback pill
        BorderSurface {
          visible: root.statusToast.length > 0
          Layout.fillWidth: true
          Layout.preferredHeight: Style.space(28)
          radius: Style.cornerRadius
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.18)
          borderSpec: Border.controlSpec("normal", root.colForeground, root.colAccent)

          RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Style.space(10)
            anchors.rightMargin: Style.space(10)
            spacing: Style.space(6)

            Text {
              text: "󰄬"
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              color: root.colAccent
            }

            Text {
              Layout.fillWidth: true
              text: root.statusToast
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              color: root.colForeground
              elide: Text.ElideRight
            }
          }
        }

        // Footer Separator
        Rectangle {
          Layout.fillWidth: true
          Layout.preferredHeight: 1
          color: Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.1)
        }

        // Details View Footer
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)

          Button {
            iconText: "󰁍"
            text: "Back to List"
            accent: root.colAccent
            horizontalPadding: Style.space(8)
            verticalPadding: Style.space(2)
            onClicked: root.backToList()
          }

          Item { Layout.fillWidth: true }

          Text {
            visible: !!(root.itemDetails && root.itemDetails.updatedAt)
            text: (root.itemDetails && root.itemDetails.updatedAt) ? ("Updated " + root.itemDetails.updatedAt.substring(0, 10)) : ""
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
          }
        }
      }
    }
  }
}
