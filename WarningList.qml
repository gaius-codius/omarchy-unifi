// REQ-013a: the envelope's warnings, as a bounded list.
//
// Two properties of this list are requirements rather than choices. It never
// changes the health colour — a warning is a note about how the reading was
// obtained, not a reading — and it is bounded, because the helper's collector
// bounds it at 32 (warn.py) and a panel that grew without limit would push the
// buttons off the bottom of a popup that cannot scroll past them.
//
// The code is printed beside the sentence on purpose: a bug report that quotes
// the sentence is not searchable, and one that quotes the code is.
import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: root

  property var vm: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  readonly property color dim: Qt.darker(foreground, 1.4)

  readonly property var rows: vm && vm.warningRows ? vm.warningRows : []

  spacing: Style.spacing.xs
  visible: rows.length > 0

  PanelSeparator { foreground: root.foreground }

  PanelSectionHeader {
    text: "Warnings"
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  Repeater {
    model: root.rows
    delegate: Column {
      id: warningRow
      required property var modelData
      width: root.width
      spacing: 0

      Text {
        width: parent.width
        text: warningRow.modelData.text
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
      }
      Text {
        text: warningRow.modelData.code
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
      }
    }
  }
}
