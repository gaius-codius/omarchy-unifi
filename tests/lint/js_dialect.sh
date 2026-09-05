#!/usr/bin/env bash
# AC-033 (second half) plus the dual-export seam.
#
# Four bans and one requirement, each with a concrete failure it prevents:
#
#   .pragma / .import   HC-16: these are QML-only directives and are syntax
#                       errors to Node. A module using them cannot be tested.
#   top-level var/let   A mutable module-scope binding is shared state. QML may
#                       instantiate a module once per import site; Node caches
#                       it once. The two engines then disagree.
#   Intl / toLocaleString  QML's V4 and Node's V8 do not ship the same ICU data,
#                       so a locale-formatted string is not reproducible across
#                       the two engines the same test corpus runs on.
#
#   module.exports tail REQUIRED. Without it `node --test` imports an empty
#                       object and every assertion in the AUTO layer passes
#                       while testing nothing. This is the single most dangerous
#                       silent failure in the plan, which is why it is a gate
#                       and not a convention. The idiom is the host's own,
#                       plugins/bar/BarModel.js:211-212.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

shopt -s nullglob
modules=("$ROOT"/*.js)
shopt -u nullglob

for f in "${modules[@]}"; do
  rel="${f#"$ROOT"/}"

  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (QML-only directive; Node cannot parse it — HC-16)"
  done < <(gate_code_lines "$f" | grep -E ':[[:space:]]*\.(pragma|import)\b' || true)

  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (mutable top-level binding; use const or a factory)"
  done < <(gate_code_lines "$f" | grep -E ':(var|let)[[:space:]]' || true)

  while IFS= read -r hit; do
    gate_violation "$rel:$hit  (locale-dependent; V4 and V8 disagree)"
  done < <(gate_code_lines "$f" | grep -E '(^|[^A-Za-z0-9_$])(Intl\b|toLocaleString|toLocaleDateString|toLocaleTimeString)' || true)

  if ! grep -qE 'typeof[[:space:]]+module[[:space:]]*!==[[:space:]]*"undefined"' "$f" \
     || ! grep -q 'module\.exports' "$f"; then
    gate_violation "$rel: missing the dual-export tail; node --test would load an empty module"
  fi
done

gate_done
