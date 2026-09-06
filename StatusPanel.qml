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
  readonly property color dim: Qt.darker(foreground, 1.4)

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
          // REQ-008a's four-gateway statistics bound is visible here rather
          // than hidden: a gateway past the bound reads "not fetched", not
          // "unknown", because those are different facts and only one of them
          // is a fault.
          text: modelData.hasMetrics
            ? modelData.modelText + "  ·  up " + modelData.uptimeText
              + "  ·  " + modelData.downloadText + " down  ·  "
              + modelData.uploadText + " up"
            : modelData.modelText + "  ·  statistics not fetched"
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
    delegate: Column {
      id: roleRow
      required property var modelData
      width: root.width
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
            // A zero class is dimmed rather than dropped. REQ-009 asks for
            // counts in each of the five classes, and a row whose columns
            // move as devices change state is unreadable at a glance.
            color: modelData.value > 0 ? root.foreground : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
          }
        }
      }
    }
  }

  // AC-025. The role rows deliberately sum to more than the unique total,
  // because a device is counted in every role it reports. Without this line the
  // panel reads as a bug in the panel.
  Text {
    visible: text !== ""
    width: root.width
    text: root.vm ? root.vm.roleCountsNote : ""
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
  }
}
