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
  signal cursorHovered(int index)
  signal copyRequested(string key, string text)
  // REQ-B10a. Emitted by the filter chip below; the panel owns `roleFilter` and
  // is the only thing that may clear it.
  signal filterCleared()

  readonly property var rows: list ? list.rows : []
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground
  readonly property string filterText: list && list.filterText ? list.filterText : ""

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

  function focusSearch() { searchField.forceActiveFocus() }
  function releaseSearch() { searchField.focus = false }

  // The field is uncontrolled — `text` is not bound to `list.searchText`, for
  // the reason given at the binding below — so the panel clearing the MODEL
  // does not clear the FIELD. Both places that clear it programmatically
  // (Escape with the list focused, and REQ-B10's reset on close) go through
  // here, or the user sees their search text over an unfiltered list.
  function setSearchText(next) { searchField.text = next }
  readonly property string searchText: searchField.text

  spacing: Style.spacing.sm

  // REQ-B10a's filter, said out loud.
  //
  // ABOVE the search field, because it is a condition on the whole page rather
  // than a modifier of the search — it survives typing, and clearing the search
  // does not clear it.
  //
  // An `Ui/Button` and not a hand-rolled chip. Host-contract §7 says never write
  // a `MouseArea` for a BUTTON, and unlike the list rows (§7's carve-out, N-100)
  // this is a button in every sense: one word, one action, and it wants the
  // kit's hover fill, border tokens and tooltip rather than a private imitation
  // of them.
  //
  // The `✕` is composed here rather than in the model. It is punctuation for an
  // affordance, not a statement — the same reasoning as `BrowseRow`'s `·`
  // separator — and keeping it out of `filterText` leaves the model asserting a
  // sentence rather than a glyph.
  //
  // NOT a Tab stop. Adding one would change REQ-B15's focus order, which is a
  // spec amendment; the keyboard route to clearing already exists and is the
  // one that set the filter in the first place — any page change clears it
  // (`Panel.setView`). What was missing was never the way out, only the sign.
  Button {
    visible: root.filterText !== ""
    text: root.filterText + "   ✕"
    tooltipText: "Show all devices"
    foreground: root.foreground
    fontFamily: root.fontFamily
    fontSize: Style.font.caption
    bordered: true
    onClicked: root.filterCleared()
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

    model: root.rows
    currentIndex: root.listFocused ? root.cursorIndex : -1

    // Deferred by a turn, as bluetooth's is and for the same reason: the model
    // is rebuilt on every poll AND on every keystroke, so swapping it resets
    // the view out from under a call made straight from the signal. Bluetooth's
    // comment records that network's list is stable enough not to need this;
    // these lists are bluetooth's case.
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
      onHoverRequested: root.cursorHovered(index)
    }
  }
}
