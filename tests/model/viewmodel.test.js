// AC-011 (AUTO half), AC-062, AC-063, AC-064, AC-066, AC-067, AC-071 (string half).

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const ViewModel = require("../../ViewModel.js")

const SPEC = fs.readFileSync(path.resolve(__dirname,
  "../../docs/feature-specs/omarchy-unifi-plugin/SPEC.md"), "utf8")
const ACCEPT = path.resolve(__dirname, "../fixtures/envelopes/accept")

function snapshotOf(name) {
  return JSON.parse(fs.readFileSync(path.join(ACCEPT, name + ".json"), "utf8")).data
}

// --- AC-066: the twenty-four panel states --------------------------------

test("AC-066: the state list is exactly DATA-007's kinds plus the five others", () => {
  // Parsed out of SPEC.md rather than copied, so removing a state from the spec
  // fails this build instead of leaving a sentence nothing can reach.
  const kinds = /DATA-007: Error `kind` is one of([\s\S]*?)—/.exec(SPEC)
  assert.ok(kinds, "SPEC.md: could not locate the DATA-007 kind list")
  const errorKinds = [...kinds[1].matchAll(/`([^`]+)`/g)].map((m) => m[1])
  assert.strictEqual(errorKinds.length, 19)

  const nonError = ["loading", "empty", "stale", "service_unavailable", "reconfiguring"]
  const expected = errorKinds.concat(nonError).sort()
  assert.deepStrictEqual(ViewModel.PANEL_STATES.slice().sort(), expected)
  assert.strictEqual(ViewModel.PANEL_STATES.length, 24)
})

test("AC-066: every state has a distinct, non-empty sentence", async (t) => {
  // One NAMED SUBTEST PER STATE, not a bare loop.
  //
  // AC-072 asks for a named, executing test per REQ-013 panel state, and
  // tests/test_suite_integrity.js finds them by looking for the state's name in
  // test output and source. A `for` loop tests all twenty-four but names none,
  // so the coverage report would show gaps for states that are fully covered —
  // and the obvious response to that is to weaken the report. Name them.
  const seen = new Map()
  for (const state of ViewModel.PANEL_STATES) {
    await t.test("panel state: " + state, () => {
      const sentence = ViewModel.sentenceFor(state)
      assert.ok(typeof sentence === "string" && sentence.length > 0, state)
      assert.ok(!seen.has(sentence),
        state + " reuses the sentence of " + seen.get(sentence)
        + " — a shared sentence means one of the two states cannot be diagnosed")
      seen.set(sentence, state)
    })
  }
  assert.strictEqual(seen.size, 24)
})

test("UX-007: each sentence names an action, not just the failure", () => {
  // A sentence that only restates the error leaves the user exactly where they
  // started. These are the words that can lead somewhere.
  const actionable = /Run |Check |Install |Create |Grant |Set |Point |Raise |Upgrade |Remove |Adopt |Make |report|scripts\/configure|console/i
  // Only two states are exempt, and both for the same reason: they are
  // transient and there is genuinely nothing for the user to do yet. Every
  // other state, including `stale` and `timeout`, has to point somewhere —
  // `stale` at the refresh error shown beside it, `timeout` at the controller's
  // load. "Retrying automatically" alone leaves the user watching a spinner.
  const exempt = ["loading", "reconfiguring"]
  for (const state of ViewModel.PANEL_STATES) {
    if (exempt.indexOf(state) !== -1) continue
    assert.ok(actionable.test(ViewModel.sentenceFor(state)),
      state + ": the sentence names no action")
  }
})

test("an unrecognised kind renders as internal, never blank (DATA-007)", () => {
  // Forward compatibility: a newer helper must not brick an older panel.
  assert.strictEqual(ViewModel.sentenceFor("a_future_kind"),
    ViewModel.sentenceFor("internal"))
  assert.strictEqual(ViewModel.sentenceFor(undefined), ViewModel.sentenceFor("internal"))
  assert.ok(ViewModel.sentenceFor(null).length > 0)
})

// --- REQ-001a ------------------------------------------------------------

test("REQ-001a: four levels map to four theme-native renderings, with no hex", () => {
  const renderings = ViewModel.LEVEL_RENDERING
  assert.deepStrictEqual(Object.keys(renderings).sort(),
    ["amber", "green", "grey", "red"])

  // The theme has no green/amber/red token (Commons/Color.qml:19-23), so the
  // levels are distinguished by token + badge + dimming rather than by hue. A
  // hex literal here would break under light themes and violate REQ-001.
  for (const level of Object.keys(renderings)) {
    const rendering = renderings[level]
    assert.ok(/^bar\.(foreground|urgent)$/.test(rendering.token), level)
    assert.ok(!/#[0-9a-fA-F]{3}/.test(JSON.stringify(rendering)), level + ": hex literal")
  }

  // Each level must be visually distinguishable from every other, or the
  // mapping conveys nothing.
  const signatures = new Set(Object.keys(renderings).map((l) =>
    JSON.stringify(renderings[l])))
  assert.strictEqual(signatures.size, 4, "two levels render identically")

  assert.strictEqual(renderings.amber.badge, true, "degraded carries the urgent badge dot")
  assert.strictEqual(renderings.red.token, "bar.urgent")
  assert.strictEqual(renderings.grey.darkenFactor, 1.55, "the established dim idiom")
})

test("UX-002: colour is never the sole signal", () => {
  const words = new Set()
  for (const level of ["green", "amber", "red", "grey"]) {
    const word = ViewModel.wordFor(level)
    assert.ok(word && word.length > 0, level)
    words.add(word)
  }
  assert.strictEqual(words.size, 4,
    "someone who cannot distinguish the levels must still be able to read them")
  assert.strictEqual(ViewModel.wordFor("nonsense"), ViewModel.wordFor("grey"))
})

// --- AC-064 / BIZ-003 ----------------------------------------------------

test("AC-064: null is unknown and zero is zero", () => {
  // BIZ-003 reserves 0 for a value the controller actually reported. Rendering
  // null as "0" states a fact nobody has — an offline WAN and an unread WAN
  // would look identical.
  assert.strictEqual(ViewModel.formatOptional(null), "unknown")
  assert.strictEqual(ViewModel.formatOptional(undefined), "unknown")
  assert.strictEqual(ViewModel.formatOptional(NaN), "unknown")
  assert.strictEqual(ViewModel.formatOptional(0), "0")
  assert.strictEqual(ViewModel.formatOptional(42), "42")

  // The four fields AC-064 names, through the formatters that render them.
  assert.strictEqual(ViewModel.formatUptime(null), "unknown")
  assert.strictEqual(ViewModel.formatUptime(0), "0m")
  assert.strictEqual(ViewModel.formatBps(null), "unknown")
  assert.strictEqual(ViewModel.formatBps(0), "0 bps")
})

test("the optional-gaps fixture renders as unknown throughout, not as zero", () => {
  const snapshot = snapshotOf("success_optional_gaps")
  assert.strictEqual(snapshot.counts.clients, null)
  assert.strictEqual(ViewModel.formatOptional(snapshot.counts.clients), "unknown")
  assert.strictEqual(ViewModel.formatBps(snapshot.wan.downloadBps), "unknown")
  assert.strictEqual(ViewModel.formatUptime(snapshot.wan.uptimeSec), "unknown")
  assert.strictEqual(ViewModel.compactText("clients", snapshot.counts), "unknown",
    "BIZ-002: a failed optional collection is never rendered as 0 clients")
})

test("formatBps and formatUptime scale without locale formatting", () => {
  assert.strictEqual(ViewModel.formatBps(999), "999 bps")
  assert.strictEqual(ViewModel.formatBps(1000), "1 kbps")
  assert.strictEqual(ViewModel.formatBps(12000000), "12 Mbps")
  assert.strictEqual(ViewModel.formatBps(2500000000), "2.5 Gbps")
  assert.strictEqual(ViewModel.formatUptime(59), "0m")
  assert.strictEqual(ViewModel.formatUptime(3600), "1h 0m")
  assert.strictEqual(ViewModel.formatUptime(864000), "10d 0h")
  assert.strictEqual(ViewModel.formatUptime(-1), "unknown")
})

// --- AC-063 --------------------------------------------------------------

test("AC-063: 'and N more' comes from offlineTotal, not the array length", () => {
  const snapshot = snapshotOf("success_500_down_bounded_list")
  assert.strictEqual(snapshot.counts.offlineTotal, 500)
  assert.strictEqual(snapshot.offlineDevices.length, 10)

  const list = ViewModel.offlineList(snapshot)
  assert.strictEqual(list.devices.length, 10)
  assert.strictEqual(list.total, 500)
  assert.strictEqual(list.truncated, true)
  assert.strictEqual(list.moreLabel, "and 490 more")

  // The failure this prevents: computing from the array would render "and 0
  // more" and report a 500-device outage as if ten machines were affected.
  assert.notStrictEqual(list.moreLabel, "and 0 more")
})

test("the offline list truncates at ten and says nothing extra when it fits", () => {
  const short = snapshotOf("success_degraded")
  const list = ViewModel.offlineList(short)
  assert.strictEqual(list.devices.length, 2)
  assert.strictEqual(list.truncated, false)
  assert.strictEqual(list.moreLabel, "")

  const overflowing = {
    counts: { offlineTotal: 17 },
    offlineDevices: Array.from({ length: 17 }, (_, i) => ({ id: "d" + i, class: "down" }))
  }
  const bounded = ViewModel.offlineList(overflowing)
  assert.strictEqual(bounded.devices.length, ViewModel.OFFLINE_LIST_BOUND)
  assert.strictEqual(bounded.moreLabel, "and 7 more")

  assert.deepStrictEqual(ViewModel.offlineList(null).devices, [])
  assert.strictEqual(ViewModel.offlineList(null).moreLabel, "")
})

// --- AC-011 (AUTO half) --------------------------------------------------

test("AC-011: all five hostile URLs are rejected without launching", () => {
  const hostile = [
    "https://a b",             // whitespace
    "https://u:p@h/",          // SEC-009: userinfo
    "file:///etc/passwd",      // scheme
    "javascript:1",            // scheme
    "https://h/;reboot"        // shaped like an injection attempt
  ]
  for (const url of hostile) {
    const result = ViewModel.acceptDashboardUrl(url)
    assert.strictEqual(result.accepted, false, url)
    assert.strictEqual(result.url, null, url + ": nothing may be handed to the launcher")
    assert.ok(result.reason && result.reason.length > 0, url + ": no reason given")
  }
})

test("AC-011 / UX-010: http is accepted and sets warnPlainHttp", () => {
  const plain = ViewModel.acceptDashboardUrl("http://h/")
  assert.strictEqual(plain.accepted, true)
  assert.strictEqual(plain.warnPlainHttp, true)

  // UX-010 requires the warning to be rendered BEFORE the launch. Returning it
  // with the acceptance is what makes that structural: a caller cannot obtain
  // the URL without also being handed the warning.
  assert.ok(Object.prototype.hasOwnProperty.call(plain, "warnPlainHttp"))

  const secure = ViewModel.acceptDashboardUrl("https://192.168.1.1/")
  assert.strictEqual(secure.accepted, true)
  assert.strictEqual(secure.warnPlainHttp, false)
})

test("more rejections that a narrower reading would have let through", () => {
  const bad = [
    "", null, undefined, 7, {},
    "example.com",                  // no scheme
    "//h/",                         // scheme-relative
    "https://",                     // no host
    "https:///path",                // no host
    "http://h" + String.fromCharCode(0) + "/",   // embedded NUL
    "https://h" + String.fromCharCode(10) + "/", // newline
    "https://h/?a=1;b=2",           // semicolon anywhere
    "HTTPS://u@h/",                 // userinfo with an upper-case scheme
    "ftp://h/",
    "data:text/html,x"
  ]
  for (const url of bad) {
    assert.strictEqual(ViewModel.acceptDashboardUrl(url).accepted, false,
      JSON.stringify(String(url)))
  }
  // Ordinary URLs must still work; a validator nobody can satisfy gets deleted.
  for (const url of ["https://h", "https://h/", "https://h:8443/network",
                     "https://192.168.1.1/proxy/network", "https://h/a?b=1&c=2#d"]) {
    assert.strictEqual(ViewModel.acceptDashboardUrl(url).accepted, true, url)
  }
})

test("REQ-012: the fallback derives https://<apiRootHost>, then disables", () => {
  assert.strictEqual(ViewModel.dashboardUrlFor("https://dash/", "1.2.3.4").url,
    "https://dash/", "a configured URL wins")

  const derived = ViewModel.dashboardUrlFor("", "192.168.1.1")
  assert.strictEqual(derived.accepted, true)
  assert.strictEqual(derived.url, "https://192.168.1.1")
  assert.strictEqual(derived.warnPlainHttp, false)

  // A bad configured value falls back rather than failing outright.
  assert.strictEqual(ViewModel.dashboardUrlFor("javascript:1", "192.168.1.1").url,
    "https://192.168.1.1")

  const none = ViewModel.dashboardUrlFor("", null)
  assert.strictEqual(none.accepted, false)
  assert.ok(none.reason.length > 0, "REQ-012: the button is disabled WITH a label")
})

// --- AC-071 (string half) ------------------------------------------------

test("AC-071: the relative-time formatter is correct at its boundaries", () => {
  const cases = [
    [-5, "now"], [0, "now"], [1, "in 1s"], [59, "in 59s"],
    [60, "in 1m"], [119, "in 1m"], [3599, "in 59m"],
    [3600, "in 1h"], [86399, "in 23h"], [86400, "in 1d"], [172800, "in 2d"]
  ]
  for (const item of cases) {
    assert.strictEqual(ViewModel.relativeFuture(item[0]), item[1], String(item[0]))
  }
  assert.strictEqual(ViewModel.relativeFuture(NaN), "unknown")
  assert.strictEqual(ViewModel.relativeFuture(null), "unknown")
})

test("the past formatter has its own boundaries and its own never", () => {
  const cases = [
    [0, "just now"], [9, "just now"], [10, "10s ago"], [59, "59s ago"],
    [60, "1m ago"], [3599, "59m ago"], [3600, "1h ago"],
    [86399, "23h ago"], [86400, "1d ago"]
  ]
  for (const item of cases) {
    assert.strictEqual(ViewModel.relativePast(item[0]), item[1], String(item[0]))
  }
  assert.strictEqual(ViewModel.relativePast(null), "never")
})

test("no formatter reaches for a locale", () => {
  // Banned by js_dialect.sh, asserted here too because the failure is silent:
  // V4 and V8 do not ship the same ICU data, so a locale-formatted string is
  // not reproducible across the two engines this corpus runs on.
  //
  // Whole-line comments are stripped first, exactly as gate_code_lines() does.
  // Without that, this test fails on the comment in ViewModel.js explaining why
  // the ban exists — and the tempting fix is to delete the explanation.
  const code = fs.readFileSync(path.resolve(__dirname, "../../ViewModel.js"), "utf8")
    .split("\n")
    .filter((line) => !/^\s*(\/\/|\/\*|\*)/.test(line))
    .join("\n")
  assert.strictEqual(code.indexOf("toLocaleString"), -1)
  assert.ok(!/[^A-Za-z]Intl\./.test(code))
})

// --- AC-062 --------------------------------------------------------------

test("AC-062: the tooltip covers all four cases", () => {
  const base = { siteName: "Home", healthLevel: "green" }

  const never = ViewModel.tooltip(
    Object.assign({}, base, { hasSnapshot: false, errorKind: null }))
  assert.ok(never.indexOf("Home") !== -1)
  assert.ok(never.indexOf("Last update: never") !== -1)
  assert.ok(never.indexOf("in progress") !== -1)

  const fresh = ViewModel.tooltip(Object.assign({}, base,
    { hasSnapshot: true, secondsSinceSuccess: 12, errorKind: null, isStale: false }))
  assert.ok(fresh.indexOf("12s ago") !== -1)
  assert.ok(fresh.indexOf("succeeded") !== -1)
  assert.ok(fresh.indexOf("Healthy") !== -1)

  // The one that matters: a failed refresh over a still-fresh snapshot must say
  // BOTH things. Collapsing it either way is REQ-004's whole failure mode —
  // the user either thinks the site is fine, or thinks it is down when only the
  // poll failed.
  const failedRefresh = ViewModel.tooltip(Object.assign({}, base,
    { hasSnapshot: true, secondsSinceSuccess: 90, errorKind: "network", isStale: false }))
  assert.ok(failedRefresh.indexOf("failed (network)") !== -1)
  assert.ok(failedRefresh.indexOf("Healthy as of the last update") !== -1)
  assert.ok(failedRefresh.indexOf("most recent refresh failed") !== -1)

  const stale = ViewModel.tooltip(Object.assign({}, base,
    { hasSnapshot: true, secondsSinceSuccess: 4000, errorKind: "timeout", isStale: true }))
  assert.ok(stale.indexOf("1h ago") !== -1)
  assert.ok(stale.indexOf(ViewModel.sentenceFor("stale")) !== -1)

  // All four must be distinguishable.
  assert.strictEqual(new Set([never, fresh, failedRefresh, stale]).size, 4)
})

// --- AC-067 --------------------------------------------------------------

test("AC-067: a null service resolves to service_unavailable", () => {
  // REQ-013b. `bar?.shell?.serviceFor(...)` is null on the first frame and stays
  // null if the service failed to construct. Returning a complete model turns a
  // blank widget or a binding error into a stated condition.
  const model = ViewModel.forNullService()
  assert.strictEqual(model.state, "service_unavailable")
  assert.strictEqual(model.sentence, ViewModel.sentenceFor("service_unavailable"))
  assert.strictEqual(model.healthLevel, "grey")
  assert.deepStrictEqual(model.rendering, ViewModel.LEVEL_RENDERING.grey)
  assert.ok(model.tooltip.length > 0)
  assert.strictEqual(model.compactText, "")
  for (const key of ["state", "sentence", "healthLevel", "rendering", "word",
                     "compactText", "hasSnapshot", "tooltip"]) {
    assert.ok(Object.prototype.hasOwnProperty.call(model, key),
      "the panel binds " + key + "; a missing key is the binding error this exists to avoid")
  }
})

// --- panelState ordering -------------------------------------------------

test("panelState is ordered: config fault > stale > transport error > empty", () => {
  const base = { serviceAvailable: true, hasSnapshot: true, devicesTotal: 5 }
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { reconfiguring: true, errorKind: "network" })), "reconfiguring")
  assert.strictEqual(ViewModel.panelState({ serviceAvailable: false }), "service_unavailable")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { errorKind: "configuration_conflict", isStale: true })), "configuration_conflict",
    "a configuration fault outranks staleness: polling is suspended")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { isStale: true, errorKind: "network" })), "stale")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { errorKind: "network" })), "network")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { devicesTotal: 0 })), "empty")
  assert.strictEqual(ViewModel.panelState(base), "ok")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { hasSnapshot: false, errorKind: null })), "loading")
  assert.strictEqual(ViewModel.panelState(Object.assign({}, base,
    { hasSnapshot: false, errorKind: "tls" })), "tls")
})

test("compactText honours REQ-005 and shows nothing for `none`", () => {
  assert.strictEqual(ViewModel.compactText("none", { clients: 42 }), "")
  assert.strictEqual(ViewModel.compactText("clients", { clients: 42 }), "42")
  assert.strictEqual(ViewModel.compactText("clients", { clients: 0 }), "0")
  assert.strictEqual(ViewModel.compactText("clients", { clients: null }), "unknown")
  assert.strictEqual(ViewModel.compactText("clients", null), "unknown")
})

// --- build: the composition Service.qml publishes -------------------------
//
// HC-16 forces the five modules to be called in order by QML, but the LAST of
// them decides what the panel shows — so it lives here, where node can execute
// it, rather than as a hand-assembled object literal inside a .qml file that
// only a live Wayland session can run.

const HEALTHY = {
  site: { id: "s-1", name: "Home" },
  wan: { status: "up", uptimeSec: 864000, downloadBps: 12000000, uploadBps: 3000000 },
  gateways: [{ id: "g-1", name: "UDM Pro", model: "UDM-Pro", state: "ONLINE",
               class: "online", uptimeSec: 864000, downloadBps: 12000000,
               uploadBps: 3000000 }],
  counts: {
    clients: 42, devicesTotal: 5, offlineTotal: 0,
    byClass: { online: 5, transitional: 0, down: 0, impaired: 0, unknown: 0 },
    gateways: { online: 1, transitional: 0, down: 0, impaired: 0, unknown: 0 },
    switches: { online: 2, transitional: 0, down: 0, impaired: 0, unknown: 0 },
    accessPoints: { online: 2, transitional: 0, down: 0, impaired: 0, unknown: 0 }
  },
  offlineDevices: [],
  applicationVersion: "9.1.0"
}

function build(overrides) {
  return ViewModel.build(Object.assign({
    snapshot: HEALTHY,
    meta: { apiRootHost: "192.0.2.9", helperVersion: "0.1.0" },
    level: { level: "green", rule: 5 },
    errorKind: null,
    error: null,
    warnings: [],
    isStale: false,
    settings: { refreshIntervalSec: 30, compactMetric: "clients", dashboardUrl: "" },
    pollingSuspended: false,
    nextAttemptAt: 1030,
    now: 1000,
    lastSuccessAt: 1767225500,
    nowWall: 1767225600
  }, overrides || {}))
}

test("build returns every key a widget binds to, on both paths", () => {
  // The first frame of a bar widget has `bar` null, so the panel binds to
  // forNullService()'s object; the frame after that it binds to build()'s. A
  // key present in one and not the other is a binding that becomes undefined
  // exactly once, which is the hardest kind of glitch to reproduce.
  const built = build()
  const empty = ViewModel.forNullService()
  assert.deepStrictEqual(Object.keys(built).sort(), Object.keys(empty).sort())
  for (const key of Object.keys(ViewModel.EMPTY_MODEL)) {
    assert.ok(key in built, "build is missing " + key)
    assert.ok(key in empty, "forNullService is missing " + key)
  }
})

test("build passes the health level through rather than deriving one", () => {
  // REQ-002 lives in Health.js. A second implementation here is how two
  // answers to one question start to exist.
  for (const level of ["green", "amber", "red", "grey"]) {
    const model = build({ level: { level: level, rule: 5 } })
    assert.strictEqual(model.healthLevel, level)
    assert.deepStrictEqual(model.rendering, ViewModel.LEVEL_RENDERING[level])
  }
})

test("build's panel state follows the documented ordering", () => {
  assert.strictEqual(build().state, "ok")
  assert.strictEqual(build({ isStale: true }).state, "stale")
  assert.strictEqual(build({ errorKind: "network" }).state, "network")
  // A configuration fault outranks a stale snapshot.
  assert.strictEqual(build({ isStale: true, errorKind: "unconfigured" }).state,
    "unconfigured")
  assert.strictEqual(build({ snapshot: null }).state, "loading")
  assert.strictEqual(build({ snapshot: null, errorKind: "tls" }).state, "tls")
  const emptySite = JSON.parse(JSON.stringify(HEALTHY))
  emptySite.counts.devicesTotal = 0
  assert.strictEqual(build({ snapshot: emptySite }).state, "empty")
})

test("AC-063: the offline line is computed from the total, not the array", () => {
  const large = JSON.parse(JSON.stringify(HEALTHY))
  large.counts.offlineTotal = 500
  large.counts.devicesTotal = 500
  large.offlineDevices = Array.from({ length: 10 }, (_, i) => ({
    id: "d-" + i, name: "Switch " + i, model: "USW", state: "OFFLINE", class: "down"
  }))
  const model = build({ snapshot: large })
  assert.strictEqual(model.offline.devices.length, 10)
  assert.strictEqual(model.offline.total, 500)
  assert.strictEqual(model.offline.moreLabel, "and 490 more")
})

test("AC-025: the role-count flag is set exactly when the rows over-count", () => {
  assert.strictEqual(build().roleCountsAreNotAPartition, false)
  const multi = JSON.parse(JSON.stringify(HEALTHY))
  multi.counts.devicesTotal = 3
  multi.counts.byClass.online = 3
  multi.counts.gateways.online = 1
  multi.counts.switches.online = 1
  multi.counts.accessPoints.online = 3
  assert.strictEqual(build({ snapshot: multi }).roleCountsAreNotAPartition, true)
})

test("REQ-011: Refresh is disabled with a reason while polling is suspended", () => {
  const running = build()
  assert.strictEqual(running.refreshEnabled, true)
  assert.strictEqual(running.refreshDisabledReason, "")

  const suspended = build({ pollingSuspended: true, errorKind: "uncommitted",
                            snapshot: null })
  assert.strictEqual(suspended.refreshEnabled, false)
  assert.strictEqual(suspended.refreshDisabledReason,
    ViewModel.sentenceFor("uncommitted"))
  assert.ok(suspended.refreshDisabledReason.length > 0,
    "a disabled control with no explanation is worse than no control")
})

test("UX-007: the next attempt is relative, and absent when suspended", () => {
  assert.strictEqual(build({ nextAttemptAt: 1120, now: 1000 }).nextAttemptText,
    "in 2m")
  // Suspended means there is no next attempt; a countdown to nothing is worse
  // than no countdown.
  assert.strictEqual(build({ pollingSuspended: true }).nextAttemptText, "")
  assert.strictEqual(build({ nextAttemptAt: null }).nextAttemptText, "")
})

test("BIZ-003: an unknown client count is unknown in the compact text", () => {
  const gaps = JSON.parse(JSON.stringify(HEALTHY))
  gaps.counts.clients = null
  assert.strictEqual(build({ snapshot: gaps }).compactText, "unknown")
  assert.strictEqual(build().compactText, "42")
  assert.strictEqual(build({ settings: { compactMetric: "none" } }).compactText, "")
})

test("the dashboard falls back to the apiRootHost meta carries", () => {
  // REQ-012: config.json is helper-only and QML cannot read it, so `meta` is
  // the only channel this can arrive by.
  const model = build({ settings: { compactMetric: "none", dashboardUrl: "" } })
  assert.strictEqual(model.dashboard.url, "https://192.0.2.9")
  const configured = build({
    settings: { compactMetric: "none", dashboardUrl: "https://unifi.example/" }
  })
  assert.strictEqual(configured.dashboard.url, "https://unifi.example/")
})

test("build never throws on a snapshot it has never seen before", () => {
  // It runs inside a QML property assignment; an exception there leaves the
  // service with no model and no way back (AC-046's spirit).
  for (const snapshot of [null, {}, { counts: null }, { counts: {} },
                          { site: null, counts: { devicesTotal: 1 } }]) {
    assert.doesNotThrow(() => ViewModel.build({ snapshot: snapshot, level: {} }),
      JSON.stringify(snapshot))
  }
  assert.doesNotThrow(() => ViewModel.build(null))
  assert.doesNotThrow(() => ViewModel.build({}))
})

// --- Phase 10: the rows the view binds to --------------------------------
//
// Everything below exists because CP7 requires the panel to render from `vm`
// with NO computation in QML. A string that is assembled in a .qml file is a
// string `node --test` cannot read, so each of these is the testable half of
// something the panel would otherwise be doing in a binding.

test("the ok state has no sentence, and every other state has one", () => {
  // The defect this pins: `panelState` returns "ok" for a healthy site, "ok" is
  // deliberately not in PANEL_SENTENCES (REQ-013 enumerates the twenty-four
  // conditions that need EXPLAINING, and healthy is not one), and
  // `sentenceFor`'s DATA-007 fallback maps anything unknown to `internal`.
  // Wired naively, a perfectly healthy panel reads "The plugin hit an internal
  // error."
  const healthy = build()
  assert.strictEqual(healthy.state, "ok")
  assert.strictEqual(healthy.sentence, "")
  assert.ok(!/internal error/i.test(healthy.sentence))

  for (const state of ViewModel.PANEL_STATES) {
    assert.ok(ViewModel.sentenceFor(state).length > 0, state)
  }
  assert.ok(build({ errorKind: "network" }).sentence.length > 0)
  assert.ok(build({ snapshot: null }).sentence.length > 0)
})

test("REQ-008: every WAN row is present, and an absent metric is unknown", () => {
  const rows = ViewModel.wanRows({ status: "up", uptimeSec: null,
    downloadBps: null, uploadBps: 0 })
  assert.deepStrictEqual(rows.map((r) => r.key),
    ["status", "uptime", "download", "upload"])
  assert.strictEqual(rows[0].value, "Up")
  // BIZ-003: null is "unknown"; a real zero is "0 bps". REQ-008 also forbids
  // dropping the row, which would read as "there is no uplink".
  assert.strictEqual(rows[1].value, "unknown")
  assert.strictEqual(rows[2].value, "unknown")
  assert.strictEqual(rows[3].value, "0 bps")
  assert.strictEqual(ViewModel.wanRows(null).length, 4)
  assert.strictEqual(ViewModel.wanRows(undefined)[0].value, "Unknown")
})

test("REQ-008a: the WAN status word is total over the domain", () => {
  assert.deepStrictEqual(Object.keys(ViewModel.WAN_STATUS_WORD).sort(),
    ["degraded", "down", "unknown", "up"])
  for (const status of ["up", "down", "degraded", "unknown"]) {
    assert.ok(ViewModel.wanStatusWord(status).length > 0, status)
  }
  // A newer helper must not brick an older panel.
  assert.strictEqual(ViewModel.wanStatusWord("flapping"), "Unknown")
  assert.strictEqual(ViewModel.wanStatusWord(undefined), "Unknown")
})

test("REQ-008a: each gateway is listed, and 'not fetched' is not 'unknown'", () => {
  const rows = ViewModel.gatewayRows([
    { id: "g-1", name: "UDM Pro", model: "UDM-Pro", class: "online",
      uptimeSec: 3600, downloadBps: 1000, uploadBps: 2000 },
    // Past REQ-008a's four-gateway statistics bound: never fetched, so all
    // three metrics are absent together. That is a different fact from one
    // metric failing, and the panel says so.
    { id: "g-5", name: null, model: null, class: "down",
      uptimeSec: null, downloadBps: null, uploadBps: null }
  ])
  assert.strictEqual(rows[0].nameText, "UDM Pro")
  assert.strictEqual(rows[0].classText, "online")
  assert.strictEqual(rows[0].hasMetrics, true)
  // A nameless device falls back to its id, which is identifiable; falling back
  // to "" would print a blank line the user cannot act on.
  assert.strictEqual(rows[1].nameText, "g-5")
  assert.strictEqual(rows[1].modelText, "unknown model")
  assert.strictEqual(rows[1].hasMetrics, false)
  assert.strictEqual(ViewModel.gatewayRows(null).length, 0)
  assert.strictEqual(ViewModel.gatewayRows("nonsense").length, 0)
})

test("REQ-009: role rows carry their non-empty classes in a fixed order", () => {
  const rows = ViewModel.countRows(HEALTHY.counts)
  assert.deepStrictEqual(rows.map((r) => r.key),
    ["gateways", "switches", "accessPoints"])
  for (const row of rows) {
    // SPEC-AMD-3 drops the empty classes; it does not reorder the survivors.
    // The order is fixed deliberately, because `for (key in obj)` is not
    // required to agree between V4 and V8 and a row whose columns move between
    // renders is unreadable.
    const order = row.cells.map((c) => c.key)
    const expected = ViewModel.CLASS_ORDER.filter((k) => order.indexOf(k) !== -1)
    assert.deepStrictEqual(order, expected)
    // And nothing that survived is zero.
    for (const cell of row.cells) assert.ok(cell.value > 0)
  }
  assert.strictEqual(rows[2].total, 2)

  // SPEC-AMD-3's two boundaries. `total` is summed over all five classes
  // BEFORE the filter, so it keeps meaning "devices in this role" and AC-025's
  // not-a-partition arithmetic is unaffected by what is displayed.
  const oneDown = ViewModel.countRows({
    gateways: { online: 0, transitional: 0, down: 2, impaired: 0, unknown: 0 },
    switches: { online: 0, transitional: 0, down: 0, impaired: 0, unknown: 0 }
  })
  assert.strictEqual(oneDown.length, 1, "a role with no devices contributes no row")
  assert.strictEqual(oneDown[0].key, "gateways")
  assert.deepStrictEqual(oneDown[0].cells.map((c) => c.value), [2])
  assert.strictEqual(oneDown[0].cells[0].key, "down")
  // The one thing the filter must never do: hide a device that is not well.
  assert.strictEqual(oneDown[0].total, 2)

  // A missing bucket is skipped rather than rendered as five zeros, which
  // would claim the controller reported something it did not.
  assert.strictEqual(ViewModel.countRows({ gateways: null }).length, 0)
  assert.strictEqual(ViewModel.countRows(null).length, 0)
})

test("AC-025: the not-a-partition note names the unique total", () => {
  const counts = JSON.parse(JSON.stringify(HEALTHY.counts))
  counts.devicesTotal = 3
  counts.accessPoints.online = 3
  const model = ViewModel.build({ snapshot: Object.assign({}, HEALTHY,
    { counts: counts }), level: { level: "green" } })
  assert.strictEqual(model.roleCountsAreNotAPartition, true)
  assert.ok(model.roleCountsNote.indexOf("3 adopted devices") !== -1,
    "the note must name the total the rows do not sum to: " + model.roleCountsNote)
  // Silent when the rows do happen to agree, so it never explains away a
  // discrepancy that is not there.
  assert.strictEqual(build().roleCountsAreNotAPartition, false)
  assert.strictEqual(build().roleCountsNote, "")
  const one = { devicesTotal: 1, gateways: { online: 2 } }
  assert.ok(ViewModel.roleCountsNote(one).indexOf("1 adopted device.") !== -1,
    "singular: " + ViewModel.roleCountsNote(one))
})

test("REQ-010: an offline row carries the words the panel prints", () => {
  const model = ViewModel.build({
    snapshot: {
      counts: { devicesTotal: 3, offlineTotal: 2 },
      offlineDevices: [
        { id: "d-1", name: "Garage AP", model: "U6-Lite", class: "down" },
        { id: "d-2", name: "", model: "", class: "impaired" }
      ]
    },
    level: { level: "amber" }
  })
  assert.strictEqual(model.offline.devices[0].nameText, "Garage AP")
  assert.strictEqual(model.offline.devices[0].classText, "down")
  assert.strictEqual(model.offline.devices[1].nameText, "d-2")
  assert.strictEqual(model.offline.devices[1].modelText, "unknown model")
  assert.strictEqual(model.offline.devices[1].classText, "impaired")
  assert.strictEqual(ViewModel.displayName({}), "unnamed device")
  // REQ-003: an unrecognised state is reported as unknown, never as the empty
  // string, which would read as "fine".
  assert.strictEqual(ViewModel.classWord("MOON_PHASE"), "unknown")
  assert.strictEqual(ViewModel.classWord(undefined), "unknown")
})

test("AC-052: every meta row is present, and a null field reads unknown", () => {
  // The two failure kinds that most need a `meta` — unconfigured and
  // uncommitted — are precisely the ones where the configuration could not be
  // read, so this is the shape the panel sees when it has least to say.
  const rows = ViewModel.metaRows({ commitGeneration: null, apiRootHost: null,
    siteId: null, allowInsecureTls: null, customCaInUse: null,
    helperVersion: "0.1.0" })
  assert.deepStrictEqual(rows.map((r) => r.key),
    ["site", "apiRootHost", "siteId", "helperVersion", "commitGeneration"])
  assert.strictEqual(rows[0].value, "unknown")
  assert.strictEqual(rows[1].value, "unknown")
  assert.strictEqual(rows[3].value, "0.1.0")
  assert.strictEqual(ViewModel.metaRows(null).length, 5)
  // A commit generation of 0 is a real generation, not an absent one.
  assert.strictEqual(ViewModel.metaRows({ commitGeneration: 0 })[4].value, "0")

  // DATA-012's auto-selection is disclosed HERE rather than as a permanent
  // entry in a list headed "Warnings", for a decision that needs no action.
  const auto = ViewModel.metaRows({}, { site: { id: "s", name: "Home" } },
    [{ code: "site_auto_selected", message: "x" }])
  assert.strictEqual(auto[0].value, "Home (auto-selected)")
  const chosen = ViewModel.metaRows({}, { site: { id: "s", name: "Home" } }, [])
  assert.strictEqual(chosen[0].value, "Home")
  // Never "unknown (auto-selected)": with no site there is nothing to disclose.
  assert.strictEqual(
    ViewModel.metaRows({}, null, [{ code: "site_auto_selected" }])[0].value, "unknown")
})

test("UX-009: the insecure-TLS flag is a boolean on every path", () => {
  // Bound to one property with no `&&` in it, so there is no arrangement of a
  // null meta in which the row quietly becomes undefined instead of false.
  assert.strictEqual(build({ meta: { allowInsecureTls: true } }).insecureTls, true)
  assert.strictEqual(build({ meta: { allowInsecureTls: null } }).insecureTls, false)
  assert.strictEqual(build({ meta: null }).insecureTls, false)
  assert.strictEqual(ViewModel.forNullService().insecureTls, false)
  assert.strictEqual(build({ meta: { customCaInUse: true } }).customCaInUse, true)
  // AC-070: it is present on a FAILURE envelope too, which is the case where
  // no batch has ever succeeded and the user most needs to know.
  const failing = build({ snapshot: null, errorKind: "tls",
    meta: { allowInsecureTls: true } })
  assert.strictEqual(failing.insecureTls, true)
})

test("REQ-013a: a warning row keeps its code beside its sentence", () => {
  const rows = ViewModel.warningRows([
    { code: "insecure_tls", message: "TLS verification is disabled." },
    { code: "clients_unavailable", message: "" },
    // The same code twice, which is the case the key has to survive:
    // `statistics_unavailable` is raised once per gateway, so a Repeater keyed
    // on the code alone would collapse four warnings about four different
    // gateways into one row.
    { code: "statistics_unavailable", message: "Gateway A has no statistics." },
    { code: "statistics_unavailable", message: "Gateway B has no statistics." },
    null
  ])
  // `insecure_tls` is dropped: UX-009 gives it a permanent row of its own, and
  // repeating it here was the same sentence twice on screen.
  assert.strictEqual(rows.length, 4)
  assert.ok(rows.every((r) => r.code !== "insecure_tls"))
  // A code with no message still prints something actionable rather than a
  // blank row.
  assert.strictEqual(rows[0].text, "clients_unavailable")
  assert.strictEqual(rows[3].code, "unknown")
  assert.strictEqual(rows[1].code, rows[2].code)
  assert.notStrictEqual(rows[1].text, rows[2].text)
  const keys = rows.map((r) => r.key)
  assert.strictEqual(new Set(keys).size, keys.length,
    "two warnings sharing a code must not share a Repeater key")
  assert.strictEqual(ViewModel.warningRows(null).length, 0)
})

test("UX-006a: the discovered sites come out of the warning that carries them", () => {
  const sites = ViewModel.sitesFromWarnings([
    { code: "insecure_tls", message: "x" },
    // DATA-012 puts the pairs under ONE code. A different warning carrying a
    // `sites` detail is not a site list — reading every warning's detail would
    // let an unrelated code populate UX-006a's "run scripts/configure --site
    // <id>" list with ids that are not sites.
    { code: "site_auto_selected", message: "z",
      detail: { sites: [{ id: "99999999-9999-4999-8999-999999999999",
                          name: "Not a discovery" }] } },
    { code: "sites_discovered", message: "y", detail: { sites: [
      { id: "11111111-1111-4111-8111-111111111111", name: "Home" },
      { id: "22222222-2222-4222-8222-222222222222", name: null },
      { id: "", name: "no id" },
      { name: "no id at all" }
    ] } }
  ])
  assert.strictEqual(sites.length, 2)
  assert.ok(sites.every((site) => site.name !== "Not a discovery"),
    "only sites_discovered carries the DATA-012 pairs")
  assert.strictEqual(sites[0].name, "Home")
  assert.ok(sites[0].label.indexOf("11111111-1111-4111-8111-111111111111") !== -1,
    "UX-006a lists the id, because that is what --site takes")
  assert.strictEqual(sites[1].name, "unnamed site")
  assert.deepStrictEqual(ViewModel.sitesFromWarnings([]), [])
  assert.deepStrictEqual(ViewModel.sitesFromWarnings(null), [])
  assert.deepStrictEqual(
    ViewModel.sitesFromWarnings([{ code: "sites_discovered", detail: null }]), [])
})

test("the hero headline states the level in words on both paths", () => {
  // UX-002: colour is never the sole signal.
  assert.strictEqual(ViewModel.headline("red", false, "never"),
    "Down  ·  never updated")
  assert.ok(ViewModel.headline("green", true, "2m ago").indexOf("2m ago") !== -1)
  assert.ok(ViewModel.forNullService().headline.length > 0)
  assert.ok(build().headline.indexOf("Healthy") === 0)
  // The INPUT KEY is part of the contract with Service.qml, which is a
  // different file in a different language and gets no error at all for
  // passing a property that does not exist. Reading the wrong name shipped a
  // panel that displayed live device counts under the words "never updated".
  assert.strictEqual(build({ lastSuccessAt: 1767225580 }).lastUpdateText, "20s ago")
  assert.ok(build({ lastSuccessAt: 1767225580 }).headline.indexOf("20s ago") !== -1)
  assert.strictEqual(build({ lastSuccessAt: null }).lastUpdateText, "never")
})

test("build's error message is a string on every path", () => {
  assert.strictEqual(build().errorMessage, "")
  assert.strictEqual(build({ error: { kind: "tls", message: "bad cert" } }).errorMessage,
    "bad cert")
  assert.strictEqual(build({ error: { kind: "tls" } }).errorMessage, "")
  assert.strictEqual(build({ error: null }).errorMessage, "")
})

test("every key the view binds to survives a snapshot-less model", () => {
  // The Phase 10 view binds around forty properties. A key present in `build`
  // and absent from EMPTY_MODEL becomes `undefined` on exactly the frame
  // REQ-013b exists for, which is the frame nobody tests by hand.
  const full = build()
  const empty = ViewModel.forNullService()
  for (const key of Object.keys(full)) {
    assert.ok(Object.prototype.hasOwnProperty.call(empty, key),
      "forNullService is missing " + key)
    assert.ok(Object.prototype.hasOwnProperty.call(ViewModel.EMPTY_MODEL, key),
      "EMPTY_MODEL is missing " + key)
  }
  for (const key of Object.keys(ViewModel.EMPTY_MODEL)) {
    assert.ok(Object.prototype.hasOwnProperty.call(full, key),
      "build is missing " + key)
  }
  // The array-valued keys must be arrays, not null: a Repeater bound to null
  // is a warning per delegate rather than an empty list.
  for (const key of ["wanRows", "gatewayRows", "countRows", "warningRows",
                     "sites", "metaRows", "gateways", "warnings"]) {
    assert.ok(Array.isArray(empty[key]), key + " must be an array when empty")
    assert.ok(Array.isArray(full[key]), key + " must be an array when populated")
  }
})

test("the Site id row shows the site actually in use, not a null committed one", () => {
  // DATA-012 auto-selects a single site, and `meta.siteId` stays null because
  // it reflects the COMMITTED configuration. The panel was then reporting
  // "Site id: unknown" for a site whose name it was displaying two rows above.
  const snapshot = Object.assign({}, HEALTHY,
    { site: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Default" } })
  const auto = ViewModel.metaRows({ siteId: null }, snapshot)
  assert.strictEqual(auto[2].value, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
  // A committed id stays the authority: it is what the user configured, and if
  // it disagreed with the snapshot that is worth seeing rather than hiding.
  const committed = ViewModel.metaRows({ siteId: "committed-id" }, snapshot)
  assert.strictEqual(committed[2].value, "committed-id")
  // And with neither, "unknown" is the honest answer.
  assert.strictEqual(ViewModel.metaRows({ siteId: null }, null)[2].value, "unknown")
  assert.strictEqual(ViewModel.metaRows(null)[2].value, "unknown")
  // Reachable through build, which is how the panel gets it.
  const model = ViewModel.build({ snapshot: snapshot, meta: { siteId: null },
    level: { level: "green" } })
  assert.strictEqual(model.metaRows[2].value, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
})

test("a warning with its own panel row is not repeated in the warning list", () => {
  // Three codes the panel renders through a dedicated affordance. Leaving them
  // in the list put the same sentence on screen twice — and made DATA-012's
  // correct, actionless auto-selection a permanent entry under "Warnings".
  assert.deepStrictEqual(ViewModel.WARNINGS_WITH_THEIR_OWN_ROW.slice().sort(),
    ["custom_ca_in_use", "insecure_tls", "site_auto_selected"])
  const warnings = [
    { code: "site_auto_selected", message: "chose the only site" },
    { code: "custom_ca_in_use", message: "a custom CA is in use" },
    { code: "insecure_tls", message: "verification is off" },
    { code: "clients_unavailable", message: "client count unavailable" }
  ]
  const rows = ViewModel.warningRows(warnings)
  assert.deepStrictEqual(rows.map((r) => r.code), ["clients_unavailable"])
  // Dropped from the list, NOT from the model: each still drives its own row.
  const model = ViewModel.build({ warnings: warnings,
    meta: { allowInsecureTls: true, customCaInUse: true },
    snapshot: { site: { id: "s", name: "Home" }, counts: { devicesTotal: 1 } },
    level: { level: "green" } })
  assert.strictEqual(model.insecureTls, true)
  assert.strictEqual(model.customCaInUse, true)
  assert.strictEqual(model.metaRows[0].value, "Home (auto-selected)")
  assert.strictEqual(model.warningRows.length, 1)
  // And the raw list is untouched, because the service and `status` use it.
  assert.strictEqual(model.warnings.length, 4)
})
