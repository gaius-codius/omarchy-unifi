// REQ-B13 / REQ-B16 / AC-B18: a searchable, scrolling list with one row open.
//
// Both pages are this component. The two list models are asserted to have
// identical shapes in `tests/model/viewmodel.test.js` precisely so that one
// file can render both — a key present on one and not the other would be a
// binding that silently reads `undefined` on one of the two pages.
//
// It decides nothing about what is shown. The rows, the "showing 200 of 412
// devices" line, the empty sentence and the term inside it all arrive on
// `list`, built by `ViewModel.deviceListModel` / `clientListModel` (REQ-014).
// What this file owns is the viewport, the cursor, and handing keystrokes to
// the right place.
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui

Column {
  id: root

  // The list model from `vm` — `vm.deviceList` or `vm.clientList`.
  property var list: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family
  property string placeholder: "Search"
  property var emphasis: null
  property string copiedKey: ""

  // Owned by the panel, because REQ-B15's Tab order spans this component and
  // the buttons below it, and because REQ-B10 requires the search text to be
  // cleared when the panel closes — which this component never learns about.
  property bool searchFocused: false
  property bool listFocused: false
  property int cursorIndex: 0
  // The panel's own scroller, which a wheel over this list hands the rest of
  // its travel to once the list is at the end it is scrolling towards.
  property Flickable scrollParent: null

  signal searchChanged(string text)
  // REQ-B15. Tab must keep cycling the panel's stops while the search field has
  // focus — but `PanelKeyCatcher` is `blocked` then and never sees the key, and
  // a QQC TextField's own Tab handling moves `activeFocus` somewhere the
  // panel's focus model knows nothing about. So the field forwards it.
  signal tabRequested(int direction)
  // Escape on an EMPTY field. `PanelKeyCatcher` is blocked while the field has
  // focus, and it uses `Keys.priority: Keys.BeforeItem` — so it has already had
  // its turn by the time the field declines the key, and leaving the event to
  // propagate would mean Escape did nothing at all. Forwarded explicitly.
  signal escapeRequested()
  signal toggleRequested(string id)
  signal cursorHovered(int index, point at)
  signal copyRequested(string key, string text)
  // REQ-B10a. Emitted by the filter chooser below; the panel owns `roleFilter`
  // and is the only thing that may change it.
  signal filterChanged(string value)

  readonly property var rows: list ? list.rows : []

  // AC-B18. The rows reach the list through `adoptRows()` rather than through a
  // binding on `model`, because the position has to be read BEFORE the new
  // array lands: a `ListView` resets `contentY` to 0 inside the assignment
  // itself, so by the time `onModelChanged` runs the old position is already
  // gone. Repeated at completion because a page whose `list` arrives with the
  // component never raises the change signal this handler is on.
  onRowsChanged: view.adoptRows()
  Component.onCompleted: view.adoptRows()
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground
  readonly property var filterChips: list && list.filterChips ? list.filterChips : []
  readonly property string filterValue: list && list.filterValue ? list.filterValue : ""

  // The scrollbar overlays the viewport rather than sitting beside it, so
  // without a reserved gutter it lands on top of the right-hand end of every
  // row — which for this list is the status word and the elided tail of the
  // context line. The host's own lists have the same overlap and get away with
  // it because their rows are one short line; ours are two, with content at
  // both edges.
  //
  // Bound to the SAME condition that drives `interactive`, so a list that does
  // not scroll gives the space back rather than carrying an empty margin for a
  // scrollbar that is not there.
  readonly property int gutter: view.interactive ? Style.space(10) : 0

  // The panel needs to know where the caret is to satisfy REQ-B15's
  // "the panel's own key handling is suspended while the search field holds
  // focus" — `PanelKeyCatcher.blocked` is bound to this.
  readonly property bool editing: searchField.activeFocus

  // REQ-B15 / UX-008. The panel scrolls to whichever of these the keyboard is
  // on, and it cannot reach inside this component to find them — the two focus
  // stops that live in here are named rather than guessed at from the child
  // order, which a later edit would silently change.
  readonly property Item searchItem: searchField
  readonly property Item listItem: view

  function focusSearch() { searchField.forceActiveFocus() }
  // Clearing focus is all this does, and all it may do: `focus = false` hands
  // the keyboard to NOBODY, and the item that has to get it back — the panel's
  // `PanelKeyCatcher` — is not something this file can see. `Panel.qml` watches
  // `editing` and gives it back there, which also covers the ways out of the
  // field that never reach this function.
  function releaseSearch() { searchField.focus = false }

  // AC-B18 / REQ-014. Where the list goes when its rows are replaced is the
  // model's rule (`ViewModel.scrollAfterRowsChange`), handed in rather than
  // imported: `Panel.qml` and `Service.qml` are the only importers of the pure
  // modules, which are not `.pragma library` (HC-16) — so a third import would
  // evaluate the whole of ViewModel.js again in each of the two pages, in every
  // panel, on every monitor. The panel already holds it.
  property var scrollAfterRowsChange: null

  // The field is uncontrolled — `text` is not bound to `list.searchText`, for
  // the reason given at the binding below — so the panel clearing the MODEL
  // does not clear the FIELD. Both places that clear it programmatically
  // (Escape with the list focused, and REQ-B10's reset on close) go through
  // here, or the user sees their search text over an unfiltered list.
  function setSearchText(next) { searchField.text = next }
  readonly property string searchText: searchField.text

  // The filter row, the field and the list are three different things and
  // need air between them; at `sm` the underline of the chosen filter sat on
  // the field's border.
  spacing: Style.spacing.xl

  // REQ-B10a's filter, offered here rather than only from Overview — a device
  // role on one page, a client's connection type on the other (SPEC-AMD-10).
  // This file does not know which: the model hands it options and a selected
  // value, and the panel decides what a change means (REQ-014).
  //
  // NOT the control the pages use one row above. It was — the same
  // `Ui/ButtonGroup`, distinguished only by a caption-size font — and the risk
  // that file accepted did not pay off: two levels of the hierarchy in
  // identical chrome, directly stacked, read as one control with seven options.
  // `FilterChips` keeps the kit's tokens and drops its chrome, so the selected
  // option is marked with a rule instead of a fill and the row reads as a
  // condition on the page rather than as a peer of it.
  //
  // It REPLACED a chip reading "Access points only ✕" (SPEC-AMD-8). A chooser
  // showing the current state and every other one available says strictly more
  // than a label plus a clear button, and keeping both would be the same fact
  // twice in a panel that is short of room.
  //
  // ABOVE the search field, because the filter is a condition on the page and
  // the search is a condition on the rows: the filter survives typing, and
  // clearing the search does not clear it.
  //
  // NOT a Tab stop, deliberately — one more stop would amend REQ-B15's focus
  // order. The keyboard route is `f`, which cycles this list; `FilterChips`
  // prints that key on the row itself, because naming it only in a legend at
  // the foot of the panel put it two screens below the control it operates.
  FilterChips {
    width: parent.width
    options: root.filterChips
    value: root.filterValue
    foreground: root.foreground
    fontFamily: root.fontFamily
    emphasis: root.emphasis
    onChanged: function (value) { root.filterChanged(value) }
  }

  TextField {
    id: searchField
    width: parent.width
    placeholderText: root.placeholder
    // `Ui/TextField` sizes from `font.pixelSize` + `verticalPadding`, and its
    // 30 px default is for dialog forms. A panel row is 22-26 px, which is what
    // the host's own inline field does (host-contract §7).
    verticalPadding: Style.space(4)
    foreground: root.foreground
    hasCursor: root.searchFocused && !activeFocus
    // One direction only. `text` is NOT bound to `list.searchText`: the field
    // is where the text comes from, and binding it back would fight the user's
    // caret every time the model rebuilt — which, since `nowWall` moves the
    // model every five seconds, is while they are still typing.
    onTextChanged: root.searchChanged(text)

    // REQ-B15's `/`, printed on the field it focuses.
    // Only while the field is empty and unfocused: once there is a caret the
    // key has done its job, and text would run under it.
    Text {
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.lg
      anchors.verticalCenter: parent.verticalCenter
      visible: !searchField.activeFocus && searchField.text === ""
      text: "/"
      color: root._tertiary
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      textFormat: Text.PlainText
    }

    Keys.onTabPressed: function (event) {
      root.releaseSearch()
      root.tabRequested(1)
      event.accepted = true
    }
    Keys.onBacktabPressed: function (event) {
      root.releaseSearch()
      root.tabRequested(-1)
      event.accepted = true
    }

    // REQ-B15. Escape clears a non-empty search BEFORE it closes the panel, and
    // it has to be caught here: while this field has focus `PanelKeyCatcher` is
    // blocked, so the panel's own Escape handler never sees the key.
    Keys.onEscapePressed: function (event) {
      if (text !== "") {
        clear()
        event.accepted = true
        return
      }
      // Empty: hand it to the panel, which closes. NOT left to propagate —
      // `PanelKeyCatcher` sits above this field with `Keys.priority:
      // Keys.BeforeItem` and has already declined the key on the way down,
      // because it is `blocked` exactly while this field has focus.
      root.escapeRequested()
      event.accepted = true
    }
  }

  // REQ-B16. Shown whenever the list is bounded, filtered or not — a user
  // searching a truncated list needs to know the thing they are looking for
  // may exist and simply not be listed.
  Text {
    visible: text !== ""
    width: parent.width
    text: root.list ? root.list.truncationText : ""
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
  }

  Text {
    visible: text !== ""
    width: parent.width
    text: root.list ? root.list.emptyText : ""
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
  }

  // AC-B18. `ListView`, not `Flickable`: it owns the scroll position, so it
  // keeps the current row visible under j/k and re-clamps itself when a search
  // shortens the list. The idiom is the host's — bluetooth/Panel.qml:806-815,
  // network/Panel.qml:1481-1485 — and is cited in host-contract §7.
  ListView {
    id: view
    // AC-B18 reaches this from the harness by name. The harness cannot
    // meaningfully drag a list, so what it asserts is the property that decides
    // whether a drag would do anything.
    objectName: "unifi-browse-list"
    width: parent.width
    // A `Column` skips an invisible child entirely but still spaces a visible
    // zero-height one, so an empty list would leave a gap under the sentence
    // explaining why it is empty.
    visible: root.rows.length > 0
    // This cap is also what makes the list a second scroller, and that has a
    // consequence worth recording here rather than rediscovering:
    //
    // `interactive` below turns true at exactly the point the content exceeds
    // this height, and Qt Quick does not chain a wheel event past an
    // interactive flickable. So from that point a wheel over the list never
    // reaches the panel underneath it, and the pointer has to leave the list
    // for the panel to scroll at all. Five devices is under the cap; nine
    // clients is not, which is the state the shipped Clients screenshot is in.
    //
    // NOT changed here. The obvious fix — drop the cap, let the panel's own
    // Flickable own the scrolling — also retires this view's scroll position
    // entirely, and with it AC-B18's "a refresh preserves where you were" and
    // the `scrollAfterRowsChange` rule it is built on. That is a behavioural
    // change with live-harness coverage, and this tree has neither qmllint nor
    // Quickshell to check it against. Guessing at it blind is how you trade a
    // wheel annoyance for a scroll position that silently resets on every poll.
    //
    // FIXED by chaining rather than by dropping the cap: the `WheelHandler`
    // below scrolls the list while it has travel left in the wheel's
    // direction and gives the remainder to `scrollParent`, so the panel keeps
    // scrolling past the end of the list. That matters because Open UniFi, its
    // disabled reason and the plain-HTTP warning sit BELOW the list, at the
    // foot of the panel.
    height: Math.min(contentHeight, Style.space(320))
    spacing: Style.spacing.sm
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    // AC-B18's line. A list shorter than its viewport must not become a drag
    // surface: inside a KeyboardPanel — which is a full-screen click sink,
    // HC-20 — a list that swallows drags it has no use for is a panel that
    // feels stuck.
    interactive: contentHeight > height

    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    // The wheel, chained to the panel. A pointer handler sees the event before
    // the Flickable does, and accepting it here keeps the list's own wheel
    // handling out of it, so both scrollers move by the same arithmetic.
    WheelHandler {
      target: null
      enabled: view.interactive && root.scrollParent !== null
      onWheel: function (event) { view.chainWheel(event) }
    }

    // A notch is 120 units of `angleDelta`; a touchpad reports pixels.
    function chainWheel(event) {
      var dy = event.pixelDelta.y !== 0 ? event.pixelDelta.y
        : event.angleDelta.y / 120 * Style.space(48)
      if (dy === 0) return
      var wanted = contentY - dy
      var top = originY
      var bottom = originY + Math.max(0, contentHeight - height)
      var inside = Math.min(bottom, Math.max(top, wanted))
      contentY = inside
      var rest = wanted - inside
      if (rest === 0) return
      var outer = root.scrollParent
      var outerBottom = outer.originY + Math.max(0, outer.contentHeight - outer.height)
      outer.contentY = Math.min(outerBottom, Math.max(outer.originY, outer.contentY + rest))
    }

    // NOT `root.rows` — see `adoptRows`. Only that function writes this, so the
    // one thing that can swap the model is the one thing that remembers where
    // the user was.
    model: shownRows
    currentIndex: root.listFocused ? root.cursorIndex : -1

    // What is on screen, and the model it came from. The previous model is kept
    // because the rule that decides whether the old position still means
    // anything compares the two — the search term and the filter the rows were
    // built for (`ViewModel.scrollAfterRowsChange`).
    property var shownRows: []
    property var shownList: null
    property var priorList: null
    property real keptContentY: 0
    property bool restorePending: false

    function adoptRows() {
      // The FIRST swap of a turn is the one that knows where the user was: a
      // keystroke moves the model twice in some paths, and by the second swap
      // `contentY` is already the 0 the first one left behind.
      if (!restorePending) {
        keptContentY = contentY
        priorList = shownList
        restorePending = true
      }
      shownList = root.list
      shownRows = root.rows
      Qt.callLater(restoreScroll)
    }

    // Deferred a turn, as `keepCurrentVisible` is and for the same reason: the
    // clamp needs the height of the content that has just been handed over, and
    // on this frame the delegates for it have not been laid out.
    function restoreScroll() {
      restorePending = false
      if (!root.scrollAfterRowsChange) return
      contentY = root.scrollAfterRowsChange(priorList, shownList, keptContentY,
                                            contentHeight, height)
    }

    // Deferred by a turn, as bluetooth's is and for the same reason: the model
    // is rebuilt on every poll AND on every keystroke, so swapping it resets
    // the view out from under a call made straight from the signal. Bluetooth's
    // comment records that network's list is stable enough not to need this;
    // these lists are bluetooth's case.
    //
    // This covers the KEYBOARD only and always did: `currentIndex` is -1
    // whenever the list is not the focus stop, which is every mouse user on
    // every poll. `restoreScroll` is the other half. The one path that raises
    // both — a search keystroke, which rebuilds the rows and puts the cursor
    // back to 0 — is a path where the two agree on the top of the list.
    onCurrentIndexChanged: if (currentIndex >= 0) Qt.callLater(keepCurrentVisible)
    function keepCurrentVisible() {
      if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
    }

    delegate: BrowseRow {
      required property var modelData
      required property int index

      width: ListView.view.width - root.gutter
      row: modelData
      // REQ-B14: one row expanded at a time. `vm` resolves which — including
      // dropping the expansion when a search filters that row away — so this
      // is a comparison and not a rule.
      expanded: root.list ? root.list.expandedId === modelData.id : false
      detail: (root.list && root.list.expandedId === modelData.id)
        ? root.list.expandedDetail : null
      hasCursor: root.listFocused && root.cursorIndex === index
      foreground: root.foreground
      urgent: root.urgent
      fontFamily: root.fontFamily
      emphasis: root.emphasis
      copiedKey: root.copiedKey
      onCopyRequested: function (key, text) { root.copyRequested(key, text) }
      onToggleRequested: root.toggleRequested(modelData.id)
      // Hover moves the panel cursor onto the row, so the mouse and the
      // keyboard never point at two different rows. The host does the same for
      // its list rows (bluetooth/Panel.qml:940-945) — and unlike the segmented
      // control, which the mouse must cross to reach anything, a row under the
      // pointer IS the row the user is pointing at.
      onHoverRequested: function (at) { root.cursorHovered(index, at) }
    }
  }
}
