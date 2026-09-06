#!/usr/bin/env bash
# HC-12. Static analysis of the QML layer.
#
# Bare `qmllint file.qml`, and `-I /usr/share/omarchy/shell`, both fail with
# "Failed to import qs.Commons" — which reads deceptively like a clean result if
# you only check the exit code. What works is an import root *containing* a `qs`
# symlink, because qmllint resolves a module URI against -I in the conventional
# nested way.
#
# Note this is the opposite convention to the runtime harness root (HC-17),
# where `qs.X` maps to `<root>/X` with no `qs/` level. Both are correct for
# their own tool; conflating them costs an afternoon.
#
# The root lives in /tmp, outside the repository, because AC-027 forbids a
# symlink anywhere inside the plugin folder.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

QMLLINT=/usr/lib/qt6/bin/qmllint
SHELL_TREE=/usr/share/omarchy/shell

# By default qmllint sets a non-zero exit ONLY on a syntax error. An unknown
# type, a misspelled property, and an unresolvable import are all merely
# "warnings" and exit 0 — verified against Qt 6 here, and caught by
# selftest.sh, which found this gate passing a file containing all three.
#
# That is the whole class of bug this gate exists to catch, so each category is
# promoted to `error`. --unqualified is deliberately NOT promoted: unqualified
# access to the Omarchy singletons (Style, Color) is the house idiom and
# flagging it would produce noise rather than findings.
LEVELS=(
  --import error
  --unresolved-type error
  --missing-type error
  --missing-property error
  --unresolved-alias error
  --missing-enum-entry error
)

if [[ ! -x $QMLLINT ]]; then
  gate_violation "$QMLLINT not found; qmllint is required by SPEC §13"
  gate_done
fi
if [[ ! -d $SHELL_TREE ]]; then
  gate_violation "$SHELL_TREE not found; the import root cannot be built"
  gate_done
fi

IMPORT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/qslint-root.XXXXXX")"
trap 'rm -rf "$IMPORT_ROOT"' EXIT
ln -s "$SHELL_TREE" "$IMPORT_ROOT/qs"

# Guard against the failure mode this whole construction exists to avoid: if the
# import root is not working, every file "passes" for the wrong reason.
probe="$IMPORT_ROOT/_probe.qml"
printf 'import QtQuick\nimport qs.Commons\nItem { property int s: Style.space(1) }\n' > "$probe"
probe_out="$("$QMLLINT" -I "$IMPORT_ROOT" "${LEVELS[@]}" "$probe" 2>&1)" && probe_rc=0 || probe_rc=$?
# Check the TEXT as well as the status. HC-12's point is that a failure to
# import reads like a clean result, and before --import was promoted to error
# it also exited 0 — so a status-only probe would have certified a broken
# import root as working.
if (( probe_rc != 0 )) || grep -qiE 'failed to import|is not installed' <<< "$probe_out"; then
  gate_violation "import root probe failed; no result below would be trustworthy:"
  while IFS= read -r line; do printf '    %s\n' "$line" >&2; done <<< "$probe_out"
  gate_done
fi
rm -f "$probe"

shopt -s nullglob
qml=("$ROOT"/*.qml)
shopt -u nullglob
(( ${#qml[@]} > 0 )) || { printf 'ok   %s (no QML files yet)\n' "$GATE_NAME"; exit 0; }

# --- the QObject-member exemption, and why it is not a weakening ------------
#
# `Style.font.bodySmall`, `Style.spacing.md`, `Style.bar.iconSlot`,
# `bar.foreground`: every grouped theme token in Omarchy is an inline,
# unnamed `QtObject` sub-object (Commons/Style.qml:234, :322, :342), and every
# bar-injected colour arrives through a property declared `QtObject`
# (Ui/Panel.qml:12, Ui/KeyboardPanel.qml:40). qmllint cannot see inside an
# unnamed QtObject, so it reports every single one as
#
#     Error: file.qml:1:1: Member "md" not found on type "QObject" [missing-property]
#
# This is not a property of this repository. The SHIPPED tailscale plugin emits
# 35 of the identical error under this exact invocation (verified 2026-09-06,
# Qt 6), so the alternative to exempting the class is a gate that no Omarchy
# plugin can pass — which in practice is a gate somebody turns off.
#
# The exemption is by RESOLVED TYPE, not by file, line, or property name.
# `QObject` is qmllint's "I could not resolve this container" answer, so what is
# skipped is exactly the set of accesses about which it has no information. A
# typo on a type it CAN resolve still reads `not found on type "QQuickText"`,
# still fails, and selftest.sh seeds one to prove it.
UNRESOLVED_CONTAINER='not found on type "QObject"'

for f in "${qml[@]}"; do
  out="$("$QMLLINT" -I "$IMPORT_ROOT" "${LEVELS[@]}" "$f" 2>&1)" && continue
  # qmllint exits non-zero for ANY error, including one this gate exempts, so
  # the verdict is taken from the surviving error lines rather than the status.
  kept="$(grep '^Error:' <<< "$out" | grep -vF "$UNRESOLVED_CONTAINER" || true)"
  [[ -z $kept ]] && continue
  gate_violation "${f#"$ROOT"/}:"
  while IFS= read -r line; do printf '    %s\n' "$line" >&2; done <<< "$kept"
done

gate_done
