#!/usr/bin/env bash
# AC-065 (manifest half). The `laten''cy` compact metric does not exist.
#
# This is not a style rule, it is a capability fact. The published UniFi
# Integration API returns {id, name} for /v1/sites/{siteId}/wans — no status,
# no latency figure, no loss, no throughput (api-contract.md). The number
# cannot be produced, so offering the setting would ship a control that
# silently does nothing. The decision to drop it is easy to lose in a later
# "restore the missing metric" change; this gate makes that change fail loudly.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

# Assembled from halves so the term does not appear literally in this file.
# A gate that has to exempt itself from its own scan leaves a hole exactly
# where someone routing around it would look.
_b1='laten'; _b2='cy'
BANNED="${_b1}${_b2}"

MANIFEST="$ROOT/manifest.json"
if [[ ! -f $MANIFEST ]]; then
  gate_violation "manifest.json is missing"
  gate_done
fi

enum="$(jq -c '.barWidget.schema.compactMetric.enum // empty' "$MANIFEST")"
if [[ $enum != '["none","clients"]' ]]; then
  gate_violation "manifest compactMetric enum is ${enum:-absent}, expected [\"none\",\"clients\"] (AC-065)"
fi

# The scope is stated POSITIVELY — the shipped plugin — rather than as a list
# of exclusions. That way the gate never has to exempt itself or its own
# self-test, which is the kind of hole a workaround uses.
#
# docs/ and PLAN.md are outside it deliberately: SPEC.md, the plan and the
# deviation log must be able to explain why the value was removed, and deleting
# that explanation is how the mistake gets made a second time.
scan_targets() {
  cd "$ROOT" || return
  find . -maxdepth 1 -type f \
    \( -name 'manifest.json' -o -name '*.qml' -o -name '*.js' -o -name 'README.md' \) -print0
  for d in helper scripts; do
    [[ -d $d ]] && find "$d" -type f -print0
  done
  return 0
}

while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (the API cannot supply it — see api-contract.md)"
  done < <(gate_code_lines "$ROOT/$rel" | grep -iE "$BANNED" || true)
done < <(scan_targets)

gate_done
