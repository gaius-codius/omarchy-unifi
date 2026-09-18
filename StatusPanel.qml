// REQ-008 / REQ-008a / REQ-009: the uplink, the gateways, and the counts.
//
// Every string on screen here arrives pre-rendered on `vm`. There is no
// arithmetic, no formatting and no conditional wording in this file: what the
// panel says about a snapshot is decided in ViewModel.js, where a test can read
// it, and the "unknown is not zero" rule (BIZ-003) is applied there exactly
// once. If a number is being computed in this file, it is in the wrong file.
import QtQuick
import qs.Commons
import qs.Ui

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
  // The panel's scale, handed down rather than rebuilt. This was a local copy
  // of `Emphasis.qml`'s `level(0.52)` — "one level is all it uses" was true,
  // and still made this the third file a change to the scale had to be made
  // in. The contrast fix that moved tertiary to 0.62 would have applied to
  // two thirds of the panel and looked, from inside the panel, like it had
  // worked.
  property var emphasis: null
  readonly property color dim: emphasis ? emphasis.tertiary : foreground

  readonly property var model: vm ? vm : null

  spacing: Style.spacing.md

  // --- REQ-008 / REQ-008a: the uplink -------------------------------------
  PanelSectionHeader {
    text: "Uplink"
    foreground: root.foreground
    fontFamily: root.fontFamily
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

  // --- REQ-008a: every gateway, individually -------------------------------
  //
  // The uplink block above carries the PRIMARY gateway's metrics under an
  // aggregate status. On a multi-gateway site those two are not the same fact,
  // so the list is not a nicety: without it "Uptime 4d" over "WAN Degraded" is
  // unattributable.
  PanelSeparator {
    visible: gatewayList.visible
    foreground: root.foreground
  }

  PanelSectionHeader {
    visible: gatewayList.visible
    text: "Gateways"
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  Column {
    id: gatewayList
    width: root.width
    spacing: Style.spacing.xs
    visible: root.vm ? root.vm.gatewayRows.length > 0 : false

    Repeater {
      model: root.vm ? root.vm.gatewayRows : []
      delegate: Column {
        required property var modelData
        width: gatewayList.width
        spacing: 0

        Text {
          text: modelData.nameText + "  ·  " + modelData.classText
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
  PanelSeparator { foreground: root.foreground }

  PanelSectionHeader {
    text: "Devices"
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  Item {
    width: root.width
    implicitHeight: Math.max(clientsLabel.implicitHeight, clientsValue.implicitHeight)
    Text {
      id: clientsLabel
      anchors.left: parent.left
      text: "Connected clients"
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }
    Text {
      id: clientsValue
      anchors.right: parent.right
      text: root.vm ? root.vm.clientsText : "unknown"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }
  }

  Item {
    width: root.width
    implicitHeight: Math.max(totalLabel.implicitHeight, totalValue.implicitHeight)
    Text {
      id: totalLabel
      anchors.left: parent.left
      text: "Adopted devices"
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }
    Text {
      id: totalValue
      anchors.right: parent.right
      text: root.vm ? root.vm.devicesTotalText : "unknown"
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      textFormat: Text.PlainText
    }
  }

  Repeater {
    model: root.vm ? root.vm.countRows : []
    delegate: Item {
      id: roleRow
      required property var modelData
      width: root.width
      implicitHeight: roleColumn.implicitHeight
      height: implicitHeight

      Column {
      id: roleColumn
      width: parent.width
      spacing: 0

      Text {
        text: roleRow.modelData.label
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        textFormat: Text.PlainText
      }
      Row {
        spacing: Style.spacing.lg
        Repeater {
          model: roleRow.modelData.cells
          delegate: Text {
            required property var modelData
            text: modelData.value + " " + modelData.label
            // SPEC-AMD-3: every cell that reaches here is non-zero, because
            // `countRows` drops the empty classes. The colour is unconditional
            // for that reason and not because the distinction stopped
            // mattering — a zero cell arriving here would be a defect in
            // ViewModel.js, and dimming it would hide that.
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
          }
        }
      }
      }

      // REQ-B10a. The row is an entry point: activating it opens Devices
      // filtered to this role. `modelData.role` carries the value
      // `devices[].roles` uses — decided in ViewModel.js, because the count
      // buckets are plural nouns and the roles are the API's feature names, and
      // a view translating between them is the one place a typo produces an
      // always-empty list rather than an error.
      //
      // A `MouseArea`, as the host's own panel rows use
      // (bluetooth/Panel.qml:933-957). §7's "never write a MouseArea" is about
      // BAR BUTTONS; `Ui/WidgetButton` is additionally unusable for an
      // invisible target, being `visible: hasVisualContent || keepSpace` with
      // `hasVisualContent: text !== ""`.
      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: Qt.PointingHandCursor
        onClicked: root.roleActivated(roleRow.modelData.role)
      }
    }
  }
}
