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
//   filled dot,  bottom-right  -> REQ-001a's persistent degraded badge
//   hollow ring, top-right     -> UX-003's refresh-failure affordance
//
// Colour is never the sole signal (UX-002): the tooltip and the panel state
// both conditions in words, and this file is only the glance-able half.
import QtQuick
import qs.Commons

Item {
  id: root

  // nf-md-access_point_network, U+F0003. Written as a surrogate pair rather
  // than as a literal so this file stays ASCII — a literal PUA codepoint is
  // invisible in a diff and indistinguishable from a neighbouring one.
  // Verified present in JetBrainsMonoNerdFont-Regular.ttf, the family
  // Style.fontFamily resolves to on this system (Style.qml:269-272).
  readonly property string glyph: "\udb80\udc03"

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

  // UX-003. Hollow, and in the opposite corner, so it reads as "the last
  // attempt failed" over whatever colour the glyph already carries — including
  // over a green one, which is exactly the case REQ-004 exists for.
  Rectangle {
    id: refreshMark
    visible: root.showRefreshFailure
    width: Math.max(4, Math.round(root.iconSize / 3))
    height: width
    radius: width / 2
    color: "transparent"
    border.width: Math.max(1, Math.round(width / 4))
    border.color: root.badgeColor
    anchors.right: mark.right
    anchors.top: mark.top
    anchors.rightMargin: -Math.round(width / 3)
    anchors.topMargin: -Math.round(height / 5)
  }
}
