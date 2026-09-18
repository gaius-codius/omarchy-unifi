// REQ-008 / REQ-008a / REQ-009: the uplink, the gateways, and the counts.
//
// Every string on screen here arrives pre-rendered on `vm`. There is no
// arithmetic, no formatting and no conditional wording in this file: what the
// panel says about a snapshot is decided in ViewModel.js, where a test can read
// it, and the "unknown is not zero" rule (BIZ-003) is applied there exactly
// once. If a number is being computed in this file, it is in the wrong file.
import QtQuick
import qs.Commons

Column {
  id: root

  property var vm: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  // REQ-B10a. Emitted with the role value `devices[].roles` uses, for the panel
  // to turn into a filtered Devices page. This component does not know what a
  // page is, which is why it emits rather than navigating.
  signal roleActivated(string role)
  // "Connected clients" and "Adopted devices" are entry points too: each opens
  // its own page, unfiltered, so the list under the pointer is the number the
  // user just clicked. `view` is "clients" or "devices".
  signal pageActivated(string view)
  // The panel's scale, handed down rather than rebuilt. This was a local copy
  // of `Emphasis.qml`'s `level(0.52)` — "one level is all it uses" was true,
  // and still made this the third file a change to the scale had to be made
  // in. The contrast fix that moved tertiary to 0.62 would have applied to
  // two thirds of the panel and looked, from inside the panel, like it had
  // worked.
  property var emphasis: null
  readonly property color dim: emphasis ? emphasis.tertiary : foreground

  readonly property var model: vm ? vm : null

  // The section rhythm, and why there are no rules between these blocks.
  //
  // Every section used to arrive with its own `PanelSeparator`, spaced by the
  // same `spacing.md` that separated the rows INSIDE it. So the gaps carried no
  // information — a row break and a section break measured the same — and five
  // horizontal rules in a 560 px popup spent a divider that is only strong
  // while it is rare.
  //
  // Now: `md` between sections, `xs` within them, and no rules at all here. The
  // grouping is done by the space and by the heading treatment
  // (`SectionHeader.qml`). The rules that survive in this panel are the two
  // where the KIND of content changes — Warnings, and Details — and they mean
  // something again.
  spacing: Style.spacing.md

  // --- REQ-008 / REQ-008a: the uplink -------------------------------------
  Column {
    width: root.width
    spacing: Style.spacing.xs

    // The heading, and on a single-gateway site the gateway's model beside it.
    //
    // That model name is the ONE thing the Gateways section below adds when
    // there is only one gateway to attribute the numbers to, so it comes up
    // here and the section goes away. It is drawn as its own Text rather than
    // appended to the heading's string because `SectionHeader` upper-cases
    // what it is given, and "UDM-PRO" is not what the controller calls it.
    Item {
      width: parent.width
      implicitHeight: uplinkHeading.implicitHeight

      SectionHeader {
        id: uplinkHeading
        anchors.left: parent.left
        text: "Uplink"
        foreground: root.foreground
        fontFamily: root.fontFamily
        emphasis: root.emphasis
      }
      Text {
        anchors.right: parent.right
        anchors.baseline: uplinkHeading.baseline
        visible: text !== ""
        text: (root.vm && root.vm.gatewayRows.length === 1)
          ? root.vm.gatewayRows[0].nameText : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        elide: Text.ElideRight
      }
    }

    Repeater {
      model: root.vm ? root.vm.wanRows : []
      delegate: Item {
        required property var modelData
        width: root.width
        implicitHeight: Math.max(wanLabel.implicitHeight, wanValue.implicitHeight)

        Text {
          id: wanLabel
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: modelData.label
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          textFormat: Text.PlainText
        }
        Text {
          id: wanValue
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: modelData.value
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          textFormat: Text.PlainText
        }
      }
    }
  }

  // --- REQ-008a: every gateway, individually -------------------------------
  //
  // The uplink block above carries the PRIMARY gateway's metrics under an
  // aggregate status. On a multi-gateway site those two are not the same fact,
  // so the list is not a nicety: without it "Uptime 4d" over "WAN Degraded" is
  // unattributable.
  //
  // Which is exactly why it is gated at MORE THAN ONE. With a single gateway
  // there is nothing to attribute — the aggregate status and that gateway's
  // status are the same fact — and the block reprinted uptime, download and
  // upload verbatim, 90 px under the identical figures in Uplink. Its only
  // unique contribution was the model name, which now sits in the Uplink
  // heading. Four rows back, on the common configuration, with nothing lost.
  Column {
    id: gatewayList
    width: root.width
    spacing: Style.spacing.xs
    visible: root.vm ? root.vm.gatewayRows.length > 1 : false

    SectionHeader {
      text: "Gateways"
      foreground: root.foreground
      fontFamily: root.fontFamily
      emphasis: root.emphasis
    }

    Repeater {
      model: root.vm ? root.vm.gatewayRows : []
      delegate: Column {
        required property var modelData
        width: gatewayList.width
        spacing: 0

        Text {
          text: modelData.nameText + "  \u00b7  " + modelData.classText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          textFormat: Text.PlainText
          elide: Text.ElideRight
          width: parent.width
        }
        Text {
          // Pre-rendered, because this line has to say WHY there are no
          // numbers and that is a branch on business state. Composed here, it
          // read "statistics not fetched" for every gateway whose three
          // metrics were null — including one whose `statistics/latest` call
          // was made and failed, which is the envelope telling the user the
          // opposite of what happened. `ViewModel.gatewayRows` separates the
          // two, and a Node test can now read the sentence.
          text: modelData.detailText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          textFormat: Text.PlainText
          elide: Text.ElideRight
          width: parent.width
        }
      }
    }
  }

  // --- REQ-009: clients, devices, and the role rows -------------------------
  Column {
    width: root.width
    spacing: Style.spacing.xs

    SectionHeader {
      text: "Inventory"
      foreground: root.foreground
      fontFamily: root.fontFamily
      emphasis: root.emphasis
    }

    // Every row in this section navigates, and they are all one component so
    // they cannot drift apart in how they look under the pointer. The two
    // counts open their pages; the role rows open Devices filtered to a role.
    NavRow {
      foreground: root.foreground
      dim: root.dim
      fontFamily: root.fontFamily
      label: "Connected clients"
      valueText: root.vm ? root.vm.clientsText : "unknown"
      onActivated: root.pageActivated("clients")
    }

    NavRow {
      foreground: root.foreground
      dim: root.dim
      fontFamily: root.fontFamily
      label: "Adopted devices"
      valueText: root.vm ? root.vm.devicesTotalText : "unknown"
      onActivated: root.pageActivated("devices")
    }

    // REQ-009 / REQ-B10a. The role rows. They used to be a two-line block — a
    // foreground label with the class cells in a Row underneath — which put two
    // opposite emphasis rules in one section, and only that kind was clickable.
    // `valueText` composes the cells (ViewModel.js) so the delegate stays a
    // delegate, and each role costs one line instead of two.
    Repeater {
      model: root.vm ? root.vm.countRows : []
      delegate: NavRow {
        required property var modelData
        foreground: root.foreground
        dim: root.dim
        fontFamily: root.fontFamily
        label: modelData.label
        valueText: modelData.valueText
        onActivated: root.roleActivated(modelData.role)
      }
    }
  }

  // One Overview row that goes somewhere: label left, value right, a resting
  // chevron, and the hover rectangle `DetailRows` draws.
  component NavRow: Item {
    id: roleRow
    // Passed in rather than read from `root`: an inline component does not
    // share the enclosing file's id scope.
    property color foreground: Color.foreground
    property color dim: foreground
    property string fontFamily: Style.font.family
    property string label: ""
    property string valueText: ""
    signal activated()
    width: parent ? parent.width : 0
    implicitHeight: Math.max(roleLabel.implicitHeight, roleValue.implicitHeight)
    height: implicitHeight

    // The resting and hover affordances this row never had. It is the only
    // navigation in the panel besides the page chips, and it announced itself
    // with a cursor shape — which nobody sees until they are already on it.
    // Meanwhile `DetailRows`, whose rows merely copy a string, drew a hover
    // rectangle. Same rectangle, same token, same geometry as that one, so a
    // row under the pointer here reads exactly as it does there.
    Rectangle {
      anchors.fill: parent
      anchors.leftMargin: -Style.spacing.xs
      anchors.rightMargin: -Style.spacing.xs
      radius: Style.cornerRadius
      color: Style.controlFill(false, true, roleRow.foreground, roleRow.foreground)
      visible: roleArea.containsMouse
    }

    Text {
      id: roleLabel
      anchors.left: parent.left
      anchors.right: roleValue.left
      anchors.rightMargin: Style.spacing.md
      text: roleRow.label
      color: roleRow.dim
      font.family: roleRow.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
      elide: Text.ElideRight
    }

    Text {
      id: roleValue
      anchors.right: chevron.left
      anchors.rightMargin: Style.spacing.xs
      // SPEC-AMD-3: every cell behind this string is non-zero, because
      // `countRows` drops the empty classes. The colour is unconditional for
      // that reason and not because the distinction stopped mattering — a
      // zero arriving here would be a defect in ViewModel.js, and dimming it
      // would hide that.
      text: roleRow.valueText
      color: roleRow.foreground
      font.family: roleRow.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }

    // The resting half of the affordance: the row says it goes somewhere
    // before the pointer arrives, and keeps saying it for a keyboard user who
    // never produces a hover at all. Tertiary, because it is a mark about the
    // row rather than part of the reading.
    Text {
      id: chevron
      anchors.right: parent.right
      anchors.baseline: roleValue.baseline
      text: "\u203a"
      color: roleRow.dim
      font.family: roleRow.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }

    // REQ-B10a, and its extension to the two count rows: activating the row
    // is the caller's decision (`activated`), so the role rows can carry the
    // role value from `ViewModel.countRows` untranslated and the count rows can
    // name a page.
    //
    // A `MouseArea`, as the host's own panel rows use
    // (bluetooth/Panel.qml:933-957). §7's "never write a MouseArea" is about
    // BAR BUTTONS; `Ui/WidgetButton` is additionally unusable for an
    // invisible target, being `visible: hasVisualContent || keepSpace` with
    // `hasVisualContent: text !== ""`.
    MouseArea {
      id: roleArea
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton
      cursorShape: Qt.PointingHandCursor
      onClicked: roleRow.activated()
    }
  }
}
