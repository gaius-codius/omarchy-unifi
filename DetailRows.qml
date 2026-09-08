// REQ-B14's expanded detail: label on the left, value on the right.
//
// Shared by both pages because both details are the same shape — an array of
// `{ key, label, value, copy }` decided in `ViewModel.js`. It is deliberately
// NOT given the port and radio tables: a client has no ports, and padding the
// client detail with empty `ports`/`radios` so one component could render both
// would be inventing fields to make a delegate shorter. The Devices page owns
// those tables, which is where the difference actually is.
//
// Every `value` is already a string when it arrives (BIZ-003 / AC-B10: `null`
// renders as "unknown", never as 0), so nothing here decides anything —
// REQ-014. That includes which rows can be copied: `copy` is non-empty exactly
// when there is a real value behind the rendered one, so this file never has to
// know that "unknown" is a placeholder and not an address.
import QtQuick
import qs.Commons

Column {
  id: root

  property var rows: []
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property var emphasis: null
  // The key of the row most recently copied, or "". Owned by the panel, which
  // also clears it — this component is rebuilt on every poll and could not hold
  // the state across one.
  property string copiedKey: ""

  readonly property color _secondary: emphasis ? emphasis.secondary : foreground
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground

  signal copyRequested(string key, string text)

  spacing: Style.spacing.xs

  Repeater {
    model: root.rows
    delegate: Item {
      id: detailRow
      required property var modelData
      width: root.width
      implicitHeight: Math.max(detailLabel.implicitHeight, detailValue.implicitHeight)

      readonly property bool copyable: modelData.copy !== ""
      readonly property bool copied: root.copiedKey === modelData.key

      // The hover tint is the only resting affordance. A copyable value should
      // not shout about it — most of these rows are read, not clicked — but it
      // has to answer the pointer, or the feature is invisible.
      Rectangle {
        anchors.fill: parent
        anchors.leftMargin: -Style.spacing.xs
        anchors.rightMargin: -Style.spacing.xs
        radius: Style.cornerRadius
        color: Style.controlFill(false, true, root.foreground, root.foreground)
        visible: detailRow.copyable && copyArea.containsMouse
      }

      Text {
        id: detailLabel
        anchors.left: parent.left
        width: Math.round(parent.width * 0.28)
        text: modelData.label
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        id: detailValue
        anchors.left: detailLabel.right
        anchors.right: copiedMark.left
        anchors.rightMargin: copiedMark.visible ? Style.spacing.xs : 0
        text: modelData.value
        color: root._secondary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }

      // The confirmation. Copying is otherwise completely silent — the value
      // looks identical before and after — so without this there is no way to
      // tell a successful copy from a click that missed.
      //
      // No Timer. The panel clears it on the next thing the user does: another
      // copy, a page change, collapsing the row, closing the panel. A message
      // that waits for the next action instead of a deadline cannot disappear
      // while it is being read.
      Text {
        id: copiedMark
        anchors.right: parent.right
        anchors.baseline: detailValue.baseline
        visible: detailRow.copied
        width: visible ? implicitWidth : 0
        text: "Copied"
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
      }

      MouseArea {
        id: copyArea
        anchors.fill: parent
        enabled: detailRow.copyable
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: Qt.PointingHandCursor
        // The RAW value — `modelData.copy`, not `modelData.value`. The two
        // differ wherever the rendering added anything, and what should land on
        // the clipboard is what the reader would otherwise have typed.
        onClicked: root.copyRequested(modelData.key, modelData.copy)
      }
    }
  }
}
