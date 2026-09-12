#!/usr/bin/env bash
# SEC-012. The only executable check that uninstall guidance names both
# halves of the credential cleanup.
#
# Uninstall removes the plugin directory and leaves ~/.config/omarchy-unifi/
# (and the API key in it) on disk. Deleting that copy does not invalidate the
# key — anyone who obtained it can still use it. A README that names only the
# `rm` is therefore a security defect, and a future edit can drop the revoke
# sentence without turning anything else red. This file exists because that
# sentence has no other test.
#
# Bash rather than a lint gate: this is a content assertion on one section of
# one file, not a tree-wide prohibition. tests/lint/ is auto-discovered as
# gates, and CP1 requires every gate to fail a seeded violation in
# tests/lint/selftest.sh. A content check does not belong in that contract.
#
# Optional argument: a README to check, so a discrimination probe can point
# this at a scratch copy. Defaults to the repository's README.md. Scratch
# copies live under mktemp — HC-14: the suite writes nothing into the tree.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README="${1:-$REPO/README.md}"

passes=0
failures=0
declare -a FAILED=()

ok()   { passes=$((passes + 1)); printf '  ok   %s\n' "$1"; }
bad()  { failures=$((failures + 1)); FAILED+=("$1"); printf '  FAIL %s\n' "$1" >&2; [[ $# -gt 1 ]] && printf '       %s\n' "$2" >&2; }

declare -a TMPFILES=()
cleanup() { for f in "${TMPFILES[@]:-}"; do [[ -n $f ]] && rm -f "$f"; done; }
trap cleanup EXIT

[[ -f $README ]] || { printf 'test_readme.sh: no such file: %s\n' "$README" >&2; exit 2; }

# The removal section, and only that section. A mention under Requirements
# ("created in the UniFi console") or Development would not be where a user
# looking up uninstall would read it.
removal_section() {
  awk '
    /^## (Removing it|Remove)([[:space:]]|$)/ { p=1; next }
    p && /^## / { exit }
    p { print }
  ' "$1"
}

# SEC-012's two instructions. Split so a missing revoke cannot hide behind a
# present rm, which is the exact edit this test exists to catch.
#
# Dots in the path are literal: an unescaped `.` would accept
# `~/Xconfig/omarchy-unifi` and the check would not be checking the directory
# the key actually lives in.
#
# `grep -q` reads from a here-string, never a pipeline. A `producer | grep -q`
# pipeline returns 141 when grep exits early on a match, and `set -o pipefail`
# turns that into a false negative — the same inversion tests/lint/no_exec_for_dashboard.sh
# records at its Process check.
section_holds_both() {
  local file="$1" section
  section="$(removal_section "$file")"
  if [[ -z $section ]]; then
    printf 'no "## Removing it" section\n'
    return 1
  fi
  local missing=()
  if ! grep -qE '~/\.config/omarchy-unifi/?' <<<"$section"; then
    missing+=("deleting ~/.config/omarchy-unifi/")
  fi
  # The revoke sentence is the half a tidy-up drops. "API key" also appears in
  # the survives-uninstall paragraph, so it cannot be the discriminator —
  # `revoke` and `UniFi console` live only in that sentence.
  if ! grep -qiE 'revoke' <<<"$section" || ! grep -qiE 'UniFi console' <<<"$section"; then
    missing+=("revoking the API key in the UniFi console")
  fi
  if (( ${#missing[@]} )); then
    printf '%s\n' "missing: ${missing[*]}"
    return 1
  fi
  return 0
}

printf '=== README removal (SEC-012)\n'

# --- positive control (default README only) --------------------------------
# A check that has never been observed to fail is indistinguishable from one
# with a typo in its regex. The same invocation is pointed at a throwaway copy
# whose revoke sentence has been deleted, and is required to reject it. The
# rm of ~/.config/omarchy-unifi/ stays, so a pass here cannot be "the copy is
# missing everything".
#
# Skipped when a path is supplied: that is the discrimination probe, and
# folding a second copy into it would mix "the mutant is rejected" with a
# control derived from the mutant.
if [[ $# -eq 0 ]]; then
  scratch="$(mktemp)"
  TMPFILES+=("$scratch")
  awk '
    /^## (Removing it|Remove)([[:space:]]|$)/ { p=1; print; next }
    p && /^## / { p=0 }
    p && /[Rr]evoke/ { next }
    { print }
  ' "$README" > "$scratch"

  reason=""
  if reason="$(section_holds_both "$scratch")"; then
    bad "a README without the revoke sentence is rejected" "the check passed"
  else
    ok "a README without the revoke sentence is rejected"
  fi
fi

if reason="$(section_holds_both "$README")"; then
  ok "the removal section names deleting ~/.config/omarchy-unifi/ and revoking the API key"
else
  bad "the removal section names deleting ~/.config/omarchy-unifi/ and revoking the API key" "$reason"
fi

printf '\n---------------------------------------------\n'
if (( failures == 0 )); then
  printf 'readme: %d check(s) passed\n' "$passes"
  exit 0
fi
printf 'readme: %d passed, %d FAILED:\n' "$passes" "$failures"
printf '  - %s\n' "${FAILED[@]}"
exit 1
