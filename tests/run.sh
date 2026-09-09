#!/usr/bin/env bash
# The single test orchestrator. "Run the suite at every checkpoint" means this.
#
#   tests/run.sh                  AUTO only: lint gates, validate, secrets,
#                                 node --test, the Python suite under BOTH
#                                 pinned interpreters. No graphical session,
#                                 no controller, nothing staged.
#   tests/run.sh --gates          the lint gates' own self-test (CP1)
#   tests/run.sh --live-harness   + the quickshell harness. Needs a live
#                                 Wayland session (HC-11). Stages nothing.
#   tests/run.sh --live-staged    + checks that require the plugin installed
#                                 into ~/.config/omarchy/plugins/. GATED.
#   tests/run.sh --live           both of the above (the SPEC §13 spelling)
#
# The LIVE tier in SPEC §13 is really two tiers, because HC-17 showed the UI
# layer loads from a config root outside the repository. --live-harness needs
# only a Wayland session; only --live-staged touches ~/.config/omarchy.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

LIVE_HARNESS=0
LIVE_STAGED=0
GATES=0
for arg in "$@"; do
  case "$arg" in
    --live-harness) LIVE_HARNESS=1 ;;
    --live-staged)  LIVE_STAGED=1 ;;
    --live)         LIVE_HARNESS=1; LIVE_STAGED=1 ;;
    --gates)        GATES=1 ;;
    -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "run.sh: unknown flag $arg" >&2; exit 2 ;;
  esac
done

failures=0
declare -a FAILED=()

step() {  # step <label> <command...>
  local label="$1"; shift
  printf '\n=== %s\n' "$label"
  if "$@"; then
    return 0
  fi
  printf '!!! FAILED: %s\n' "$label" >&2
  failures=$((failures + 1))
  FAILED+=("$label")
  return 0   # keep going; a full report beats a first-failure abort
}

# --- HC-14: prove the suite writes nothing into the tree -------------------
# Recorded before anything runs and compared after everything has. A
# __pycache__ directory appearing under a staged plugin hot-reloads it mid-poll,
# so this is the check that keeps `-B` from being merely a convention.
BASELINE="$(mktemp)"
trap 'rm -f "$BASELINE"' EXIT
bash tests/lint/no_repo_writes.sh "$REPO" --record "$BASELINE"

# --- Python interpreters ---------------------------------------------------
# Both ends of the SPEC §11 support range. Testing only the current interpreter
# would leave the declared 3.9 floor as an assertion rather than a fact; a 3.10+
# syntax feature would ship and fail on the oldest supported system.
#
# Every invocation carries -B (HC-14), -E and -s (SEC: ignore PYTHON* env and
# the user site directory, so the suite cannot be steered by the environment).
PYFLAGS=(-B -E -s)
pinned_pythons() {
  awk -F'"' '/^python *=/ {for(i=2;i<=NF;i+=2) print $i}' mise.toml
}
pythons() {
  local v p
  for v in $(pinned_pythons); do
    p="$(mise which python --tool=python@"$v" 2>/dev/null || true)"
    [[ -n $p ]] && printf '%s\t%s\n' "$v" "$p"
  done
}

# --- AUTO: gates -----------------------------------------------------------
if (( GATES )); then
  step "lint gates: self-test (seeded violations)" bash tests/lint/selftest.sh
fi

for g in tests/lint/*.sh; do
  [[ "$(basename "$g")" == selftest.sh ]] && continue
  step "lint: $(basename "$g" .sh)" bash "$g"
done

step "omarchy plugin validate" omarchy plugin validate "$REPO"

# --- AUTO: model layer -----------------------------------------------------
shopt -s nullglob
# test_end_to_end.js is named without the `.test.js` suffix and is listed
# explicitly, because it is not a model test: it SPAWNS the real helper against
# a local TLS stub. It is still AUTO — no Wayland, no staging, no controller —
# but it costs seconds rather than milliseconds and it is worth being able to
# see that in the list.
model_tests=(tests/*.test.js tests/model/*.test.js tests/test_suite_integrity.js)
[[ -f tests/test_end_to_end.js ]] && model_tests+=(tests/test_end_to_end.js)
shopt -u nullglob
if (( ${#model_tests[@]} )); then
  step "node --test (pure model layer)" node --test "${model_tests[@]}"
else
  printf '\n=== node --test: no test files yet (Phase 2)\n'
fi

# --- AUTO: helper suite, under every pinned interpreter --------------------
# Each file is invoked DIRECTLY rather than through `unittest discover`.
# discover requires the start directory to be an importable package, which
# would mean adding tests/__init__.py and putting the repo root on sys.path via
# PYTHONPATH — and PYTHONPATH is exactly what -E discards. Each test file
# instead derives the repo root from its own __file__, which works identically
# under both interpreters with -E -s in force. Verified on 3.9.25 and 3.14.7.
shopt -s nullglob
py_tests=(tests/test_*.py)
shopt -u nullglob
if (( ${#py_tests[@]} )); then
  # FAIL CLOSED. `pythons()` swallows mise's stderr, so an unresolved interpreter
  # used to leave this loop iterating zero times: no step registered, nothing
  # printed, nothing counted, and `SUITE PASS` at the end. A fresh clone hits it
  # every time — mise refuses an untrusted mise.toml — so the tier most worth
  # running was the one silently skipped, on exactly the machine least likely to
  # notice. `secrets.sh` has always failed closed on a missing binary; this now
  # does too.
  #
  # Resolving SOME of the pins is also a failure. Dropping 3.9 while 3.14 runs
  # turns the declared support floor back into the assertion this tier exists to
  # replace, and it would do so without a word.
  mapfile -t pinned   < <(pinned_pythons)
  mapfile -t resolved < <(pythons)
  if (( ${#resolved[@]} != ${#pinned[@]} )); then
    printf '\n=== python suite: PINNED INTERPRETERS NOT RESOLVED\n' >&2
    printf '    mise.toml pins %d (%s); mise resolved %d.\n' \
      "${#pinned[@]}" "$(IFS=,; echo "${pinned[*]}")" "${#resolved[@]}" >&2
    for v in "${pinned[@]}"; do
      if ! printf '%s\n' "${resolved[@]}" | grep -q "^$v"$'\t'; then
        printf '    missing: python@%s — %s\n' "$v" \
          "$(mise which python --tool=python@"$v" 2>&1 | head -1)" >&2
      fi
    done
    printf '    Run `mise trust && mise install`. Refusing to report a pass\n' >&2
    printf '    for %d test file(s) that did not run.\n' "${#py_tests[@]}" >&2
    failures=$((failures + 1)); FAILED+=("python suite: pinned interpreters not resolved")
  else
    for entry in "${resolved[@]}"; do
      IFS=$'\t' read -r ver bin <<< "$entry"
      for t in "${py_tests[@]}"; do
        step "python $ver: $t" "$bin" "${PYFLAGS[@]}" "$t"
      done
    done
  fi
else
  printf '\n=== python suite: not written yet (Phase 5)\n'
fi

if [[ -f tests/test_configure.sh ]]; then
  step "tests/test_configure.sh" bash tests/test_configure.sh
else
  printf '\n=== configure suite: not written yet (Phase 8)\n'
fi

# --- LIVE (harness): needs Wayland, stages nothing -------------------------
if (( LIVE_HARNESS )); then
  if [[ -z ${WAYLAND_DISPLAY:-} ]]; then
    printf '!!! --live-harness needs a live Wayland session (HC-11: qmltestrunner cannot load Quickshell.Io)\n' >&2
    failures=$((failures + 1)); FAILED+=("--live-harness precondition")
  elif [[ -f tests/harness/runner.qml ]]; then
    step "quickshell harness" bash tests/harness/run_harness.sh
  else
    printf '\n=== quickshell harness: not written yet (Phase 6)\n'
  fi
fi

# --- LIVE (staged): GATED --------------------------------------------------
if (( LIVE_STAGED )); then
  printf '\n=== live-staged\n'
  if [[ ${OMARCHY_UNIFI_STAGING_APPROVED:-0} != 1 ]]; then
    cat >&2 <<'GATE'
!!! REFUSED: --live-staged installs this plugin into ~/.config/omarchy/plugins/.

    SPEC.md §15 classifies staging as stop-and-ask (gate G-STAGING), so it is
    not something a test run may decide to do. Obtain explicit approval, then
    re-run with OMARCHY_UNIFI_STAGING_APPROVED=1.

    Nothing has been installed.
GATE
    failures=$((failures + 1)); FAILED+=("--live-staged is gated (G-STAGING)")
  elif [[ -f tests/harness/staged.sh ]]; then
    step "staged checks" bash tests/harness/staged.sh
  else
    printf '=== staged checks: not written yet (Phase 11)\n'
  fi
fi

# --- HC-14 again, now that everything has run ------------------------------
step "no repo writes (post-run)" bash tests/lint/no_repo_writes.sh "$REPO" "$BASELINE"

# --- report ----------------------------------------------------------------
printf '\n---------------------------------------------\n'
if (( failures == 0 )); then
  printf 'SUITE PASS\n'
  exit 0
fi
printf 'SUITE FAIL — %d step(s):\n' "$failures"
printf '  - %s\n' "${FAILED[@]}"
exit 1
