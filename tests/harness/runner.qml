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

        check("the whole envelope corpus was driven", 78, checked)
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
        check("both gateways are listed", 2, model.gatewayRows.length)
        check("the online gateway is on screen", true, panelTextContains("UDM Pro"))
        check("the down gateway is on screen", true, panelTextContains("USG Backup"))
        check("the metric-less gateway says so", false, model.gatewayRows[1].hasMetrics)
        check("'statistics not fetched' is on screen", true,
              panelTextContains("statistics not fetched"))

        // REQ-010 / AC-063: two listed, seven down. The remainder comes from
        // counts.offlineTotal and never from the array's length.
        check("two devices are listed", 2, model.offline.devices.length)
        check("the total is the independent integer", 7, model.offline.total)
        check("the remainder is five", "and 5 more", model.offline.moreLabel)
        check("an offline device is on screen", true, panelTextContains("Garage AP"))
        check("an impaired device is on screen", true, panelTextContains("Loft Switch"))
        check("its class is on screen", true, panelTextContains("impaired"))
        check("the remainder line is on screen", true, panelTextContains("and 5 more"))

        // REQ-013a. The fixture raises two warnings and one of them,
        // `insecure_tls`, has its own permanent row (UX-009) — so it is
        // deliberately NOT repeated in the list, and the list has one entry.
        check("the list carries the warning without a row of its own", 1,
              model.warningRows.length)
        check("a warning sentence is on screen", true,
              panelTextContains("offline device list is truncated"))
        check("its code is on screen", true, panelTextContains("offline_list_truncated"))
        check("the self-rendering warning is not repeated", true,
              model.warningRows[0].code !== "insecure_tls")

        // AC-025: the role rows sum to 13 over 12 unique devices.
        check("the role rows are not a partition", true, model.roleCountsAreNotAPartition)
        check("the note names the unique total", true,
              panelTextContains("12 adopted device"))

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
        var text = panelWidget.vm.nextAttemptText
        if (/^in \d+s$|^now$/.test(text)) ok("the retry is shown as a countdown (" + text + ")")
        else bad("the retry is shown as a countdown", "got [" + text + "]")
        check("the countdown reached the panel", true,
              text === "" || panelTextContains(text))
      }
    })

    // The measurement is taken against a SUCCESSFUL schedule, not a failed one.
    // REQ-023b's ramp retries a fresh `network` failure every two seconds and
    // resets the deadline each time, so the countdown there is a sawtooth
    // between "in 1s" and "in 2s" — it recomputes constantly and still never
    // moves by twelve seconds. Reading a twelve-second decrease off it is
    // measuring the ramp, not the tick. An idle interval is monotone, which is
    // what makes the drop mean what the assertion says it means.
    pending.push({
      name: "AC-071: an idle countdown is running before the measurement",
      waitMs: 900,
      prepare: function () { writeScenario({ mode: "success" }) },
      setup: function () { resetService(30) },
      assert: function () {
        if (panelWidget === null || service === null) return
        check("a snapshot arrived", true, panelWidget.vm.hasSnapshot)
        check("the scheduler is idle between polls", "idle-normal",
              service._status().schedulerState)
        var text = panelWidget.vm.nextAttemptText
        if (/^in \d+s$/.test(text)) ok("the countdown is rendered in seconds (" + text + ")")
        else bad("the countdown is rendered in seconds", "got [" + text + "]")
      }
    })

    pending.push({
      name: "AC-071: the countdown recomputes while the panel is open",
      // Twelve seconds against a 5 s tick, so at least two recomputations must
      // fall inside the window. SAMPLING — rather than reading once at the end
      // — is what makes this a measurement of the interval rather than a single
      // observation that happens to land just after a tick: a one-shot read six
      // seconds in can legitimately see a one-second drop, because the last
      // tick may have fired a second after the baseline was taken. That is how
      // the first version of this case failed while the code was correct.
      waitMs: 12000,
      setup: function () {
        var self = this
        self.before = panelWidget.vm.nextAttemptText
        self.rebuilds = 0
        self.lastAt = Date.now()
        self.maxGapMs = 0
        self.lastModel = panelWidget.vm
        pollTimer.callback = function () {
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
        var after = panelWidget.vm.nextAttemptText

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

        if (!/^in \d+s$/.test(after)) {
          bad("the countdown is still in seconds", "got [" + after + "]")
          return
        }
        var wasSec = parseInt(this.before.replace(/[^0-9]/g, ""), 10)
        var nowSec = parseInt(after.replace(/[^0-9]/g, ""), 10)
        // Lower bound (window - tick): a countdown that has not been
        // recomputed since the last tick can be one tick stale, and no more.
        // Upper bound is the window plus slack, because the baseline is read in
        // `setup` and the assertion runs after the driver's own scheduling —
        // a measured 13 against a nominal 12 is the driver, not a fault.
        between("the countdown fell by roughly the elapsed time",
                7, 15, wasSec - nowSec)
        checkPanelText("the new countdown reached the panel", after)
        // The panel holds no timer of its own: one clock, in the object that
        // owns it, so every monitor's widget updates from the same instant
        // (REQ-014 / UX-011).
        check("the countdown is still the service's own", true,
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
      name: "UX-008: Tab cycles the two actions and Enter activates the focused one",
      waitMs: 0,
      assert: function () {
        if (panelWidget === null) return
        panelWidget.focusIndex = 0
        panelWidget.moveFocus(1)
        check("Tab moves to Open UniFi", 1, panelWidget.focusIndex)
        panelWidget.moveFocus(1)
        check("Tab wraps back to Refresh", 0, panelWidget.focusIndex)
        panelWidget.moveFocus(-1)
        check("Backtab wraps the other way", 1, panelWidget.focusIndex)
        openedUrls = []
        check("Enter on Open UniFi tries the dashboard", "rejected",
              panelWidget.activateFocused())
        panelWidget.focusIndex = 0
        check("Enter on Refresh reaches the refresh path", "disabled",
              panelWidget.activateFocused())
      }
    })

    // --- DATA-011 / UX-004 --------------------------------------------------
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
