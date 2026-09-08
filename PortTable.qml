// REQ-B14's port and radio tables, and AC-B09's distinction.
//
// Both tables and both empty sentences live here because they are the same
// question asked twice: did the controller answer, and if it did, what did it
// say? `ViewModel.deviceDetail` decides — `portsEmptyText` is non-empty only
// when the detail WAS fetched and the array is genuinely empty, and
// `unavailableText` (rendered by the caller, above this) is non-empty only when
// it was not fetched at all. This file renders whichever it was handed and
// keeps no rule of its own, so the two can never be confused here.
//
// Nothing in it is drawn for a client: `detail.ports` is absent on a client
// detail, which `hasPorts` reads as false. That is deliberate — the client
// detail is not padded with empty device fields just to make one component
// serve both.
import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: root

  property var detail: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  readonly property color dim: Qt.darker(foreground, 1.4)

  readonly property var ports: detail && detail.ports ? detail.ports : []
  readonly property var radios: detail && detail.radios ? detail.radios : []
  readonly property string portsEmpty: detail && detail.portsEmptyText
    ? detail.portsEmptyText : ""
  readonly property string radiosEmpty: detail && detail.radiosEmptyText
    ? detail.radiosEmptyText : ""

  spacing: Style.spacing.xs
  visible: ports.length > 0 || radios.length > 0
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
      implicitHeight: Math.max(portIndex.implicitHeight, portDetail.implicitHeight)

      Text {
        id: portIndex
        anchors.left: parent.left
        width: Style.space(28)
        text: modelData.idxText
        // A port that is up reads at full strength and one that is down does
        // not, which is the same weighting the offline list uses. UX-002:
        // never the only signal — `stateText` says the word beside it.
        color: modelData.isUp ? root.foreground : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
      }
      Text {
        id: portDetail
        anchors.left: portIndex.right
        anchors.right: parent.right
        text: modelData.stateText + "  ·  " + modelData.connectorText
          + "  ·  " + modelData.speedText
          + (modelData.poeText === "" ? "" : "  ·  " + modelData.poeText)
        color: root.dim
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
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }

  PanelSectionHeader {
    visible: root.radios.length > 0 || root.radiosEmpty !== ""
    height: visible ? implicitHeight : 0
    text: "Radios"
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  Repeater {
    model: root.radios
    delegate: Text {
      required property var modelData
      width: root.width
      text: modelData.frequencyText + "  ·  " + modelData.retriesText
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      textFormat: Text.PlainText
      elide: Text.ElideRight
    }
  }

  Text {
    visible: text !== ""
    width: root.width
    text: root.radiosEmpty
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }
}
