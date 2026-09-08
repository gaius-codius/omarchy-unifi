#!/usr/bin/env bash
# Drive tests/harness/runner.qml under a real quickshell (HC-11, live-harness).
#
# Stages NOTHING. The stub plugin root lives under /tmp and the runner loads
# Service.qml from the repository by absolute path, so ~/.config/omarchy is
# never touched — that is G-STAGING's boundary and this side of it needs no
# approval.
set -uo pipefail

# --- --only <regex> ----------------------------------------------------------
# Runs just the cases whose name matches <regex>. Purely a development
# aid: the full run takes five minutes, because several assertions are ABOUT
# durations of 17, 26 and 31 seconds and cannot be made shorter without
# testing something else. Filtering breaks the ordering some cases rely on, so
# a checkpoint always runs unfiltered — and the teardown assertion below is
# skipped when a filter is in force, because it belongs to a case that may not
# have run.
ONLY=""
if [[ ${1:-} == --only ]]; then
  ONLY="${2:-}"
  [[ -n $ONLY ]] || { echo "run_harness.sh: --only needs a regex" >&2; exit 2; }
fi

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STUB_ROOT="/tmp/unifi-harness/plugin"
QML_ROOT="/tmp/unifi-harness/qml"

# One run at a time. Both roots are fixed paths that this script deletes and
# rebuilds, and the stub's scenario file is shared mutable state, so two
# overlapping runs rewrite each other's fixtures mid-case and every verdict in
# both is noise. That has now happened twice in this project — once to the
# mutation probe loop (see the lock in its probe script) and once here — so it
# is made impossible rather than left as something to remember.
mkdir -p /tmp/unifi-harness
exec 9>/tmp/unifi-harness/.run.lock
if ! flock -w 600 9; then
  echo "run_harness.sh: another run holds the lock" >&2
  exit 2
fi

# The dual-use modules Service.qml imports, and the Phase 10 view layer.
# Symlinked rather than copied so the harness cannot silently test a stale
# snapshot of the code.
#
# Panel.qml and its five components go in beside them because a bar widget is
# loaded from ONE directory at runtime — `Panel.qml` reaches `BarItem.qml` and
# `ViewModel.js` through the implicit directory import and a relative `.js`
# import, and both of those resolve against the config root. Testing the view
# from anywhere else would test a layout that never ships.
# DERIVED, not listed. This was a hand-maintained array and it rotted the first
# time it was asked to: Phase B3 added BrowseList, BrowseRow, DetailRows and
# PortTable, the array did not, and the harness reported
# `BrowseList is not a type` — so every UI case ran against a `panelWidget` that
# had failed to load, and returned silently rather than failing.
#
# The repository root IS the plugin folder, so its `*.qml` and `*.js` are
# exactly what ships and exactly what the harness must stage. A glob cannot
# disagree with that; a list can, and did.
shopt -s nullglob
MODULES=()
for path in "$REPO"/*.qml "$REPO"/*.js; do
  MODULES+=("$(basename "$path")")
done
shopt -u nullglob
[[ ${#MODULES[@]} -gt 0 ]] || {
  echo "run_harness.sh: no QML/JS found in $REPO — nothing to stage" >&2; exit 2; }

SHELL_TREE=/usr/share/omarchy/shell

command -v quickshell >/dev/null 2>&1 || { echo "run_harness.sh: quickshell not on PATH" >&2; exit 2; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "run_harness.sh: needs a live Wayland session (HC-11)" >&2; exit 2; }
[[ -d $SHELL_TREE ]] || { echo "run_harness.sh: $SHELL_TREE not found" >&2; exit 2; }

for dir in "$STUB_ROOT" "$QML_ROOT"; do
  case "$dir" in
    "$REPO"|"$REPO"/*)
      echo "run_harness.sh: $dir must be outside the repository (AC-027)" >&2; exit 2 ;;
  esac
done

rm -rf "$STUB_ROOT"
mkdir -p "$STUB_ROOT/helper"
cp "$REPO/tests/harness/stub_helper.py" "$STUB_ROOT/helper/unifi_status.py"
printf '{"mode":"success"}' > "$STUB_ROOT/helper/scenario.json"

# Quickshell resolves a relative `.js` import against the config root — the
# directory of the file given to `-p` — and replaces anything outside it with
# `qrc:/qs-blackhole`, which then fails to load. So the runner cannot live in
# tests/harness and reach ../../Protocol.js; it runs from a root that has the
# modules beside it.
rm -rf "$QML_ROOT"
mkdir -p "$QML_ROOT"
for module in "${MODULES[@]}"; do
  ln -s "$REPO/$module" "$QML_ROOT/$module"
done

# HC-17: `qs.X` maps to <config-root>/X/qmldir — NOT <config-root>/qs/X, which
# is the qmllint convention and the opposite one. Every shell module directory
# carrying a qmldir is linked, rather than just Ui and Commons, so a future
# Omarchy release that adds one does not surface as "module is not installed"
# in a test that has nothing to do with it.
found=0
for d in "$SHELL_TREE"/*/; do
  [[ -f "$d/qmldir" ]] || continue
  ln -s "${d%/}" "$QML_ROOT/$(basename "$d")"
  found=$((found + 1))
done
(( found > 0 )) || { echo "run_harness.sh: no qmldir modules under $SHELL_TREE" >&2; exit 2; }

cp "$REPO/tests/harness/runner.qml" "$QML_ROOT/runner.qml"
printf '{"repoRoot":"%s","stubRoot":"%s","only":"%s"}\n' \
  "$REPO" "$STUB_ROOT" "$ONLY" > "$QML_ROOT/harness.json"

# QML_XHR_ALLOW_FILE_READ: Qt refuses XMLHttpRequest against file:// URLs
# unless this is set, and the runner reads the fixture corpus that way — the
# same 77 envelopes `node --test` drives, so that V4's verdicts can be compared
# with V8's. It is scoped to this one process and affects nothing that ships.
# 420 s, not 240: the service cases alone spend 17 s, 26 s and 31 s inside
# single assertions — REQ-023a's cadence, REQ-016's skipped ticks and REQ-017a's
# watchdog cannot be observed in less time than they take — and Phase 10 added
# another 12 s of view cases. The bound exists to stop a wedged harness hanging
# a checkpoint, so it is set well above the real runtime rather than near it.
# REQ-B14's absolute instant renders in LOCAL time, so the literal the runner
# asserts depends on the zone. Pinned to the same +05:30, DST-free zone
# `tests/model/viewmodel.test.js` pins, because the point of running this module
# under V4 at all is that the two engines agree — and they can only be compared
# on a local-time rendering if they are given the same locality. The runner
# asserts the pin took effect; a missing tzdata would make local time equal UTC
# in both engines, and they would agree on the wrong answer.
# 600, raised from 420 at Phase B3. Several assertions are ABOUT durations of
# 17, 26 and 31 seconds and cannot be shortened without testing something else,
# so the run only ever grows — and by B3 it was finishing at ~6:30 against a
# 7:00 cap, which is a suite that fails intermittently under load and tells you
# nothing about the code when it does. The cap exists to stop a HANG, and a hang
# is minutes past this, not seconds.
out="$(TZ=Asia/Kolkata QML_XHR_ALLOW_FILE_READ=1 timeout 600 quickshell -p "$QML_ROOT/runner.qml" 2>&1)"
status=$?

# Quickshell prefixes every console.log with a colourised level tag.
clean="$(printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g')"
printf '%s\n' "$clean" | grep -aoE '(HARNESS RESULT|HARNESS|gaius-codius\.unifi):?.*' || true

if (( status == 124 )); then
  echo "run_harness.sh: the harness timed out" >&2
  exit 1
fi

# AC-028: teardown emits one line naming each released resource. Asserted here
# rather than inside the runner, because the runner cannot observe its own
# console output.
if [[ -z $ONLY ]]; then
  # The line names the instance it belongs to (AC-003), so match on the shape
  # rather than on a literal prefix.
  if grep -qE 'gaius-codius\.unifi\[[0-9a-f]+\]: released wakeTimer, watchdog, freshnessTimer, helper Process' <<< "$clean"; then
    echo "HARNESS: ok   teardown named every released resource"
  else
    echo "HARNESS: FAIL teardown named every released resource" >&2
    exit 1
  fi
fi

if grep -q 'HARNESS RESULT: PASS' <<< "$clean"; then
  exit 0
fi
echo "run_harness.sh: the harness did not report PASS" >&2
printf '%s\n' "$clean" | sed 's/^/    /' >&2
exit 1
