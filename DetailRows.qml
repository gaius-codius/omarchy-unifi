// REQ-B14's expanded detail: label on the left, value on the right.
//
// Shared by both pages because both details are the same shape — an array of
// `{ key, label, value }` decided in `ViewModel.js`. It is deliberately NOT
// given the port and radio tables: a client has no ports, and padding the
// client detail with empty `ports`/`radios` so one component could render both
// would be inventing fields to make a delegate shorter. The Devices page owns
// those tables, which is where the difference actually is.
//
// Every `value` is already a string when it arrives (BIZ-003 / AC-B10: `null`
// renders as "unknown", never as 0), so nothing here decides anything —
// REQ-014.
import QtQuick
import qs.Commons

Column {
  id: root

  property var rows: []
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  readonly property color dim: Qt.darker(foreground, 1.4)

  spacing: Style.spacing.xs

  Repeater {
    model: root.rows
    delegate: Item {
      required property var modelData
      width: root.width
      implicitHeight: Math.max(detailLabel.implicitHeight, detailValue.implicitHeight)

      Text {
        id: detailLabel
        anchors.left: parent.left
        width: Math.round(parent.width * 0.32)
        text: modelData.label
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        id: detailValue
        anchors.left: detailLabel.right
        anchors.right: parent.right
        text: modelData.value
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
    }
  }
}
