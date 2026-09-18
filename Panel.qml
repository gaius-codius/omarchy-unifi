// The bar button and its popup, from one file.
//
// The entry point is named Panel.qml, not BarWidget.qml: this plugin ships a
// bar item *and* a popup, which is what qs.Ui.Panel is the base class for
// (Ui/Panel.qml:9-59). The manifest's entryPoints.barWidget names this file.
//
// Two rules govern everything below.
//
// DATA-003a: a bar widget receives ONLY `bar`, `moduleName` and `settings`, and
// `bar` is null on the first frame. The service is therefore always reached
// through optional chaining, and a null result renders REQ-013b's
// `service_unavailable` rather than throwing or blanking (AC-067).
//
// REQ-014 / UX-011: this file holds no state that could differ between
// monitors. It renders `service.viewModel` and nothing else. "Every monitor
// shows identical state" is then a consequence of there being one service,
// rather than of every widget happening to agree.
//
// REQ-007a is deliberately NOT implemented here. KeyboardPanel already calls
// bar.requestPopout / releasePopout (Ui/KeyboardPanel.qml:240, :246) against
// the single global Bar (Bar.qml:83, :316-327), so at most one panel is open
// across all monitors for free. Do not add arbitration; a second coordinator is
// the bug AC-068 would find.
import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "ViewModel.js" as ViewModel

Panel {
  id: root

  moduleName: "gaius-codius.unifi"
  ipcTarget: ""          // DATA-010: the *service* owns the IPC target.
  manageIpc: false

  // Ui/Panel declares `bar` as QtObject, so qmllint cannot see that the real
  // Bar item carries `shell`. The access is correct at runtime and the optional
  // chaining is mandatory (DATA-003a). The suppression is scoped to this one
  // line rather than lowering the category, which would stop the gate catching
  // genuine typos everywhere else.
  // qmllint disable missing-property
  readonly property var unifiService: bar?.shell?.serviceFor("gaius-codius.unifi") ?? null
  // qmllint enable missing-property

  // REQ-013b / AC-067. `forNullService()` returns the SAME SHAPE `build()`
  // does, so no binding below ever sees `undefined` — not on the first frame,
  // and not if the service genuinely failed to construct, which the host
  // surfaces only as a console.warn nobody reads.
  readonly property var vm: (unifiService && unifiService.viewModel)
    ? unifiService.viewModel : ViewModel.forNullService()

  // qmllint disable missing-property
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // qmllint enable missing-property
  // The one muted colour, from the one scale. It was `Qt.darker(foreground,
  // 1.4)` — the host's convention — which does not mute at all on a light
  // theme. See `Emphasis.qml` for the arithmetic.
  readonly property color dim: emphasisTokens.tertiary

  // REQ-B15 (extending UX-008). The stop ORDER, the wrap and what happens to
  // the cursor when a page change removes the stop it is on all live in
  // `ViewModel.js` as pure functions — a state machine reachable only through a
  // five-minute live harness is one that gets tested once. What is here is the
  // current stop and what each stop does when activated.
  property string focusStop: ViewModel.homeFocus(focusState)

  // What is on screen, as far as the stop list cares (SPEC-AMD-13). A stop
  // that is not drawn is one Tab lands on with no ring and Enter does nothing
  // on — the segmented control before the first snapshot, a list a search has
  // emptied — so the stop list is built from this rather than from the page
  // alone, and a change here moves a cursor that was on a stop that went away.
  readonly property var focusState: ({
    hasSnapshot: vm.hasSnapshot,
    hasList: view === "overview" ? inventoryCount > 0
      : (activeList ? activeList.rows.length > 0 : false)
  })
  onFocusStateChanged: {
    var settled = ViewModel.settleFocus(view, focusStop, focusState)
    if (settled !== focusStop) focusStop = settled
  }

  // The Inventory rows are Overview's list: two totals, then a row per role.
  // Walked with Up/Down while the list stop has focus, opened with Enter,
  // exactly as a browse page's rows are.
  readonly property int inventoryCount: vm.hasSnapshot ? 2 + vm.countRows.length : 0
  property int inventoryCursor: 0

  // REQ-B10's view state, passed into `build` rather than held by it. The panel
  // owns it because it is the panel's: which page is showing, what is typed in
  // each search field, which row is open, and which row the cursor is on.
  property string view: "overview"
  property string deviceSearch: ""
  property string clientSearch: ""
  property string roleFilter: ""
  // REQ-B10a (SPEC-AMD-10). The Clients page's axis. Two properties and not one
  // shared string: the pages filter on different things, so a single one would
  // carry "WIRED" into the Devices page's role filter the moment the user
  // switched — which, now that a filter SURVIVES a page change, would silently
  // empty the other list.
  property string typeFilter: ""
  property string expandedDeviceId: ""
  property string expandedClientId: ""
  property int deviceCursor: 0
  property int clientCursor: 0

  // Where the pointer was the last time a row reported a hover, in scene
  // coordinates. `hoverList` is the only writer and `ViewModel.focusAfterHover`
  // the only reader: it is the evidence that the pointer MOVED, which nothing
  // else in a hover event carries — a delegate rebuilt under a stationary
  // pointer raises the same signals at the same coordinates as a real
  // movement. Null when the panel has heard from no row yet.
  property var lastHoverPoint: null

  // One instance, passed down rather than instantiated per delegate — a
  // 200-row list would otherwise allocate 200 copies of three constants.
  readonly property Emphasis emphasis: emphasisTokens
  // Bound to the BAR's foreground, not the global one — the panel takes its
  // colours from the bar it belongs to (`foreground` above does the same), so a
  // scale built from `Color.foreground` would drift from everything around it.
  Emphasis { id: emphasisTokens; foreground: root.foreground }

  // The two disclosures at the foot of the panel (REQ-B10's view state, same
  // rule: the panel owns what the panel shows).
  //
  // Both were permanent blocks. Details is five rows of site id, controller
  // host, helper version and config generation — read once, during setup, and
  // never again — sitting between the content and the actions on every page.
  // The keyboard legend is two wrapped lines naming keys, at the very bottom:
  // too far down for the newcomer who needs it, always present for the expert
  // who does not, and the only place `f` was ever mentioned.
  //
  // Reset with the rest of the browse state when the panel closes, because
  // reopening should answer the health question rather than resume a rummage.
  //
  // Details has a default and a choice. The default is open in any state that
  // is not `ok` — `sentence` is non-empty in exactly those — and closed
  // otherwise; `detailsChoice` is the user's click or `d`, null until they
  // make one. A plain `detailsOpen || sentence !== ""` could not be closed
  // during an error, and the click it swallowed resurfaced as Details open
  // once the error cleared. The choice is dropped when the sentence changes,
  // so a new error still opens it.
  property var detailsChoice: null
  property bool keysOpen: false
  readonly property bool detailsExpanded: detailsChoice !== null
    ? detailsChoice : vm.sentence !== ""
  readonly property string currentSentence: vm.sentence
  onCurrentSentenceChanged: detailsChoice = null
  function toggleDetails() { detailsChoice = !detailsExpanded }

  // REQ-B24 (SPEC-AMD-6). The key of the value most recently copied, so the row
  // that was clicked can confirm it. Cleared by everything the user might do
  // next, which is why there is no Timer: a confirmation that expires on a
  // deadline can vanish mid-read, and one that waits for the next action
  // cannot.
  property string copiedKey: ""

  // The ONLY place this plugin touches the clipboard, so there is one line to
  // audit against REQ-B20.
  //
  // `Quickshell.clipboardText` is a writable property on the Quickshell
  // singleton (HC-21). Deliberately NOT `execDetached(["wl-copy", value])`,
  // which is what the host's own clipboard plugin uses: `no_exec_for_dashboard`
  // forbids a launcher in any panel file for REQ-012, and loosening a security
  // gate to gain a copy button would be a bad trade. It is also better on its
  // own terms — a property assignment never becomes a command line, so a MAC
  // address never appears in /proc/<pid>/cmdline.
  //
  // The value is never logged. REQ-B20 permits it on the clipboard by the
  // user's own action and nowhere else, and a console.log here would be the
  // "nowhere else".
  function copyValue(key, text) {
    if (typeof text !== "string" || text === "") return "empty"
    Quickshell.clipboardText = text
    copiedKey = key
    return "copied"
  }

  // Every exit from the state the confirmation describes, and the two focus
  // duties a page change carries.
  // Three things on one signal, because QML permits exactly one handler per
  // signal and a second `onViewChanged` further down the file is not a second
  // handler — it is "Property value set multiple times", which loads as an
  // error and takes the whole panel with it.
  onViewChanged: {
    copiedKey = ""
    // REQ-B15. The page being hidden may still hold the caret: Qt clears focus
    // when an item is DISABLED, not when it is made invisible. So clicking the
    // Clients chip while typing in the Devices search left the caret in a
    // search box nobody could see — every keystroke filtering the page that is
    // not on screen, `PanelKeyCatcher` blocked throughout because the panel
    // correctly reported that a field had focus, and no key reaching the panel
    // until the user clicked their way out.
    releaseHiddenSearch()
    // REQ-B15 / UX-008: a page change can move the focus stop without changing
    // it (the same stop on a taller page sits somewhere else), so the reveal is
    // driven from here as well as from `onFocusStopChanged`.
    Qt.callLater(revealFocused)
  }

  // Which is only ever the page that is NOT showing: a field on the page in
  // front of the user keeps the caret it was given.
  function releaseHiddenSearch() {
    if (deviceBrowse && deviceBrowse.editing && view !== "devices") {
      deviceBrowse.releaseSearch()
    }
    if (clientBrowse && clientBrowse.editing && view !== "clients") {
      clientBrowse.releaseSearch()
    }
  }

  onExpandedDeviceIdChanged: copiedKey = ""
  onExpandedClientIdChanged: copiedKey = ""

  readonly property bool browsing: view !== "overview"

  // The segmented control is hidden when there is no reading, because Devices
  // and Clients over no snapshot are two empty pages and a third that explains
  // why — and the explanation is the one worth being on.
  //
  // Which means losing the snapshot while browsing would strand the user on an
  // empty page with the only way back hidden. A reload (DATA-011) does exactly
  // that, on purpose. So the panel goes back to Overview itself.
  readonly property bool hasSnapshot: vm.hasSnapshot
  onHasSnapshotChanged: if (!hasSnapshot && browsing) setView("overview")

  // Bound rather than tracked, so it cannot disagree with where the caret
  // actually is. `PanelKeyCatcher.blocked` reads this.
  readonly property bool searchHasFocus: (deviceBrowse && deviceBrowse.editing)
    || (clientBrowse && clientBrowse.editing)

  // REQ-B15. When the caret leaves the search field, the keyboard goes BACK to
  // the key catcher.
  //
  // `QQuickItem.focus = false` — which is all `BrowseList.releaseSearch` can do
  // — clears focus within the scope and hands it to NOBODY: the window's
  // contentItem is left holding active focus, and `keyCatcher` is a descendant
  // of it, so nothing reaches it. Key events travel down a focus chain, not
  // down the item tree. So Tab out of the search field and Escape, the arrows,
  // Enter, `r`, `f` and hjkl all stopped working for the rest of the panel's
  // life; the only way out was to click outside it and let the dismiss area
  // close it. `KeyboardPanel.focusTarget` covers the panel OPENING and nothing
  // after it.
  //
  // Bound to where the caret is rather than added to the one function that
  // moves it, because Tab is not the only way out: `releaseHiddenSearch` above
  // is another, and it is not the field's own doing. One handler on the state
  // itself cannot be the half of the pair that a later exit forgets.
  //
  // This is the host's own remedy for its inline editor, at
  // network/Panel.qml:351-358 (host-contract §7), deferral included.
  onSearchHasFocusChanged: if (!searchHasFocus && opened) Qt.callLater(restoreKeyFocus)

  // Deferred a turn, as the host defers its own: the focus change is being made
  // from inside the notification of the focus change that provoked it, and
  // taking focus there fights whatever else is still reacting to the same
  // signal. Re-tested rather than trusted, because by the time it runs `/` may
  // have handed the field the keyboard back.
  function restoreKeyFocus() {
    if (!opened || searchHasFocus || !keyCatcher) return
    keyCatcher.forceActiveFocus()
  }

  readonly property var activeList: view === "devices" ? vm.deviceList
    : view === "clients" ? vm.clientList : null
  readonly property int activeCursor: view === "devices" ? deviceCursor : clientCursor

  // Pushed to the service, which is the single composition site: one place
  // decides what the panel shows and it is not the panel (REQ-014). A computed
  // object, so each of the five properties it names is a dependency and none
  // can change without the model being rebuilt from it.
  //
  // What is deliberately ABSENT is as considered as what is here. The two
  // cursors decide which row is highlighted, which is the view's business, and
  // listing them would rebuild the whole model on every arrow key for a change
  // no model field reflects. `view` is absent for the same reason once it
  // stopped being published (N-104): both lists are built on every recompute
  // regardless of which is on screen, so a page switch changes nothing the
  // model would compute differently — and `role`, which a page switch CAN
  // carry, is listed.
  readonly property var browseState: ({
    deviceSearch: deviceSearch,
    clientSearch: clientSearch,
    role: roleFilter,
    type: typeFilter,
    expandedDeviceId: expandedDeviceId,
    expandedClientId: expandedClientId
  })

  onBrowseStateChanged: if (unifiService) unifiService.browse = browseState
  // The service may arrive after the panel does — `serviceFor` is null on the
  // first frame (REQ-013b) — so the push is repeated when it appears rather
  // than only when the state changes.
  onUnifiServiceChanged: if (unifiService) unifiService.browse = browseState

  // The bar sizes its slot from the button, and the button sizes itself from
  // `slotSize` before the icon component has been loaded — so REQ-005's compact
  // text has to be measured outside the component that draws it.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // AC-011: the launcher is a property so a harness can substitute one and
  // observe the call. The production value is the ONLY Qt.openUrlExternally in
  // the repository (REQ-012), and tests/lint/no_exec_for_dashboard.sh proves
  // there is no Process, execDetached, execArgv or omarchy-launch-browser path
  // anywhere in the QML layer.
  property var urlOpener: function (url) { Qt.openUrlExternally(url) }

  // REQ-011 / REQ-018. Refusing here rather than in the service would be a
  // second copy of REQ-018a's rule; `vm.refreshEnabled` is the service's own
  // answer, and `requestRefresh()` refuses again on its own account (AC-038).
  function doRefresh() {
    if (!vm.refreshEnabled) return "disabled"
    if (!unifiService) return "unavailable"
    return unifiService.requestRefresh()
  }

  // REQ-012 / SEC-009 / UX-010. Every acceptance decision — scheme, userinfo,
  // character set, the https://<apiRootHost> fallback — was made in
  // ViewModel.acceptDashboardUrl before this ran. This function opens what it
  // is handed or refuses; it does not parse.
  function openDashboard() {
    var dashboard = vm.dashboard
    if (!dashboard || !dashboard.accepted) return "rejected"
    urlOpener(dashboard.url)
    return "opened"
  }

  function moveFocus(direction) {
    if (direction === 0) return
    focusStop = ViewModel.nextFocus(view, focusStop, direction, focusState)
  }

  function moveCursor(dx, dy) {
    // REQ-B15 as amended (SPEC-AMD-5): one meaning per key, and no meaning that
    // depends on a focus stop the user cannot see.
    //
    // It used to be modal — Left/Right moved the segmented control on ONE stop
    // and fell through to "next Tab stop" on the other four. The fallthrough
    // was this file's invention, defended in a comment as better than a key
    // that did nothing. It was not: pressing Right in the list walked focus out
    // of the list silently, and two more presses started changing pages, which
    // is exactly how it was reported. A key that does nothing reads as "not
    // applicable here"; a key that quietly moves your focus and then changes
    // the page reads as broken.
    if (dx !== 0) {
      setView(ViewModel.nextBrowseView(view, dx))
      return
    }
    if (dy === 0) return
    // Vertical stays where it is useful: inside the list when the list has the
    // cursor, walking the stops when it does not — on Overview there is no list
    // for "down" to mean anything else.
    if (focusStop === ViewModel.FOCUS_LIST) {
      moveListCursor(dy)
      return
    }
    moveFocus(dy)
  }

  // The mouse landing on a row puts the panel cursor there, so the two never
  // point at different rows. Whether it also moves the focus STOP is
  // `ViewModel.focusAfterHover`, and the answer is no longer always yes: a
  // hover arrives when the user aims at a row, and it arrives again when the
  // caret is in the search field with the mouse merely resting, and again when
  // the freshness tick rebuilds the delegates under a hand that has not moved.
  // Only the first of the three is a reason to take the ring off the control
  // the keyboard is on. The cursor follows all three, because pointing at a row
  // and pressing Enter must still open the row that was pointed at.
  function hoverList(index, at) {
    if (!browsing) return
    focusStop = ViewModel.focusAfterHover(focusStop, searchHasFocus,
                                          lastHoverPoint, at)
    // Recorded whichever way that went. It is where the pointer IS, not what
    // was decided about it, and a position dropped on a refused hover is a
    // position the next poll's re-delivered hover would read as a movement.
    lastHoverPoint = at
    if (view === "devices") deviceCursor = index
    else clientCursor = index
  }

  function moveListCursor(delta) {
    if (view === "overview") {
      if (inventoryCount === 0) return
      inventoryCursor = Math.min(inventoryCount - 1, Math.max(0, inventoryCursor + delta))
      return
    }
    if (!activeList) return
    var count = activeList.rows.length
    if (count === 0) return
    var next = Math.min(count - 1, Math.max(0, activeCursor + delta))
    if (view === "devices") deviceCursor = next
    else clientCursor = next
  }

  // REQ-B10 / REQ-B10a. Switching pages moves the focus stop only when the one
  // it is on does not exist on the new page.
  //
  // SPEC-AMD-10: a filter now SURVIVES a page change. It used to be cleared by
  // every one of them, and the comment here defended that — "a filter that
  // outlived the row that set it would show an empty Devices page with nothing
  // saying why". That was true when Overview's count rows were the only way to
  // set one and nothing on the page said it was filtered. Both halves have since
  // changed: the page carries its own chooser, and the selected chip says what
  // is on. Clearing it on the way past Clients and back is now only work the
  // user has to redo.
  //
  // `role` is still honoured when passed, which is how the Overview rows set it.
  function setView(next, role) {
    var wanted = ViewModel.browseView(next)
    view = wanted
    if (role !== undefined) roleFilter = role
    focusStop = ViewModel.focusAfterViewChange(wanted, focusStop, focusState)
  }

  // Which axis the page on screen filters on. Overview has none.
  function currentFilter() {
    return view === "devices" ? roleFilter : view === "clients" ? typeFilter : ""
  }

  // The cursor goes back to the top, as it does on a search: the row it was on
  // has very likely just been filtered away, and a cursor left pointing past the
  // end of a shortened list is the defect `setSearch` already avoids.
  function setFilter(value) {
    if (view === "devices") { roleFilter = value; deviceCursor = 0 }
    else if (view === "clients") { typeFilter = value; clientCursor = 0 }
  }

  // REQ-B15 / SPEC-AMD-9, generalised by SPEC-AMD-10. The chip list is the
  // MODEL's, so the order `f` walks and the order the chips are drawn in cannot
  // drift apart, and an option the site does not have is skipped by both for one
  // reason rather than two.
  function cycleFilter() {
    if (!browsing) return
    var list = view === "devices" ? vm.deviceList : vm.clientList
    setFilter(ViewModel.nextFilter(list.filterChips, currentFilter()))
  }

  // REQ-B15 / UX-008. The item the keyboard is on, or null.
  //
  // By stop rather than by `activeFocus`: this panel drives its cursor with a
  // string and never gives Qt focus to most of these controls (the segmented
  // control is `focusable: false`), so `Window.activeFocusItem` would be the
  // search field or nothing at all.
  function focusedItem() {
    if (focusStop === ViewModel.FOCUS_SEGMENTS) return segmentsFrame
    if (focusStop === ViewModel.FOCUS_REFRESH) return refreshButton
    if (focusStop === ViewModel.FOCUS_DASHBOARD) return dashboardButton
    if (view === "overview" && focusStop === ViewModel.FOCUS_LIST)
      return statusPanel.inventoryItem
    var browse = activeBrowse()
    if (browse === null) return null
    if (focusStop === ViewModel.FOCUS_SEARCH) return browse.searchItem
    if (focusStop === ViewModel.FOCUS_LIST) return browse.listItem
    return null
  }

  // Scroll the panel so the focused control is on screen. WHERE to scroll to is
  // `ViewModel.scrollToReveal` — the four cases and two clamps are arithmetic,
  // and this file decides nothing (REQ-014). All this does is measure.
  function revealFocused() {
    var item = focusedItem()
    if (item === null || item === undefined || !item.visible) return
    var pos = item.mapToItem(panelFlick.contentItem, 0, 0)
    panelFlick.contentY = ViewModel.scrollToReveal(
      pos.y, item.height, panelFlick.contentY, panelFlick.height,
      panelFlick.contentHeight, Style.spacing.md)
  }

  // Deferred a turn, for the reason the list's own `keepCurrentVisible` is
  // (bluetooth/Panel.qml records the same): a stop change often arrives WITH a
  // page change, and on that frame the page being revealed has not been laid
  // out — its `visible` is still false and every position is stale. Measuring
  // then scrolls to where the control used to be.
  onFocusStopChanged: Qt.callLater(revealFocused)

  // REQ-B14: one row expanded at a time, and activating the open row closes it.
  function toggleExpanded(id) {
    if (view === "devices") {
      expandedDeviceId = expandedDeviceId === id ? "" : id
    } else if (view === "clients") {
      expandedClientId = expandedClientId === id ? "" : id
    }
  }

  function activateFocused() {
    if (focusStop === ViewModel.FOCUS_REFRESH) return doRefresh()
    if (focusStop === ViewModel.FOCUS_DASHBOARD) return openDashboard()
    if (focusStop === ViewModel.FOCUS_SEARCH) {
      var field = activeBrowse()
      if (field) field.focusSearch()
      return "searching"
    }
    if (focusStop === ViewModel.FOCUS_LIST && view === "overview") {
      if (inventoryCursor >= inventoryCount) return "empty"
      statusPanel.activateRow(inventoryCursor)
      return "navigated"
    }
    if (focusStop === ViewModel.FOCUS_LIST) {
      if (!activeList || activeCursor >= activeList.rows.length) return "empty"
      toggleExpanded(activeList.rows[activeCursor].id)
      return "toggled"
    }
    // The segmented control: Enter on a chip is the chip being chosen, and the
    // chip under the cursor IS the current view, so there is nothing to do.
    return "view"
  }

  // REQ-B15. Escape clears a non-empty search before it closes the panel. The
  // case where the search field itself has focus is handled inside
  // `BrowseList` — `PanelKeyCatcher` is blocked then and never sees the key.
  function escapePressed() {
    if (browsing && currentSearch() !== "") {
      clearSearch()
      return "cleared"
    }
    close()
    return "closed"
  }

  // By NAME, not by current view, so a caller can reach a page's search field
  // when that page is not the one showing — which is exactly the case REQ-B10's
  // "clears any search text when it closes" has to be checked in, because
  // closing has already returned the panel to Overview by then.
  function browseFor(name) {
    return name === "devices" ? deviceBrowse : name === "clients" ? clientBrowse : null
  }

  function activeBrowse() { return browseFor(view) }

  function currentSearch() {
    return view === "devices" ? deviceSearch : view === "clients" ? clientSearch : ""
  }

  // Called BY the field, when the user types. It records what was typed and
  // does not write back — see `clearSearch` for the other direction.
  function setSearch(text) {
    if (view === "devices") { deviceSearch = text; deviceCursor = 0 }
    else if (view === "clients") { clientSearch = text; clientCursor = 0 }
  }

  // Called AT the field, when the panel clears the search on its own account:
  // Escape with the list focused, and REQ-B10's reset when the panel closes.
  // The field is uncontrolled, so setting the model alone would leave the
  // user's text on screen above a list that is no longer filtered by it.
  function clearSearch() {
    if (deviceBrowse) deviceBrowse.setSearchText("")
    if (clientBrowse) clientBrowse.setSearchText("")
    deviceSearch = ""
    clientSearch = ""
    deviceCursor = 0
    clientCursor = 0
  }

  // REQ-B10: the panel returns to Overview and clears both searches when it
  // closes. The widget's job is health, and reopening it should answer that
  // question rather than resume a browse.
  onOpenedChanged: if (!opened) resetBrowse()

  function resetBrowse() {
    copiedKey = ""
    detailsChoice = null
    keysOpen = false
    clearSearch()
    view = "overview"
    roleFilter = ""
    typeFilter = ""
    expandedDeviceId = ""
    expandedClientId = ""
    deviceCursor = 0
    clientCursor = 0
    inventoryCursor = 0
    focusStop = ViewModel.homeFocus(focusState)
    // Forgotten with the rest of the browse state. A position held across a
    // close would be compared against the pointer's position in a session that
    // has nothing to do with it — and if the panel reopened under a motionless
    // mouse, the first genuine hover of the new browse would be read as none.
    lastHoverPoint = null
  }

  // REQ-001a lives in exactly one file. The panel hero needs the same
  // descriptor-to-token mapping the bar item uses, so it shares the evaluator
  // rather than restating it. A second copy of the table is how a theme change
  // ends up half applied — the bar item re-themed and the hero still on the old
  // palette.
  readonly property HealthColor health: HealthColor { bar: root.bar }

  TextMetrics {
    id: compactMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.bar.iconFont
    text: root.vm.compactText
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // REQ-006. The tooltip states the condition in words, which is what makes
    // UX-002's "colour is never the sole signal" true for the bar item.
    tooltipText: root.vm.tooltip
    slotSize: Style.bar.iconSlot
      + (root.vm.compactText === "" ? 0 : compactMetrics.width + Style.space(4))

    iconComponent: Component {
      BarItem {
        anchors.centerIn: parent
        vm: root.vm
        bar: root.bar
      }
    }

    // REQ-007. Middle-click refreshes, matching the house idiom
    // (tailscale/Panel.qml:397). There is no right-click action: every write is
    // forbidden by BIZ-001, so there is nothing to put there that would not be
    // a lie about what this plugin can do.
    onPressed: function (buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.doRefresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      objectName: "unifi-key-catcher"
      anchors.fill: parent

      // REQ-B15. While the search field holds focus the panel's own key
      // handling is suspended — otherwise typing "ap" drives the panel cursor
      // instead of filtering. `PanelKeyCatcher.blocked` forwards every key to
      // descendants without emitting a signal, and its own documentation names
      // this exact case (host-contract §7).
      blocked: root.searchHasFocus

      onCloseRequested: root.escapePressed()
      onTabRequested: function (direction) { root.moveFocus(direction) }
      onMoveRequested: function (dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateFocused()
      onTextKey: function (t) {
        // REQ-B15: "/" focuses the search field. Checked before "r", because a
        // panel that refreshed on a keystroke meant for the search box would be
        // both surprising and a request the user did not make.
        if (t === "/") {
          if (!root.browsing) root.setView("devices")
          root.focusStop = ViewModel.FOCUS_SEARCH
          var field = root.activeBrowse()
          if (field) field.focusSearch()
          return
        }
        if (t === "r" || t === "R") { root.doRefresh(); return }
        // REQ-B15 (SPEC-AMD-9/10). `f` cycles the filter of whichever browse
        // page is showing — the keyboard half of a chooser that is deliberately
        // not a Tab stop. Announced in the hint line below, as `/` and `r` are.
        if (t === "f" || t === "F") { root.cycleFilter(); return }
        // The legend's own key. The disclosure at the foot of the panel is
        // labelled "?  keys", which reads as an instruction to press `?` — and
        // it only answered a click, so the one key the panel names on screen
        // did nothing.
        if (t === "?") { root.keysOpen = !root.keysOpen; return }
        // Details' key, for the reason `?` has one: it is a disclosure that
        // holds something worth copying (the site id, REQ-B24), and a control
        // only a pointer can open is one a keyboard user cannot reach.
        if (t === "d" || t === "D") root.toggleDetails()
      }

      Flickable {
        id: panelFlick
        // AC-B17 measures the viewport by name: "the focused control is on
        // screen" is a claim about this Flickable, not about `visible`.
        objectName: "unifi-panel-flick"
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          // The gap between blocks — hero, pages, content, foot. It was `md`,
          // the same 6 px that separates rows INSIDE a block, so nothing on
          // screen said where one thing ended and the next began and the
          // whole panel read as one run of lines. Rows keep the small step;
          // blocks take the large one.
          spacing: Style.spacing.xxl

          // The hero, drawn here rather than by `Ui/PanelHero`, because the
          // host's hero cannot draw the design this panel asked for. It renders
          // `meta` bold, upper-cased and letter-spaced, and `detail` as a bold
          // bordered pill in the top corner a size above `meta`
          // (Ui/PanelHero.qml). So "Healthy" arrived as tracked caps — the
          // treatment `SectionHeader` is meant to own alone — and "updated just
          // now", the least important fact in the header, became the loudest
          // thing in it. Neither is a property the host lets a caller turn off.
          //
          // Three lines: the site, the verdict at body size in full strength,
          // and the timestamp at caption size in tertiary under it. Refresh is
          // an icon button at the trailing edge, beside the timestamp it
          // invalidates. It lives here rather than in `PanelHero`'s
          // `trailingControl` for a reason that outlives the hero: that
          // property is a `Component`, and an item built from a Component is
          // out of reach of `focusedItem()`, which has to return it by id.
          Item {
            id: hero
            width: parent.width
            implicitHeight: Math.max(heroGlyph.implicitHeight, heroLabels.implicitHeight,
                                     refreshButton.implicitHeight)

            UnifiGlyph {
              id: heroGlyph
              objectName: "unifi-hero-glyph"
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              iconSize: Style.font.display
              glyphColor: health.colorFor(root.vm.rendering)
              badgeColor: root.urgent
              fontFamily: root.fontFamily
              showBadge: root.vm.rendering ? root.vm.rendering.badge === true : false
            }

            Column {
              id: heroLabels
              anchors.left: heroGlyph.right
              anchors.leftMargin: Style.space(14)
              anchors.right: refreshButton.left
              anchors.rightMargin: Style.spacing.lg
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                width: parent.width
                text: root.vm.siteName === "" ? "UniFi" : root.vm.siteName
                color: root.foreground
                font.family: root.fontFamily
                // The largest type in the panel, at regular weight: size alone
                // makes it the title, and a bold 14 px beside a 24 px glyph
                // read as a label that had lost its title. 1.75 × body is the
                // design's 21 px at the default 12, and scales with the user's
                // font size as the tokens do; no token sits between `heading`
                // (16) and `display` (24).
                font.pixelSize: Math.round(Style.font.body * 1.75)
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
              // The verdict: the one word the panel exists to deliver, on its
              // own line, in sentence case, at full strength.
              Text {
                width: parent.width
                visible: text !== ""
                text: root.vm.headlineWord
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
              Text {
                width: parent.width
                visible: text !== ""
                text: root.vm.headlineDetail
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
            }

            // U+21BB rather than a Nerd Font codepoint, for the reason
            // `UnifiGlyph` gives: a codepoint the user's font lacks is a tofu
            // box, and Qt's fallback finds this one in any stock font. The
            // tooltip is the button's name, since the glyph alone is not one.
            Button {
              id: refreshButton
              objectName: "unifi-refresh"
              anchors.right: parent.right
              anchors.top: heroLabels.top
              iconText: "\u21bb"
              tooltipText: "Refresh"
              enabled: root.vm.refreshEnabled
              opacity: enabled ? 1.0 : 0.45
              hasCursor: root.focusStop === ViewModel.FOCUS_REFRESH
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.doRefresh()
            }
          }

          // REQ-B10. Three pages, one row of chips. `Ui/ButtonGroup` is one
          // Tab stop rather than one per chip, and this panel drives it through
          // `cursorIndex` rather than giving it Tab focus — which is the path
          // the host's own bar panels use and the one its source says they use
          // (host-contract §7). Giving it focus as well would put two
          // independent notions of "which chip" on screen at once.
          //
          // Shown only when there is a snapshot: Devices and Clients over no
          // reading are two empty pages and a third that explains why, and the
          // explanation is the one worth being on.
          // Wrapped so the segments stop can show a focus ring of its own.
          // The ring is drawn HERE and not by the group, because the group's
          // own cursor painting is what made selection ambiguous — see the note
          // on `cursorIndex` below. Focus and selection are two facts and now
          // have two marks: a ring around the control says where the keyboard
          // is, the chip fill says which page you are on, and neither moves
          // when the other changes.
          Item {
            id: segmentsFrame
            width: parent.width
            visible: root.vm.hasSnapshot
            implicitHeight: segments.implicitHeight

            Rectangle {
              anchors.fill: segments
              anchors.margins: -Style.spacing.xs
              radius: Style.cornerRadius
              color: "transparent"
              border.width: Style.controlBorderWidth(true, false)
              border.color: Style.controlBorder(true, false, root.foreground, root.foreground)
              visible: root.focusStop === ViewModel.FOCUS_SEGMENTS
            }

              ButtonGroup {
                id: segments
                options: [
                  { value: "overview", label: "Overview" },
                  { value: "devices", label: "Devices" },
                  { value: "clients", label: "Clients" }
                ]
                value: root.view
                // NOT driven from the focus stop any more, and the reason is worth
                // recording. `cursorIndex` was bound to the selected index whenever
                // the segments held focus — so the cursor was never on a chip other
                // than the selected one, and the only thing the binding achieved was
                // to change how the SELECTED chip was painted depending on where
                // focus happened to be.
                //
                // `Ui/Button` resolves its fill `hot` before `selected`
                // (Ui/Button.qml:112-117), and `hot` is `hasCursor || hover`. So the
                // current page wore the hover fill while the segments had focus and
                // the selected fill when they did not — two different appearances
                // for one state, flipping on every Tab and every arrow. That is the
                // "it does not reliably display which page you are on" report.
                //
                // Selection is now painted by `value` alone and never changes. Focus
                // is drawn as a ring around the whole group below, which is additive:
                // it says where the keyboard is without touching what is selected.
                cursorIndex: -1
                focusable: false
                // The kit's gap between buttons, not ButtonGroup's `md`: the
                // group is built for a dense row of options in a form, and at
                // 6 px three page tabs read as one control split in three.
                spacing: Style.spacing.controlGap
                foreground: root.foreground
                fontFamily: root.fontFamily
                onChanged: function (next) {
                  root.focusStop = ViewModel.FOCUS_SEGMENTS
                  root.setView(next)
                }
                // The host's gallery also wires `onHovered` so the mouse drags the
                // panel cursor onto the group (GalleryPanel.qml:1055-1061), and this
                // deliberately does not. There, the group sits in a form of rows
                // where hover-follows-cursor is natural. Here it is three chips at
                // the TOP of a panel whose content is below them, so the mouse
                // crosses it on the way to everything — and stealing the focus stop
                // in passing would move Tab's starting point without the user
                // having asked for anything.
                //
                // A CLICK still sets it, in `onChanged` above, which is the case
                // where the user did ask.
              }
          }

          // REQ-011: disabled WITH an explanatory label. A greyed button that
          // says nothing sends the user to look for a fault in the button.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.refreshDisabledReason === ""
              ? "" : "Refresh is unavailable: " + root.vm.refreshDisabledReason
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // REQ-013 / UX-007. One sentence naming what failed and the single
          // action that would fix it. Empty, and therefore invisible, only in
          // the `ok` state — which is the twenty-fifth state and the one that
          // needs no explaining.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.sentence
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // UX-007's relative countdown. Recomputed by the service's freshness
          // tick, not by a timer in this file — see the comment on AC-071 in
          // Service.qml's freshnessTimer.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.nextAttemptText === ""
              ? "" : "Next attempt " + root.vm.nextAttemptText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
          }

          // UX-006a. DATA-012 carries the discovered {id, name} pairs in a
          // warning, because the batch that discovers them has no data at all.
          Column {
            width: parent.width
            spacing: 0
            visible: root.vm.sites.length > 0

            SectionHeader {
              text: "Sites on this controller"
              foreground: root.foreground
              fontFamily: root.fontFamily
              emphasis: root.emphasis
            }
            Repeater {
              model: root.vm.sites
              delegate: Text {
                required property var modelData
                width: column.width
                text: modelData.label
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
            }
          }

          // UX-009 / AC-070. Persistent for the whole session and with NO
          // dismiss handler, deliberately: `allowInsecureTls` is an opt-in that
          // turns off certificate verification, and a warning the user can make
          // go away is one they will make go away.
          Text {
            visible: root.vm.insecureTls
            width: parent.width
            text: "TLS verification is disabled for this controller. "
              + "Anything on the network path can read and alter what is shown here."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // REQ-B10 / REQ-B13 / REQ-B16. Two instances rather than one bound to
          // the active list: each owns its own search field, and a field's text
          // is not bound back from the model (see BrowseList) — so one shared
          // field would carry the device search into the client page.
          BrowseList {
            id: deviceBrowse
            width: parent.width
            visible: root.view === "devices"
            list: root.vm.deviceList
            placeholder: "Search devices"
            // AC-B18 / REQ-014. The rule for where a rebuilt list sits is the
            // model's; it is passed down because this file is one of the only
            // two that may import the pure modules (they are not
            // `.pragma library`, HC-16, so a third importer would evaluate
            // ViewModel.js again inside every panel, twice, per monitor).
            scrollAfterRowsChange: ViewModel.scrollAfterRowsChange
            scrollParent: panelFlick
            searchFocused: root.focusStop === ViewModel.FOCUS_SEARCH
            listFocused: root.focusStop === ViewModel.FOCUS_LIST
            cursorIndex: root.deviceCursor
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            emphasis: root.emphasis
            copiedKey: root.copiedKey
            onCopyRequested: function (key, text) { root.copyValue(key, text) }
            onSearchChanged: function (text) { root.setSearch(text) }
            onToggleRequested: function (id) { root.toggleExpanded(id) }
            onTabRequested: function (direction) { root.moveFocus(direction) }
            onEscapeRequested: root.escapePressed()
            onCursorHovered: function (index, at) { root.hoverList(index, at) }
            // REQ-B10a. Wired on BOTH pages though only Devices can raise it:
            // the two blocks are deliberately parallel, and a handler that
            // exists on one of them is the asymmetry that gets missed when a
            // second filter dimension is added.
            onFilterChanged: function (value) { root.setFilter(value) }
          }

          BrowseList {
            id: clientBrowse
            width: parent.width
            visible: root.view === "clients"
            list: root.vm.clientList
            placeholder: "Search clients"
            // Wired on BOTH pages, for the reason the filter handler below is:
            // a property set on one of two deliberately parallel blocks is the
            // asymmetry nobody notices until the other page misbehaves.
            scrollAfterRowsChange: ViewModel.scrollAfterRowsChange
            scrollParent: panelFlick
            searchFocused: root.focusStop === ViewModel.FOCUS_SEARCH
            listFocused: root.focusStop === ViewModel.FOCUS_LIST
            cursorIndex: root.clientCursor
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            emphasis: root.emphasis
            copiedKey: root.copiedKey
            onCopyRequested: function (key, text) { root.copyValue(key, text) }
            onSearchChanged: function (text) { root.setSearch(text) }
            onToggleRequested: function (id) { root.toggleExpanded(id) }
            onTabRequested: function (direction) { root.moveFocus(direction) }
            onEscapeRequested: root.escapePressed()
            onCursorHovered: function (index, at) { root.hoverList(index, at) }
            // REQ-B10a. Wired on BOTH pages though only Devices can raise it:
            // the two blocks are deliberately parallel, and a handler that
            // exists on one of them is the asymmetry that gets missed when a
            // second filter dimension is added.
            onFilterChanged: function (value) { root.setFilter(value) }
          }

          StatusPanel {
            id: statusPanel
            objectName: "unifi-status-panel"
            width: parent.width
            visible: root.vm.hasSnapshot && !root.browsing
            vm: root.vm
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            emphasis: root.emphasis
            cursorIndex: root.focusStop === ViewModel.FOCUS_LIST && !root.browsing
              ? root.inventoryCursor : -1
            // REQ-B10a / AC-B19. The Overview count row is an entry point into
            // a filtered Devices page. The role value travels with the row from
            // `ViewModel.countRows`, so nothing between here and there
            // translates a plural noun into a feature name.
            onRoleActivated: function (role) {
              root.setView("devices", role)
              root.focusStop = ViewModel.FOCUS_LIST
            }
            // The two totals above the role rows open their pages UNFILTERED:
            // "Connected clients 44" has to land on the 44, not on whichever
            // Wired/Wireless chip was left selected earlier in this session.
            onPageActivated: function (page) {
              if (page === "clients") {
                root.typeFilter = ""
                root.clientCursor = 0
                root.setView("clients")
              } else {
                root.deviceCursor = 0
                root.setView("devices", "")
              }
              root.focusStop = ViewModel.FOCUS_LIST
            }
          }

          DeviceList {
            width: parent.width
            // `active`, not `visible`: a `visible` written here would replace
            // the component's own guard rather than combine with it, which is
            // exactly how the empty "Offline and impaired" heading shipped.
            active: !root.browsing
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
            emphasis: root.emphasis
          }

          WarningList {
            width: parent.width
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
            emphasis: root.emphasis
          }

          // AC-052. Rendered as "unknown" rather than omitted, so an
          // `unconfigured` failure — the case with the least information and
          // the most need for it — still shows the same rows in the same
          // places.
          // The one rule in the panel's own flow: it sits where the content
          // stops and the chrome at the foot begins. Sections above it are
          // separated by space alone.
          PanelSeparator { foreground: root.foreground }

          // Two disclosures on one line, at tertiary. Details used to be a
          // `SectionHeader` with a chevron at the far edge, which gave setup
          // diagnostics the same heading as Uplink and Inventory; it is an
          // affordance, and now looks like one.
          //
          // Details is closed by default and opened for you in any state that
          // is not `ok` — where the controller host and the config generation
          // stop being trivia and become the diagnosis. `sentence` is non-empty
          // in exactly those states, so `detailsExpanded` reads the fact rather
          // than re-deriving it.
          //
          // The keys work today and nothing said so — which is the same as them
          // not working. `Ui/PanelKeyCatcher` maps the arrows AND hjkl,
          // Tab/Shift-Tab, Enter, Space and Escape (PanelKeyCatcher.qml:51-83),
          // and this panel adds "/", "f" and "?"; the legend names them.
          Item {
            width: parent.width
            implicitHeight: detailsToggle.implicitHeight

            // Points down when open, right when closed. Shapes rather than
            // glyph names, for the reason `UnifiGlyph` gives: a codepoint the
            // user's font lacks renders as a tofu box, and these two are in
            // every font that has ever shipped.
            Text {
              id: detailsToggle
              anchors.left: parent.left
              text: (root.detailsExpanded ? "\u25be" : "\u25b8") + "  Details"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              textFormat: Text.PlainText

              MouseArea {
                anchors.fill: parent
                anchors.margins: -Style.spacing.xs
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: Qt.PointingHandCursor
                onClicked: root.toggleDetails()
              }
            }

            Text {
              anchors.right: parent.right
              anchors.baseline: detailsToggle.baseline
              visible: root.vm.hasSnapshot
              text: root.keysOpen ? "\u25be  keys" : "?  keys"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              textFormat: Text.PlainText

              MouseArea {
                anchors.fill: parent
                anchors.margins: -Style.spacing.xs
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: Qt.PointingHandCursor
                onClicked: root.keysOpen = !root.keysOpen
              }
            }
          }

          // `DetailRows`, not a second hand-rolled label/value Repeater. These
          // rows have exactly the shape it renders — `{ key, label, value,
          // copy }` — and the copy affordance the site id needed was already
          // there, so the alternative was to write it twice and then keep the
          // two in step.
          //
          // It also fixes a smaller thing on the way: the old delegate
          // right-anchored the value with no left bound, so a long controller
          // host and a long label could overlap rather than elide.
          DetailRows {
            width: parent.width
            visible: root.detailsExpanded
            rows: root.vm.metaRows
            foreground: root.foreground
            fontFamily: root.fontFamily
            emphasis: root.emphasis
            copiedKey: root.copiedKey
            onCopyRequested: function (key, text) { root.copyValue(key, text) }
          }

          // DATA-007's diagnostic line, shown only when there is one. The
          // helper sanitizes every message before it reaches an envelope
          // (sanitize.py), which is what makes it safe to print (SEC-001).
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.errorMessage
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          Text {
            width: parent.width
            visible: root.keysOpen && root.vm.hasSnapshot
            // `f` is named on both browse pages and on neither Overview,
            // which has nothing to filter — a key named where it does nothing
            // is worse than one that is not named. `/` stays on Overview: it
            // opens Devices with the caret in its search field.
            text: root.browsing
              ? "←→ pages  ·  Tab move  ·  ↑↓ select  ·  ⏎ open  ·  f filter  ·  / search  ·  r refresh  ·  d details  ·  ? keys  ·  Esc close"
              : "←→ pages  ·  Tab move  ·  ↑↓ select  ·  ⏎ open  ·  / search  ·  r refresh  ·  d details  ·  ? keys  ·  Esc close"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // Last, below everything, because it is the one action that leaves the
          // panel: reaching it should take a deliberate scroll, not a stray
          // click on the way to Refresh.
          Button {
            id: dashboardButton
            text: "Open UniFi  \u2197"
            enabled: root.vm.dashboard ? root.vm.dashboard.accepted : false
            opacity: enabled ? 1.0 : 0.45
            hasCursor: root.focusStop === ViewModel.FOCUS_DASHBOARD
            foreground: root.foreground
            fontFamily: root.fontFamily
            bordered: true
            onClicked: root.openDashboard()
          }

          // REQ-012's disabled label, and UX-010's warning. The warning is a
          // BINDING, not something raised on click, which is precisely what
          // "rendered before the launch occurs" requires: it is on screen for
          // as long as the URL is plain HTTP, whether or not anyone presses the
          // button.
          Text {
            visible: text !== ""
            width: parent.width
            text: !root.vm.dashboard ? ""
              : !root.vm.dashboard.accepted
                ? "Open UniFi is unavailable: " + root.vm.dashboard.reason + "."
                : root.vm.dashboard.warnPlainHttp
                  ? "This dashboard URL is plain HTTP. Your session, including "
                    + "anything you type into it, will not be encrypted."
                  : ""
            color: root.vm.dashboard && root.vm.dashboard.warnPlainHttp
              ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

        }
      }
    }
  }
}
