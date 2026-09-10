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
//      `barConfig.layout` (the 4.0.3 PluginShellApi shape) and a stub
//      `manifest` carrying `__sourceDir` so the stub helper launches —
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
// HC-17. The view layer needs the real theme singletons, which resolve because
// run_harness.sh links every shell module directory into the harness root.
// Nothing is staged: this is a root under /tmp that Omarchy never reads.
import qs.Commons

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

  function injectHost(instance, intervalSec, duplicate, extra) {
    var layout = { left: [], center: [], right: [] }
    var entry = { id: "gaius-codius.unifi" }
    if (intervalSec !== undefined && intervalSec !== null) {
      entry.refreshIntervalSec = intervalSec
    }
    // DATA-002b: the service reads its settings out of `bar.layout`, not from
    // the injected `settings`, so REQ-005's compactMetric has to arrive here.
    if (extra) { for (var key in extra) entry[key] = extra[key] }
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
      // 4.0.3 PluginShellApi shape: `barConfig.layout`, no `shellConfig`.
      return shellFactory.createObject(harness, { barConfig: { layout: layout } })
    }
  }
  Component {
    id: shellFactory
    QtObject { property var barConfig: null }
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

  // `prepare` and `setup` are wrapped for a reason found the hard way: an
  // exception in either one skipped `driver.start()`, so the driver never
  // fired, `runNext` was never reached, and the whole harness sat silent until
  // the outer `timeout` killed it seven minutes later. A test that hangs
  // instead of failing costs more than the bug it was hiding — here, a case
  // calling a method on a service an earlier case had destroyed.
  function runNext() {
    if (pending.length === 0) { finish(); return }
    currentCase = pending.shift()
    console.log("HARNESS: -- " + currentCase.name)
    if (currentCase.prepare) {
      try {
        currentCase.prepare()
      } catch (e) {
        bad(currentCase.name, "prepare threw: " + e)
        Qt.callLater(harness.runNext)
        return
      }
    }
    afterWriter(harness.runAct)
  }

  function runAct() {
    currentCase.startedAt = Date.now()
    if (currentCase.setup) {
      try {
        currentCase.setup()
      } catch (e) {
        bad(currentCase.name, "setup threw: " + e)
        Qt.callLater(harness.runNext)
        return
      }
    }
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
    // A run that asserted NOTHING is not a pass. `--only` cannot build the
    // service — the cases that do are the ones it filters out — so every
    // selected case returns early at its `if (service === null) return` guard
    // and the runner printed "PASS 0 passed, 0 failed". That is the same shape
    // as N-103, where six UI cases returned silently and the run stayed green
    // with a lower count: a result whose only symptom is a number nobody is
    // comparing to anything.
    if (passes === 0 && failures === 0) {
      console.log("HARNESS RESULT: FAIL 0 passed, 0 failed"
                  + " -- no assertion ran, so this run proves nothing")
      exitTimer.running = true
      return
    }
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
    uiCases()
    // run_harness.sh --only <regex>. A development aid; see the note there.
    // A regex rather than a substring because the cases that matter are rarely
    // adjacent: several of them depend on a service or a panel an earlier case
    // built, so a useful filter is usually an alternation.
    var only = typeof config.only === "string" ? config.only : ""
    if (only !== "") {
      var pattern = new RegExp(only)
      var kept = []
      for (var i = 0; i < pending.length; i++) {
        if (pattern.test(pending[i].name)) kept.push(pending[i])
      }
      pending = kept
      console.log("HARNESS: --only " + only + " selected " + pending.length + " case(s)")
    }
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

        // 102 since gateways gained a `metrics` container: one accept fixture
        // covering its three states, and three reject ones for the shapes the
        // consumer must refuse. Was 98 after DEV-7 added three accept and
        // seventeen reject fixtures for SPEC-v1.1-browse.md. A literal, so a
        // corpus that silently shrank fails here rather than quietly proving
        // less under V4 than V8.
        check("the whole envelope corpus was driven", 102, checked)
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
      name: "SPEC-v1.1-browse.md: the browse model builds the same under V4",
      waitMs: 0,
      assert: function () {
        // `build` reaches most of the browse code on every path, so the ordinary
        // cases below already drive it under V4. The DETAIL builders do not:
        // they run only for an expanded row, so `deviceDetail`, `clientDetail`,
        // `portRow`, `radioSummaryText`, `formatPct`, `poeText` and
        // `formatInstant` would have had 400-odd V8 assertions and no V4
        // execution at all until Phase B3 drew them.
        //
        // HC-16's whole premise is that these modules run UNCHANGED under both
        // engines. A module half of which has never been executed by one of them
        // is not evidence for that.
        var data = readJson(repoRoot
          + "/tests/fixtures/envelopes/accept/success_browse_full.json")
        if (data === null) { bad("the browse fixture loads"); return }
        var snapshot = data.data

        var withDetail = null
        for (var i = 0; i < snapshot.devices.length; i++) {
          if (snapshot.devices[i].detail !== null) {
            withDetail = snapshot.devices[i]
            break
          }
        }
        if (withDetail === null) { bad("the fixture carries a fetched detail"); return }

        // A client that HAS a MAC. `clients[0]` was used here, and its
        // `macAddress` is null — so REQ-B21's assertion below ran against the
        // string "unknown" and demonstrated nothing about a MAC at all. Chosen
        // the same way the device with a detail is chosen, and for the same
        // reason: the fixture decides which record fits, not an index.
        var withMac = null
        for (var c = 0; c < snapshot.clients.length; c++) {
          var mac = snapshot.clients[c].macAddress
          if (typeof mac === "string" && mac !== "") { withMac = snapshot.clients[c]; break }
        }
        if (withMac === null) { bad("the fixture carries a client with a MAC"); return }

        var model = ViewModel.build({
          snapshot: snapshot, level: { level: "amber", rule: 3 },
          settings: {}, warnings: [], nowWall: 1768209240,
          browse: {
            expandedDeviceId: withDetail.id,
            expandedClientId: withMac.id
          }
        })

        check("REQ-B11: the helper's order survives the model",
              -1, ViewModel.firstBrowseOrderViolation(snapshot.devices))
        check("REQ-B12: the client order survives too",
              -1, ViewModel.firstClientOrderViolation(snapshot.clients))
        check("every listed device became a row",
              snapshot.devices.length, model.deviceList.rows.length)
        check("every listed client became a row",
              snapshot.clients.length, model.clientList.rows.length)

        // The detail path, which is the point of this case.
        var detail = model.deviceList.expandedDetail
        if (detail === null) { bad("the expanded device has a detail"); return }
        check("AC-B09: a fetched detail states no truncation", "", detail.unavailableText)
        check("the port table rendered", withDetail.detail.ports.length,
              detail.ports.length)
        var strings = 0
        for (var p = 0; p < detail.ports.length; p++) {
          var port = detail.ports[p]
          if (typeof port.idxText === "string" && typeof port.poeText === "string"
              && typeof port.connectorText === "string"
              && typeof port.stateText === "string") strings++
        }
        check("every port cell is a string", detail.ports.length, strings)
        // The radio line is built under V4 too, and it is a string even when
        // the device has no radios — `PortTable` binds it unconditionally.
        check("the radio line built under V4", "string", typeof detail.radiosText)
        for (var r = 0; r < detail.rows.length; r++) {
          if (typeof detail.rows[r].value !== "string") {
            bad("every detail row is a string", detail.rows[r].key); return
          }
        }
        ok("every device detail row is a string under V4")

        // `formatInstant` and `parseRfc3339` are hand-written against the RFC
        // 3339 grammar precisely because `Date.parse` is implementation-defined
        // outside ISO 8601 — so the two engines agreeing on this literal is the
        // assertion, not a formality.
        //
        // The literal is a LOCAL time, and `run_harness.sh` pins TZ to the same
        // +05:30 DST-free zone the node suite pins. This is the canary for that:
        // without it, a missing tzdata would make local time equal UTC in both
        // engines and they would agree on the wrong answer.
        check("REQ-B17: the pinned zone reached V4", -330,
              new Date(Date.UTC(2026, 0, 12)).getTimezoneOffset())
        check("REQ-B17: V4 renders the same instant", "2026-01-12 14:44",
              ViewModel.formatInstant("2026-01-12T09:14:00Z"))
        check("REQ-B17: V4 rejects a date that does not exist", "unknown",
              ViewModel.formatInstant("2026-02-30T00:00:00Z"))
        check("V4 says the port state in words", "no link",
              ViewModel.portStateWord("DOWN"))
        check("V4 omits an absent retry rate", "5 GHz",
              ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: null }))
        check("and keeps a real zero", "5 GHz (0% retries)",
              ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: 0 }))
        check("BIZ-003 holds under V4", "unknown", ViewModel.formatPct(null))
        check("and a real zero survives it", "0%", ViewModel.formatPct(0))

        var clientDetail = model.clientList.expandedDetail
        if (clientDetail === null) { bad("the expanded client has a detail"); return }
        // Membership, not a count. This assertion was `4 === rows.length`, and
        // adding an IP row broke it while changing nothing REQ-B21 is about —
        // the same defect as N-90. A count also passes for a detail whose MAC
        // row has been replaced by something else entirely, which is the one
        // thing this is supposed to catch.
        //
        // REQ-B21 / D3 is: the MAC is in the DETAIL and on no collapsed row.
        var detailKeys = []
        var macValue = ""
        for (var di = 0; di < clientDetail.rows.length; di++) {
          detailKeys.push(clientDetail.rows[di].key)
          if (clientDetail.rows[di].key === "mac") macValue = clientDetail.rows[di].value
        }
        check("REQ-B21: the detail carries the MAC", true,
              detailKeys.indexOf("mac") !== -1)
        check("REQ-B21: and it is an address, not the 'unknown' placeholder", true,
              macValue.indexOf(":") !== -1)
        // The row the list draws when the client is COLLAPSED. Every string on
        // it, so a MAC arriving in a field nobody thought about still fails.
        var collapsed = null
        for (var ci = 0; ci < model.clientList.rows.length; ci++) {
          if (model.clientList.rows[ci].id === withMac.id) collapsed = model.clientList.rows[ci]
        }
        if (collapsed === null) { bad("the expanded client is in the list"); return }
        var leaked = []
        for (var key in collapsed) {
          if (typeof collapsed[key] === "string" && key !== "searchText"
              && collapsed[key].indexOf(macValue) !== -1) {
            leaked.push(key)
          }
        }
        check("REQ-B21: no collapsed row renders it", 0, leaked.length)
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


  // =========================================================================
  // Phase 10: the view layer (CP7)
  //
  // The panel is instantiated the way the host instantiates it — constructed
  // first, `bar` assigned afterwards — because DATA-003a's whole hazard is the
  // frame where `bar` is still null. Everything asserted here is either a
  // decision the view makes (AC-011's launcher, UX-008's focus ring) or a
  // string that reached the screen (REQ-008/009/010/013a).
  // =========================================================================

  property var panelWidget: null
  property var openedUrls: []
  // AC-B18's scroll cases act in `setup` and read the result a turn later in
  // `assert` — the restoration is deferred by a `Qt.callLater`, exactly as the
  // list's own `keepCurrentVisible` is — so what was measured before the act
  // has to survive between the two.
  property var scrollProbe: null

  // The stub bar. Only what the widget and the qs.Ui components actually
  // touch, so a new host dependency shows up here as an undefined rather than
  // being quietly satisfied by a catch-all.
  Component {
    id: stubBarFactory
    QtObject {
      property var shell: null
      // Deliberately NOT the Color.* defaults. A stub whose colours match the
      // fallbacks makes "takes its colour from the bar" and "fell back to the
      // theme singleton" indistinguishable — a widget that ignored `bar`
      // entirely would paint identically and pass.
      property color foreground: "darkseagreen"
      property color barForeground: "steelblue"
      property color urgent: "firebrick"
      property string fontFamily: Style.font.family
      property bool vertical: false
      property int barSize: Style.bar.sizeHorizontal
      property string position: "top"
      property bool foregroundAnimationEnabled: false
      property var activePopout: null
      function showTooltip(item, text) {}
      function hideTooltip(item) {}
      function registerClickTarget(item) {}
      function unregisterClickTarget(item) {}
      // REQ-007a is the host's, not ours (HC-13). The stub records the calls
      // so a panel that started arbitrating for itself would be visible.
      function requestPopout(key) { return true }
      function releasePopout(key) {}
    }
  }
  Component {
    id: stubShellForWidget
    QtObject {
      property var unifiService: null
      function serviceFor(id) {
        return id === "gaius-codius.unifi" ? unifiService : null
      }
    }
  }

  function createPanel() {
    var component = Qt.createComponent("Panel.qml", Component.PreferSynchronous)
    if (component.status !== Component.Ready) {
      bad("Panel.qml loads", component.errorString())
      return null
    }
    var instance = component.createObject(harness)
    if (instance === null) {
      bad("Panel.qml instantiates", "createObject returned null")
      return null
    }
    // AC-011: the launcher is replaced before anything can call it, so a
    // browser is never opened by the suite and the call is observable.
    openedUrls = []
    instance.urlOpener = function (url) { harness.openedUrls.push(String(url)) }
    return instance
  }

  function attachBar(widget, unifiService) {
    var shell = stubShellForWidget.createObject(harness, { unifiService: unifiService })
    var stub = stubBarFactory.createObject(harness, { shell: shell })
    widget.bar = stub
    return stub
  }

  // Every string that reached an item, wherever it is in the tree. A panel that
  // "renders from vm" is only demonstrated by the text actually arriving on
  // screen — a binding that was never evaluated because its delegate was never
  // created reads identically from the outside.
  function collectText(node, out, depth) {
    if (node === null || node === undefined || depth > 20) return out
    // INVISIBLE SUBTREES ARE SKIPPED. Without this the walk proves only that a
    // binding evaluated, not that anything reached the screen — and `visible:
    // false` on the gateway list and on the insecure-TLS row both survived a
    // mutation pass while every assertion still passed.
    try { if (node.visible === false) return out } catch (e) { /* not an item */ }
    try {
      if (typeof node.text === "string" && node.text !== "") out.push(node.text)
    } catch (e) { /* not a text-bearing object */ }
    var kids = null
    try { kids = node.data } catch (e) { kids = null }
    if (kids === null || kids === undefined) {
      try { kids = node.children } catch (e) { kids = null }
    }
    if (kids === null || kids === undefined) return out
    var length = 0
    try { length = kids.length } catch (e) { return out }
    for (var i = 0; i < length; i++) {
      var child = null
      try { child = kids[i] } catch (e) { child = null }
      collectText(child, out, depth + 1)
    }
    try {
      if (node.contentItem !== undefined && node.contentItem !== null) {
        collectText(node.contentItem, out, depth + 1)
      }
    } catch (e) { /* not a window */ }
    return out
  }

  // The same walk, for a property that is not `text`. `tooltipText` lives on
  // the BarIconButton, which is not reachable by id from here — the widget is
  // created by Qt.createComponent, exactly as the host creates it, so the
  // runner sees only its root.
  function collectProperty(node, name, out, depth) {
    if (node === null || node === undefined || depth > 20) return out
    try { if (node.visible === false) return out } catch (e) { /* not an item */ }
    try {
      var value = node[name]
      if (value !== undefined && value !== null && value !== "") out.push(value)
    } catch (e) { /* no such property here */ }
    var kids = null
    try { kids = node.data } catch (e) { kids = null }
    if (kids === null || kids === undefined) return out
    var length = 0
    try { length = kids.length } catch (e) { return out }
    for (var i = 0; i < length; i++) {
      var child = null
      try { child = kids[i] } catch (e) { child = null }
      collectProperty(child, name, out, depth + 1)
    }
    return out
  }

  function findByObjectName(node, wanted, depth) {
    if (node === null || node === undefined || depth > 20) return null
    try { if (node.objectName === wanted) return node } catch (e) { /* not a QObject */ }
    var kids = null
    try { kids = node.data } catch (e) { kids = null }
    if (kids === null || kids === undefined) return null
    var length = 0
    try { length = kids.length } catch (e) { return null }
    for (var i = 0; i < length; i++) {
      var child = null
      try { child = kids[i] } catch (e) { child = null }
      var hit = findByObjectName(child, wanted, depth + 1)
      if (hit !== null) return hit
    }
    return null
  }

  // A KeyboardPanel is a FULL-SCREEN layer-shell overlay whose `dismissArea`
  // MouseArea closes it on any press outside the bar region
  // (Ui/KeyboardPanel.qml:281, :331). So while one is open in this harness it
  // covers every output and swallows the user's clicks — and the first click
  // they make closes it. That is correct product behaviour (REQ-007: losing
  // focus closes the panel) and it makes "the panel is still open twelve
  // seconds later" an assertion about whether anyone touched the mouse.
  //
  // Every text assertion therefore OPENS the panel itself, immediately before
  // walking. `panelController.show()` sets `open` synchronously and the
  // delegates already exist, so the walk in the same tick sees them.
  function ensurePanelOpen() {
    if (panelWidget !== null && !panelWidget.opened) panelWidget.open()
  }

  function panelTextContains(needle) {
    ensurePanelOpen()
    var found = collectText(panelWidget, [], 0)
    for (var i = 0; i < found.length; i++) {
      if (String(found[i]).indexOf(needle) !== -1) return true
    }
    return false
  }

  // A containment check that says WHY it failed. "expected true got false" over
  // a tree walk is the least informative failure in this file: it cannot
  // distinguish a closed panel from a delegate that was never created from a
  // string that changed between being read and being looked for.
  // Every open/close transition, so a case that finds the panel shut can say
  // WHEN it shut rather than only that it did. A popup closing on its own would
  // be a product defect and this is what tells the two apart.
  property var openLog: []
  Connections {
    target: harness.panelWidget
    ignoreUnknownSignals: true
    function onOpenedChanged() {
      harness.openLog = harness.openLog.concat(
        [(harness.panelWidget.opened ? "open@" : "close@") + Date.now()])
    }
  }

  function checkPanelText(name, needle) {
    if (panelTextContains(needle)) { ok(name); return }
    var found = collectText(panelWidget, [], 0)
    bad(name, "\"" + needle + "\" is not among the " + found.length
        + " visible strings; panel open=" + panelWidget.opened
        + "; transitions=" + JSON.stringify(openLog)
        + "; now=" + Date.now()
        + "; sample=" + JSON.stringify(found.slice(0, 12)))
  }

  function uiCases() {
    // --- AC-067 / DATA-003a -------------------------------------------------
    pending.push({
      name: "AC-067: the widget renders service_unavailable with no bar at all",
      waitMs: 60,
      setup: function () { panelWidget = createPanel() },
      assert: function () {
        if (panelWidget === null) return
        // The first frame. `bar` has never been assigned, so
        // `bar?.shell?.serviceFor(...)` is null and REQ-013b applies.
        check("the service reference is null", null, panelWidget.unifiService)
        check("the state is service_unavailable", "service_unavailable",
              panelWidget.vm.state)
        check("the sentence is not blank", true, panelWidget.vm.sentence.length > 0)
        // Bar.qml:1581-1582 sizes the slot from these. A zero-sized widget is
        // an invisible one, which is the other way REQ-013b fails.
        check("the widget has a non-zero implicit width", true,
              panelWidget.implicitWidth > 0)
        check("the widget has a non-zero implicit height", true,
              panelWidget.implicitHeight > 0)
        check("no binding produced undefined", false,
              String(panelWidget.vm.tooltip).indexOf("undefined") !== -1)
      }
    })

    pending.push({
      name: "AC-067: a bar whose shell has no service renders the same state",
      waitMs: 200,
      setup: function () {
        attachBar(panelWidget, null)
        // The text walk skips invisible subtrees, so the panel has to be open
        // for its content to count as rendered. It stays open for the rest of
        // the view cases.
        panelWidget.open()
      },
      assert: function () {
        if (panelWidget === null) return
        check("serviceFor returned null", null, panelWidget.unifiService)
        check("the state is still service_unavailable", "service_unavailable",
              panelWidget.vm.state)
        check("the sentence reached the panel", true,
              panelTextContains("service is not running"))
      }
    })

    // --- AC-034: the four REQ-001a expressions ------------------------------
    pending.push({
      name: "AC-034: all four health levels map to a theme expression",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        var evaluator = colourProbe
        var seen = {}
        var levels = ["green", "amber", "red", "grey"]
        for (var i = 0; i < levels.length; i++) {
          var rendering = ViewModel.renderingFor(levels[i])
          seen[levels[i]] = String(evaluator.colorFor(rendering))
        }
        // healthy and degraded share the token on purpose — the badge is what
        // separates them, which is why the badge flag is checked here too.
        check("healthy paints the foreground token", seen.green,
              String(evaluator.foreground))
        check("degraded shares the healthy token", seen.green, seen.amber)
        check("degraded carries the badge", true,
              ViewModel.renderingFor("amber").badge)
        check("healthy carries no badge", false,
              ViewModel.renderingFor("green").badge)
        check("critical paints the urgent token", seen.red,
              String(evaluator.urgent))
        check("unknown is dimmed, not the foreground", true, seen.grey !== seen.green)
        check("unknown is not the urgent token", true, seen.grey !== seen.red)
        // A missing descriptor is the never-injected frame: grey, not healthy.
        check("a missing descriptor is dim, never healthy", seen.grey,
              String(evaluator.colorFor(null)))
      }
    })

    // --- AC-011 / SEC-009 / UX-010 -----------------------------------------
    pending.push({
      name: "AC-011: the dashboard opens through the injected launcher only",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        var rejected = ["https://a b", "https://u:p@h/", "file:///etc/passwd",
                        "javascript:1", "https://h/;reboot"]
        for (var i = 0; i < rejected.length; i++) {
          var verdict = ViewModel.acceptDashboardUrl(rejected[i])
          check("rejected: " + rejected[i], false, verdict.accepted)
        }
        openedUrls = []
        // Nothing is configured and there is no service, so there is no URL:
        // the button refuses rather than opening something it invented.
        check("no URL means no launch", "rejected", panelWidget.openDashboard())
        check("nothing was opened", 0, openedUrls.length)

        // UX-010: the warning is produced WITH the acceptance, so a caller
        // cannot open the URL without having been handed the warning first.
        var plain = ViewModel.acceptDashboardUrl("http://h/")
        check("plain http is accepted", true, plain.accepted)
        check("plain http warns before the launch", true, plain.warnPlainHttp)
      }
    })

    // --- the wired path ----------------------------------------------------
    pending.push({
      name: "REQ-014: the widget renders the service's model and computes none",
      waitMs: 900,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        // AC-028's case destroyed the service the earlier block used, so this
        // one builds its own. Constructed first and injected afterwards, which
        // is the order the host uses and the order DATA-003 is about.
        service = createService()
        panelWidget.bar.shell.unifiService = service
        injectHost(service, 30)
      },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("the widget found the service", true,
              panelWidget.unifiService === service)
        check("the widget's model IS the service's", true,
              panelWidget.vm === service.viewModel)
        check("a snapshot arrived", true, panelWidget.vm.hasSnapshot)
        check("the healthy state carries no sentence", "", panelWidget.vm.sentence)
      }
    })

    pending.push({
      name: "REQ-008/008a/009/010/013a: the panel renders from vm",
      waitMs: 250,
      setup: function () { panelWidget.open() },
      assert: function () {
        if (panelWidget === null || service === null) return
        var model = panelWidget.vm
        // REQ-008a: the aggregate status word, from ViewModel.wanRows.
        check("the WAN status word is on screen", true,
              model.wanRows.length === 4
              && panelTextContains(model.wanRows[0].value))
        // REQ-008a: and every gateway, individually.
        if (model.gatewayRows.length > 0) {
          check("the gateway name is on screen", true,
                panelTextContains(model.gatewayRows[0].nameText))
        } else {
          bad("the stub snapshot has a gateway to list")
        }
        // REQ-009: the client count and the unique device total.
        check("the client count is on screen", true,
              panelTextContains(model.clientsText))
        check("the adopted-device total is on screen", true,
              panelTextContains(model.devicesTotalText))
        // REQ-010 and REQ-013a render only when there is something to render;
        // asserted against the model so the case cannot pass by rendering
        // nothing when the model was empty.
        if (model.offline.devices.length > 0) {
          check("an offline device is on screen", true,
                panelTextContains(model.offline.devices[0].nameText))
        }
        if (model.warningRows.length > 0) {
          check("a warning is on screen", true,
                panelTextContains(model.warningRows[0].text))
        }
        // AC-052: the meta rows are present whatever else is. Found by KEY
        // rather than by index — the row order changed once already, and an
        // index quietly starts asserting about a different row.
        var helperRow = null
        for (var m = 0; m < model.metaRows.length; m++) {
          if (model.metaRows[m].key === "helperVersion") helperRow = model.metaRows[m]
        }
        if (helperRow === null) bad("the meta rows carry the helper version")
        else check("the helper version row is on screen", true,
                   panelTextContains(helperRow.value))
      }
    })

    // REQ-004: "updated" is the freshness half of the two facts the widget owes
    // the user, and it is the half no other case looked at with a REAL service
    // behind it. `viewmodel.test.js` supplies `lastSuccessAt` itself and so can
    // only ever prove the formatter; the wiring from the scheduler's field to
    // that argument lives in Service.qml, in another language, where a
    // misspelled property is `undefined` rather than an error. It was
    // misspelled, and the panel spent Phase 12 telling the truth about the site
    // under the words "never updated".
    //
    // Asserted on the RENDERED STRING as well as the model, because the
    // headline is what the user reads and it is composed, not bound.
    pending.push({
      name: "REQ-004: a successful batch is reported as recently updated",
      waitMs: 4000,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        resetService(30)
        service.requestRefresh()
      },
      assert: function () {
        var vm = service.viewModel
        check("the batch succeeded", true, service._status().hasSnapshot)
        if (vm.lastUpdateText === "never") {
          bad("the last-update text is a time, not \"never\"", vm.lastUpdateText)
        } else {
          ok("the last-update text is \"" + vm.lastUpdateText + "\"")
        }
        if (vm.headline.indexOf("never") !== -1) {
          bad("the headline does not say never", vm.headline)
        } else {
          ok("the headline reads \"" + vm.headline + "\"")
        }
        if (vm.tooltip.indexOf("Last update: never") !== -1) {
          bad("the tooltip does not say never", vm.tooltip)
        } else {
          ok("the tooltip states a real last-update time")
        }
        // The panel, not just the model. A binding that dropped the headline
        // would leave every assertion above green.
        //
        // Upper-cased because `PanelHero` is Omarchy's, and it upcases its
        // `meta` line — the model's "just now" reaches the screen as
        // "JUST NOW". Matched on the host's rendering rather than worked
        // around, because the string the user reads is the subject here.
        checkPanelText("the panel shows the update time",
                       vm.lastUpdateText.toUpperCase())
      }
    })

    // --- REQ-010 / REQ-013a / UX-009 / AC-025 / AC-063 ----------------------
    //
    // Against the healthy snapshot, `DeviceList` and `WarningList` render
    // nothing and a case guarded by `if (list.length > 0)` passes without ever
    // running them. This one uses the degraded fixture, whose lists are all
    // non-empty, so the assertions below cannot be satisfied by an empty panel.
    pending.push({
      name: "REQ-010/013a/UX-009: the degraded panel renders every list",
      waitMs: 900,
      prepare: function () {
        writeScenario({ mode: "success", variant: "degraded", insecureTls: true })
      },
      setup: function () { resetService(30) },
      assert: function () {
        if (panelWidget === null || service === null) return
        var model = panelWidget.vm
        check("a snapshot arrived", true, model.hasSnapshot)

        // REQ-002 rule 4, and REQ-001a's one level with no colour of its own.
        check("the level is degraded", "amber", model.healthLevel)
        check("degraded is carried by the badge", true, model.rendering.badge)
        // REQ-001a: `degraded` paints the SAME token as `healthy`, so the badge
        // is the entire visual difference between "fine" and "something is
        // wrong". Asserted on the glyph, not on the model — the descriptor
        // being right does not put a dot on the bar.
        var barGlyph = findByObjectName(panelWidget, "unifi-bar-glyph", 0)
        var heroGlyph = findByObjectName(panelWidget, "unifi-hero-glyph", 0)
        if (barGlyph === null || heroGlyph === null) {
          bad("both glyphs exist", "bar=" + barGlyph + " hero=" + heroGlyph)
          return
        }
        check("the bar glyph carries the badge", true, barGlyph.showBadge)
        check("the panel hero carries it too", true, heroGlyph.showBadge)
        // AMD-13 / REQ-001a: the hero shares the bar item's evaluator rather
        // than restating the table, so it must resolve to the BAR's colour and
        // not to the Commons fallback. The stub's colours differ from the
        // theme's precisely so this can tell the two apart.
        check("the hero glyph takes its colour from the bar",
              String(panelWidget.bar.barForeground), String(heroGlyph.glyphColor))

        // REQ-008a: two gateways, and the second has no statistics at all —
        // a different fact from one metric being unknown.
        //
        // The envelope carries no `statistics_unavailable` naming this
        // gateway, so the model cannot say whether the helper asked, and the
        // row must not claim it did or did not. The panel used to print
        // "statistics not fetched" for every metric-less gateway, including
        // ones whose statistics call was made and failed.
        check("both gateways are listed", 2, model.gatewayRows.length)
        check("the online gateway is on screen", true, panelTextContains("UDM Pro"))
        check("the down gateway is on screen", true, panelTextContains("USG Backup"))
        check("the metric-less gateway says so", false, model.gatewayRows[1].hasMetrics)
        check("it claims nothing about whether the helper asked", "absent",
              model.gatewayRows[1].metricsState)
        check("the honest sentence is on screen", true,
              panelTextContains("no statistics in this reading"))

        // REQ-010 / AC-063: two listed, seven down. The remainder comes from
        // counts.offlineTotal and never from the array's length.
        check("two devices are listed", 2, model.offline.devices.length)
        check("the total is the independent integer", 7, model.offline.total)
        check("the remainder is five", "and 5 more", model.offline.moreLabel)
        check("an offline device is on screen", true, panelTextContains("Garage AP"))
        check("an impaired device is on screen", true, panelTextContains("Loft Switch"))
        check("its class is on screen", true, panelTextContains("impaired"))
        check("the remainder line is on screen", true, panelTextContains("and 5 more"))

        // REQ-013a / SPEC-AMD-2. `insecure_tls` has its own permanent row
        // (UX-009) and is deliberately NOT repeated in the list; every other
        // warning is.
        //
        // Asserted by MEMBERSHIP rather than by counting. A count says "one
        // entry" and passes for a list holding the wrong single warning, and it
        // breaks whenever the fixture gains an unrelated one — which it just
        // did, when the browse arrays brought `clients_truncated` with them.
        var listed = model.warningRows.map(function (row) { return row.code })
        check("the self-rendering warning is not repeated", -1,
              listed.indexOf("insecure_tls"))
        check("every other warning is listed", true,
              listed.indexOf("offline_list_truncated") !== -1
              && listed.indexOf("clients_truncated") !== -1)
        check("the envelope did raise the one that is suppressed", true,
              model.warnings.some(function (w) { return w.code === "insecure_tls" }))
        check("a warning sentence is on screen", true,
              panelTextContains("offline device list is truncated"))
        check("its code is on screen", true, panelTextContains("offline_list_truncated"))

        // AC-025: the role rows sum to 13 over 12 unique devices.
        //
        // This used to look for "12 adopted device" — the sentence under the
        // role rows that explained the arithmetic, removed at the user's
        // request (N-125). What AC-025 actually asks is that the model expose
        // the flag and that the PANEL LABEL carry the unique total, and the
        // total has its own labelled row, where it always was.
        //
        // The value is matched EXACTLY against the collected strings rather
        // than by containment: "12" is a substring of a great many numbers on
        // this panel, and a containment check would pass on any of them.
        check("the role rows are not a partition", true, model.roleCountsAreNotAPartition)
        var panelStrings = collectText(panelWidget, [], 0)
        var totalOnScreen = false
        var noteOnScreen = false
        for (var ts = 0; ts < panelStrings.length; ts++) {
          if (String(panelStrings[ts]) === "12") totalOnScreen = true
          if (String(panelStrings[ts]).indexOf("counted in every role") !== -1) {
            noteOnScreen = true
          }
        }
        check("the unique total is labelled on screen", true,
              panelTextContains("Adopted devices"))
        check("and its value is rendered beside the label", true, totalOnScreen)
        // The removed sentence is gone from the SCREEN, not merely from the
        // model — the whole point of a tree walk over visible items.
        check("the removed note is not rendered", false, noteOnScreen)

        // UX-009 / AC-070: driven by meta, present for the whole session, and
        // with no dismiss handler anywhere in the file.
        check("the insecure-TLS flag is set", true, model.insecureTls)
        // The row's OWN sentence, not the shared opening clause: the warning
        // list carries a `insecure_tls` warning reading "TLS verification is
        // disabled for this controller.", so matching on that prefix is
        // satisfied by a panel whose dedicated row is hidden.
        check("the insecure-TLS row is on screen", true,
              panelTextContains("Anything on the network path"))
      }
    })

    pending.push({
      name: "AC-070: the insecure-TLS row survives a close, a reopen and a refresh",
      waitMs: 900,
      setup: function () {
        panelWidget.close()
        panelWidget.open()
        panelWidget.doRefresh()
      },
      assert: function () {
        if (panelWidget === null) return
        check("the flag is still set after a successful refresh", true,
              panelWidget.vm.insecureTls)
        check("the row is still on screen", true,
              panelTextContains("Anything on the network path"))
      }
    })

    // --- REQ-004 / UX-003 ---------------------------------------------------
    //
    // The case REQ-004 exists for: a fresh snapshot, and the refresh that
    // followed it failed. The colour must stay whatever the snapshot said —
    // never green because the fetch failed, never red because it did — and a
    // separate affordance must say "the last attempt failed" in a shape that
    // is not the degraded badge.
    pending.push({
      name: "REQ-004/UX-003: a failed refresh over a fresh snapshot is marked, not recoloured",
      waitMs: 1000,
      prepare: function () { writeScenario({ mode: "failure", kind: "network" }) },
      setup: function () {
        this.levelBefore = panelWidget.vm.healthLevel
        this.hadSnapshot = panelWidget.vm.hasSnapshot
        panelWidget.doRefresh()
      },
      assert: function () {
        if (panelWidget === null || service === null) return
        var model = panelWidget.vm
        check("there was a snapshot to preserve", true, this.hadSnapshot)
        check("the snapshot survived the failure", true, model.hasSnapshot)
        // BIZ-005 / REQ-021: an unsuccessful batch does not replace the snapshot.
        check("the colour is still the snapshot's", this.levelBefore, model.healthLevel)
        check("the failure is named", "network", model.errorKind)
        check("it is not stale yet", false, model.isStale)

        // UX-003's affordance, on the glyph. A different shape in a different
        // corner from the degraded badge, because the whole requirement is that
        // "the last refresh failed" be distinguishable from "confirmed bad".
        var marks = collectProperty(panelWidget, "showRefreshFailure", [], 0)
        var marked = false
        for (var i = 0; i < marks.length; i++) { if (marks[i] === true) marked = true }
        check("the refresh-failure mark is shown", true, marked)

        // REQ-004 again, in words: the tooltip has to say BOTH things, or the
        // user reads a healthy site as broken or a broken refresh as fine.
        check("the tooltip names the failure", true,
              model.tooltip.indexOf("failed (network)") !== -1)
        check("the tooltip still reports the last update", true,
              model.tooltip.indexOf("Last update:") !== -1)
      }
    })

    // --- REQ-005 / REQ-006 --------------------------------------------------
    pending.push({
      name: "REQ-005/006: the compact metric and the tooltip reach the bar item",
      waitMs: 900,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        service._reload()
        injectHost(service, 30, null, { compactMetric: "clients" })
      },
      assert: function () {
        if (panelWidget === null || service === null) return
        var model = panelWidget.vm
        check("the setting reached the service", "clients",
              service._status().compactMetric)
        check("the compact text is the client count", "42", model.compactText)
        // On the BAR, not merely somewhere in the widget: the client count also
        // appears in the panel's Devices section, so a containment check over
        // the whole tree is satisfied by a bar item rendering nothing.
        var figure = findByObjectName(panelWidget, "unifi-bar-compact", 0)
        if (figure === null) {
          bad("the bar item has a compact-text element")
        } else {
          check("the bar figure is visible", true, figure.visible)
          check("the bar figure is the client count", "42", String(figure.text))
        }

        // REQ-006: the tooltip is what makes UX-002's "colour is never the sole
        // signal" true for the bar item, so it has to be ON the button rather
        // than merely present on the model.
        var tooltips = collectProperty(panelWidget, "tooltipText", [], 0)
        var found = false
        for (var i = 0; i < tooltips.length; i++) {
          if (tooltips[i] === model.tooltip) found = true
        }
        check("the bar button carries the model's tooltip", true, found)
        check("the tooltip names the site", true, model.tooltip.indexOf("Home") !== -1)
        check("the tooltip states the last update", true,
              model.tooltip.indexOf("Last update:") !== -1)
        check("the tooltip states the latest attempt", true,
              model.tooltip.indexOf("Latest attempt:") !== -1)
      },
      teardown: function () {
        // Back to the default, so the cases after this one see the settings
        // they were written against.
        service._reload()
        injectHost(service, 30)
      }
    })

    // --- UX-007 / AC-071 ----------------------------------------------------
    pending.push({
      name: "UX-007: a failed batch shows its retry as a relative time",
      waitMs: 1400,
      prepare: function () { writeScenario({ mode: "failure", kind: "network" }) },
      setup: function () { resetService(15) },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("the batch failed", "network", panelWidget.vm.errorKind)
        check("polling is not suspended", false, panelWidget.vm.pollingSuspended)
        // UX-007: "never a static instant". The retry deadline reaches the
        // panel as a countdown whatever schedule produced it — here REQ-023b's
        // two-second ramp, which is the shortest one that exists.
        // SPEC-AMD-4: this is now the ONLY state in which the countdown is
        // drawn at all, so this case carries the whole of UX-007's rendering.
        check("the scheduler is backing off", "retry-wait",
              service._status().schedulerState)
        var text = panelWidget.vm.nextAttemptText
        if (/^in \d+s$|^now$/.test(text)) ok("the retry is shown as a countdown (" + text + ")")
        else bad("the retry is shown as a countdown", "got [" + text + "]")
        check("the countdown reached the panel", true,
              text === "" || panelTextContains(text))
        check("the panel labels it as the next attempt", true,
              panelTextContains("Next attempt"))
      }
    })

    // SPEC-AMD-4's negative half, and the reason the case below measures what
    // it measures. A healthy widget draws NO countdown: it always has a next
    // attempt and never needs to say so.
    pending.push({
      name: "SPEC-AMD-4: a healthy schedule draws no countdown at all",
      waitMs: 900,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () { resetService(30) },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("a snapshot arrived", true, panelWidget.vm.hasSnapshot)
        check("the scheduler is idle between polls", "idle-normal",
              service._status().schedulerState)
        // The deadline still EXISTS — the line is suppressed by the display
        // rule, not by the scheduler having nothing to count down to. A case
        // that did not check this would pass just as happily against a broken
        // scheduler that had stopped polling.
        if (typeof service._schedule.nextAttemptAt !== "number") {
          bad("the scheduler still holds a deadline to count down to")
        } else {
          ok("the scheduler still holds a deadline; the panel simply omits it")
        }
        check("the model's countdown is empty", "", panelWidget.vm.nextAttemptText)
        check("the panel shows no next-attempt line", false,
              panelTextContains("Next attempt"))
      }
    })

    // AC-071, as amended by SPEC-AMD-4. The criterion used to name
    // `nextAttemptAt` specifically; that string is now absent on the healthy
    // path, so the guarantee is stated over EVERY relative time the panel
    // draws and measured over the one that survives here — `lastUpdateText`,
    // which nothing had ever measured.
    //
    // This is strictly stronger than the version it replaces. The bound AC-071
    // actually asserts is about the service recomputing, not about which string
    // it recomputes into, and `maxGapMs` measured that all along.
    //
    // The old case took a baseline and subtracted. That cannot work here: the
    // window opens inside `relativePast`'s "just now" band, so the baseline is
    // not a number. SAMPLING is used instead — the same reason as before, that
    // a single read at the end cannot tell a string that updates from one that
    // was rendered once and latched.
    pending.push({
      name: "AC-071: the panel's relative times recompute while it is open",
      // TWENTY seconds, and the number is derived rather than picked. The
      // freshness tick is 5 s and free-running, so it has an arbitrary phase
      // relative to the batch that set `lastSuccessAt`; `relativePast` says
      // "just now" below 10 s. A twelve-second window can therefore contain two
      // recomputations that BOTH land inside the "just now" band — measured, at
      // ticks 3.5 s and 8.5 s after a success — and the case fails against
      // correct code. Twenty seconds guarantees a tick at or past 10 s
      // whatever the phase.
      //
      // The lag it exposes is real and is within the criterion: what the panel
      // shows can trail the true elapsed time by up to one tick, and AC-071
      // allows fifteen seconds of exactly that.
      waitMs: 20000,
      setup: function () {
        var self = this
        self.rebuilds = 0
        self.lastAt = Date.now()
        self.maxGapMs = 0
        self.lastModel = panelWidget.vm
        self.seen = [panelWidget.vm.lastUpdateText]
        pollTimer.callback = function () {
          var text = panelWidget.vm.lastUpdateText
          if (self.seen.indexOf(text) === -1) self.seen.push(text)
          if (panelWidget.vm === self.lastModel) return
          self.lastModel = panelWidget.vm
          self.rebuilds++
          var now = Date.now()
          if (now - self.lastAt > self.maxGapMs) self.maxGapMs = now - self.lastAt
          self.lastAt = now
        }
        pollTimer.running = true
      },
      teardown: function () { pollTimer.running = false; pollTimer.callback = null },
      assert: function () {
        if (panelWidget === null || service === null) return

        if (this.rebuilds < 2) {
          bad("the model was rebuilt more than once in twelve seconds",
              "saw " + this.rebuilds)
        } else {
          ok("the model was rebuilt " + this.rebuilds + " times in twelve seconds")
        }
        // AC-071's actual bound, measured rather than assumed: the gap between
        // consecutive recomputations is what "recomputes at least every 15 s"
        // is a statement about.
        if (this.maxGapMs <= 15000) {
          ok("the longest gap between recomputations was "
             + this.maxGapMs + "ms (AC-071 allows 15000)")
        } else {
          bad("the longest gap between recomputations is within 15 s",
              this.maxGapMs + "ms")
        }

        // A model rebuilt twice with a string that never moved would satisfy
        // everything above. The point of the criterion is that the WORDS
        // change, so the words are what is checked.
        if (this.seen.length >= 2) {
          ok("the relative time took " + this.seen.length
             + " distinct values: " + JSON.stringify(this.seen))
        } else {
          bad("the relative time changed during the window",
              JSON.stringify(this.seen))
        }

        var after = panelWidget.vm.lastUpdateText
        if (!/^\d+s ago$/.test(after)) {
          bad("the relative time ends as a count of seconds", "got [" + after + "]")
          return
        }
        // It aged by roughly the window: the batch completed shortly before
        // `setup` ran, so twelve seconds later it must read somewhere near
        // twelve. Bounded on BOTH sides — a string stuck at a large constant
        // would pass a lower bound alone.
        // Bounded on BOTH sides — a string stuck at a large constant would pass
        // a lower bound alone. The floor is 10 rather than 20 because the
        // reading may be one 5 s tick stale, and the ceiling allows for the
        // driver's own scheduling on top of the nominal window.
        between("the relative time aged by roughly the elapsed time",
                10, 30, parseInt(after.replace(/[^0-9]/g, ""), 10))
        checkPanelText("the new relative time reached the panel",
                       after.toUpperCase())
        // The panel holds no timer of its own: one clock, in the object that
        // owns it, so every monitor's widget updates from the same instant
        // (REQ-014 / UX-011).
        check("the relative time is still the service's own", true,
              panelWidget.vm === service.viewModel)
      }
    })

    // --- REQ-011 / AC-038 ---------------------------------------------------
    pending.push({
      name: "REQ-011: Refresh is refused, with a reason, while polling is suspended",
      waitMs: 700,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        // A duplicate entry disagreeing about a service-consumed setting is
        // DATA-002's configuration conflict, which suspends polling.
        service._reload()
        injectHost(service, 30, 60)
      },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("polling is suspended", true, panelWidget.vm.pollingSuspended)
        check("Refresh is disabled", false, panelWidget.vm.refreshEnabled)
        check("the reason is not blank", true,
              panelWidget.vm.refreshDisabledReason.length > 0)
        var before = service._status().generation
        check("pressing Refresh is refused", "disabled", panelWidget.doRefresh())
        check("no batch was launched", before, service._status().generation)
        checkPanelText("the reason reached the panel", "Refresh is unavailable")
      }
    })

    // --- UX-008 -------------------------------------------------------------
    pending.push({
      name: "UX-008: Tab cycles the actions and Enter activates the focused one",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        // REQ-B15 replaced the two-stop `focusIndex` with a named stop list
        // that depends on the page. On Overview it is three long, and the two
        // actions keep the last two places — which is what UX-008 asserted and
        // still asserts.
        panelWidget.view = "overview"
        panelWidget.focusStop = "refresh"
        panelWidget.moveFocus(1)
        check("Tab moves to Open UniFi", "dashboard", panelWidget.focusStop)
        panelWidget.moveFocus(1)
        check("Tab wraps to the segmented control", "segments", panelWidget.focusStop)
        panelWidget.moveFocus(1)
        check("Tab reaches Refresh", "refresh", panelWidget.focusStop)
        panelWidget.moveFocus(-1)
        check("Backtab wraps the other way", "segments", panelWidget.focusStop)
        openedUrls = []
        panelWidget.focusStop = "dashboard"
        check("Enter on Open UniFi tries the dashboard", "rejected",
              panelWidget.activateFocused())
        panelWidget.focusStop = "refresh"
        check("Enter on Refresh reaches the refresh path", "disabled",
              panelWidget.activateFocused())
      }
    })

    // --- SPEC-v1.1-browse.md: AC-B15 … AC-B19 -------------------------------
    //
    // The panel's own state machine, driven through the same functions
    // `PanelKeyCatcher` calls. Keystrokes are not synthesised: what these
    // assert is the panel's response to a key, and `Qt.Key_Tab` arriving at
    // `onTabRequested` is the host's business and is covered by the host's own
    // tests. What is NOT covered anywhere else is what this panel does with it.
    pending.push({
      name: "AC-B15: the segmented control switches views and the pages follow",
      // Long enough for the batch this case's setup launches to finish. The
      // first version reset the service with `waitMs: 0`, so every assertion
      // here and in the four cases after it ran against a panel with no
      // snapshot — which is not a browse page at all, and reported as five
      // separate failures with one cause.
      waitMs: 2500,
      // Guarded, because `--only` skips the cases that build these. Without it
      // a filtered run fails in `setup` and reports nothing about the case it
      // was asked to run — which is how this guard came to be written.
      setup: function () {
        if (service === null) return
        resetService(30)
        service.requestRefresh()
      },
      // The DEGRADED variant, not the healthy one. `success_data()` carries
      // `devices: []` and `clients: []` on purpose — it models a fully
      // budget-truncated reading — so every browse assertion written against it
      // compared two empty arrays and passed for that reason. The degraded stub
      // is the one with something in every list the panel can draw, which is
      // what its own comment says it exists for.
      prepare: function () { writeScenario({ mode: "success", variant: "degraded" }) },
      assert: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        check("the panel opens on Overview", "overview", panelWidget.view)
        check("Overview is not a browse page", false, panelWidget.browsing)

        panelWidget.setView("devices")
        check("the view switched", "devices", panelWidget.view)
        // AC-B15's real assertion: the page renders from `vm` and computes
        // nothing. The rows the panel is about to draw are the rows the model
        // published, object for object — and there ARE some, which the healthy
        // stub's empty lists let this pass without.
        check("the device page has rows to render", true,
              panelWidget.vm.deviceList.rows.length > 0)
        check("the device rows come from vm", panelWidget.vm.deviceList.rows,
              panelWidget.activeList.rows)

        panelWidget.setView("clients")
        check("the client page follows too", "clients", panelWidget.view)
        check("the client page has rows to render", true,
              panelWidget.vm.clientList.rows.length > 0)
        check("the client rows come from vm", panelWidget.vm.clientList.rows,
              panelWidget.activeList.rows)

        // A junk value cannot produce a fourth page.
        panelWidget.setView("nonsense")
        check("an unrecognised view falls back to Overview", "overview",
              panelWidget.view)

        if (service === null) return
        check("the control is on screen while there is a reading", true,
              panelWidget.hasSnapshot)
      }
    })

    pending.push({
      name: "AC-B16: typing filters the list and does not drive the panel cursor",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        if (service === null) return
        // OPENED HERE, not inherited. A KeyboardPanel is a full-screen
        // overlay and its `dismissArea` closes it on any press outside the bar
        // (Ui/KeyboardPanel.qml:281, :331) — so a case that assumes the panel
        // is still open from an earlier one is asserting that nobody touched
        // the mouse. `ensurePanelOpen`'s own comment said exactly this and only
        // `panelTextContains` was calling it; AC-B16 asserted `opened` while
        // depending on AC-B15 having left it that way. It passed every B3 run
        // and failed during the B4 live run, on an unchanged tree — which is
        // the signature of a case whose result was never its own to decide.
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var all = panelWidget.vm.deviceList.rows.length
        if (all === 0) { bad("the fixture site has devices to filter"); return }

        var name = panelWidget.vm.deviceList.rows[0].nameText
        panelWidget.setSearch(name)
        check("the search text reached the model", name.toLowerCase(),
              panelWidget.vm.deviceList.searchText)
        check("the list narrowed", true,
              panelWidget.vm.deviceList.rows.length < all
              || all === 1)

        // A term matching nothing yields the empty list and says so — never
        // the unfiltered list, which is the reflex this guards against.
        panelWidget.setSearch("zzz-no-such-device")
        check("nothing matches", 0, panelWidget.vm.deviceList.rows.length)
        check("and the panel says why", true,
              panelWidget.vm.deviceList.emptyText.indexOf("zzz-no-such-device") !== -1)

        // REQ-B15: Escape clears a non-empty search BEFORE it closes.
        var field = panelWidget.activeBrowse()
        if (field === null) { bad("the device page has a search field"); return }
        check("Escape clears the search first", "cleared", panelWidget.escapePressed())
        check("the search is empty", "", panelWidget.vm.deviceList.searchText)
        // The FIELD too, not only the model. It is uncontrolled — its `text` is
        // not bound to `list.searchText`, so a panel that cleared the model
        // alone would leave the user's text on screen above a list no longer
        // filtered by it.
        check("and the field itself is empty", "", field.searchText)
        check("the panel is still open", true, panelWidget.opened)
        check("Escape again closes", "closed", panelWidget.escapePressed())
        check("the panel closed", false, panelWidget.opened)
        // REQ-B10: closing returns to Overview and clears the search — the
        // widget's job is health, and reopening it should answer that question
        // rather than resume a browse.
        check("closing returned to Overview", "overview", panelWidget.view)
        // REOPENED first. The Escape sequence above closed it, so a `close()`
        // here would be a no-op — `onOpenedChanged` would never fire and the
        // reset this is about would never run, while the assertion below read
        // the state left by the Escape and passed.
        panelWidget.open()
        panelWidget.setView("devices")
        panelWidget.setSearch("router")
        panelWidget.activeBrowse().setSearchText("router")
        check("the search is in hand before closing", "router",
              panelWidget.vm.deviceList.searchText)
        panelWidget.close()
        check("closing cleared the model's search", "",
              panelWidget.vm.deviceList.searchText)
        // Reached BY NAME. `activeBrowse()` is null here, because closing has
        // already returned the panel to Overview — so a check written through
        // it would read "" from the null branch and pass whether or not the
        // field was ever cleared.
        var closed = panelWidget.browseFor("devices")
        if (closed === null) { bad("the device page is reachable by name"); return }
        check("closing cleared the field", "", closed.searchText)
      }
    })

    pending.push({
      name: "AC-B17: Tab reaches every control in REQ-B15's order and wraps",
      waitMs: 0,
      setup: function () { if (panelWidget !== null) panelWidget.open() },
      assert: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var order = ["segments", "search", "list", "refresh", "dashboard"]
        var walked = []
        panelWidget.focusStop = "segments"
        for (var i = 0; i < order.length; i++) {
          walked.push(panelWidget.focusStop)
          panelWidget.moveFocus(1)
        }
        check("Tab walks REQ-B15's five stops", order.join(","), walked.join(","))
        check("and wraps to the first", "segments", panelWidget.focusStop)

        // REQ-B15 / UX-008: every stop names an item the panel can scroll to.
        //
        // Tab reached all five above and the panel never followed, so on a long
        // list the cursor landed on Refresh correctly and invisibly, below the
        // bottom edge. WHERE to scroll is arithmetic and lives in the model,
        // where node covers its four cases; what cannot be checked there is
        // this — that each stop maps to a real, VISIBLE item. A stop added
        // later with no mapping fails here rather than silently not scrolling.
        var unmapped = []
        for (var f = 0; f < order.length; f++) {
          panelWidget.focusStop = order[f]
          var item = panelWidget.focusedItem()
          if (item === null || item === undefined || !item.visible) {
            unmapped.push(order[f])
          }
        }
        check("every focus stop maps to a visible item", "", unmapped.join(","))

        // Overview has three stops and no search or list, so those two must map
        // to nothing rather than to a hidden page's widgets — scrolling to an
        // invisible item is how a panel jumps somewhere with nothing in it.
        panelWidget.setView("overview")
        panelWidget.focusStop = "search"
        check("a stop that does not exist on this page maps to nothing", true,
              panelWidget.focusedItem() === null)
        panelWidget.setView("devices")
        panelWidget.focusStop = "segments"

        if (service === null) return
        // Up/Down move the LIST cursor, not the focus, while the list is
        // focused — the one place the two axes mean different things.
        panelWidget.focusStop = "list"
        panelWidget.deviceCursor = 0
        panelWidget.moveCursor(0, 1)
        check("Down moves the list cursor", 1, panelWidget.deviceCursor)
        check("and does not move the focus stop", "list", panelWidget.focusStop)
        panelWidget.moveCursor(0, -1)
        check("Up moves it back", 0, panelWidget.deviceCursor)
        // Clamped, not wrapped: a list cursor that wraps from the last row to
        // the first while the viewport stays put reads as the list jumping.
        panelWidget.moveCursor(0, -1)
        check("the cursor stops at the top", 0, panelWidget.deviceCursor)

        // Enter expands the focused row, and exactly one row is open.
        var first = panelWidget.vm.deviceList.rows[0].id
        check("Enter expands the focused row", "toggled", panelWidget.activateFocused())
        check("the model reports it expanded", first,
              panelWidget.vm.deviceList.expandedId)
        panelWidget.moveCursor(0, 1)
        panelWidget.activateFocused()
        check("expanding another closes the first",
              panelWidget.vm.deviceList.rows[1].id,
              panelWidget.vm.deviceList.expandedId)
        panelWidget.activateFocused()
        check("Enter on the open row closes it", "",
              panelWidget.vm.deviceList.expandedId)

        // Left/Right on the segmented control move between pages. From
        // OVERVIEW, stated explicitly: this case sets the view to Devices at
        // the top, and a first version assumed otherwise and read the step from
        // Devices to Clients as a failure.
        panelWidget.setView("overview")
        panelWidget.focusStop = "segments"
        panelWidget.moveCursor(1, 0)
        check("Right moves to the next page", "devices", panelWidget.view)
        panelWidget.moveCursor(1, 0)
        check("Right again reaches the last page", "clients", panelWidget.view)
        // Clamped at both ends rather than wrapping, so the three chips read as
        // a row and not a carousel — and the ends are where a modulo would
        // differ from a clamp.
        panelWidget.moveCursor(1, 0)
        check("Right at the last chip stays", "clients", panelWidget.view)
        panelWidget.moveCursor(-1, 0)
        check("Left moves back", "devices", panelWidget.view)
        panelWidget.moveCursor(-1, 0)
        check("Left reaches Overview", "overview", panelWidget.view)
        panelWidget.moveCursor(-1, 0)
        check("Left at the first chip stays", "overview", panelWidget.view)

        // SPEC-AMD-5. The page keys are no longer the segmented control's — they
        // work from every focus stop, and this is the half nothing tested. Every
        // assertion above drives them from `focusStop = "segments"`, which is
        // the one stop where the OLD modal behaviour also worked; the reported
        // bug lived entirely in the other four.
        panelWidget.setView("devices")
        panelWidget.focusStop = ViewModel.FOCUS_LIST
        panelWidget.moveCursor(1, 0)
        check("Right switches page from the list", "clients", panelWidget.view)
        // And it did NOT relocate the focus stop on the way. Walking focus out
        // of the list on a horizontal key is what made the page changes look
        // like the list handing itself over.
        check("and the list keeps the focus", ViewModel.FOCUS_LIST,
              panelWidget.focusStop)

        panelWidget.focusStop = ViewModel.FOCUS_REFRESH
        panelWidget.moveCursor(-1, 0)
        check("Left switches page from a button too", "devices", panelWidget.view)
        check("and the button keeps the focus", ViewModel.FOCUS_REFRESH,
              panelWidget.focusStop)

        // Leaving for a page that has no such stop still relocates, because the
        // stop genuinely is not there — that rule is REQ-B10's and is unchanged.
        panelWidget.focusStop = ViewModel.FOCUS_LIST
        panelWidget.moveCursor(-1, 0)
        check("Overview has no list, so the stop resets", "overview",
              panelWidget.view)
        check("and the focus went to the segments", ViewModel.FOCUS_SEGMENTS,
              panelWidget.focusStop)

        // Vertical keys are now the only thing that walks the stops, and only
        // where there is no list cursor to claim them.
        panelWidget.moveCursor(0, 1)
        check("Down walks the stops on Overview", ViewModel.FOCUS_REFRESH,
              panelWidget.focusStop)
      }
    })

    pending.push({
      name: "AC-B16: the panel's key handling is suspended while the field has focus",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var catcher = findByObjectName(panelWidget, "unifi-key-catcher", 0)
        if (catcher === null) { bad("the key catcher is reachable"); return }

        // REQ-B15's actual mechanism, not a proxy for it. Without `blocked`,
        // typing "ap" into the search box drives the panel cursor: "a" is not
        // bound, but `PanelKeyCatcher` forwards every unhandled single
        // character to `textKey`, where this panel reads "r" as Refresh — so a
        // user searching for "router" would fire a refresh on the third letter.
        check("nothing is being edited to start with", false,
              panelWidget.searchHasFocus)
        check("so the catcher is live", false, catcher.blocked)

        var field = panelWidget.activeBrowse()
        if (field === null) { bad("the device page has a search field"); return }
        field.focusSearch()
        check("the field took focus", true, panelWidget.searchHasFocus)
        check("and the catcher is suspended", true, catcher.blocked)

        field.releaseSearch()
        check("releasing the field revives the catcher", false, catcher.blocked)
        check("and the panel knows it", false, panelWidget.searchHasFocus)

        // The suspension is BOUND to where the caret is, not tracked alongside
        // it — the two cannot disagree, which is what a tracked flag would
        // eventually do.
        check("blocked is the caret's own state", panelWidget.searchHasFocus,
              catcher.blocked)
      }
    })

    // --- REQ-B15, corrected 2026-09-10 --------------------------------------
    //
    // The defect: Tab out of the search field and the panel went deaf. Escape,
    // the arrows, Enter, `r`, `f` and hjkl all stopped, and the only way to
    // dismiss the panel was to click outside it.
    //
    // `BrowseList.releaseSearch` sets `searchField.focus = false`, which clears
    // focus within the scope and hands it to NOBODY: the window's contentItem
    // is left holding active focus and `keyCatcher` is a DESCENDANT of it, so
    // nothing reaches it — key events travel down a focus chain, not down the
    // item tree. `KeyboardPanel.focusTarget` covers the panel opening and
    // nothing after it.
    //
    // Un-blocking the catcher is not the same as giving it the keyboard, which
    // is why the case above passed throughout: it asserts `blocked`, and
    // `blocked` was correct. These two assert `activeFocus` — the property that
    // decides whether a key arrives at all.
    pending.push({
      name: "REQ-B15: Tab out of the search field hands the keyboard back",
      // The restoration is deferred a turn, as the host defers its own
      // (network/Panel.qml:351-358): it is made from inside the notification of
      // the focus change that provoked it.
      waitMs: 60,
      setup: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var field = panelWidget.activeBrowse()
        if (field === null) return
        // The `/` path, both halves of it (Panel.qml's `onTextKey`).
        panelWidget.focusStop = "search"
        field.focusSearch()
        // And what `Keys.onTabPressed` does inside the field, all of it.
        field.releaseSearch()
        panelWidget.moveFocus(1)
      },
      assert: function () {
        if (panelWidget === null) return
        var catcher = findByObjectName(panelWidget, "unifi-key-catcher", 0)
        if (catcher === null) { bad("the key catcher is reachable"); return }
        check("the caret has left the field", false, panelWidget.searchHasFocus)
        check("so the catcher is live", false, catcher.blocked)
        // The assertion the defect survived: live and unfocused is a panel that
        // reads keys nobody is sending it. If this fails while the case above
        // passes, the panel is deaf from the first Tab onwards.
        check("and it HOLDS the keyboard", true, catcher.activeFocus)
        check("Tab still moved the stop", "list", panelWidget.focusStop)
      }
    })

    pending.push({
      name: "REQ-B15: changing page takes the caret off the page being hidden",
      waitMs: 60,
      setup: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var field = panelWidget.activeBrowse()
        if (field === null) return
        panelWidget.focusStop = "search"
        field.focusSearch()
        // Clicking the other page's chip mid-search. Qt clears focus when an
        // item is DISABLED, not when it is hidden, so the Devices field kept
        // the caret behind the Clients page: every keystroke filtering a list
        // nobody could see, and `PanelKeyCatcher` blocked the whole time
        // because the panel was correctly reporting that a field had focus.
        // `Panel.releaseHiddenSearch` is what makes this a path out.
        panelWidget.setView("clients")
      },
      assert: function () {
        if (panelWidget === null) return
        var catcher = findByObjectName(panelWidget, "unifi-key-catcher", 0)
        if (catcher === null) { bad("the key catcher is reachable"); return }
        check("the hidden page's field gave the caret up", false,
              panelWidget.searchHasFocus)
        var hidden = panelWidget.browseFor("devices")
        if (hidden === null) { bad("the device page is reachable by name"); return }
        check("named directly, the same answer", false, hidden.editing)
        check("so the catcher is live", false, catcher.blocked)
        check("and holds the keyboard", true, catcher.activeFocus)
      }
    })

    pending.push({
      name: "AC-B18: a list shorter than its viewport is not a drag surface",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        var list = findByObjectName(panelWidget, "unifi-browse-list", 0)
        if (list === null) { bad("the device list is on screen"); return }

        // The harness cannot meaningfully drag a list, so what it asserts is
        // the property that decides whether a drag would do anything. Inside a
        // KeyboardPanel — a full-screen click sink, HC-20 — a short list that
        // swallowed drags would be a panel that feels stuck.
        //
        // Both directions, and the RULE rather than one particular fit: how
        // many of the stub's devices happen to fit a 320 px viewport is not
        // something this criterion is about, and pinning it would make the
        // case fail on a font change.
        check("whatever the list's size, the rule is the height comparison", true,
              list.interactive === (list.contentHeight > list.height))

        // A search that empties the list must not leave a live drag surface
        // behind — the boundary the comparison has to get right.
        panelWidget.setSearch("zzz-no-such-device")
        check("an emptied list has no content", 0, list.contentHeight)
        check("and is not interactive", false, list.interactive)
        panelWidget.setSearch("")
        check("and the rule still holds once it refills", true,
              list.interactive === (list.contentHeight > list.height))
      }
    })

    // --- AC-B18, corrected 2026-09-10 ---------------------------------------
    //
    // The defect: a 200-device site, mouse-scrolled to the bottom, snapped back
    // to the top every five seconds and did it forever. The rows are a plain JS
    // array that `deviceListModel` rebuilds on every recompute, the freshness
    // tick recomputes whether or not anything moved (REQ-B17), and a `ListView`
    // handed a new array sends `contentY` to 0. `currentIndex` was the only
    // thing putting it back and it is -1 whenever the list is not the
    // keyboard's stop — every mouse user, every poll.
    //
    // `ViewModel.scrollAfterRowsChange` decides where the list lands and node
    // covers its branches. What only this layer can show is that the position
    // survives a REAL rebuild of the real model through the real ListView —
    // that the swap goes through `adoptRows`, which reads the position before
    // the assignment destroys it, rather than through a binding.
    function browseListFor(page) {
      panelWidget.resetBrowse()
      panelWidget.setView(page)
      return findByObjectName(panelWidget, "unifi-browse-list", 0)
    }

    // The fixture site is five devices against a 320 px viewport, so nothing
    // here scrolls on its own. The viewport is shrunk rather than the site
    // grown: "content taller than the viewport" is the whole of the condition,
    // and the code path a 200-device site takes is the same one. Restored in
    // `teardown`, because breaking a binding for the rest of the run would
    // leave a later case measuring a 60 px list.
    function shrinkBrowseList(list) {
      scrollProbe = { list: list, at: 0 }
      list.height = 60
      var reach = list.contentHeight - list.height
      if (reach <= 0) return false
      list.contentY = Math.min(80, reach)
      scrollProbe.at = list.contentY
      return true
    }

    // The list is captured in a LOCAL before the probe is cleared: a binding
    // written against `scrollProbe.list` would be re-evaluated after this
    // function set `scrollProbe` to null, and would throw on every frame from
    // then on.
    function restoreBrowseList() {
      if (scrollProbe === null) return
      var list = scrollProbe.list
      scrollProbe = null
      if (list === null || list === undefined) return
      list.height = Qt.binding(function () {
        return Math.min(list.contentHeight, Style.space(320))
      })
    }

    pending.push({
      name: "AC-B18: a poll leaves the list where the reader scrolled it",
      // A turn is all the restoration needs; 80 ms is a turn with room.
      waitMs: 80,
      setup: function () {
        if (panelWidget === null || service === null) return
        ensurePanelOpen()
        var list = browseListFor("devices")
        if (list === null || !shrinkBrowseList(list)) return
        // The five-second tick, called by name. `_recompute` is the function
        // `freshnessTimer` fires, and it rebuilds both list models from the
        // snapshot already in hand — no controller, no network, and the same
        // new-array-every-time that the defect is made of.
        service._recompute()
      },
      teardown: function () { restoreBrowseList() },
      assert: function () {
        if (panelWidget === null || service === null) return
        if (scrollProbe === null) { bad("the device list was reached"); return }
        var list = scrollProbe.list
        if (scrollProbe.at <= 0) {
          bad("the fixture list is tall enough to scroll",
              "contentHeight=" + list.contentHeight + " height=" + list.height)
          return
        }
        check("the poll left the list where the reader put it",
              scrollProbe.at, list.contentY)
        // And it is showing the REBUILT rows, not the ones whose position it
        // kept: a swap that quietly stopped happening would preserve every
        // position perfectly and freeze "3d ago" at whatever it said an hour
        // ago (REQ-B17).
        check("and it is showing the rows the poll rebuilt",
              panelWidget.vm.deviceList.rows.length, list.count)
        if (list.count > 0) {
          check("row for row, the model's",
                panelWidget.vm.deviceList.rows[0].nameText, list.model[0].nameText)
        }
      }
    })

    pending.push({
      name: "AC-B18: a search returns the list to the top, position or not",
      waitMs: 80,
      setup: function () {
        if (panelWidget === null || service === null) return
        ensurePanelOpen()
        var list = browseListFor("devices")
        if (list === null || !shrinkBrowseList(list)) return
        // Not a poll: the rows underneath are a different question's answer.
        // The cursor goes back to 0 on the same keystroke, and a viewport left
        // half-way down the matches would disagree with it and hide the match
        // the reader typed towards.
        panelWidget.setSearch("e")
      },
      teardown: function () { restoreBrowseList() },
      assert: function () {
        if (panelWidget === null || service === null) return
        if (scrollProbe === null) { bad("the device list was reached"); return }
        var list = scrollProbe.list
        if (scrollProbe.at <= 0) {
          bad("the fixture list is tall enough to scroll",
              "contentHeight=" + list.contentHeight + " height=" + list.height)
          return
        }
        check("a search puts the list back at the top", 0, list.contentY)
        panelWidget.setSearch("")
      }
    })

    pending.push({
      name: "AC-B19: an Overview role row opens Devices filtered to that role",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        if (service === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        var rows = panelWidget.vm.countRows
        if (rows.length === 0) { bad("the fixture site has role rows"); return }

        // The role travels with the row. The count buckets are plural nouns and
        // `devices[].roles` holds the API's feature names; a view translating
        // between them is the one place a typo yields an always-empty list
        // instead of an error.
        var role = rows[0].role
        check("the count row carries a role", true,
              typeof role === "string" && role !== "")
        panelWidget.setView("devices", role)
        check("the page switched", "devices", panelWidget.view)
        check("the filter reached the model", role, panelWidget.vm.deviceList.filterValue)

        var listed = panelWidget.vm.deviceList.rows
        var offRole = 0
        for (var i = 0; i < listed.length; i++) {
          var has = false
          for (var j = 0; j < listed[i].roles.length; j++) {
            if (listed[i].roles[j] === role) has = true
          }
          if (!has) offRole++
        }
        check("every listed device holds the role", 0, offRole)
        check("and the filter is not empty", true, listed.length > 0)

        // REQ-B10a, the part that was missing: the filtered page SAYS it is
        // filtered. Everything above this asserts the filter works, and all of
        // it passed while the page was indistinguishable on screen from an
        // unfiltered one — `matched` was computed and rendered nowhere, and
        // `emptyText` names the role only when the list is EMPTY, which is the
        // one case the chooser is not needed for.
        //
        // Through the tree walk, which skips invisible subtrees, so this is
        // "reached the screen" and not "a binding evaluated".
        var chips = panelWidget.vm.deviceList.filterChips
        var label = ""
        for (var c = 0; c < chips.length; c++) {
          if (chips[c].value === role) label = chips[c].label
        }
        check("the site offers a chip for the role Overview emitted", true, label !== "")
        check("and the chooser is on the filtered page", true, panelTextContains(label))
        check("with All beside it, so the filter can be undone", true,
              panelTextContains("All"))

        // SPEC-AMD-9: `f` cycles the same list the chooser draws. Driven
        // through the panel's own function rather than a synthesised keystroke,
        // which the harness cannot deliver for a text key.
        var before = panelWidget.vm.deviceList.filterValue
        panelWidget.cycleFilter()
        check("f moved the filter", true, panelWidget.vm.deviceList.filterValue !== before)
        // A full lap comes back. One step has already been taken above, so this
        // is the REMAINING `chips.length - 1` — a cycle that does not close is
        // one the user cannot undo, and `f` is its only keyboard route.
        for (var lap = 0; lap < chips.length - 1; lap++) panelWidget.cycleFilter()
        check("and a full lap comes back to where it was", before,
              panelWidget.vm.deviceList.filterValue)

        // SPEC-AMD-10: a filter SURVIVES a page change. It used to be cleared by
        // every one of them, which was right while Overview was the only way to
        // set one and nothing on the page said it was filtered. Both halves have
        // changed, and clearing it is now work the user has to redo.
        panelWidget.setView("devices", role)
        check("the filter is set", role, panelWidget.vm.deviceList.filterValue)
        panelWidget.setView("clients")
        panelWidget.setView("devices")
        check("a round trip through Clients leaves it on", role,
              panelWidget.vm.deviceList.filterValue)

        // The Clients page filters on its OWN axis, and the two do not leak into
        // each other — a single shared string would carry "WIRED" into the role
        // filter and silently empty the Devices page.
        panelWidget.setView("clients")
        var typeChips = panelWidget.vm.clientList.filterChips
        check("the Clients page offers a chooser of its own", true,
              typeChips.length > 1)
        check("and it is All plus connection types", "", typeChips[0].value)
        panelWidget.cycleFilter()
        check("f moved the client filter", typeChips[1].value,
              panelWidget.vm.clientList.filterValue)
        check("and left the device filter alone", role,
              panelWidget.vm.deviceList.filterValue)
        check("the client chooser is on screen", true,
              panelTextContains(typeChips[1].label))
        var typed = panelWidget.vm.clientList.rows
        var offType = 0
        for (var t = 0; t < typed.length; t++) {
          if (typed[t].type !== typeChips[1].value) offType++
        }
        check("every listed client holds the type", 0, offType)
        check("and the filter is not empty", true, typed.length > 0)

        // Only an explicit clear puts it back — which is what "All" is, and what
        // `f` wrapping past the last chip does.
        for (var w = 0; w < typeChips.length - 1; w++) panelWidget.cycleFilter()
        check("wrapping past the last type clears it", "",
              panelWidget.vm.clientList.filterValue)

        // And closing the panel resets both, as REQ-B10 requires of the search.
        panelWidget.resetBrowse()
        check("reset clears the device filter", "",
              panelWidget.vm.deviceList.filterValue)
        check("reset clears the client filter", "",
              panelWidget.vm.clientList.filterValue)
      }
    })

    // --- DATA-011 / UX-004 --------------------------------------------------
    pending.push({
      name: "REQ-B10: losing the snapshot while browsing returns to Overview",
      // LAST of the browse cases, and deliberately so. It discards the snapshot
      // as its whole point, and an earlier version of this ran inside AC-B15 —
      // where it left the four cases after it browsing a site with no reading,
      // and reported four failures with one cause.
      waitMs: 2500,
      // Degraded, like the browse cases above it: leaving the healthy stub in
      // place here would hand the NEXT batch a reading with empty lists, and
      // the cases after this one would inherit it.
      prepare: function () { writeScenario({ mode: "success", variant: "degraded" }) },
      assert: function () {
        if (panelWidget === null || service === null) return
        ensurePanelOpen()
        panelWidget.resetBrowse()
        panelWidget.setView("devices")
        check("a reading is in hand", true, panelWidget.hasSnapshot)

        // The segmented control is hidden when there is no reading — Devices
        // and Clients over no snapshot are two empty pages and a third that
        // explains why. Which would strand the user on an empty page with the
        // only way back hidden. DATA-011's reload does exactly this, on
        // purpose, so the panel has to come back by itself.
        //
        // `_reload()` — the internal one. `reload()` belongs to the IpcHandler,
        // not to the service root.
        service._reload()
        check("the reload discarded the snapshot", false, panelWidget.hasSnapshot)
        check("and the panel came back to Overview", "overview", panelWidget.view)
      }
    })

    pending.push({
      name: "DATA-011: a snapshot is in hand before the reload",
      waitMs: 1200,
      // A deliberately slow helper, so the batch that follows the reload is
      // still running when the next case asserts. Without that, "reconfiguring
      // holds for the whole batch" and "reconfiguring is cleared the instant
      // the batch launches" are indistinguishable.
      prepare: function () { writeScenario({ mode: "success", delaySec: 0.4 }) },
      setup: function () { resetService(30) },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("a snapshot is in hand before the reload", true,
              panelWidget.vm.hasSnapshot)
        check("the panel is showing data", "ok", panelWidget.vm.state)
      }
    })

    pending.push({
      name: "DATA-011: a reload discards the snapshot and renders reconfiguring",
      waitMs: 250,
      setup: function () { service._reload() },
      assert: function () {
        if (panelWidget === null || service === null) return
        // The cached snapshot may belong to a different controller entirely;
        // continuing to show it under the new site's name would be a lie the
        // user has no way to detect.
        check("the snapshot is discarded", false, panelWidget.vm.hasSnapshot)
        check("the panel says reconfiguring", "reconfiguring", panelWidget.vm.state)
        check("the sentence explains why the reading vanished", true,
              panelWidget.vm.sentence.indexOf("different controller") !== -1)
        // The state has to HOLD for the batch, not merely appear for the frame
        // between the reload and the launch. A reload that flashed
        // `reconfiguring` and then showed `loading` would be indistinguishable
        // from a first start, which is the confusion UX-004 exists to remove.
        var status = service._status()
        check("the replacement batch is still running", true, status.helperRunning)
        check("reconfiguring is still in force", true, status.reconfiguring)
        check("the state is not mistaken for a first load", true,
              panelWidget.vm.state !== "loading")
      }
    })

    pending.push({
      name: "DATA-011: reconfiguring ends when the next batch completes",
      waitMs: 1200,
      assert: function () {
        if (panelWidget === null || service === null) return
        check("reconfiguring has ended", false, service._status().reconfiguring)
        check("the panel is showing data again", true, panelWidget.vm.hasSnapshot)
        check("the panel is out of the reconfiguring state", true,
              panelWidget.vm.state !== "reconfiguring")
      },
      teardown: function () {
        panelWidget.close()
        panelWidget.destroy()
        panelWidget = null
        service.destroy()
        service = null
      }
    })
  }

  // The colour evaluator under test, instantiated once. REQ-001a lives in
  // HealthColor.qml and nowhere else, so AC-034 has to drive that file rather
  // than a copy of its table.
  HealthColor { id: colourProbe }

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
      prepare: function () {
        // The stub advances its OWN scenario: it skews once and succeeds
        // afterwards. A timer in the runner cannot do this, because the
        // re-baseline retry is a `Qt.callLater` a few milliseconds behind the
        // batch it retries — and until this was written the case passed on a
        // snapshot left over from an earlier case, which is not the thing it
        // claims to assert.
        writeScenario({ mode: "skew", skewSec: 3600, then: { mode: "success" } })
      },
      setup: function () { resetService(30) },
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
      name: "DATA-002: 4.0.3 barConfigChanged re-resolves a live layout edit",
      waitMs: 800,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () {
        // Same shell object, new barConfig. 4.0.3 never replaces `shell`;
        // syncPluginApis assigns `barConfig`. The service binds that
        // property (and listens for barConfigChanged); a Connections that
        // only listed onShellConfigChanged missed this entirely.
        resetService(30)
        service.shell.barConfig = { layout: { left: [], center: [],
          right: [{ id: "gaius-codius.unifi", refreshIntervalSec: 15,
                    compactMetric: "clients" }] } }
      },
      assert: function () {
        var status = service._status()
        check("the shorter interval was picked up from barConfig", 15, status.refreshIntervalSec)
        check("compactMetric came with it", "clients", status.compactMetric)
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
