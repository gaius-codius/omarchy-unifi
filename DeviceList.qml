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

  readonly property int listed: offline ? offline.devices.length : 0

  spacing: Style.spacing.xs
  // Present whenever there is a reading, whatever the reading says. The
  // section's whole job is to answer "is anything broken?", and it used to
  // answer it by not being there — which is also what a rendering fault and a
  // poll that never arrived look like. With no snapshot it is hidden, because
  // then nothing HAS been checked and an all-clear would be a claim; `vm`
  // decides that by leaving `summaryText` empty (ViewModel.js).
  // `active`, and not a second `visible` binding, because a `visible` set at
  // the CALL SITE replaces the one written here — a derived binding wins over
  // the base's. Panel.qml binds `visible: !root.browsing` on this instance, so
  // the guard this file used to carry never ran at all, and the section drew
  // its heading on every Overview whatever the reading said. That is the empty
  // "Offline and impaired" heading in the shipped screenshot: not a mistaken
  // condition, a condition that was never evaluated.
  //
  // The two facts now live where each belongs. The panel owns "is this page
  // showing"; this file owns "has this section anything to say".
  property bool active: true
  visible: active && (offline ? (listed > 0 || offline.summaryText !== "") : false)

  PanelSeparator { foreground: root.foreground }

  // The heading belongs to the LIST, not to the section. Guarded on the rows
  // actually present rather than on the count, because a count with an empty
  // array — which is what the shipped Overview screenshot shows — drew a rule,
  // a heading, and nothing under it. The count is still reported; it is
  // reported as a sentence, below, instead of as a heading over emptiness.
  SectionHeader {
    visible: root.listed > 0
    text: "Offline and impaired"
    foreground: root.foreground
    fontFamily: root.fontFamily
    emphasis: root.emphasis
  }

  // Said out loud, in the same place on screen, whether the answer is good or
  // bad — so the reader confirms it rather than inferring it from a gap.
  // Tertiary: it is reassurance when it reads "Nothing offline or impaired",
  // and the list itself carries the weight when there is one.
  Text {
    visible: text !== ""
    width: root.width
    text: root.offline ? root.offline.summaryText : ""
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
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
