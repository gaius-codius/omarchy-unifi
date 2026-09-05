#!/usr/bin/env bash
# CP1 exit criterion: "every tests/lint/*.sh demonstrably fails on a seeded
# violation."
#
# A lint gate that has never been observed to fail is indistinguishable from a
# gate with a typo in its regex, a wrong path, or an accidental `|| true`. Each
# gate below is therefore run twice against a scratch copy of the tree: once
# clean, where it must PASS, and once with a deliberate violation planted, where
# it must FAIL. Both directions matter — a gate that fails on everything is as
# useless as one that fails on nothing, it just gets disabled sooner.
#
# Every gate takes its root as an argument precisely so this file can exist.
#
# The scratch copy is a copy. Nothing here writes to the real tree, and the
# secrets gate's own canary control clones rather than commits (see that file).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LINT="$ROOT/tests/lint"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/unifi-gate-selftest.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

pass=0 fail=0

# Build a scratch tree. `git clone` for gates that need history, plain copy
# otherwise (cheaper, and proves the gates do not secretly require a repo).
scratch() {
  local name="$1"
  local mode="${2:-copy}"
  local dir="$WORK/$name"
  rm -rf "$dir"
  if [[ $mode == clone ]]; then
    git clone --quiet "$ROOT" "$dir"
  else
    mkdir -p "$dir"
    ( cd "$ROOT" && tar --exclude=.git -cf - . ) | ( cd "$dir" && tar -xf - )
  fi
  printf '%s' "$dir"
}

check() {  # check <gate> <mode> <seed-shell-snippet>
  local gate="$1" mode="$2" seed="$3" dir out

  dir="$(scratch "$gate" "$mode")"
  if out="$(bash "$LINT/$gate.sh" "$dir" 2>&1)"; then
    :
  else
    printf 'SELFTEST FAIL %-24s clean tree was rejected:\n' "$gate" >&2
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    fail=$((fail + 1)); return 0
  fi

  ( cd "$dir" && eval "$seed" )
  if out="$(bash "$LINT/$gate.sh" "$dir" 2>&1)"; then
    printf 'SELFTEST FAIL %-24s seeded violation was NOT detected\n' "$gate" >&2
    printf '    seed: %s\n' "$seed" >&2
    fail=$((fail + 1)); return 0
  fi

  printf 'ok  %-24s clean passes, seeded violation caught\n' "$gate"
  pass=$((pass + 1))
}

# --- the table -------------------------------------------------------------
# Each seed is the smallest edit that violates exactly the rule under test.

check no_qt_in_js copy \
  'printf "const c = Qt.rgba(0,0,0,1)\nif (typeof module !== \"undefined\") module.exports = { c }\n" > Seed.js'

check js_dialect copy \
  'printf "const ok = 1\n" > Seed.js'   # no dual-export tail: node --test would load nothing

check no_hex_colors copy \
  'printf "import QtQuick\nItem { property color c: \"#ff0000\" }\n" > Seed.qml'

check no_default_ssl_context copy \
  'mkdir -p helper && printf "import ssl\nctx = ssl.create_default_context()\n" > helper/seed.py'

check no_exec_for_dashboard copy \
  'printf "import QtQuick\nimport Quickshell\nItem { function go(u) { Quickshell.execDetached([\"xdg-open\", u]) } }\n" > Seed.qml'

check no_symlinks copy \
  'ln -s /etc/passwd seed-link'

check no_qs_in_service copy \
  'printf "import qs.Ui\n" >> Service.qml'

check no_repo_writes copy \
  'mkdir -p helper/__pycache__ && printf "x" > helper/__pycache__/seed.cpython-39.pyc'

# The seed is an unknown TYPE, not a syntax error. qmllint exits 0 on unknown
# types by default; catching this is the whole reason the gate promotes
# categories to `error`, so the seed must test that promotion and not the
# parser, which needs no gate to work.
check qmllint copy \
  'printf "import QtQuick\nItem { NoSuchType { } }\n" > Seed.qml'

# The banned term is assembled from halves for the same reason the gate does
# it: writing it literally here would make this file trip the gate it tests.
check no_latency_metric copy \
  '_a=laten; _b=cy; jq --arg m "$_a$_b" ".barWidget.schema.compactMetric.enum += [\$m]" manifest.json > m.tmp && mv m.tmp manifest.json'

# The secrets gate needs real history: its history scan and its canary control
# both run git. The seed is a live-shaped token in the working tree, assembled
# here in halves for the same reason the gate assembles its own.
check secrets clone \
  '_a=glpat-; _b=9mQ4vRt7WzY2nBc8LxK3; printf "token = %s%s\n" "$_a" "$_b" > seed-secret.txt'

printf '\n%d gate(s) proven, %d failure(s)\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
