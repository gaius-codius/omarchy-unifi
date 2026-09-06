#!/usr/bin/env bash
# AC-010, AC-055, AC-009's write half, and DATA-004's commit protocol.
#
# Bash rather than Python, because what is under test is a Bash entry point:
# the interpreter check, the delegation flags, and the exit status a caller
# would act on. Driving it through Python's subprocess would test the same
# script through a layer that hides the thing that matters.
#
# Every run uses a throwaway configuration directory under mktemp and a stub
# `omarchy` prepended to PATH. Nothing here touches ~/.config, and nothing here
# opens a network connection: `apiRoot` is TEST-NET-1 (RFC 5737), which is
# guaranteed not to be routed.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURE="$REPO/scripts/configure"
STUBBIN="$REPO/tests/tools/stubbin"

# Loopback, port 1. Nothing listens there, so the helper's connect is refused
# instantly and locally: these checks care whether it got PAST the commit
# verification, not what the controller said. An unroutable documentation
# address would also be safe but would cost the helper's whole 25 s budget per
# invocation waiting for a SYN nobody will answer.
API_ROOT="https://127.0.0.1:1/proxy/network/integration"
API_KEY="sk-configure-test-key"
SITE_A="140d6676-08f6-5cbd-806a-bff7222ccc5d"
SITE_B="47c53529-88cc-5d8b-9d2e-d15c8d965691"

passes=0
failures=0
declare -a FAILED=()

ok()   { passes=$((passes + 1)); printf '  ok   %s\n' "$1"; }
bad()  { failures=$((failures + 1)); FAILED+=("$1"); printf '  FAIL %s\n' "$1" >&2; [[ $# -gt 1 ]] && printf '       %s\n' "$2" >&2; }

check() {  # check <label> <expected> <actual>
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1" "expected [$2], got [$3]"; fi
}

workdir() {
  local dir
  dir="$(mktemp -d)"
  TMPDIRS+=("$dir")
  printf '%s' "$dir/omarchy-unifi"
}
declare -a TMPDIRS=()
cleanup() { for d in "${TMPDIRS[@]:-}"; do [[ -n $d ]] && rm -rf "$d"; done; }
trap cleanup EXIT

configure() {  # configure <config-dir> [args...]
  local dir="$1"; shift
  PATH="$STUBBIN:$PATH" bash "$CONFIGURE" --config-dir "$dir" "$@"
}

digests() {  # digests <config-dir> -> one sha256 per committed file
  local dir="$1" name
  for name in config.json api-key commit.json; do
    if [[ -f "$dir/$name" ]]; then
      sha256sum "$dir/$name" | cut -d' ' -f1
    else
      printf 'absent\n'
    fi
  done
}

seed() {  # seed <config-dir> -> a committed set, via the delivered path
  local dir="$1"
  printf '%s' "$API_KEY" | STUB_OMARCHY_STDERR="" configure "$dir" \
    --api-root "$API_ROOT" --site "$SITE_A" --api-key-stdin >/dev/null 2>&1
}

printf '=== scripts/configure\n'

# --- the interpreter check comes first --------------------------------------
# SPEC §11: Omarchy's installer does not install runtime packages, so a missing
# python3 is the one failure this script must report as a sentence rather than
# as an interpreter error.
# A PATH with the externals the script needs and NO python3. `$BASH` is used
# absolutely, because resolving `bash` through the stripped PATH would fail for
# the wrong reason — which is exactly how a first version of this check passed
# with exit 127 and no message.
nopython="$(mktemp -d)"; TMPDIRS+=("$nopython")
ln -s "$(command -v cat)" "$nopython/cat"
out="$(PATH="$nopython" "$BASH" "$CONFIGURE" --help 2>&1)"; status=$?
check "a missing python3 exits non-zero" "1" "$status"
case $out in
  *"python3 is not installed"*) ok "a missing python3 names itself" ;;
  *) bad "a missing python3 names itself" "$out" ;;
esac

# SPEC §11's floor is 3.9: the configuration is opened by descriptor, which
# needs dir_fd support older versions do not have. Reported as a sentence, not
# as a SyntaxError from an import.
oldpython="$(mktemp -d)"; TMPDIRS+=("$oldpython")
cat > "$oldpython/python3" <<'STUB'
#!/usr/bin/env bash
# Reports 3.8 for the version probe and refuses to run anything else.
if [[ "$*" == *"sys.version_info"* ]]; then echo "3 8"; exit 0; fi
echo "the too-old stub was asked to run a program" >&2
exit 9
STUB
chmod +x "$oldpython/python3"
out="$(PATH="$oldpython:$PATH" "$BASH" "$CONFIGURE" --help 2>&1)"; status=$?
check "a too-old python3 exits non-zero" "1" "$status"
case $out in
  *"too old"*) ok "a too-old python3 says so before importing anything" ;;
  *) bad "a too-old python3 says so before importing anything" "$out" ;;
esac

# --- HC-14: the delegation must carry -B ------------------------------------
# `configure` runs from the INSTALLED plugin directory and imports
# helper/unifi/commitset.py. Without -B that import writes __pycache__ into the
# plugin tree, which the registry sees as a change and hot-reloads mid-run.
if grep -qE 'exec python3 -B( |$)|exec python3 -B -E -s' "$CONFIGURE"; then
  ok "the delegation runs python3 -B"
else
  bad "the delegation runs python3 -B"
fi
before_pycache="$(find "$REPO/helper" -name '__pycache__' | wc -l)"

# --- a first run writes the whole set ---------------------------------------
dir="$(workdir)"
out="$(printf '%s' "$API_KEY" | STUB_OMARCHY_STDERR="" configure "$dir" \
  --api-root "$API_ROOT" --api-key-stdin 2>&1)"; status=$?
check "a first run exits 0" "0" "$status"
check "the directory is 0700" "700" "$(stat -c '%a' "$dir")"
for name in config.json api-key commit.json; do
  check "$name is 0600" "600" "$(stat -c '%a' "$dir/$name")"
done
check "the first generation is 1" "1" \
  "$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["commitGeneration"])' "$dir/commit.json")"

after_pycache="$(find "$REPO/helper" -name '__pycache__' | wc -l)"
check "no __pycache__ appeared in the plugin tree" "$before_pycache" "$after_pycache"

# --- DATA-004b: the digests are over the RAW bytes --------------------------
# The credential is written with a trailing newline and hashed as written. A
# script that hashed the trimmed value and a helper that hashes the file would
# produce a set that can never validate, permanently.
recorded="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["apiKeySha256"])' "$dir/commit.json")"
actual="$(sha256sum "$dir/api-key" | cut -d' ' -f1)"
check "the credential digest is over the file's raw bytes" "$actual" "$recorded"

# --- the helper accepts what configure wrote --------------------------------
# The two sides agree by SHARED CODE, not by convention: configure imports the
# same commitset.py the helper verifies with. This asserts the consequence.
kind="$(python3 -B -E -s "$REPO/helper/unifi_status.py" --nonce e2ecfg --config-dir "$dir" \
  | python3 -B -c 'import json,sys; print(json.load(sys.stdin)["error"]["kind"])')"
if [[ $kind != "uncommitted" && $kind != "unconfigured" && $kind != "credential" ]]; then
  ok "the helper accepts the committed set (reached $kind)"
else
  bad "the helper accepts the committed set" "got $kind"
fi

# --- DATA-010b: every outcome, and exactly three exit 0 ---------------------
printf '\n=== DATA-010b outcomes (AC-010)\n'
zero_exits=0
run_outcome() {  # run_outcome <label> <stderr> <expected exit>
  local label="$1" message="$2" expected="$3"
  local dir; dir="$(workdir)"
  seed "$dir"
  local before; before="$(digests "$dir")"
  local out; out="$(printf '%s' "$API_KEY" | STUB_OMARCHY_STDERR="$message" \
    configure "$dir" --site "$SITE_B" --api-key-stdin 2>&1)"
  local status=$?
  check "$label exits $expected" "$expected" "$status"
  (( status == 0 )) && zero_exits=$((zero_exits + 1))
  local after; after="$(digests "$dir")"
  if (( expected == 0 )); then
    if [[ "$before" != "$after" ]]; then
      ok "$label committed the change"
    else
      bad "$label committed the change" "files are unchanged"
    fi
  else
    # AC-010: every non-zero path leaves all three files byte-identical to
    # their pre-run digests. The run is atomic across its own exit status.
    check "$label leaves the files byte-identical" "$before" "$after"
    case $out in
      *"NOT changed"*) ok "$label says the configuration was not changed" ;;
      *) bad "$label says the configuration was not changed" "$out" ;;
    esac
  fi
}

# The DATA-010b table's first row is written "(none, exit 0)" — no stderr at
# all. Named here verbatim so the AC-072 coverage scan can see that this
# outcome has a test, the same way the other six are named by their messages.
run_outcome "delivered (none, exit 0)"   ""                                0
run_outcome "omarchy-shell is not running"    "omarchy-shell is not running"    0
run_outcome "Target not found."               "Target not found."               0
run_outcome "omarchy-shell is not responding" "omarchy-shell is not responding" 1
run_outcome "omarchy-shell is not ready"      "omarchy-shell is not ready"      1
run_outcome "Function not found."             "Function not found."             1
run_outcome "any other"                       "something nobody has seen"       1

check "exactly three outcomes exit 0" "3" "$zero_exits"

# DATA-010b classifies on the EXACT message. A substring match would read
# "Target not found. (plus something else)" as the deferred-success case and
# exit 0 on a failure nobody has seen before.
run_outcome "a message CONTAINING a known one" "Target not found. and then some" 1

# --- the reload is invoked WITHOUT -q ---------------------------------------
# With -q the wrapper exits 0 silently for every failure, which would make the
# whole classification above unobservable (HC-5).
dir="$(workdir)"; seed "$dir"
log="$(mktemp)"; TMPDIRS+=("$log")
printf '%s' "$API_KEY" | STUB_OMARCHY_LOG="$log" STUB_OMARCHY_STDERR="" \
  configure "$dir" --site "$SITE_B" --api-key-stdin >/dev/null 2>&1
if grep -q -- '-q' "$log"; then
  bad "the reload is invoked without -q" "$(cat "$log")"
else
  ok "the reload is invoked without -q"
fi
check "the reload targets gaius-codius.unifi reload" "shell gaius-codius.unifi reload" "$(head -1 "$log")"

# --- --commit mode ----------------------------------------------------------
printf '\n=== --commit\n'
dir="$(workdir)"; seed "$dir"
# A hand edit breaks the set: the digests no longer describe the files.
python3 -B -c '
import json, sys
path = sys.argv[1]
body = json.load(open(path))
body["siteId"] = sys.argv[2]
open(path, "w").write(json.dumps(body, indent=2, sort_keys=True) + "\n")
' "$dir/config.json" "$SITE_B"
kind="$(python3 -B -E -s "$REPO/helper/unifi_status.py" --nonce cfg --config-dir "$dir" \
  | python3 -B -c 'import json,sys; print(json.load(sys.stdin)["error"]["kind"])')"
check "a hand edit makes the set uncommitted" "uncommitted" "$kind"

before_config="$(sha256sum "$dir/config.json" | cut -d' ' -f1)"
STUB_OMARCHY_STDERR="" configure "$dir" --commit >/dev/null 2>&1
status=$?
check "--commit exits 0" "0" "$status"
after_config="$(sha256sum "$dir/config.json" | cut -d' ' -f1)"
# DATA-004b hashes raw bytes, so --commit must NOT reformat the file: doing so
# would change the digest of what the user hand-edited and hide what they wrote.
check "--commit leaves the hand-edited file byte-identical" "$before_config" "$after_config"
check "--commit raises the generation" "2" \
  "$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["commitGeneration"])' "$dir/commit.json")"
kind="$(python3 -B -E -s "$REPO/helper/unifi_status.py" --nonce cfg --config-dir "$dir" \
  | python3 -B -c 'import json,sys; print(json.load(sys.stdin)["error"]["kind"])')"
if [[ $kind != "uncommitted" ]]; then
  ok "--commit makes the set valid again (reached $kind)"
else
  bad "--commit makes the set valid again" "still uncommitted"
fi

out="$(STUB_OMARCHY_STDERR="" configure "$dir" --commit --site "$SITE_A" 2>&1)"; status=$?
check "--commit refuses to be combined with an edit" "1" "$status"

# --- validation refuses to write something the helper cannot use ------------
printf '\n=== validation\n'
reject() {  # reject <label> <args...>
  local label="$1"; shift
  local dir; dir="$(workdir)"
  seed "$dir"
  local before; before="$(digests "$dir")"
  printf '%s' "$API_KEY" | STUB_OMARCHY_STDERR="" configure "$dir" --api-key-stdin "$@" >/dev/null 2>&1
  local status=$?
  local after; after="$(digests "$dir")"
  if (( status == 0 )); then
    bad "$label is refused" "exited 0"
  elif [[ "$before" != "$after" ]]; then
    bad "$label is refused" "the files changed"
  else
    ok "$label is refused and changes nothing"
  fi
}

reject "a plain-http apiRoot"      --api-root "http://192.0.2.9/proxy"
reject "an apiRoot with userinfo"  --api-root "https://u:p@192.0.2.9/proxy"
reject "a traversing apiRoot"      --api-root "https://192.0.2.9/proxy/../../admin"
reject "a non-UUID site"           --site "not-a-uuid"
reject "a traversing site"         --site "x/../../v1/hotspots"
reject "a missing custom CA"       --custom-ca "/nonexistent/ca.pem"

dir="$(workdir)"
out="$(printf 'sk-with a space' | STUB_OMARCHY_STDERR="" configure "$dir" \
  --api-root "$API_ROOT" --api-key-stdin 2>&1)"; status=$?
check "a credential with whitespace is refused" "1" "$status"
if [[ -f "$dir/api-key" ]]; then
  bad "a refused credential is never written"
else
  ok "a refused credential is never written"
fi
case $out in
  *"sk-with"*) bad "the refusal never quotes the credential" "$out" ;;
  *) ok "the refusal never quotes the credential" ;;
esac

# --- SEC-001: the credential never appears in argv --------------------------
# Asserted behaviourally, not by grepping the source: a first version's regex
# matched the docstring that EXPLAINS why the option does not exist, which is
# the same trap as N-25 and N-31. argv is world-readable through /proc, so a
# `--api-key <value>` option would publish the credential to every user on the
# machine for as long as this ran.
dir="$(workdir)"
out="$(STUB_OMARCHY_STDERR="" configure "$dir" --api-key "$API_KEY" 2>&1)"; status=$?
if (( status == 0 )); then
  bad "there is no --api-key value option" "it was accepted"
else
  case $out in
    *"unrecognized arguments"*|*"unrecognised arguments"*)
      ok "there is no --api-key value option" ;;
    *) bad "there is no --api-key value option" "$out" ;;
  esac
fi

# --- AC-055: two concurrent runs serialise on the flock ---------------------
printf '\n=== AC-055 concurrency\n'

# The deterministic half. Six racing processes cannot reliably catch a missing
# lock — process startup dominates, so they mostly serialise on their own, and
# removing the flock entirely still passed. So the lock is tested for what it
# is: another holder takes it, and `configure` must WAIT.
dir="$(workdir)"; seed "$dir"
holder_log="$(mktemp)"; TMPDIRS+=("$holder_log")
python3 -B -E -s -c '
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").write("held")
time.sleep(float(sys.argv[3]))
' "$dir/.configure.lock" "$holder_log" 2 &
holder=$!
for _ in $(seq 1 50); do
  [[ -s "$holder_log" ]] && break
  sleep 0.1
done
if [[ ! -s "$holder_log" ]]; then
  bad "the lock holder started"
else
  ok "the lock holder started"
  started="$(date +%s%N)"
  printf '%s' "$API_KEY" | STUB_OMARCHY_STDERR="" configure "$dir" \
    --site "$SITE_B" --api-key-stdin >/dev/null 2>&1
  status=$?
  elapsed_ms=$(( ($(date +%s%N) - started) / 1000000 ))
  check "configure completes once the lock is released" "0" "$status"
  if (( elapsed_ms >= 1000 )); then
    ok "configure waited for the lock (${elapsed_ms}ms)"
  else
    bad "configure waited for the lock" "finished in ${elapsed_ms}ms"
  fi
fi
wait "$holder" 2>/dev/null

# The integration half. This cannot fail reliably — six short processes usually
# serialise without any help — so it is a smoke check on the whole path, not the
# assertion that the lock works. Every run writes DIFFERENT content. A first version had them all write the
# same site and the same key, which made the test unable to fail: any interleave
# of identical bytes is still a consistent set, and removing the flock entirely
# passed. With distinct content, an interleaved pair of renames leaves a marker
# certifying one run's config beside another run's key — permanently
# `uncommitted`, which is exactly what DATA-004a exists to prevent.
dir="$(workdir)"; seed "$dir"
sites=(
  "140d6676-08f6-5cbd-806a-bff7222ccc5d"
  "47c53529-88cc-5d8b-9d2e-d15c8d965691"
  "6ad37e61-e4f2-5738-9a83-525ae3e6f168"
  "83a8f994-a402-55d0-89d5-a1a5937be8e6"
  "b8028173-5c97-5682-bb49-cea8c08122fd"
  "1adb131d-3ac8-5392-bac7-cde80c9a0699"
)
for i in 0 1 2 3 4 5; do
  ( printf 'sk-concurrent-key-%02d' "$i" | STUB_OMARCHY_STDERR="" configure "$dir" \
      --site "${sites[$i]}" --api-key-stdin >/dev/null 2>&1 ) &
done
wait
# Whatever order they ran in, the set that survives must be internally
# consistent: DATA-004a exists so two runs cannot interleave their renames and
# strand a permanently mismatched pair.
kind="$(python3 -B -E -s "$REPO/helper/unifi_status.py" --nonce cfg --config-dir "$dir" \
  | python3 -B -c 'import json,sys; print(json.load(sys.stdin)["error"]["kind"])')"
if [[ $kind == "uncommitted" ]]; then
  bad "concurrent runs leave a consistent set" "the set is mismatched"
else
  ok "concurrent runs leave a consistent set (reached $kind)"
fi
# And the surviving set must be one run's WHOLE output, not a mixture: the
# committed site is one of the six that were offered, and the key beside it is
# the one that run supplied.
survivor="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1])).get("siteId",""))' "$dir/config.json")"
found=0
for candidate in "${sites[@]}"; do
  [[ "$survivor" == "$candidate" ]] && found=1
done
check "the surviving config is one of the six runs'" "1" "$found"
generation="$(python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))["commitGeneration"])' "$dir/commit.json")"
if (( generation >= 2 )); then
  ok "the generation advanced under concurrency ($generation)"
else
  bad "the generation advanced under concurrency" "$generation"
fi

# --- report -----------------------------------------------------------------
printf '\n---------------------------------------------\n'
if (( failures == 0 )); then
  printf 'configure: %d check(s) passed\n' "$passes"
  exit 0
fi
printf 'configure: %d passed, %d FAILED:\n' "$passes" "$failures"
printf '  - %s\n' "${FAILED[@]}"
exit 1
