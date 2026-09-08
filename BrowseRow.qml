// One row of the Devices or Clients list (REQ-B14), and its detail when open.
//
// One component for both lists, not two that look alike. Every string it draws
// was decided in `ViewModel.js` — `nameText`, `secondaryText` (a device's model,
// a client's IP, in the same place and meaning the same thing), `metaText` — so
// this file makes no decision about what a row says. That is REQ-014, and it is
// what lets 33 `node --test` cases cover the wording of a list the harness only
// has to prove is on screen.
//
// The MAC address is not here (REQ-B21 / D3). It appears in `detailRows` and
// therefore only after a deliberate expansion.
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
  readonly property color dim: Qt.darker(foreground, 1.4)

  signal toggleRequested()
  signal hoverRequested()

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
    // Item and not the whole row: a target over the expanded detail would
    // collapse the row when the user clicked in the port table they had just
    // opened to read.
    Item {
      id: header
      width: parent.width
      implicitHeight: Math.max(primary.implicitHeight, secondary.implicitHeight)

      Text {
        id: primary
        anchors.left: parent.left
        anchors.right: secondary.left
        anchors.rightMargin: Style.spacing.md
        text: root.row ? root.row.nameText : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }

      Text {
        id: secondary
        anchors.right: parent.right
        // Capped so a long model name cannot squeeze the name it belongs to
        // down to an ellipsis. The name is what the user searched for.
        width: Math.min(implicitWidth, Math.round(parent.width * 0.4))
        horizontalAlignment: Text.AlignRight
        text: root.row ? root.row.secondaryText : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
    }

    Text {
      id: metaLine
      width: parent.width
      visible: text !== ""
      text: root.row ? root.row.metaText : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      textFormat: Text.PlainText
      elide: Text.ElideRight
    }

    // REQ-B14's "update available" mark. `vm` decided whether to show it —
    // `firmwareUpdatable === true` and not truthiness, because `null` means the
    // controller did not say and a mark on that basis invents the fact.
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
      spacing: Style.spacing.xs
      visible: root.expanded && root.detail !== null

      PanelSeparator { foreground: root.foreground }

      DetailRows {
        width: parent.width
        rows: root.detail ? root.detail.rows : []
        foreground: root.foreground
        fontFamily: root.fontFamily
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
        color: root.dim
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
  // It covers the header and the meta line only. A target over the expanded
  // detail would collapse the row when the user clicked in the port table they
  // had just opened to read.
  MouseArea {
    anchors.left: layout.left
    anchors.right: layout.right
    anchors.top: layout.top
    height: header.height + (metaLine.visible ? metaLine.height + layout.spacing : 0)
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton
    cursorShape: Qt.PointingHandCursor
    onContainsMouseChanged: if (containsMouse) root.hoverRequested()
    onClicked: root.toggleRequested()
  }
}
