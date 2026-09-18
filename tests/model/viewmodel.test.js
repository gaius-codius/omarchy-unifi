// AC-011 (AUTO half), AC-062, AC-063, AC-064, AC-066, AC-067, AC-071 (string half).

// REQ-B14's absolute instant renders in LOCAL time, so every assertion over it
// would otherwise depend on where the test ran. Pinned before anything reads a
// clock, and pinned to a zone chosen for what it can catch:
//
//   * +05:30, so a mutation back to `getUTC*` shifts the HOUR and the MINUTE.
//     A whole-hour zone would let a half-applied offset through.
//   * no DST, so the expected string does not depend on the date in the fixture.
//
// `node --test` runs each file in its own process, so this does not leak into
// the other model suites. `formatInstant` asserts the pin took effect — a
// missing tzdata would silently make local time equal UTC and every assertion
// here would pass while proving nothing.
process.env.TZ = "Asia/Kolkata"

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
               class: "online",
               metrics: { uptimeSec: 864000, downloadBps: 12000000,
                          uploadBps: 3000000 } }],
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

test("UX-007: the next attempt is relative, and shown only while backing off", () => {
  assert.strictEqual(
    build({ backingOff: true, nextAttemptAt: 1120, now: 1000 }).nextAttemptText,
    "in 2m")
  // SPEC-AMD-4. A healthy widget on a 30 s interval always has a next attempt
  // and never needs to say so. The line answers "are we stuck?", which is only
  // a question once something has failed.
  assert.strictEqual(
    build({ backingOff: false, nextAttemptAt: 1120, now: 1000 }).nextAttemptText, "")
  assert.strictEqual(build({ nextAttemptAt: 1120, now: 1000 }).nextAttemptText, "",
    "absent by default, so a caller that forgets the flag shows less, not more")
  // Suspended means there is no next attempt; a countdown to nothing is worse
  // than no countdown. Asserted with `backingOff` ON, so suspension is doing
  // the work rather than being masked by the new condition.
  assert.strictEqual(
    build({ backingOff: true, pollingSuspended: true }).nextAttemptText, "")
  assert.strictEqual(build({ backingOff: true, nextAttemptAt: null }).nextAttemptText, "")
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
      metrics: { uptimeSec: 3600, downloadBps: 1000, uploadBps: 2000 } },
    // Past REQ-008a's four-gateway statistics bound: no body was obtained, so
    // the CONTAINER is null. That is a different fact from one metric failing,
    // and a different fact again from a body that arrived carrying nothing.
    { id: "g-5", name: null, model: null, class: "down", metrics: null }
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

test("REQ-008a / REQ-014: a metric-less gateway says which kind of nothing it is", () => {
  // The panel composed this sentence itself and had one branch for three
  // metrics being null: "statistics not fetched". BIZ-004 makes
  // `statistics/latest` optional PER DEVICE, so a gateway whose statistics
  // call was made and REFUSED landed in that same branch and the panel told
  // the user the helper had never asked — pointing them at the plugin for a
  // fault that is on the controller. The sentence lives here now, and it says
  // only what the envelope proves.
  const gateways = [
    { id: "g-1", name: "UDM Pro", model: "UDM-Pro", class: "online",
      metrics: { uptimeSec: 3600, downloadBps: 1000, uploadBps: 2000 } },
    { id: "g-2", name: "USG Backup", model: "USG-3P", class: "down",
      metrics: null },
    { id: "g-3", name: "USG Spare", model: "USG-3P", class: "online",
      metrics: null }
  ]
  // protocol-v1.md's table: `statistics_unavailable` is raised only for a
  // device whose `statistics/latest` request was MADE and failed, and its
  // detail names that device. It is the whole of the evidence.
  const rows = ViewModel.gatewayRows(gateways, [
    { code: "statistics_unavailable",
      message: "Gateway statistics unavailable; metrics shown as unknown.",
      detail: { deviceId: "g-3" } }
  ])

  assert.strictEqual(rows[0].metricsState, "reported")
  assert.strictEqual(rows[0].detailText,
    "UDM-Pro  ·  up 1h 0m  ·  1 kbps down  ·  2 kbps up")

  // Nothing names g-2, so the model does not know whether anyone asked — and
  // the row must not guess in either direction.
  assert.strictEqual(rows[1].metricsState, "absent")
  assert.strictEqual(rows[1].detailText, "USG-3P  ·  no statistics in this reading")
  assert.ok(!/fetch/.test(rows[1].detailText),
    "a row with no evidence must not claim the helper did or did not ask")

  // g-3 is named, so the request was made and failed. Distinct wording,
  // because "we did not ask" and "we asked and were refused" send the user to
  // different places.
  assert.strictEqual(rows[2].metricsState, "unavailable")
  assert.strictEqual(rows[2].detailText, "USG-3P  ·  statistics unavailable")
  assert.notStrictEqual(rows[1].detailText, rows[2].detailText)

  // The warning must name the device to count. A detail-less one is evidence
  // about nothing in particular and may not silently mark the first gateway.
  const vague = ViewModel.gatewayRows(gateways,
    [{ code: "statistics_unavailable", message: "x", detail: null }])
  assert.deepStrictEqual(vague.map((r) => r.metricsState),
    ["reported", "absent", "absent"])

  // `device_detail_unavailable` is raised when route 6 OR the statistics call
  // failed, and when route 6 is the one that failed the statistics call was
  // never made. Reading it as proof of a refused statistics request would put
  // the panel back to asserting what it cannot know.
  const detailFailed = ViewModel.gatewayRows(gateways,
    [{ code: "device_detail_unavailable", message: "x",
       detail: { deviceId: "g-2" } }])
  assert.strictEqual(detailFailed[1].metricsState, "absent")

  // A device id that is also an Object.prototype member must not answer true
  // out of the prototype chain.
  assert.strictEqual(
    ViewModel.gatewayRows([{ id: "constructor", model: "m", class: "online",
      metrics: null }], [])[0].metricsState,
    "absent")

  assert.deepStrictEqual(Object.keys(ViewModel.statisticsFailedIds([
    { code: "statistics_unavailable", message: "x", detail: { deviceId: "g-9" } },
    { code: "statistics_unavailable", message: "x", detail: { deviceId: "" } },
    null
  ])), ["g-9"])

  // And it reaches the panel through `build`, which is the only route the
  // view has to it.
  const model = ViewModel.build({
    snapshot: { site: { id: "s", name: "Home" }, gateways: gateways,
                counts: { devicesTotal: 3 } },
    warnings: [{ code: "statistics_unavailable", message: "x",
                 detail: { deviceId: "g-3" } }],
    level: { level: "amber" }
  })
  assert.deepStrictEqual(model.gatewayRows.map((r) => r.metricsState),
    ["reported", "absent", "unavailable"])
})

test("REQ-008a: an empty statistics body is not a missing one", () => {
  // The defect the `metrics` container was added to make unrepresentable.
  // `collect` records a gateway's statistics only on success, so `metrics:
  // null` is "no body was obtained" — and while the three figures sat INLINE
  // on the record, a body that arrived carrying nothing encoded to exactly the
  // same three nulls. The panel then told a user whose controller had answered
  // that no statistics had been fetched, and sent them to look at the plugin.
  //
  // Every assertion in this test passes on the flat shape only by accident of
  // wording; the two rows below were one row before the container existed.
  const rows = ViewModel.gatewayRows([
    // The controller answered and reported nothing. A statement about the
    // CONTROLLER.
    { id: "g-1", name: "UDM Pro", model: "UDM-Pro", class: "online",
      metrics: { uptimeSec: null, downloadBps: null, uploadBps: null } },
    // No body arrived. A statement about the HELPER.
    { id: "g-2", name: "USG Backup", model: "USG-3P", class: "online",
      metrics: null }
  ], [])

  assert.strictEqual(rows[0].metricsState, "empty")
  assert.strictEqual(rows[1].metricsState, "absent")
  assert.notStrictEqual(rows[0].detailText, rows[1].detailText,
    "a controller that answered emptily and a helper that never asked "
      + "must not render as the same sentence")
  assert.strictEqual(rows[0].detailText, "UDM-Pro  ·  statistics reported no figures")
  assert.strictEqual(rows[1].detailText, "USG-3P  ·  no statistics in this reading")

  // `hasMetrics` keeps its meaning — "there are figures to print" — so the
  // view's numeric rows stay empty for both. It is `metricsState` that carries
  // the new distinction, which is what keeps the caption honest without making
  // the panel print three "unknown"s.
  assert.strictEqual(rows[0].hasMetrics, false)
  assert.strictEqual(rows[1].hasMetrics, false)
  assert.strictEqual(rows[0].uptimeText, "unknown")

  // A body that arrived carrying ONE figure is `reported`: BIZ-003 makes each
  // field independently nullable, and the row prints what it has.
  const partial = ViewModel.gatewayRows([
    { id: "g-3", model: "UXG-Pro", class: "online",
      metrics: { uptimeSec: 3600, downloadBps: null, uploadBps: null } }
  ], [])
  assert.strictEqual(partial[0].metricsState, "reported")
  assert.strictEqual(partial[0].detailText,
    "UXG-Pro  ·  up 1h 0m  ·  unknown down  ·  unknown up")

  // A refused request outranks nothing: the warning names a gateway whose
  // container is null, and cannot contradict a body that arrived.
  const named = ViewModel.gatewayRows([
    { id: "g-1", model: "UDM-Pro", class: "online",
      metrics: { uptimeSec: null, downloadBps: null, uploadBps: null } },
    { id: "g-2", model: "USG-3P", class: "online", metrics: null }
  ], [{ code: "statistics_unavailable", message: "x", detail: { deviceId: "g-1" } },
      { code: "statistics_unavailable", message: "x", detail: { deviceId: "g-2" } }])
  assert.deepStrictEqual(named.map((r) => r.metricsState), ["empty", "unavailable"])

  // The figures are read from the CONTAINER and never from the record. A
  // producer that kept the old inline fields alongside a null container is
  // exactly the two-sources-of-truth case this shape removed, and the row must
  // believe the container.
  const stale = ViewModel.gatewayRows([
    { id: "g-9", model: "UXG-Pro", class: "online", metrics: null,
      uptimeSec: 999999, downloadBps: 5, uploadBps: 5 }
  ], [])
  assert.strictEqual(stale[0].metricsState, "absent")
  assert.strictEqual(stale[0].hasMetrics, false)
  assert.strictEqual(stale[0].uptimeText, "unknown")

  // And a container that is not an object at all — which `checkGatewayRecord`
  // has already refused at the wire — must not be read as a body either.
  for (const junk of [42, "none", true]) {
    assert.strictEqual(
      ViewModel.gatewayRows([{ id: "g-x", model: "m", class: "online",
        metrics: junk }], [])[0].metricsState, "absent", String(junk))
  }
})

test("REQ-008a: the three states reach the panel from a real envelope", () => {
  // End to end from the corpus, because the four states above are only worth
  // having if the helper's own output produces them. `success_gateway_metrics_states`
  // is normalize.py's output asserted byte for byte by the Python suite.
  const envelope = JSON.parse(fs.readFileSync(
    path.join(ACCEPT, "success_gateway_metrics_states.json"), "utf8"))
  const model = ViewModel.build({
    snapshot: envelope.data,
    warnings: envelope.warnings,
    level: { level: "green" }
  })
  assert.deepStrictEqual(model.gatewayRows.map((r) => r.metricsState),
    ["reported", "empty", "unavailable", "reported", "absent"])
  // Gateway 2 answered emptily and gateway 5 was never asked. Distinct
  // sentences, which is the whole of the change the envelope shape bought.
  assert.notStrictEqual(model.gatewayRows[1].detailText,
    model.gatewayRows[4].detailText)
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

test("F18: the hero's verdict and its timestamp are separate strings", () => {
  // PanelHero renders `meta` in tracked small caps, the loudest treatment in
  // the panel. `headline` handed it both facts at once, so the timestamp
  // shouted as loud as the verdict.
  const m = build()
  assert.strictEqual(m.headlineWord, "Healthy")
  assert.ok(m.headlineDetail.indexOf("updated") === 0, m.headlineDetail)
  assert.strictEqual(m.headlineDetail.indexOf("Healthy"), -1,
    "the detail line must not repeat the verdict")

  // `headline` is unchanged: the tooltip and a surface with one line still
  // want both facts in one string, and splitting is a rendering choice rather
  // than a change to what the panel knows.
  assert.ok(m.headline.indexOf("Healthy") !== -1)
  assert.ok(m.headline.indexOf("updated") !== -1)

  // The never-updated case keeps its wording in both shapes.
  const cold = ViewModel.forNullService()
  assert.strictEqual(cold.headlineDetail, "never updated")
  assert.strictEqual(cold.headlineWord, ViewModel.headlineWord("grey"))

  // Present and a string on every path, so the hero never binds to undefined.
  for (const model of [m, cold, ViewModel.EMPTY_MODEL]) {
    assert.strictEqual(typeof model.headlineWord, "string")
    assert.strictEqual(typeof model.headlineDetail, "string")
  }
})

test("F03: the offline section states the good case and never heads an empty list", () => {
  // A healthy site says so. The panel used to answer "is anything broken?" by
  // hiding the section, and a reader cannot tell an absent section from a
  // rendering fault or a poll that never arrived.
  const clear = ViewModel.offlineList({ offlineDevices: [], counts: { offlineTotal: 0 } })
  assert.strictEqual(clear.summaryText, "Nothing offline or impaired")
  assert.strictEqual(clear.devices.length, 0)

  // A count with no array. This is the state the shipped Overview screenshot
  // is in, and it used to draw a separator, a heading, and nothing else. The
  // count is still reported — as a sentence, not as a heading over emptiness.
  const countOnly = ViewModel.offlineList({ offlineDevices: [], counts: { offlineTotal: 5 } })
  assert.strictEqual(countOnly.summaryText, "5 devices offline or impaired")
  assert.strictEqual(countOnly.devices.length, 0)
  const one = ViewModel.offlineList({ offlineDevices: [], counts: { offlineTotal: 1 } })
  assert.strictEqual(one.summaryText, "1 device offline or impaired")

  // With rows to show, the list and its "and N more" line speak and the
  // sentence gets out of the way — the section must not say the same thing
  // twice in a panel this short of room.
  const listed = ViewModel.offlineList({
    offlineDevices: [{ id: "d1", name: "Garage AP", class: "down" }],
    counts: { offlineTotal: 1 }
  })
  assert.strictEqual(listed.summaryText, "")
  assert.strictEqual(listed.devices.length, 1)

  // No reading at all is not an all-clear: nothing has been checked, so the
  // section has nothing to say and the panel hides it.
  assert.strictEqual(ViewModel.EMPTY_MODEL.offline.summaryText, "")
  assert.strictEqual(ViewModel.forNullService().offline.summaryText, "")
  // And not from `build` either. The two constants above are what the panel
  // shows before the service exists; `build` is what it shows for every
  // failure after that, and it used to read a null snapshot's total as zero
  // and draw the all-clear under the error sentence.
  for (const kind of ["network", "tls", "auth"]) {
    const failed = ViewModel.build({ error: { kind: kind } })
    assert.strictEqual(failed.hasSnapshot, false, kind)
    assert.strictEqual(failed.offline.summaryText, "", kind)
    assert.strictEqual(failed.offline.devices.length, 0, kind)
  }
})

test("F10: a role row carries its cells as one right-hand value", () => {
  // The Overview role rows render label-left / value-right, the same shape as
  // "Connected clients" four rows above them. Joining the cells is a wording
  // decision, so it is made here rather than by a Row of Texts in the delegate
  // (REQ-014) — and asserting it here is what stops the separator and the
  // ordering drifting when someone edits the QML.
  const healthy = ViewModel.countRows(HEALTHY.counts)
  const aps = healthy.find((r) => r.key === "accessPoints")
  assert.strictEqual(aps.valueText, "2 online")

  // A mixed role keeps every non-zero class, in CLASS_ORDER, joined rather
  // than stacked. This is the case the right-hand column exists for.
  const mixed = ViewModel.countRows({
    switches: { online: 2, transitional: 0, down: 1, impaired: 0, unknown: 0 }
  })
  assert.strictEqual(mixed[0].valueText, "2 online  \u00b7  1 down")

  // `cells` is unchanged by the addition: the tests above read it, and a
  // future column may want the parts back rather than the sentence.
  assert.deepStrictEqual(mixed[0].cells.map((c) => c.key), ["online", "down"])

  // Every row has one, so the delegate never binds to undefined.
  for (const row of healthy) assert.strictEqual(typeof row.valueText, "string")
  for (const row of healthy) assert.notStrictEqual(row.valueText, "")
})

test("AC-025: the flag is exposed, and the panel carries the unique total", () => {
  // AC-025 asks for two things: the model exposes `roleCountsAreNotAPartition`,
  // and the panel label carries the unique total. Both still hold — the total is
  // its own labelled row ("Adopted devices"), which is where it always was.
  //
  // What AC-025 never asked for is the SENTENCE that used to sit under the role
  // rows explaining the arithmetic. It was removed at the user's request
  // (N-125), so there is nothing left to assert about its wording.
  const counts = JSON.parse(JSON.stringify(HEALTHY.counts))
  counts.devicesTotal = 3
  counts.accessPoints.online = 3
  const model = ViewModel.build({ snapshot: Object.assign({}, HEALTHY,
    { counts: counts }), level: { level: "green" } })
  assert.strictEqual(model.roleCountsAreNotAPartition, true)
  assert.strictEqual(model.devicesTotalText, "3")
  // The rows really do out-total it, which is what makes the flag true rather
  // than a constant.
  const summed = model.countRows.reduce((n, row) => n + row.total, 0)
  assert.ok(summed > 3, "the role rows must out-total the unique count: " + summed)
  // And false when they do agree, so it is not simply always on.
  assert.strictEqual(build().roleCountsAreNotAPartition, false)
  // The removed sentence is gone from the model, not merely unbound in the QML.
  assert.strictEqual(model.roleCountsNote, undefined)
  assert.strictEqual(typeof ViewModel.roleCountsNote, "undefined")
  assert.strictEqual(JSON.stringify(model).indexOf("counted in every role"), -1)
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

test("informational and separately rendered warnings are not in the warning list", () => {
  // A pinned custom CA needs no panel warning. The other two codes have a
  // dedicated affordance, so listing them would duplicate the same fact.
  assert.deepStrictEqual(ViewModel.WARNINGS_NOT_LISTED.slice().sort(),
    ["custom_ca_in_use", "insecure_tls", "site_auto_selected"])
  const warnings = [
    { code: "site_auto_selected", message: "chose the only site" },
    { code: "custom_ca_in_use", message: "a custom CA is in use" },
    { code: "insecure_tls", message: "verification is off" },
    { code: "clients_unavailable", message: "client count unavailable" }
  ]
  const rows = ViewModel.warningRows(warnings)
  assert.deepStrictEqual(rows.map((r) => r.code), ["clients_unavailable"])
  // Dropped from the list, NOT from the model or protocol.
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

test("DATA-002a: the service's own settings warnings reach the panel's list", () => {
  // protocol-v1.md, `warnings`: the service appends warnings for the
  // conditions it owns "so the panel has one list to render". It did not. The
  // service computed them, stored them where only the `status` IPC handler
  // looked, and the panel's Warnings section stayed empty — so a shell.json
  // saying `"refreshIntervalSec": "30"` polled on the 30 s DEFAULT with
  // nothing on screen to say the typed value had been thrown away.
  const settingsWarnings = [
    { code: "settings_invalid",
      message: "refreshIntervalSec must be a whole number of seconds; using 30.",
      detail: { key: "refreshIntervalSec", received: "string \"30\"" } }
  ]
  const model = ViewModel.build({
    snapshot: { site: { id: "s", name: "Home" }, counts: { devicesTotal: 1 } },
    level: { level: "green" },
    warnings: [{ code: "clients_unavailable", message: "Client list unavailable." }],
    settingsWarnings: settingsWarnings
  })
  const codes = model.warningRows.map((r) => r.code)
  assert.ok(codes.indexOf("settings_invalid") !== -1,
    "a settings warning the service computed must be on screen")
  // Appended, in protocol-v1.md's word: the envelope's own warnings keep their
  // order and stay first.
  assert.deepStrictEqual(codes, ["clients_unavailable", "settings_invalid"])
  assert.strictEqual(model.warningRows[1].text, settingsWarnings[0].message)

  // REQ-B20: no client name, address or MAC in a warning. Nothing the service
  // routes through here echoes a user-typed string into the sentence — the
  // raw value stays in `detail`, which is not rendered.
  for (const row of model.warningRows) {
    assert.ok(!/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/.test(row.text), row.text)
    assert.ok(!/([0-9a-f]{2}:){5}[0-9a-f]{2}/i.test(row.text), row.text)
  }

  // A settings warning is a note about how the reading was configured, not a
  // reading: REQ-013a says it never recolours the widget.
  assert.strictEqual(model.healthLevel, "green")
})

test("REQ-013a: merging the two warning sources keeps every distinct one, once", () => {
  const envelope = [
    { code: "clients_unavailable", message: "Client list unavailable." },
    { code: "statistics_unavailable", message: "A has none." },
    { code: "statistics_unavailable", message: "B has none." }
  ]
  // DATA-002 accepts several `gaius-codius.unifi` entries that differ only in
  // presentation, and every entry is resolved on its own — so two bar entries
  // with the same bad `compactMetric` produce the identical warning twice, and
  // printing it twice would read as two separate mistakes.
  const service = [
    { code: "settings_invalid", message: "compactMetric must be one of none, clients; using none." },
    { code: "settings_invalid", message: "compactMetric must be one of none, clients; using none." },
    { code: "settings_invalid", message: "dashboardUrl must be a string; falling back." }
  ]
  const merged = ViewModel.mergeWarnings(envelope, service)
  assert.deepStrictEqual(merged.map((w) => w.message), [
    "Client list unavailable.",
    "A has none.",
    "B has none.",
    "compactMetric must be one of none, clients; using none.",
    "dashboardUrl must be a string; falling back."
  ])
  // Deduplicated on the code AND the message: `settings_invalid` covers three
  // keys, so collapsing on the code alone would hide the second mistake in a
  // shell.json that has two.
  assert.strictEqual(merged.filter((w) => w.code === "statistics_unavailable").length, 2)

  // Junk from either side is dropped rather than rendered as a blank row.
  assert.deepStrictEqual(ViewModel.mergeWarnings(null, null), [])
  assert.deepStrictEqual(ViewModel.mergeWarnings("nonsense", [null, {}, { code: "" }]), [])

  // With no service warnings the list is the envelope's, unchanged — the
  // ordinary case must not be reshaped by the merge.
  assert.deepStrictEqual(ViewModel.mergeWarnings(envelope, []), envelope)
})

// =========================================================================
// Phase B2 — SPEC-v1.1-browse.md
// AC-B07, AC-B08, AC-B09, AC-B10, AC-B11, plus the REQ-B11 … REQ-B17 strings.
// =========================================================================

const BROWSE_SPEC = fs.readFileSync(path.resolve(__dirname,
  "../../docs/feature-specs/omarchy-unifi-plugin/SPEC-v1.1-browse.md"), "utf8")

// Every accept envelope carrying a `devices` array. Read off disk rather than
// listed here, so a fixture added in a later phase is covered by the corpus
// assertions the moment it exists rather than when somebody remembers.
function acceptSnapshots() {
  return fs.readdirSync(ACCEPT)
    .filter((name) => name.endsWith(".json"))
    .map((name) => ({
      name: name.replace(/\.json$/, ""),
      data: JSON.parse(fs.readFileSync(path.join(ACCEPT, name), "utf8")).data
    }))
    .filter((entry) => entry.data !== null && entry.data !== undefined)
}

function device(fields) {
  const base = {
    id: "00000000-0000-5000-9000-000000000001",
    name: null, model: null, state: "ONLINE", class: "online", roles: [],
    ipAddress: null, macAddress: null, firmwareVersion: null,
    firmwareUpdatable: null, uplinkDeviceId: null, detail: null, metrics: null
  }
  for (const key in fields) base[key] = fields[key]
  return base
}

function client(fields) {
  const base = {
    id: "00000000-0000-5000-9000-000000000101",
    name: null, type: "WIRED", accessType: null, ipAddress: null,
    macAddress: null, uplinkDeviceId: null, connectedAt: null
  }
  for (const key in fields) base[key] = fields[key]
  return base
}

function snapshotWith(devices, clients, counts) {
  return {
    site: { id: "s", name: "Home" },
    wan: null,
    gateways: [],
    offlineDevices: [],
    counts: counts || { devicesTotal: devices.length, clients: clients.length,
      offlineTotal: 0 },
    devices: devices,
    clients: clients
  }
}

// --- AC-B07: the REQ-B11 total order -------------------------------------

test("AC-B07: REQ-B11's five classes rank in the order the spec states", () => {
  // Parsed out of SPEC-v1.1-browse.md, not copied. `ViewModel.js` and
  // `normalize.py` each hold a copy of this ranking — HC-16 forbids them
  // sharing one — and both are pinned to this sentence rather than to each
  // other, so two copies drifting together past the requirement is not a way
  // for this to pass.
  const rule = /\*\*REQ-B11 — device ordering\.\*\*[\s\S]*?applied by the helper:\s*([\s\S]*?);/
    .exec(BROWSE_SPEC)
  assert.ok(rule, "SPEC-v1.1-browse.md: could not locate REQ-B11's order")
  const classes = [...rule[1].matchAll(/`([a-z]+)`/g)].map((m) => m[1])
  assert.deepStrictEqual(classes,
    ["down", "impaired", "unknown", "transitional", "online"])

  for (let i = 0; i < classes.length; i++) {
    assert.strictEqual(ViewModel.BROWSE_CLASS_RANK[classes[i]], i, classes[i])
  }
  assert.strictEqual(Object.keys(ViewModel.BROWSE_CLASS_RANK).length,
    classes.length)
})

test("AC-B07: unknown outranks transitional, which is the non-obvious half", () => {
  // The pair the ordering exists for. A device in a state this build does not
  // recognise is a thing to look at; an UPDATING one is not, and an
  // alphabetical or an accidental insertion order would put them the other way
  // round. Asserted as a comparison, not as two integers, because a rank table
  // that is right and a comparator that ignores it both pass the test above.
  const a = device({ class: "unknown", id: "a" })
  const b = device({ class: "transitional", id: "b" })
  assert.ok(ViewModel.compareBrowseOrder(a, b) < 0)
  assert.ok(ViewModel.compareBrowseOrder(b, a) > 0)
})

test("AC-B07: gateways sort first within their class, not across classes", () => {
  // Both halves. A comparator that put gateways first GLOBALLY would pass any
  // test built from one class, and would bury a down switch under a healthy
  // gateway — the exact outcome REQ-B11 exists to prevent.
  const downSwitch = device({ class: "down", roles: ["switching"], name: "zzz", id: "1" })
  const onlineGateway = device({ class: "online", roles: ["gateway"], name: "aaa", id: "2" })
  assert.ok(ViewModel.compareBrowseOrder(downSwitch, onlineGateway) < 0,
    "a down switch must precede an online gateway")

  const onlineSwitch = device({ class: "online", roles: ["switching"], name: "aaa", id: "3" })
  assert.ok(ViewModel.compareBrowseOrder(onlineGateway, onlineSwitch) < 0,
    "within one class the gateway leads")
})

test("AC-B07: names compare case-insensitively", () => {
  // "Zebra" and "apple", not "Apple" and "zebra". The second pair sorts the
  // same way under both rules — uppercase A is 0x41 and lowercase z is 0x7a —
  // so it cannot tell a case-insensitive comparison from a case-sensitive one.
  // This pair inverts: 'Z' is 0x5a and 'a' is 0x61, so a byte comparison puts
  // Zebra first and the requirement puts apple first.
  const zebra = device({ class: "online", name: "Zebra", id: "1" })
  const apple = device({ class: "online", name: "apple", id: "2" })
  assert.ok(ViewModel.compareBrowseOrder(apple, zebra) < 0)
  assert.ok(ViewModel.compareBrowseOrder(zebra, apple) > 0)
})

test("AC-B07: two devices differing only in id sort stably by id", () => {
  const first = device({ class: "online", name: "same", id: "aaa" })
  const second = device({ class: "online", name: "same", id: "bbb" })
  assert.ok(ViewModel.compareBrowseOrder(first, second) < 0)
  assert.ok(ViewModel.compareBrowseOrder(second, first) > 0)
  // Total: identical inputs tie, and nothing else in the corpus can.
  assert.strictEqual(ViewModel.compareBrowseOrder(first, first), 0)
})

test("AC-B07: a corpus of every class and a multi-role device sorts as specified", () => {
  const built = [
    device({ id: "6", class: "online", name: "alpha", roles: ["switching"] }),
    device({ id: "5", class: "online", name: "Beta", roles: ["gateway", "switching"] }),
    device({ id: "4", class: "transitional", name: "gamma", roles: ["accessPoint"] }),
    device({ id: "3", class: "unknown", name: "delta", roles: [] }),
    device({ id: "2", class: "impaired", name: "epsilon", roles: ["accessPoint"] }),
    device({ id: "1", class: "down", name: "zeta", roles: ["switching"] })
  ]
  const sorted = built.slice().sort(ViewModel.compareBrowseOrder)
  assert.deepStrictEqual(sorted.map((d) => d.id),
    ["1", "2", "3", "4", "5", "6"])
  assert.strictEqual(ViewModel.firstBrowseOrderViolation(sorted), -1)
  // The multi-role device is a gateway AND a switch, and it is its gateway
  // role that lifts it above the plain switch despite "Beta" > "alpha".
  assert.strictEqual(ViewModel.firstBrowseOrderViolation(built), 1)
})

test("AC-B07: every accept envelope's device list is already in REQ-B11's order", () => {
  // REQ-B01 puts the ordering in the HELPER so every consumer sees one order.
  // This is the assertion that keeps that true, over the corpus that is itself
  // asserted to be `normalize.py`'s byte-for-byte output.
  let checked = 0
  for (const entry of acceptSnapshots()) {
    const devices = entry.data.devices
    if (!Array.isArray(devices) || devices.length < 2) continue
    checked++
    assert.strictEqual(ViewModel.firstBrowseOrderViolation(devices), -1,
      entry.name + ": device " + ViewModel.firstBrowseOrderViolation(devices)
        + " is out of REQ-B11 order")
  }
  assert.ok(checked > 0, "no accept envelope carried a device list to check")
})

test("AC-B07: the order key is reproducible from the emitted record alone", () => {
  // `normalize.py` sorts by `is_gateway(raw_device)`, which is
  // `"gateway" in roles_of(raw_device)`; `ViewModel.js` sorts by whether the
  // EMITTED `roles` array contains "gateway". The two agree only because
  // `roles` is emitted from that same set. If they ever part company, the
  // corpus assertion above stops meaning what it says — so the link is checked
  // here rather than assumed.
  for (const entry of acceptSnapshots()) {
    for (const record of entry.data.devices || []) {
      const inGateways = (entry.data.gateways || [])
        .some((g) => g.id === record.id)
      if (!inGateways) continue
      assert.ok(ViewModel.hasRole(record.roles, "gateway"),
        entry.name + ": " + record.id + " is in gateways[] without the role")
    }
  }
})

// --- REQ-B12: client order -----------------------------------------------

test("REQ-B12: clients sort by the name the panel renders, then by id", () => {
  // The pair has to INVERT between the two rules, or it proves nothing. An
  // unnamed client beside a "Zebra" sorts first either way — by its address
  // under the requirement, and by the empty string under the mutation — so a
  // first version of this test passed while `name: lowerOf(entry.name)`
  // survived. "111-printer" sorts BEFORE "192.0.2.9" and AFTER "": the
  // requirement puts the named client first and the raw-name rule puts it last.
  const named = client({ id: "2", name: "111-printer" })
  const byIp = client({ id: "1", name: null, ipAddress: "192.0.2.9" })
  assert.ok(ViewModel.compareClientOrder(named, byIp) < 0)
  assert.ok(ViewModel.compareClientOrder(byIp, named) > 0)
  assert.ok(ViewModel.compareClientOrder(
    byIp, client({ id: "3", name: "Zebra" })) < 0)
  const same = [client({ id: "b", name: "same" }), client({ id: "a", name: "same" })]
  assert.deepStrictEqual(same.slice().sort(ViewModel.compareClientOrder)
    .map((c) => c.id), ["a", "b"])
})

test("REQ-B12: every accept envelope's client list is already in that order", () => {
  // The helper applies this too, and it must: `CLIENTS_LISTED_MAX` takes the
  // HEAD of the list, so without a producer-side order the 500 clients that
  // survive on a 900-client site are whichever the controller paginated first
  // — a set that can differ between two polls with nothing having changed.
  let checked = 0
  for (const entry of acceptSnapshots()) {
    const clients = entry.data.clients
    if (!Array.isArray(clients) || clients.length < 2) continue
    checked++
    assert.strictEqual(ViewModel.firstClientOrderViolation(clients), -1,
      entry.name + ": client " + ViewModel.firstClientOrderViolation(clients)
        + " is out of REQ-B12 order")
  }
  assert.ok(checked > 0, "no accept envelope carried a client list to check")
})

// --- AC-B08: search ------------------------------------------------------

test("AC-B08: a device matches on its name, case-insensitively", () => {
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "Attic AP" }),
      device({ id: "2", name: "Garage Switch" })], []),
    { search: "aTTiC" })
  assert.deepStrictEqual(list.rows.map((r) => r.id), ["1"])
})

test("AC-B08: a device matches on its model", () => {
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "one", model: "U6-Pro" }),
      device({ id: "2", name: "two", model: "USW-Lite-8-PoE" })], []),
    { search: "usw" })
  assert.deepStrictEqual(list.rows.map((r) => r.id), ["2"])
})

test("AC-B08: a device matches on its IP address", () => {
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "one", ipAddress: "192.0.2.31" }),
      device({ id: "2", name: "two", ipAddress: "192.0.2.44" })], []),
    { search: ".44" })
  assert.deepStrictEqual(list.rows.map((r) => r.id), ["2"])
})

test("AC-B08: a device matches on its MAC address, which the row never prints", () => {
  // REQ-B21 keeps the MAC out of the row and REQ-B13 puts it in the search.
  // Both, at once: the haystack is not what is rendered.
  const target = device({ id: "1", name: "one", macAddress: "02:00:00:00:00:1f" })
  const list = ViewModel.deviceListModel(
    snapshotWith([target, device({ id: "2", name: "two" })], []),
    { search: "00:1F" })
  assert.deepStrictEqual(list.rows.map((r) => r.id), ["1"])
  assert.strictEqual(ViewModel.browseDeviceRow(target).metaText
    .indexOf("02:00:00"), -1, "the MAC reached the row")
})

test("AC-B08: a device does NOT match on a field REQ-B13 does not name", () => {
  // `id` and the raw `state` are on the record and are not searchable. A
  // haystack built by joining every field would pass every test above and
  // would match rows for reasons a user cannot see, which is the one thing
  // REQ-B13's "never fuzzy" is protecting.
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "abc123", name: "one", state: "PENDING" })], []),
    { search: "abc123" })
  assert.deepStrictEqual(list.rows, [])
  const byState = ViewModel.deviceListModel(
    snapshotWith([device({ id: "abc123", name: "one", state: "PENDING" })], []),
    { search: "pending" })
  assert.deepStrictEqual(byState.rows, [])
})

test("AC-B08: an UNNAMED device is findable by the id its row shows", () => {
  // The counterpart to the test above, and the reason it says "a field REQ-B13
  // does not name" rather than "the id". `displayName` falls back to the id, so
  // an unnamed device's id IS what the row prints — and what the panel shows
  // must be searchable, or the fallback produces a row nobody can find.
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "abc123", name: null })], []),
    { search: "abc123" })
  assert.deepStrictEqual(list.rows.map((r) => r.id), ["abc123"])
})

test("AC-B08: a client matches on name, IP, MAC, type and uplink device name", async (t) => {
  const uplink = device({ id: "up", name: "Garage Switch" })
  const target = client({ id: "1", name: "workshop-pi", ipAddress: "192.0.2.40",
    macAddress: "02:00:00:00:01:28", type: "WIRED", uplinkDeviceId: "up" })
  const other = client({ id: "2", name: "phone", type: "WIRELESS" })
  const cases = [
    ["name", "WORKSHOP"],
    ["IP", "2.40"],
    ["MAC", "01:28"],
    ["type", "wired"],
    ["uplink device name", "garage"]
  ]
  for (const [field, term] of cases) {
    await t.test(field, () => {
      const list = ViewModel.clientListModel(
        snapshotWith([uplink], [target, other]), { search: term })
      assert.deepStrictEqual(list.rows.map((r) => r.id), ["1"])
    })
  }
})

test("AC-B08: a term matching nothing yields the empty list, not the unfiltered one", () => {
  // The mutation this exists for is `if (rows.length === 0) return all`, which
  // is a plausible "don't show an empty page" reflex and would answer a search
  // with every row on the site.
  const snapshot = snapshotWith(
    [device({ id: "1", name: "one" }), device({ id: "2", name: "two" })], [])
  const list = ViewModel.deviceListModel(snapshot, { search: "nothingmatches" })
  assert.deepStrictEqual(list.rows, [])
  assert.strictEqual(list.matched, 0)
  assert.strictEqual(list.listed, 2, "the unfiltered count is still reported")
  assert.ok(list.emptyText.indexOf("nothingmatches") !== -1,
    "REQ-B16: the empty state names the term")
})

test("AC-B08: an empty or whitespace-only term filters nothing", () => {
  const snapshot = snapshotWith(
    [device({ id: "1", name: "one" }), device({ id: "2", name: "two" })], [])
  for (const term of ["", "   ", undefined, null, 7]) {
    const list = ViewModel.deviceListModel(snapshot, { search: term })
    assert.strictEqual(list.rows.length, 2, JSON.stringify(term))
    assert.strictEqual(list.emptyText, "")
  }
})

test("AC-B08: search is substring and never fuzzy", () => {
  // "atc" is "attic" with a letter removed. A fuzzy or subsequence match finds
  // it; a substring match does not, and REQ-B13 requires a user to be able to
  // say why a row matched.
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "Attic AP" })], []), { search: "atc" })
  assert.deepStrictEqual(list.rows, [])
})

test("REQ-B10a: a role filter narrows the list to devices holding that role", () => {
  const snapshot = snapshotWith([
    device({ id: "1", name: "gw", roles: ["gateway", "switching"] }),
    device({ id: "2", name: "sw", roles: ["switching"] }),
    device({ id: "3", name: "ap", roles: ["accessPoint"] })
  ], [])
  assert.deepStrictEqual(
    ViewModel.deviceListModel(snapshot, { role: "gateway" }).rows.map((r) => r.id),
    ["1"])
  // The multi-role device appears under BOTH its roles (REQ-009's rule, which
  // is why the counts are not a partition).
  assert.deepStrictEqual(
    ViewModel.deviceListModel(snapshot, { role: "switching" }).rows.map((r) => r.id),
    ["1", "2"])
  assert.deepStrictEqual(
    ViewModel.deviceListModel(snapshot, { role: "accessPoint" }).rows.map((r) => r.id),
    ["3"])
})

test("REQ-B10a: every count row carries a role value devices actually use", () => {
  // The count buckets are plural nouns and `devices[].roles` holds the API's
  // feature names. A typo in the mapping produces an always-empty Devices page
  // rather than an error, so the two vocabularies are pinned to each other over
  // the corpus.
  const seen = {}
  for (const entry of acceptSnapshots()) {
    for (const record of entry.data.devices || []) {
      for (const role of record.roles || []) seen[role] = true
    }
  }
  const mapped = ViewModel.ROLE_FOR_COUNT_KEY
  assert.deepStrictEqual(Object.keys(mapped).sort(),
    ["accessPoints", "gateways", "switches"])
  for (const key in mapped) {
    assert.ok(seen[mapped[key]], key + " maps to " + mapped[key]
      + ", which no device in the corpus reports")
    assert.ok(ViewModel.ROLE_PLURAL[mapped[key]], mapped[key] + " has no plural")
  }
  const rows = ViewModel.countRows({ devicesTotal: 2,
    gateways: { online: 1 }, switches: { online: 1 }, accessPoints: null })
  assert.deepStrictEqual(rows.map((r) => r.role), ["gateway", "switching"])
})

// --- AC-B09: `detail: null` and `detail.ports: []` -----------------------

test("AC-B09: a device with detail null renders the truncation sentence", () => {
  const detail = ViewModel.deviceDetail(device({ id: "1", detail: null }), {})
  assert.strictEqual(detail.fetched, false)
  assert.strictEqual(detail.unavailableText, ViewModel.DETAIL_NOT_FETCHED)
  assert.deepStrictEqual(detail.ports, [])
  // And says NOTHING about ports, because it does not know.
  assert.strictEqual(detail.portsEmptyText, "")
  assert.strictEqual(detail.radiosEmptyText, "")
})

test("AC-B09: a device with detail.ports [] renders an empty port table", () => {
  const detail = ViewModel.deviceDetail(
    device({ id: "1", detail: { provisionedAt: null, ports: [], radios: [] } }), {})
  assert.strictEqual(detail.fetched, true)
  assert.strictEqual(detail.unavailableText, "")
  assert.deepStrictEqual(detail.ports, [])
  assert.strictEqual(detail.portsEmptyText, "No ports reported.")
})

test("AC-B09: the two are distinguishable in one envelope", () => {
  // `success_browse_full` carries both on purpose. A corpus where every device
  // is alike lets a consumer conflate them and still pass.
  const data = snapshotOf("success_browse_full")
  const withDetail = data.devices.filter((d) => d.detail !== null)
  const without = data.devices.filter((d) => d.detail === null)
  assert.ok(withDetail.length > 0 && without.length > 0,
    "the fixture must carry both, or this asserts nothing")
  for (const record of withDetail) {
    assert.strictEqual(ViewModel.deviceDetail(record, {}).unavailableText, "")
    assert.strictEqual(ViewModel.browseDeviceRow(record).detailFetched, true)
  }
  for (const record of without) {
    assert.strictEqual(ViewModel.deviceDetail(record, {}).unavailableText,
      ViewModel.DETAIL_NOT_FETCHED)
    assert.strictEqual(ViewModel.browseDeviceRow(record).detailFetched, false)
  }
})

test("AC-B09: a device with ports renders no empty-table sentence", () => {
  // The third case, and the one that catches a `portsEmptyText` that is always
  // present. Two of the three states pass a test written over the other two.
  const detail = ViewModel.deviceDetail(device({
    id: "1",
    detail: { provisionedAt: null, radios: [],
      ports: [{ idx: 1, connector: "RJ45", state: "UP", maxSpeedMbps: 1000, poe: null }] }
  }), {})
  assert.strictEqual(detail.portsEmptyText, "")
  assert.strictEqual(detail.ports.length, 1)
  assert.strictEqual(detail.radiosEmptyText, "No radios reported.")
})

test("REQ-B14: a port reporting no PoE and a port with PoE switched off differ", () => {
  // A property of the hardware and a setting. Someone working out why a camera
  // has no power needs to tell them apart, and both would render as "off" under
  // a truthiness test on `poe`.
  assert.strictEqual(ViewModel.poeText(null), "")
  assert.strictEqual(ViewModel.poeText(undefined), "")
  assert.strictEqual(ViewModel.poeText({ enabled: false, standard: "802.3at", state: "OFF" }),
    "PoE off")
  assert.strictEqual(ViewModel.poeText({ enabled: true, standard: "802.3at", state: "GOOD" }),
    "PoE 802.3at GOOD")
  assert.strictEqual(ViewModel.poeText({ enabled: true, standard: null, state: null }), "PoE")
})

// --- AC-B10: null is unknown, never zero ---------------------------------

test("AC-B10: a null utilisation is unknown and a zero one is 0%", () => {
  // BIZ-003, on the two fields most likely to be absent: `statistics/latest` is
  // the collection DATA-B04's budget drops first, so `metrics: null` is the
  // ordinary case on a large site rather than an error.
  assert.strictEqual(ViewModel.formatPct(null), "unknown")
  assert.strictEqual(ViewModel.formatPct(undefined), "unknown")
  assert.strictEqual(ViewModel.formatPct(0), "0%")
  assert.strictEqual(ViewModel.formatPct(4.15), "4.2%")
  assert.strictEqual(ViewModel.formatPct(NaN), "unknown")
  assert.strictEqual(ViewModel.formatPct("38"), "unknown")
})

test("AC-B10: a device with metrics null shows unknown in every metric row", () => {
  const detail = ViewModel.deviceDetail(device({ id: "1", metrics: null }), {})
  const values = {}
  for (const row of detail.rows) values[row.key] = row.value
  for (const key of ["cpu", "memory", "download", "upload"]) {
    assert.strictEqual(values[key], "unknown", key)
  }
  // And never renders as a zero anywhere in the block.
  assert.strictEqual(JSON.stringify(detail.rows).indexOf('"0'), -1)
})

test("AC-B10: a device reporting zero throughput says 0, not unknown", () => {
  // The negative control for the test above. A `formatBps` that returned
  // "unknown" for everything falsy would pass it, and would hide a WAN link
  // that is up and carrying nothing — which is a fact worth showing.
  const detail = ViewModel.deviceDetail(device({
    id: "1",
    metrics: { uptimeSec: 0, cpuUtilizationPct: 0, memoryUtilizationPct: 0,
      downloadBps: 0, uploadBps: 0 }
  }), {})
  const values = {}
  for (const row of detail.rows) values[row.key] = row.value
  assert.strictEqual(values.cpu, "0%")
  assert.strictEqual(values.memory, "0%")
  assert.strictEqual(values.download, "0 bps")
  assert.strictEqual(values.upload, "0 bps")
})

test("AC-B10: an absent radio retry rate is omitted and a zero one is reported", () => {
  // BIZ-003 turned around. Everywhere else an absent optional renders as the
  // word "unknown"; here it renders as nothing at all, because the band beside
  // it is the subject of the line and an omitted figure cannot be mistaken for
  // a band. What must NOT happen is the other half of BIZ-003: a real zero —
  // the best retry rate a radio can have — being dropped as though absent.
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: 0 }),
    "5 GHz (0% retries)")
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 2.4, txRetriesPct: 3.14 }),
    "2.4 GHz (3.1% retries)")
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: null }),
    "5 GHz")
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 5 }), "5 GHz")
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: NaN }),
    "5 GHz")
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: 5, txRetriesPct: "3" }),
    "5 GHz")
  // A radio whose band is missing is still a radio, and saying so is not the
  // same as saying nothing.
  assert.strictEqual(ViewModel.radioText({ frequencyGHz: null, txRetriesPct: null }),
    "unknown band")
})

test("REQ-B14: the radios render as one line, in the order given", () => {
  assert.strictEqual(ViewModel.radioSummaryText([]), "")
  assert.strictEqual(ViewModel.radioSummaryText(null), "")
  assert.strictEqual(
    ViewModel.radioSummaryText([{ frequencyGHz: 2.4, txRetriesPct: null },
                                { frequencyGHz: 5, txRetriesPct: null },
                                { frequencyGHz: 6, txRetriesPct: null }]),
    "2.4 GHz,  5 GHz,  6 GHz")
  // The controller this was written against reports no retry rate at all, so
  // the ordinary case is bands alone — and it must not contain the word the
  // two-column table used to fill its second column with.
  const bands = ViewModel.radioSummaryText(
    [{ frequencyGHz: 2.4, txRetriesPct: null }, { frequencyGHz: 5, txRetriesPct: null }])
  assert.strictEqual(bands.indexOf("unknown"), -1)
})

test("REQ-B14: a port state is a word, not the API's enum", () => {
  // "DOWN" reads as a fault. What it means is that nothing is plugged in,
  // which on a 24-port switch is the ordinary condition of most of the ports.
  assert.strictEqual(ViewModel.portStateWord("UP"), "up")
  assert.strictEqual(ViewModel.portStateWord("DOWN"), "no link")
  // Not in the map: rendered, not guessed at. protocol-v1.md does not constrain
  // `state` to a closed set, so a member added by a controller update has to
  // reach the panel as itself rather than as "unknown".
  assert.strictEqual(ViewModel.portStateWord("BLOCKING"), "blocking")
  assert.strictEqual(ViewModel.portStateWord(null), "unknown")
  assert.strictEqual(ViewModel.portStateWord(""), "unknown")
  assert.strictEqual(ViewModel.portStateWord(7), "unknown")
  // The `own` guard. `map["constructor"]` is a function, and it reached the
  // client rows once already (see `clientTypeWord`).
  assert.strictEqual(ViewModel.portStateWord("constructor"), "constructor")
  assert.strictEqual(ViewModel.portStateWord("valueOf"), "valueof")
})

test("REQ-B14: the port row carries the state word and no speed", () => {
  const row = ViewModel.portRow(
    { idx: 3, connector: "RJ45", state: "DOWN", maxSpeedMbps: 1000, poe: null })
  assert.strictEqual(row.idxText, "3")
  assert.strictEqual(row.stateText, "no link")
  assert.strictEqual(row.isUp, false)
  // The speed is still on the wire — the protocol table is frozen — and must
  // not be anywhere in what the panel renders.
  assert.strictEqual(row.speedText, undefined)
  assert.strictEqual(JSON.stringify(row).indexOf("1000"), -1)
  assert.strictEqual(JSON.stringify(row).indexOf("Mbps"), -1)
  // `isUp` is the colour, `stateText` is the word. UX-002 wants both, and a
  // single field cannot be both.
  assert.strictEqual(ViewModel.portRow({ idx: 1, state: "UP" }).isUp, true)
  assert.strictEqual(ViewModel.portRow({ idx: 1, state: "UP" }).stateText, "up")
})

test("AC-B10: an unknown uptime is dropped from the row and unknown on the field", () => {
  // REQ-B14 says "uptime when known", and BIZ-003 says an absent optional is
  // never a zero. Both: the glance line omits it, the field says "unknown", and
  // neither says "0m".
  const row = ViewModel.browseDeviceRow(device({ id: "1", name: "n", metrics: null }))
  assert.strictEqual(row.uptimeText, "unknown")
  assert.strictEqual(row.metaText.indexOf("up "), -1)
  assert.strictEqual(row.metaText.indexOf("0m"), -1)

  const up = ViewModel.browseDeviceRow(device({
    id: "1", name: "n", metrics: { uptimeSec: 864000 } }))
  assert.strictEqual(up.uptimeText, "10d 0h")
  assert.ok(up.metaText.indexOf("up 10d 0h") !== -1)
})

test("AC-B10: a non-boolean firmwareUpdatable does not claim an update", () => {
  // `=== true` rather than truthiness, and this is the test that makes the
  // difference visible. Over the three values DATA-B01 allows — true, false,
  // null — the two rules agree exactly, so a `!!` mutation survives every test
  // written from the contract.
  //
  // It is reachable. `Protocol.js`'s `checkDeviceRecord` validates `id`,
  // `class`, `roles`, `detail` and `metrics` and does NOT validate
  // `firmwareUpdatable`, so a producer that sent the string "false" would have
  // it accepted — and `!!"false"` is true, which is the panel telling a user
  // there is a firmware update on the strength of a word.
  for (const value of ["yes", "false", 1, {}, []]) {
    assert.strictEqual(
      ViewModel.browseDeviceRow(device({ firmwareUpdatable: value })).updateAvailable,
      false, JSON.stringify(value))
    const detail = ViewModel.deviceDetail(device({ firmwareUpdatable: value }), {})
    // Both, because they are two fields and a test naming one leaves the other
    // free: `updateText` and `updateAvailable` are separate expressions and the
    // mutation survived a version of this test that only checked the sentence.
    assert.strictEqual(detail.updateText, "", JSON.stringify(value))
    assert.strictEqual(detail.updateAvailable, false, JSON.stringify(value))
    // And the third place the mark appears, the Firmware row's value.
    const firmware = detail.rows.filter((r) => r.key === "firmware")[0]
    assert.strictEqual(firmware.value.indexOf("update available"), -1, JSON.stringify(value))
  }
})

test("AC-B10: firmwareUpdatable null does not claim an update is available", () => {
  // Nullable, so `=== true` and not truthiness: `null` is "the controller did
  // not say", and a mark on that basis invents the fact.
  assert.strictEqual(ViewModel.browseDeviceRow(device({ firmwareUpdatable: null }))
    .updateAvailable, false)
  assert.strictEqual(ViewModel.browseDeviceRow(device({ firmwareUpdatable: false }))
    .updateAvailable, false)
  assert.strictEqual(ViewModel.browseDeviceRow(device({ firmwareUpdatable: true }))
    .updateAvailable, true)
  assert.strictEqual(ViewModel.deviceDetail(device({ firmwareUpdatable: null }), {})
    .updateText, "")
  assert.strictEqual(ViewModel.deviceDetail(device({ firmwareUpdatable: true }), {})
    .updateText, "update available")
})

// --- AC-B11: the client name fallback ------------------------------------

test("AC-B11: a client with no name renders its IP, then its id, never blank", async (t) => {
  const cases = [
    ["a name", client({ name: "workshop-pi", ipAddress: "192.0.2.40", id: "x" }), "workshop-pi"],
    ["no name, an IP", client({ name: null, ipAddress: "192.0.2.40", id: "x" }), "192.0.2.40"],
    ["neither, an id", client({ name: null, ipAddress: null, id: "x" }), "x"],
    ["an empty name", client({ name: "", ipAddress: "192.0.2.40", id: "x" }), "192.0.2.40"],
    ["nothing at all", client({ name: null, ipAddress: null, id: "" }), "unnamed client"]
  ]
  for (const [label, record, expected] of cases) {
    await t.test(label, () => {
      assert.strictEqual(ViewModel.clientDisplayName(record), expected)
      assert.notStrictEqual(ViewModel.browseClientRow(record, "", null).nameText, "")
    })
  }
})

test("AC-B11: no client row in the corpus renders a blank name", () => {
  for (const entry of acceptSnapshots()) {
    for (const record of entry.data.clients || []) {
      const row = ViewModel.browseClientRow(record, "", 1767225600)
      assert.notStrictEqual(row.nameText, "", entry.name + ": " + record.id)
    }
  }
})

test("AC-B11: the IP is not printed twice when the name fell back to it", () => {
  const row = ViewModel.browseClientRow(
    client({ name: null, ipAddress: "192.0.2.40", id: "x" }), "", null)
  assert.strictEqual(row.nameText, "192.0.2.40")
  assert.strictEqual(row.ipText, "")
  const named = ViewModel.browseClientRow(
    client({ name: "pi", ipAddress: "192.0.2.40", id: "x" }), "", null)
  assert.strictEqual(named.ipText, "192.0.2.40")
})

test("REQ-B14: an unrecognised client type renders as itself, not as unknown", () => {
  assert.strictEqual(ViewModel.clientTypeWord("WIRED"), "Wired")
  assert.strictEqual(ViewModel.clientTypeWord("WIRELESS"), "Wireless")
  assert.strictEqual(ViewModel.clientTypeWord("TELEPORT"), "Teleport")
  assert.strictEqual(ViewModel.clientTypeWord("VPN"), "VPN")
  // The point of the rule: a type this build has not seen decides nothing, so
  // printing "unknown" over a value the controller stated plainly would be the
  // panel losing information it has.
  assert.strictEqual(ViewModel.clientTypeWord("SATELLITE_UPLINK"), "SATELLITE_UPLINK")
  assert.strictEqual(ViewModel.clientTypeWord(null), "unknown")
})

test("REQ-B21: a client MAC address appears only in the expanded detail", () => {
  const record = client({ id: "1", name: "pi", ipAddress: "192.0.2.40",
    macAddress: "02:00:00:00:01:28", uplinkDeviceId: null })
  const row = ViewModel.browseClientRow(record, "", 1767225600)
  assert.strictEqual(row.metaText.indexOf("02:00:00"), -1)
  assert.strictEqual(row.nameText.indexOf("02:00:00"), -1)
  assert.strictEqual(row.ipText.indexOf("02:00:00"), -1)
  const detail = ViewModel.clientDetail(record, "")
  const mac = detail.rows.filter((r) => r.key === "mac")[0]
  assert.strictEqual(mac.value, "02:00:00:00:01:28")
})

// --- REQ-B14: the uplink name --------------------------------------------

test("REQ-B14: an uplink id resolves to the device name, and says so when it cannot", () => {
  const names = ViewModel.uplinkNames([
    device({ id: "up", name: "Garage Switch" }),
    device({ id: "noname", name: null })
  ])
  assert.strictEqual(ViewModel.uplinkNameFor(names, "up"), "Garage Switch")
  // An unnamed uplink resolves to whatever ITS row shows, not to the empty
  // string — the same fallback, one level down.
  assert.strictEqual(ViewModel.uplinkNameFor(names, "noname"), "noname")
  // Not an error: `devices[]` is bounded, so on a large site a client can
  // legitimately uplink to a device that was not listed. Rendering a bare uuid
  // would give the user something they cannot act on; dropping the segment
  // would read as "connected to nothing".
  assert.strictEqual(ViewModel.uplinkNameFor(names, "missing"), "an unlisted device")
  assert.strictEqual(ViewModel.uplinkNameFor(names, null), "")
  assert.strictEqual(ViewModel.uplinkNameFor(names, ""), "")
})

test("REQ-B14: a client with no uplink omits the via segment entirely", () => {
  const row = ViewModel.browseClientRow(
    client({ id: "1", name: "pi", type: "WIRED", uplinkDeviceId: null }), "", null)
  assert.strictEqual(row.metaText.indexOf("via"), -1)
  assert.strictEqual(row.uplinkText, "")
})

// --- REQ-B16: the empty and truncated states -----------------------------

test("AC-B06/REQ-B16: the truncation line comes from counts, never from the array", () => {
  // The REQ-010/AC-063 rule applied a third time. Shortening the array must not
  // change either number — a consumer computing the total from `devices.length`
  // reports 200 devices on a site of 412 and calls it complete.
  const devices = [device({ id: "1", name: "a" }), device({ id: "2", name: "b" })]
  const list = ViewModel.deviceListModel(
    snapshotWith(devices, [], { devicesTotal: 412, clients: 0, offlineTotal: 0 }), {})
  assert.strictEqual(list.truncated, true)
  assert.strictEqual(list.total, 412)
  assert.strictEqual(list.listed, 2)
  assert.strictEqual(list.truncationText, "showing 2 of 412 devices")

  const shorter = ViewModel.deviceListModel(
    snapshotWith([devices[0]], [], { devicesTotal: 412, clients: 0, offlineTotal: 0 }), {})
  assert.strictEqual(shorter.total, 412, "the total moved with the array")
})

test("REQ-B16: an untruncated list says nothing about truncation", () => {
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "a" })], [],
      { devicesTotal: 1, clients: 0, offlineTotal: 0 }), {})
  assert.strictEqual(list.truncated, false)
  assert.strictEqual(list.truncationText, "")
})

test("REQ-B16: the truncation line is singular for a site of one", () => {
  assert.strictEqual(ViewModel.truncationText(0, 1, "device"), "showing 0 of 1 device")
  assert.strictEqual(ViewModel.truncationText(0, 2, "device"), "showing 0 of 2 devices")
})

test("REQ-B16: an empty array over a non-zero total never says 'no devices'", () => {
  // DATA-B04's boundary: the budget admitted nothing. "No devices to show."
  // beside "showing 0 of 300 devices" is the panel contradicting itself in two
  // adjacent lines, and the wrong one is the one the user reads first.
  const data = snapshotOf("success_browse_empty_lists")
  const list = ViewModel.deviceListModel(data, {})
  assert.deepStrictEqual(list.rows, [])
  assert.strictEqual(list.truncationText, "showing 0 of 300 devices")
  assert.notStrictEqual(list.emptyText, "No devices to show.")
  assert.ok(list.emptyText.indexOf("300") !== -1,
    "the empty state must acknowledge the bound: " + list.emptyText)
})

test("REQ-B16: a search over a truncated list admits the bound", () => {
  // "No devices match" is not TRUE on a bounded list — the device may exist and
  // simply not be listed. A panel that answers a search with a confident wrong
  // "no" is worse than one that admits what it did not look at.
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "a" })], [],
      { devicesTotal: 412, clients: 0, offlineTotal: 0 }), { search: "garage" })
  assert.ok(list.emptyText.indexOf("garage") !== -1)
  assert.ok(list.emptyText.indexOf("412") !== -1, list.emptyText)
})

test("REQ-B16: a role-filtered empty list names the role", () => {
  const snapshot = snapshotWith([device({ id: "1", name: "a", roles: ["switching"] })], [],
    { devicesTotal: 1, clients: 0, offlineTotal: 0 })
  assert.strictEqual(
    ViewModel.deviceListModel(snapshot, { role: "gateway" }).emptyText,
    "No gateways to show.")
  assert.strictEqual(
    ViewModel.deviceListModel(snapshot, { role: "accessPoint" }).emptyText,
    "No access points to show.")
})

test("REQ-B16: the client list carries its own totals and empty state", () => {
  const list = ViewModel.clientListModel(
    snapshotWith([], [client({ id: "1", name: "pi" })],
      { devicesTotal: 0, clients: 900, offlineTotal: 0 }), {})
  assert.strictEqual(list.truncationText, "showing 1 of 900 clients")
  const none = ViewModel.clientListModel(
    snapshotWith([], [], { devicesTotal: 0, clients: 0, offlineTotal: 0 }), {})
  assert.strictEqual(none.emptyText, "No clients to show.")
  assert.strictEqual(none.truncationText, "")
})

test("REQ-B16: a truncated corpus envelope renders both lines from its counts", () => {
  const data = snapshotOf("success_browse_truncated")
  assert.strictEqual(ViewModel.deviceListModel(data, {}).truncationText,
    "showing 2 of 300 devices")
  assert.strictEqual(ViewModel.clientListModel(data, {}).truncationText,
    "showing 1 of 900 clients")
})

// --- REQ-B14: one row expanded at a time ---------------------------------

test("REQ-B14: the detail is built for the expanded row and for no other", () => {
  const snapshot = snapshotWith([
    device({ id: "1", name: "one", firmwareVersion: "9.1.0" }),
    device({ id: "2", name: "two", firmwareVersion: "9.2.0" })
  ], [])
  const list = ViewModel.deviceListModel(snapshot, { expandedId: "2" })
  assert.strictEqual(list.expandedId, "2")
  assert.strictEqual(list.expandedDetail.id, "2")
  const firmware = list.expandedDetail.rows.filter((r) => r.key === "firmware")[0]
  assert.strictEqual(firmware.value, "9.2.0")
})

test("REQ-B14: the expanded detail carries the update-available mark", () => {
  const snapshot = snapshotWith([
    device({ id: "1", name: "one", firmwareVersion: "9.1.0", firmwareUpdatable: true }),
    device({ id: "2", name: "two", firmwareVersion: "9.2.0", firmwareUpdatable: false })
  ], [])
  const firmwareOf = (id) => ViewModel.deviceListModel(snapshot, { expandedId: id })
    .expandedDetail.rows.filter((r) => r.key === "firmware")[0].value
  assert.strictEqual(firmwareOf("1"), "9.1.0  \u00b7  update available")
  assert.strictEqual(firmwareOf("2"), "9.2.0")
})

test("REQ-B14: a row filtered out by a search takes its detail with it", () => {
  // Otherwise the panel shows a detail block for a row the user cannot see —
  // and the block carries the MAC address REQ-B21 keeps hidden until a
  // deliberate expansion.
  const snapshot = snapshotWith([
    device({ id: "1", name: "one" }), device({ id: "2", name: "two" })], [])
  const list = ViewModel.deviceListModel(snapshot,
    { expandedId: "2", search: "one" })
  assert.strictEqual(list.expandedId, "")
  assert.strictEqual(list.expandedDetail, null)
})

test("REQ-B14: expanding an id that is not in the list yields no detail", () => {
  const list = ViewModel.deviceListModel(
    snapshotWith([device({ id: "1", name: "one" })], []), { expandedId: "nope" })
  assert.strictEqual(list.expandedId, "")
  assert.strictEqual(list.expandedDetail, null)
})

test("REQ-B14: a client detail carries the MAC, access type and absolute instant", () => {
  const snapshot = snapshotWith([device({ id: "up", name: "Garage Switch" })],
    [client({ id: "1", name: "pi", macAddress: "02:00:00:00:01:28",
      accessType: "DEFAULT", uplinkDeviceId: "up",
      connectedAt: "2026-01-12T09:14:00Z" })])
  const list = ViewModel.clientListModel(snapshot, { expandedId: "1" })
  const values = {}
  for (const row of list.expandedDetail.rows) values[row.key] = row.value
  assert.strictEqual(values.mac, "02:00:00:00:01:28")
  assert.strictEqual(values.access, "DEFAULT")
  assert.strictEqual(values.uplink, "Garage Switch")
  assert.strictEqual(values.connected, "2026-01-12 14:44")
})

// --- REQ-B17: the time strings -------------------------------------------

test("REQ-B17: an RFC 3339 instant parses to the same epoch in both engines", async (t) => {
  // Hand-matched against the grammar and computed with `Date.UTC`, which is
  // fully specified. `Date.parse` is implementation-defined outside ISO 8601
  // and `toLocaleString` is banned outright — V4 and V8 do not ship the same
  // ICU data, and this corpus runs under both.
  const cases = [
    ["Z", "2026-01-12T09:14:00Z", 1768209240],
    ["lowercase z", "2026-01-12t09:14:00z", 1768209240],
    ["fractional seconds", "2026-01-12T09:14:00.123Z", 1768209240],
    ["a positive offset", "2026-01-12T11:14:00+02:00", 1768209240],
    ["a negative offset", "2026-01-12T04:14:00-05:00", 1768209240]
  ]
  for (const [label, text, expected] of cases) {
    await t.test(label, () => {
      assert.strictEqual(ViewModel.parseRfc3339(text), expected)
    })
  }
})

test("REQ-B17: anything that is not an instant parses to null", () => {
  for (const value of [null, undefined, "", "yesterday", 1768209240,
    "2026-01-12", "2026-01-12T09:14Z", "2026-01-12T09:14:00", {},
    "2026-01-12T09:14:00Z "]) {
    assert.strictEqual(ViewModel.parseRfc3339(value), null, JSON.stringify(value))
  }
})

test("REQ-B17: a date that does not exist is unknown, not a nearby one", () => {
  // `Date.UTC` rolls over instead of failing, and the grammar only says "two
  // digits". Before the round-trip check "2026-13-99T09:14:00Z" rendered as
  // "2027-04-09 09:14 UTC" — a plausible instant, confidently wrong, which is
  // worse than "unknown" precisely because nothing about it looks wrong.
  //
  // The trailing-space case above hid this: the first version of that list used
  // "2026-13-99T09:14:00Z " and passed on the SPACE, so the impossible date was
  // never actually tested.
  for (const value of ["2026-13-99T09:14:00Z", "2026-02-30T00:00:00Z",
    "2026-01-12T25:70:00Z", "2026-00-01T00:00:00Z", "2025-02-29T00:00:00Z"]) {
    assert.strictEqual(ViewModel.parseRfc3339(value), null, value)
    assert.strictEqual(ViewModel.formatInstant(value), "unknown", value)
  }
  // And the leap day that DOES exist still parses, or the guard is just a ban
  // on February.
  assert.strictEqual(ViewModel.formatInstant("2024-02-29T12:00:00Z"),
    "2024-02-29 17:30")
})

test("REQ-B17: an unparseable connectedAt renders as nothing, never as 'never'", () => {
  // `relativePast` answers "never" for a missing number, which is right for a
  // last-successful-poll and wrong here: "connected never" beside a client that
  // is plainly connected is the panel contradicting itself.
  assert.strictEqual(ViewModel.connectedText(null, 1768209240), "")
  assert.strictEqual(ViewModel.connectedText("nonsense", 1768209240), "")
  assert.strictEqual(ViewModel.connectedText("2026-01-12T09:14:00Z", null), "")
  const row = ViewModel.browseClientRow(
    client({ id: "1", name: "pi", connectedAt: null }), "", 1768209240)
  assert.strictEqual(row.connectedText, "")
  assert.strictEqual(row.metaText.indexOf("never"), -1)
  assert.strictEqual(row.metaText.indexOf("connected"), -1)
})

test("AC-071/REQ-B17: connected-since recomputes from nowWall and owns no clock", () => {
  // The guarantee AC-071 makes for `lastUpdateText`, extended to the new
  // strings: `nowWall` is an INPUT, so the existing freshness tick moves them
  // and no widget owns a timer (REQ-014, UX-011).
  const record = client({ id: "1", name: "pi", connectedAt: "2026-01-12T09:14:00Z" })
  const at = ViewModel.parseRfc3339("2026-01-12T09:14:00Z")
  assert.strictEqual(ViewModel.browseClientRow(record, "", at + 30).connectedText,
    "30s ago")
  assert.strictEqual(ViewModel.browseClientRow(record, "", at + 3 * 86400).connectedText,
    "3d ago")
  // Same record, same function, two different renderings — which is only
  // possible because the clock is an argument.
  assert.notStrictEqual(
    ViewModel.browseClientRow(record, "", at + 30).metaText,
    ViewModel.browseClientRow(record, "", at + 3 * 86400).metaText)
})

test("REQ-B17: the pinned test zone is in force", () => {
  // The canary for every assertion below. Without tzdata, or with `TZ` ignored,
  // local time silently becomes UTC — and then a `getUTC*` rendering would pass
  // every local-time assertion in this file. A test that cannot fail for the
  // reason it exists is worse than none, so the reason is checked first.
  const offsetMinutes = new Date(Date.UTC(2026, 0, 12)).getTimezoneOffset()
  assert.strictEqual(offsetMinutes, -330,
    "TZ=Asia/Kolkata (+05:30) is not in force; local-time assertions prove nothing")
  assert.strictEqual(new Date(Date.UTC(2026, 6, 1)).getTimezoneOffset(), -330,
    "the pinned zone must not observe DST, or the expected strings move")
})

test("REQ-B17: the absolute instant is the reader's local time", () => {
  // 09:14 UTC is 14:44 in the pinned +05:30 zone. Both fields move, which is
  // what a half-hour offset buys: a UTC rendering fails on the hour AND the
  // minute, and an offset applied to hours only fails on the minute.
  assert.strictEqual(ViewModel.formatInstant("2026-01-12T09:14:00Z"),
    "2026-01-12 14:44")
  // Zero-padded on both fields, which a naive concatenation gets wrong exactly
  // once a year and once an hour. 04:07 UTC is 09:37 local.
  assert.strictEqual(ViewModel.formatInstant("2026-03-05T04:07:00Z"),
    "2026-03-05 09:37")
  // A local rendering crosses the date boundary, and the DATE has to cross with
  // it: 21:00 UTC is 02:30 the NEXT DAY here. Formatting the local time beside
  // the UTC date is the classic version of this bug and reads as plausible.
  assert.strictEqual(ViewModel.formatInstant("2026-01-12T21:00:00Z"),
    "2026-01-13 02:30")
  // An offset in the input is resolved to the same instant, so two timestamps
  // on one panel are always comparable however the controller wrote them.
  assert.strictEqual(ViewModel.formatInstant("2026-01-12T11:14:00+02:00"),
    ViewModel.formatInstant("2026-01-12T09:14:00Z"))
  assert.strictEqual(ViewModel.formatInstant(null), "unknown")
  assert.strictEqual(ViewModel.formatInstant("whenever"), "unknown")
  // And it carries no zone label. It is the reader's own clock; a suffix would
  // be a conversion to do in their head at the moment they least want one.
  assert.strictEqual(ViewModel.formatInstant("2026-01-12T09:14:00Z").indexOf("UTC"),
    -1)
})

// --- REQ-B10: the three pages --------------------------------------------

test("REQ-B10: the view defaults to Overview and rejects anything else", () => {
  assert.deepStrictEqual(ViewModel.BROWSE_VIEWS, ["overview", "devices", "clients"])
  for (const view of ViewModel.BROWSE_VIEWS) {
    assert.strictEqual(ViewModel.browseView(view), view)
  }
  for (const junk of [null, undefined, "", "Devices", "settings", 3, {}]) {
    assert.strictEqual(ViewModel.browseView(junk), "overview", JSON.stringify(junk))
  }
})

test("REQ-B10: the model carries what each page contains, not which is showing", () => {
  // `view` was published here and is not any more. It was a second source of
  // truth for something the panel owns: with no service the model is
  // `forNullService()`, so it read "overview" while the panel was on Devices,
  // and nothing could reconcile them. `browseView` still sanitises the value —
  // the panel calls it — so the rule stays in the model layer and only the
  // STATE moved out.
  assert.strictEqual(ViewModel.EMPTY_MODEL.view, undefined)
  assert.strictEqual(ViewModel.build({}).view, undefined)
  assert.strictEqual(typeof ViewModel.browseView, "function")
  assert.strictEqual(ViewModel.browseView("devices"), "devices")
})

test("REQ-B10: build publishes both lists", () => {
  const data = snapshotOf("success_browse_full")
  const model = ViewModel.build({
    snapshot: data, level: { level: "amber" }, nowWall: 1768209240,
    browse: { view: "devices", deviceSearch: "ap", expandedDeviceId: null }
  })
  assert.ok(model.deviceList.rows.length > 0)
  assert.strictEqual(model.deviceList.searchText, "ap")
  assert.ok(model.clientList.rows.length > 0)
  // The raw arrays are NOT what the pages bind to, and the two names differ so
  // a widget cannot pick up one where it wanted the other.
  assert.ok(Array.isArray(model.deviceList.rows))
  assert.strictEqual(model.deviceList.rows, model.deviceList.rows)
})

test("REQ-B10: a model with no snapshot still has both list shapes", () => {
  // REQ-013b's rule, extended: a widget binds to one object and cannot have
  // half its bindings become undefined on the first frame — which is exactly
  // the frame where `serviceFor()` is null.
  const keys = Object.keys(ViewModel.emptyBrowseList()).sort()
  for (const model of [ViewModel.forNullService(), ViewModel.build({})]) {
    assert.deepStrictEqual(Object.keys(model.deviceList).sort(), keys)
    assert.deepStrictEqual(Object.keys(model.clientList).sort(), keys)
  }
  // A fresh object per call: one shared default behind both keys would make a
  // mutation through either visible through the other.
  assert.notStrictEqual(ViewModel.build({}).deviceList,
    ViewModel.build({}).clientList)
})

test("REQ-B10a: the chooser offers every role the site has, and no other", () => {
  // Built from `counts`, not from the rows on screen. If it read the filtered
  // rows, choosing "Switches" would leave "Switches" as the only chip left —
  // a chooser whose options move when you choose one.
  const chips = ViewModel.roleChips({
    devicesTotal: 4,
    gateways: { online: 1 }, switches: { online: 2 }, accessPoints: { online: 1 }
  })
  assert.deepStrictEqual(chips, [
    { value: "", label: "All" },
    { value: "gateway", label: "Gateways" },
    { value: "switching", label: "Switches" },
    { value: "accessPoint", label: "Access points" }
  ])
  // "All" first, because it is the unfiltered state and because `f` wrapping
  // past the last chip is how the keyboard clears the filter.
  assert.strictEqual(chips[0].value, "")

  // SPEC-AMD-3's drop rule: a role with no devices is not offered, because the
  // only thing that filter could produce is an empty list.
  const noSwitches = ViewModel.roleChips({
    devicesTotal: 2, gateways: { online: 1 }, switches: null,
    accessPoints: { online: 1 }
  })
  assert.deepStrictEqual(noSwitches.map((c) => c.value), ["", "gateway", "accessPoint"])
  const zeroed = ViewModel.roleChips({
    devicesTotal: 1, gateways: { online: 1 },
    switches: { online: 0, down: 0, impaired: 0, transitional: 0, unknown: 0 }
  })
  assert.deepStrictEqual(zeroed.map((c) => c.value), ["", "gateway"])

  // Summed over ALL five classes, as `countRows` sums it — a role present only
  // in the `unknown` class is still a role the site has.
  const onlyUnknown = ViewModel.roleChips({ devicesTotal: 1, switches: { unknown: 2 } })
  assert.deepStrictEqual(onlyUnknown.map((c) => c.value), ["", "switching"])

  // No roles, no chooser — never a lone "All" chip, which offers one choice
  // that has already been made.
  assert.deepStrictEqual(ViewModel.roleChips({ devicesTotal: 0 }), [])
  assert.deepStrictEqual(ViewModel.roleChips(null), [])

  // Every role the Overview rows can emit is offered here under the same label
  // the rest of the panel uses for it.
  for (const key of Object.keys(ViewModel.ROLE_FOR_COUNT_KEY)) {
    const role = ViewModel.ROLE_FOR_COUNT_KEY[key]
    const one = ViewModel.roleChips({ devicesTotal: 1, [key]: { online: 1 } })
    assert.deepStrictEqual(one.map((c) => c.value), ["", role], key)
    assert.strictEqual(one[1].label,
      ViewModel.ROLE_PLURAL[role].charAt(0).toUpperCase()
        + ViewModel.ROLE_PLURAL[role].slice(1))
  }
})

test("REQ-B15 (SPEC-AMD-9/10): f cycles the filter and wraps back to All", () => {
  const chips = ViewModel.roleChips({
    devicesTotal: 3,
    gateways: { online: 1 }, switches: { online: 1 }, accessPoints: { online: 1 }
  })
  assert.strictEqual(ViewModel.nextFilter(chips, ""), "gateway")
  assert.strictEqual(ViewModel.nextFilter(chips, "gateway"), "switching")
  assert.strictEqual(ViewModel.nextFilter(chips, "switching"), "accessPoint")
  // WRAPS, unlike the page keys, which SPEC-AMD-5 clamps. `f` is the only
  // keyboard route to this filter: clamped, it would strand the user on the
  // last role with no way back but the mouse.
  assert.strictEqual(ViewModel.nextFilter(chips, "accessPoint"), "")

  // A filter for a role that is no longer offered — its last device went away
  // between polls — lands on "All" rather than throwing or sticking.
  assert.strictEqual(ViewModel.nextFilter(chips, "switching-that-left"), "")
  assert.strictEqual(ViewModel.nextFilter(chips, null), "")
  // Nothing to cycle: the key does nothing rather than inventing a filter.
  assert.strictEqual(ViewModel.nextFilter([], "gateway"), "")
  assert.strictEqual(ViewModel.nextFilter(null, "gateway"), "")

  // One full lap returns to where it started, for however many roles the site
  // has. A cycle that does not close is one the user cannot undo.
  let at = ""
  for (let i = 0; i < chips.length; i++) at = ViewModel.nextFilter(chips, at)
  assert.strictEqual(at, "")
})

test("REQ-B10a: the chooser reaches the model the page is built from", () => {
  const snapshot = snapshotWith([
    device({ id: "gw", name: "Gateway", roles: ["gateway"] }),
    device({ id: "ap", name: "attic-ap", roles: ["accessPoint"] })
  ], [], { devicesTotal: 2, clients: 0, offlineTotal: 0,
           gateways: { online: 1 }, accessPoints: { online: 1 } })

  const filtered = ViewModel.deviceListModel(snapshot, { role: "accessPoint" })
  assert.deepStrictEqual(filtered.filterChips.map((c) => c.value),
    ["", "gateway", "accessPoint"])
  // `role` is what the chooser paints as selected, so it has to survive.
  assert.strictEqual(filtered.filterValue, "accessPoint")
  assert.strictEqual(filtered.rows.length, 1)
  assert.strictEqual(filtered.rows[0].nameText, "attic-ap")

  // The chips do NOT change when a filter is applied — same list, filtered or
  // not. This is the "options move when you choose one" defect.
  const unfiltered = ViewModel.deviceListModel(snapshot, {})
  assert.deepStrictEqual(unfiltered.filterChips, filtered.filterChips)
  assert.strictEqual(unfiltered.filterValue, "")
  assert.strictEqual(unfiltered.rows.length, 2)

  // The Clients page has no filter dimension and carries the key regardless —
  // one `BrowseList` renders both pages.
  assert.deepStrictEqual(ViewModel.clientListModel(snapshot, {}).filterChips, [])
  const empty = ViewModel.forNullService()
  assert.deepStrictEqual(empty.deviceList.filterChips, [])
  assert.deepStrictEqual(empty.clientList.filterChips, [])
})

test("REQ-B10a: the filter survives typing, because it is not a search modifier", () => {
  // The filter is a condition on the page and the search is a condition on the
  // rows. Neither clears the other — which is why the chooser is drawn above
  // the search field rather than beside it.
  const snapshot = snapshotWith([
    device({ id: "a", name: "attic-ap", roles: ["accessPoint"] }),
    device({ id: "b", name: "barn-ap", roles: ["accessPoint"] }),
    device({ id: "gw", name: "Gateway", roles: ["gateway"] })
  ], [], { devicesTotal: 3, clients: 0, offlineTotal: 0,
           gateways: { online: 1 }, accessPoints: { online: 2 } })
  const both = ViewModel.deviceListModel(snapshot, { role: "accessPoint", search: "barn" })
  assert.strictEqual(both.filterValue, "accessPoint")
  assert.strictEqual(both.searchText, "barn")
  assert.strictEqual(both.rows.length, 1)

  const filterOnly = ViewModel.deviceListModel(snapshot, { role: "accessPoint" })
  assert.strictEqual(filterOnly.filterValue, "accessPoint")
  assert.strictEqual(filterOnly.rows.length, 2)
})

test("REQ-B10a (SPEC-AMD-10): the Clients page offers the types it actually has", () => {
  // From the LISTED clients and not from `counts`, which carries no per-type
  // breakdown — only one total. What matters is preserved: this reads the whole
  // client array, never the filtered rows, so choosing "Wired" cannot leave
  // "Wired" standing as the only chip.
  const chips = ViewModel.clientTypeChips([
    client({ id: "1", type: "WIRELESS" }),
    client({ id: "2", type: "WIRED" }),
    client({ id: "3", type: "WIRELESS" })
  ])
  assert.deepStrictEqual(chips, [
    { value: "", label: "All" },
    { value: "WIRED", label: "Wired" },
    { value: "WIRELESS", label: "Wireless" }
  ])
  // Declaration order of CLIENT_TYPE_WORD, NOT order of appearance — the client
  // list is sorted by name, so first-seen order would reshuffle the chooser
  // between polls as clients come and go.
  assert.strictEqual(chips[1].value, "WIRED")

  // A type nobody on this site has is not offered.
  const wiredOnly = ViewModel.clientTypeChips([client({ id: "1", type: "WIRED" })])
  assert.deepStrictEqual(wiredOnly.map((c) => c.value), ["", "WIRED"])
  assert.deepStrictEqual(ViewModel.clientTypeChips([]), [])
  assert.deepStrictEqual(ViewModel.clientTypeChips(null), [])
  // A client with no type at all contributes no chip and does not crash.
  assert.deepStrictEqual(
    ViewModel.clientTypeChips([client({ id: "1", type: null })]), [])

  // protocol-v1.md says `type` is NOT a closed set. An unrecognised one is
  // still offered — labelled with the raw string, which is exactly what the row
  // beside it renders — and sorted after the known ones so the order is stable.
  const exotic = ViewModel.clientTypeChips([
    client({ id: "1", type: "ZIGBEE" }),
    client({ id: "2", type: "WIRED" }),
    client({ id: "3", type: "AURORA" })
  ])
  assert.deepStrictEqual(exotic.map((c) => c.value), ["", "WIRED", "AURORA", "ZIGBEE"])
  assert.strictEqual(exotic[2].label, "AURORA")

  // The SAME two types in the opposite order in the list. A mutant that
  // reversed the client list's order instead of sorting survived the case
  // above, because that fixture happened to hold them in reverse already —
  // one arrangement cannot distinguish "sorted" from "reversed".
  //
  // The property being pinned is stability: the client list is ordered by name
  // (REQ-B12), so an order derived from it would reshuffle the chooser every
  // time a client was renamed, connected or dropped.
  const flipped = ViewModel.clientTypeChips([
    client({ id: "1", type: "AURORA" }),
    client({ id: "2", type: "WIRED" }),
    client({ id: "3", type: "ZIGBEE" })
  ])
  assert.deepStrictEqual(flipped.map((c) => c.value), ["", "WIRED", "AURORA", "ZIGBEE"])
  assert.deepStrictEqual(flipped.map((c) => c.value), exotic.map((c) => c.value))

  // The prototype guard, for the third map indexed by a controller string.
  const poison = ViewModel.clientTypeChips([client({ id: "1", type: "constructor" })])
  assert.deepStrictEqual(poison.map((c) => c.value), ["", "constructor"])
  assert.strictEqual(poison[1].label, "constructor")
})

test("REQ-B10a (SPEC-AMD-10): the client filter matches the raw type", () => {
  const snapshot = snapshotWith([], [
    client({ id: "1", name: "laptop", type: "WIRELESS" }),
    client({ id: "2", name: "nas", type: "WIRED" }),
    client({ id: "3", name: "phone", type: "WIRELESS" })
  ])
  const wireless = ViewModel.clientListModel(snapshot, { type: "WIRELESS" })
  assert.deepStrictEqual(wireless.rows.map((r) => r.nameText), ["laptop", "phone"])
  assert.strictEqual(wireless.filterValue, "WIRELESS")
  assert.strictEqual(ViewModel.clientListModel(snapshot, {}).rows.length, 3)
  assert.strictEqual(ViewModel.clientListModel(snapshot, {}).filterValue, "")

  // The chips do not move when a filter is applied.
  assert.deepStrictEqual(wireless.filterChips,
    ViewModel.clientListModel(snapshot, {}).filterChips)

  // Matched on the RAW type, not the rendered word. "Wired" is the label and
  // "WIRED" is the value; matching the rendering would work by accident for
  // three of the four known types and not for VPN.
  assert.strictEqual(ViewModel.clientListModel(snapshot, { type: "Wireless" }).rows.length, 0)

  // The filter and the search compose, and neither clears the other.
  const both = ViewModel.clientListModel(snapshot, { type: "WIRELESS", search: "phone" })
  assert.deepStrictEqual(both.rows.map((r) => r.nameText), ["phone"])
  assert.strictEqual(both.filterValue, "WIRELESS")
  assert.strictEqual(both.searchText, "phone")
})

test("REQ-B16: an empty filtered client list names the type it was filtered to", () => {
  // "no wired clients", the way the Devices page names the role. A sentence
  // reading "no clients" over a site with forty of them is a wrong answer.
  const snapshot = snapshotWith([], [client({ id: "1", name: "laptop", type: "WIRELESS" })])
  const empty = ViewModel.clientListModel(snapshot, { type: "WIRED" })
  assert.strictEqual(empty.rows.length, 0)
  assert.ok(empty.emptyText.indexOf("wired clients") !== -1, empty.emptyText)
  // And the unfiltered empty sentence is unchanged.
  const none = ViewModel.clientListModel(snapshotWith([], []), {})
  assert.ok(none.emptyText.indexOf("clients") !== -1, none.emptyText)
  assert.strictEqual(none.emptyText.indexOf("wired"), -1, none.emptyText)
})

test("REQ-B15 (SPEC-AMD-10): f cycles the client types too", () => {
  const chips = ViewModel.clientTypeChips([
    client({ id: "1", type: "WIRED" }), client({ id: "2", type: "WIRELESS" })
  ])
  assert.strictEqual(ViewModel.nextFilter(chips, ""), "WIRED")
  assert.strictEqual(ViewModel.nextFilter(chips, "WIRED"), "WIRELESS")
  assert.strictEqual(ViewModel.nextFilter(chips, "WIRELESS"), "")
})

test("REQ-B15 / UX-008: the panel scrolls to the control the keyboard is on", () => {
  // The defect: Tab reached Refresh correctly and invisibly, three hundred
  // pixels below the bottom edge of a long device list. AC-B17 asserts Tab
  // "reaches every control" and it did — a control the user cannot see is
  // reached in every sense but the one that matters.
  const reveal = ViewModel.scrollToReveal
  const VIEW = 400
  const CONTENT = 1000
  const MARGIN = 10

  // Already fully inside: do not move. A reveal that always scrolls turns every
  // Tab into a jump, which is worse than the bug it fixes.
  assert.strictEqual(reveal(200, 30, 100, VIEW, CONTENT, MARGIN), 100)
  // Flush against the margin on both edges, still inside.
  assert.strictEqual(reveal(110, 30, 100, VIEW, CONTENT, MARGIN), 100)
  assert.strictEqual(reveal(460, 30, 100, VIEW, CONTENT, MARGIN), 100)

  // Below: scroll down by the least that reveals it, plus the margin.
  assert.strictEqual(reveal(500, 30, 100, VIEW, CONTENT, MARGIN), 140)
  // Above: scroll up to its top, less the margin.
  assert.strictEqual(reveal(50, 30, 100, VIEW, CONTENT, MARGIN), 40)

  // Clamped at both ends — never past the top, never past the end of the
  // content, which on a Flickable shows blank space that cannot be scrolled
  // back from.
  assert.strictEqual(reveal(0, 30, 100, VIEW, CONTENT, MARGIN), 0)
  assert.strictEqual(reveal(5, 30, 100, VIEW, CONTENT, MARGIN), 0)
  assert.strictEqual(reveal(980, 20, 0, VIEW, CONTENT, MARGIN), CONTENT - VIEW)

  // Taller than the viewport: align the TOP. Aligning the bottom would push its
  // first line off the top of the panel, and the first line carries the label.
  assert.strictEqual(reveal(300, 500, 0, VIEW, CONTENT, MARGIN), 290)
  assert.strictEqual(reveal(300, 500, 600, VIEW, CONTENT, MARGIN), 290)

  // Nothing scrolls, so the top is the only honest answer: a non-zero position
  // on a panel that fits is already wrong.
  assert.strictEqual(reveal(200, 30, 50, VIEW, 300, MARGIN), 0)
  assert.strictEqual(reveal(200, 30, 50, VIEW, VIEW, MARGIN), 0)

  // No margin asked for, none applied.
  assert.strictEqual(reveal(500, 30, 100, VIEW, CONTENT, 0), 130)
  assert.strictEqual(reveal(500, 30, 100, VIEW, CONTENT, undefined), 130)
})

test("REQ-B15 / UX-008: a measurement mid-layout never moves the panel", () => {
  // Every argument is a number QML computes during layout, where NaN and
  // undefined are ordinary intermediate states — a Repeater's delegate has no
  // height for a frame, and `mapToItem` on an unparented item returns NaN.
  //
  // A NaN reaching `contentY` scrolls the panel to nowhere and it does not come
  // back, so the guard returns the CURRENT position rather than trusting the
  // arithmetic to fail harmlessly.
  const reveal = ViewModel.scrollToReveal
  for (const bad of [NaN, undefined, null, "300", {}, Infinity, -Infinity]) {
    assert.strictEqual(reveal(bad, 30, 120, 400, 1000, 10), 120, "top " + String(bad))
    assert.strictEqual(reveal(300, bad, 120, 400, 1000, 10), 120, "height " + String(bad))
    assert.strictEqual(reveal(300, 30, 120, bad, 1000, 10), 120, "viewport " + String(bad))
    assert.strictEqual(reveal(300, 30, 120, 400, bad, 10), 120, "content " + String(bad))
    // A bad MARGIN is not a reason to refuse: it is the one argument with a
    // sane default, so it falls back to none and the scroll still happens.
    assert.strictEqual(reveal(500, 30, 100, 400, 1000, bad), 130, "margin " + String(bad))
  }
  // A bad CURRENT position cannot be preserved, so it becomes the top rather
  // than propagating.
  assert.strictEqual(reveal(NaN, 30, NaN, 400, 1000, 10), 0)
  // And every good path returns a real number, never NaN.
  for (const top of [0, 50, 300, 980]) {
    const out = reveal(top, 30, 100, 400, 1000, 10)
    assert.ok(Number.isFinite(out), "top " + top + " gave " + out)
  }
})

// --- AC-B18: the list keeps its place across a rebuild --------------------
//
// The defect: a 200-device site, mouse-scrolled to the bottom, snapped back to
// the top every five seconds and did it forever. `deviceListModel` builds a new
// rows array on every recompute and the service recomputes on the freshness
// tick whether or not anything moved (REQ-B17) — and a `ListView` handed a new
// array sends `contentY` to 0. The list's `currentIndex` was the only thing
// putting it back, and that is -1 whenever the list is not the keyboard's stop,
// which is every mouse user.
//
// Nothing about this is visible to the harness in under a five-minute run, and
// every branch below is a jump the reader sees.

// A list model reduced to the two fields the rule reads. Written as a helper
// rather than passing whole `deviceListModel` outputs, so a test that changes
// the search term cannot also change six other fields by accident.
function shownAs(searchText, filterValue) {
  return { searchText: searchText, filterValue: filterValue }
}

test("AC-B18: the five-second poll leaves the list where the reader put it", () => {
  const after = ViewModel.scrollAfterRowsChange
  const VIEW = 320
  const CONTENT = 4000

  // The poll rebuilds the rows and changes nothing about the page: same search,
  // same filter, same site. The position is the reader's and stays theirs.
  const was = shownAs("", "")
  const now = shownAs("", "")
  assert.strictEqual(after(was, now, 3000, CONTENT, VIEW), 3000)
  assert.strictEqual(after(was, now, 1, CONTENT, VIEW), 1)
  // Including a filtered page that is merely being refreshed — the filter is
  // not new, so neither is the question the reader is scrolling through.
  assert.strictEqual(after(shownAs("ap", "gateway"), shownAs("ap", "gateway"),
    2000, CONTENT, VIEW), 2000)
  // The top is the top. A rule that "restored" a position onto a list already
  // at the top would be indistinguishable from the defect it replaces.
  assert.strictEqual(after(was, now, 0, CONTENT, VIEW), 0)
})

test("AC-B18: a search or a filter change puts the list back at the top", () => {
  const after = ViewModel.scrollAfterRowsChange
  const VIEW = 320
  const CONTENT = 4000

  // These rows are a different question's answer. `Panel.setSearch` and
  // `Panel.setFilter` put the cursor back to 0 on the same keystroke, and a
  // viewport left half-way down a fresh set of matches would both disagree with
  // the cursor and hide the match the reader typed towards.
  assert.strictEqual(after(shownAs("", ""), shownAs("ap", ""), 3000, CONTENT, VIEW), 0)
  assert.strictEqual(after(shownAs("ap", ""), shownAs("", ""), 3000, CONTENT, VIEW), 0)
  assert.strictEqual(after(shownAs("ap", ""), shownAs("apx", ""), 3000, CONTENT, VIEW), 0)
  // REQ-B10a's filter, on either page — the Devices role and the Clients
  // connection type reach this through the one field for that reason.
  assert.strictEqual(after(shownAs("", ""), shownAs("", "gateway"), 3000, CONTENT, VIEW), 0)
  assert.strictEqual(after(shownAs("", "wired"), shownAs("", ""), 3000, CONTENT, VIEW), 0)

  // The FIRST model a page ever shows has no predecessor, and a list being
  // populated for the first time belongs at the top.
  assert.strictEqual(after(null, shownAs("", ""), 3000, CONTENT, VIEW), 0)
  assert.strictEqual(after(undefined, shownAs("ap", ""), 3000, CONTENT, VIEW), 0)
  assert.strictEqual(after(shownAs("", ""), null, 3000, CONTENT, VIEW), 0)
  // A model missing the fields entirely reads as "no search, no filter" on both
  // sides and is therefore not a change — the page is being polled, not asked
  // a new question.
  assert.strictEqual(after({}, {}, 3000, CONTENT, VIEW), 3000)
  // `emptyBrowseList` is that model in practice: it arrives whenever a page is
  // drawn without a snapshot (REQ-013b), and two of them in a row are two polls
  // of the same empty page.
  const blank = ViewModel.emptyBrowseList()
  assert.strictEqual(after(blank, ViewModel.emptyBrowseList(), 3000, CONTENT, VIEW), 3000)
})

test("AC-B18: a position past the end of a shortened list is clamped", () => {
  const after = ViewModel.scrollAfterRowsChange
  const VIEW = 320
  const same = [shownAs("ap", ""), shownAs("ap", "")]

  // The site shrank under the reader — devices went offline, or the controller
  // returned fewer — with the search unchanged. `contentY` past the end of a
  // Flickable shows blank space it will not scroll back from.
  assert.strictEqual(after(same[0], same[1], 3000, 1000, VIEW), 1000 - VIEW)
  assert.strictEqual(after(same[0], same[1], 680, 1000, VIEW), 680)
  // Nothing left to scroll: a list shorter than its viewport, and an emptied
  // one. The top is the only position either of them has.
  assert.strictEqual(after(same[0], same[1], 3000, 200, VIEW), 0)
  assert.strictEqual(after(same[0], same[1], 3000, VIEW, VIEW), 0)
  assert.strictEqual(after(same[0], same[1], 3000, 0, VIEW), 0)
  // And never above the top, which a negative `contentY` — an overscrolled
  // Flickable caught mid-bounce — would otherwise carry across.
  assert.strictEqual(after(same[0], same[1], -50, 4000, VIEW), 0)
})

test("AC-B18: a measurement mid-layout never moves the list", () => {
  // `contentHeight` is NaN for a frame while a ListView regenerates its
  // delegates, and 0 while it has none — the swap this rule runs on is exactly
  // when both are true. A NaN reaching `contentY` scrolls the list to nowhere
  // and it does not come back, so an unreadable measurement leaves the position
  // alone rather than trusting the arithmetic to fail harmlessly. The rule
  // `scrollToReveal` follows, for the same reason.
  const after = ViewModel.scrollAfterRowsChange
  const was = shownAs("", "")
  for (const bad of [NaN, undefined, null, "4000", {}, Infinity, -Infinity]) {
    assert.strictEqual(after(was, was, 900, bad, 320), 900, "content " + String(bad))
    assert.strictEqual(after(was, was, 900, 4000, bad), 900, "viewport " + String(bad))
  }
  // A position that cannot be read becomes the top rather than propagating.
  assert.strictEqual(after(was, was, NaN, 4000, 320), 0)
  assert.strictEqual(after(was, was, undefined, 4000, 320), 0)
  // Every good path returns a real number.
  for (const at of [0, 100, 3680, 5000]) {
    const out = after(was, was, at, 4000, 320)
    assert.ok(Number.isFinite(out), at + " gave " + out)
  }
})

test("AC-B18: the rule reads the fields the two list models actually publish", () => {
  // Pinned to the models rather than to the helper above. `searchText` and
  // `filterValue` are the names both pages carry — `filterValue` and not
  // `role`, precisely so one rule can serve both — and a rename that left this
  // function reading `undefined` on both sides would make every comparison
  // above equal and the list would simply never return to the top.
  const data = snapshotOf("success_browse_full")
  const plain = ViewModel.build({ snapshot: data, nowWall: 1768209240 })
  const searched = ViewModel.build({
    snapshot: data, nowWall: 1768209240, browse: { deviceSearch: "ap" }
  })
  assert.ok("searchText" in plain.deviceList && "filterValue" in plain.deviceList)
  assert.ok("searchText" in plain.clientList && "filterValue" in plain.clientList)
  assert.notStrictEqual(plain.deviceList.searchText, searched.deviceList.searchText)
  assert.strictEqual(ViewModel.scrollAfterRowsChange(
    plain.deviceList, searched.deviceList, 3000, 4000, 320), 0)
  // Two consecutive polls of the same page: the rows are rebuilt, the page is
  // the same page, and the reader keeps their place.
  assert.strictEqual(ViewModel.scrollAfterRowsChange(
    plain.deviceList, ViewModel.build({ snapshot: data, nowWall: 1768209245 }).deviceList,
    3000, 4000, 320), 3000)
})

test("REQ-B10: the two list models have identical shapes", () => {
  // They are rendered by the same delegate machinery in Phase B3. A key present
  // on one and not the other is a binding that silently reads undefined on one
  // of the two pages.
  const data = snapshotOf("success_browse_full")
  const model = ViewModel.build({ snapshot: data, nowWall: 1768209240 })
  assert.deepStrictEqual(Object.keys(model.deviceList).sort(),
    Object.keys(model.clientList).sort())
})

test("REQ-B14: a controller string cannot reach Object.prototype", () => {
  // `clients[].type` is explicitly NOT a closed set (protocol-v1.md), and
  // neither `devices[].id` nor `uplinkDeviceId` is validated beyond "a
  // non-empty string". A plain `map[key]` therefore answers "valueOf" with a
  // FUNCTION, and `clientTypeWord("valueOf")` rendered
  // `function valueOf() { [native code] }` into a client row — a line of engine
  // internals where a word should be, from one word in one API response.
  const poison = ["constructor", "toString", "valueOf", "hasOwnProperty",
    "__proto__", "isPrototypeOf"]
  for (const key of poison) {
    assert.strictEqual(typeof ViewModel.clientTypeWord(key), "string", key)
    assert.strictEqual(ViewModel.clientTypeWord(key), key, key)

    const names = ViewModel.uplinkNames([device({ id: "real", name: "Real" })])
    assert.strictEqual(ViewModel.uplinkNameFor(names, key), "an unlisted device", key)

    const row = ViewModel.browseClientRow(
      client({ id: "c", name: "x", type: key }), "", null)
    assert.strictEqual(row.metaText.indexOf("native code"), -1, key)

    // The role filter's noun, and the class ranking, by the same route.
    const empty = ViewModel.deviceListModel(snapshotWith([], []), { role: key })
    assert.strictEqual(typeof empty.emptyText, "string", key)
    assert.strictEqual(empty.emptyText, "No devices to show.", key)
    assert.strictEqual(typeof ViewModel.browseOrderKey(device({ class: key })).rank,
      "number", key)

    // `classWord` and `wanStatusWord` predate this and had the same lookup.
    // They rendered engine internals on their own; once `browseDeviceRow`
    // passed the result through `wordCase` it THREW, and a throw inside `build`
    // costs the whole model rather than one word. `class` is validated by
    // `checkDeviceRecord`, so both are backstops — backstops that had stopped
    // working.
    assert.strictEqual(ViewModel.classWord(key), "unknown", key)
    assert.strictEqual(ViewModel.wanStatusWord(key), "Unknown", key)
    assert.strictEqual(ViewModel.browseDeviceRow(device({ class: key })).classText,
      "Unknown", key)
  }
})

test("REQ-003: the prototype guard did not break the words it guards", () => {
  // The other half. A guard that returned the fallback for EVERY key would pass
  // the test above and would render every device "unknown".
  const poison = ["constructor", "toString", "valueOf", "hasOwnProperty",
    "__proto__", "isPrototypeOf"]
  for (const klass of ["online", "down", "impaired", "unknown", "transitional"]) {
    assert.strictEqual(ViewModel.classWord(klass), ViewModel.CLASS_WORD[klass], klass)
  }
  for (const status of ["up", "down", "degraded", "unknown"]) {
    assert.strictEqual(ViewModel.wanStatusWord(status),
      ViewModel.WAN_STATUS_WORD[status], status)
  }
  for (const key of poison) {
    // `CLASS_WORD[key]` is NOT undefined for these — that is the entire defect.
    // The map has no OWN entry, and the lookup is what has to say so.
    assert.strictEqual(
      Object.prototype.hasOwnProperty.call(ViewModel.CLASS_WORD, key), false, key)
  }
})

test("REQ-B14: a device named after a prototype member still resolves as an uplink", () => {
  // The other half: the guard must not make a legitimate name unreachable. A
  // device whose id happens to be "toString" is absurd but permitted, and it
  // must resolve to ITS name rather than to the fallback.
  const names = ViewModel.uplinkNames([device({ id: "toString", name: "Odd Switch" })])
  assert.strictEqual(ViewModel.uplinkNameFor(names, "toString"), "Odd Switch")
})

// --- REQ-B15: the focus order --------------------------------------------

test("REQ-B15: Tab cycles the five stops in the order the spec states", () => {
  // Parsed out of SPEC-v1.1-browse.md rather than restated, so reordering the
  // requirement fails this build instead of leaving the panel walking an order
  // the spec no longer asks for.
  const rule = /\*\*REQ-B15 — keyboard[^*]*\*\*\s*Tab cycles ([^.]+)\./.exec(BROWSE_SPEC)
  assert.ok(rule, "SPEC-v1.1-browse.md: could not locate REQ-B15's Tab order")
  const named = rule[1].replace(/\s+and wraps$/, "").split("→").map((s) => s.trim())
  assert.deepStrictEqual(named,
    ["Refresh", "segmented control", "search", "list", "Open UniFi"])
  assert.strictEqual(ViewModel.focusStops("devices").length, named.length)
  assert.deepStrictEqual(ViewModel.focusStops("devices"),
    ["refresh", "segments", "search", "list", "dashboard"])
  assert.deepStrictEqual(ViewModel.focusStops("clients"),
    ViewModel.focusStops("devices"))
})

test("REQ-B15: Overview has four stops — no search, and the Inventory rows as its list", () => {
  // SPEC-AMD-13. The Inventory rows are the only navigation on Overview
  // besides the page chips, and they were reachable only by pointer.
  assert.deepStrictEqual(ViewModel.focusStops("overview"),
    ["refresh", "segments", "list", "dashboard"])
  // The default, and anything unrecognised, is Overview — so a junk view value
  // cannot produce a stop list with a search field the panel is not drawing.
  for (const junk of [null, undefined, "", "Devices", 7]) {
    assert.deepStrictEqual(ViewModel.focusStops(junk),
      ["refresh", "segments", "list", "dashboard"], JSON.stringify(junk))
  }
})

test("REQ-B15: a stop the panel is not drawing is not a stop", () => {
  // SPEC-AMD-13. With no snapshot the segmented control and every list are
  // hidden, and Refresh is the one action that can change that — so it is
  // where the panel opens, and Tab goes between it and Open UniFi.
  const cold = { hasSnapshot: false, hasList: false }
  for (const view of ["overview", "devices", "clients"]) {
    assert.deepStrictEqual(ViewModel.focusStops(view, cold), ["refresh", "dashboard"], view)
    assert.strictEqual(ViewModel.nextFocus(view, "refresh", 1, cold), "dashboard", view)
    assert.strictEqual(ViewModel.nextFocus(view, "segments", 1, cold), "refresh", view)
  }
  assert.strictEqual(ViewModel.homeFocus(cold), "refresh")
  assert.strictEqual(ViewModel.homeFocus({ hasSnapshot: true }), "segments")
  assert.strictEqual(ViewModel.homeFocus(undefined), "segments")

  // A list emptied by a search is skipped rather than walked onto, where Tab
  // would put the cursor on a hidden ListView with no ring.
  const empty = { hasSnapshot: true, hasList: false }
  assert.deepStrictEqual(ViewModel.focusStops("devices", empty),
    ["refresh", "segments", "search", "dashboard"])
  assert.strictEqual(ViewModel.nextFocus("devices", "search", 1, empty), "dashboard")
})

test("REQ-B15: a stop that goes away under the cursor settles somewhere drawn", () => {
  const empty = { hasSnapshot: true, hasList: false }
  // The list emptied while the cursor was on it: the search field above is
  // what the user is most likely to change next.
  assert.strictEqual(ViewModel.settleFocus("devices", "list", empty), "search")
  // Overview has no search field, so its emptied list goes home.
  assert.strictEqual(ViewModel.settleFocus("overview", "list", empty), "segments")
  // The snapshot lost: everything but Refresh and Open UniFi goes to Refresh.
  const cold = { hasSnapshot: false, hasList: false }
  assert.strictEqual(ViewModel.settleFocus("devices", "search", cold), "refresh")
  assert.strictEqual(ViewModel.settleFocus("overview", "segments", cold), "refresh")
  assert.strictEqual(ViewModel.settleFocus("overview", "dashboard", cold), "dashboard")
  // A stop still drawn is left alone.
  assert.strictEqual(ViewModel.settleFocus("devices", "list", { hasSnapshot: true, hasList: true }), "list")
})

test("REQ-B15: Tab wraps in both directions", () => {
  assert.strictEqual(ViewModel.nextFocus("devices", "dashboard", 1), "refresh")
  assert.strictEqual(ViewModel.nextFocus("devices", "refresh", -1), "dashboard")
  assert.strictEqual(ViewModel.nextFocus("overview", "dashboard", 1), "refresh")
  assert.strictEqual(ViewModel.nextFocus("overview", "refresh", -1), "dashboard")
  // A full cycle returns to where it started, in both directions, on both stop
  // lists — which is what "wraps" means and what an off-by-one in the modulo
  // would break without changing any single step.
  for (const view of ["overview", "devices"]) {
    for (const direction of [1, -1]) {
      let stop = "segments"
      const stops = ViewModel.focusStops(view)
      const seen = []
      for (let i = 0; i < stops.length; i++) {
        seen.push(stop)
        stop = ViewModel.nextFocus(view, stop, direction)
      }
      assert.strictEqual(stop, "segments", view + " " + direction)
      assert.strictEqual(new Set(seen).size, stops.length,
        view + " " + direction + " visited " + seen.join(","))
    }
  }
})

test("REQ-B15: a direction of zero does not move, and an unknown stop resets", () => {
  assert.strictEqual(ViewModel.nextFocus("devices", "list", 0), "list")
  // `PanelKeyCatcher.moveRequested` delivers (dx, dy) and one of them is always
  // zero, so "zero does not move" is not a hypothetical — it is every arrow
  // press on the other axis.
  assert.strictEqual(ViewModel.nextFocus("devices", "nonsense", 1), "segments")
  assert.strictEqual(ViewModel.nextFocus("overview", "search", 1), "segments")
})

test("REQ-B15: a view change keeps the stop when it survives and resets when it cannot", () => {
  // Devices to Clients has the same five stops, so focus stays where it is —
  // moving it would be the panel taking the cursor away from a user who
  // switched pages to look at the same thing in a different list.
  assert.strictEqual(ViewModel.focusAfterViewChange("clients", "list"), "list")
  assert.strictEqual(ViewModel.focusAfterViewChange("devices", "search"), "search")
  // Overview has no search field, so the cursor goes back to the control that
  // got it here; the list stop survives, as the Inventory rows (SPEC-AMD-13).
  assert.strictEqual(ViewModel.focusAfterViewChange("overview", "search"), "segments")
  assert.strictEqual(ViewModel.focusAfterViewChange("overview", "list"), "list")
  assert.strictEqual(ViewModel.focusAfterViewChange("overview", "list",
    { hasSnapshot: true, hasList: false }), "segments")
  // And the stops Overview does have are left alone.
  assert.strictEqual(ViewModel.focusAfterViewChange("overview", "refresh"), "refresh")
  assert.strictEqual(ViewModel.focusAfterViewChange("overview", "dashboard"), "dashboard")
})

test("REQ-B15: a hover never takes the stop from the caret in the search field", () => {
  // `/` puts the caret in the search field and `PanelKeyCatcher` is blocked for
  // as long as it is there. A hover that moved the stop to the list then drew
  // the ring around a row while the keystrokes went on filtering the search
  // box — the focus the user can SEE and the focus that receives keys naming
  // two different controls, and the next Tab starting from the invisible one.
  // The pointer really has moved in this case; it is still not a reason.
  const moved = [{ x: 10, y: 10 }, { x: 10, y: 40 }]
  for (const stop of ["segments", "search", "list", "refresh", "dashboard"]) {
    assert.strictEqual(
      ViewModel.focusAfterHover(stop, true, moved[0], moved[1]), stop, stop)
  }
  // And with the caret elsewhere the same hover does move it, so the case above
  // is the search field's doing and not the rule refusing everything.
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", false, moved[0], moved[1]), "list")
})

test("REQ-B15: a hover from a pointer that has not moved moves nothing", () => {
  // The 5 s freshness tick replaces the rows array, ListView rebuilds every
  // delegate, and Qt re-delivers hover to whatever is under the pointer.
  // Measured on Qt 6.11.2: the rebuilt MouseArea reports `containsMouse` and
  // raises `entered` and `positionChanged` at the same coordinates a real
  // movement produces, so the POSITION is the only thing that separates them.
  // Without this the stop was dragged off Refresh and back onto the list every
  // five seconds, indefinitely, with the mouse untouched.
  const at = { x: 120, y: 88 }
  for (const stop of ["segments", "search", "list", "refresh", "dashboard"]) {
    assert.strictEqual(ViewModel.focusAfterHover(stop, false, at, { x: 120, y: 88 }),
      stop, stop)
  }
  // One axis at a time: a rule that compared only x would let the poll through
  // on every list, because a row rebuild keeps the pointer's x exactly.
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", false, at, { x: 121, y: 88 }), "list")
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", false, at, { x: 120, y: 89 }), "list")
})

test("REQ-B15: a pointer that has arrived, or moved, does take the stop", () => {
  // The first hover of a browse has nothing to compare against and is a pointer
  // that has just arrived, so it counts. Refusing it would leave the mouse
  // unable to move the stop at all until it had hovered twice.
  assert.strictEqual(
    ViewModel.focusAfterHover("segments", false, null, { x: 1, y: 2 }), "list")
  assert.strictEqual(
    ViewModel.focusAfterHover("segments", false, undefined, { x: 1, y: 2 }), "list")
  // Re-entering the row the cursor is already on is a real movement and is
  // honoured: the pointer left and came back, and it names a position it was
  // not at. This is the case an index comparison — "the hover names the row we
  // are already on, so ignore it" — would have thrown away.
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", false, { x: 40, y: 60 }, { x: 40, y: 61 }),
    "list")
})

test("REQ-B15: an unreadable position is not movement", () => {
  // The panel always has a position, so this is a backstop rather than a live
  // path — and it is pointed the safe way. Treating a junk value as movement
  // would hand the poll back the stop it was stealing, which is the defect;
  // treating it as none costs a hover the mouse can repeat.
  const was = { x: 1, y: 2 }
  for (const junk of [null, undefined, {}, { x: 1 }, { x: "1", y: "2" },
    { x: NaN, y: 2 }, { x: 1, y: Infinity }, 7, "x,y"]) {
    assert.strictEqual(ViewModel.focusAfterHover("refresh", false, was, junk),
      "refresh", JSON.stringify(junk))
  }
  // A junk PRIOR position is the "we have nothing to compare against" case, and
  // reads as an arrival.
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", false, { x: NaN, y: 2 }, { x: 1, y: 2 }),
    "list")
})

test("REQ-B15: the stop a hover moves to is the list stop, by its constant", () => {
  assert.strictEqual(
    ViewModel.focusAfterHover("segments", false, null, { x: 0, y: 0 }),
    ViewModel.FOCUS_LIST)
  // `searchHasCaret` is compared with `=== true`: the panel binds a bool, and a
  // rule that accepted any truthy value would let a stray non-empty string
  // suspend hover focus for the rest of the session.
  assert.strictEqual(
    ViewModel.focusAfterHover("refresh", "no", { x: 1, y: 1 }, { x: 2, y: 2 }),
    "list")
})

test("REQ-B15: every stop name is a constant, so no caller spells one", () => {
  // The names are compared with `===` in QML and a misspelling there is a stop
  // that silently never matches — Tab would appear to skip it. Exported so
  // `Panel.qml` binds to the constant rather than to a string literal.
  const constants = [ViewModel.FOCUS_REFRESH, ViewModel.FOCUS_SEGMENTS,
    ViewModel.FOCUS_SEARCH, ViewModel.FOCUS_LIST, ViewModel.FOCUS_DASHBOARD]
  assert.deepStrictEqual(ViewModel.focusStops("devices"), constants)
  assert.strictEqual(new Set(constants).size, constants.length)
})

test("REQ-B14: both row kinds carry the same secondary line, under the same name", () => {
  // Phase B3 renders one delegate for both lists. `modelText` and `ipText`
  // occupy the same place on screen and mean the same thing — the second line
  // of identity — so the delegate binds one name and the two row builders
  // decide what fills it.
  const deviceRow = ViewModel.browseDeviceRow(
    device({ id: "1", name: "Attic AP", model: "U6-Pro" }))
  assert.strictEqual(deviceRow.secondaryText, "U6-Pro")
  assert.strictEqual(deviceRow.secondaryText, deviceRow.modelText)

  const clientRow = ViewModel.browseClientRow(
    client({ id: "2", name: "pi", ipAddress: "192.0.2.40" }), "", null)
  assert.strictEqual(clientRow.secondaryText, "192.0.2.40")
  assert.strictEqual(clientRow.secondaryText, clientRow.ipText)

  // Present on every row of both lists, so the delegate never binds undefined.
  const data = snapshotOf("success_browse_full")
  const model = ViewModel.build({ snapshot: data, nowWall: 1768209240 })
  for (const list of [model.deviceList, model.clientList]) {
    for (const row of list.rows) {
      assert.strictEqual(typeof row.secondaryText, "string", row.id)
    }
  }
})

test("REQ-B14: the status word is a field of its own, not the head of the meta line", () => {
  // The row draws it as a fixed right-hand column so the eye can run down one
  // edge and find every broken device. While it was the first of four
  // `·`-joined segments there was nothing for the delegate to bind.
  //
  // Nothing asserted the class word was EVER in `metaText` — the three existing
  // assertions about that string are all negative ("the MAC is not in it", "up
  // is not in it") — so moving it out of the line broke no test at all. These
  // are the positive statements that were missing.
  const row = ViewModel.browseDeviceRow(
    device({ id: "1", name: "Attic AP", class: "down", ipAddress: "192.0.2.7" }))
  assert.strictEqual(row.tokenText, "Down")
  assert.strictEqual(row.tokenText, row.classText)
  assert.strictEqual(row.metaText.indexOf("Down"), -1,
    "the class word is still in the meta line as well as the token")
  assert.ok(row.metaText.indexOf("192.0.2.7") !== -1,
    "the meta line lost the address when it lost the class word")

  const clientRow = ViewModel.browseClientRow(
    client({ id: "2", name: "pi", type: "WIRED" }), "sw", null)
  assert.strictEqual(clientRow.tokenText, "Wired")
  assert.strictEqual(clientRow.tokenText, clientRow.typeText)
  assert.strictEqual(clientRow.metaText.indexOf("Wired"), -1)
  assert.ok(clientRow.metaText.indexOf("via sw") !== -1)
})

test("F14: the port grid carries its counts in words", () => {
  // The ports render as a mark each rather than a row each — 48 rows inside a
  // 560 px popup is a detail that cannot be opened. A mark is not a word, so
  // this sentence is what keeps the shape from being the only signal (UX-002)
  // and what a bug report can quote.
  const summary = ViewModel.portsSummaryText([
    { state: "UP", poe: { enabled: true, standard: "802.3at" } },
    { state: "UP", poe: null },
    { state: "DOWN", poe: null }
  ])
  assert.strictEqual(summary, "2 up  \u00b7  1 down  \u00b7  1 PoE")

  // PoE switched off is not PoE. `poeText` says "PoE off" for it, which is
  // non-empty, and a count built on that drew a switch with PoE disabled on
  // every port as a PoE switch.
  assert.strictEqual(ViewModel.portsSummaryText([
    { state: "UP", poe: { enabled: false, standard: "802.3at" } },
    { state: "DOWN", poe: { enabled: false } }
  ]), "1 up  \u00b7  1 down")
  assert.strictEqual(ViewModel.portRow({ idx: 1, poe: { enabled: false } }).poeLive, false)
  assert.strictEqual(ViewModel.portRow({ idx: 1, poe: { enabled: true } }).poeLive, true)
  assert.strictEqual(ViewModel.portRow({ idx: 1, poe: null }).poeLive, false)

  // A class with nothing in it is dropped rather than printed as a zero — the
  // same rule countRows applies to the Overview, for the same reason.
  assert.strictEqual(
    ViewModel.portsSummaryText([{ state: "UP", poe: null }]), "1 up")
  assert.strictEqual(
    ViewModel.portsSummaryText([{ state: "DOWN", poe: null }]), "1 down")

  // Nothing for an empty array. `portsEmptyText` already says that, and says
  // it only in the case where it is true — the detail was actually fetched.
  assert.strictEqual(ViewModel.portsSummaryText([]), "")
  assert.strictEqual(ViewModel.portsSummaryText(null), "")
})

test("F07: the status column prints exceptions, not the expected state", () => {
  // The column exists so the eye can run down one edge and find the broken
  // thing. On a healthy site it printed "Online" once per row in the quietest
  // colour on screen — a column whose value never varies is one the eye stops
  // checking, which loses it on the day it has something to say.
  const up = ViewModel.browseDeviceRow(
    device({ id: "1", name: "Attic AP", class: "online", ipAddress: "192.0.2.7" }))
  assert.strictEqual(up.tokenText, "")
  // The word itself is not lost — `classText` carries it unconditionally, and
  // the Overview's offline list reads that one.
  assert.strictEqual(up.classText, "Online")

  // Only `online` is suppressed, because only `online` is expected. Everything
  // else is the exception the column is there to show.
  for (const cls of ["down", "impaired", "transitional", "unknown"]) {
    const row = ViewModel.browseDeviceRow(device({ id: "2", name: "x", class: cls }))
    assert.notStrictEqual(row.tokenText, "",
      cls + " must keep its word: it is not the expected state")
    assert.strictEqual(row.tokenText, row.classText)
  }

  // A client keeps its word even though its column is the more repetitive of
  // the two. There is no expected value to suppress — wired and wireless are
  // equally ordinary — and the filter chips default to All, so dropping it
  // would delete the only place the connection type appears rather than
  // quieting a redundancy.
  const wired = ViewModel.browseClientRow(
    client({ id: "3", name: "pi", type: "WIRED" }), "sw", null)
  assert.strictEqual(wired.tokenText, "Wired")
})

test("REQ-B14: the delegate is told WHICH states are urgent, and does not decide", () => {
  // `down` is not the only one. The delegate colours on this flag rather than
  // comparing class words itself (REQ-014), which is also what stops the two
  // lists needing two delegates.
  const urgent = { down: true, impaired: true,
                   online: false, unknown: false, transitional: false }
  for (const cls of Object.keys(urgent)) {
    assert.strictEqual(
      ViewModel.browseDeviceRow(device({ id: "1", class: cls })).tokenUrgent,
      urgent[cls], cls)
  }
  // A client is connected or it is not in the list, so it has no urgent state —
  // but it carries the field, because the two row shapes must stay identical.
  assert.strictEqual(
    ViewModel.browseClientRow(client({ id: "2" }), "", null).tokenUrgent, false)
})

test("REQ-B14: an expanded client shows its IP address, labelled", () => {
  // It is on the collapsed row too, as `secondaryText` — unlabelled, and in a
  // column that elides. An address the reader has to infer the meaning of is
  // not one they can act on.
  const detail = ViewModel.clientDetail(
    client({ id: "1", name: "pi", ipAddress: "192.0.2.40" }), "")
  const ip = detail.rows.filter((r) => r.key === "ip")
  assert.strictEqual(ip.length, 1)
  assert.strictEqual(ip[0].label, "IP")
  assert.strictEqual(ip[0].value, "192.0.2.40")
  // BIZ-003 / AC-B10: absent is "unknown", never blank and never an invented 0.
  const none = ViewModel.clientDetail(client({ id: "2", ipAddress: null }), "")
  assert.strictEqual(none.rows.filter((r) => r.key === "ip")[0].value, "unknown")
})

test("REQ-B15 (SPEC-AMD-5): Left/Right walk the pages and clamp at both ends", () => {
  // The arithmetic lives here now because the key is no longer modal: it moves
  // pages from every focus stop, so the panel has nowhere sensible to keep a
  // branch that only applied on one of them.
  assert.strictEqual(ViewModel.nextBrowseView("overview", 1), "devices")
  assert.strictEqual(ViewModel.nextBrowseView("devices", 1), "clients")
  assert.strictEqual(ViewModel.nextBrowseView("clients", -1), "devices")
  assert.strictEqual(ViewModel.nextBrowseView("devices", -1), "overview")

  // CLAMPED, not wrapped — and asserted at both ends, because a modulo and a
  // clamp agree everywhere except exactly here.
  assert.strictEqual(ViewModel.nextBrowseView("clients", 1), "clients")
  assert.strictEqual(ViewModel.nextBrowseView("overview", -1), "overview")

  // A direction of zero is not a move.
  assert.strictEqual(ViewModel.nextBrowseView("devices", 0), "devices")

  // Junk resolves through browseView first, so a corrupted view cannot land
  // the user on a fourth page or on `undefined`.
  assert.strictEqual(ViewModel.nextBrowseView("nonsense", 1), "devices")
  assert.strictEqual(ViewModel.nextBrowseView(null, -1), "overview")
})

test("REQ-B14: downlinks are the inverse of uplinkDeviceId, and nothing more", () => {
  // The API carries no per-port peer — `interfaces.ports[]` is idx, connector,
  // speed, state, PoE and nothing else — so "port 5 goes to the kitchen switch"
  // is not derivable and is not attempted. Inverting device-level topology is
  // the whole of what can honestly be said.
  const devices = [
    device({ id: "gw", name: "Gateway" }),
    device({ id: "a", name: "attic-ap", uplinkDeviceId: "gw" }),
    device({ id: "b", name: "Kitchen SW", uplinkDeviceId: "gw" }),
    device({ id: "c", name: "garage-ap", uplinkDeviceId: "b" })
  ]
  const map = ViewModel.downlinkNames(devices)
  assert.deepStrictEqual(map.gw.slice().sort(), ["Kitchen SW", "attic-ap"])
  assert.deepStrictEqual(map.b, ["garage-ap"])

  // Case-insensitive order, so a capitalised name does not sort into its own
  // block ahead of everything lowercase.
  assert.strictEqual(ViewModel.downlinkText(map, "gw"),
    "2 devices — attic-ap, Kitchen SW")
  assert.strictEqual(ViewModel.downlinkText(map, "b"), "1 device — garage-ap")

  // A leaf says so rather than showing an empty row. BIZ-003 is not in play —
  // this is a known zero, not an unknown.
  assert.strictEqual(ViewModel.downlinkText(map, "c"), "none")
  assert.strictEqual(ViewModel.downlinkText(map, "no-such-id"), "none")
})

test("REQ-B14: a long downlink list is counted in full and named in part", () => {
  const devices = [device({ id: "gw", name: "Gateway" })]
  for (let i = 0; i < 7; i++) {
    devices.push(device({ id: "d" + i, name: "sw-" + i, uplinkDeviceId: "gw" }))
  }
  const text = ViewModel.downlinkText(ViewModel.downlinkNames(devices), "gw")
  // The COUNT is all seven — read from the array, so it cannot disagree with
  // the list beside it — while only the first four are named.
  assert.ok(text.indexOf("7 devices") === 0, text)
  assert.ok(text.indexOf("and 3 more") !== -1, text)
  assert.strictEqual(text.indexOf("sw-4"), -1, "named past the cap")
})

test("REQ-B14: a device is never its own downlink", () => {
  // The controller has never reported this. The guard is one comparison and the
  // alternative is a detail row that reads as a loop.
  const map = ViewModel.downlinkNames([device({ id: "x", name: "x", uplinkDeviceId: "x" })])
  assert.strictEqual(ViewModel.downlinkText(map, "x"), "none")
})

test("REQ-B14: the expanded device detail carries the downlinks row", () => {
  const snapshot = snapshotWith([
    device({ id: "gw", name: "Gateway" }),
    device({ id: "a", name: "attic-ap", uplinkDeviceId: "gw" })
  ], [])
  const list = ViewModel.deviceListModel(snapshot, { expandedId: "gw" })
  const rows = list.expandedDetail.rows.filter((r) => r.key === "downlinks")
  assert.strictEqual(rows.length, 1)
  assert.strictEqual(rows[0].label, "Downlinks")
  assert.strictEqual(rows[0].value, "1 device — attic-ap")
})

// --- REQ-B14: how many clients sit behind a device -----------------------

test("REQ-B14: the client count is the client list read backwards", () => {
  const clients = [
    client({ id: "c1", uplinkDeviceId: "ap" }),
    client({ id: "c2", uplinkDeviceId: "ap" }),
    client({ id: "c3", uplinkDeviceId: "sw" }),
    client({ id: "c4", uplinkDeviceId: null }),
    client({ id: "c5", uplinkDeviceId: "" })
  ]
  const counts = ViewModel.clientCountsByUplink(clients)
  assert.strictEqual(counts.ap, 2)
  assert.strictEqual(counts.sw, 1)
  // A client with no uplink belongs to nothing and is counted nowhere. It is
  // NOT quietly attributed to the empty-string key, which would then be handed
  // to any device whose id failed to parse.
  assert.strictEqual(Object.keys(counts).length, 2)
  assert.strictEqual(ViewModel.clientCountText(counts, "ap", false), "2")
  assert.strictEqual(ViewModel.clientCountText(counts, "sw", false), "1")
  assert.strictEqual(ViewModel.clientCountText(counts, "gw", false), "none")
})

test("REQ-B14: the client count survives a prototype key", () => {
  // `uplinkDeviceId` is a controller string that protocol-v1.md does not
  // constrain, and `found["constructor"]` starts life as a function — so
  // `found[up] = found[up] + 1` without the `own` guard produces
  // "function Object() { [native code] }1" as a count.
  const counts = ViewModel.clientCountsByUplink([
    client({ id: "c1", uplinkDeviceId: "constructor" }),
    client({ id: "c2", uplinkDeviceId: "constructor" })
  ])
  assert.strictEqual(ViewModel.clientCountText(counts, "constructor", false), "2")
  // And a device whose id happens to be a prototype member has no clients
  // rather than inheriting one.
  assert.strictEqual(ViewModel.clientCountText({}, "valueOf", false), "none")
  assert.strictEqual(ViewModel.clientCountText({}, "toString", false), "none")
})

test("REQ-B14: a count taken from a truncated client list says it is a floor", () => {
  // REQ-010/AC-063's rule applied to a number this panel derives itself. When
  // CLIENTS_LISTED_MAX or the DATA-B04 budget has shortened `clients[]`, every
  // count read from it is a floor — and a bare "14" beside an access point that
  // in fact has forty looks exactly like an answer.
  const counts = { ap: 14 }
  assert.strictEqual(ViewModel.clientCountText(counts, "ap", false), "14")
  const text = ViewModel.clientCountText(counts, "ap", true)
  assert.ok(text.indexOf("14") !== -1, text)
  assert.ok(text.indexOf("or more") !== -1, text)
  assert.ok(text.indexOf("truncated") !== -1, text)
  // BIZ-003: a floor of zero carries no information, so it must not render as
  // "none" — which would be a claim.
  assert.strictEqual(ViewModel.clientCountText(counts, "gw", true),
    "unknown — the client list is truncated")
  assert.strictEqual(ViewModel.clientCountText(counts, "gw", false), "none")
})

test("REQ-B14: the expanded device detail carries the clients row", () => {
  const snapshot = snapshotWith([
    device({ id: "ap", name: "attic-ap" }),
    device({ id: "gw", name: "Gateway" })
  ], [
    client({ id: "c1", uplinkDeviceId: "ap" }),
    client({ id: "c2", uplinkDeviceId: "ap" }),
    client({ id: "c3", uplinkDeviceId: "ap" })
  ])
  const rows = ViewModel.deviceListModel(snapshot, { expandedId: "ap" })
    .expandedDetail.rows.filter((r) => r.key === "clients")
  assert.strictEqual(rows.length, 1)
  assert.strictEqual(rows[0].label, "Clients")
  assert.strictEqual(rows[0].value, "3")
  // The negative control: the gateway has none of them, and a row that read
  // the whole list rather than its own key would say 3 here too.
  const other = ViewModel.deviceListModel(snapshot, { expandedId: "gw" })
    .expandedDetail.rows.filter((r) => r.key === "clients")
  assert.strictEqual(other[0].value, "none")
})

test("REQ-B14: the clients row and the Clients view agree about truncation", () => {
  // The two truncation tests are written separately — `deviceListModel` cannot
  // read `clientListModel`'s — so this holds them to the same answer. A device
  // detail saying "14" while the Clients view says "showing 2 of 900" is the
  // panel contradicting itself one keystroke apart.
  const clients = [client({ id: "c1", uplinkDeviceId: "ap" }),
                   client({ id: "c2", uplinkDeviceId: "ap" })]
  const snapshot = snapshotWith([device({ id: "ap", name: "attic-ap" })], clients,
    { devicesTotal: 1, clients: 900, offlineTotal: 0 })

  assert.strictEqual(ViewModel.clientListModel(snapshot, {}).truncated, true)
  const row = ViewModel.deviceListModel(snapshot, { expandedId: "ap" })
    .expandedDetail.rows.filter((r) => r.key === "clients")[0]
  assert.ok(row.value.indexOf("or more") !== -1, row.value)

  // And the control, on the same snapshot with an honest total.
  const whole = snapshotWith([device({ id: "ap", name: "attic-ap" })], clients,
    { devicesTotal: 1, clients: 2, offlineTotal: 0 })
  assert.strictEqual(ViewModel.clientListModel(whole, {}).truncated, false)
  const honest = ViewModel.deviceListModel(whole, { expandedId: "ap" })
    .expandedDetail.rows.filter((r) => r.key === "clients")[0]
  assert.strictEqual(honest.value, "2")
})

test("REQ-B14: a detail built with no context still renders every row", () => {
  // `deviceDetail(record, {})` is what the AC-B09 and AC-B10 cases pass, and
  // what a future caller will pass. Every row must still be a string — an
  // undefined `value` binds into QML as the string "undefined".
  const detail = ViewModel.deviceDetail(device({ id: "1", name: "n" }), {})
  const keys = detail.rows.map((r) => r.key)
  for (const key of ["firmware", "ip", "mac", "uplink", "downlinks", "clients",
                     "cpu", "memory", "download", "upload"]) {
    assert.ok(keys.indexOf(key) !== -1, "missing row: " + key)
  }
  for (const row of detail.rows) {
    assert.strictEqual(typeof row.value, "string", row.key)
    assert.strictEqual(typeof row.copy, "string", row.key)
  }
  assert.strictEqual(ViewModel.deviceDetail(device({ id: "1" }), undefined).rows.length,
    detail.rows.length)
})

test("REQ-B24 (SPEC-AMD-6): only a real value is copyable, never the placeholder", () => {
  // Copying the word "unknown" onto the clipboard is worse than doing nothing:
  // it silently replaces whatever the user had, and they find out later.
  const known = ViewModel.clientDetail(
    client({ id: "1", ipAddress: "192.0.2.40", macAddress: "02:00:00:aa:bb:cc" }), "")
  const byKey = {}
  for (const row of known.rows) byKey[row.key] = row
  assert.strictEqual(byKey.ip.copy, "192.0.2.40")
  assert.strictEqual(byKey.mac.copy, "02:00:00:aa:bb:cc")

  const missing = ViewModel.clientDetail(
    client({ id: "2", ipAddress: null, macAddress: null }), "")
  for (const row of missing.rows) {
    if (row.key === "ip" || row.key === "mac") {
      assert.strictEqual(row.value, "unknown")
      assert.strictEqual(row.copy, "", row.key + " offered the placeholder")
    }
  }
})

test("REQ-B24: every row carries the field, so the view never tests for it", () => {
  // REQ-014. A view that has to ask whether `copy` exists is a view holding a
  // rule, and the two detail shapes would drift the first time one gained a row.
  const snapshot = snapshotOf("success_browse_full")
  const model = ViewModel.build({ snapshot: snapshot, nowWall: 1768209240,
    browse: { expandedDeviceId: snapshot.devices[0].id,
              expandedClientId: snapshot.clients[0].id } })
  const sets = [model.metaRows,
                model.deviceList.expandedDetail.rows,
                model.clientList.expandedDetail.rows]
  for (const rows of sets) {
    for (const row of rows) {
      assert.strictEqual(typeof row.copy, "string", row.key)
    }
  }
})

test("REQ-B24: what is copied is the raw value, not the rendered one", () => {
  // The site row renders "Home (auto-selected)" when DATA-012 picked the site.
  // Pasting that into a terminal would be nonsense, so the row is not copyable
  // at all — while the site ID beside it, which is exactly what a reader would
  // otherwise transcribe, is.
  const rows = ViewModel.build({
    snapshot: snapshotOf("success_healthy"),
    warnings: [{ code: "site_auto_selected" }],
    nowWall: 1768209240
  }).metaRows
  const byKey = {}
  for (const row of rows) byKey[row.key] = row
  assert.ok(byKey.site.value.indexOf("(auto-selected)") !== -1)
  assert.strictEqual(byKey.site.copy, "")
  assert.notStrictEqual(byKey.siteId.copy, "")
  assert.strictEqual(byKey.siteId.copy, byKey.siteId.value)
})

// --- the row's columns and the detail's bars (design review, 2026-09-18) ---

test("REQ-B14: the device row's context arrives as fixed slots, empty when unknown", () => {
  // The row is drawn as columns, so a missing uptime must leave its slot empty
  // rather than shift nothing into place — an absent slot is a misaligned row.
  const bare = ViewModel.browseDeviceRow(device({ id: "1", name: "n", metrics: null }))
  assert.deepStrictEqual(bare.metaColumns, ["IP unknown", ""])
  const up = ViewModel.browseDeviceRow(device({
    id: "1", name: "n", ipAddress: "10.0.0.2", metrics: { uptimeSec: 864000 } }))
  assert.deepStrictEqual(up.metaColumns, ["10.0.0.2", "up 10d 0h"])
})

test("REQ-B14: a client with no uplink keeps an empty first slot", () => {
  const row = ViewModel.browseClientRow(
    client({ id: "1", name: "pi", type: "WIRED", uplinkDeviceId: null }), "", null)
  assert.strictEqual(row.metaColumns.length, 2)
  assert.strictEqual(row.metaColumns[0], "")
})

test("BIZ-003: a bar is drawn only for a figure that exists, and never past full", () => {
  assert.strictEqual(ViewModel.pctFraction(null), null)
  assert.strictEqual(ViewModel.pctFraction(undefined), null)
  assert.strictEqual(ViewModel.pctFraction(NaN), null)
  assert.strictEqual(ViewModel.pctFraction("48"), null)
  assert.strictEqual(ViewModel.pctFraction(48), 0.48)
  assert.strictEqual(ViewModel.pctFraction(0), 0)
  assert.strictEqual(ViewModel.pctFraction(104), 1)
  assert.strictEqual(ViewModel.pctFraction(-3), 0)
})

test("BIZ-003: only CPU and memory carry a bar, and an unknown one carries none", () => {
  const known = ViewModel.deviceDetail(device({ id: "1", detail: {},
    metrics: { cpuUtilizationPct: 12, memoryUtilizationPct: 48 } }), {})
  const byKey = {}
  for (const row of known.rows) byKey[row.key] = row
  assert.strictEqual(byKey.cpu.fraction, 0.12)
  assert.strictEqual(byKey.memory.fraction, 0.48)
  for (const row of known.rows) {
    if (row.key !== "cpu" && row.key !== "memory") {
      assert.strictEqual(row.fraction, undefined, row.key)
    }
  }
  const unknown = ViewModel.deviceDetail(device({ id: "1", detail: {}, metrics: null }), {})
  for (const row of unknown.rows) {
    if (row.key === "cpu" || row.key === "memory") {
      assert.strictEqual(row.value, "unknown")
      assert.strictEqual(row.fraction, null)
    }
  }
})

test("REQ-008a: a gateway's identity names the model only when the name does not", () => {
  const rows = ViewModel.gatewayRows([
    { id: "a", name: "UDM-Pro", model: "UDM Pro", class: "online", metrics: null },
    { id: "b", name: "Garage", model: "UCG-Ultra", class: "online", metrics: null },
    { id: "c", name: "Edge", model: null, class: "online", metrics: null }
  ], [])
  assert.strictEqual(rows[0].identityText, "UDM-Pro")
  assert.strictEqual(rows[1].identityText, "Garage  ·  UCG-Ultra")
  assert.strictEqual(rows[2].identityText, "Edge")
})
