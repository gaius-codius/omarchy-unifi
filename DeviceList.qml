// REQ-010: the devices that are down or impaired, and the honest remainder.
//
// The "and N more" line comes from `vm.offline.moreLabel`, which ViewModel.js
// computes from `counts.offlineTotal` — an independent integer — and never from
// the length of the truncated array. That is the whole point of AC-063: a site
// with five hundred devices down must not report an outage as if ten machines
// were affected because ten is how many fit in the array.
//
// REQ-003: a `transitional` device is never in this list. It is not offline,
// and putting an access point that is merely updating next to one that is down
// would make a routine firmware push look like a fault.
import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: root

  property var vm: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  // The panel's scale, handed down rather than rebuilt. This was a local copy
  // of `Emphasis.qml`'s `level(0.52)` — "one level is all it uses" was true,
  // and still made this the third file a change to the scale had to be made
  // in. The contrast fix that moved tertiary to 0.62 would have applied to
  // two thirds of the panel and looked, from inside the panel, like it had
  // worked.
  property var emphasis: null
  readonly property color dim: emphasis ? emphasis.tertiary : foreground

  readonly property var offline: vm && vm.offline ? vm.offline : null

  spacing: Style.spacing.xs
  visible: offline ? (offline.devices.length > 0 || offline.total > 0) : false

  PanelSeparator { foreground: root.foreground }

  SectionHeader {
    text: "Offline and impaired"
    foreground: root.foreground
    fontFamily: root.fontFamily
    emphasis: root.emphasis
  }

  Repeater {
    model: root.offline ? root.offline.devices : []
    delegate: Item {
      required property var modelData
      width: root.width
      implicitHeight: Math.max(deviceName.implicitHeight, deviceState.implicitHeight)

      Text {
        id: deviceName
        anchors.left: parent.left
        anchors.right: deviceState.left
        anchors.rightMargin: Style.spacing.lg
        text: modelData.nameText + "  ·  " + modelData.modelText
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
      Text {
        id: deviceState
        anchors.right: parent.right
        text: modelData.classText
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
      }
    }
  }

  Text {
    visible: text !== ""
    width: root.width
    text: root.offline ? root.offline.moreLabel : ""
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
  }
}
