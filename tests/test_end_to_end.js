// The first slice where the two halves meet.
//
// Everything until now has been one language checking itself. Here the REAL
// helper — CPython, the real TLS stack, the real fixture corpus over a real
// socket — produces an envelope, and the REAL consumer parses it: `Protocol.js`
// under Node's V8, the same file the shell loads, with no Python assertion
// standing in between.
//
// That is risk R-B's first half. Two implementations of one contract, written
// in different languages in different phases, have never been in the same room;
// this is the room. A test that asserted the helper's output against a Python
// expectation and separately asserted `Protocol.js` against a Python-generated
// fixture would leave exactly the gap that DEV-1 fell through.
//
// The Python side is `tests/tools/fixture_controller.py`, which mints a CA,
// serves a scenario over HTTPS on 127.0.0.1, writes a committed configuration
// and launches the helper with the service's own flags. It hands back the
// helper's literal stdout, JSON-encoded so the bytes survive the trip.

const { test } = require("node:test")
const assert = require("node:assert")
const path = require("node:path")
const { execFileSync } = require("node:child_process")

const Protocol = require("../Protocol.js")
const Health = require("../Health.js")

const REPO = path.resolve(__dirname, "..")
const HARNESS = path.join(REPO, "tests/tools/fixture_controller.py")

function run(scenario, options) {
  const args = ["-B", "-E", "-s", HARNESS, "--scenario", scenario]
  if (options && options.site) args.push("--site", options.site)
  if (options && options.failRoute) args.push("--fail-route", options.failRoute)
  if (options && options.corrupt) args.push("--corrupt", options.corrupt)
  if (options && options.rotateAfter) args.push("--rotate-after", String(options.rotateAfter))
  if (options && options.keylog) args.push("--keylog", options.keylog)
  const raw = execFileSync("python3", args, {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024
  })
  return JSON.parse(raw)
}

function accept(result) {
  return Protocol.acceptStdout({
    stdout: result.stdout,
    batch: {
      nonce: result.nonce,
      exitStatus: result.exitCode,
      launchAt: result.launchAt,
      receiptAt: result.receiptAt
    }
  })
}

function acceptedEnvelope(scenario, options) {
  const result = run(scenario, options)
  const verdict = accept(result)
  assert.strictEqual(verdict.accepted, true,
    scenario + " rejected: " + JSON.stringify(verdict.reasons))
  return { result: result, envelope: verdict.envelope }
}

test("a healthy site produces an envelope the service accepts, and green", () => {
  const { result, envelope } = acceptedEnvelope("healthy")
  assert.strictEqual(result.exitCode, 0)
  assert.strictEqual(result.stderr, "")
  assert.strictEqual(envelope.ok, true)
  assert.strictEqual(envelope.error, null)
  assert.strictEqual(envelope.meta.helperVersion, "0.1.0")
  // meta carries the transport facts the panel cannot get any other way: QML
  // cannot read config.json.
  assert.strictEqual(envelope.meta.allowInsecureTls, false)
  assert.strictEqual(envelope.meta.customCaInUse, true)
  assert.strictEqual(envelope.meta.commitGeneration, 1)

  const level = Health.healthLevel({ snapshot: envelope.data, errorKind: null })
  assert.strictEqual(level.level, "green")
  assert.strictEqual(level.rule, 5)
})

test("every gateway down is red, and reaches red through rule 3", () => {
  const { envelope } = acceptedEnvelope("all-down")
  assert.strictEqual(envelope.data.wan.status, "down")
  const level = Health.healthLevel({ snapshot: envelope.data, errorKind: null })
  assert.strictEqual(level.level, "red")
  assert.strictEqual(level.rule, 3)
})

test("a site with a down and an impaired device is amber", () => {
  const { envelope } = acceptedEnvelope("degraded")
  const level = Health.healthLevel({ snapshot: envelope.data, errorKind: null })
  assert.strictEqual(level.level, "amber")
  assert.strictEqual(level.rule, 4)
})

test("an empty site is a SUCCESS that renders grey, not a failure", () => {
  const { result, envelope } = acceptedEnvelope("empty-site")
  assert.strictEqual(result.exitCode, 0)
  assert.strictEqual(envelope.ok, true)
  assert.strictEqual(envelope.data.counts.devicesTotal, 0)
  assert.strictEqual(envelope.data.wan.status, "unknown")
  const level = Health.healthLevel({ snapshot: envelope.data, errorKind: null })
  assert.strictEqual(level.level, "grey")
  assert.strictEqual(level.rule, 2)
})

test("no gateway means wan.status unknown and red is unreachable", () => {
  const { envelope } = acceptedEnvelope("no-gateway")
  assert.strictEqual(envelope.data.wan.status, "unknown")
  assert.strictEqual(envelope.data.gateways.length, 0)
  const level = Health.healthLevel({ snapshot: envelope.data, errorKind: null })
  assert.notStrictEqual(level.level, "red")
})

test("the DATA-006b invariants hold on the wire, not just in a unit test", () => {
  // The service rejects a success envelope that violates either. If the helper
  // could produce one, every batch against that controller would be discarded
  // as malformed and the real reason would never be visible.
  for (const scenario of ["healthy", "degraded", "all-down", "all-states",
                          "multi-feature", "two-gateways", "five-gateways"]) {
    const { envelope } = acceptedEnvelope(scenario)
    const counts = envelope.data.counts
    const byClass = counts.byClass
    const sum = Object.keys(byClass).reduce((total, key) => total + byClass[key], 0)
    assert.strictEqual(sum, counts.devicesTotal, scenario)
    assert.strictEqual(byClass.down + byClass.impaired, counts.offlineTotal, scenario)
  }
})

test("AC-063: the offline list is bounded to ten and the total is not", () => {
  const { envelope } = acceptedEnvelope("large-500-down")
  assert.strictEqual(envelope.data.offlineDevices.length, 10)
  assert.strictEqual(envelope.data.counts.offlineTotal, 500)
  assert.strictEqual(envelope.data.counts.devicesTotal, 500)
  // "and 490 more", computed from the total. From the array length it would say
  // "and 0 more" for a site with 500 devices down.
  const truncated = envelope.warnings.find((w) => w.code === "offline_list_truncated")
  assert.ok(truncated, "no offline_list_truncated warning")
  assert.strictEqual(truncated.detail.total, 500)
  assert.strictEqual(truncated.detail.listed, 10)
})

test("AC-025: a multi-role device is counted in every role it reports", () => {
  const { envelope } = acceptedEnvelope("multi-feature")
  const counts = envelope.data.counts
  const roleTotal = counts.gateways.online + counts.switches.online
    + counts.accessPoints.online
  // The role rows deliberately sum to more than the unique device total.
  assert.ok(roleTotal > counts.devicesTotal,
    "role rows " + roleTotal + " should exceed devicesTotal " + counts.devicesTotal)
  assert.strictEqual(counts.byClass.online + counts.byClass.transitional
    + counts.byClass.down + counts.byClass.impaired + counts.byClass.unknown,
    counts.devicesTotal)
})

test("REQ-008a: statistics stop at four gateways and the rest are listed", () => {
  const { result, envelope } = acceptedEnvelope("five-gateways")
  assert.strictEqual(envelope.data.gateways.length, 5)
  const withMetrics = envelope.data.gateways.filter((g) => g.uptimeSec !== null)
  assert.strictEqual(withMetrics.length, 4)
  const warning = envelope.warnings.find((w) => w.code === "gateway_statistics_truncated")
  assert.ok(warning, "no gateway_statistics_truncated warning")
  assert.deepStrictEqual(warning.detail, { fetched: 4, total: 5 })
  // The bound is on route 4's contribution to the REQ-017 budget, so it has to
  // be visible in the requests actually made.
  const statistics = result.requests.filter((t) => t.includes("/statistics/latest"))
  assert.strictEqual(statistics.length, 4)
})

test("REQ-000: an unrecognised state lands in unknown with a warning", () => {
  const { envelope } = acceptedEnvelope("all-states")
  const warning = envelope.warnings.find((w) => w.code === "unknown_device_state")
  assert.ok(warning, "no unknown_device_state warning")
  assert.strictEqual(warning.detail.state, "REBOOTING")
  assert.ok(envelope.data.counts.byClass.unknown >= 1)
})

test("AC-058: one site auto-selects, several suspend, none is unsupported", () => {
  const single = acceptedEnvelope("healthy")
  const auto = single.envelope.warnings.find((w) => w.code === "site_auto_selected")
  assert.ok(auto, "a one-site controller did not auto-select")
  assert.strictEqual(typeof auto.detail.id, "string")

  const many = run("multi-site")
  const manyVerdict = accept(many)
  assert.strictEqual(manyVerdict.accepted, true, JSON.stringify(manyVerdict.reasons))
  assert.strictEqual(many.exitCode, 1)
  assert.strictEqual(manyVerdict.envelope.ok, false)
  assert.strictEqual(manyVerdict.envelope.error.kind, "site_unselected")
  assert.strictEqual(manyVerdict.envelope.error.retryable, false)
  const discovered = manyVerdict.envelope.warnings.find((w) => w.code === "sites_discovered")
  assert.ok(discovered, "the discovered sites were not carried for UX-006a")
  assert.strictEqual(discovered.detail.sites.length, 3)

  const none = run("zero-site")
  const noneVerdict = accept(none)
  assert.strictEqual(noneVerdict.accepted, true, JSON.stringify(noneVerdict.reasons))
  assert.strictEqual(noneVerdict.envelope.error.kind, "unsupported")
})

test("BIZ-006: a committed siteId selects that site out of several", () => {
  const many = run("multi-site")
  const discovered = accept(many).envelope.warnings
    .find((w) => w.code === "sites_discovered")
  const chosen = discovered.detail.sites[1]

  const { envelope } = acceptedEnvelope("multi-site", { site: chosen.id })
  assert.strictEqual(envelope.ok, true)
  assert.strictEqual(envelope.data.site.id, chosen.id)
  assert.strictEqual(envelope.meta.siteId, chosen.id)
})

test("a failure envelope pairs with a non-zero exit, and success with zero", () => {
  // Exit status is part of the protocol: the service rejects `ok: true` with a
  // non-zero exit and `ok: false` with zero, so the helper choosing the wrong
  // one makes every batch unusable regardless of its contents.
  const good = run("healthy")
  assert.strictEqual(good.exitCode, 0)
  assert.strictEqual(JSON.parse(good.stdout).ok, true)

  const bad = run("multi-site")
  assert.notStrictEqual(bad.exitCode, 0)
  assert.strictEqual(JSON.parse(bad.stdout).ok, false)
})

test("stdout is exactly one JSON value and stderr is empty", () => {
  // DATA-005: one bounded JSON object, nothing else. A stray print, a warning
  // from the interpreter, or a traceback would all land here.
  for (const scenario of ["healthy", "large-500-down", "multi-site", "zero-site"]) {
    const result = run(scenario)
    assert.strictEqual(result.stderr, "", scenario + " wrote to stderr")
    assert.ok(result.stdout.length <= Protocol.STDOUT_MAX_BYTES,
      scenario + " exceeded the stdout bound")
    assert.strictEqual(result.stdout.trim(), result.stdout,
      scenario + " padded its output")
    assert.doesNotThrow(() => JSON.parse(result.stdout), scenario)
  }
})

test("AC-054: the credential is captured once, across a rotation mid-batch", () => {
  // The credential and the whole committed set are REPLACED on disk while the
  // batch is in flight — which is the window `scripts/configure` actually
  // opens. A helper that re-read between requests would send this controller's
  // URL with the next controller's key from that point on.
  const { result, envelope } = acceptedEnvelope("large-500-down", { rotateAfter: 3 })
  assert.strictEqual(result.rotated, true, "the rotation never fired")
  assert.ok(result.requests.length > 5, "not a multi-request batch")
  const distinct = [...new Set(result.apiKeys)]
  assert.strictEqual(distinct.length, 1,
    "the batch sent more than one credential: " + distinct.length)
  assert.strictEqual(envelope.ok, true, "the rotation should not fail the batch")
})

test("AC-009: a broken committed set yields uncommitted and sends NOTHING", () => {
  // Not "fails" — sends nothing. DATA-004 puts the digest check before any
  // request exists, so the assertion is on the request count, which is the
  // only thing that distinguishes checking first from checking at all.
  for (const file of ["api-key", "config.json", "commit.json"]) {
    const result = run("healthy", { corrupt: file })
    const verdict = accept(result)
    assert.strictEqual(verdict.accepted, true, JSON.stringify(verdict.reasons))
    assert.strictEqual(verdict.envelope.ok, false, file)
    assert.strictEqual(verdict.envelope.error.kind, "uncommitted", file)
    assert.strictEqual(verdict.envelope.error.retryable, false, file)
    assert.strictEqual(result.requests.length, 0,
      file + ": a request was sent despite an unverified configuration")
  }
})

test("AC-051: an optional gap succeeds; a required gap does not", () => {
  // BIZ-004's whole point: a device-health indicator must not be disabled by an
  // unavailable throughput metric.
  for (const route of ["clients", "wans", "device_statistics"]) {
    const { result, envelope } = acceptedEnvelope("healthy", { failRoute: route })
    assert.strictEqual(result.exitCode, 0, route + " failed the batch")
    assert.strictEqual(envelope.ok, true, route)
    assert.ok(envelope.data, route + " produced no snapshot")
    assert.ok(envelope.warnings.length > 0, route + " warned about nothing")
  }
  assert.strictEqual(
    acceptedEnvelope("healthy", { failRoute: "clients" }).envelope.data.counts.clients,
    null, "an unreadable client list must be unknown, never zero")

  for (const route of ["info", "sites", "devices"]) {
    const result = run("healthy", { failRoute: route })
    const verdict = accept(result)
    assert.strictEqual(verdict.accepted, true, JSON.stringify(verdict.reasons))
    assert.strictEqual(verdict.envelope.ok, false, route + " should fail the batch")
    assert.strictEqual(verdict.envelope.data, null, route)
    assert.notStrictEqual(result.exitCode, 0, route)
  }
})

test("AC-052: meta is an object on both shapes, and null-filled when unreadable", () => {
  const success = acceptedEnvelope("healthy").envelope
  for (const key of ["commitGeneration", "apiRootHost", "siteId",
                     "allowInsecureTls", "customCaInUse", "helperVersion"]) {
    assert.ok(key in success.meta, "success meta is missing " + key)
  }
  assert.strictEqual(typeof success.meta.apiRootHost, "string")

  // `uncommitted` is precisely the case where the configuration those fields
  // come from could not be read — and the one where the panel most needs a
  // meta object to render rows as "unknown" rather than omit them.
  const broken = accept(run("healthy", { corrupt: "config.json" })).envelope
  assert.strictEqual(typeof broken.meta, "object")
  assert.notStrictEqual(broken.meta, null)
  assert.strictEqual(broken.meta.apiRootHost, null)
  assert.strictEqual(broken.meta.commitGeneration, null)
  assert.strictEqual(broken.meta.helperVersion, "0.1.0")
})

test("AC-015a request half: a real request writes no TLS key log", () => {
  // SSLKEYLOGFILE is consumed INSIDE ssl.create_default_context(), which this
  // helper never calls. The grep gate asserts the absence of the call; this
  // asserts the consequence against a real handshake with the variable set.
  const keylog = path.join(require("node:os").tmpdir(),
    "omarchy-unifi-keylog-" + process.pid + ".txt")
  const { result, envelope } = acceptedEnvelope("healthy", { keylog: keylog })
  assert.strictEqual(envelope.ok, true)
  assert.strictEqual(result.keylogExists, false,
    "a TLS key log was written for a credential-bearing request")
})
