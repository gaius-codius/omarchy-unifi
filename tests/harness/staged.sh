#!/usr/bin/env bash
# CP8 — the criteria that need the plugin INSTALLED in the running Omarchy
# shell. Everything here is behind G-STAGING (SPEC §15) and behind
# OMARCHY_UNIFI_STAGING_APPROVED=1; tests/run.sh refuses without it.
#
# What this touches, and what it puts back:
#
#   ~/.config/omarchy/plugins/gaius-codius.unifi/   installed, then REMOVED
#   ~/.config/omarchy/shell.json             edited, then RESTORED byte for byte
#   ~/.config/omarchy-unifi/                 written, then RESTORED or removed
#
# It also RESTARTS the shell, once, after staging. That is not a convenience:
# HC-19 says a staged plugin's QML source change does not take effect in a
# running shell. The reload cycle runs — services are destroyed and recreated,
# and `Local plugin changed, reloading` is logged — but the recreated instance
# is built from the previously compiled source. Without the restart every check
# below would run against whatever version happened to load first, which for a
# checkpoint is worse than not running at all. The bar disappears for a second
# and comes back on its own.
#
# All three are backed up before anything is changed and restored by an EXIT
# trap, including on failure. It refuses to start if a backup from an
# interrupted run is already there, rather than overwriting the only copy of
# the user's real configuration.
#
# G-CONTROLLER is NOT engaged. The controller under test is
# tests/tools/live_controller.py: a loopback HTTPS server with a CA it mints
# itself, serving the committed fixture corpus. No packet leaves the machine and
# no real API key exists.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

PLUGIN_ID=gaius-codius.unifi
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
SHELL_JSON="$HOME/.config/omarchy/shell.json"
UNIFI_CONF="$HOME/.config/omarchy-unifi"
BACKUP="$HOME/.config/omarchy-unifi.cp8-backup"
STATE=/tmp/unifi-live
LIVE_LOG=/tmp/unifi-live/server.log

passes=0
failures=0
ok()  { passes=$((passes + 1));  printf '  ok   %s\n' "$*"; }
bad() { failures=$((failures + 1)); printf '  FAIL %s\n' "$*" >&2; }
check() { # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 -- expected [$2] got [$3]"; fi
}
section() { printf '\n--- %s\n' "$*"; }

status_json() { omarchy shell "$PLUGIN_ID" status 2>/dev/null; }

# `pgrep -c` prints 0 AND exits 1 when nothing matches, so the count has to be
# taken without letting the status leak into the arithmetic below.
helper_count() {
  local n
  n="$(pgrep -fc unifi_status.py 2>/dev/null)" || n=0
  printf '%s' "${n:-0}"
}

# Sample the helper count every 200 ms for <seconds> and print the peak. REQ-014
# says per-monitor widgets never poll; two monitors polling would show here as a
# peak of two.
peak_helpers() {
  local deadline=$((SECONDS + $1)) peak=0 count
  while (( SECONDS < deadline )); do
    count="$(helper_count)"
    (( count > peak )) && peak=$count
    sleep 0.2
  done
  printf '%s' "$peak"
}

# Every DISTINCT helper pid seen over <seconds>, one per line. A peak of one is
# not enough on its own for AC-003: with a 30 s interval and a 150 ms batch, a
# 200 ms sampler can miss every batch and report a peak of zero, which "at most
# one" accepts. Counting distinct pids says how many helpers actually ran, so
# the assertion can require that at least one did.
helper_pids() {
  local deadline=$((SECONDS + $1))
  while (( SECONDS < deadline )); do
    pgrep -f unifi_status.py 2>/dev/null
    sleep 0.2
  done
}
status_field() { status_json | python3 -c "
import json, sys
try: print(json.load(sys.stdin)[sys.argv[1]])
except Exception: print('')
" "$1"; }

layout_set() { # layout_set <python-expression-file>
  python3 - "$SHELL_JSON" "$1" <<'PY'
import json, os, sys
path, mode = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    config = json.load(handle)
layout = config["bar"]["layout"]
for section in layout.values():
    section[:] = [e for e in section if e.get("id") != "gaius-codius.unifi"]
if mode == "bare":
    layout["right"].append({"id": "gaius-codius.unifi"})
elif mode == "interval15":
    layout["right"].append({"id": "gaius-codius.unifi", "refreshIntervalSec": 15})
elif mode == "conflict":
    layout["right"].append({"id": "gaius-codius.unifi", "refreshIntervalSec": 30})
    layout["left"].append({"id": "gaius-codius.unifi", "refreshIntervalSec": 60})
elif mode == "presentation":
    # DATA-002: duplicates differing only in a PRESENTATION setting are
    # accepted, with the left-most entry winning. `left` is scanned first, so
    # the winner is the one placed there.
    layout["left"].append({"id": "gaius-codius.unifi", "compactMetric": "clients"})
    layout["right"].append({"id": "gaius-codius.unifi", "compactMetric": "none"})
elif mode != "absent":
    raise SystemExit("unknown layout mode: " + mode)
# In-place write (truncate + fsync). FileView watches the inode; os.replace
# leaves that watch on the unlinked file, so later layout_set calls never
# reach applyShellConfig. persistShellConfig uses FileView.setText, which
# updates the cache itself — an external editor cannot.
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
  # 4.0.3 FileView may miss an external truncate+write. The host's IPC
  # reloads the user file and runs applyShellConfig (shell.qml:1607), which
  # is what pushes a new publicBarConfig into PluginShellApi.
  omarchy shell shell reloadConfig >/dev/null 2>&1 || true
}

wait_for() { # wait_for <seconds> <field> <value>
  local deadline=$((SECONDS + $1))
  while (( SECONDS < deadline )); do
    [[ "$(status_field "$2")" == "$3" ]] && return 0
    sleep 0.25
  done
  return 1
}

# --- preconditions -----------------------------------------------------------
if [[ ${OMARCHY_UNIFI_STAGING_APPROVED:-0} != 1 ]]; then
  echo "staged.sh: refuses without OMARCHY_UNIFI_STAGING_APPROVED=1 (G-STAGING)" >&2
  exit 2
fi
command -v omarchy >/dev/null || { echo "staged.sh: omarchy not on PATH" >&2; exit 2; }
omarchy shell shell ping >/dev/null 2>&1 || { echo "staged.sh: no running Omarchy shell" >&2; exit 2; }
[[ -f $SHELL_JSON ]] || { echo "staged.sh: $SHELL_JSON not found" >&2; exit 2; }
if [[ -e $BACKUP ]]; then
  echo "staged.sh: $BACKUP already exists — an earlier run did not finish." >&2
  echo "  Restore it by hand before running again; it may be your only copy." >&2
  exit 2
fi

find_shell_pid() { pgrep -f 'quickshell -n -p /usr/share/omarchy/shell' | head -1; }
SHELL_PID="$(find_shell_pid)"
[[ -n $SHELL_PID ]] || { echo "staged.sh: cannot find the shell process" >&2; exit 2; }
START_STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

restart_shell() { # HC-19; see the header
  omarchy restart shell >/dev/null 2>&1
  local deadline=$((SECONDS + 40))
  while (( SECONDS < deadline )); do
    sleep 0.5
    SHELL_PID="$(find_shell_pid)"
    [[ -n $SHELL_PID ]] || continue
    omarchy shell shell ping >/dev/null 2>&1 && return 0
  done
  return 1
}

shell_log() { # shell_log <since>
  journalctl --user "_PID=$SHELL_PID" --since "$1" -o cat --no-pager 2>/dev/null \
    | sed 's/\x1b\[[0-9;]*m//g'
}
rss_kb() { awk '/^VmRSS:/ {print $2}' "/proc/$SHELL_PID/status" 2>/dev/null; }

# --- backups, and the trap that puts everything back -------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cp8.XXXXXX")"
cp "$SHELL_JSON" "$WORK/shell.json.orig"
HAD_CONF=0
if [[ -d $UNIFI_CONF ]]; then HAD_CONF=1; cp -a "$UNIFI_CONF" "$BACKUP"; fi

restore() {
  local rc=$?
  printf '\n--- restoring\n'
  [[ -f $LIVE_PID_FILE ]] && kill "$(cat "$LIVE_PID_FILE")" 2>/dev/null
  omarchy plugin disable gaius-codius.unifi-probe >/dev/null 2>&1
  cp "$WORK/shell.json.orig" "$SHELL_JSON" && echo "  shell.json restored"
  rm -rf "$PLUGIN_DIR" "$PROBE_DIR" && echo "  plugin folders removed"
  rm -rf "$UNIFI_CONF"
  if (( HAD_CONF )); then
    cp -a "$BACKUP" "$UNIFI_CONF" && rm -rf "$BACKUP" && echo "  omarchy-unifi restored"
  else
    echo "  omarchy-unifi removed (there was none before)"
  fi
  omarchy shell shell rescanPlugins >/dev/null 2>&1
  rm -rf "$WORK"
  exit $rc
}
PROBE_DIR="$HOME/.config/omarchy/plugins/gaius-codius.unifi-probe"
LIVE_PID_FILE="$WORK/live.pid"
trap restore EXIT

# --- AC-002: the install writes nothing into /usr/share/omarchy --------------
section "AC-002 — install is confined to \$HOME"
find /usr/share/omarchy -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/tree.before" 2>/dev/null

rm -rf "$PLUGIN_DIR"; mkdir -p "$PLUGIN_DIR"
# Exactly the tracked tree, which is what a `git clone` of this repository
# gives. Copied, never symlinked: omarchy-plugin-validate:115 rejects a symlink
# anywhere inside a plugin folder (AC-027).
git ls-files -z | tar --null -T - -cf - | tar -x -C "$PLUGIN_DIR"
if omarchy plugin validate "$PLUGIN_DIR" >/dev/null 2>&1; then
  ok "omarchy plugin validate accepts the staged folder"
else
  bad "omarchy plugin validate accepts the staged folder"
fi
# HC-19: the source only takes effect across a restart.
if restart_shell; then
  ok "the shell restarted and answers IPC (pid $SHELL_PID)"
else
  bad "the shell restarted and answers IPC"; exit 1
fi

find /usr/share/omarchy -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/tree.after" 2>/dev/null
if diff -q "$WORK/tree.before" "$WORK/tree.after" >/dev/null; then
  ok "every file under /usr/share/omarchy is byte-identical ($(wc -l < "$WORK/tree.before") files)"
else
  bad "the install modified /usr/share/omarchy:"
  diff "$WORK/tree.before" "$WORK/tree.after" | head -20 >&2
fi

if omarchy plugin list --json 2>/dev/null | python3 -c "
import json, sys
print('yes' if any(p.get('id') == 'gaius-codius.unifi' for p in json.load(sys.stdin)) else 'no')
" | grep -q yes; then
  ok "the registry discovered gaius-codius.unifi"
else
  bad "the registry discovered gaius-codius.unifi"
fi

# --- the loopback controller and a committed configuration -------------------
section "a loopback fixture controller (no real controller, G-CONTROLLER not engaged)"
rm -rf "$STATE"; mkdir -p "$STATE"
python3 -B -E -s tests/tools/live_controller.py --scenario healthy --state "$STATE" \
  > "$LIVE_LOG" 2>&1 &
echo $! > "$LIVE_PID_FILE"
for _ in $(seq 1 100); do [[ -s $STATE/state.json ]] && break; sleep 0.1; done
if [[ ! -s $STATE/state.json ]]; then
  bad "the loopback controller started"; exit 1
fi
API_ROOT="$(python3 -c "import json;print(json.load(open('$STATE/state.json'))['apiRoot'])")"
CA_PATH="$(python3 -c "import json;print(json.load(open('$STATE/state.json'))['caPath'])")"
SITE_ID="$(python3 -c "import json;print(json.load(open('$STATE/state.json'))['siteId'])")"
ok "serving the healthy fixture on $API_ROOT"

rm -rf "$UNIFI_CONF"
if printf 'sk-cp8-fixture-not-a-real-key\n' | bash scripts/configure \
     --api-root "$API_ROOT" --custom-ca "$CA_PATH" --site "$SITE_ID" \
     --api-key-stdin >/dev/null 2>&1; then
  ok "scripts/configure wrote and committed a configuration"
else
  bad "scripts/configure wrote and committed a configuration"
fi

# --- AC-032: a bare layout entry takes the manifest defaults -----------------
section "AC-032 — defaults from a settings-free layout entry"
layout_set bare
# Wait on compactMetric, not hasSnapshot: the user's live shell.json may
# already have a snapshot and a non-default compactMetric, so hasSnapshot
# would return immediately against the pre-edit reading.
if ! wait_for 30 compactMetric none; then bad "compactMetric defaults to none"; fi
if ! wait_for 30 hasSnapshot True; then bad "a first snapshot arrived within 30 s"; fi
check "refreshIntervalSec defaults to 30" "30" "$(status_field refreshIntervalSec)"
check "compactMetric defaults to none" "none" "$(status_field compactMetric)"
check "the service reports a snapshot" "True" "$(status_field hasSnapshot)"
check "the health level is the fixture's" "green" "$(status_field healthLevel)"
check "the panel state is ok" "ok" "$(status_field panelState)"
# 4.0.3 deletes __sourceDir from the public manifest, so this is
# Qt.resolvedUrl on Service.qml, not manifest.__sourceDir (HC-22).
check "sourceDir is under \$HOME" "$PLUGIN_DIR" "$(status_field sourceDir)"

# --- AC-003: one service, whatever the monitor count ------------------------
section "AC-003 — one service instance across every output"
MONITORS="$(hyprctl monitors -j 2>/dev/null | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))' 2>/dev/null || echo 1)"
printf '  (%s output(s) attached)\n' "$MONITORS"
FIRST_ID="$(status_field instanceId)"
if [[ -n $FIRST_ID ]]; then ok "the service reports an instance id ($FIRST_ID)"; else bad "the service reports an instance id"; fi

# Held across a window in which the service is alive, both outputs are attached,
# and nothing rewrites shell.json.
#
# The last clause is the whole point, and an earlier version of this check did
# not have it: it captured the id here and compared it again after two
# `layout_set` calls, then reported a difference as a failure. Under HC-3 the
# service is tied to the bar ENTRY, so rewriting the layout destroys and
# rebuilds it — legitimately, and this script asserts exactly that in AC-028.
# The B4 run caught it; the CP8 run had passed it, because which of the
# briefly-overlapping IpcHandlers answers a `status` call during a rebuild is a
# race. The shell's own journal settled it: six `released wakeTimer` lines, one
# per layout write, each id replacing the last.
#
# What AC-003 is actually about is one service across every OUTPUT, and the two
# checks that carry that claim are the helper peak and the one-reload-one-helper
# count below. This one adds that the identity is stable while the service runs.
# Guarded on a NON-EMPTY id. `status_field` prints "" when the call fails, and
# "" == "" would report a service that answered nothing twelve times as stable.
if [[ -z $FIRST_ID ]]; then
  bad "the id is stable across successive status calls (no id to compare)"
else
  ID_STABLE=1
  for _ in $(seq 1 12); do
    SEEN="$(status_field instanceId)"
    [[ "$SEEN" == "$FIRST_ID" ]] || ID_STABLE=0
    sleep 0.25
  done
  if (( ID_STABLE )); then
    ok "the id is stable across 12 successive status calls ($MONITORS output(s) attached)"
  else
    bad "the id changed across successive status calls with no layout write"
  fi
fi

# Sample the helper count every 200 ms across three refresh cycles. REQ-014
# says per-monitor widgets never poll; two monitors polling would show here as
# a count of two.
# A short interval and a deliberately slow fixture, so batches certainly fall
# inside the sampling window and each one is wide enough to be sampled several
# times. Without both, a peak of zero passes "at most one" while proving
# nothing — which is what the first version of this check did.
echo 0.3 > "$STATE/delay"
layout_set interval15
wait_for 30 refreshIntervalSec 15 || bad "the shorter interval was picked up"
PID_LOG="$WORK/helper-pids"
helper_pids 45 | sort -u > "$PID_LOG"          # three 15 s cycles
DISTINCT="$(grep -c . "$PID_LOG" || true)"
if (( DISTINCT >= 1 )); then
  ok "helpers really ran during the window ($DISTINCT distinct pid(s) over three cycles)"
else
  bad "helpers really ran during the window (none observed, so the count below proves nothing)"
fi
MAX_HELPERS="$(peak_helpers 8)"
if (( MAX_HELPERS <= 1 )); then
  ok "never more than one helper at a time (peak $MAX_HELPERS, $MONITORS output(s) attached)"
else
  bad "never more than one helper at a time (peak $MAX_HELPERS)"
fi
echo 0 > "$STATE/delay"
layout_set bare
wait_for 30 refreshIntervalSec 30 || true
# Deliberately NOT compared with $FIRST_ID. Two layout rewrites have happened
# since, and each one rebuilds the service by design — see the note above.
LATER_ID="$(status_field instanceId)"
if [[ -n $LATER_ID ]]; then
  ok "the rebuilt service reports an id of its own ($LATER_ID)"
else
  bad "the rebuilt service reports an id of its own"
fi

# One reload must produce ONE batch, not one per output. Counted as distinct
# helper pids rather than as a generation, because `_reload` rebuilds the
# scheduler and its generation restarts at zero — a counter that resets cannot
# answer "how many".
# The window has to START idle. REQ-016 explicitly allows an ABANDONED helper to
# outlive its replacement (HC-8), so reloading while a batch is in flight
# legitimately shows two pids — which is a fact about the previous batch, not
# about how many the reload launched.
wait_for 40 helperRunning False || bad "the service was idle before the reload"
echo 0.5 > "$STATE/delay"
omarchy shell "$PLUGIN_ID" reload >/dev/null 2>&1
RELOAD_PIDS="$(helper_pids 6 | sort -u | grep -c . || true)"
if (( RELOAD_PIDS == 1 )); then
  ok "one reload launched exactly one helper, not one per output"
else
  bad "one reload launched exactly one helper -- saw $RELOAD_PIDS"
fi
echo 0 > "$STATE/delay"
wait_for 30 hasSnapshot True || true

# --- AC-056: reload returns inside the IPC budget with a batch in flight -----
section "AC-056 — reload never blocks on a running batch"
echo 4 > "$STATE/delay"          # 4 s per request: the batch is genuinely slow
omarchy shell "$PLUGIN_ID" reload >/dev/null 2>&1
for _ in $(seq 1 100); do [[ "$(status_field helperRunning)" == True ]] && break; sleep 0.1; done
check "a batch is in flight" "True" "$(status_field helperRunning)"
START_NS=$(date +%s%N)
RELOAD_OUT="$(omarchy shell "$PLUGIN_ID" reload 2>&1)"
ELAPSED_MS=$(( ($(date +%s%N) - START_NS) / 1000000 ))
check "reload answers" "reloading" "$RELOAD_OUT"
if (( ELAPSED_MS < 2000 )); then
  ok "reload returned in ${ELAPSED_MS}ms, inside the 2 s IPC budget"
else
  bad "reload returned in ${ELAPSED_MS}ms, outside the 2 s IPC budget"
fi
# DATA-011 in the live shell: the cached snapshot is discarded and the panel
# says why, rather than showing a reading that may belong to another controller.
check "the reload discarded the snapshot" "False" "$(status_field hasSnapshot)"
check "the panel renders reconfiguring" "reconfiguring" "$(status_field panelState)"
echo 0 > "$STATE/delay"
wait_for 45 hasSnapshot True || bad "the service recovered after the slow batch"
check "reconfiguring ended once a batch completed" "False" "$(status_field reconfiguring)"

# --- AC-019: a settings conflict suspends polling ---------------------------
section "AC-019 — conflicting duplicate entries"
layout_set conflict
if ! wait_for 20 errorKind configuration_conflict; then
  bad "the conflict is reported"
else
  ok "the conflict is reported"
fi
check "polling is suspended" "True" "$(status_field pollingSuspended)"
LAUNCH_BEFORE="$(status_field generation)"
MAX_DURING="$(peak_helpers 12)"
check "no helper launched while suspended" "0" "$MAX_DURING"
check "the generation did not advance" "$LAUNCH_BEFORE" "$(status_field generation)"

layout_set presentation
if wait_for 30 hasSnapshot True; then
  ok "duplicates differing only in presentation are accepted"
else
  bad "duplicates differing only in presentation are accepted"
fi
check "the left-most entry wins" "clients" "$(status_field compactMetric)"
layout_set bare
wait_for 30 hasSnapshot True || true

# --- AC-012b: sixty consecutive failures ------------------------------------
section "AC-012b — sixty consecutive failures, per mode"
RSS_BEFORE="$(rss_kb)"
IPC_MAX_MS=0
FAIL_STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

drive_failures() { # drive_failures <label> <api-root> <count>
  local label="$1" root="$2" want="$3" seen=0
  printf 'sk-cp8-fixture-not-a-real-key\n' | bash scripts/configure \
    --api-root "$root" --api-key-stdin --no-custom-ca >/dev/null 2>&1
  for _ in $(seq 1 "$want"); do
    local t0 t1 ms
    t0=$(date +%s%N)
    omarchy shell "$PLUGIN_ID" reload >/dev/null 2>&1
    t1=$(date +%s%N)
    ms=$(( (t1 - t0) / 1000000 ))
    (( ms > IPC_MAX_MS )) && IPC_MAX_MS=$ms
    for _ in $(seq 1 60); do
      [[ "$(status_field errorKind)" == network ]] && break
      sleep 0.1
    done
    [[ "$(status_field errorKind)" == network ]] && seen=$((seen + 1))
  done
  if (( seen >= want )); then
    ok "$label: $seen/$want batches failed as network"
  else
    bad "$label: only $seen/$want batches failed as network"
  fi
}

# Connection refused: a closed port on loopback. Instant and deterministic.
drive_failures "connection refused" "https://127.0.0.1:9/proxy/network/integration" 60
# Name resolution failure: the reserved .invalid TLD can never resolve (RFC 2606).
drive_failures "name resolution" "https://cp8.invalid/proxy/network/integration" 60

RSS_AFTER="$(rss_kb)"
GROWTH_KB=$(( RSS_AFTER - RSS_BEFORE ))
if (( GROWTH_KB < 5120 )); then
  ok "the shell's RSS grew ${GROWTH_KB} KiB over 120 failing batches (bound 5120)"
else
  bad "the shell's RSS grew ${GROWTH_KB} KiB over 120 failing batches (bound 5120)"
fi
if (( IPC_MAX_MS < 2000 )); then
  ok "every status/reload answered within ${IPC_MAX_MS}ms (bound 2000)"
else
  bad "an IPC call took ${IPC_MAX_MS}ms (bound 2000)"
fi
if kill -0 "$SHELL_PID" 2>/dev/null; then
  ok "the shell process is still alive"
else
  bad "the shell process is still alive"
fi
if shell_log "$FAIL_STAMP" | grep -qiE 'TypeError|ReferenceError|is not a function|Unable to assign'; then
  bad "the shell log carries an unhandled QML exception:"
  shell_log "$FAIL_STAMP" | grep -iE 'TypeError|ReferenceError|is not a function|Unable to assign' | head -5 >&2
else
  ok "no unhandled QML exception in the shell log"
fi

# Back to a working controller for the teardown check.
printf 'sk-cp8-fixture-not-a-real-key\n' | bash scripts/configure \
  --api-root "$API_ROOT" --custom-ca "$CA_PATH" --site "$SITE_ID" \
  --api-key-stdin >/dev/null 2>&1
wait_for 40 hasSnapshot True || bad "the service recovered after 120 failures"

# --- AC-028: teardown when the widget leaves the bar ------------------------
section "AC-028 — teardown releases every resource"
GEN_BEFORE="$(status_field generation)"
TEARDOWN_STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
layout_set absent
sleep 5

RELOAD_OUT="$(omarchy shell "$PLUGIN_ID" reload 2>&1)"
if grep -qi 'not found' <<< "$RELOAD_OUT"; then
  ok "reload reports the target is gone ($RELOAD_OUT)"
else
  bad "reload reports the target is gone -- got [$RELOAD_OUT]"
fi

# Three times the 30 s refresh interval.
MAX_AFTER="$(peak_helpers 90)"
check "no helper ran for three intervals after removal" "0" "$MAX_AFTER"

TEARDOWN_LINES="$(shell_log "$TEARDOWN_STAMP" | grep -c 'released wakeTimer, watchdog, freshnessTimer')"
if (( TEARDOWN_LINES >= 1 )); then
  ok "the shell log names the released resources ($TEARDOWN_LINES line(s))"
  shell_log "$TEARDOWN_STAMP" | grep 'released wakeTimer' | tail -1 | sed 's/^/       /'
else
  bad "the shell log names the released resources"
fi

layout_set bare
if wait_for 45 hasSnapshot True; then
  ok "re-adding the widget produces a snapshot within one interval"
else
  bad "re-adding the widget produces a snapshot within one interval"
fi
GEN_AFTER="$(status_field generation)"
if (( GEN_AFTER >= 1 )); then
  ok "the new service starts its own generation sequence (gen $GEN_AFTER)"
else
  bad "the new service starts its own generation sequence"
fi

# --- AC-026: qs.Commons and qs.Ui resolve from a staged plugin --------------
section "AC-026 — qs.* resolves for a plugin under \$HOME"
rm -rf "$PROBE_DIR"; mkdir -p "$PROBE_DIR"
cat > "$PROBE_DIR/manifest.json" <<'JSON'
{
  "schemaVersion": 1,
  "id": "gaius-codius.unifi-probe",
  "name": "UniFi HC-10 probe",
  "version": "0.0.1",
  "author": "gaius-codius",
  "description": "Confirms qs.Commons and qs.Ui resolve from a staged plugin",
  "kinds": ["service"],
  "entryPoints": { "service": "Service.qml" }
}
JSON
cat > "$PROBE_DIR/Service.qml" <<'QML'
import QtQuick
import qs.Commons
import qs.Ui

QtObject {
  Component.onCompleted: {
    var kit = Qt.createComponent("Kit.qml", Component.PreferSynchronous)
    var item = kit.status === Component.Ready ? kit.createObject(null) : null
    console.log("PROBE_OK space=" + Style.space(12)
                + " fg=" + Color.foreground
                + " kit=" + (item !== null ? "instantiated" : kit.errorString()))
    if (item !== null) item.destroy()
  }
}
QML
cat > "$PROBE_DIR/Kit.qml" <<'QML'
import QtQuick
import qs.Ui

Item {
  BarIconButton { }
  Panel { }
  KeyboardPanel { anchorItem: parent; bar: null }
}
QML
PROBE_STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
# Through the supported path. `plugins` in shell.json is a LIST of enabled ids,
# not a map, and `omarchy plugin enable` is what maintains it — hand-editing it
# would be testing a shape the shell does not use.
omarchy shell shell rescanPlugins >/dev/null 2>&1
# The registry scan is asynchronous; enabling before it lands fails with
# "plugin is not known", which the first version of this check swallowed and
# then reported as a missing PROBE_OK fifteen seconds later.
PROBE_KNOWN=0
for _ in $(seq 1 40); do
  if omarchy plugin list --json 2>/dev/null | python3 -c "
import json, sys
sys.exit(0 if any(p.get('id') == 'gaius-codius.unifi-probe' for p in json.load(sys.stdin)) else 1)
"; then PROBE_KNOWN=1; break; fi
  sleep 0.25
done
if (( PROBE_KNOWN )); then
  ok "the registry discovered the probe plugin"
else
  bad "the registry discovered the probe plugin"
fi
PROBE_ENABLE="$(omarchy plugin enable gaius-codius.unifi-probe 2>&1)"
if grep -qi 'enabled' <<< "$PROBE_ENABLE"; then
  ok "the probe plugin was enabled ($PROBE_ENABLE)"
else
  bad "the probe plugin was enabled -- got [$PROBE_ENABLE]"
fi
PROBE_DEADLINE=$((SECONDS + 15))
PROBE_LINE=""
while (( SECONDS < PROBE_DEADLINE )); do
  PROBE_LINE="$(shell_log "$PROBE_STAMP" | grep -m1 'PROBE_OK' || true)"
  [[ -n $PROBE_LINE ]] && break
  sleep 0.25
done
if [[ -n $PROBE_LINE ]]; then
  ok "a staged plugin resolved qs.Commons and qs.Ui"
  printf '       %s\n' "$PROBE_LINE"
  if grep -q 'kit=instantiated' <<< "$PROBE_LINE"; then
    ok "Style, Color, BarIconButton, Panel and KeyboardPanel all instantiated"
  else
    bad "the qs.Ui types instantiated -- $PROBE_LINE"
  fi
else
  bad "a staged plugin resolved qs.Commons and qs.Ui (no PROBE_OK in the log)"
fi
if shell_log "$PROBE_STAMP" | grep -qE 'gaius-codius\.unifi(-probe)?.*(Failed to import|is not a type)'; then
  bad "the shell log carries an import failure for a staged plugin"
else
  ok "no import failure for either staged plugin"
fi

# --- report ------------------------------------------------------------------
printf '\n---------------------------------------------\n'
printf 'staged: %d check(s) passed, %d failed\n' "$passes" "$failures"
(( failures == 0 ))
