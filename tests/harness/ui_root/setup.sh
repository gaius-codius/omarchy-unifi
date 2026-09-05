#!/usr/bin/env bash
# HC-17. Build the Quickshell config root that lets the UI layer be tested
# WITHOUT staging anything into ~/.config/omarchy/plugins/ (which needs
# G-STAGING approval, and which two reviewers wrongly concluded was required).
#
# The mapping is the load-bearing detail: Quickshell resolves `qs.X` against
# <config-root>/X/qmldir. NOT <config-root>/qs/X. That is the opposite of the
# qmllint import root in HC-12, which does nest under qs/ because qmllint
# resolves a module URI against -I in the conventional way. Getting it backwards
# fails with `module "qs.Ui" is not installed`, which is how the first attempt
# at this went.
#
# The root lives outside the repository because AC-027 and
# omarchy-plugin-validate:115 forbid a symlink anywhere inside the plugin folder.
#
#     tests/harness/ui_root/setup.sh [--probe]
#
# Prints the root path on stdout. With --probe it additionally runs the HC-17
# verification under quickshell, which needs a live Wayland session.
set -euo pipefail

SHELL_TREE=/usr/share/omarchy/shell
ROOT="${UNIFI_HARNESS_ROOT:-/tmp/unifi-harness/root}"
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"

[[ -d $SHELL_TREE ]] || { echo "setup.sh: $SHELL_TREE not found" >&2; exit 2; }

case "$ROOT" in
  "$REPO"|"$REPO"/*)
    echo "setup.sh: refusing to build the harness root inside the repository ($ROOT):" >&2
    echo "  omarchy-plugin-validate:115 rejects any symlink inside a plugin folder (AC-027)." >&2
    exit 2 ;;
esac

rm -rf "$ROOT"
mkdir -p "$ROOT"

# Every module directory the shell exposes — those carrying a qmldir. Listing
# them rather than hard-coding Ui and Commons means a future Omarchy release
# that adds a module does not silently produce an "is not installed" failure
# that looks like a bug in the plugin.
found=0
for d in "$SHELL_TREE"/*/; do
  [[ -f "$d/qmldir" ]] || continue
  ln -s "${d%/}" "$ROOT/$(basename "$d")"
  found=$((found + 1))
done
(( found > 0 )) || { echo "setup.sh: no qmldir modules under $SHELL_TREE" >&2; exit 2; }

# The probe is the HC-17 verification, kept as runnable code rather than a
# transcript in a document: it instantiates the REAL qs.Ui.Panel and reads live
# theme values, so a passing run proves the UI layer is genuinely loaded and not
# merely that the file parsed.
cat > "$ROOT/probe.qml" <<'QML'
import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

ShellRoot {
  Panel {
    id: probe
    Component.onCompleted: {
      console.log("UIHARNESS RESULT: PASS -> UI_OK"
                  + " Style.space=" + Style.space(12)
                  + " Color.fg=" + Color.foreground
                  + " opened=" + probe.opened)
      probeExit.running = true
    }
  }
  // Qt.exit() warns under a bare ShellRoot (SPEC §13), so the runner arranges
  // its own exit path instead.
  Timer { id: probeExit; interval: 50; onTriggered: Qt.callLater(Qt.quit) }
}
QML

if [[ ${1:-} == --probe ]]; then
  command -v quickshell >/dev/null 2>&1 || { echo "setup.sh: quickshell not on PATH" >&2; exit 2; }
  [[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "setup.sh: --probe needs a live Wayland session (HC-11)" >&2; exit 2; }
  out="$(quickshell -p "$ROOT/probe.qml" 2>&1 || true)"
  if grep -q 'UIHARNESS RESULT: PASS' <<< "$out"; then
    grep 'UIHARNESS RESULT' <<< "$out"
  else
    echo "setup.sh: probe did not report PASS:" >&2
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    exit 1
  fi
fi

printf '%s\n' "$ROOT"
