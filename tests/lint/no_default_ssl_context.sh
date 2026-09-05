#!/usr/bin/env bash
# SEC-007 / SEC-007a. The helper builds its TLS context explicitly from
# ssl.SSLContext(PROTOCOL_TLS_CLIENT) and never calls create_default_context().
#
# This is not stylistic. SSLKEYLOGFILE is consumed *inside*
# create_default_context(): CPython reads the variable and assigns
# keylog_filename during construction, so a hostile or stale value raises an
# OSError from within the constructor and, when it succeeds, writes session keys
# for every connection. Clearing keylog_filename afterwards is too late — the
# only fix is to never call the convenience constructor.
#
# The second pattern is the other half of the same rule: `keylog_filename` is
# never ASSIGNED either. Reading it is fine and the tests do, to assert it
# stayed None; writing it is the thing create_default_context does behind your
# back, and doing it deliberately is no better.
source "$(dirname "$0")/../lib/gate.sh"
ROOT="$(gate_root "${1:-}")"

for dir in helper scripts; do
  [[ -d "$ROOT/$dir" ]] || continue
  while IFS= read -r -d '' rel; do
    while IFS= read -r hit; do
      gate_violation "${rel#./}:$hit  (SEC-007: build the context explicitly)"
    done < <(gate_code_lines "$ROOT/${rel#./}" | grep -E 'create_default_context|_create_unverified_context|_create_stdlib_context|keylog_filename[[:space:]]*=' || true)
  done < <( cd "$ROOT" && find "$dir" -type f -name '*.py' -print0 )
done

gate_done
