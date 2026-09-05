#!/usr/bin/env bash
# REQ-001a / AC-034. The panel and widget paint using Omarchy theme tokens only.
#
# The theme exposes no green/amber/red, so a hex literal is the obvious way to
# reach for a health colour — and it is exactly what breaks under a light theme.
# Derivations of a token (Qt.darker(Color.foreground, 1.55)) are allowed; the
# ban is on literals that ignore the theme entirely.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

while IFS= read -r -d '' rel; do
  while IFS= read -r hit; do
    gate_violation "${rel#./}:$hit  (use a theme token, not a literal — REQ-001a)"
  done < <(gate_code_lines "$ROOT/${rel#./}" | grep -E '"#[0-9a-fA-F]{3,8}"' || true)
done < <(gate_files "$ROOT" -name '*.qml')

gate_done
