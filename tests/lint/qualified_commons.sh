#!/usr/bin/env bash
# HC-23. `qs.Commons` must be imported under the `Commons` qualifier.
#
# Qt 6.12 added a `Color` singleton to QtQuick. In a file that imports both
# QtQuick and an unqualified `qs.Commons`, QtQuick's `Color` wins, so
# `Color.foreground` is undefined and every colour built from it paints black.
# Omarchy's own files import `qs.Commons as Commons` for this reason. A
# qualified import cannot be shadowed by whatever a future QtQuick adds.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

while IFS= read -r -d '' rel; do
  while IFS= read -r hit; do
    gate_violation "${rel#./}:$hit  (import qs.Commons as Commons — HC-23)"
  # Any import of qs.Commons that lacks an `as <Qualifier>`: a version number,
  # a trailing comment or a semicolon must not hide an unqualified import.
  done < <(gate_code_lines "$ROOT/${rel#./}" \
             | grep -E '^[0-9]+:[[:space:]]*import[[:space:]]+qs\.Commons([^A-Za-z0-9_.]|$)' \
             | grep -v -E 'import[[:space:]]+qs\.Commons([[:space:]]+[0-9.]+)?[[:space:]]+as[[:space:]]+[A-Z][A-Za-z0-9_]*' || true)
done < <(gate_files "$ROOT" -name '*.qml')

gate_done
