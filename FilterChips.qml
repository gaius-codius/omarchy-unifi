// REQ-B10a's filter chooser, drawn so it does not compete with the pages.
//
// It was an `Ui/ButtonGroup`, which is the same control the page chips one row
// above it are. Same shape, same border, same selected fill, differing by one
// point of type — so two different levels of the hierarchy arrived in identical
// chrome, directly stacked, and the pair read as one control with seven options
// rather than as a page and a condition on it. `BrowseList` recorded that as
// "the risk this accepts"; on screen it is the first thing under the hero.
//
// So this row keeps the kit's tokens and drops the kit's chrome. No borders, no
// fill: the selected option is marked with a rule underneath it, which is a
// weaker mark for a subordinate control, and the unselected ones sit at
// tertiary. One bordered chip row per panel, and it belongs to the pages.
//
// Plain `Text` + `MouseArea` rather than `Ui/Button`, because the kit's Button
// has no `selected` of its own — `ButtonGroup` owns that — and a borderless
// Button row could not say which option is current. §7's "never write a
// MouseArea" is about BAR BUTTONS; inside a panel the host writes them itself
// (bluetooth/Panel.qml:933-957), as three other files in this plugin already do.
//
// It decides nothing: `options` and `value` arrive from the list model and a
// click emits. Which page a change applies to is the panel's business (REQ-014).
import QtQuick
import qs.Commons

Item {
  id: root

  // `[{ value, label }]`, from `list.filterChips`.
  property var options: []
  property string value: ""
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property var emphasis: null

  signal changed(string value)

  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground

  visible: options.length > 0
  implicitHeight: visible ? chips.implicitHeight : 0
  height: implicitHeight

  Row {
    id: chips
    // Left only. A Row sizes itself to its content; anchoring both edges would
    // fix its width and clip the last chip.
    //
    // No `f` hint at the right edge. It was printed there as the chips'
    // keyboard key, and alone at the far edge it read as a stray character
    // rather than as a key. `f` is named in the `? keys` legend.
    anchors.left: parent.left
    // Wide, because nothing else separates the options: no border, no fill.
    // At `md` the words ran together into one phrase — "All Gateways
    // Switches Access points" — which is the opposite of a choice. 16 px at
    // the default font size, from `Style.space` so it scales with it.
    spacing: Style.space(16)

    Repeater {
      model: root.options

      delegate: Item {
        id: chip
        required property var modelData
        readonly property bool selected: root.value === modelData.value

        width: chipLabel.implicitWidth
        implicitHeight: chipLabel.implicitHeight + Style.spacing.xs
        height: implicitHeight

        Text {
          id: chipLabel
          anchors.top: parent.top
          anchors.left: parent.left
          text: chip.modelData.label
          // Full strength when chosen, tertiary when not. The rule below is
          // the primary mark; this is the second half of it, so the state is
          // never carried by one signal alone (UX-002).
          color: chip.selected ? root.foreground : root._tertiary
          font.family: root.fontFamily
          // One step under the page tabs (`body`), not two. At caption the
          // row was the smallest text in the panel beside the largest gaps.
          font.pixelSize: Style.font.bodySmall
          textFormat: Text.PlainText
        }

        // The selected mark. A rule rather than a fill: a fill is what the page
        // chips above use, and the whole point of this row is to read as
        // subordinate to them.
        Rectangle {
          anchors.left: chipLabel.left
          anchors.right: chipLabel.right
          anchors.top: chipLabel.bottom
          anchors.topMargin: Math.max(2, Math.round(Style.spacing.xs * 0.75))
          height: 1
          color: root.foreground
          visible: chip.selected
        }

        MouseArea {
          anchors.fill: parent
          anchors.leftMargin: -Style.spacing.xs
          anchors.rightMargin: -Style.spacing.xs
          hoverEnabled: true
          acceptedButtons: Qt.LeftButton
          cursorShape: Qt.PointingHandCursor
          onClicked: root.changed(chip.modelData.value)
        }
      }
    }
  }
}
