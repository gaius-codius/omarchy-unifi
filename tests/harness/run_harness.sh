#!/usr/bin/env bash
# Drive tests/harness/runner.qml under a real quickshell (HC-11, live-harness).
#
# Stages NOTHING. The stub plugin root lives under /tmp and the runner loads
# Service.qml from the repository by absolute path, so ~/.config/omarchy is
# never touched — that is G-STAGING's boundary and this side of it needs no
# approval.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STUB_ROOT="/tmp/unifi-harness/plugin"
QML_ROOT="/tmp/unifi-harness/qml"

# The dual-use modules Service.qml imports. Symlinked rather than copied so the
# harness cannot silently test a stale snapshot of the code.
MODULES=(Service.qml Health.js Protocol.js Schedule.js Settings.js ViewModel.js)

command -v quickshell >/dev/null 2>&1 || { echo "run_harness.sh: quickshell not on PATH" >&2; exit 2; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "run_harness.sh: needs a live Wayland session (HC-11)" >&2; exit 2; }

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
cp "$REPO/tests/harness/runner.qml" "$QML_ROOT/runner.qml"
printf '{"repoRoot":"%s","stubRoot":"%s"}\n' "$REPO" "$STUB_ROOT" > "$QML_ROOT/harness.json"

# QML_XHR_ALLOW_FILE_READ: Qt refuses XMLHttpRequest against file:// URLs
# unless this is set, and the runner reads the fixture corpus that way — the
# same 77 envelopes `node --test` drives, so that V4's verdicts can be compared
# with V8's. It is scoped to this one process and affects nothing that ships.
out="$(QML_XHR_ALLOW_FILE_READ=1 timeout 240 quickshell -p "$QML_ROOT/runner.qml" 2>&1)"
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
if grep -q 'gaius-codius.unifi: released wakeTimer, watchdog, freshnessTimer, helper Process' <<< "$clean"; then
  echo "HARNESS: ok   teardown named every released resource"
else
  echo "HARNESS: FAIL teardown named every released resource" >&2
  exit 1
fi

if grep -q 'HARNESS RESULT: PASS' <<< "$clean"; then
  exit 0
fi
echo "run_harness.sh: the harness did not report PASS" >&2
printf '%s\n' "$clean" | sed 's/^/    /' >&2
exit 1
