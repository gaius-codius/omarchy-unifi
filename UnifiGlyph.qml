// The bar mark: one Nerd Font glyph plus the two affordances REQ-001a and
// UX-003 require.
//
// Both affordances are drawn as SHAPES rather than as further glyphs. A second
// codepoint would be a second bet on the user's font covering it, and the one
// that matters most — "your site is degraded" — would be the one that silently
// rendered as a tofu box. A Rectangle cannot be missing from a font.
//
// The two are deliberately different shapes in different corners, because
// UX-003's entire requirement is that "the last refresh failed" be
// distinguishable from "the site is confirmed bad":
//
//   round dot,     bottom-right  -> REQ-001a's persistent degraded badge
//   straight bar,  top-right     -> UX-003's refresh-failure affordance
//
// Different in KIND, not merely in fill. They were a dot and a hollow ring of
// the same size, which at a 16 px bar icon differ by about three interior
// pixels — a distinction the documentation had to explain in words for anyone
// to see it.
//
// Colour is never the sole signal (UX-002): the tooltip and the panel state
// both conditions in words, and this file is only the glance-able half.
import QtQuick
import qs.Commons

Item {
  id: root

  // nf-md-lan, U+F0317. Written as a surrogate pair rather than as a literal
  // so this file stays ASCII — a literal PUA codepoint is invisible in a
  // diff and indistinguishable from a neighbouring one.
  // Verified present in JetBrainsMonoNerdFont-Regular.ttf, the family
  // Style.fontFamily resolves to on this system (Style.qml:269-272).
  readonly property string glyph: "\udb80\udf17"

  property color glyphColor: Color.foreground
  property color badgeColor: Color.urgent
  property string fontFamily: Style.font.family
  property real iconSize: Style.bar.iconFont

  property bool showBadge: false
  property bool showRefreshFailure: false

  implicitWidth: mark.implicitWidth
  implicitHeight: mark.implicitHeight

  Text {
    id: mark
    anchors.centerIn: parent
    text: root.glyph
    color: root.glyphColor
    font.family: root.fontFamily
    font.pixelSize: root.iconSize
    textFormat: Text.PlainText
    renderType: Text.NativeRendering
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
  }

  // REQ-001a. `degraded` is the one level with no colour of its own — it paints
  // the same foreground token as `healthy` — so this dot is the whole visual
  // difference between "fine" and "something is wrong". AC-069 checks it stays
  // visible against a light theme's bar.
  Rectangle {
    id: badge
    visible: root.showBadge
    width: Math.max(3, Math.round(root.iconSize / 3.5))
    height: width
    radius: width / 2
    color: root.badgeColor
    anchors.right: mark.right
    anchors.bottom: mark.bottom
    anchors.rightMargin: -Math.round(width / 3)
    anchors.bottomMargin: -Math.round(height / 5)
  }

  // UX-003. A BAR, not a ring.
  //
  // Drawing both affordances as shapes is right — a Rectangle cannot be missing
  // from a font — but the two shapes chosen were a 5 px filled dot and a 5 px
  // ring with a 1 px border, at a 16 px icon. Same size, same colour, same
  // family, differing by about three interior pixels and which corner they sat
  // in. That is not a distinction at bar scale, and the giveaway is that
  // docs/usage.md has to spell out "hollow ring in the opposite corner" for a
  // reader to have any chance of telling them apart.
  //
  // UX-003's entire requirement is that "the last refresh failed" be
  // distinguishable from "the site is confirmed bad", so the two marks now
  // differ in KIND: a round dot for the condition, a straight bar for the
  // staleness. Shape survives a small size and a bad display in a way that
  // fill does not, and the corners still differ as a second cue.
  //
  // Colour is still never the sole signal (UX-002): the tooltip and the panel
  // state both conditions in words.
  Rectangle {
    id: refreshMark
    visible: root.showRefreshFailure
    width: Math.max(5, Math.round(root.iconSize / 2.2))
    height: Math.max(2, Math.round(root.iconSize / 8))
    radius: 0
    color: root.badgeColor
    anchors.right: mark.right
    anchors.top: mark.top
    anchors.rightMargin: -Math.round(width / 4)
    anchors.topMargin: -Math.round(height / 2)
  }
}
