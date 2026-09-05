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

while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (REQ-012: open the dashboard with Qt.openUrlExternally)"
  done < <(gate_code_lines "$ROOT/$rel" | grep -E 'execDetached|execArgv|omarchy-launch-browser|xdg-open' || true)

  if [[ $rel != "$PROCESS_OWNER" ]] && gate_code_lines "$ROOT/$rel" | grep -qE '(^|[^A-Za-z0-9_])Process[[:space:]]*\{'; then
    gate_violation "$rel: instantiates Process; only $PROCESS_OWNER may launch a process"
  fi
done < <(gate_files "$ROOT" -name '*.qml')

gate_done
