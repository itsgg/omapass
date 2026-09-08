import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Visual audit harness for the OmaPass bar widget.
//
// Renders the real widget inside a minimal fake bar so its PopupCard anchors
// and lays out exactly as it does in omarchy-shell, then drives it into one
// named state so the result can be screenshotted with grim. The user's real
// bar is never touched.
//
// Usage: OMAPASS_AUDIT_STATE=<state> quickshell -p audit/shell.qml
ShellRoot {
  id: harness

  readonly property string state: Quickshell.env("OMAPASS_AUDIT_STATE") || "list"

  // Minimal stand-in for omarchy-shell's Bar: only the members BarWidget,
  // WidgetButton and PopupCard actually read.
  QtObject {
    id: fakeBar
    property string position: "top"
    readonly property bool vertical: false
    readonly property int barSize: Style.bar.sizeHorizontal
    property color foreground: Color.foreground
    property color barForeground: Color.foreground
    property color urgent: Color.urgent
    property string fontFamily: Style.font.family
    property var activePopout: null
    property bool foregroundAnimationEnabled: false
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { if (activePopout === owner) activePopout = null }
    function moduleWidgets(id) { return [] }
    function run(cmd) {}
    function runProcess(p) {}
    function showTooltip(t, s) {}
    function hideTooltip(t) {}
    function registerClickTarget(t) {}
    function unregisterClickTarget(t) {}
    function targetTooltipHovered(t) { return false }
  }

  // Opaque full-screen ground, so release screenshots do not carry whatever
  // happened to be on the desktop behind the popup.
  PanelWindow {
    visible: Quickshell.env("OMAPASS_AUDIT_BACKDROP") === "1"
    anchors { top: true; bottom: true; left: true; right: true }
    color: Color.background
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "omapass-audit-backdrop"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
  }

  PanelWindow {
    id: fakeBarWindow
    anchors { top: true; left: true; right: true }
    implicitHeight: Style.bar.sizeHorizontal
    color: Color.bar.background
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.namespace: "omapass-audit"

    OmaPassWidget {
      id: widget
      anchors.left: parent.left
      anchors.leftMargin: 120
      anchors.verticalCenter: parent.verticalCenter
      bar: fakeBar
      moduleName: "gg.omapass"
    }
  }

  // Fixtures. Long strings are deliberate: overflow only shows up at the
  // extremes, and every one of these is a shape a real vault produces.
  readonly property var loginItem: ({
    id: "login1", title: "GitHub", category: "LOGIN", username: "itsgg",
    url: "https://github.com/login", vault: "Personal", favorite: true
  })
  readonly property var longItem: ({
    id: "long1",
    title: "Very Long Item Title That Should Elide Rather Than Overflow The Card",
    category: "LOGIN",
    username: "an.extremely.long.email.address.for.testing@some-very-long-domain.example.com",
    url: "https://an-extremely-long-subdomain.example.com/a/deep/path/that/keeps/going",
    vault: "A Vault With A Long Name", favorite: false
  })
  readonly property var cardItem: ({
    id: "card1", title: "Acme Bank Visa", category: "CREDIT_CARD",
    username: "4611 **** 4017", url: "", vault: "Financial", favorite: false
  })

  // The popup is opened first and the state applied after: PopupCard's
  // onOpenChanged resets currentView to "list", so anything set before the
  // open is thrown away.
  function openPopup() {
    widget.unlocked = harness.state.indexOf("locked") !== 0 && harness.state !== "demo-locked"
    widget.popupOpen = true
  }

  function applyState() {
    widget.unlocked = harness.state.indexOf("locked") !== 0 && harness.state !== "demo-locked"

    if (harness.state === "locked") {
      widget.account = "you@example.com"
      return
    }
    if (harness.state === "locked-long") {
      widget.unlocked = false
      widget.account = "an.extremely.long.account.address.for.testing@some-very-long-domain.example.com"
      return
    }
    if (harness.state === "locked-none") {
      widget.unlocked = false
      widget.account = ""
      return
    }
    if (harness.state === "demo-locked") {
      widget.unlocked = false
      widget.account = "you@example.com"
      return
    }

    widget.account = "you@example.com"
    widget.itemCount = 367

    if (harness.state === "empty") {
      widget.items = []
      widget.searchQuery = ""
    } else if (harness.state === "no-results") {
      widget.items = []
      widget.searchQuery = "zzzznomatch"
    } else if (harness.state === "long") {
      widget.items = [harness.longItem, harness.longItem, harness.longItem]
    } else if (harness.state === "many") {
      var many = []
      for (var i = 1; i <= 20; i++) {
        many.push({ id: "m" + i, title: "Item " + (i < 10 ? "0" + i : i),
                    category: "LOGIN", username: "user" + i,
                    url: "https://example.com/" + i, vault: "Personal", favorite: false })
      }
      widget.items = many
    } else {
      widget.items = [harness.loginItem, harness.cardItem, harness.longItem]
    }

    if (harness.state === "details" || harness.state === "details-totp"
        || harness.state === "details-notes" || harness.state === "details-card"
        || harness.state === "details-many" || harness.state === "details-legacy" || harness.state === "details-totp60") {
      widget.selectedItem = harness.state === "details-card" ? harness.cardItem : harness.loginItem
      widget.itemDetails = harness.detailsFixture(harness.state)
      widget.currentView = "details"
      if (harness.state === "details-totp" || harness.state === "details-totp60") {
        widget.markTotpFetched()
        console.warn("PERIOD state=" + harness.state
          + " periodMs=" + widget.totpPeriod
          + " secondsLeft=" + widget.totpSecondsLeft
          + " aligned=" + ((widget.totpExpiresAt % widget.totpPeriod) === 0))
      }
    } else if (harness.state === "details-error") {
      widget.selectedItem = harness.loginItem
      widget.detailsError = "1Password returned an authorization error while reading this item"
      widget.currentView = "details"
    } else if (harness.state === "details-loading") {
      widget.selectedItem = harness.loginItem
      widget.loadingDetails = true
      widget.currentView = "details"
    }

    // Set directly, not via showToast: the 3.2s timer would clear it before grim fires.
    if (harness.state.indexOf("demo") === 0) {
      widget.items = harness.demoItems
      widget.itemCount = 412
      widget.account = "you@example.com"
      widget.selectedIndex = 0
      if (harness.state === "demo-list") {
        widget.searchQuery = ""
        widget.statusToast = "Copied password for GitHub (clears in 30s)"
      } else if (harness.state === "demo-search") {
        widget.searchQuery = "git"
        widget.items = [harness.demoItems[0]]
      } else {
        widget.selectedItem = harness.demoItems[harness.state === "demo-card" ? 2 : 0]
        widget.itemDetails = harness.demoDetails(harness.state)
        widget.currentView = "details"
        // Anchor the code to a real window so the countdown renders.
        if (harness.state === "demo-details") {
          widget.freshTotp = "418 209"
          widget.markTotpFetched()
        }
      }
    }

    if (harness.state === "create-card" || harness.state === "create-note") {
      widget.vaults = [{ id: "v1", name: "Personal" }]
      widget.startCreate(harness.state === "create-card" ? "Acme Bank Visa" : "Recovery codes")
      widget.setCreateCategory(harness.state === "create-card" ? "CREDIT_CARD" : "SECURE_NOTE")
    }
    if (harness.state === "create") {
      widget.vaults = [{ id: "v1", name: "Personal" }, { id: "v2", name: "Work" }]
      widget.startCreate("GitHub")
    }
    if (harness.state === "create-empty") {
      widget.vaults = [{ id: "v1", name: "Personal" }]
      widget.startCreate("")
    }

    if (harness.state === "toast") widget.statusToast = "Copied password (clears in 30s)"
  }

  readonly property var demoItems: [
    { id: "d1", title: "GitHub", category: "LOGIN", username: "octocat",
      url: "https://github.com/login", vault: "Personal", favorite: true },
    { id: "d2", title: "Fastmail", category: "LOGIN", username: "you@example.com",
      url: "https://app.fastmail.com", vault: "Personal", favorite: true },
    { id: "d3", title: "Acme Bank Visa", category: "CREDIT_CARD", username: "4242 **** 4242",
      url: "", vault: "Finance", favorite: false },
    { id: "d4", title: "Home Wi-Fi", category: "SECURE_NOTE", username: "",
      url: "", vault: "Personal", favorite: false },
    { id: "d5", title: "staging.example.com", category: "LOGIN", username: "deploy",
      url: "https://staging.example.com", vault: "Work", favorite: false },
    { id: "d6", title: "Cloudflare", category: "LOGIN", username: "you@example.com",
      url: "https://dash.cloudflare.com", vault: "Work", favorite: false }
  ]

  function demoDetails(which) {
    if (which === "demo-card") {
      return {
        id: "d3", title: "Acme Bank Visa", category: "CREDIT_CARD", vault: "Finance",
        favorite: false, updatedAt: "2026-08-27T07:09:29Z", urls: [], notes: "", totp: "",
        fields: [
          { id: "cardholder", label: "cardholder", value: "A N OTHER", type: "STRING", purpose: "", concealed: false },
          { id: "ccnum", label: "ccnum", value: "4242424242424242", type: "CREDIT_CARD_NUMBER", purpose: "", concealed: true },
          { id: "cvv", label: "cvv", value: "123", type: "CONCEALED", purpose: "", concealed: true },
          { id: "expiry", label: "expiry", value: "202812", type: "MONTH_YEAR", purpose: "", concealed: false }
        ]
      }
    }
    return {
      id: "d1", title: "GitHub", category: "LOGIN", vault: "Personal", favorite: true,
      updatedAt: "2026-08-27T07:09:29Z",
      urls: [{ label: "website", href: "https://github.com/login", primary: true }],
      notes: "", totp: "418 209", hasTotp: true,
      fields: [
        { id: "username", label: "username", value: "octocat", type: "STRING", purpose: "USERNAME", concealed: false },
        { id: "password", label: "password", value: "correct-horse-battery-staple", type: "CONCEALED", purpose: "PASSWORD", concealed: true }
      ]
    }
  }

  function detailsFixture(which) {
    var base = {
      id: "login1", title: "GitHub", category: "LOGIN", vault: "Personal",
      favorite: true, updatedAt: "2026-08-27T07:09:29Z",
      urls: [{ label: "website", href: "https://github.com/login", primary: true }],
      notes: "", totp: "", hasTotp: false, fields: []
    }

    if (which === "details-many") {
      base.fields = []
      for (var i = 1; i <= 12; i++) {
        base.fields.push({ id: "f" + i, label: "field " + i, value: "value " + i,
                           type: "STRING", purpose: "", concealed: false })
      }
      return base
    }
    if (which === "details-card") {
      base.title = "Acme Bank Visa"
      base.category = "CREDIT_CARD"
      base.urls = []
      base.fields = [
        { id: "cardholder", label: "cardholder", value: "A N OTHER", type: "STRING", purpose: "", concealed: false },
        { id: "ccnum", label: "ccnum", value: "4242424242424242", type: "CREDIT_CARD_NUMBER", purpose: "", concealed: true },
        { id: "cvv", label: "cvv", value: "123", type: "CONCEALED", purpose: "", concealed: true },
        { id: "expiry", label: "expiry", value: "202812", type: "MONTH_YEAR", purpose: "", concealed: false }
      ]
      return base
    }

    base.fields = [
      { id: "username", label: "username", value: "itsgg", type: "STRING", purpose: "USERNAME", concealed: false },
      { id: "password", label: "password", value: "correct-horse-battery-staple", type: "CONCEALED", purpose: "PASSWORD", concealed: true }
    ]
    if (which === "details-totp") { base.totp = "123456"; base.hasTotp = true }
    if (which === "details-legacy") { base.totp = "123456" }
    if (which === "details-totp60") { base.totp = "654321"; base.hasTotp = true; base.totpPeriod = 60 }
    if (which === "details-notes") {
      base.notes = "A secure note that runs on for a while so the notes card has to scroll.\nSecond line.\nThird line.\nFourth line.\nFifth line."
    }
    return base
  }

  // Applied after the widget's own startup churn has settled, so nothing it
  // does on open can clobber the fixture we are trying to photograph.
  Timer {
    interval: 400
    running: true
    repeat: false
    onTriggered: harness.openPopup()
  }

  Timer {
    interval: 1100
    running: true
    repeat: false
    onTriggered: harness.applyState()
  }
}
