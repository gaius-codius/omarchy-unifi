// A section label, and the panel's only heading level.
//
// `Ui/PanelSectionHeader` renders at caption size in a muted colour, which puts
// every heading in this panel BELOW the rows it heads on both axes at once:
//
//   heading   Style.font.caption    muted
//   row       Style.font.bodySmall  foreground
//
// That is an inverted hierarchy, and no amount of space around a heading
// recovers a level it never had. It is why "Uplink" and "Inventory" read as
// stray labels rather than as titles.
//
// The panel has ONE font family — `bar.fontFamily`, a monospace Nerd Font — so
// there is no second typeface to make the distinction with. The levers are
// size, case, tracking, colour and space, and they do not cost the same:
//
//   BIGGER      unambiguous, and spends ~3 px per heading. Five sections in a
//               560 px popup is 15 px of chrome, in the one panel where
//               vertical space is the binding constraint. Wrong trade here.
//   TRACKED     distinct by KIND rather than by degree — nothing else in this
//               panel is letter-spaced, so the eye sorts it without measuring
//               it. Monospace takes tracking well and every heading here is one
//               or two short words. Costs nothing; at caption size it is
//               slightly SHORTER than the body it heads.
//
// So: caption size, uppercase, tracked, at FULL foreground, regular weight.
// The size stays small and the colour stops apologising.
//
// Regular, not bold. The base type sets `font.bold`, and bold on top of
// tracked caps at caption size is two emphases where one was asked for: it
// turned every heading into the heaviest text in the panel, and "DETAILS" out-
// shouted the rows it hid.
//
// `color` is set rather than `foreground` because the base type derives a muted
// colour from `foreground`, and muted is the half of the problem that space
// cannot fix. A binding in a derived type wins over the base's.
//
// NOT used inside an expanded row. `PortTable` used to head its table with this
// type, which put a label two levels down in the top level's clothes; there,
// "Ports" is a detail label like Firmware or CPU and is drawn as one. One
// heading level in the panel, and this is it.
//
// The tracked-uppercase treatment is also the one `PanelHero` spends on its
// meta line. That is deliberately given up (see Panel.qml's hero) so this file
// holds it alone: one treatment, one meaning.
import QtQuick
import qs.Commons
import qs.Ui

PanelSectionHeader {
  id: root

  // The scale, for the colour. Null before the panel injects it, which is the
  // first frame and not worth a branch at every call site.
  property var emphasis: null

  fontSize: Style.font.caption
  color: emphasis ? emphasis.primary : foreground

  font.bold: false
  font.capitalization: Font.AllUppercase
  // Derived from the size rather than set in pixels, so it tracks the user's
  // font scaling the way `Style.space` does.
  //
  // 0.26em, and the first attempt at 0.16 was wrong for a reason worth keeping.
  //
  // Tracking is a WEAKER signal in a monospace face than in a proportional one.
  // Mono glyphs already carry generous side bearings — that is what makes a
  // fixed advance width work for both an `i` and an `m` — so the untracked
  // baseline already looks airy, and a value that reads as emphatic small caps
  // in a proportional font is nearly invisible here. 0.16 was chosen by
  // reasoning about type in general rather than about the face this panel
  // actually uses, and it did not show.
  //
  // Above roughly 0.35 short words start to come apart into separate letters,
  // so the usable band is narrow. 0.26 sits in it.
  font.letterSpacing: fontSize * 0.26

  // A heading owns the gap BEFORE it, not after. The parent's own spacing
  // supplies the gap above; this closes up the one below so the label sits
  // with the rows it belongs to instead of floating between two sections.
  //
  // `topPadding` is left at the host's `ceil(fontSize * 0.15)`. It is not
  // spacing: it reserves the part of a capital that paints above the box Text
  // reports, and a heading scrolled against the panel's clipping edge — or
  // drawn in a font that overshoots further — loses that sliver without it.
  bottomPadding: 0
}
