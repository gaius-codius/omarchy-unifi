// REQ-B14's port table and radio line, and AC-B09's distinction.
//
// Both are here, and both empty sentences with them, because they are the same
// question asked twice: did the controller answer, and if it did, what did it
// say? `ViewModel.deviceDetail` decides — `portsEmptyText` is non-empty only
// when the detail WAS fetched and the array is genuinely empty, and
// `unavailableText` (rendered by the caller, above this) is non-empty only when
// it was not fetched at all. This file renders whichever it was handed and
// keeps no rule of its own, so the two can never be confused here.
//
// Nothing in it is drawn for a client: `detail.ports` is absent on a client
// detail, which the guards below read as false. That is deliberate — the client
// detail is not padded with empty device fields just to make one component
// serve both.
//
// The ports are a TABLE, in the sense of having columns that line up. They used
// to join their fields into one elided line — `up  ·  RJ45  ·  1000 Mbps  ·
// PoE 802.3at` — which reads as a sentence and cannot be scanned.
//
// The radios are NOT a table, and used to be. Their second column was the
// transmit-retry rate, which this controller does not report on any radio of
// any access point, so the table was a column of bands beside a column of the
// word "unknown". `radiosText` is one line, and carries the retry figure inline
// wherever a controller does report it.
// "Ports" and "Radios" are LABELS here, not sections.
//
// They used to be `PanelSectionHeader`, the same type that heads "Uplink" and
// "Inventory" at panel level — so a label two levels down, inside one expanded
// row of one list, wore the top level's clothes. Whatever heading treatment
// the panel adopts, applying it here would say that a device's port table
// ranks with the site's uplink.
//
// Inside a row these belong to the same class as Firmware, CPU and Memory:
// tertiary, caption, and read when looked at. See `SectionHeader.qml` for the
// one level the panel does have.
import QtQuick
import qs.Commons

Column {
  id: root

  property var detail: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property var emphasis: null

  readonly property color _secondary: emphasis ? emphasis.secondary : foreground
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground
  readonly property color _ground: emphasis ? emphasis.background : Color.background

  readonly property var ports: detail && detail.ports ? detail.ports : []
  readonly property string radiosText: detail && detail.radiosText
    ? detail.radiosText : ""
  readonly property string portsEmpty: detail && detail.portsEmptyText
    ? detail.portsEmptyText : ""
  readonly property string portsSummary: detail && detail.portsSummaryText
    ? detail.portsSummaryText : ""
  readonly property string radiosEmpty: detail && detail.radiosEmptyText
    ? detail.radiosEmptyText : ""

  spacing: Style.spacing.xs
  visible: ports.length > 0 || radiosText !== ""
    || portsEmpty !== "" || radiosEmpty !== ""

  Text {
    visible: root.ports.length > 0 || root.portsEmpty !== ""
    height: visible ? implicitHeight : 0
    text: "Ports"
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }

  // The ports, as a grid of marks.
  //
  // They were one row per port. On the USW-Pro-48-PoE this plugin is tested
  // against that is 48 rows inside a popup capped at 560 px — a detail that
  // cannot be opened rather than one that is merely long. And the thing a
  // reader wants from a port table at a glance is not any single port, it is
  // the SHAPE: how many are up, where the gaps are, which carry power.
  //
  // A mark per port answers that in three lines. Filled when the link is up,
  // outlined when it is not, underscored when the port carries PoE — and the
  // index is printed inside every mark, so an individual port is still
  // identifiable rather than merely counted.
  //
  // The shape is never the only signal (UX-002): `portsSummaryText` states the
  // same counts in words beside the grid, and that sentence is what a bug
  // report can quote.
  Flow {
    width: root.width
    visible: root.ports.length > 0
    spacing: Style.spacing.xs

    Repeater {
      model: root.ports

      delegate: Item {
        id: mark
        required property var modelData

        // Square, and sized from the type rather than in pixels so it keeps
        // step with the user's font scaling like everything else here. The
        // Item is taller than the square by the PoE rule and its gap, so the
        // rule sits UNDER the mark rather than across the digits inside it.
        readonly property int side: Math.round(Style.font.caption * 1.5)
        readonly property int ruleGap: Math.max(1, Math.round(side / 8))
        width: side
        height: side + ruleGap + 1

        // Up is a solid fill, down is an outline, both from the emphasis
        // scale, so nothing here invents a colour (REQ-001a / UX-001) and the
        // grid re-themes with everything else.
        //
        // The fill was `Style.controlFill(true, …)`. That is a control's focus
        // WASH — foreground at 8% alpha — and inside an expanded row, which is
        // already washed, it all but vanished: on hardware the up ports read
        // as the faint ones and the down ports, which kept their border, as
        // the strong ones. The inverse of what the grid says.
        Rectangle {
          width: mark.side
          height: mark.side
          radius: Math.max(1, Math.round(mark.side / 6))
          color: mark.modelData.isUp ? root._secondary : "transparent"
          border.width: mark.modelData.isUp ? 0 : 1
          border.color: root._tertiary

          Text {
            anchors.centerIn: parent
            text: mark.modelData.idxText
            // On the fill, the ground colour: the index is knocked out of the
            // mark. On an outline, the same tertiary as the border.
            color: mark.modelData.isUp ? root._ground : root._tertiary
            font.family: root.fontFamily
            // A step below caption: the index identifies the mark, it does
            // not compete with the row's own text.
            font.pixelSize: Math.max(8, Math.round(Style.font.caption * 0.8))
            font.bold: mark.modelData.isUp
            textFormat: Text.PlainText
          }
        }

        // PoE, as a rule under the mark. A third state on the same square
        // rather than a fourth colour, because PoE is orthogonal to link state
        // — a port can be down and still powered — and two overlaid colours
        // cannot say that.
        Rectangle {
          anchors.bottom: parent.bottom
          anchors.horizontalCenter: parent.horizontalCenter
          width: mark.side - 2 * Math.max(1, Math.round(mark.side / 6))
          height: 1
          color: root._secondary
          visible: mark.modelData.poeText !== ""
        }
      }
    }
  }

  // The counts, in words. See the grid above: this is what keeps the marks from
  // being the only statement of the same fact.
  Text {
    visible: text !== ""
    width: root.width
    text: root.portsSummary
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
  }

  // "The controller answered and there are none" — never shown for a device
  // whose detail was not fetched, which is AC-B09's whole point.
  Text {
    visible: text !== ""
    width: root.width
    text: root.portsEmpty
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }

  Text {
    visible: root.radiosText !== "" || root.radiosEmpty !== ""
    height: visible ? implicitHeight : 0
    text: "Radios"
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }

  Text {
    visible: root.radiosText !== ""
    width: root.width
    text: root.radiosText
    color: root._secondary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
  }

  Text {
    visible: text !== ""
    width: root.width
    text: root.radiosEmpty
    color: root._tertiary
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }
}
