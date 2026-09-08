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
import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: root

  property var detail: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property var emphasis: null

  readonly property color _secondary: emphasis ? emphasis.secondary : foreground
  readonly property color _tertiary: emphasis ? emphasis.tertiary : foreground

  readonly property var ports: detail && detail.ports ? detail.ports : []
  readonly property string radiosText: detail && detail.radiosText
    ? detail.radiosText : ""
  readonly property string portsEmpty: detail && detail.portsEmptyText
    ? detail.portsEmptyText : ""
  readonly property string radiosEmpty: detail && detail.radiosEmptyText
    ? detail.radiosEmptyText : ""

  // One place, so a column cannot drift between rows. The state column widened
  // when the speed column went: "no link" is two words where "DOWN" was one.
  readonly property int idxWidth: Style.space(30)
  readonly property real stateShare: 0.34

  spacing: Style.spacing.xs
  visible: ports.length > 0 || radiosText !== ""
    || portsEmpty !== "" || radiosEmpty !== ""

  PanelSectionHeader {
    visible: root.ports.length > 0 || root.portsEmpty !== ""
    height: visible ? implicitHeight : 0
    text: "Ports"
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  Repeater {
    model: root.ports
    delegate: Item {
      required property var modelData
      width: root.width
      implicitHeight: portIndex.implicitHeight

      readonly property int _body: width - root.idxWidth

      Text {
        id: portIndex
        anchors.left: parent.left
        width: root.idxWidth
        text: modelData.idxText
        // A port that is up reads at full strength and one that is down does
        // not, which is the same weighting the offline list uses. UX-002:
        // never the only signal — `stateText` says the word beside it.
        color: modelData.isUp ? root._secondary : root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
      }
      Text {
        id: portState
        anchors.left: portIndex.right
        width: Math.round(parent._body * root.stateShare)
        text: modelData.stateText
        color: modelData.isUp ? root._secondary : root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        anchors.left: portState.right
        anchors.right: parent.right
        // Connector and PoE share the last column: both are occasional detail,
        // and giving each a column of its own would leave a mostly-empty one
        // down the middle of the table.
        text: modelData.connectorText
          + (modelData.poeText === "" ? "" : "  ·  " + modelData.poeText)
        color: root._tertiary
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
    }
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

  PanelSectionHeader {
    visible: root.radiosText !== "" || root.radiosEmpty !== ""
    height: visible ? implicitHeight : 0
    text: "Radios"
    foreground: root.foreground
    fontFamily: root.fontFamily
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
