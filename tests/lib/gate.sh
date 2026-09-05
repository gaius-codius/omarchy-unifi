# Shared helpers for tests/lint/*.sh
#
# Every gate obeys one calling convention:
#
#     tests/lint/<gate>.sh [ROOT]        exit 0 = clean, non-zero = violation
#
# ROOT defaults to the repository root. It is a parameter rather than a
# constant so tests/lint/selftest.sh can point a gate at a scratch copy of the
# tree carrying a deliberate violation and assert the gate bites. A gate that
# hard-coded its own location could not be proven to work.

set -euo pipefail

GATE_NAME="$(basename "${BASH_SOURCE[1]:-gate}" .sh)"

# Resolve ROOT from $1, else the repo root two levels above this library.
gate_root() {
  local root="${1:-}"
  if [[ -z $root ]]; then
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  [[ -d $root ]] || { printf '%s: no such root: %s\n' "$GATE_NAME" "$root" >&2; exit 2; }
  (cd "$root" && pwd)
}

_gate_violations=0

gate_violation() {
  _gate_violations=$((_gate_violations + 1))
  printf '  %s\n' "$*" >&2
}

gate_done() {
  if (( _gate_violations > 0 )); then
    printf 'FAIL %s: %d violation(s)\n' "$GATE_NAME" "$_gate_violations" >&2
    exit 1
  fi
  printf 'ok   %s\n' "$GATE_NAME"
}

# Files a gate should look at. Deliberately excludes .git and any path the
# gate's caller asked to skip. Emits NUL-separated paths relative to ROOT.
gate_files() {
  local root="$1"; shift
  ( cd "$root" && find . -name .git -prune -o -type f "$@" -print0 )
}

# Emit "<lineno>:<text>" for every line of a file that is not a whole-line
# comment. Gates grep this instead of the raw file.
#
# The reason is concrete: no_qs_in_service.sh failed on its own header, which
# explains *why* the service imports no qs.*. A gate that a comment can trip is
# a gate contributors learn to phrase around, and the next person quietly drops
# the explanation rather than the violation.
#
# Only WHOLE-LINE comments are dropped — `// ...` and `# ...` and a `*` block
# continuation. Trailing comments are kept, so nothing can be hidden by
# appending code after a `//` on a line that also contains real code.
gate_code_lines() {
  awk '
    /^[[:space:]]*\/\// { next }
    /^[[:space:]]*\/\*/ { next }
    /^[[:space:]]*\*/   { next }
    /^[[:space:]]*#/    { next }
    { print NR ":" $0 }
  ' "$1"
}
