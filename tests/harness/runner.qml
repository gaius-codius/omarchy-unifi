// The live-harness. `quickshell -p tests/harness/runner.qml`, no staging.
//
// HC-11 rules out qmltestrunner: `Quickshell.Io` is linked statically into the
// binary and has no loadable plugin, so any TestCase importing `Process`,
// `StdioCollector` or `IpcHandler` fails to compile. This runs under a real
// quickshell instead, which needs a live Wayland session and installs nothing.
//
// It does four things no other layer can:
//
//   1. Instantiates the REAL `Service.qml` with the REAL `Process`, so the
//      HC-7 join, the HC-8 abandonment and the generation-tagged watchdog are
//      exercised against real signals with real timing.
//   2. Supplies the injected host surface — a stub `shell` carrying
//      `shellConfig.bar.layout` and a stub `manifest` carrying `__sourceDir` —
//      ASSIGNED AFTER construction, which is the only way DATA-003's deferred
//      initialization gets tested at all.
//   3. Re-runs the protocol corpus through `Protocol.js` under **V4**, against
//      the verdicts `node --test` recorded under V8. That is risk R-B: two
//      engines, one file, and no reason to assume they agree.
//   4. Replays the completion join in every signal order, which a real process
//      cannot be made to produce on demand.
import QtQuick
import Quickshell
import Quickshell.Io
// Resolved against the harness ROOT, where run_harness.sh symlinks them —
// not against this file's location in the repository. See the note on
// `repoRoot` below for why the root exists at all.
import "Protocol.js" as Protocol
import "ViewModel.js" as ViewModel
import "Schedule.js" as Schedule

ShellRoot {
  id: harness

  // Read from harness.json beside this file, written by run_harness.sh.
  //
  // The runner is COPIED into a root under /tmp that symlinks the repository's
  // dual-use modules and Service.qml. Quickshell resolves a relative `.js`
  // import against the CONFIG ROOT — the directory of the file passed to `-p` —
  // and replaces anything outside it with `qrc:/qs-blackhole`, which fails to
  // load. That is why the root exists rather than pointing `-p` straight at
  // tests/harness/runner.qml. It lives outside the repository because
  // omarchy-plugin-validate:115 rejects any symlink inside a plugin folder
  // (AC-027), which is the same reason HC-17's UI root is outside it.
  property string repoRoot: ""
  property string stubRoot: ""

  property int passes: 0
  property int failures: 0
  property var pending: []
  property var service: null
  property var currentCase: null

  function ok(name) { passes++; console.log("HARNESS: ok   " + name) }
  function bad(name, detail) {
    failures++
    console.log("HARNESS: FAIL " + name + (detail === undefined ? "" : " -- " + detail))
  }
  function check(name, expected, actual) {
    if (expected === actual) ok(name)
    else bad(name, "expected [" + expected + "] got [" + actual + "]")
  }
  function between(name, low, high, actual) {
    if (actual >= low && actual <= high) ok(name + " (" + actual + ")")
    else bad(name, "expected " + low + ".." + high + ", got " + actual)
  }

  // --- file access ---------------------------------------------------------
  // XMLHttpRequest on a file:// URL. Not a host API — it is QtQml's, so R1a's
  // citation rule does not apply, and it avoids pulling `FileView` into a test
  // that is not about FileView.
  function readFile(path) {
    var request = new XMLHttpRequest()
    request.open("GET", "file://" + path, false)
    request.send(null)
    return request.responseText
  }
  function readJson(path) {
    try { return JSON.parse(readFile(path)) } catch (e) { return null }
  }
  // Written by a Process rather than by QML, which has no file writer that is
  // not `FileView` — and pulling FileView into a test that is not about
  // FileView would couple them for no reason.
  //
  // Every write is followed by a WAIT for the writer to exit. Without it the
  // helper can start before the file it reads has been replaced, and the case
  // silently runs the PREVIOUS scenario — passing or failing for reasons that
  // have nothing to do with what it is testing.
  function writeScenario(body) {
    if (scenarioWriter.running) {
      bad("the scenario writer was still busy", "a scenario write was dropped")
      return
    }
    scenarioWriter.command = ["python3", "-B", "-E", "-s", "-c",
      "import sys; open(sys.argv[1], 'w').write(sys.argv[2])",
      stubRoot + "/helper/scenario.json", JSON.stringify(body)]
    scenarioWriter.running = true
  }

  Process { id: scenarioWriter; running: false; command: [] }

  Timer {
    id: writerPoll
    interval: 20
    repeat: true
    running: false
    property var callback: null
    onTriggered: {
      if (scenarioWriter.running) return
      running = false
      var next = callback
      callback = null
      if (next) next()
    }
  }

  function afterWriter(callback) {
    if (!scenarioWriter.running) { callback(); return }
    writerPoll.callback = callback
    writerPoll.running = true
  }

  // --- the service under test ----------------------------------------------
  //
  // Created by Qt.createComponent from an absolute path, exactly as
  // `ensureService` does (shell.qml:283-321), and with the same injection
  // ORDER: construct first, assign afterwards.
  function createService() {
    // Relative, so it resolves through the harness root's symlink and its own
    // `.js` imports resolve alongside it.
    var component = Qt.createComponent("Service.qml", Component.PreferSynchronous)
    if (component.status !== Component.Ready) {
      bad("Service.qml loads", component.errorString())
      return null
    }
    var instance = component.createObject(harness)
    if (instance === null) {
      bad("Service.qml instantiates", "createObject returned null")
      return null
    }
    return instance
  }

  // Every service case that does not deliberately start from a failed state
  // calls this first. The suite runs one service through many scenarios, and a
  // case that inherited the previous one's suspension would fail for a reason
  // that has nothing to do with what it asserts — which is how the first run of
  // this harness produced five failures with one cause.
  function resetService(intervalSec) {
    service._reload()
    injectHost(service, intervalSec === undefined ? 30 : intervalSec)
  }

  function injectHost(instance, intervalSec, duplicate) {
    var layout = { left: [], center: [], right: [] }
    var entry = { id: "gaius-codius.unifi" }
    if (intervalSec !== undefined && intervalSec !== null) {
      entry.refreshIntervalSec = intervalSec
    }
    layout.right.push(entry)
    if (duplicate !== undefined && duplicate !== null) {
      layout.left.push({ id: "gaius-codius.unifi", refreshIntervalSec: duplicate })
    }
    instance.manifest = { id: "gaius-codius.unifi", __sourceDir: stubRoot }
    instance.shell = stubShell.withLayout(layout)
  }

  QtObject {
    id: stubShell
    function withLayout(layout) {
      return shellFactory.createObject(harness, { shellConfig: { bar: { layout: layout } } })
    }
  }
  Component {
    id: shellFactory
    QtObject { property var shellConfig: null }
  }

  // --- the scenario driver --------------------------------------------------
  //
  // Each case is { name, setup, waitMs, assert }. Sequential rather than
  // parallel because they share one service and one stub, and because the
  // timing assertions are the point of several of them.
  Timer {
    id: driver
    repeat: false
    onTriggered: harness.runAssert()
  }

  function runNext() {
    if (pending.length === 0) { finish(); return }
    currentCase = pending.shift()
    console.log("HARNESS: -- " + currentCase.name)
    if (currentCase.prepare) currentCase.prepare()
    afterWriter(harness.runAct)
  }

  function runAct() {
    currentCase.startedAt = Date.now()
    if (currentCase.setup) currentCase.setup()
    driver.interval = currentCase.waitMs === undefined ? 0 : currentCase.waitMs
    driver.start()
  }

  function runAssert() {
    try {
      currentCase.assert(Date.now() - currentCase.startedAt)
    } catch (e) {
      bad(currentCase.name, "threw: " + e)
    }
    if (currentCase.teardown) currentCase.teardown()
    Qt.callLater(harness.runNext)
  }

  function finish() {
    console.log("HARNESS RESULT: " + (failures === 0 ? "PASS" : "FAIL")
                + " " + passes + " passed, " + failures + " failed")
    exitTimer.running = true
  }

  // Qt.exit() warns under a bare ShellRoot (SPEC §13), so the runner arranges
  // its own exit path.
  Timer { id: exitTimer; interval: 80; onTriggered: Qt.callLater(Qt.quit) }

  Component.onCompleted: Qt.callLater(harness.begin)

  function begin() {
    var here = String(Qt.resolvedUrl(".")).replace("file://", "").replace(/\/+$/, "")
    var config = readJson(here + "/harness.json")
    if (config === null) {
      bad("harness.json is readable", here + "/harness.json")
      finish()
      return
    }
    repoRoot = config.repoRoot
    stubRoot = config.stubRoot
    console.log("HARNESS: repo=" + repoRoot + " stub=" + stubRoot)
    pureCases()
    serviceCases()
    runNext()
  }

  // =========================================================================
  // Pure-layer cases: the same code node --test runs, executed by V4
  // =========================================================================

  function pureCases() {
    pending.push({
      name: "R-B: the protocol corpus yields identical verdicts under V4",
      waitMs: 0,
      assert: function () {
        // The batch parameters are `tests/model/protocol.test.js`'s, exactly.
        // A different launch/receipt window would make this a different
        // question than the one V8 answered, and "the two engines agree" would
        // mean nothing.
        var index = readJson(repoRoot + "/tests/fixtures/index.json")
        if (index === null) { bad("the fixture index loads"); return }
        var checked = 0
        var wrong = []

        for (var key in index.cases) {
          if (key.indexOf("envelopes/") !== 0) continue
          var entry = index.cases[key]
          var fixture = readJson(repoRoot + "/tests/fixtures/" + key + ".json")
          if (fixture === null) { wrong.push(key + " (unreadable)"); continue }

          var result = null
          if (entry.verdict === "accept") {
            result = Protocol.acceptEnvelope({
              envelope: fixture, batch: batchFor(fixture)
            })
          } else if (fixture.synthesize) {
            // The oversized case is synthesized rather than committed, so a
            // 256 KiB file does not ship inside a plugin folder.
            var donor = readJson(repoRoot
              + "/tests/fixtures/envelopes/accept/success_healthy.json")
            result = Protocol.acceptStdout({
              stdout: pad(JSON.stringify(donor), fixture.synthesize.bytes),
              batch: fixture.batch
            })
          } else if (fixture.stdout !== null && fixture.stdout !== undefined) {
            result = Protocol.acceptStdout({
              stdout: fixture.stdout, batch: fixture.batch
            })
          } else {
            result = Protocol.acceptEnvelope({
              envelope: fixture.envelope, batch: fixture.batch
            })
          }

          checked++
          var expected = entry.verdict === "accept"
          if (result.accepted !== expected) {
            wrong.push(key + ": expected " + entry.verdict)
          } else if (!expected && entry.rejectionClass) {
            var classes = result.reasons.map(function (r) { return r.rejectionClass })
            if (classes.indexOf(entry.rejectionClass) === -1) {
              wrong.push(key + ": expected class " + entry.rejectionClass
                         + " got " + classes.join(","))
            }
          }
        }

        check("the whole envelope corpus was driven", 77, checked)
        if (wrong.length === 0) {
          ok("V4 agrees with V8 on all " + checked + " envelopes")
        } else {
          bad("V4 agrees with V8", wrong.slice(0, 4).join("; "))
        }
      }
    })

    pending.push({
      name: "AC-050: the completion join, in every signal order, under V4",
      waitMs: 0,
      assert: function () {
        var events = ["exit", "stdout", "stderr"]
        var orders = permutations(events)
        check("there are 3! orderings of the three stream signals", 6, orders.length)

        // AC-050 says "all 4! orderings". The watchdog is the fourth event and
        // it is a timer rather than a process signal, so it can land anywhere
        // in the sequence. Every one of the 24 must produce exactly ONE
        // terminal: two would publish twice for one launch (risk R-E), none
        // would leave the service waiting forever.
        var full = permutations(["exit", "stdout", "stderr", "watchdog"])
        check("there are 4! orderings of all four events", 24, full.length)
        var offBy = 0
        for (var f = 0; f < full.length; f++) {
          var fs = Protocol.createJoin()
          var terminals = 0
          for (var g = 0; g < full[f].length; g++) {
            var step = full[f][g] === "watchdog"
              ? { event: "watchdog" } : eventFor(full[f][g])
            var res = Protocol.joinBatch(fs, step)
            fs = res.state
            if (res.terminal) terminals++
          }
          if (terminals !== 1) offBy++
        }
        check("every 4! ordering terminates exactly once", 0, offBy)
        var terminals = []
        for (var i = 0; i < orders.length; i++) {
          var state = Protocol.createJoin()
          var count = 0
          for (var j = 0; j < orders[i].length; j++) {
            var out = Protocol.joinBatch(state, eventFor(orders[i][j]))
            state = out.state
            if (out.terminal) { count++; terminals.push(j) }
          }
          if (count !== 1) {
            bad("ordering " + orders[i].join(",") + " terminates exactly once",
                "terminal count " + count)
            return
          }
        }
        ok("every ordering terminates exactly once, on the third signal")
        for (var k = 0; k < terminals.length; k++) {
          if (terminals[k] !== 2) { bad("the join waits for all three"); return }
        }
        ok("the join never terminates on an exit alone (HC-7)")

        // The watchdog is the fourth event. REQ-017a says it does NOT wait for
        // the process to exit, so it terminates from any INCOMPLETE point — but
        // once all three streams have landed the batch is already terminal, and
        // a second terminal would publish twice for one launch (risk R-E).
        var withWatchdog = 0
        for (var w = 0; w <= 3; w++) {
          var st = Protocol.createJoin()
          var order = ["exit", "stdout", "stderr"]
          for (var x = 0; x < w; x++) st = Protocol.joinBatch(st, eventFor(order[x])).state
          var fired = Protocol.joinBatch(st, { event: "watchdog" })
          if (w < 3 && fired.terminal) withWatchdog++
          if (w === 3 && fired.terminal) {
            bad("a watchdog after a complete join produces no second terminal")
            return
          }
          var late = Protocol.joinBatch(fired.state, eventFor("exit"))
          if (late.terminal) { bad("a late signal after the watchdog is discarded"); return }
        }
        check("the watchdog terminates from every incomplete point", 3, withWatchdog)
        ok("a watchdog after a complete join produces no second terminal (R-E)")
        ok("late signals after the watchdog produce no second terminal (HC-8)")
      }
    })

    pending.push({
      name: "the published model has the same shape whether or not a service exists",
      waitMs: 0,
      assert: function () {
        var empty = ViewModel.forNullService()
        var built = ViewModel.build({
          snapshot: null, level: { level: "grey", rule: 1 }, errorKind: "network",
          settings: { compactMetric: "clients", refreshIntervalSec: 30 },
          warnings: [], nowWall: 1767225600
        })
        var missing = []
        for (var key in empty) {
          if (!(key in built)) missing.push(key)
        }
        check("no key of the null model is missing from a built one", 0, missing.length)
        check("a null service renders REQ-013b", "service_unavailable", empty.state)
        check("a failure with no snapshot renders its kind", "network", built.state)
      }
    })

    pending.push({
      name: "the reload handler delegates and does not wait",
      waitMs: 0,
      assert: function () {
        // AC-056's structural half. The timing half is measured below with a
        // real batch in flight; this is what keeps the handler a delegation, so
        // no future edit can put a wait inside it.
        var source = readFile(repoRoot + "/Service.qml")
        var body = /function reload\(\): string \{([\s\S]*?)\n    \}/.exec(source)
        if (body === null) { bad("the reload handler is declared"); return }
        var lines = body[1].split("\n").filter(function (line) {
          var trimmed = line.trim()
          return trimmed !== "" && trimmed.indexOf("//") !== 0
        })
        check("the reload handler is two statements", 2, lines.length)
        if (/while|for |\.running\s*=\s*true|waitFor/.test(body[1])) {
          bad("the reload handler contains no wait")
        } else {
          ok("the reload handler contains no wait")
        }
      }
    })
  }

  function shiftIso(iso, seconds) {
    return new Date(Date.parse(iso) + seconds * 1000)
      .toISOString().replace(/\.\d{3}Z$/, "Z")
  }

  function batchFor(envelope) {
    return {
      nonce: envelope.nonce,
      exitStatus: envelope.ok === true ? 0 : 2,
      launchAt: shiftIso(envelope.attemptedAt, -60),
      receiptAt: shiftIso(envelope.observedAt || envelope.attemptedAt, 1)
    }
  }

  function pad(text, bytes) {
    var out = text
    while (out.length < bytes) out += " "
    return out
  }

  function eventFor(kind) {
    if (kind === "exit") return { event: "exit", status: 0 }
    if (kind === "stdout") return { event: "stdout", text: "{}" }
    return { event: "stderr", text: "" }
  }

  function permutations(items) {
    if (items.length <= 1) return [items]
    var out = []
    for (var i = 0; i < items.length; i++) {
      var rest = items.slice(0, i).concat(items.slice(i + 1))
      var sub = permutations(rest)
      for (var j = 0; j < sub.length; j++) out.push([items[i]].concat(sub[j]))
    }
    return out
  }

  // =========================================================================
  // Service cases: the real Process, real timers, real signals
  // =========================================================================

  function serviceCases() {
    pending.push({
      name: "AC-040: nothing launches before the host injects its properties",
      waitMs: 400,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () { service = createService() },
      assert: function () {
        if (service === null) return
        check("the service is not ready before injection", false, service.ready)
        var status = service._status()
        check("no batch has been launched", 0, status.generation)
        check("the model is service-shaped from the first frame", true,
              service.viewModel !== null && service.viewModel !== undefined)
      }
    })

    pending.push({
      name: "AC-040: REQ-023b's first poll fires on ready's rising edge",
      waitMs: 1500,
      setup: function () { injectHost(service, 30) },
      assert: function () {
        var status = service._status()
        check("the service became ready", true, status.ready)
        check("a batch was launched immediately", 1, status.generation)
        check("the resolved interval is the injected one", 30, status.refreshIntervalSec)
        check("a snapshot arrived", true, status.hasSnapshot)
        check("the health level is green", "green", status.healthLevel)
        check("the panel state is ok", "ok", status.panelState)
        check("the watchdog was disarmed on completion", false, status.watchdogRunning)

        // HC-14, on the ACTUAL argv rather than on the source. A `__pycache__`
        // write inside a staged plugin folder is seen by the registry as a
        // change and hot-reloads the plugin mid-poll, so `-B` is not a style
        // choice — and a grep of the source would pass on a line that never
        // runs.
        var argv = service._helpers.length > 0 ? service._helpers[0].command : []
        check("the helper is launched with -B (HC-14)", true, argv.indexOf("-B") >= 0)
        check("and with -E and -s", true,
              argv.indexOf("-E") >= 0 && argv.indexOf("-s") >= 0)
        check("and NOT with -I, which would drop its own sys.path entry (HC-15)",
              -1, argv.indexOf("-I"))
        check("the nonce is passed as an argv pair", true,
              argv.indexOf("--nonce") >= 0)
      }
    })

    pending.push({
      name: "AC-005: the published model updates within 100 ms of the join",
      waitMs: 3000,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        var self = this
        self.completedAt = 0
        self.publishedAt = 0
        self.baseline = service._status().generation
        pollTimer.callback = function () {
          var status = service._status()
          if (status.generation <= self.baseline) return
          if (self.completedAt === 0 && !status.helperRunning && status.hasSnapshot) {
            self.completedAt = Date.now()
          }
          if (self.publishedAt === 0 && service.viewModel
              && service.viewModel.state === "ok") {
            self.publishedAt = Date.now()
          }
        }
        pollTimer.running = true
        service.requestRefresh()
      },
      teardown: function () { pollTimer.running = false; pollTimer.callback = null },
      assert: function () {
        if (this.completedAt === 0) { bad("the batch completed"); return }
        if (this.publishedAt === 0) { bad("the model was published"); return }
        // `_recompute` runs synchronously inside the completion path, so this
        // gap is zero by construction. It is measured anyway: the criterion is
        // about what a widget SEES, and a future edit that deferred the
        // publication to a callLater would satisfy every other assertion here.
        var gap = Math.abs(this.publishedAt - this.completedAt)
        if (gap <= 100) ok("the model was published within " + gap + "ms of the join")
        else bad("the model was published within 100ms of the join", gap + "ms")
      }
    })

    pending.push({
      name: "AC-048: an envelope echoing the wrong nonce is discarded",
      waitMs: 1500,
      prepare: function () { writeScenario({ mode: "bad-nonce" }) },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("the batch failed", "malformed_response", status.errorKind)
        // REQ-021 / BIZ-005: an unsuccessful batch does not replace the
        // snapshot, and the previous one is still there.
        check("the previous snapshot is retained", true, status.hasSnapshot)
        check("the failure is visible", "malformed_response", status.panelState)
      }
    })

    pending.push({
      name: "AC-046: a malformed envelope publishes malformed_response",
      waitMs: 1500,
      prepare: function () { writeScenario({ mode: "malformed" }) },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("the kind is the named one", "malformed_response", status.errorKind)
        check("the panel shows a failure, not the stale snapshot silently",
              "malformed_response", status.panelState)
        check("nothing threw", true, service.viewModel !== null)
      }
    })

    pending.push({
      name: "a success envelope paired with a non-zero exit is rejected",
      waitMs: 1500,
      prepare: function () { writeScenario({ mode: "wrong-exit" }) },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("exit status is part of the protocol", "malformed_response", status.errorKind)
      }
    })

    pending.push({
      name: "DATA-005b: a stderr flood marks the batch internal",
      waitMs: 2000,
      prepare: function () { writeScenario({ mode: "stderr-flood", stderrBytes: 65536 }) },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("the batch is internal even though stdout was valid",
              "internal", status.errorKind)
        var found = false
        for (var i = 0; i < status.warnings.length; i++) {
          if (status.warnings[i] === "stderr_bound_exceeded") found = true
        }
        check("the bound is reported as a warning", true, found)
      }
    })

    pending.push({
      name: "a typed failure envelope reaches the panel as its own kind",
      waitMs: 1500,
      prepare: function () {
        writeScenario({ mode: "failure", kind: "unauthorized",
                        httpStatus: 401, retryable: false })
      },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("the kind survives the wire", "unauthorized", status.errorKind)
        check("the panel names it", "unauthorized", status.panelState)
        // REQ-021 / BIZ-005: an unsuccessful batch does not replace the last
        // complete snapshot. A failure envelope carries `data: null`, so a
        // service that assigned it unconditionally would CLEAR the snapshot and
        // the panel would fall back to `loading` — losing a perfectly good
        // reading because one refresh failed.
        check("the previous snapshot survives a failure envelope", true,
              status.hasSnapshot)
      }
    })

    pending.push({
      name: "AC-038: a manual refresh is refused while polling is suspended",
      waitMs: 1500,
      prepare: function () {
        writeScenario({ mode: "failure", kind: "uncommitted", retryable: false })
      },
      setup: function () { service.requestRefresh() },
      assert: function () {
        var status = service._status()
        check("the configuration fault suspends polling", true, status.pollingSuspended)
        var before = status.generation
        var reason = service.requestRefresh()
        check("the refusal names itself", "suspended", reason)
        check("no helper was launched", before,
              service._status().generation)
      }
    })

    pending.push({
      name: "AC-056: reload returns immediately with a batch in flight",
      waitMs: 900,
      prepare: function () { writeScenario({ mode: "hang", hangSec: 40 }) },
      setup: function () {
        // The previous case left polling SUSPENDED on purpose, and a suspended
        // service launches nothing (AC-038). Reset first, or "a batch is in
        // flight" is false for a reason that is not about reload at all.
        resetService(30)
      },
      assert: function () {
        var running = service._status().helperRunning
        check("a batch is in flight", true, running)
        writeScenario({ mode: "success" })
        var started = Date.now()
        service._reload()
        var elapsed = Date.now() - started
        // DATA-010a: the 2 s IPC budget. A handler that waited for the process
        // to exit would take the 90 s the stub is sleeping for.
        if (elapsed < 2000) ok("reload returned in " + elapsed + "ms")
        else bad("reload returned within the IPC budget", elapsed + "ms")
      }
    })

    pending.push({
      name: "the reload starts a fresh batch and the abandoned one is ignored",
      waitMs: 2500,
      assert: function () {
        var status = service._status()
        check("a new snapshot arrived", true, status.hasSnapshot)
        check("no error is showing", null, status.errorKind)
      }
    })

    pending.push({
      name: "AC-004: the watchdog fires at 30 s and abandons the batch",
      waitMs: 33000,
      prepare: function () { writeScenario({ mode: "hang", hangSec: 34 }) },
      setup: function () { resetService(30) },
      assert: function () {
        var status = service._status()
        check("the kind is timeout", "timeout", status.errorKind)
        check("the watchdog was disarmed", false, status.watchdogRunning)
        // HC-8: there is no kill API, so the abandoned process is very likely
        // still running. The invariant is on AUTHORITY, not on process count.
        check("the abandoned batch cannot affect state", -1, status.batchGeneration)
      }
    })

    pending.push({
      name: "REQ-017a: a watchdog tagged with an older batch does nothing",
      waitMs: 6000,
      prepare: function () { writeScenario({ mode: "success", delaySec: 3 }) },
      setup: function () {
        // Called directly, because the guard is unreachable through the timer:
        // `_complete` always stops the watchdog and `_launch` always restarts
        // it, so a stale one never fires on its own. The tag exists so that
        // discipline can be relaxed without the watchdog then firing against
        // somebody else's batch.
        var self = this
        resetService(30)
        // The batch must be genuinely IN FLIGHT and freshly launched. Two
        // earlier versions of this case measured nothing: one fired against a
        // COMPLETED batch, where `joinBatch` returns `already-terminal` and the
        // guard makes no difference; the other read `helperRunning` while a
        // PREVIOUS case's abandoned helper was still alive, so "in flight" was
        // true for the wrong process.
        fireTimer.callback = function () {
          fireTimer.running = false
          self.before = service._status()
          service._watchdogGeneration = 9999
          service._onWatchdog()
          self.after = service._status()
        }
        fireTimer.interval = 900
        fireTimer.running = true
      },
      teardown: function () { fireTimer.running = false; fireTimer.callback = null },
      assert: function () {
        if (!this.before) { bad("the mistagged watchdog was fired"); return }
        check("a batch was in flight when it fired", true, this.before.helperRunning)
        check("a mistagged watchdog abandons no batch",
              this.before.batchGeneration, this.after.batchGeneration)
        // The discriminating assertion is the batch's OUTCOME. Comparing
        // `errorKind` before and after says nothing when the previous case left
        // one set — which is how the first version of this passed with the
        // guard removed.
        var status = service._status()
        check("the batch it did not abandon went on to succeed", true,
              status.hasSnapshot)
        check("and published no timeout", null, status.errorKind)
      }
    })

    pending.push({
      name: "AC-004: the next batch after a timeout is normal",
      waitMs: 2500,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        this.before = service._status().generation
        service.requestRefresh()
      },
      assert: function () {
        var status = service._status()
        check("a new batch ran", this.before + 1, status.generation)
        check("a snapshot arrived", true, status.hasSnapshot)
        check("the timeout cleared", null, status.errorKind)
        check("the watchdog is not left running", false, status.watchdogRunning)
      }
    })

    pending.push({
      name: "HC-8: an abandoned helper exiting mid-batch cannot corrupt it",
      waitMs: 42000,
      // Hang durations across this suite are kept just past the 30 s watchdog
      // rather than far past it, so an earlier case's abandoned helper has
      // exited before a later case needs a pool slot. A first version used 90
      // and 120 seconds and exhausted the pool three cases later, which then
      // looked like a defect in the case under test.
      //
      // The hang outlives the 30 s watchdog by two seconds, so the abandoned
      // process exits WHILE the replacement batch is in flight. Without the
      // per-process generation tag its `onExited` folds into the new batch's
      // join and completes it early, against a stdout that has not arrived —
      // which reads as a malformed envelope for a batch that was fine.
      prepare: function () { writeScenario({ mode: "hang", hangSec: 32 }) },
      setup: function () {
        resetService(30)
        var self = this
        // Once the watchdog has fired, start a SLOW success. Its stdout lands
        // after the abandoned process has exited.
        overlapTimer.callback = function () {
          overlapTimer.running = false
          writeScenario({ mode: "success", delaySec: 6 })
          afterWriter(function () { service.requestRefresh() })
        }
        overlapTimer.interval = 31000
        overlapTimer.running = true
      },
      teardown: function () { overlapTimer.running = false; overlapTimer.callback = null },
      assert: function () {
        var status = service._status()
        check("the replacement batch succeeded", true, status.hasSnapshot)
        check("no protocol error was invented", null, status.errorKind)
      }
    })

    pending.push({
      name: "AC-005: the next automatic batch is one interval after completion",
      waitMs: 17000,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        // REQ-023a anchors the cadence on COMPLETION, not launch: with the
        // minimum interval (15 s) shorter than the maximum batch (25 s), a
        // launch anchor would poll continuously with no idle gap at all.
        resetService(15)
        var self = this
        self.completedAt = 0
        self.observed = 0
        pollTimer.callback = function () {
          var status = service._status()
          if (self.completedAt === 0 && status.hasSnapshot && !status.helperRunning) {
            self.completedAt = Date.now()
            self.baseline = status.generation
          } else if (self.completedAt !== 0 && status.generation > self.baseline
                     && self.observed === 0) {
            self.observed = Date.now()
          }
        }
        pollTimer.running = true
      },
      teardown: function () { pollTimer.running = false; pollTimer.callback = null },
      assert: function () {
        if (this.completedAt === 0) { bad("the first batch completed"); return }
        if (this.observed === 0) { bad("a second batch was launched"); return }
        // AC-005's window is 15.0-16.0. The lower bound is relaxed by the
        // poll timer's own resolution, because a measurement that can read
        // 25 ms early would fail the criterion for being right.
        between("the next batch lands one interval after completion",
                14.97, 16.0, (this.observed - this.completedAt) / 1000)
      }
    })

    pending.push({
      name: "AC-005: poll ticks during a long batch are skipped, not queued",
      waitMs: 26000,
      // A 20 s stub with a 15 s interval: at least one tick falls inside the
      // batch. REQ-016 SKIPS it — queueing would let a slow controller build a
      // backlog that then fires as a burst.
      prepare: function () { writeScenario({ mode: "success", delaySec: 20 }) },
      setup: function () {
        this.before = service._status().generation
        service.requestRefresh()
      },
      waitMs: 26000,
      assert: function () {
        var status = service._status()
        check("exactly one batch ran during the 20 s window",
              this.before + 1, status.generation)
        check("it succeeded", true, status.hasSnapshot)
      }
    })

    pending.push({
      name: "DATA-008a: a backwards clock re-baselines once and retries",
      waitMs: 4000,
      prepare: function () { writeScenario({ mode: "skew", skewSec: 3600 }) },
      setup: function () {
        resetService(30)
        var self = this
        // Switch to a good stub once the skewed batch has been rejected, so the
        // retry has something to succeed against.
        overlapTimer.callback = function () {
          overlapTimer.running = false
          writeScenario({ mode: "success" })
        }
        overlapTimer.interval = 1500
        overlapTimer.running = true
      },
      teardown: function () { overlapTimer.running = false; overlapTimer.callback = null },
      assert: function () {
        var status = service._status()
        // The retry is the whole of DATA-008a. A version that re-baselined but
        // could not retry left the scheduler in `active-batch` forever, which
        // is worse than rejecting every batch: it stops polling entirely.
        check("the service is not wedged in a batch", false, status.helperRunning)
        check("polling resumed", true, status.hasSnapshot)
        if (status.schedulerState === "active-batch") {
          bad("the scheduler left the batch", "still active-batch")
        } else {
          ok("the scheduler left the batch (" + status.schedulerState + ")")
        }
      }
    })

    pending.push({
      name: "DATA-002: conflicting duplicate entries suspend polling",
      waitMs: 800,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        service._reload()
        injectHost(service, 30, 60)
      },
      assert: function () {
        var status = service._status()
        check("the conflict is named", "configuration_conflict", status.errorKind)
        check("polling is suspended", true, status.pollingSuspended)
        check("the panel state is the conflict", "configuration_conflict",
              status.panelState)
      }
    })

    pending.push({
      name: "DATA-002: duplicates differing only in presentation are accepted",
      waitMs: 1500,
      setup: function () {
        // Same service-consumed setting, different presentation. Accepted, with
        // the left-most entry winning for service purposes.
        var layout = { left: [{ id: "gaius-codius.unifi", refreshIntervalSec: 45,
                                compactMetric: "clients" }],
                       center: [],
                       right: [{ id: "gaius-codius.unifi", refreshIntervalSec: 45,
                                 compactMetric: "none" }] }
        service.manifest = { id: "gaius-codius.unifi", __sourceDir: stubRoot }
        service.shell = stubShell.withLayout(layout)
      },
      assert: function () {
        var status = service._status()
        check("polling is not suspended", false, status.pollingSuspended)
        check("the left-most entry wins for the service", 45, status.refreshIntervalSec)
      }
    })

    pending.push({
      name: "DATA-003c: an unresolvable helper path is helper_unavailable",
      waitMs: 1200,
      setup: function () {
        // A manifest with no `__sourceDir` and a service whose own directory
        // holds no helper. The batch must fail with a NAMED, actionable kind —
        // and, more importantly, the scheduler must leave `active-batch`, which
        // an earlier version did not because it reported the failure against
        // the previous batch's generation.
        service._reload()
        service.manifest = { id: "gaius-codius.unifi", __sourceDir: "/nonexistent-plugin-root" }
        service.shell = stubShell.withLayout({
          left: [], center: [], right: [{ id: "gaius-codius.unifi" }]
        })
      },
      assert: function () {
        var status = service._status()
        check("the kind names the helper", "helper_unavailable", status.errorKind)
        if (status.schedulerState === "active-batch") {
          bad("the scheduler left the batch", "still active-batch")
        } else {
          ok("the scheduler left the batch (" + status.schedulerState + ")")
        }
      }
    })

    pending.push({
      name: "REQ-018: a refresh pressed during a batch is coalesced, then runs",
      waitMs: 9000,
      prepare: function () { writeScenario({ mode: "success", delaySec: 4 }) },
      setup: function () {
        resetService(3600)          // no automatic tick can confuse the count
        var self = this
        self.reasons = []
        // `_reload` schedules the first batch through Qt.callLater, so pressing
        // immediately finds an IDLE scheduler and launches rather than
        // coalescing — which is what a first version measured, and it reported
        // "manual" for a press that was supposed to queue.
        pressTimer.callback = function () {
          pressTimer.running = false
          self.inFlight = service._status().helperRunning
          self.before = service._status().generation
          self.reasons = [service.requestRefresh(), service.requestRefresh(),
                          service.requestRefresh()]
        }
        pressTimer.interval = 700
        pressTimer.running = true
      },
      teardown: function () { pressTimer.running = false; pressTimer.callback = null },
      assert: function () {
        check("a batch was in flight when Refresh was pressed", true, this.inFlight)
        // REQ-018 coalesces them into exactly ONE pending refresh —
        // `pendingManual` is a boolean and not a count for that reason.
        for (var i = 0; i < this.reasons.length; i++) {
          check("press " + (i + 1) + " was coalesced", "coalesced", this.reasons[i])
        }
        var status = service._status()
        check("exactly one coalesced batch ran", this.before + 1, status.generation)
        check("it succeeded", true, status.hasSnapshot)
      }
    })

    pending.push({
      name: "REQ-022: the widget greys at staleAt with no batch in flight",
      waitMs: 7000,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        resetService(3600)
        var self = this
        // Age the last success past `staleAt` by rewriting the scheduler's own
        // record, then touch NOTHING. Only the service's freshness tick can
        // move the panel to `stale` from here — which is REQ-022's "evaluated
        // live rather than latched", and the one thing a batch-driven test
        // cannot show.
        staleTimer.callback = function () {
          staleTimer.running = false
          var st = service._schedule
          var aged = {}
          for (var key in st) aged[key] = st[key]
          // `lastSuccessAt` is the WALL-clock field REQ-022 ages a snapshot by;
          // `lastCompletionAt` is monotonic and is the scheduler's own axis.
          // A first version aged a field named `lastSuccessAtWall`, which does
          // not exist, so nothing moved and the case passed for no reason.
          aged.lastSuccessAt = st.lastSuccessAt - 100000
          aged.lastCompletionAtWall = st.lastCompletionAtWall - 100000
          service._schedule = aged
          self.staleAtAfter = Schedule.staleAt(aged)
          self.atRewrite = service.viewModel.state
        }
        staleTimer.interval = 1200
        staleTimer.running = true
      },
      teardown: function () { staleTimer.running = false; staleTimer.callback = null },
      assert: function () {
        var status = service._status()
        if (!this.staleAtAfter) { bad("the snapshot could be aged"); return }
        check("the model had not gone stale before the rewrite", "ok", this.atRewrite)
        check("nothing was polling", false, status.helperRunning)
        check("the snapshot is still there", true, status.hasSnapshot)
        check("the service now reports it stale", true, status.isStale)
        // The published model, not the computed answer: widgets bind to this
        // and compute nothing, which is what makes UX-011's identical
        // cross-monitor state a consequence of the singleton.
        check("the published model went stale on its own", "stale",
              service.viewModel.state)
        check("and grey", "grey", service.viewModel.healthLevel)
      }
    })

    pending.push({
      name: "DATA-003c: a helper path that resolves to nothing ends the batch",
      waitMs: 600,
      setup: function () {
        // `_helperPath` is set directly. The branch is unreachable in any real
        // deployment — `manifest.__sourceDir` is stamped by the scanner and
        // `Qt.resolvedUrl` always resolves for a plugin loaded off a
        // filesystem — so it is a barrier against a future change rather than
        // against today's input, and a barrier whose test never runs is one
        // that gets deleted.
        //
        // What it pins is the GENERATION the failure is reported against.
        // `beginBatch` has already advanced it before `_launch` is reached, so
        // reporting against `_batchGeneration` (the previous batch's) makes
        // `Schedule.onFailure` discard it and the scheduler never leaves
        // `active-batch` — the same defect the watchdog had.
        resetService(3600)
        service._helperPath = ""
        this.reason = service.requestRefresh()
        this.after = service._status()
      },
      teardown: function () { service._resolveHelperPath() },
      assert: function () {
        check("the refresh was attempted", "manual", this.reason)
        check("the kind names the helper", "helper_unavailable", this.after.errorKind)
        if (this.after.schedulerState === "active-batch") {
          bad("the scheduler left the batch", "still active-batch")
        } else {
          ok("the scheduler left the batch (" + this.after.schedulerState + ")")
        }
      }
    })

    pending.push({
      name: "REQ-016: an exhausted helper pool reports timeout, not internal",
      waitMs: 34000,
      prepare: function () { writeScenario({ mode: "hang", hangSec: 45 }) },
      setup: function () {
        // Pool of one, so exhaustion is reachable without waiting for three
        // consecutive 30 s watchdogs. The hang occupies it; the watchdog
        // abandons the batch at 30 s; the refresh that follows finds nothing
        // free.
        resetService(3600)
        service.helperPoolSize = 1
        var self = this
        exhaustTimer.callback = function () {
          exhaustTimer.running = false
          self.afterWatchdog = service._status()
          self.reason = service.requestRefresh()
          self.afterRefresh = service._status()
        }
        exhaustTimer.interval = 31500
        exhaustTimer.running = true
      },
      teardown: function () {
        exhaustTimer.running = false
        exhaustTimer.callback = null
        service.helperPoolSize = 3
      },
      assert: function () {
        if (!this.afterWatchdog) { bad("the watchdog fired"); return }
        check("the watchdog abandoned the batch", "timeout",
              this.afterWatchdog.errorKind)
        // `internal` would send a user to a bug report. Every pool member being
        // occupied means every recent batch is still hung, which is a fact
        // about their controller.
        check("exhaustion reports timeout", "timeout", this.afterRefresh.errorKind)
        // And the scheduler must leave the batch, or nothing ever polls again —
        // the failure has to be reported against the generation `beginBatch`
        // just created, not the previous one.
        if (this.afterRefresh.schedulerState === "active-batch") {
          bad("the scheduler left the batch", "still active-batch")
        } else {
          ok("the scheduler left the batch (" + this.afterRefresh.schedulerState + ")")
        }
      }
    })

    pending.push({
      name: "AC-028: teardown names every released resource",
      waitMs: 600,
      setup: function () {
        service.destroy()
        service = null
      },
      assert: function () {
        // The log line itself is asserted by run_harness.sh, which sees stdout.
        ok("the service was destroyed without an error")
      }
    })
  }

  // 25 ms, because it is the MEASUREMENT instrument for AC-005's 15.0-16.0 s
  // window. At 100 ms a true 15.0 s gap can read as 14.9 and fail the criterion
  // it is meant to confirm.
  Timer {
    id: fireTimer
    repeat: false
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }

  Timer {
    id: pressTimer
    repeat: false
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }

  Timer {
    id: staleTimer
    repeat: false
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }

  Timer {
    id: exhaustTimer
    repeat: false
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }

  Timer {
    id: overlapTimer
    repeat: false
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }

  Timer {
    id: pollTimer
    interval: 25
    repeat: true
    running: false
    property var callback: null
    onTriggered: if (callback) callback()
  }
}
