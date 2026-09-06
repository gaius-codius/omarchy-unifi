// The bar button and its popup, from one file.
//
// The entry point is named Panel.qml, not BarWidget.qml: this plugin ships a
// bar item *and* a popup, which is what qs.Ui.Panel is the base class for
// (Ui/Panel.qml:9-59). The manifest's entryPoints.barWidget names this file.
//
// Two rules govern everything below.
//
// DATA-003a: a bar widget receives ONLY `bar`, `moduleName` and `settings`, and
// `bar` is null on the first frame. The service is therefore always reached
// through optional chaining, and a null result renders REQ-013b's
// `service_unavailable` rather than throwing or blanking (AC-067).
//
// REQ-014 / UX-011: this file holds no state that could differ between
// monitors. It renders `service.viewModel` and nothing else. "Every monitor
// shows identical state" is then a consequence of there being one service,
// rather than of every widget happening to agree.
//
// REQ-007a is deliberately NOT implemented here. KeyboardPanel already calls
// bar.requestPopout / releasePopout (Ui/KeyboardPanel.qml:240, :246) against
// the single global Bar (Bar.qml:83, :316-327), so at most one panel is open
// across all monitors for free. Do not add arbitration; a second coordinator is
// the bug AC-068 would find.
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "ViewModel.js" as ViewModel

Panel {
  id: root

  moduleName: "gaius-codius.unifi"
  ipcTarget: ""          // DATA-010: the *service* owns the IPC target.
  manageIpc: false

  // Ui/Panel declares `bar` as QtObject, so qmllint cannot see that the real
  // Bar item carries `shell`. The access is correct at runtime and the optional
  // chaining is mandatory (DATA-003a). The suppression is scoped to this one
  // line rather than lowering the category, which would stop the gate catching
  // genuine typos everywhere else.
  // qmllint disable missing-property
  readonly property var unifiService: bar?.shell?.serviceFor("gaius-codius.unifi") ?? null
  // qmllint enable missing-property

  // REQ-013b / AC-067. `forNullService()` returns the SAME SHAPE `build()`
  // does, so no binding below ever sees `undefined` — not on the first frame,
  // and not if the service genuinely failed to construct, which the host
  // surfaces only as a console.warn nobody reads.
  readonly property var vm: (unifiService && unifiService.viewModel)
    ? unifiService.viewModel : ViewModel.forNullService()

  // qmllint disable missing-property
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // qmllint enable missing-property
  readonly property color dim: Qt.darker(foreground, 1.4)

  // UX-008: Tab cycles Refresh and Open UniFi, Enter activates.
  property int focusIndex: 0
  readonly property int actionCount: 2

  // The bar sizes its slot from the button, and the button sizes itself from
  // `slotSize` before the icon component has been loaded — so REQ-005's compact
  // text has to be measured outside the component that draws it.
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // AC-011: the launcher is a property so a harness can substitute one and
  // observe the call. The production value is the ONLY Qt.openUrlExternally in
  // the repository (REQ-012), and tests/lint/no_exec_for_dashboard.sh proves
  // there is no Process, execDetached, execArgv or omarchy-launch-browser path
  // anywhere in the QML layer.
  property var urlOpener: function (url) { Qt.openUrlExternally(url) }

  // REQ-011 / REQ-018. Refusing here rather than in the service would be a
  // second copy of REQ-018a's rule; `vm.refreshEnabled` is the service's own
  // answer, and `requestRefresh()` refuses again on its own account (AC-038).
  function doRefresh() {
    if (!vm.refreshEnabled) return "disabled"
    if (!unifiService) return "unavailable"
    return unifiService.requestRefresh()
  }

  // REQ-012 / SEC-009 / UX-010. Every acceptance decision — scheme, userinfo,
  // character set, the https://<apiRootHost> fallback — was made in
  // ViewModel.acceptDashboardUrl before this ran. This function opens what it
  // is handed or refuses; it does not parse.
  function openDashboard() {
    var dashboard = vm.dashboard
    if (!dashboard || !dashboard.accepted) return "rejected"
    urlOpener(dashboard.url)
    return "opened"
  }

  function moveFocus(direction) {
    if (direction === 0) return
    focusIndex = (focusIndex + direction + actionCount) % actionCount
  }

  function activateFocused() {
    return focusIndex === 0 ? doRefresh() : openDashboard()
  }

  // REQ-001a lives in exactly one file. The panel hero needs the same
  // descriptor-to-token mapping the bar item uses, so it shares the evaluator
  // rather than restating it. A second copy of the table is how a theme change
  // ends up half applied — the bar item re-themed and the hero still on the old
  // palette.
  readonly property HealthColor health: HealthColor { bar: root.bar }

  TextMetrics {
    id: compactMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.bar.iconFont
    text: root.vm.compactText
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // REQ-006. The tooltip states the condition in words, which is what makes
    // UX-002's "colour is never the sole signal" true for the bar item.
    tooltipText: root.vm.tooltip
    slotSize: Style.bar.iconSlot
      + (root.vm.compactText === "" ? 0 : compactMetrics.width + Style.space(4))

    iconComponent: Component {
      BarItem {
        anchors.centerIn: parent
        vm: root.vm
        bar: root.bar
      }
    }

    // REQ-007. Middle-click refreshes, matching the house idiom
    // (tailscale/Panel.qml:397). There is no right-click action: every write is
    // forbidden by BIZ-001, so there is nothing to put there that would not be
    // a lie about what this plugin can do.
    onPressed: function (buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.doRefresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.moveFocus(direction) }
      onMoveRequested: function (dx, dy) { root.moveFocus(dy !== 0 ? dy : dx) }
      onActivateRequested: root.activateFocused()
      onTextKey: function (t) { if (t === "r" || t === "R") root.doRefresh() }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.spacing.md

          PanelHero {
            width: parent.width
            title: root.vm.siteName === "" ? "UniFi" : root.vm.siteName
            meta: root.vm.headline
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              UnifiGlyph {
                objectName: "unifi-hero-glyph"
                iconSize: Style.font.display
                glyphColor: health.colorFor(root.vm.rendering)
                badgeColor: root.urgent
                fontFamily: root.fontFamily
                showBadge: root.vm.rendering ? root.vm.rendering.badge === true : false
              }
            }
          }

          // REQ-013 / UX-007. One sentence naming what failed and the single
          // action that would fix it. Empty, and therefore invisible, only in
          // the `ok` state — which is the twenty-fifth state and the one that
          // needs no explaining.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.sentence
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // UX-007's relative countdown. Recomputed by the service's freshness
          // tick, not by a timer in this file — see the comment on AC-071 in
          // Service.qml's freshnessTimer.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.nextAttemptText === ""
              ? "" : "Next attempt " + root.vm.nextAttemptText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
          }

          // UX-006a. DATA-012 carries the discovered {id, name} pairs in a
          // warning, because the batch that discovers them has no data at all.
          Column {
            width: parent.width
            spacing: 0
            visible: root.vm.sites.length > 0

            PanelSectionHeader {
              text: "Sites on this controller"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Repeater {
              model: root.vm.sites
              delegate: Text {
                required property var modelData
                width: column.width
                text: modelData.label
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
            }
          }

          // UX-009 / AC-070. Persistent for the whole session and with NO
          // dismiss handler, deliberately: `allowInsecureTls` is an opt-in that
          // turns off certificate verification, and a warning the user can make
          // go away is one they will make go away.
          Text {
            visible: root.vm.insecureTls
            width: parent.width
            text: "TLS verification is disabled for this controller. "
              + "Anything on the network path can read and alter what is shown here."
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          Text {
            visible: root.vm.customCaInUse
            width: parent.width
            text: "A custom certificate authority is in use for this controller."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          StatusPanel {
            width: parent.width
            visible: root.vm.hasSnapshot
            vm: root.vm
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
          }

          DeviceList {
            width: parent.width
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          WarningList {
            width: parent.width
            vm: root.vm
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          // AC-052. Rendered as "unknown" rather than omitted, so an
          // `unconfigured` failure — the case with the least information and
          // the most need for it — still shows the same rows in the same
          // places.
          PanelSeparator { foreground: root.foreground }

          PanelSectionHeader {
            text: "Details"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Repeater {
            model: root.vm.metaRows
            delegate: Item {
              required property var modelData
              width: column.width
              implicitHeight: Math.max(metaLabel.implicitHeight, metaValue.implicitHeight)
              Text {
                id: metaLabel
                anchors.left: parent.left
                text: modelData.label
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                textFormat: Text.PlainText
              }
              Text {
                id: metaValue
                anchors.right: parent.right
                text: modelData.value
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                textFormat: Text.PlainText
                elide: Text.ElideRight
              }
            }
          }

          // DATA-007's diagnostic line, shown only when there is one. The
          // helper sanitizes every message before it reaches an envelope
          // (sanitize.py), which is what makes it safe to print (SEC-001).
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.errorMessage
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          PanelSeparator { foreground: root.foreground }

          // --- the two actions (REQ-011, REQ-012, UX-008) -------------------
          Row {
            spacing: Style.spacing.controlGap

            Button {
              text: "Refresh"
              enabled: root.vm.refreshEnabled
              opacity: enabled ? 1.0 : 0.45
              hasCursor: root.focusIndex === 0
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.doRefresh()
            }

            Button {
              text: "Open UniFi"
              enabled: root.vm.dashboard ? root.vm.dashboard.accepted : false
              opacity: enabled ? 1.0 : 0.45
              hasCursor: root.focusIndex === 1
              foreground: root.foreground
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.openDashboard()
            }
          }

          // REQ-011: disabled WITH an explanatory label. A greyed button that
          // says nothing sends the user to look for a fault in the button.
          Text {
            visible: text !== ""
            width: parent.width
            text: root.vm.refreshDisabledReason === ""
              ? "" : "Refresh is unavailable: " + root.vm.refreshDisabledReason
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }

          // REQ-012's disabled label, and UX-010's warning. The warning is a
          // BINDING, not something raised on click, which is precisely what
          // "rendered before the launch occurs" requires: it is on screen for
          // as long as the URL is plain HTTP, whether or not anyone presses the
          // button.
          Text {
            visible: text !== ""
            width: parent.width
            text: !root.vm.dashboard ? ""
              : !root.vm.dashboard.accepted
                ? "Open UniFi is unavailable: " + root.vm.dashboard.reason + "."
                : root.vm.dashboard.warnPlainHttp
                  ? "This dashboard URL is plain HTTP. Your session, including "
                    + "anything you type into it, will not be encrypted."
                  : ""
            color: root.vm.dashboard && root.vm.dashboard.warnPlainHttp
              ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }
}
