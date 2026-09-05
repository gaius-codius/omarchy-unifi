#!/usr/bin/env bash
# AC-027. omarchy-plugin-validate:115 refuses any symlink inside a plugin
# folder, because `omarchy plugin add` clones this repository straight into the
# trusted plugins directory and a link could then point at anything on disk.
#
# The repository root IS the plugin folder (SPEC §11), so the ban covers docs/
# and tests/ too. That is why the qmllint import root (HC-12) and the UI
# harness root (HC-17) both live outside the tree.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

while IFS= read -r -d '' link; do
  gate_violation "${link#./}: symlink (AC-027 / omarchy-plugin-validate:115)"
done < <( cd "$ROOT" && find . -name .git -prune -o -type l -print0 )

gate_done
