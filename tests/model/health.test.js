// AC-020 through AC-025. The REQ-002 decision table is the product, so this is
// the suite that matters most in Phase 2.
//
// Wherever possible the inputs are the Phase 1 ACCEPT CORPUS rather than
// objects invented here. Those same bytes are asserted as normalize.py's output
// in tests/test_unifi_status.py, so Health.js is tested against exactly what the
// helper will be required to produce. That is the "one corpus, two directions"
// mechanism that closes risk R-F — inventing a snapshot here would restore
// precisely the gap DEV-1 slipped through.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Health = require("../../Health.js")

const ACCEPT = path.resolve(__dirname, "../fixtures/envelopes/accept")

function envelope(name) {
  return JSON.parse(fs.readFileSync(path.join(ACCEPT, name + ".json"), "utf8"))
}

function snapshotOf(name) {
  return envelope(name).data
}

// --- REQ-000 -------------------------------------------------------------

test("classifyState is total over the ten API states", () => {
  const expected = {
    ONLINE: "online",
    PENDING_ADOPTION: "transitional", UPDATING: "transitional",
    GETTING_READY: "transitional", ADOPTING: "transitional", DELETING: "transitional",
    OFFLINE: "down", CONNECTION_INTERRUPTED: "down",
    ISOLATED: "impaired", U5G_INCORRECT_TOPOLOGY: "impaired"
  }
  for (const state of Object.keys(expected)) {
    assert.strictEqual(Health.classifyState(state), expected[state], state)
  }
  assert.strictEqual(Object.keys(expected).length, 10)
})

test("an unrecognised state lands in `unknown`, never in `online`", () => {
  // The failure mode this prevents is specific: a future controller firmware
  // invents a state, a permissive default calls it online, and the widget goes
  // green while devices are unreachable.
  for (const value of ["REBOOTING", "", "online", "Online", "0", null, undefined, 7, {}]) {
    assert.strictEqual(Health.classifyState(value), "unknown", String(value))
  }
})

test("an unrecognised state produces a warning naming the value (REQ-003)", () => {
  const result = Health.classifyDevice({ id: "d1", state: "REBOOTING" })
  assert.strictEqual(result.klass, "unknown")
  assert.strictEqual(result.warning.code, "unknown_device_state")
  assert.ok(result.warning.message.indexOf("REBOOTING") !== -1,
    "the warning must name the value, or the user cannot report it")
  assert.strictEqual(result.warning.detail.state, "REBOOTING")

  assert.strictEqual(Health.classifyDevice({ id: "d2", state: "ONLINE" }).warning, null)
})

// --- AC-020: the health function is total and exclusive -------------------

function syntheticSnapshot(gatewayCount, deviceClass) {
  // gatewayCount gateways plus one switch, every device in deviceClass, so
  // devicesTotal is never zero and rule 2 is exercised separately.
  const total = gatewayCount + 1
  const byClass = { online: 0, transitional: 0, down: 0, impaired: 0, unknown: 0 }
  byClass[deviceClass] = total
  const gateways = []
  for (let i = 0; i < gatewayCount; i++) {
    gateways.push({ id: "gw-" + i, name: "GW " + i, model: "UXG", state: "X",
                    class: deviceClass, uptimeSec: null, downloadBps: null, uploadBps: null })
  }
  return {
    site: { id: "s", name: "Synthetic" },
    wan: { status: Health.wanStatusFor(gateways), uptimeSec: null,
           downloadBps: null, uploadBps: null },
    gateways: gateways,
    counts: {
      clients: null, devicesTotal: total,
      offlineTotal: byClass.down + byClass.impaired,
      byClass: byClass,
      gateways: { online: 0, transitional: 0, down: 0, impaired: 0, unknown: 0 },
      switches: { online: 0, transitional: 0, down: 0, impaired: 0, unknown: 0 },
      accessPoints: { online: 0, transitional: 0, down: 0, impaired: 0, unknown: 0 }
    },
    offlineDevices: [],
    applicationVersion: "9.1.0"
  }
}

function expectedFor(gatewayCount, deviceClass, isStale, hasSnapshot) {
  if (!hasSnapshot) return { level: "grey", rule: 1 }
  if (isStale) return { level: "grey", rule: 1 }
  if (gatewayCount >= 1 && deviceClass === "down") return { level: "red", rule: 3 }
  if (deviceClass === "down" || deviceClass === "impaired" || deviceClass === "unknown") {
    return { level: "amber", rule: 4 }
  }
  return { level: "green", rule: 5 }
}

test("AC-020: every cell of the cross product returns exactly one colour", () => {
  const gatewayCounts = [0, 1, 2]
  const staleness = [false, true]
  const presence = [true, false]
  const rulesSeen = {}
  let cells = 0

  for (const gatewayCount of gatewayCounts) {
    for (const deviceClass of Health.CLASSES) {
      for (const isStale of staleness) {
        for (const hasSnapshot of presence) {
          cells++
          const label = gatewayCount + " gateway(s), " + deviceClass
            + (isStale ? ", stale" : ", fresh")
            + (hasSnapshot ? ", snapshot" : ", no snapshot")

          const result = Health.healthLevel({
            snapshot: hasSnapshot ? syntheticSnapshot(gatewayCount, deviceClass) : null,
            errorKind: null,
            isStale: isStale
          })

          // Total: something came back, and it is one of the four levels.
          assert.ok(result && typeof result === "object", label + ": no result")
          assert.ok(Health.LEVELS.indexOf(result.level) !== -1,
            label + ": level " + result.level + " is not one of " + Health.LEVELS)
          // Exclusive: a rule number identifies WHICH rule matched, so a green
          // reached by falling through a rule that should have fired is
          // distinguishable from a correct green.
          assert.ok(result.rule >= 1 && result.rule <= 5, label + ": rule " + result.rule)
          assert.ok(result.reason && result.reason.length > 0, label + ": no reason")

          const expected = expectedFor(gatewayCount, deviceClass, isStale, hasSnapshot)
          assert.strictEqual(result.level, expected.level, label)
          assert.strictEqual(result.rule, expected.rule, label)
          rulesSeen[result.rule] = true
        }
      }
    }
  }

  assert.strictEqual(cells, 3 * 5 * 2 * 2, "the cross product changed size")
  // Rule 2 needs an empty snapshot, which this matrix never builds; it is
  // asserted by AC-024 below. Every other rule must have fired here, or the
  // matrix is not reaching the table it claims to test.
  for (const rule of [1, 3, 4, 5]) {
    assert.ok(rulesSeen[rule], "no cell in the cross product reached rule " + rule)
  }
})

test("a configuration fault greys immediately; a transport failure does not", () => {
  // REQ-004 / AC-021. This pair is the whole distinction: polling is suspended
  // for a configuration fault, so whatever is on screen has stopped meaning
  // anything — but a network blip over a fresh snapshot must keep showing that
  // snapshot's colour, or every brief outage would read as a site-wide failure.
  const snapshot = snapshotOf("success_healthy")

  for (const kind of Health.CONFIG_FAULT_KINDS) {
    const result = Health.healthLevel({ snapshot: snapshot, errorKind: kind, isStale: false })
    assert.strictEqual(result.level, "grey", kind)
    assert.strictEqual(result.rule, 1, kind)
  }

  for (const kind of ["network", "timeout", "rate_limited", "http", "tls",
                      "malformed_response", "internal"]) {
    const result = Health.healthLevel({ snapshot: snapshot, errorKind: kind, isStale: false })
    assert.strictEqual(result.level, "green",
      kind + " must not recolour a fresh snapshot (REQ-004)")
    assert.strictEqual(result.rule, 5, kind)
  }
})

test("AC-021: a snapshot-less failure is never red", () => {
  for (const kind of ["network", "timeout", "tls", "unauthorized", "internal"]) {
    const result = Health.healthLevel({ snapshot: null, errorKind: kind, isStale: false })
    assert.strictEqual(result.level, "grey", kind)
    assert.notStrictEqual(result.level, "red")
  }
})

// --- AC-021 through AC-025, from the corpus -------------------------------

test("AC-021: red requires a gateway, and a gateway-less site can never be red", () => {
  const noGateway = snapshotOf("success_no_gateway")
  assert.strictEqual(noGateway.gateways.length, 0)
  assert.strictEqual(noGateway.wan.status, "unknown",
    "REQ-008a: no gateway present means wan.status is unknown")
  assert.strictEqual(Health.wanStatusFor(noGateway.gateways), "unknown")

  const result = Health.healthLevel({ snapshot: noGateway, errorKind: null, isStale: false })
  assert.notStrictEqual(result.level, "red",
    "rule 3 requires at least one gateway, so this site can never be red")
  assert.strictEqual(result.level, "amber")
  assert.strictEqual(result.rule, 4)

  const allDown = snapshotOf("success_all_gateways_down")
  const red = Health.healthLevel({ snapshot: allDown, errorKind: null, isStale: false })
  assert.strictEqual(red.level, "red")
  assert.strictEqual(red.rule, 3)
  for (const gateway of allDown.gateways) {
    assert.strictEqual(gateway.class, "down")
    assert.ok(["OFFLINE", "CONNECTION_INTERRUPTED"].indexOf(gateway.state) !== -1,
      "AC-021 names exactly these two states")
  }
})

test("AC-022: transitional stays green; impaired and unknown go amber", () => {
  const green = Health.healthLevel({
    snapshot: snapshotOf("success_transitional_only"), errorKind: null, isStale: false })
  assert.strictEqual(green.level, "green",
    "an UPDATING access point must not degrade the colour — rule 4 does not read transitional")
  assert.strictEqual(green.rule, 5)

  const transitional = snapshotOf("success_transitional_only")
  assert.strictEqual(transitional.counts.offlineTotal, 0)
  assert.strictEqual(transitional.offlineDevices.length, 0,
    "REQ-003 keeps a transitional device out of the offline list entirely")

  const impaired = Health.healthLevel({
    snapshot: snapshotOf("success_degraded"), errorKind: null, isStale: false })
  assert.strictEqual(impaired.level, "amber")
  assert.strictEqual(impaired.rule, 4)

  // REBOOTING is outside the ten-value enum. It reaches rule 4 through
  // byClass.unknown, which is the exact gap offlineTotal cannot see: that count
  // is down+impaired only, so before DEV-1 this snapshot had no way to be amber.
  const unknownState = snapshotOf("success_unknown_state")
  assert.strictEqual(unknownState.counts.byClass.unknown, 1)
  assert.strictEqual(unknownState.counts.offlineTotal, 0,
    "offlineTotal is down+impaired only and cannot see `unknown` — this is why DEV-1 exists")
  const amber = Health.healthLevel({ snapshot: unknownState, errorKind: null, isStale: false })
  assert.strictEqual(amber.level, "amber")
  assert.strictEqual(amber.rule, 4)

  const warnings = envelope("success_unknown_state").warnings
  assert.ok(warnings.some((w) => w.code === "unknown_device_state"
    && w.message.indexOf("REBOOTING") !== -1),
    "AC-022 requires a warning naming the unrecognised value")
})

test("AC-023: red strictly precedes amber", () => {
  const mixed = snapshotOf("success_mixed_gateways")
  const amber = Health.healthLevel({ snapshot: mixed, errorKind: null, isStale: false })
  assert.strictEqual(amber.level, "amber",
    "one ONLINE and one ISOLATED gateway is amber via rule 4, not red — rule 3 needs EVERY gateway down")
  assert.strictEqual(amber.rule, 4)
  assert.strictEqual(Health.wanStatusFor(mixed.gateways), "degraded")

  // All gateways down PLUS a device in an unrecognised state. Rule 4 would also
  // match, so this asserts the ORDER: red must win.
  const allDown = snapshotOf("success_all_gateways_down")
  const contaminated = JSON.parse(JSON.stringify(allDown))
  contaminated.counts.byClass.down -= 1
  contaminated.counts.byClass.unknown += 1
  contaminated.counts.offlineTotal -= 1
  const red = Health.healthLevel({ snapshot: contaminated, errorKind: null, isStale: false })
  assert.strictEqual(red.level, "red")
  assert.strictEqual(red.rule, 3, "rule 3 must match before rule 4 gets a chance")
})

test("AC-024: zero adopted devices is grey via rule 2, not a failure", () => {
  const empty = snapshotOf("success_empty_site")
  assert.strictEqual(empty.counts.devicesTotal, 0)
  const result = Health.healthLevel({ snapshot: empty, errorKind: null, isStale: false })
  assert.strictEqual(result.level, "grey")
  assert.strictEqual(result.rule, 2, "rule 2, which is distinct from rule 1's failure grey")
})

test("AC-025: the role counts are not a partition and the model says so", () => {
  const snapshot = snapshotOf("success_multi_feature_roles")
  const counts = snapshot.counts

  // AC-025's exact numbers.
  assert.strictEqual(counts.gateways.online, 1)
  assert.strictEqual(counts.switches.online, 1)
  assert.strictEqual(counts.accessPoints.online, 3)
  assert.strictEqual(counts.devicesTotal, 3)

  const roleSum = counts.gateways.online + counts.switches.online + counts.accessPoints.online
  assert.strictEqual(roleSum, 5, "AC-025: the role rows sum to 5 against a total of 3")
  assert.notStrictEqual(roleSum, counts.devicesTotal,
    "the Dream Machine is counted in all three roles")
  assert.strictEqual(Health.roleCountsAreNotAPartition(counts), true,
    "the panel must be told, or rows summing to 4 above a total of 3 read as a bug")

  const byClassSum = Health.CLASSES.reduce((acc, c) => acc + counts.byClass[c], 0)
  assert.strictEqual(byClassSum, counts.devicesTotal, "byClass IS a partition")
})

test("a device with no features is counted by byClass and by no role (DATA-006b)", () => {
  // The role objects cannot see it at all, so byClass is the only place it
  // exists. A partition that silently dropped it would pass every role-based
  // check while under-reporting the site.
  const counts = snapshotOf("success_featureless_device").counts
  const roleSum = Health.CLASSES.reduce((acc, c) =>
    acc + counts.gateways[c] + counts.switches[c] + counts.accessPoints[c], 0)
  assert.strictEqual(roleSum, 2, "the featureless device appears in no role object")
  assert.strictEqual(counts.devicesTotal, 3)
  assert.strictEqual(Health.CLASSES.reduce((a, c) => a + counts.byClass[c], 0), 3,
    "byClass must still count it exactly once")
  assert.strictEqual(Health.roleCountsAreNotAPartition(counts), true)
})

// --- REQ-008a ------------------------------------------------------------

test("wanStatusFor covers every gateway configuration", () => {
  const gw = (klass) => ({ id: "g" + klass, class: klass })
  assert.strictEqual(Health.wanStatusFor([]), "unknown")
  assert.strictEqual(Health.wanStatusFor(null), "unknown")
  assert.strictEqual(Health.wanStatusFor([gw("down")]), "down")
  assert.strictEqual(Health.wanStatusFor([gw("down"), gw("down")]), "down")
  assert.strictEqual(Health.wanStatusFor([gw("online"), gw("down")]), "degraded")
  assert.strictEqual(Health.wanStatusFor([gw("online"), gw("impaired")]), "degraded")
  assert.strictEqual(Health.wanStatusFor([gw("online"), gw("unknown")]), "degraded")
  assert.strictEqual(Health.wanStatusFor([gw("online")]), "up")
  assert.strictEqual(Health.wanStatusFor([gw("online"), gw("transitional")]), "up")
})

test("every gateway impaired is degraded, not up (DEV-3)", () => {
  // REQ-008a's third bullet has two readings and they disagree on exactly this
  // cell. Read as "some-but-not-all of {down, impaired, unknown}" it falls
  // through to `up`; read as "some-but-not-all down, OR any impaired, OR any
  // unknown" it is `degraded`. The first would report a healthy WAN while every
  // gateway is isolated, and REQ-002 rule 4 would colour the same snapshot
  // amber — so the panel would contradict the bar item. Raised as DEV-3.
  const impaired = [{ id: "a", class: "impaired" }, { id: "b", class: "impaired" }]
  assert.strictEqual(Health.wanStatusFor(impaired), "degraded")
  assert.strictEqual(Health.wanStatusFor([{ id: "a", class: "unknown" }]), "degraded")

  const snapshot = {
    counts: { devicesTotal: 2, byClass: { online: 0, transitional: 0, down: 0,
                                          impaired: 2, unknown: 0 } },
    gateways: impaired
  }
  assert.strictEqual(Health.healthLevel({ snapshot: snapshot }).level, "amber",
    "the bar item is amber here, so `up` would be a visible contradiction")
})

test("primaryGateway prefers the first online gateway in ascending id order", () => {
  // Ascending ID, not array order, so the choice is stable across batches
  // whatever order the controller happens to return.
  const gateways = [
    { id: "gw-c", class: "online" },
    { id: "gw-a", class: "down" },
    { id: "gw-b", class: "online" }
  ]
  assert.strictEqual(Health.primaryGateway(gateways).id, "gw-b")

  const noneOnline = [
    { id: "gw-c", class: "down" },
    { id: "gw-a", class: "impaired" }
  ]
  assert.strictEqual(Health.primaryGateway(noneOnline).id, "gw-a")
  assert.strictEqual(Health.primaryGateway([]), null)
  assert.strictEqual(Health.primaryGateway(null), null)

  // The input array must not be reordered underneath the caller: the panel
  // lists every gateway and relies on the order the helper sent.
  const original = gateways.slice()
  Health.primaryGateway(gateways)
  assert.deepStrictEqual(gateways, original)
})

test("isGateway reads the features array, including the empty one", () => {
  assert.strictEqual(Health.isGateway({ features: ["gateway", "switching"] }), true)
  assert.strictEqual(Health.isGateway({ features: ["switching"] }), false)
  assert.strictEqual(Health.isGateway({ features: [] }), false)
  assert.strictEqual(Health.isGateway({}), false)
  assert.strictEqual(Health.isGateway(null), false)
})

// --- the corpus, end to end ----------------------------------------------

test("every success fixture in the accept corpus yields its documented level", () => {
  const expected = {
    success_healthy: { level: "green", rule: 5 },
    success_degraded: { level: "amber", rule: 4 },
    success_all_gateways_down: { level: "red", rule: 3 },
    success_empty_site: { level: "grey", rule: 2 },
    success_no_gateway: { level: "amber", rule: 4 },
    success_mixed_gateways: { level: "amber", rule: 4 },
    success_transitional_only: { level: "green", rule: 5 },
    success_unknown_state: { level: "amber", rule: 4 },
    success_multi_feature_roles: { level: "green", rule: 5 },
    success_featureless_device: { level: "green", rule: 5 },
    success_500_down_bounded_list: { level: "red", rule: 3 },
    success_optional_gaps: { level: "green", rule: 5 },
    success_five_gateways_truncated: { level: "green", rule: 5 },
    success_insecure_tls: { level: "green", rule: 5 },
    success_meta_all_null: { level: "green", rule: 5 }
  }

  const names = fs.readdirSync(ACCEPT)
    .filter((f) => f.startsWith("success_"))
    .map((f) => f.replace(/\.json$/, ""))

  // Every success fixture must be listed. A new fixture with no expectation
  // would otherwise be silently untested.
  assert.deepStrictEqual(names.sort(), Object.keys(expected).sort(),
    "a success fixture has no expected health level here")

  for (const name of names) {
    const result = Health.healthLevel({
      snapshot: snapshotOf(name), errorKind: null, isStale: false })
    assert.strictEqual(result.level, expected[name].level, name)
    assert.strictEqual(result.rule, expected[name].rule, name)
  }
})

test("the DEV-1 invariants hold for every success fixture", () => {
  for (const file of fs.readdirSync(ACCEPT).filter((f) => f.startsWith("success_"))) {
    const counts = JSON.parse(fs.readFileSync(path.join(ACCEPT, file), "utf8")).data.counts
    const sum = Health.CLASSES.reduce((acc, c) => acc + counts.byClass[c], 0)
    assert.strictEqual(sum, counts.devicesTotal, file + ": sum(byClass) != devicesTotal")
    assert.strictEqual(counts.byClass.down + counts.byClass.impaired, counts.offlineTotal,
      file + ": byClass.down+impaired != offlineTotal")
  }
})
