// The panel's three emphasis levels, in one place.
//
// OPACITY, not `Qt.darker`. The host's own panels dim with
// `Qt.darker(foreground, 1.4)` (bluetooth/Panel.qml:754, :873) and this panel
// copied that, which was right until it was measured against a light theme:
//
//   dark theme   fg #e0e0e0 -> #a0a0a0 on a near-black ground.  Muted.
//   light theme  fg #1a1a1a -> #131313 on a near-white ground.  Not muted at
//                all — contrast against the background is essentially
//                unchanged, so "secondary" text reads exactly as loud as the
//                name above it.
//
// `Qt.darker` moves a colour toward black, which only reads as *quieter* when
// black is also the direction of the background. Opacity blends toward whatever
// is actually behind, so one number mutes correctly in both directions. That is
// what AC-B21 asks for — legible across three themes including a light one —
// and it is not reachable by tuning the darkening factor, because no single
// factor is dimmer in both.
//
// The host convention is departed from deliberately and only here; everything
// else in this plugin still takes its colours from theme tokens (UX-001).
//
// Three levels and no more. The panel previously had one, spent on six
// different jobs — row meta, the secondary line, detail labels, port rows, the
// truncation notice and the empty-state sentence — which is the same as having
// none.
// Colours rather than `opacity`, so a muted string is a colour like any other
// and nothing has to worry about opacity inheriting onto a child item.
import QtQuick
import qs.Commons

QtObject {
  property color foreground: Color.foreground
  property color background: Color.background

  // Foreground blended toward the background by `amount`. This is the whole
  // mechanism: 1.0 is the foreground itself, 0 is invisible, and every value
  // between is quieter in EITHER direction because the thing being blended
  // toward is the actual ground the text sits on.
  function level(amount) {
    return Qt.tint(background,
      Qt.rgba(foreground.r, foreground.g, foreground.b, amount))
  }

  // The name of the thing. What the eye lands on first.
  readonly property color primary: foreground


  // The amounts, and why they are not the ones this file shipped with.
  //
  // `level` was right and the numbers were not. 0.74 / 0.52 were chosen by eye on
  // a dark ground, where they read correctly; the same pair on a near-white
  // ground is where the trouble is, because the blend runs toward the OTHER end:
  //
  //   level(a) = a*fg + (1-a)*bg, so contrast falls as the ground gets closer to
  //   the text it is being blended with.
  //
  // Measured against WCAG 2.1 (fg #1a1a1a on bg #fafafa for the light theme,
  // #e0e0e0 on #0d0f12 for the dark one):
  //
  //              light    dark
  //   0.52       3.5:1    4.6:1     <- tertiary, and 3.5 is a FAIL
  //   0.62       4.8:1    5.9:1
  //   0.74       7.3:1    7.9:1
  //   0.82       9.6:1    9.8:1
  //
  // Tertiary carries the detail labels, the port rows, uptime, "Copied", the
  // truncation notice and the keyboard hint — every one of them at
  // `Style.font.caption`, the smallest type in the panel, which is exactly the
  // case AA's 4.5:1 floor is written for. 0.62 clears it on both grounds.
  //
  // Secondary moves with it. It did not fail anywhere, but three levels only
  // work as three if the gaps stay visible, and 0.74 over a 0.62 tertiary is not
  // a step the eye can find. 0.82 restores it.
  //
  // AC-B21 asks for legibility across three themes including a light one. It was
  // checked by looking, which is how a 3.5:1 passes: the text IS readable to
  // someone with full vision on a good display, and that is not what the
  // threshold is about.

  // Identity that is not the name: a device's model, a client's IP address.
  // Present at a glance, but never competing with the name.
  readonly property color secondary: level(0.82)

  // Everything that is context or chrome: labels, counts, uptime, the
  // truncation notice, port rows. Readable when looked at, quiet when not.
  readonly property color tertiary: level(0.62)
}