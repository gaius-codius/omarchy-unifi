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
  property string focusStop: ViewModel.FOCUS_SEGMENTS

  // REQ-B10's view state, passed into `build` rather than held by it. The panel
  // owns it because it is the panel's: which page is showing, what is typed in
  // each search field, which row is open, and which row the cursor is on.
  property string view: "overview"
  property string deviceSearch: ""
  property string clientSearch: ""
  property string roleFilter: ""
  property string expandedDeviceId: ""
  property string expandedClientId: ""
  property int deviceCursor: 0
  property int clientCursor: 0

  // One instance, passed down rather than instantiated per delegate — a
  // 200-row list would otherwise allocate 200 copies of three constants.
  readonly property Emphasis emphasis: emphasisTokens
  // Bound to the BAR's foreground, not the global one — the panel takes its
  // colours from the bar it belongs to (`foreground` above does the same), so a
  // scale built from `Color.foreground` would drift from everything around it.
  Emphasis { id: emphasisTokens; foreground: root.foreground }

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

  // Every exit from the state the confirmation describes.
  onViewChanged: copiedKey = ""
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
    focusStop = ViewModel.nextFocus(view, focusStop, direction)
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
  // point at different rows. It moves the focus STOP too: pressing Enter after
  // pointing at a row should open the row that was pointed at.
  function hoverList(index) {
    if (!browsing) return
    focusStop = ViewModel.FOCUS_LIST
    if (view === "devices") deviceCursor = index
    else clientCursor = index
  }

  function moveListCursor(delta) {
    if (!activeList) return
    var count = activeList.rows.length
    if (count === 0) return
    var next = Math.min(count - 1, Math.max(0, activeCursor + delta))
    if (view === "devices") deviceCursor = next
    else clientCursor = next
  }

  // REQ-B10 / REQ-B10a. Switching pages moves the focus stop only when the one
  // it is on does not exist on the new page, and clears the role filter unless
  // the caller is setting one — a filter that outlived the row that set it
  // would show an empty Devices page with nothing saying why.
  function setView(next, role) {
    var wanted = ViewModel.browseView(next)
    view = wanted
    roleFilter = role === undefined ? "" : role
    focusStop = ViewModel.focusAfterViewChange(wanted, focusStop)
  }

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
    clearSearch()
    view = "overview"
    roleFilter = ""
    expandedDeviceId = ""
    expandedClientId = ""
    deviceCursor = 0
    clientCursor = 0
    focusStop = ViewModel.FOCUS_SEGMENTS
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
        if (t === "r" || t === "R") root.doRefresh()
      }

      Flickable {
        id: panelFlick
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
          spacing: Style.spacing.md

          PanelHero {
            width: parent.width
            title: root.vm.siteName === "" ? "UniFi" : root.vm.siteName
            meta: root.vm.headline
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              UnifiGlyph {
                objectName: "unifi-hero-glyph"
                iconSize: Style.font.display
                glyphColor: health.colorFor(root.vm.rendering)
                badgeColor: root.urgent
                fontFamily: root.fontFamily
                showBadge: root.vm.rendering ? root.vm.rendering.badge === true : false
              }
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

            PanelSectionHeader {
              text: "Sites on this controller"
              foreground: root.foreground
              fontFamily: root.fontFamily
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

          Text {
            visible: root.vm.customCaInUse
            width: parent.width
            text: "A custom certificate authority is in use for this controller."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
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
            onCursorHovered: function (index) { root.hoverList(index) }
            // REQ-B10a. Wired on BOTH pages though only Devices can raise it:
            // the two blocks are deliberately parallel, and a handler that
            // exists on one of them is the asymmetry that gets missed when a
            // second filter dimension is added.
            onFilterCleared: root.roleFilter = ""
          }

          BrowseList {
            id: clientBrowse
            width: parent.width
            visible: root.view === "clients"
            list: root.vm.clientList
            placeholder: "Search clients"
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
            onCursorHovered: function (index) { root.hoverList(index) }
            // REQ-B10a. Wired on BOTH pages though only Devices can raise it:
            // the two blocks are deliberately parallel, and a handler that
            // exists on one of them is the asymmetry that gets missed when a
            // second filter dimension is added.
            onFilterCleared: root.roleFilter = ""
          }

          StatusPanel {
            width: parent.width
            visible: root.vm.hasSnapshot && !root.browsing
            vm: root.vm
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            // REQ-B10a / AC-B19. The Overview count row is an entry point into
            // a filtered Devices page. The role value travels with the row from
            // `ViewModel.countRows`, so nothing between here and there
            // translates a plural noun into a feature name.
            onRoleActivated: function (role) {
              root.setView("devices", role)
              root.focusStop = ViewModel.FOCUS_LIST
            }
          }

          DeviceList {
            width: parent.width
            visible: !root.browsing
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          WarningList {
            width: parent.width
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          // AC-052. Rendered as "unknown" rather than omitted, so an
          // `unconfigured` failure — the case with the least information and
          // the most need for it — still shows the same rows in the same
          // places.
          PanelSeparator { foreground: root.foreground }

          PanelSectionHeader {
            text: "Details"
            foreground: root.foreground
            fontFamily: root.fontFamily
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

          PanelSeparator { foreground: root.foreground }

          // --- the two actions (REQ-011, REQ-012, UX-008) -------------------
          Row {
            spacing: Style.spacing.controlGap

            Button {
              text: "Refresh"
              enabled: root.vm.refreshEnabled
              opacity: enabled ? 1.0 : 0.45
              hasCursor: root.focusStop === ViewModel.FOCUS_REFRESH
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.doRefresh()
            }

            Button {
              text: "Open UniFi"
              enabled: root.vm.dashboard ? root.vm.dashboard.accepted : false
              opacity: enabled ? 1.0 : 0.45
              hasCursor: root.focusStop === ViewModel.FOCUS_DASHBOARD
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.openDashboard()
            }
          }

          // The keys work today and nothing said so — which is the same as
          // them not working. `Ui/PanelKeyCatcher` maps the arrows AND hjkl,
          // Tab/Shift-Tab, Enter, Space and Escape (PanelKeyCatcher.qml:51-83),
          // and this panel adds "/" and "r"; none of it was discoverable.
          //
          // Tertiary weight and one line: an affordance, not a manual.
          Text {
            width: parent.width
            visible: root.vm.hasSnapshot
            text: root.browsing
              ? "←→ pages  ·  ↑↓ select  ·  ⏎ open  ·  Tab move  ·  / search  ·  Esc close"
              : "←→ pages  ·  Tab move  ·  ⏎ activate  ·  / search  ·  Esc close"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
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
