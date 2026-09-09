#!/usr/bin/env bash
# REQ-012 / AC-011 (grep half). The dashboard opens through
# Qt.openUrlExternally and through nothing else.
#
# Two assertions:
#   1. No QML anywhere names an exec-style launcher. A URL that reached
#      execDetached would be argv the user partly controls.
#   2. Quickshell.Io's Process appears in exactly one QML file — the service,
#      which launches the helper. If a panel file acquires a Process, the
#      launcher rule has been routed around even if execDetached is absent.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

PROCESS_OWNER="Service.qml"

# The one file besides the owner that may hold a Process, and why. The harness
# is not shipped — it is not named in `manifest.json`'s entryPoints — and its
# Process writes the scenario file the stub helper reads. Listed explicitly
# rather than exempting `tests/` wholesale, so a Process appearing in any OTHER
# test file is still a violation.
PROCESS_EXEMPT="tests/harness/runner.qml"

while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (REQ-012: open the dashboard with Qt.openUrlExternally)"
  done < <(gate_code_lines "$ROOT/$rel" | grep -E 'execDetached|execArgv|omarchy-launch-browser|xdg-open' || true)

  # `grep -q` reads from a process SUBSTITUTION, never a pipeline. It exits at
  # the first match, and against a file large enough that `gate_code_lines` is
  # still writing, that used to kill awk with SIGPIPE — which `set -o pipefail`
  # (gate.sh:12) turned into status 141, making this `if` false EVEN WHEN GREP
  # MATCHED. The rule was therefore dead on every file long enough to matter,
  # which is every file it exists to police. A substitution's status is not
  # grep's, so pipefail has nothing to observe.
  if [[ $rel != "$PROCESS_OWNER" && $rel != "$PROCESS_EXEMPT" ]] \
     && grep -qE '(^|[^A-Za-z0-9_])Process[[:space:]]*\{' \
          < <(gate_code_lines "$ROOT/$rel"); then
    gate_violation "$rel: instantiates Process; only $PROCESS_OWNER may launch a process"
  fi
done < <(gate_files "$ROOT" -name '*.qml')

gate_done
