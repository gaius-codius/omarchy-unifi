// One row of the Devices or Clients list (REQ-B14), and its detail when open.
//
// One component for both lists, not two that look alike. Every string it draws
// was decided in `ViewModel.js` — `nameText`, `secondaryText` (a device's model,
// a client's IP, in the same place and meaning the same thing), `tokenText`,
// `metaText` — so this file makes no decision about what a row says. That is
// REQ-014, and it is what lets 33 `node --test` cases cover the wording of a
// list the harness only has to prove is on screen.
//
// The MAC address is not here (REQ-B21 / D3). It appears in `detailRows` and
// therefore only after a deliberate expansion.
//
// The layout is two lines and three columns:
//
//   Switch 1 ................................................ Down
//   USW-Pro-48-PoE    192.168.1.5  ·  up 3d 4h
//
// The second line's first field is a FIXED column, so whatever follows begins
// on the same left edge on every row and a list of addresses can be read down
// rather than hunted across. The separator that used to precede it is gone: it
// was standing in for an alignment that did not exist.
//
// The status word sits in a fixed right-hand column rather than at the head of
// the second line, so the eye can run down one edge and find every broken
// device — and it is printed only when it is NOT the expected one, so on a
// healthy list that edge is empty and a broken device is the only thing on it.
// `vm` decides which states qualify. That is what a list ordered by brokenness
// is for, and while the word was the first of four `·`-joined segments it was
// just another word in a sentence. It also frees the name: an earlier layout
// put the model on the same line, right-aligned and capped at 40% of the
// width, which meant a long model name ate the name being searched for.
import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: root

  property var row: null
  property bool expanded: false
  property bool hasCursor: false
  property var detail: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family
  property var emphasis: null
  property string copiedKey: ""

  signal toggleRequested()
  signal hoverRequested(point at)
  signal copyRequested(string key, string text)

  readonly property color _secondary: emphasis ? emphasis.secondary : foreground
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground

  // The name's size, and the reason it is not `Style.font.bodySmall`.
  //
  // The row is two lines: a name at `bodySmall` over a meta line at `caption`.
  // Those two tokens differ by about 1.5 px, which in a monospace face at this
  // size is not a step the eye reads as a level — so the name, which is the
  // thing the list is searched by and the thing every row is identified by,
  // arrived looking like slightly brighter metadata.
  //
  // `title` — body + 2, 14 at the default base against caption's 10 — rather
  // than a multiple of another token, so it tracks the user's font settings
  // and any per-token theme override the way `Style.font.*` exists to. The
  // design's step is 3.5-4 px between the name and the line under it; at
  // `subtitle` (13) the name was a slightly brighter line rather than the
  // thing the row is about.
  //
  // Sentence case, deliberately, and never the tracked-uppercase treatment
  // `SectionHeader` uses: a device name is a proper noun the user typed into
  // the search field, and upper-casing it breaks the match between what they
  // searched for and what they are looking at.
  readonly property real titleSize: Style.font.title

  implicitHeight: layout.implicitHeight
  height: implicitHeight

  // The cursor tint is the kit's, taken from the same token `Ui/Button` uses
  // for its hover-cursor state, so a row under the panel cursor reads the same
  // as a chip or a button under it. UX-002: it is not the only signal — the
  // expanded row also shows its detail, and the cursor moves with the keyboard.
  Rectangle {
    anchors.fill: parent
    anchors.leftMargin: -Style.spacing.xs
    anchors.rightMargin: -Style.spacing.xs
    radius: Style.cornerRadius
    color: Style.controlFill(false, root.hasCursor, root.foreground, root.foreground)
    visible: root.hasCursor
  }

  Column {
    id: layout
    width: parent.width
    spacing: Style.spacing.xs

    // The header, and the only part that toggles. The click target covers this
    // Item and the line under it but not the whole row: a target over the
    // expanded detail would collapse the row when the user clicked in the port
    // table they had just opened to read.
    Item {
      id: header
      width: parent.width
      implicitHeight: Math.max(primary.implicitHeight, token.implicitHeight)

      Text {
        id: primary
        anchors.left: parent.left
        anchors.right: token.left
        anchors.rightMargin: Style.spacing.md
        text: root.row ? root.row.nameText : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: root.titleSize
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }

      // The status word. Never elided and never given a width cap — it is a
      // fixed vocabulary ("Online", "Down", "Wired", "Wireless"), so it cannot
      // grow, and the whole point of the column is that it is always in the
      // same place.
      //
      // `tokenUrgent` and not a class comparison here: the model decides which
      // states are the ones worth colouring (REQ-014), and `down` is not the
      // only one. UX-002 — the colour is never the only signal, because the
      // word itself says the condition.
      Text {
        id: token
        anchors.right: parent.right
        anchors.baseline: primary.baseline
        text: root.row ? root.row.tokenText : ""
        color: (root.row && root.row.tokenUrgent) ? root.urgent : root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
      }
    }

    // The second line: identity that is not the name, then context. Two Text
    // items rather than one string, because they are two different weights —
    // which is the whole reason this row was hard to read. The separator is
    // punctuation and carries no meaning, so composing it here is not a
    // decision (REQ-014); every word around it came from `vm`.
    // Three fixed columns rather than a `·`-joined sentence: identity
    // (model, or a client's address), then the two context slots. In a face
    // where every glyph is one cell, a fixed x per column makes the addresses
    // — and the uplinks on the client page — a column the eye can run down,
    // where the joined line started each one somewhere different. The
    // separators go with the alignment; they only ever stood in for it.
    //
    // All tertiary. The line is context for the name above it, and at
    // secondary its first column competed with the name for the same glance.
    Item {
      id: metaLine
      width: parent.width
      readonly property var slots: root.row && root.row.metaColumns
        ? root.row.metaColumns : ["", ""]
      readonly property real columnWidth: Math.round(width * 0.3)
      visible: secondary.text !== "" || slotA.text !== "" || slotB.text !== ""
      implicitHeight: visible ? secondary.implicitHeight : 0

      Text {
        id: secondary
        anchors.left: parent.left
        width: metaLine.columnWidth - Style.spacing.md
        text: root.row ? root.row.secondaryText : ""
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        id: slotA
        x: metaLine.columnWidth
        width: metaLine.columnWidth - Style.spacing.md
        text: metaLine.slots[0] || ""
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        id: slotB
        x: 2 * metaLine.columnWidth
        width: metaLine.width - x
        text: metaLine.slots[1] || ""
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
    }

    Text {
      width: parent.width
      visible: root.row ? root.row.updateAvailable === true : false
      text: "Firmware update available"
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      textFormat: Text.PlainText
    }

    // The detail, present only while this row is the expanded one. `visible`
    // AND a null `detail` guard: `vm` supplies the detail for one row per
    // build, so every other row's is null and binding into it would warn on
    // every frame.
    Column {
      width: parent.width
      spacing: Style.spacing.sm
      visible: root.expanded && root.detail !== null
      topPadding: visible ? Style.spacing.xs : 0

      PanelSeparator { foreground: root.foreground }

      DetailRows {
        width: parent.width
        rows: root.detail ? root.detail.rows : []
        foreground: root.foreground
        fontFamily: root.fontFamily
        emphasis: root.emphasis
        copiedKey: root.copiedKey
        onCopyRequested: function (key, text) { root.copyRequested(key, text) }
      }

      // AC-B09. This sentence and an empty port table are different claims:
      // this one says the helper never asked, and an empty table says the
      // controller answered and the device has no ports. `vm` keeps them
      // apart; this only renders whichever it was given.
      Text {
        width: parent.width
        visible: text !== ""
        text: root.detail && root.detail.unavailableText !== undefined
          ? root.detail.unavailableText : ""
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
      }

      PortTable {
        width: parent.width
        detail: root.detail
        foreground: root.foreground
        fontFamily: root.fontFamily
        emphasis: root.emphasis
      }
    }
  }

  // A `MouseArea`, and host-contract §7's "never write a MouseArea" does not
  // reach here: that rule is about BAR BUTTONS, where `Ui/WidgetButton` owns
  // hover, tooltip and click dispatch. For a list ROW the host writes a plain
  // MouseArea itself — bluetooth/Panel.qml:933-957 — and this is that shape,
  // hover-to-cursor sync included.
  //
  // `WidgetButton` is in fact unusable here, which is worth recording because
  // it looks like the right type: it is `visible: hasVisualContent || keepSpace`
  // and `hasVisualContent` is `text !== ""` (`Ui/WidgetButton.qml:29,67`), so a
  // transparent one with no text is INVISIBLE — and an invisible item takes no
  // mouse events at all. The row would simply not have been clickable.
  //
  // It covers the two identity lines only. A target over the expanded detail
  // would collapse the row when the user clicked in the port table they had
  // just opened to read.
  MouseArea {
    anchors.left: layout.left
    anchors.right: layout.right
    anchors.top: layout.top
    height: header.height + (metaLine.visible ? metaLine.height + layout.spacing : 0)
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.PointingHandCursor
    // The pointer's position goes with the signal, in SCENE coordinates so it
    // survives the row moving underneath it. `ViewModel.focusAfterHover` is
    // what needs it: this handler fires both when the user aims at the row and
    // when the freshness tick rebuilds the delegates under a hand that has not
    // moved, and the position is the only thing in the event that differs.
    onContainsMouseChanged: if (containsMouse) {
      root.hoverRequested(mapToItem(null, mouseX, mouseY))
    }
    onClicked: root.toggleRequested()
  }
}
