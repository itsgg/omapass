import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "components"

BarWidget {
  id: root
  moduleName: "io.github.itsgg.omapass"

  // Open / Close lifecycle
  property bool popupOpen: false
  function close() {
    popupOpen = false
    // Drop the decrypted item as soon as the popup goes away; there is no
    // reason for a plaintext password to outlive the view showing it. The
    // form holds the same material once an edit has loaded it.
    forgetDetails()
    forgetCreateForm()
  }
  function open() {
    root.currentView = "list"
    // Setting this fires the panel's onOpenChanged, which does the status
    // check and the list fetch. Doing them here as well spawned the helper
    // twice for every open.
    popupOpen = true
  }

  // Releases the collector holding the last decrypted item.
  //
  // A StdioCollector keeps its buffer after streamFinished, and clearing
  // `stdout` only disconnects it: the object stays parented to the Process
  // with the plaintext still in it, so a per-request collector would pile up
  // one decrypted item per view. It has to be destroyed explicitly.
  function releaseDetailsCollector() {
    var used = itemDetailsProc.stdout
    itemDetailsProc.stdout = null
    if (used) used.destroy()
  }

  function forgetDetails() {
    // The confirmation is about an item; dropping the item has to drop the
    // question, or the scrim stays up over whatever is shown next.
    root.pendingDeleteId = ""
    root.pendingDeleteTitle = ""
    deleteConfirm.opened = false
    itemDetailsProc.pendingItem = null
    root.releaseDetailsCollector()
    root.itemDetails = null
    root.selectedItem = null
    root.detailsError = ""
    root.loadingDetails = false
    root.freshTotp = ""
    root.totpExpiresAt = 0
    root.detailIndex = 0
    root.detailRevealed = false
  }
  function toggle() { popupOpen ? close() : open() }
  readonly property bool opened: popupOpen

  // Settings from shell.json configuration
  readonly property int clipboardTimeout: setting("clipboardTimeout", 30)
  readonly property string defaultAction: setting("defaultAction", "password")

  // The highlighted item and the actions its category actually supports. The
  // footer hint is built from these, so the keys it advertises are the keys
  // that will work on this row.
  readonly property var currentItem: (root.selectedIndex >= 0 && root.selectedIndex < root.items.length)
    ? root.items[root.selectedIndex]
    : null
  readonly property var currentFields: Model.quickFieldsFor(root.currentItem)

  // The field the primary action copies. `defaultAction` only applies where
  // the item actually has that field, so a card is never asked for a TOTP.
  function primaryFieldFor(item) {
    var f = Model.quickFieldsFor(item)
    var wanted = root.defaultAction
    if (wanted && (wanted === f.identity || wanted === f.extra)) return wanted
    return f.primary
  }

  // The row's buttons in the same order the keyboard uses: the primary action
  // first, so the leftmost button and Shift+Enter never disagree.
  function quickFieldsOrdered(item) {
    var list = Model.quickFieldList(item)
    var primary = root.primaryFieldFor(item)
    var out = [primary]
    for (var i = 0; i < list.length; i++) {
      if (list[i] !== primary) out.push(list[i])
    }
    return out
  }

  // ---------------------------------------------------------------- Details navigation
  //
  // The details view had no keyboard at all: it could be entered with Enter
  // and left with nothing. `detailIndex` walks the same field cards the mouse
  // hovers, so every action there has a key.
  property int detailIndex: 0

  // The fields that actually get a card. FieldCard hides an empty one, so a
  // list that still carried it let the selection land on a row that was not
  // on screen. The Repeater below is bound to this same list rather than to
  // the raw fields: filtering only one of the two put the focus ring on the
  // wrong card instead, which is worse than the gap it closed.
  readonly property var detailFieldCards: {
    var all = (root.itemDetails && root.itemDetails.fields) ? root.itemDetails.fields : []
    var out = []
    for (var i = 0; i < all.length; i++) {
      var f = all[i]
      if (f && f.value !== undefined && f.value !== null
          && String(f.value).trim().length > 0) {
        out.push(f)
      }
    }
    return out
  }

  // What the keyboard walks: the cards above, plus the note. The helper
  // lifts notes out of `fields` into its own key, so navigation skipped them
  // entirely and on a notes-only item every copy key did nothing. The note
  // is last, so a card's index here is its index in the Repeater.
  readonly property var detailFields: {
    var out = root.detailFieldCards.slice()
    if (root.itemDetails && root.itemDetails.notes && String(root.itemDetails.notes).trim().length > 0) {
      out.push({ id: "notes", label: "notes", value: root.itemDetails.notes,
                 type: "STRING", purpose: "NOTES", concealed: false })
    }
    return out
  }

  readonly property var focusedDetailField: (root.detailIndex >= 0 && root.detailIndex < root.detailFields.length)
    ? root.detailFields[root.detailIndex]
    : null

  // True when the keyboard focus is on the notes entry appended above.
  readonly property bool notesFocused: {
    var f = root.focusedDetailField
    return !!(f && f.id === "notes" && f.purpose === "NOTES")
  }

  function moveDetailField(delta) {
    var n = root.detailFields.length
    if (n === 0) return
    root.detailIndex = ((root.detailIndex + delta) % n + n) % n
  }

  // Called by a field card when it takes focus. Without this, Down or End on
  // an item with many fields selects a card that is scrolled out of sight and
  // Enter copies something the user cannot see.
  function ensureDetailVisible(y, h) {
    if (!detailsFlick.visible || detailsFlick.height <= 0) return
    var top = detailsFlick.contentY
    var bottom = top + detailsFlick.height
    if (y < top) detailsFlick.contentY = Math.max(0, y)
    else if (y + h > bottom) {
      var maxY = Math.max(0, detailsFlick.contentHeight - detailsFlick.height)
      detailsFlick.contentY = Math.min(maxY, y + h - detailsFlick.height)
    }
  }

  function copyFocusedDetailField() {
    var f = root.focusedDetailField
    if (!f) return
    root.copyDetailField(f.id, Model.fieldDisplayName(f))
  }

  function revealFocusedDetailField() {
    if (root.focusedDetailField) root.detailRevealed = !root.detailRevealed
  }

  function typeFocusedDetailField() {
    var f = root.focusedDetailField
    if (f) root.typeDetailField(f.id, Model.fieldDisplayName(f))
  }

  // Reveal is per focused field rather than per card: it resets as soon as the
  // focus moves, so a password cannot be left uncovered by accident.
  property bool detailRevealed: false
  onDetailIndexChanged: root.detailRevealed = false

  // ---------------------------------------------------------------- List navigation
  function selectIndex(i) {
    if (root.items.length === 0) return
    var n = root.items.length
    root.selectedIndex = Math.max(0, Math.min(i, n - 1))
    itemList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  // Wraps, because a list you can only walk off the end of is worse than one
  // that comes back round.
  function moveSelection(delta) {
    if (root.items.length === 0) return
    var n = root.items.length
    root.selectedIndex = ((root.selectedIndex + delta) % n + n) % n
    itemList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
  }

  function pageSize() {
    var rows = Math.floor(itemList.height / Math.max(1, Style.space(48) + Style.space(2)))
    return Math.max(1, rows - 1)
  }

  function cycleCategory(delta) {
    var cats = Model.CATEGORIES
    var at = 0
    for (var i = 0; i < cats.length; i++) {
      if (cats[i].id === root.selectedCategory) { at = i; break }
    }
    root.selectedCategory = cats[((at + delta) % cats.length + cats.length) % cats.length].id
  }

  function activateCurrent() {
    if (!root.currentItem) return
    root.showItemDetails(root.currentItem)
  }

  function copyPrimary() {
    var it = root.currentItem
    if (!it) return
    root.copyField(it.id, root.primaryFieldFor(it), it.title)
  }

  function copyExtra() {
    var it = root.currentItem
    if (!it) return
    var f = root.currentFields
    if (!f.extra) {
      root.showToast("No " + "second factor" + " on this item")
      return
    }
    root.copyField(it.id, f.extra, it.title)
  }

  function openCurrentUrl() {
    var it = root.currentItem
    if (!it || !it.url) {
      root.showToast("This item has no website")
      return
    }
    root.openUrl(it.url)
  }

  // Emacs/readline chords, matching the ones this machine adds to every other
  // Omarchy popup: N/P step the list, F descends, B goes back, M is Enter and
  // Ctrl+[ is Escape. Returns true when the chord was consumed.
  function handleReadlineChord(event, inDetails) {
    if (!(event.modifiers & Qt.ControlModifier)) return false
    switch (event.key) {
    case Qt.Key_N: inDetails ? root.moveDetailField(1) : root.moveSelection(1); return true
    case Qt.Key_P: inDetails ? root.moveDetailField(-1) : root.moveSelection(-1); return true
    case Qt.Key_F: inDetails ? root.copyFocusedDetailField() : root.activateCurrent(); return true
    case Qt.Key_B: inDetails ? root.backToList() : root.close(); return true
    case Qt.Key_M: inDetails ? root.copyFocusedDetailField() : root.activateCurrent(); return true
    case Qt.Key_BracketLeft: inDetails ? root.backToList() : root.close(); return true
    }
    return false
  }

  readonly property string listHint: {
    if (root.items.length === 0) return ""
    var f = root.currentFields
    var parts = ["\u21b5 Details"]
    var primary = root.primaryFieldFor(root.currentItem)
    if (primary) parts.push("\u21e7\u21b5 " + Model.fieldLabelFor(primary))
    if (f.extra && f.extra !== primary) parts.push("\u2303\u21b5 " + Model.fieldLabelFor(f.extra))
    if (root.currentItem && root.currentItem.url) parts.push("\u2325\u21b5 Website")
    return parts.join("   ")
  }

  // Vault state
  property bool installed: true
  property bool unlocked: false
  property string account: ""
  property int itemCount: 0
  property bool busy: false
  property bool searching: false

  // View state: "list", "details" or "create"
  property string currentView: "list"
  property var vaults: []
  property bool creating: false
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

  // Absolute path to helper script. resolvedUrl percent-encodes, so a plugin
  // directory containing a space would otherwise yield an unusable path.
  readonly property string helperPath: decodeURIComponent(Qt.resolvedUrl("omapass-agent.py").toString().replace(/^file:\/\//, ""))

  readonly property color colForeground: bar ? bar.foreground : Color.foreground
  readonly property color colDim: bar ? Qt.darker(bar.foreground, 1.45) : Color.muted
  readonly property color colAccent: Color.accent
  readonly property color colSurface: Color.popups.background
  readonly property color colBorder: Color.popups.border
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // One palette for every component below, so a component takes `palette`
  // rather than four look-alike colour properties.
  // Named `theme`, not `palette`: Qt 6 gives every Item a `palette` property
  // and already has a Palette type, and a property colliding with either
  // silently stays null instead of failing loudly.
  Theme {
    id: appTheme
    bar: root.bar
  }

  Component.onCompleted: {
    checkStatus()
  }

  // Lets the popup be bound to a key, which is the point of a launcher:
  //   omarchy-shell io.github.itsgg.omapass toggle
  //   omarchy-shell io.github.itsgg.omapass search github
  IpcHandler {
    target: "io.github.itsgg.omapass"

    function open(): void { root.broadcast("openFromIpc") }
    function close(): void { root.broadcast("close") }
    function toggle(): void { root.broadcast("toggleFromIpc") }
    function sync(): void { root.broadcast("syncVault") }
    function lock(): void { root.broadcast("lockVault") }

    function new_login(title: string): void {
      root.pendingCreateTitle = title || ""
      root.broadcast("openForCreate")
    }

    function search(query: string): void {
      root.pendingQuery = query || ""
      root.broadcast("openFromIpc")
    }
  }

  // Set by the search IPC just before opening, and consumed on open so the
  // popup comes up with the query already applied.
  property string pendingQuery: ""
  property string pendingCreateTitle: ""

  function openFromIpc() {
    root.open()
    if (root.pendingQuery !== "") {
      root.searchQuery = root.pendingQuery
      searchInput.text = root.pendingQuery
      root.pendingQuery = ""
    }
  }

  function openForCreate() {
    root.open()
    var title = root.pendingCreateTitle
    root.pendingCreateTitle = ""
    Qt.callLater(function() { root.startCreate(title) })
  }

  function toggleFromIpc() {
    if (root.popupOpen) root.close()
    else root.openFromIpc()
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

  // Builds the argv for one helper request.
  //
  // The payload goes to the helper on stdin ("request -"), never as an
  // argument: /proc/<pid>/cmdline is readable by every process on the machine,
  // so a copied credential in argv is a credential published to the machine.
  function helperCommand(payload) {
    return ["python3", root.helperPath, "request", "-"]
  }

  // Starts a helper request, handing the JSON to the child on stdin and then
  // closing it so the child's read() sees EOF.
  function runHelper(proc, payload) {
    proc.command = root.helperCommand(payload)
    proc.stdinEnabled = true
    proc.running = true
    proc.write(JSON.stringify(payload))
    proc.stdinEnabled = false
  }

  // Status check process.
  //
  // `force` is what makes the helper actually ask 1Password, which is also
  // what triggers the desktop app's authorization prompt. Unforced checks are
  // answered from the helper's cache and never prompt, so the widget can poll
  // freely; only the unlock flow forces.
  Process {
    id: statusProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var resp = JSON.parse(text)
          root.installed = resp.installed !== false
          var wasUnlocked = root.unlocked
          root.unlocked = !!resp.unlocked
          if (wasUnlocked && !root.unlocked) {
            // Seeing the vault lock has to drop what is on screen, not merely
            // hide it: otherwise unlocking again redisplays the previous item,
            // reveal state and all, without fetching it.
            root.forgetDetails()
            root.forgetCreateForm()
            root.items = []
            root.currentView = "list"
          }
          root.account = resp.account || resp.name || ""
          if (resp.itemCount !== undefined) root.itemCount = resp.itemCount
          if (!wasUnlocked && root.unlocked) {
            root.refreshItems()
            // The helper answers a forced status as soon as it knows the vault
            // is open, and syncs the item list in the background. Refreshing
            // once here can land before that sync finishes and leave the user
            // looking at "No items in vault" until they touch something.
            syncSettleTimer.attempts = 0
            syncSettleTimer.restart()
          }
        } catch (e) {}
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) root.showToast("Helper failed (exit " + exitCode + ")")
    }
  }

  function checkStatus(force) {
    if (statusProc.running) return
    root.runHelper(statusProc, { action: "status", force: !!force })
  }

  // Items search process
  Process {
    id: searchProc
    running: false
    // Set when a query arrives while a search is already in flight, so the
    // last keystroke is never the one that gets dropped.
    property bool pending: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var resp = JSON.parse(text)
          // A list fetched before the vault locked must not repopulate the
          // view afterwards; the locked card is meant to show nothing.
          if (!root.unlocked) return
          if (resp.ok && resp.items) {
            root.items = resp.items
            if (resp.totalItems !== undefined) root.itemCount = resp.totalItems
            if (root.selectedIndex >= root.items.length) {
              root.selectedIndex = Math.max(0, root.items.length - 1)
            }
          } else if (resp.error) {
            root.showToast(resp.error)
          }
        } catch (e) {}
      }
    }
    property int generation: 0
    property bool exitSeen: false
    function finish() {
      root.searching = false
      if (searchProc.pending) {
        searchProc.pending = false
        Qt.callLater(root.refreshItems)
      }
    }
    onExited: {
      searchProc.exitSeen = true
      searchProc.finish()
    }
    onRunningChanged: {
      if (searchProc.running) return
      var gen = searchProc.generation
      Qt.callLater(function() {
        if (searchProc.generation !== gen) return
        if (searchProc.exitSeen || !root.searching) return
        searchProc.finish()
      })
    }
  }

  function refreshItems() {
    if (searchProc.running) {
      searchProc.pending = true
      return
    }
    root.searching = true
    searchProc.exitSeen = false
    searchProc.generation++
    root.runHelper(searchProc, {
      action: "list",
      query: root.searchQuery,
      category: root.selectedCategory,
      limit: 80
    })
  }

  // Collectors that carry decrypted material are Components, not inline
  // objects, so a fresh one can replace a used one and take its buffer with it.
  Component {
    id: detailsCollector
    StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.loadingDetails = false
        // Closing the popup runs forgetDetails(); without this guard the
        // in-flight response walks straight back in and repopulates the
        // decrypted fields that cleanup just dropped.
        var stillWanted = root.popupOpen
          && root.currentView === "details"
          && root.selectedItem
          && root.selectedItem.id === itemDetailsProc.requestedId
        if (!stillWanted) return
        try {
          var resp = JSON.parse(text)
          if (resp.ok && resp.item) {
            root.itemDetails = resp.item
            root.detailsShownAt = Date.now()
            root.detailsError = ""
            // The code in the item payload has an unknown amount of its window
            // left, so fetch one whose expiry we actually know.
            if (resp.item.hasTotp || resp.item.totp) Qt.callLater(root.refreshTotp)
          } else {
            root.detailsError = resp.error || "Failed to retrieve item details"
          }
        } catch (e) {
          root.detailsError = "Error loading item details"
        }
      }
    }
  }

  // Item details process
  Process {
    id: itemDetailsProc
    running: false
    // The item this fetch was issued for, so a response that lands after the
    // user closed or navigated away cannot be adopted.
    property string requestedId: ""
    // Assigned per request from detailsCollector, and dropped by
    // forgetDetails(): a StdioCollector keeps its buffer after the stream
    // ends, so the whole decrypted item would otherwise stay in memory.
    // Item requested while this fetch was still in flight, re-issued on exit
    // so clicking a second item supersedes the first instead of being ignored.
    property var pendingItem: null
    property int generation: 0
    property bool exitSeen: false
    function finish(errorText) {
      // A helper that never produced parseable output leaves the spinner up
      // forever unless the exit is handled here.
      root.loadingDetails = false
      if (errorText && !root.itemDetails && !root.detailsError) {
        root.detailsError = errorText
      }
      var next = itemDetailsProc.pendingItem
      itemDetailsProc.pendingItem = null
      // Only re-issue if the user is still waiting on that item. Without this
      // check, going Back while a fetch was in flight yanked the view into
      // details again when the old request finished.
      // The gate is re-checked inside the callback, not just before it:
      // Back, close or another selection can land between scheduling and
      // running, and clearing pendingItem cannot cancel a captured value.
      if (next) {
        Qt.callLater(function() {
          if (root.currentView !== "details" || !root.popupOpen) return
          if (!root.selectedItem || root.selectedItem.id !== next.id) return
          root.showItemDetails(next)
        })
      }
    }
    onExited: function(exitCode) {
      itemDetailsProc.exitSeen = true
      itemDetailsProc.finish(exitCode !== 0 ? "Helper failed (exit " + exitCode + ")" : "")
    }
    onRunningChanged: {
      if (itemDetailsProc.running) return
      var gen = itemDetailsProc.generation
      Qt.callLater(function() {
        if (itemDetailsProc.generation !== gen) return
        if (itemDetailsProc.exitSeen || !root.loadingDetails) return
        itemDetailsProc.finish("Could not start the helper (python3 missing?)")
      })
    }
  }

  function showItemDetails(item) {
    if (!item) return
    if (itemDetailsProc.running) {
      itemDetailsProc.pendingItem = item
      root.selectedItem = item
      root.itemDetails = null
      root.freshTotp = ""
      root.totpExpiresAt = 0
      root.detailIndex = 0
      root.detailRevealed = false
      root.currentView = "details"
      root.loadingDetails = true
      return
    }
    root.selectedItem = item
    root.itemDetails = null
    root.detailsError = ""
    root.freshTotp = ""
    root.totpExpiresAt = 0
    // Reveal is consent for one field of one item. Carrying the index and the
    // reveal flag into the next item would uncover a different credential the
    // user never asked to see.
    root.detailIndex = 0
    root.detailRevealed = false
    root.loadingDetails = true
    root.currentView = "details"

    itemDetailsProc.exitSeen = false
    itemDetailsProc.generation++
    itemDetailsProc.requestedId = String(item.id)
    // Drop the previous one before making another, or each view leaves its
    // decrypted item behind.
    root.releaseDetailsCollector()
    itemDetailsProc.stdout = detailsCollector.createObject(itemDetailsProc)
    root.runHelper(itemDetailsProc, { action: "get_item", id: item.id })
  }

  // Live TOTP.
  //
  // A code is only valid until the next 30s wall-clock boundary, so the banner
  // refreshes on that boundary rather than on a free-running interval, and it
  // says how long the displayed code has left instead of quietly going stale.
  // This token's own window, not an assumed 30 seconds: a 60-second code shown
  // on a 30-second timer reads as expired while it is still good.
  readonly property int totpPeriod: {
    var seconds = root.itemDetails ? root.itemDetails.totpPeriod : 0
    return (seconds && seconds > 0) ? seconds * 1000 : 30000
  }

  // Whether the open item has a one-time password at all. `hasTotp` survives
  // the helper's cache, where the code itself deliberately does not; the
  // fallback covers a helper still running an older build, which happens
  // whenever the plugin is updated without restarting the daemon.
  readonly property bool itemHasTotp: !!(root.itemDetails
    && (root.itemDetails.hasTotp
        || (root.itemDetails.totp && String(root.itemDetails.totp).trim().length > 0)))
  property string freshTotp: ""
  property double totpExpiresAt: 0
  property double totpNow: 0

  readonly property string currentTotp: root.freshTotp !== ""
    ? root.freshTotp
    : ((root.itemDetails && root.itemDetails.totp) ? String(root.itemDetails.totp) : "")

  readonly property int totpSecondsLeft: root.totpExpiresAt > 0
    ? Math.max(0, Math.ceil((root.totpExpiresAt - root.totpNow) / 1000))
    : 0
  // Only claim expiry once we have actually anchored a code to a window.
  // Keyed off currentTotp alone, the banner read "EXPIRED" for the whole gap
  // between the item loading and the first live fetch returning.
  // Stale once its window has passed, and also once the code on screen is the
  // one that arrived with the item and no live fetch has anchored it. That
  // code's age is unknown, so after one window it cannot be trusted.
  readonly property bool totpStale: root.currentTotp !== ""
    && (root.totpExpiresAt > 0
        ? root.totpSecondsLeft <= 0
        : (root.detailsShownAt > 0 && (root.totpNow - root.detailsShownAt) > root.totpPeriod))

  // When the open item's details arrived, used above to age an unanchored code.
  property double detailsShownAt: 0

  Process {
    id: otpProc
    running: false
    // The item this request was issued for. A response that arrives after the
    // user has moved on must not overwrite the code shown for another item.
    property string requestedId: ""
    // When the request was issued. If the reply crosses a window boundary the
    // code belongs to the window that just ended, and giving it a fresh
    // countdown would show an expired code as good for a full period.
    property double requestedAt: 0
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var ok = false
        try {
          var resp = JSON.parse(text)
          var stillCurrent = root.itemDetails && root.itemDetails.id === otpProc.requestedId
          if (resp.ok && resp.otp && stillCurrent) {
            var now = Date.now()
            var period = root.totpPeriod
            if (Math.floor(otpProc.requestedAt / period) !== Math.floor(now / period)) {
              // Straddled a boundary: this code is already dead. Ask again
              // rather than display it.
              root.scheduleTotpRetry(true)
              return
            }
            root.freshTotp = String(resp.otp)
            root.markTotpFetched()
            ok = true
          }
        } catch (e) {}
        // Without this a single transient failure froze the displayed code at
        // "expired" until the item was reopened.
        if (!ok) root.scheduleTotpRetry()
      }
    }
  }

  // A TOTP rolls at wall-clock multiples of its period, so the code we just
  // received is good until the next boundary, not for a full period.
  function markTotpFetched() {
    var now = Date.now()
    root.totpNow = now
    root.totpExpiresAt = now + (root.totpPeriod - (now % root.totpPeriod))
    totpTimer.interval = Math.max(1000, root.totpExpiresAt - now + 500)
    totpTimer.restart()
  }

  // Try again on the next window boundary rather than giving up for good.
  function scheduleTotpRetry(immediate) {
    if (!root.itemHasTotp) return
    if (immediate) {
      totpTimer.interval = 250
      totpTimer.restart()
      return
    }
    var now = Date.now()
    totpTimer.interval = Math.max(2000, root.totpPeriod - (now % root.totpPeriod) + 500)
    totpTimer.restart()
  }

  function refreshTotp() {
    if (otpProc.running) return
    if (!root.itemDetails || !root.itemDetails.id) return
    if (!root.itemHasTotp) return
    otpProc.requestedId = String(root.itemDetails.id)
    otpProc.requestedAt = Date.now()
    root.runHelper(otpProc, { action: "otp", id: otpProc.requestedId })
  }

  // Fires once per code window, aligned to the boundary by markTotpFetched.
  Timer {
    id: totpTimer
    interval: root.totpPeriod
    repeat: false
    running: false
    onTriggered: root.refreshTotp()
  }

  // Drives the countdown, and is what makes an un-refreshable code visibly
  // expire rather than sit there looking valid.
  Timer {
    interval: 1000
    repeat: true
    running: root.popupOpen
      && root.currentView === "details"
      && root.itemHasTotp
    onTriggered: root.totpNow = Date.now()
  }

  // Opening the form from a search that found nothing is the common path, so
  // the query becomes the title. Reached from the empty state, the header, and
  // `omarchy-shell io.github.itsgg.omapass new`.
  function startCreate(title) {
    root.forgetDetails()
    root.forgetCreateForm()
    root.currentView = "create"
    createForm.reset(title !== undefined ? title : root.searchQuery)
    createForm.vault = root.vaults.length > 0 ? root.vaults[0].name : ""
    root.refreshVaults()
    Qt.callLater(function() { createForm.focusFirst() })
  }

  // Used by the audit harness to render each category without a mouse.
  function setCreateCategory(id) {
    createForm.selectCategory(id)
  }

  // The popup is its own window, so nothing inside it is reachable from the
  // bar item's focus chain. The audit harness walks the tab order from here:
  // a control Tab skips looks exactly like one it reaches in a screenshot, so
  // enumeration is the only way to check it.
  readonly property Item createFormItem: createForm
  readonly property Item popupContentItem: popupContent

  // Editing reuses the create form: the same spec decides which fields are
  // shown. The helper preserves everything the form does not show, so a
  // narrow form cannot cost the user their custom fields.
  // The address the backend actually writes. op's --url sets the primary URL,
  // so showing urls[0] meant that editing an item whose primary is not listed
  // first quietly moved the primary to a different address.
  function primaryUrlOf(d) {
    var urls = (d && d.urls) ? d.urls : []
    for (var i = 0; i < urls.length; i++) {
      if (urls[i].primary) return urls[i].href || ""
    }
    return urls.length > 0 ? (urls[0].href || "") : ""
  }

  function startEdit() {
    if (!root.itemDetails) return
    var d = root.itemDetails
    root.forgetCreateForm()
    root.currentView = "create"
    createForm.category = String(d.category || "LOGIN").toUpperCase()
    var values = { title: d.title || "" }
    for (var i = 0; i < (d.fields || []).length; i++) {
      var f = d.fields[i]
      values[f.id] = f.value || ""
    }
    // get_item lifts the note out of `fields` into its own key. Loading only
    // `fields` left the note box empty, and saving then wrote that emptiness
    // back: editing the title of a secure note erased the note.
    if (d.notes) values.notesPlain = d.notes
    values.url = root.primaryUrlOf(d)
    createForm.editingId = d.id
    createForm.loadValues(values)
    root.refreshVaults()
    Qt.callLater(function() { createForm.focusFirst() })
  }

  function submitEdit(payload) {
    if (root.creating) return
    var id = createForm.editingId
    if (!id) return
    root.creating = true
    createForm.submitError = ""
    createForm.busy = true
    var changes = ({})
    for (var k in payload.fields) changes[k] = payload.fields[k].value
    runAction({
      action: "edit_item",
      id: id,
      title: payload.title,
      url: payload.url,
      // Carried through so Generate rotates the password. Dropping it left
      // the form sending the empty box it had cleared, which set the
      // password to nothing.
      generateField: payload.generateField,
      changes: changes
    }, "Saved " + (payload.displayTitle || payload.title))
  }

  // --- removing an item ----------------------------------------------------
  property string pendingDeleteId: ""
  property string pendingDeleteTitle: ""

  function askDelete() {
    if (!root.itemDetails) return
    root.pendingDeleteId = root.itemDetails.id
    root.pendingDeleteTitle = root.itemDetails.title || "this item"
    deleteConfirm.selectedIndex = 0
    deleteConfirm.opened = true
  }

  function confirmDelete() {
    var id = root.pendingDeleteId
    var title = root.pendingDeleteTitle
    root.pendingDeleteId = ""
    deleteConfirm.opened = false
    if (!id) return
    root.backToList()
    runAction({ action: "delete_item", id: id, archive: true },
              "Archived " + title)
  }

  // Empties the form and forgets what it was editing.
  //
  // Called from every path that leaves the form, not just Cancel. The form
  // holds whatever the user typed, and in an edit that is the item's own
  // decrypted password, so it has no reason to outlive the view. Leaving
  // `editingId` set was worse than untidy: the next press of + opened a
  // create form that still carried it, and saving overwrote the item that
  // had been edited before.
  // Bumped every time the form is emptied. A save that finishes after the
  // user has cancelled and started typing something else belongs to a form
  // that no longer exists, and must not clear the one now on screen.
  property int formSession: 0

  function forgetCreateForm() {
    root.formSession += 1
    root.creating = false
    createForm.editingId = ""
    createForm.busy = false
    createForm.submitError = ""
    createForm.reset("")
  }

  function cancelCreate() {
    root.forgetCreateForm()
    // Reached from the details view when editing, which still holds the
    // decrypted item; going back to the list is not a reason to keep it.
    root.forgetDetails()
    root.currentView = "list"
    Qt.callLater(function() { searchInput.forceActiveFocus() })
  }

  Process {
    id: vaultsProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var resp = JSON.parse(text)
          if (resp.ok && resp.vaults) root.vaults = resp.vaults
          if (root.vaults.length > 0 && !createForm.vault) {
            createForm.vault = root.vaults[0].name
          }
        } catch (e) {}
      }
    }
  }

  function refreshVaults() {
    if (vaultsProc.running) return
    root.runHelper(vaultsProc, { action: "vaults" })
  }

  function submitCreate(payload) {
    if (root.creating) return
    root.creating = true
    createForm.submitError = ""
    createForm.busy = true
    runAction({
      action: "create_item",
      category: payload.category,
      title: payload.title,
      url: payload.url,
      vault: payload.vault,
      generateField: payload.generateField,
      fields: payload.fields
    }, "Created " + payload.title)
  }

  function backToList() {
    root.forgetCreateForm()
    // forgetDetails, not a copy of most of it: this repeated eight of its
    // ten lines and left out releasing the collector, so going Back, which
    // is how the details view is usually left, kept the decrypted item in
    // that buffer for as long as the popup stayed open.
    root.forgetDetails()
    root.currentView = "list"
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

  // Generic action runner.
  //
  // Actions are queued rather than dropped: a second click while a copy is in
  // flight used to vanish with no feedback at all.
  property var actionQueue: []
  property var runningAction: null
  property bool lastActionFailed: false

  Process {
    id: actionProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var job = root.runningAction
        root.lastActionFailed = false
        // Recorded on the job itself, not just in a property: finishAction
        // runs from onExited, and it must not read the verdict of whichever
        // action happened to run before this one.
        if (job) job.answered = true
        try {
          var resp = JSON.parse(text)
          if (resp.ok) {
            if (job) job.succeeded = true
            if (job && job.successToast) root.showToast(job.successToast)
          } else {
            root.lastActionFailed = true
            if (job && (job.payload.action === "create_item"
                        || job.payload.action === "edit_item")) {
              createForm.submitError = resp.error || "Could not create the item"
            }
            root.showToast(resp.error || "Action failed")
            // op refuses when the vault relocked under us; reflect that.
            root.checkStatus()
          }
        } catch (e) {
          root.showToast("Could not read helper response")
        }
      }
    }
    // Bumped on every start. The deferred launch-failure check below compares
    // it so a stale callback can never finish a job that already moved on.
    property int generation: 0
    property bool exitSeen: false
    onExited: function(exitCode) {
      actionProc.exitSeen = true
      root.finishAction(exitCode !== 0 ? "Helper failed (exit " + exitCode + ")" : "")
    }
    onRunningChanged: {
      // Quickshell reports a process that failed to launch by clearing
      // `running` without ever emitting `exited`. Without this the current
      // action gets no feedback and the whole queue stalls behind it.
      if (actionProc.running) return
      var gen = actionProc.generation
      var job = root.runningAction
      if (!job) return
      Qt.callLater(function() {
        if (actionProc.generation !== gen) return
        if (actionProc.exitSeen || root.runningAction !== job) return
        root.finishAction("Could not start the helper (python3 missing?)")
      })
    }
  }

  function finishAction(errorText) {
    var job = root.runningAction
    if (!job) return
    root.runningAction = null
    root.busy = false
    if (errorText) root.showToast(errorText)

    var completed = job.payload.action
    // A write changes the vault, so the list on screen is now wrong. Archiving
    // an item left it sitting in the list, a new item did not appear, and a
    // renamed one kept its old title, until something else happened to
    // refresh. The helper updates its cached list before it answers, so this
    // reads back the change rather than racing the background sync.
    if (completed === "create_item" || completed === "edit_item"
        || completed === "delete_item") {
      // The job's own verdict, not root.lastActionFailed: that property
      // belongs to whichever action last parsed a response, and a reply this
      // job could not parse at all leaves it reading as a success.
      if (job.answered === true && job.succeeded === true && !errorText) {
        root.refreshItems()
      }
    }
    if (completed === "create_item" || completed === "edit_item") {
      // Only if the form on screen is still the one that submitted. Otherwise
      // this is an older save landing under a draft the user has since
      // started, and clearing it would throw that draft away.
      if (job.formSession !== root.formSession) {
        Qt.callLater(root.pumpActions)
        return
      }
      // The form has to become usable again either way. Without this both
      // `creating` and `busy` stayed true after the first attempt, and every
      // later submit was dropped without a word.
      root.creating = false
      createForm.busy = false
      if (job.answered === true && job.succeeded === true && !errorText) {
        root.forgetCreateForm()
        // An edit is reached from the details view, which still holds the
        // item decrypted. Saving is not a reason to keep it resident.
        root.forgetDetails()
        root.currentView = "list"
        Qt.callLater(function() {
          if (root.unlocked) searchInput.forceActiveFocus()
        })
      }
    }
    if (completed === "sync") {
      root.refreshItems()
    }
    if (completed === "sync" || completed === "lock" || completed === "unlock") {
      // Only the unlock path forces, since forcing is what prompts 1Password.
      root.checkStatus(completed === "unlock")
    }
    // Deferred: restarting the Process from inside its own exit handler.
    Qt.callLater(root.pumpActions)
  }

  function pumpActions() {
    if (actionProc.running || root.actionQueue.length === 0) return
    var queue = root.actionQueue
    var job = queue.shift()
    root.actionQueue = queue
    root.runningAction = job
    root.busy = true
    actionProc.exitSeen = false
    actionProc.generation++
    root.runHelper(actionProc, job.payload)
  }

  function runAction(payload, successToast) {
    var queue = root.actionQueue
    // Stamped with the form that was on screen when this was asked for, so a
    // save landing late cannot act on a form the user has since replaced.
    queue.push({
      payload: payload,
      successToast: successToast || "",
      formSession: root.formSession
    })
    root.actionQueue = queue
    root.pumpActions()
  }

  function unlock() {
    runAction({ action: "unlock" })
    unlockPollTimer.attempts = 0
    unlockPollTimer.running = true
  }

  function lockVault() {
    runAction({ action: "lock" }, "Vault locked and clipboard cleared")
    root.unlocked = false
    root.items = []
    root.currentView = "list"
    root.forgetDetails()
    root.forgetCreateForm()
  }

  function syncVault() {
    runAction({ action: "sync" }, "Vault synced")
  }

  function clearsIn() {
    return root.clipboardTimeout > 0
      ? " (clears in " + root.clipboardTimeout + "s)"
      : ""
  }

  function copyField(itemId, field, title) {
    var what = field || root.defaultAction
    runAction({
      action: "copy",
      id: itemId,
      field: what,
      title: title || "",
      timeout: root.clipboardTimeout
    }, "Copied " + what + root.clearsIn())
  }

  // Copies a field of the open item by naming it, rather than handing its
  // plaintext back to the helper. The helper already holds the decrypted item;
  // round-tripping the secret through a subprocess only creates exposure.
  function copyDetailField(fieldId, label) {
    if (!root.itemDetails || !root.itemDetails.id) return
    runAction({
      action: "copy",
      id: root.itemDetails.id,
      field: fieldId,
      title: root.itemDetails.title || "",
      timeout: root.clipboardTimeout
    }, "Copied " + (label || fieldId) + root.clearsIn())
  }

  // Values that are not secrets and have no field identity of their own (a
  // website address the user can already read on screen).
  function copyRawValue(val, label, title) {
    if (!val) return
    runAction({
      action: "copy",
      value: val,
      title: title || "",
      field: label || "",
      timeout: root.clipboardTimeout
    }, "Copied " + (label || "value") + root.clearsIn())
  }

  function typeDetailField(fieldId, label) {
    if (!root.itemDetails || !root.itemDetails.id) return
    runAction({
      action: "type",
      id: root.itemDetails.id,
      field: fieldId
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

  // Re-checks the list for a short while after unlocking, because the first
  // sync completes asynchronously and has no way to notify us.
  Timer {
    id: syncSettleTimer
    interval: 1200
    repeat: true
    running: false
    property int attempts: 0
    onTriggered: {
      attempts++
      // The helper allows a 30s sync. Stopping at 26 ticks put the last
      // request at exactly 30s, racing the completion it was waiting for;
      // 32 ticks keeps polling a few seconds past it.
      if (!root.popupOpen || !root.unlocked || root.items.length > 0 || attempts >= 32) {
        running = false
        attempts = 0
        return
      }
      root.refreshItems()
    }
  }

  // Unlock polling timer: only runs temporarily after clicking Unlock until
  // unlocked or ~16s elapsed. These checks must force: an unforced status is
  // answered from the helper's "still locked" cache and could never observe
  // the unlock that just happened.
  Timer {
    id: unlockPollTimer
    interval: 2000
    running: false
    repeat: true
    property int attempts: 0
    onTriggered: {
      attempts++
      if (root.unlocked || attempts >= 8 || !root.popupOpen) {
        running = false
        attempts = 0
      } else {
        root.checkStatus(true)
      }
    }
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
      ? "1Password: CLI (op) not found"
      : root.unlocked
        ? ("1Password: Unlocked" + (root.itemCount > 0 ? " (" + root.itemCount + " items)" : ""))
        : "1Password: Locked (Click to open)"

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
  // KeyboardPanel, not PopupCard.
  //
  // PopupCard is an xdg-popup on the bar's layer surface, and the bar sets
  // keyboardFocus: None, so nothing anchored to it can ever receive a key.
  // The search field could be clicked but never typed into, and every key
  // handler in this file was dead code. KeyboardPanel is the same card on a
  // layer surface that takes focus, which is what Omarchy's own typeable
  // popups (menu, clipboard, wifi passphrase) use.
  KeyboardPanel {
    id: popup
    anchorItem: button
    bar: root.bar
    owner: root
    open: root.popupOpen

    // Qt needs an active-focus target inside the surface before any
    // Keys handler fires, and it differs per view.
    focusTarget: !root.unlocked
      ? lockedColumn.unlockButton
      : (root.currentView === "details"
          ? detailsViewArea
          : (root.currentView === "create" ? createForm : searchInput))

    // KeyboardPanel only focuses focusTarget when the surface maps. Unlocking,
    // locking or switching views swaps the target while the panel stays open,
    // and without this the layer keeps keyboard focus while every handler sits
    // on a hidden item.
    onFocusTargetChanged: if (popup.open && popup.focusTarget) {
      Qt.callLater(function() {
        if (popup.open && popup.focusTarget) popup.focusTarget.forceActiveFocus()
      })
    }
    contentWidth: popup.fittedContentWidth(Style.space(480))
    contentHeight: root.unlocked
      ? popup.cappedContentHeight(Style.space(520))
      : popup.fittedContentHeight(popupContent.implicitHeight)

    onOpenChanged: {
      if (open) {
        root.currentView = "list"
        root.checkStatus()
        if (root.unlocked) {
          root.refreshItems()
        }
        Qt.callLater(function() {
          if (root.unlocked) {
            searchInput.forceActiveFocus()
          } else {
            lockedColumn.unlockButton.forceActiveFocus()
          }
        })
      }
    }

    ColumnLayout {
      id: popupContent
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

        // New item
        Button {
          visible: root.unlocked
          iconText: "\u{f0415}"
          tooltipText: "New login"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: root.startCreate(root.searchQuery)
        }

        // Sync button
        Button {
          visible: root.unlocked
          // A vault sync is a network round trip; without this the button
          // looks inert until the toast arrives seconds later.
          iconText: root.busy ? "󰔟" : "󰑐"
          tooltipText: root.busy ? "Working..." : "Sync vault"
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
          iconText: "󰏫"
          tooltipText: "Edit this item (e)"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: root.startEdit()
        }

        Button {
          iconText: "󰩺"
          tooltipText: "Move to the 1Password archive (Del)"
          accent: root.colAccent
          horizontalPadding: Style.space(6)
          verticalPadding: Style.space(4)
          onClicked: root.askDelete()
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
      LockedCard {
        id: lockedColumn
        visible: !root.unlocked
        theme: appTheme
        account: root.account
        onUnlockRequested: root.unlock()
        onCloseRequested: root.close()
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

              // Chords and navigation keys are taken before the input sees
              // them; anything not claimed here still types normally.
              Keys.onPressed: function(event) {
                if (root.handleReadlineChord(event, false)) {
                  event.accepted = true
                  return
                }
                switch (event.key) {
                case Qt.Key_Home:
                  if (event.modifiers & Qt.ControlModifier) { root.selectIndex(0); event.accepted = true }
                  break
                case Qt.Key_End:
                  if (event.modifiers & Qt.ControlModifier) { root.selectIndex(root.items.length - 1); event.accepted = true }
                  break
                case Qt.Key_PageDown:
                  root.moveSelection(root.pageSize()); event.accepted = true
                  break
                case Qt.Key_PageUp:
                  root.moveSelection(-root.pageSize()); event.accepted = true
                  break
                case Qt.Key_Tab:
                  // Qt delivers Shift+Tab as Key_Tab with the modifier set on
                  // some layouts and as Key_Backtab on others; handle both.
                  root.cycleCategory((event.modifiers & Qt.ShiftModifier) ? -1 : 1)
                  event.accepted = true
                  break
                case Qt.Key_Backtab:
                  root.cycleCategory(-1); event.accepted = true
                  break
                }
              }
              Keys.onEscapePressed: {
                if (searchInput.text.length > 0) {
                  searchInput.text = ""
                } else {
                  root.close()
                }
              }
              Keys.onDownPressed: root.moveSelection(1)
              Keys.onUpPressed: root.moveSelection(-1)
              // Right only opens the item when it is not a text gesture: with
              // a modifier, or with the caret mid-query, it belongs to the
              // search field so selection and caret movement still work.
              Keys.onRightPressed: function(event) {
                var editing = (event.modifiers & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
                  || searchInput.cursorPosition < searchInput.text.length
                if (editing) {
                  event.accepted = false
                  return
                }
                root.activateCurrent()
              }
              Keys.onReturnPressed: function(event) {
                if (event.modifiers & Qt.ShiftModifier) root.copyPrimary()
                else if (event.modifiers & Qt.ControlModifier) root.copyExtra()
                else if (event.modifiers & Qt.AltModifier) root.openCurrentUrl()
                else root.activateCurrent()
              }
              Keys.onEnterPressed: function(event) {
                if (event.modifiers & Qt.ShiftModifier) root.copyPrimary()
                else if (event.modifiers & Qt.ControlModifier) root.copyExtra()
                else if (event.modifiers & Qt.AltModifier) root.openCurrentUrl()
                else root.activateCurrent()
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
                  // onSelectedCategoryChanged already refreshes; calling it
                  // here too fired two helper processes per chip click.
                  root.selectedCategory = modelData.id
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
            // Last pointer position in viewport coordinates, so hover can tell
            // a moved mouse from a moved list.
            property real lastHoverX: -1
            property real lastHoverY: -1
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
                // Hover selects only on real pointer movement. Qt emits
                // positionChanged from hoverEnterEvent as well, so keyboard
                // navigation scrolling a row under a stationary cursor would
                // otherwise yank the selection back to whatever slid under it.
                // Viewport coordinates are used because they do not change
                // when the list scrolls beneath a still pointer.
                onPositionChanged: function(mouse) {
                  var p = mapToItem(itemList, mouse.x, mouse.y)
                  if (Math.abs(p.x - itemList.lastHoverX) < 1
                      && Math.abs(p.y - itemList.lastHoverY) < 1) return
                  itemList.lastHoverX = p.x
                  itemList.lastHoverY = p.y
                  root.selectedIndex = index
                }
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

                // Quick actions, derived from the item's category.
                //
                // A credit card has no password, username or TOTP, so a fixed
                // set of buttons put three dead controls on every card row and
                // every click returned "No username field on this item".
                Row {
                  id: quickActions
                  z: 10
                  spacing: Style.space(4)
                  visible: isSelected || itemMouse.containsMouse

                  readonly property var item: modelData

                  Repeater {
                    // Ordered by root.quickFieldsOrdered so the first button
                    // and Shift+Enter always agree with `defaultAction`.
                    model: root.quickFieldsOrdered(quickActions.item)
                    delegate: Rectangle {
                      required property string modelData
                      readonly property string field: modelData

                      width: Style.space(26)
                      height: Style.space(26)
                      radius: Style.space(4)
                      color: fieldMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

                      Text {
                        anchors.centerIn: parent
                        text: Model.fieldIconFor(field)
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        color: fieldMouse.containsMouse ? Color.background : root.colForeground
                      }

                      MouseArea {
                        id: fieldMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.copyField(quickActions.item.id, field, quickActions.item.title)
                      }
                    }
                  }

                  // Open URL
                  Rectangle {
                    visible: !!quickActions.item.url
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: Style.space(4)
                    color: urlMouse.containsMouse ? root.colAccent : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.12)

                    Text {
                      anchors.centerIn: parent
                      text: "\u{f059f}"
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      color: urlMouse.containsMouse ? Color.background : root.colForeground
                    }

                    MouseArea {
                      id: urlMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.openUrl(quickActions.item.url)
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
                      text: "\u{f0142}"
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      color: detBtnMouse.containsMouse ? Color.background : root.colForeground
                    }

                    MouseArea {
                      id: detBtnMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.showItemDetails(quickActions.item)
                    }
                  }
                }
              }
            }
          }

          // Empty State
          EmptyState {
            anchors.centerIn: parent
            visible: root.items.length === 0
            theme: appTheme
            loading: root.searching
            query: root.searchQuery
            onCreateRequested: function(title) { root.startCreate(title) }
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
            // Elided, not merely fillWidth: without this the hint runs
            // straight through the account label to its right.
            text: root.listHint
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
            elide: Text.ElideRight
          }

          Text {
            visible: !!root.account
            text: root.account
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            color: root.colDim
            elide: Text.ElideMiddle
            Layout.maximumWidth: Style.space(150)
          }
        }
      }

      // ========================================== CREATE HEADER
      RowLayout {
        visible: root.currentView === "create"
        Layout.fillWidth: true
        spacing: Style.space(8)

        Button {
          iconText: "\u{f004d}"
          text: "Back"
          accent: root.colAccent
          horizontalPadding: Style.space(8)
          verticalPadding: Style.space(4)
          onClicked: root.cancelCreate()
        }

        Rectangle {
          width: Style.space(28)
          height: Style.space(28)
          radius: Style.space(6)
          color: Qt.rgba(root.colAccent.r, root.colAccent.g, root.colAccent.b, 0.2)

          Text {
            anchors.centerIn: parent
            text: "\u{f0415}"
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
            text: (createForm.editing ? "Edit " : "New ") + createForm.spec.label.toLowerCase()
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            color: root.colForeground
            elide: Text.ElideRight
          }

          Text {
            Layout.fillWidth: true
            // Only some categories have a field 1Password can generate.
            text: createForm.editing
              ? "Fields not shown here are left exactly as they are"
              : (createForm.canGenerate
                  ? "1Password generates the password unless you type one"
                  : "Stored in your vault, never on disk")
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption - 1
            color: root.colDim
            elide: Text.ElideRight
          }
        }
      }

      // ========================================== UNLOCKED: CREATE VIEW
      CreateForm {
        id: createForm
        visible: root.unlocked && root.currentView === "create"
        theme: appTheme
        vaults: root.vaults
        focus: visible
        onVisibleChanged: if (visible) forceActiveFocus()
        vaultsLoading: vaultsProc.running
        onVaultsRequested: root.refreshVaults()
        onCancelled: root.cancelCreate()
        onSubmitted: function(payload) {
          if (createForm.editingId) root.submitEdit(payload)
          else root.submitCreate(payload)
        }
      }


      // ========================================== UNLOCKED: DETAILS VIEW
      Item {
        id: detailsViewArea
        visible: root.unlocked && root.currentView === "details"
        Layout.fillWidth: true
        Layout.fillHeight: true

        // The search field owns the keyboard in the list view and is hidden
        // here, so without this the details view has no key handling at all.
        focus: visible
        // Each guarded, because Keys.onPressed below guards the same way and
        // which of the two Qt runs first is not something this file should
        // depend on. Unguarded, Enter here copied the field behind the
        // confirmation instead of answering it.
        Keys.onEscapePressed: if (!deleteConfirm.opened) root.backToList()
        Keys.onLeftPressed: if (!deleteConfirm.opened) root.backToList()
        Keys.onDownPressed: if (!deleteConfirm.opened) root.moveDetailField(1)
        Keys.onUpPressed: if (!deleteConfirm.opened) root.moveDetailField(-1)
        Keys.onReturnPressed: if (!deleteConfirm.opened) root.copyFocusedDetailField()
        Keys.onEnterPressed: if (!deleteConfirm.opened) root.copyFocusedDetailField()
        Keys.onPressed: function(event) {
          // While the confirmation is up it owns the keyboard, or Escape and
          // Enter would act on the item behind it.
          if (deleteConfirm.opened) {
            event.accepted = deleteConfirm.handleKey(event)
            return
          }
          if (root.handleReadlineChord(event, true)) {
            event.accepted = true
            return
          }
          switch (event.key) {
          case Qt.Key_R: root.revealFocusedDetailField(); event.accepted = true; break
          case Qt.Key_T: root.typeFocusedDetailField(); event.accepted = true; break
          case Qt.Key_C: root.copyFocusedDetailField(); event.accepted = true; break
          case Qt.Key_W:
            if (root.itemDetails && root.itemDetails.urls && root.itemDetails.urls.length > 0) {
              root.openUrl(root.itemDetails.urls[0].href)
            }
            event.accepted = true
            break
          case Qt.Key_E: root.startEdit(); event.accepted = true; break
          case Qt.Key_Delete: root.askDelete(); event.accepted = true; break
          case Qt.Key_Home: root.detailIndex = 0; event.accepted = true; break
          case Qt.Key_End: root.detailIndex = Math.max(0, root.detailFields.length - 1); event.accepted = true; break
          }
        }
        onVisibleChanged: if (visible) forceActiveFocus()

        // Loading Indicator (Pixel-Perfect Mathematical Centering)
        ColumnLayout {
          visible: root.loadingDetails
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

        // Error State (Centered)
        ColumnLayout {
          visible: !root.loadingDetails && !!root.detailsError
          anchors.centerIn: parent
          spacing: Style.space(10)

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "󰅙"
            font.family: root.fontFamily
            font.pixelSize: Style.space(34)
            color: Color.urgent
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

        // Scrollable Details Body and Content Footer
        ColumnLayout {
          visible: !root.loadingDetails && !root.detailsError && !!root.itemDetails
          anchors.fill: parent
          spacing: Style.space(8)

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
              visible: root.itemHasTotp
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
                    text: root.totpStale
                      ? "ONE-TIME PASSWORD (EXPIRED)"
                      : (root.totpSecondsLeft > 0
                        ? "ONE-TIME PASSWORD (TOTP), " + root.totpSecondsLeft + "s LEFT"
                        : "ONE-TIME PASSWORD (TOTP)")
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption - 1
                    font.bold: true
                    color: root.totpStale ? Color.urgent : root.colAccent
                  }

                  Text {
                    // Dimmed once the window has passed: a code that looks
                    // crisp but no longer works is worse than one that says so.
                    text: root.currentTotp
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.heading
                    font.bold: true
                    color: root.totpStale ? root.colDim : root.colForeground
                  }
                }

                Button {
                  iconText: "󰆏"
                  text: "Copy"
                  accent: root.colAccent
                  horizontalPadding: Style.space(10)
                  verticalPadding: Style.space(4)
                  onClicked: {
                    // Fetched fresh rather than copying the displayed code:
                    // by the time this is clicked that code may have rolled.
                    var id = root.itemDetails ? root.itemDetails.id : ""
                    if (id) root.copyField(id, "otp", root.itemDetails.title)
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
              // The same list the keyboard walks, so `index` here and
              // root.detailIndex mean the same row.
              model: root.detailFieldCards
              delegate: FieldCard {
                theme: appTheme
                focusedIndex: root.detailIndex
                revealed: root.detailIndex === index && root.detailRevealed
                onFocusRequested: function(i) { root.detailIndex = i }
                onToggleRevealRequested: root.detailRevealed = !root.detailRevealed
                onCopyRequested: function(fieldId, label) { root.copyDetailField(fieldId, label) }
                onTypeRequested: function(fieldId, label) { root.typeDetailField(fieldId, label) }
                onVisibilityRequested: function(y, h) { root.ensureDetailVisible(y, h) }
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
              id: notesCard
              // Notes are the last entry in detailFields, so they take the
              // focus ring like any other field rather than being a card the
              // keyboard could act on but never highlight.
              readonly property bool focused: root.notesFocused

              visible: !!(root.itemDetails && root.itemDetails.notes && String(root.itemDetails.notes).trim().length > 0)
              Layout.fillWidth: true
              Layout.preferredHeight: Math.min(Style.space(130), Math.max(Style.space(64), notesText.implicitHeight + Style.space(24)))
              radius: Style.cornerRadius
              color: focused
                ? Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.08)
                : Qt.rgba(root.colForeground.r, root.colForeground.g, root.colForeground.b, 0.04)
              borderSpec: Border.controlSpec(focused ? "focus" : "normal", root.colForeground, root.colAccent)
              onFocusedChanged: if (focused) root.ensureDetailVisible(y, height)

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
                    onClicked: root.copyDetailField("notes", "Notes")
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
                    font.family: root.fontFamily
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
              visible: !!(root.itemDetails && (!root.itemDetails.fields || root.itemDetails.fields.length === 0) && !root.itemDetails.notes && (!root.itemDetails.urls || root.itemDetails.urls.length === 0) && !root.itemHasTotp)
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

      // Confirmation of the last action, at the popup root so a copy made from
      // the list is confirmed too.
      Toast {
        theme: appTheme
        message: root.statusToast
      }
    // Overlay, not a layout child: ConfirmDialog paints a scrim across its
    // parent, so inside the column it was laid out with no size and never
    // appeared.
    // Removing an item changes someone's vault in a way they might not have
    // meant, so it asks first, and it archives rather than deletes, which
    // the 1Password app can undo.
    ConfirmDialog {
      id: deleteConfirm
      anchors.fill: parent
      z: 100
      opened: false
      message: "Move \"" + root.pendingDeleteTitle + "\" to the 1Password archive?"
      confirmText: "Archive"
      cancelText: "Keep"
      background: root.colSurface
      foreground: root.colForeground
      // ConfirmDialog defaults selectedIndex to 1, which is the confirm
      // button: as shipped, opening this and pressing Enter archived the item.
      // Reset on every open, since Left/Right moves it.
      selectedIndex: 0
      onConfirmed: root.confirmDelete()
      onCanceled: {
        root.pendingDeleteId = ""
        root.pendingDeleteTitle = ""
        deleteConfirm.opened = false
      }
    }

  }
}
}
