// The singleton service. **It decides nothing.**
//
// Every branch this file would otherwise take lives in a pure module that
// `node --test` can execute: `Settings.js` resolves configuration,
// `Schedule.js` decides when to poll, `Protocol.js` decides whether an envelope
// is acceptable, `Health.js` decides the colour, `ViewModel.js` decides the
// words. What is left here is the part QML alone can do — owning a `Process`,
// arming `Timer`s, registering an `IpcHandler`, and publishing a property.
//
// HC-16 is why the composition happens here and not in a sixth module: dual-use
// `.js` files cannot import one another, so something has to call all five in
// order. `_recompute()` is that one function, and it is the only place any of
// them is invoked.
//
// Three host facts shape the rest:
//
//   DATA-003  `shell` and `manifest` are injected AFTER construction, so
//             nothing initializes in `Component.onCompleted`.
//   HC-7      `onExited` and `onStreamFinished` have no guaranteed order, so
//             every signal handler is a one-liner delegating to
//             `Protocol.joinBatch` and nothing is parsed from an exit alone.
//   HC-8      there is no kill API and `running = false` is asynchronous, so an
//             abandoned batch is discarded by GENERATION, not reaped.
//
// This file imports no `qs.*` (tests/lint/no_qs_in_service.sh). That is what
// lets it load under a bare `ShellRoot` in the test harness, with nothing
// staged into ~/.config/omarchy/plugins/.
import QtQuick
import Quickshell.Io
import "Settings.js" as Settings
import "Schedule.js" as Schedule
import "Protocol.js" as Protocol
import "Health.js" as Health
import "ViewModel.js" as ViewModel

Item {
  id: root

  // --- injected by the shell after createObject returns (DATA-003) ---------
  property QtObject shell: null
  property var manifest: null
  property string omarchyPath: ""
  property QtObject barWidgetRegistry: null
  property QtObject pluginRegistry: null

  readonly property string pluginId: "gaius-codius.unifi"

  // AC-003's observable. The criterion asks for `serviceInstanceCount == 1`,
  // which no object in this process can honestly compute: `shell._services`
  // holds at most one entry per plugin id BY CONSTRUCTION (`ensureService`
  // returns early when the key exists, shell.qml:284), and it exposes no way to
  // read that back. A counter here would count this instance, which is one
  // whatever the truth is.
  //
  // What is honestly observable is IDENTITY: a token minted once per
  // construction. If a second service were ever constructed for this plugin,
  // successive `status` calls would not agree on it, and the teardown line
  // would name a different one than the batches did.
  // `Qt.md5` is not available in this engine — it evaluated to nothing and left
  // the property empty, which the CP8 run caught by asserting the id was
  // non-blank rather than merely stable. Twelve hex digits from Math.random
  // needs no host function at all.
  readonly property string instanceId: _mintInstanceId()

  function _mintInstanceId() {
    var out = ""
    for (var i = 0; i < 12; i++) {
      out += "0123456789abcdef".charAt(Math.floor(Math.random() * 16))
    }
    return out
  }

  // REQ-017: the service's watchdog is the OUTER bound. The helper's own 25 s
  // budget is the inner, best-effort one, and REQ-017c says it cannot interrupt
  // a blocking getaddrinfo — so this number is what actually bounds a batch.
  readonly property int watchdogSec: 30

  // The single published property. Widgets bind to this and compute nothing:
  // UX-011's "every monitor shows identical state" is then a consequence of
  // there being one service, rather than of every widget agreeing.
  property var viewModel: ViewModel.forNullService()

  // --- readiness (DATA-003, REQ-023b) --------------------------------------
  //
  // One derived predicate, and REQ-023b's "immediately" anchors on its RISING
  // EDGE. Anchoring on Component.onCompleted would fire before `shell` exists
  // (risk R-I): the first poll would run against no configuration, fail, and
  // put the scheduler into a backoff it never needed.
  readonly property bool ready: shell !== null && manifest !== null

  // REQ-018a's "suspended is not idle", as one predicate. `Schedule.suspended`
  // covers a suspending FAILURE kind; a configuration conflict and a missing
  // helper arrive the other way, as `configured: false`, and the panel must
  // disable Refresh for those too — the user has nothing to refresh against.
  readonly property bool pollingSuspended: _schedule.suspended === true
    || (ready && _schedule.configured !== true)
  property bool _started: false

  onReadyChanged: if (ready && !_started) Qt.callLater(root._start)
  onShellChanged: root._onConfigurationChanged()
  onManifestChanged: root._onConfigurationChanged()

  // DATA-002b again, for the case the two handlers above do not cover: `shell`
  // is assigned once, but the settings live in `shell.shellConfig.bar.layout`,
  // which the user edits at any time — `shell.json` is the ONLY settings
  // surface Omarchy 4.0.2 has (HC-1), so an edit while the service is running
  // is the normal way to change anything.
  //
  // Without this the service reads the layout once and never again: AC-019's
  // conflict would be seen only by a service that happened to start after the
  // edit, and a corrected `refreshIntervalSec` would not take effect until the
  // widget was removed and re-added. Found by CP8, which edited `shell.json`
  // under a running service and watched nothing happen.
  Connections {
    target: root.shell
    ignoreUnknownSignals: true
    function onShellConfigChanged() { root._onConfigurationChanged() }
  }

  // --- state ---------------------------------------------------------------
  property var _schedule: Schedule.create({ configured: false })
  property var _join: Protocol.createJoin()
  property var _rebaseline: Protocol.createRebaseline()

  property string _nonce: ""
  property int _batchGeneration: 0
  property int _watchdogGeneration: -1

  // A tag for the PROCESS, separate from the scheduler's generation and never
  // reset. `_reload` rebuilds the scheduler, so `_schedule.generation` restarts
  // at zero — and a helper abandoned before the reload then carries the same
  // number as a batch launched after it. Its events were accepted into the new
  // batch's join, which completed early against a stdout nobody had sent and
  // reported `malformed_response` for a batch that was fine.
  //
  // This is DATA-005a's argument one level down: the nonce exists because a
  // counter cannot survive the service being destroyed, and the same reasoning
  // applies to a counter that is deliberately reset.
  property int _launchTag: 0
  property int _activeTag: -1

  // DATA-011 / UX-004. True from the moment a reload is accepted until the
  // first batch after it completes. It is a separate flag rather than an
  // inference from "no snapshot", because "no snapshot yet" is `loading` and
  // means something entirely different to the user: one says the first reading
  // is on its way, the other says the reading you were looking at has been
  // thrown away because it may have come from a different controller.
  property bool _reconfiguring: false

  property var _snapshot: null          // the last COMPLETE successful data object
  property var _snapshotMeta: null
  property var _warnings: []
  property string _errorKind: ""
  property var _lastError: null
  property real _launchAtWall: 0
  property var _settings: Settings.resolve(null).settings
  property var _settingsWarnings: []
  property string _helperPath: ""
  property string _helperError: ""

  // --- clocks --------------------------------------------------------------
  //
  // Two axes, because Schedule.js needs both: monotonic for deadlines, wall for
  // staleness and for REQ-024a's resume rule.
  //
  // QML has no monotonic clock and none is citable in host-contract.md (R1a),
  // so the monotonic axis is BUILT from the wall clock by accumulating only
  // non-negative deltas. That gets the property REQ-024 actually needs: a
  // backwards NTP correction can never push `nextAttemptAt` into the future and
  // stall polling for an hour.
  //
  // What it deliberately does not do is distinguish a forward NTP step from a
  // system suspend — both inflate it, and both then make the schedule due
  // early. For the suspend case that is the right answer, and REQ-024a's
  // `wallOverdue` covers it explicitly on the other axis. For an NTP step
  // forward the cost is one early poll. Stating the residual is better than a
  // timer-driven counter that would drift instead.
  QtObject {
    id: clocks
    property var state: Schedule.createClock(Date.now() / 1000)

    // The accumulation itself is `Schedule.advanceClock`, a reducer `node
    // --test` can drive. Reading the clock MUTATES it, which is unusual enough
    // to say out loud: `mono` only advances when somebody asks the time, and
    // two reads in one turn differ by nothing.
    function now() {
      state = Schedule.advanceClock(state, Date.now() / 1000)
      return state.mono
    }
    function nowWall() { return Date.now() / 1000 }
    function nowWallIso() { return new Date().toISOString().replace(/\.\d+Z$/, "Z") }
  }

  // --- the helper processes (HC-7, HC-8, REQ-016) --------------------------
  //
  // A POOL, not one object, and the reason is REQ-016's own wording: "an
  // abandoned helper process may briefly outlive its replacement". One `Process`
  // cannot express that. Worse, it would be wrong rather than merely limited —
  // the abandoned child's `onExited` arrives while the object is being used for
  // the NEXT batch, and folds into that batch's join. `Protocol.joinBatch`'s
  // `terminated` flag guards one join; it cannot guard against events from a
  // different process arriving in a fresh one.
  //
  // So each member carries the tag of the batch that launched it, and every
  // event is discarded unless the tag still matches. That is what makes HC-8's
  // "termination is advisory" survivable: nothing is reaped, and nothing an
  // abandoned process says can reach state.
  // Not `readonly`: the harness lowers it to reach the exhaustion path, which
  // otherwise needs three consecutive 30 s watchdog timeouts to arrange. The
  // service never writes it.
  property int helperPoolSize: 3
  property var _helpers: []
  property var _activeHelper: null

  Component {
    id: helperFactory
    Process {
      id: proc
      property int batchTag: -1
      running: false
      command: []

      // Every handler is a one-liner delegating to the pure join. HC-7 says
      // these three arrive in any order, and REQ-017b says a batch is complete
      // only when all of them — or the watchdog — have been seen.
      stdout: StdioCollector {
        waitForEnd: true
        onStreamFinished: root._processEvent(proc, "stdout", String(text), 0)
      }
      stderr: StdioCollector {
        waitForEnd: true
        onStreamFinished: root._processEvent(proc, "stderr", String(text), 0)
      }
      onExited: function (exitCode) {
        root._processEvent(proc, "exit", "", exitCode)
      }
    }
  }

  function _freeHelper() {
    // Only the first `helperPoolSize` members are eligible. Searching the whole
    // list and capping only CREATION means a lowered bound does not lower the
    // bound, which is both wrong and untestable.
    var eligible = Math.min(_helpers.length, helperPoolSize)
    for (var i = 0; i < eligible; i++) {
      if (!_helpers[i].running) return _helpers[i]
    }
    if (_helpers.length >= helperPoolSize) return null
    var made = helperFactory.createObject(root)
    if (made === null) return null
    var grown = _helpers.slice()
    grown.push(made)
    _helpers = grown
    return made
  }

  function _processEvent(process, kind, text, status) {
    // The tag check, at the boundary rather than at each call site.
    if (process.batchTag !== _activeTag) return
    _join_event({ event: kind, text: text, status: status })
  }

  // --- timers ---------------------------------------------------------------
  //
  // `wakeTimer` is REQ-024's one-shot: armed for the EARLIEST future deadline
  // and rearmed after every firing. A repeating poll timer cannot express a
  // schedule whose next event is sometimes a backoff deadline and sometimes a
  // staleness transition.
  Timer {
    id: wakeTimer
    repeat: false
    onTriggered: root._onWake()
  }

  // REQ-017a: armed per batch, TAGGED with that batch's generation, disarmed on
  // completion. The tailscale precedent arms once and never restarts, which
  // pushes the deadline out ahead of a hung process forever; tagging is what
  // lets this one be restarted safely.
  Timer {
    id: watchdog
    repeat: false
    interval: root.watchdogSec * 1000
    onTriggered: root._onWatchdog()
  }

  // REQ-022 requires staleness to be evaluated LIVE rather than latched: the
  // widget must go grey at `staleAt` with no batch in flight and no failure.
  // The service owns that tick so widgets can bind to a published value.
  Timer {
    id: freshnessTimer
    // AC-071 / UX-007 are satisfied here, not in Panel.qml. The relative
    // strings — "in 4m", "2h ago" — are rebuilt by _recompute(), so a tick of
    // this timer IS the recomputation the panel needs, at 5 s against a
    // required 15 s.
    //
    // A second timer inside the panel would be a second clock: one per monitor,
    // each with its own idea of "now", against REQ-014's rule that per-monitor
    // widgets hold no state. The one that already exists is in the object that
    // owns the clock, so every widget updates from the same instant.
    interval: 5000
    repeat: true
    running: root.ready
    onTriggered: root._recompute()
  }

  // --- IPC (DATA-010) -------------------------------------------------------
  IpcHandler {
    id: ipc
    target: root.pluginId

    // DATA-010a: returns IMMEDIATELY. It marks the current batch abandoned,
    // resets state, and schedules the new batch via Qt.callLater. A handler
    // that waited for the old process to exit would time out on every
    // configuration change (2 s IPC budget, 30 s watchdog) and be reported as a
    // failure by `scripts/configure` — which is AC-056.
    function reload(): string {
      root._reload()
      return "reloading"
    }

    // The observable channel for AC-003, AC-019, AC-028 and AC-032. Without it
    // those criteria depend on looking at a bar.
    function status(): string {
      return JSON.stringify(root._status())
    }
  }

  // --- lifecycle ------------------------------------------------------------

  function _start() {
    if (_started) return
    _started = true
    _resolveHelperPath()
    _onConfigurationChanged()
    // REQ-023b: the first poll is immediate, on the rising edge of `ready`.
    _tickNow(true)
  }

  function _onConfigurationChanged() {
    if (!ready) return
    // `shell` is declared QtObject, so qmllint cannot see that the real shell
    // root carries `shellConfig` (shell.qml:56). The access is correct at
    // runtime; the suppression is scoped to this line rather than lowering the
    // category, which would stop the gate catching genuine typos elsewhere.
    // qmllint disable missing-property
    var config = shell ? shell.shellConfig : null
    // qmllint enable missing-property
    var layout = config && config.bar ? config.bar.layout : null

    // DATA-002b: the service resolves its own settings by locating the
    // `gaius-codius.unifi` entries in `bar.layout` — it is not injected `settings` at
    // all. DATA-002: only a conflict in a SERVICE-CONSUMED setting suspends
    // polling; duplicates differing only in presentation are accepted, with the
    // left-most entry winning.
    var classified = Settings.classifyLayout(layout)
    _settings = classified.effective
    _settingsWarnings = classified.warnings

    var resolved = classified
    var configured = classified.conflict !== true && _helperError === ""
    var out = Schedule.setConfigured(_schedule, {
      now: clocks.now(), configured: configured
    })
    _schedule = out.state

    if (classified.conflict === true) {
      _errorKind = "configuration_conflict"
    } else if (_helperError !== "") {
      _errorKind = _helperError
    } else if (_errorKind === "configuration_conflict" || _errorKind === "helper_unavailable") {
      _errorKind = ""
    }

    var changed = Schedule.changeInterval(_schedule, {
      now: clocks.now(), intervalSec: classified.effective.refreshIntervalSec
    })
    _schedule = changed.state
    if (changed.dueNow) Qt.callLater(root._tickNow)
    _rearm()
    _recompute()
  }

  // DATA-003c. `manifest.__sourceDir` is stamped by the plugin scanner;
  // `Qt.resolvedUrl` on this file is the fallback, percent-decoded so a path
  // containing a space or a `%` still resolves. Neither yielding a path is
  // `helper_unavailable` — an explicit, actionable state rather than a batch
  // that fails on every poll with a confusing kind.
  function _resolveHelperPath() {
    var base = ""
    if (manifest && typeof manifest.__sourceDir === "string" && manifest.__sourceDir !== "") {
      base = String(manifest.__sourceDir)
    } else {
      base = _directoryOfThisFile()
    }
    if (base === "") {
      _helperPath = ""
      _helperError = "helper_unavailable"
      return
    }
    _helperPath = base.replace(/\/+$/, "") + "/helper/unifi_status.py"
    _helperError = ""
  }

  function _directoryOfThisFile() {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") !== 0) return ""
    var path = url.slice("file://".length)
    try {
      path = decodeURIComponent(path)
    } catch (e) {
      // A malformed percent escape is not a reason to launch nothing at all;
      // the undecoded path is still more likely to work than "".
    }
    return path.replace(/\/+$/, "")
  }

  // --- the poll cycle -------------------------------------------------------

  function _tickNow(immediate) {
    if (!ready) return
    var now = clocks.now()
    var out = Schedule.tick(_schedule, {
      now: now,
      nowWall: clocks.nowWall(),
      overdue: immediate === true || Schedule.wallOverdue(_schedule, clocks.nowWall())
    })
    _schedule = out.state
    if (out.launched) {
      _launch()
    } else {
      _rearm()
      // DATA-011: `reconfiguring` is a PROMISE that a new reading is on its
      // way. A tick that launched nothing against a scheduler that is not in a
      // batch means none is coming — the new configuration is faulty and
      // polling is suspended — so the promise has to give way to the fault's
      // own sentence. Without this, a reload into `unconfigured`,
      // `uncommitted`, `configuration_conflict` or `site_unselected` replaces
      // four messages that each name the fix with "Applying the new
      // configuration", permanently.
      if (_schedule.state !== "active-batch") _reconfiguring = false
    }
    _recompute()
  }

  function _onWake() {
    if (!ready) return
    // REQ-024: rearmed after EVERY firing, whatever the reason was. A wake that
    // only rearmed when it launched something would stop the clock the first
    // time it fired for a staleness transition.
    _tickNow(false)
  }

  function _launch() {
    // The batch the scheduler has ALREADY begun. `beginBatch` bumps the
    // generation before this function is reached, so both failure paths below
    // must report against that number — `_batchGeneration` is still the
    // PREVIOUS batch's, and `Schedule.onFailure` discards anything whose
    // generation does not match. Reporting against the stale one leaves the
    // scheduler in `active-batch` for a batch that never launched, and it never
    // polls again. Same shape as the watchdog ordering below.
    var beginning = _schedule.generation

    if (_helperPath === "") {
      _warnings = []
      _finishWithKind("helper_unavailable", null, false, beginning)
      return
    }
    var process = _freeHelper()
    if (process === null) {
      // Every pool member is still occupied, which means every recent batch is
      // still hung — HC-8 gives no way to reap them. `timeout` rather than
      // `internal`: this is the controller or the helper not responding, and
      // `internal` would send the user to a bug report instead of to their
      // controller. It also keeps the panel's state stable across the
      // transition from "this batch timed out" to "so did the last three".
      _warnings = []
      _finishWithKind("timeout", null, false, beginning)
      return
    }

    _batchGeneration = _schedule.generation
    _nonce = _freshNonce()
    _join = Protocol.createJoin()
    _launchAtWall = clocks.nowWall()
    _activeHelper = process
    _launchTag = _launchTag + 1
    _activeTag = _launchTag
    process.batchTag = _activeTag

    // -B (HC-14): a `__pycache__` write inside a staged plugin folder is seen
    // by the registry as a change and hot-reloads the plugin mid-poll.
    // -E and -s: the environment and the user site directory must not steer a
    // process that reads an API key. NOT -I, which would also drop the script's
    // own directory from sys.path on 3.11+ (HC-15).
    process.command = ["python3", "-B", "-E", "-s", _helperPath, "--nonce", _nonce]
    process.running = true

    _watchdogGeneration = _batchGeneration
    watchdog.restart()
  }

  function _freshNonce() {
    // DATA-005a: a fresh random token per batch, in argv, carrying no secret
    // material. It is what makes generation checking survive HC-8's
    // asynchronous termination AND a plugin hot-reload, which destroys the
    // service and resets any in-memory counter.
    var out = ""
    for (var i = 0; i < 4; i++) {
      out += ("0000" + Math.floor(Math.random() * 0x10000).toString(16)).slice(-4)
    }
    return out
  }

  // --- the join (HC-7 / REQ-017b) -------------------------------------------

  function _join_event(event) {
    var out = Protocol.joinBatch(_join, event)
    _join = out.state
    if (out.terminal) _complete(out.reason)
  }

  function _onWatchdog() {
    if (_watchdogGeneration !== _batchGeneration) return
    // REQ-017a: mark abandoned, publish `timeout` immediately, do NOT wait for
    // the process to exit. Its later signals arrive against a generation that
    // has moved on and are discarded by `_complete`'s guard.
    _join_event({ event: "watchdog" })
  }

  function _complete(reason) {
    watchdog.stop()
    _watchdogGeneration = -1

    if (reason === "watchdog") {
      // Two things happen here and their ORDER is the whole point.
      //
      // The process is orphaned by moving the generation on — HC-8 gives no way
      // to reap it, so that is what makes its later output harmless. But the
      // scheduler must still be told that THIS batch failed, and
      // `Schedule.onFailure` discards anything whose generation no longer
      // matches. Clearing first and reporting afterwards makes the watchdog
      // discard its own failure: the scheduler stays in `active-batch` forever
      // and never polls again. So the generation that failed is passed
      // explicitly, and the clearing happens before anything can launch.
      var abandoned = _batchGeneration
      _batchGeneration = -1
      _activeTag = -1
      _warnings = []
      _finishWithKind("timeout", null, false, abandoned)
      return
    }

    // DATA-003c / SPEC §11. The helper guarantees exactly one JSON object on
    // stdout on EVERY exit path — that is `unifi_status.py`'s one rule, and it
    // is asserted directly. So no stdout at all, paired with a non-zero exit,
    // means it never got to run: a missing interpreter, a helper path that
    // resolved to nothing readable, or a permissions problem. All three are
    // `helper_unavailable`, which names something the user can fix.
    //
    // Reporting `malformed_response` here — which is what parsing an empty
    // string produces — would tell a user with no python3 installed that their
    // controller sent a bad response.
    if (String(_join.stdout).trim() === "" && _join.exitStatus !== 0) {
      _lastError = null
      _warnings = []
      _finishWithKind("helper_unavailable", null)
      return
    }

    var stderrCheck = Protocol.stderrVerdict(_join.stderr)
    var result = Protocol.acceptStdout({
      stdout: _join.stdout,
      batch: {
        nonce: _nonce,
        exitStatus: _join.exitStatus,
        launchAt: _launchAtIso(),
        receiptAt: clocks.nowWallIso()
      }
    })

    // DATA-008a: a rejection caused ONLY by `attemptedAt` preceding the
    // recorded launch time re-baselines once and retries. A laptop resuming
    // with a 40-minute-slow RTC would otherwise reject every batch permanently
    // until the shell restarted.
    var rebase = Protocol.considerRebaseline(_rebaseline, result, clocks.nowWallIso())
    _rebaseline = rebase.state
    if (rebase.rebaselined) {
      // The batch has to END in the scheduler before it can be retried.
      // `Schedule.tick` refuses while the state is `active-batch`, so a version
      // that only re-baselined and called the retry left the service wedged
      // forever — and DATA-008a exists precisely for the laptop whose RTC is
      // wrong on the FIRST batch after a resume, which would then never poll
      // again. The failure is recorded without publishing an error kind,
      // because the retry is about to happen and a one-frame flash of
      // `malformed_response` would be a lie about the state.
      _launchAtWall = clocks.nowWall()
      var ended = Schedule.onFailure(_schedule, {
        now: clocks.now(), nowWall: clocks.nowWall(),
        generation: _batchGeneration,
        kind: Protocol.REJECTION_KIND, httpStatus: null, retryAfterSec: null
      })
      _schedule = ended.state
      Qt.callLater(root._retryAfterRebaseline)
      return
    }

    if (!result.accepted) {
      // AC-046: every protocol rejection is published as the named kind
      // `malformed_response` with a visible failure state. Never silently
      // leaving the previous snapshot on screen without an indication, and
      // never throwing.
      _lastError = {
        kind: Protocol.REJECTION_KIND,
        message: "The helper's response could not be understood.",
        retryable: true,
        reasons: result.reasons
      }
      // Nothing was parsed, so nothing is known. Carrying the PREVIOUS batch's
      // warnings alongside this failure would show a per-batch fact —
      // `page_reread_mismatch`, `retry_after_clamped` — as though it belonged
      // to the batch that just failed. UX-009's insecure-TLS row does not
      // depend on this: DATA-006a puts `allowInsecureTls` in `meta`, on both
      // envelope shapes, precisely so a persistent warning does not ride on a
      // warning.
      _warnings = []
      _finishWithKind(Protocol.REJECTION_KIND, null)
      return
    }

    var envelope = result.envelope
    if (stderrCheck.overBound) {
      // DATA-005b: past its own 4 KiB bound the helper is not behaving as
      // specified, and the likely content is a traceback — which is exactly
      // where a request header carrying a credential would appear. The batch is
      // `internal` even when stdout carried a perfectly good envelope.
      // The envelope's own warnings are KEPT: UX-009's insecure-TLS row is
      // persistent for the whole session, and dropping it because the helper
      // was noisy would hide a security state for a diagnostic reason.
      _warnings = (envelope.warnings || []).concat([stderrCheck.warning])
      _snapshotMeta = envelope.meta
      _finishWithKind(stderrCheck.kind, envelope)
      return
    }

    if (envelope.ok === true) {
      _snapshot = envelope.data
      _snapshotMeta = envelope.meta
      _warnings = envelope.warnings
      _errorKind = ""
      _lastError = null
      var success = Schedule.onSuccess(_schedule, {
        now: clocks.now(), nowWall: clocks.nowWall(),
        generation: _batchGeneration
      })
      _schedule = success.state
      _afterBatch()
      return
    }

    // BIZ-005 / REQ-021: an unsuccessful batch does NOT replace the snapshot.
    _snapshotMeta = envelope.meta
    _warnings = envelope.warnings
    _lastError = envelope.error
    _finishWithKind(envelope.error ? envelope.error.kind : "internal", envelope)
  }

  function _launchAtIso() {
    return new Date(_launchAtWall * 1000).toISOString().replace(/\.\d+Z$/, "Z")
  }

  function _retryAfterRebaseline() {
    if (!ready) return
    _tickNow(true)
  }

  function _finishWithKind(kind, envelope, quiet, generation) {
    if (kind !== "") {
      _errorKind = kind
      var failure = Schedule.onFailure(_schedule, {
        now: clocks.now(), nowWall: clocks.nowWall(),
        generation: generation === undefined ? _batchGeneration : generation,
        kind: kind,
        httpStatus: _lastError && typeof _lastError.httpStatus === "number"
          ? _lastError.httpStatus : null,
        retryAfterSec: _lastError && typeof _lastError.retryAfterSec === "number"
          ? _lastError.retryAfterSec : null
      })
      _schedule = failure.state
      if (failure.warnings && failure.warnings.length > 0) {
        _warnings = _warnings.concat(failure.warnings)
      }
    }
    if (quiet === true) return
    _afterBatch()
  }

  function _afterBatch() {
    // DATA-011: `reconfiguring` ends when the FIRST batch after the reload
    // completes, whatever its outcome. Ending it on success alone would leave a
    // controller that is now unreachable reading "applying the new
    // configuration" forever, which is the one thing it is not doing.
    _reconfiguring = false

    // REQ-018: a manual refresh coalesced during the batch starts now.
    var pending = Schedule.takePendingManual(_schedule, {
      now: clocks.now(), nowWall: clocks.nowWall()
    })
    _schedule = pending.state
    if (pending.launched) {
      _launch()
      _recompute()
      return
    }
    _rearm()
    _recompute()
  }

  // --- REQ-024: one-shot wake, rearmed after every firing -------------------

  function _rearm() {
    var armed = Schedule.wake(_schedule, {
      now: clocks.now(), nowWall: clocks.nowWall()
    })
    wakeTimer.stop()
    if (!armed.armed) return
    // A deadline already in the past arms at zero rather than being skipped:
    // REQ-024 excludes PAST deadlines from selection, not overdue work from
    // being done.
    wakeTimer.interval = Math.max(0, Math.round(armed.delaySec * 1000))
    wakeTimer.start()
  }

  // --- the composition site (HC-16) -----------------------------------------
  //
  // The one function that calls the pure layer. Five modules, in order, ending
  // in a single published property. Every module is instantiated here and
  // nowhere else in this file.
  function _recompute() {
    var nowWall = clocks.nowWall()
    var stale = Schedule.isStale(_schedule, nowWall)
    var level = Health.healthLevel({
      snapshot: _snapshot,
      errorKind: _errorKind === "" ? null : _errorKind,
      isStale: stale
    })
    viewModel = ViewModel.build({
      reconfiguring: _reconfiguring,
      snapshot: _snapshot,
      meta: _snapshotMeta,
      level: level,
      errorKind: _errorKind === "" ? null : _errorKind,
      error: _lastError,
      warnings: _warnings,
      isStale: stale,
      settings: _settings,
      pollingSuspended: pollingSuspended,
      nextAttemptAt: _schedule.nextAttemptAt,
      // `lastSuccessAt`, not `lastSuccessAtWall`. The scheduler names its two
      // wall-clock fields inconsistently — `lastCompletionAtWall` carries the
      // suffix and `lastSuccessAt` does not — and reading the suffixed name
      // here returned `undefined`, which `relativePast` renders as "never". A
      // panel showing live device counts under the words "never updated" is
      // the failure mode; JS gives no error for the wrong property name, so
      // the guard is the CP-live assertion in runner.qml, not this comment.
      lastSuccessAt: _schedule.lastSuccessAt,
      staleAt: Schedule.staleAt(_schedule),
      now: clocks.now(),
      nowWall: nowWall
    })
  }

  // --- IPC bodies -----------------------------------------------------------

  function _reload() {
    // DATA-010a. Everything here is O(1) and nothing waits: the batch is
    // abandoned by generation, and the new one is scheduled for the next event
    // loop turn so this returns inside the 2 s IPC budget.
    _batchGeneration = -1
    _activeTag = -1
    _watchdogGeneration = -1
    watchdog.stop()
    _join = Protocol.createJoin()
    _rebaseline = Protocol.createRebaseline()
    _schedule = Schedule.create({
      configured: false, intervalSec: _settings.refreshIntervalSec
    })

    // DATA-011: a reload clears the CACHED SNAPSHOT as well as the retry state.
    // The new configuration may point at a different controller entirely, and
    // continuing to display the old site's device counts under the new site's
    // name would be a lie the user has no way to detect. UX-004 names this as
    // the one case where a widget with a snapshot is allowed to blank.
    _snapshot = null
    _snapshotMeta = null
    _warnings = []
    _errorKind = ""
    _lastError = null
    _reconfiguring = true
    _recompute()

    Qt.callLater(root._afterReload)
  }

  function _afterReload() {
    if (!ready) return
    _resolveHelperPath()
    _onConfigurationChanged()
    _tickNow(true)
  }

  function _status() {
    return {
      pluginId: pluginId,
      instanceId: instanceId,
      // AC-002's observable. `omarchy plugin list --json` was expected to carry
      // a `sourceDir`; in 4.0.2-1 it does not (its keys are id, name, kinds,
      // enabled, active, canDisable, firstParty, clonedFrom). Reporting it from
      // the RUNNING service is a stronger answer anyway: it is where this
      // instance was actually loaded from, not where the registry believes the
      // plugin lives. `PluginRegistry.qml:564` stamps it onto the manifest.
      //
      // qmllint disable missing-property
      sourceDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : null,
      // qmllint enable missing-property
      ready: ready,
      refreshIntervalSec: _settings.refreshIntervalSec,
      compactMetric: _settings.compactMetric,
      pollingSuspended: pollingSuspended,
      schedulerState: _schedule.state,
      generation: _schedule.generation,
      batchGeneration: _batchGeneration,
      activeTag: _activeTag,
      failures: _schedule.failures,
      nextAttemptAt: _schedule.nextAttemptAt,
      staleAt: Schedule.staleAt(_schedule),
      isStale: Schedule.isStale(_schedule, clocks.nowWall()),
      errorKind: _errorKind === "" ? null : _errorKind,
      hasSnapshot: _snapshot !== null,
      reconfiguring: _reconfiguring,
      healthLevel: viewModel ? viewModel.healthLevel : null,
      panelState: viewModel ? viewModel.state : null,
      warnings: _warnings.map(function (w) { return w.code }),
      helperRunning: _activeHelper !== null && _activeHelper.running,
      watchdogRunning: watchdog.running,
      settingsWarnings: _settingsWarnings.map(function (w) { return w.code })
    }
  }

  // The public refresh entry, used by the panel's Refresh button (REQ-011).
  function requestRefresh() {
    if (!ready) return "not-ready"
    var out = Schedule.requestManual(_schedule, {
      now: clocks.now(), nowWall: clocks.nowWall()
    })
    _schedule = out.state
    if (out.launched) _launch()
    else _rearm()
    _recompute()
    return out.reason
  }

  // --- teardown (DATA-003b / AC-028) ----------------------------------------
  //
  // Per HC-3 the service is destroyed when the widget leaves the bar. Each
  // released resource is NAMED, so teardown is observable rather than assumed —
  // a leaked Process would otherwise keep polling a controller for a widget
  // nobody can see.
  Component.onDestruction: {
    wakeTimer.stop()
    watchdog.stop()
    freshnessTimer.stop()
    _batchGeneration = -1
    var live = 0
    _activeTag = -1
    for (var i = 0; i < _helpers.length; i++) {
      if (_helpers[i].running) live++
      _helpers[i].batchTag = -1
    }
    console.log("gaius-codius.unifi[" + instanceId + "]: released wakeTimer, watchdog, freshnessTimer, "
                + "helper Process pool (" + _helpers.length + " members, "
                + live + " still running), IpcHandler " + ipc.target)
  }
}
