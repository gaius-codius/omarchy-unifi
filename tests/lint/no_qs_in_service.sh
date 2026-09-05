#!/usr/bin/env bash
# The service half of the test harness must stay loadable without the UI layer.
#
# Service.qml is instantiated by ensureService() and parented to an invisible
# Item; it has no business importing qs.Ui or qs.Commons. Keeping it free of
# qs.* is what lets the service be exercised under a bare ShellRoot, separately
# from the UI harness root of HC-17. Without this gate a stray `import qs.Ui`
# would silently couple the two and only surface as a confusing import failure.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

f="$ROOT/Service.qml"
if [[ ! -f $f ]]; then
  gate_violation "Service.qml is missing (it is a declared entry point)"
else
  # The check is on IMPORTS specifically, which is exact: QML cannot reference
  # a qs.* type without importing it, so there is no way to depend on the UI
  # layer that this misses.
  while IFS= read -r hit; do
    gate_violation "Service.qml:$hit  (the service must not depend on the UI layer)"
  done < <(gate_code_lines "$f" | grep -E ':[[:space:]]*import[[:space:]]+qs\.' || true)
fi

gate_done
