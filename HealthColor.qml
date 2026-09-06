// REQ-001a's only implementation.
//
// `ViewModel.renderingFor(level)` returns a DESCRIPTOR — {token, badge,
// darkenFactor} — and this file is the single place in the repository that
// turns one into a colour. The level-to-token mapping stays in ViewModel.js,
// where `node --test` can read it; what cannot live there is `Qt.darker`, which
// is why the descriptor carries a factor rather than a colour
// (tests/lint/no_qt_in_js.sh enforces the split).
//
// So the table below has exactly two tokens and one derivation, and between
// them they produce the four theme-native visual levels REQ-001a specifies:
//
//   healthy   bar.foreground
//   degraded  bar.foreground  + badge          (see UnifiGlyph.qml)
//   critical  bar.urgent
//   unknown   Qt.darker(bar.foreground, 1.55)  (tailscale/Panel.qml:40-44)
//
// It is a QtObject rather than an Item because both the bar glyph and the panel
// hero need the mapping and only one of them is a bar item. An earlier version
// made the hero borrow an invisible BarItem for its `colorFor`, which worked and
// was wrong: that BarItem carried a whole second bar glyph and compact-text
// element, and a harness looking for the bar's figure by name found the hidden
// one first.
import QtQuick
import qs.Commons

QtObject {
  id: root

  property QtObject bar: null

  // The widget convention from host-contract.md §8: prefer the bar-injected
  // colour with a Color.* fallback, because `bar` is null before injection.
  // `barForeground` rather than `foreground` is the transparency-aware variant
  // and is the right choice for bar chrome (Bar.qml:69).
  // qmllint disable missing-property
  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // qmllint enable missing-property

  // Total over every descriptor, including a missing one. An absent descriptor
  // is the never-injected first frame, which is a grey state, so it takes the
  // dim expression rather than the healthy one — defaulting to `foreground`
  // would paint an unknown site as if it had been confirmed fine.
  function colorFor(descriptor) {
    if (!descriptor) return Qt.darker(foreground, 1.55)
    var base = descriptor.token === "bar.urgent" ? urgent : foreground
    if (descriptor.darkenFactor === null || descriptor.darkenFactor === undefined) return base
    return Qt.darker(base, descriptor.darkenFactor)
  }
}
