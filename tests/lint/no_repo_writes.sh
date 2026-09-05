#!/usr/bin/env bash
# HC-14. Nothing the test suite runs may write into this tree.
#
# PluginRegistry.localPluginIdForPath() (PluginRegistry.qml:701-713) excludes
# only top-level dotted entries and .git, so a __pycache__ directory appearing
# under a staged plugin is seen as a change and hot-reloads the plugin — in the
# middle of a poll, destroying the service and its in-flight batch. Every Python
# invocation, tests included, therefore runs with -B.
#
# Bytecode is the known case, not the only one. With a baseline the gate is
# exact: it compares the full file list before and after and reports anything
# that appeared, whatever it is.
#
#     tests/lint/no_repo_writes.sh <ROOT> --record <snapshot>   take a baseline
#     tests/lint/no_repo_writes.sh <ROOT> [snapshot]            check
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"
shift || true

snapshot_to() {
  ( cd "$ROOT" && find . -name .git -prune -o -print | LC_ALL=C sort ) > "$1"
}

if [[ ${1:-} == --record ]]; then
  snapshot_to "$2"
  exit 0
fi

# Always-on checks, baseline or not.
while IFS= read -r -d '' p; do
  gate_violation "${p#./} (bytecode in the plugin tree — launch Python with -B, HC-14)"
done < <( cd "$ROOT" && find . -name .git -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print0 )

BASELINE="${1:-}"
if [[ -n $BASELINE && -f $BASELINE ]]; then
  after="$(mktemp)"; trap 'rm -f "$after"' EXIT
  snapshot_to "$after"
  while IFS= read -r p; do
    gate_violation "${p#./} (appeared while the suite ran; the tree must be inert)"
  done < <(comm -13 "$BASELINE" "$after")
  while IFS= read -r p; do
    gate_violation "${p#./} (disappeared while the suite ran)"
  done < <(comm -23 "$BASELINE" "$after")
fi

gate_done
