#!/usr/bin/env bash
# AC-033 (first half). Every dual-use .js module at the repository root must be
# free of `Qt.` and `qs.` so that `node --test` can execute it unchanged.
#
# The scope is a GLOB, not a filename (AMD-1). HC-16 forbids `.import` between
# dual-use files, so the pure layer is necessarily several modules; a gate
# naming one file would police whichever module happened to be written first.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

shopt -s nullglob
modules=("$ROOT"/*.js)
shopt -u nullglob

for f in "${modules[@]}"; do
  rel="${f#"$ROOT"/}"
  while IFS= read -r hit; do
    gate_violation "$rel:$hit"
  done < <(gate_code_lines "$f" | grep -E '(^|[^A-Za-z0-9_$.])(Qt|qs)\.' || true)
done

gate_done
