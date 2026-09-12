#!/usr/bin/env bash
# AC-013 / SEC-001. The only executable check that the API key never enters Git.
#
# Three parts, and the third is the one that matters:
#
#   1. gitleaks over the working tree.
#   2. gitleaks over FULL HISTORY. The repository is scanned from its first
#      commit, so a key committed and later removed is still a finding. Every
#      commit made from Phase 0 onward is permanently in scope.
#   3. A POSITIVE CONTROL. A scan that reports nothing is indistinguishable
#      from a scan that ran nothing — a wrong path, a missing binary, an
#      accidentally broad .gitleaksignore all look like success. So the same
#      invocation is pointed at a throwaway clone carrying a planted canary and
#      is required to find it.
#
# The canary is committed into a clone under mktemp, never into this
# repository. A canary in the real history would make part 2 permanently
# unsatisfiable, which is a slow way to delete the whole gate.
#
# The canary literal is assembled at runtime from two halves so that the token
# gitleaks looks for does not itself appear in any tracked file.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

# Resolve to an absolute path. `mise exec` only resolves a tool while the
# working directory is inside the project that pins it, and this gate scans a
# clone under mktemp -d, which is not.
GITLEAKS="$(command -v gitleaks 2>/dev/null || true)"
# Resolve against the directory this script lives in, NOT the root being
# scanned: `mise which` only resolves a tool from inside the project that pins
# it, and the root may be a scratch clone under /tmp (as it is in selftest.sh).
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -n $GITLEAKS ]] || GITLEAKS="$(cd "$_here" && mise which gitleaks 2>/dev/null || true)"
if [[ -z $GITLEAKS ]] || ! "$GITLEAKS" version >/dev/null 2>&1; then
  gate_violation "gitleaks not available; it is pinned in mise.toml — run 'mise install'"
  gate_done
fi

scan() {  # scan <dir> <extra flags...>
  "$GITLEAKS" detect --source "$1" --no-banner --redact --exit-code 1 \
    --log-level error "${@:2}"
}

# --- 1 & 2: the real repository, tree and history -------------------------
if ! out="$(scan "$ROOT" --no-git 2>&1)"; then
  gate_violation "gitleaks found secrets in the WORKING TREE:"
  while IFS= read -r l; do printf '    %s\n' "$l" >&2; done <<< "$out"
fi
if ! out="$(scan "$ROOT" 2>&1)"; then
  gate_violation "gitleaks found secrets in GIT HISTORY:"
  while IFS= read -r l; do printf '    %s\n' "$l" >&2; done <<< "$out"
fi

# --- pattern grep, everywhere except fixtures -----------------------------
# AC-013 exempts tests/fixtures/, which holds synthetic key-shaped strings by
# design (SEC-011 governs their content instead). secret-patterns.txt is
# exempt because it is the pattern list; scanning it would match itself.
PATTERNS="$(dirname "$0")/secret-patterns.txt"
while IFS= read -r -d '' rel; do
  rel="${rel#./}"
  case "$rel" in
    tests/fixtures/*|tests/lint/secret-patterns.txt) continue ;;
  esac
# -I skips binary files so a marketplace `preview.png` cannot trip a key
# regex on compressed bytes. gitleaks above already scans the tree.
  while IFS= read -r hit; do
    gate_violation "$rel:$hit"
  done < <(grep -I -nEf "$PATTERNS" "$ROOT/$rel" 2>/dev/null || true)
done < <( cd "$ROOT" && find . -name .git -prune -o -type f -print0 )

# --- 3: the positive control ----------------------------------------------
if [[ ${SECRETS_SKIP_POSITIVE_CONTROL:-0} != 1 ]]; then
  # Two canaries matching two independent upstream rules. One would make the
  # control hostage to a single rule surviving a gitleaks upgrade; the gate
  # requires at least one to fire and says which.
  #
  # Both the token and the line it is planted on were verified against
  # gitleaks 8.29.0. Two traps, both hit while building this gate:
  #
  #   * A token containing EXAMPLE is allowlisted by gitleaks by design, so
  #     such a canary is never detected and the control reports a failure that
  #     is really its own.
  #   * The default AWS rule is CONTEXT-SENSITIVE. `token = AKIA...` is
  #     detected; a bare `AKIA...` on its own line, and the SDK idiom
  #     `aws_access_key_id = AKIA...`, are both allowlisted. The GitLab PAT
  #     rule has no such condition and is the sturdier of the two.
  #
  # Hence the planted line below is `token = <canary>` and not something more
  # natural-looking. Changing that string can silently disarm the control, so
  # re-verify against a live gitleaks if you touch it.
  _g1='glpat-'; _g2='7xK9mQ2vRt4WzY8nBc3L'
  _h1='AKIA'; _h2='X7QJ4M2NPRZK8VTD'
  canaries=("${_g1}${_g2}" "${_h1}${_h2}")

  # The real history must not contain them, or part 2 above is compromised.
  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    for c in "${canaries[@]}"; do
      if git -C "$ROOT" grep -qF "$c" HEAD -- . 2>/dev/null \
         || git -C "$ROOT" log -S"$c" --oneline 2>/dev/null | grep -q .; then
        gate_violation "canary $c is present in the real repository; part 2 is no longer meaningful"
      fi
    done
  fi

  clone="$(mktemp -d "${TMPDIR:-/tmp}/secrets-canary.XXXXXX")"
  trap 'rm -rf "$clone"' EXIT
  if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    gate_violation "not a git repository; the positive control did not run"
  else
    detected=0
    for i in "${!canaries[@]}"; do
      probe="$clone/probe$i"
      mkdir -p "$probe"
      git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1 \
        && git clone --quiet "$ROOT" "$probe/repo" 2>/dev/null
      printf 'token = %s\n' "${canaries[$i]}" > "$probe/repo/CANARY"
      git -C "$probe/repo" add CANARY
      git -C "$probe/repo" -c user.name=canary -c user.email=canary@invalid \
          commit --quiet -m 'canary: positive control, throwaway clone only'
      if scan "$probe/repo" >/dev/null 2>&1; then
        printf '  note: canary %d was not detected by this gitleaks version\n' "$i" >&2
      else
        detected=$((detected + 1))
      fi
    done
    if (( detected == 0 )); then
      gate_violation "POSITIVE CONTROL FAILED: gitleaks detected neither planted secret. Every clean result above is meaningless."
    fi
  fi
  rm -rf "$clone"
  trap - EXIT
fi

gate_done
