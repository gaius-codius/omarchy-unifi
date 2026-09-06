// The bar item: the health-coloured glyph and REQ-005's optional figure.
//
// It decides nothing. The colour comes from HealthColor.qml, which owns
// REQ-001a's table; the figure comes from `vm.compactText`, which ViewModel.js
// has already resolved against `compactMetric` and BIZ-003's "unknown is not
// zero". This file places them.
import QtQuick
import qs.Commons

Item {
  id: root

  property var vm: null
  property QtObject bar: null

  readonly property var rendering: vm && vm.rendering ? vm.rendering : null
  readonly property color glyphColor: palette.colorFor(rendering)

  readonly property HealthColor palette: HealthColor { bar: root.bar }

  implicitWidth: content.implicitWidth
  implicitHeight: Math.max(glyph.implicitHeight, compact.implicitHeight)

  Row {
    id: content
    anchors.centerIn: parent
    spacing: compact.visible ? Style.spacing.sm : 0

    UnifiGlyph {
      id: glyph
      // Named for the same reason the compact text is: the panel hero draws the
      // same glyph with the same badge, so an assertion that "a badge is lit
      // somewhere" is satisfied by a bar item with no badge at all.
      objectName: "unifi-bar-glyph"
      anchors.verticalCenter: parent.verticalCenter
      glyphColor: root.glyphColor
      badgeColor: root.palette.urgent
      fontFamily: root.palette.fontFamily
      iconSize: Style.bar.iconFont
      showBadge: root.rendering ? root.rendering.badge === true : false
      // UX-003: a failed refresh over a snapshot that is still fresh. Not shown
      // once the snapshot has gone stale, because `stale` is already the greyed
      // level and the panel says so in words — two affordances for one fact
      // would suggest two problems.
      showRefreshFailure: root.vm ? (root.vm.errorKind !== null
        && root.vm.hasSnapshot === true && root.vm.isStale !== true) : false
    }

    // REQ-005. The metric is chosen by `compactMetric`, whose only values are
    // `none` and `clients`; ViewModel.compactText() has already resolved that,
    // so this is a plain binding.
    Text {
      id: compact
      // Named so the harness can assert that REQ-005's figure is on the BAR
      // rather than merely somewhere in the widget. The client count also
      // appears in the panel's Devices section, so "the text 42 is on screen"
      // is satisfied by a bar item that renders nothing at all — which is how
      // hiding this element survived a mutation pass.
      objectName: "unifi-bar-compact"
      anchors.verticalCenter: parent.verticalCenter
      visible: text !== ""
      text: root.vm ? root.vm.compactText : ""
      color: root.glyphColor
      font.family: root.palette.fontFamily
      font.pixelSize: Style.bar.iconFont
      textFormat: Text.PlainText
      renderType: Text.NativeRendering
    }
  }
}
