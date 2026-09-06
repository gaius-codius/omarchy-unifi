// AC-020a, AC-044, AC-045, AC-046 (pure half), AC-047, AC-048, AC-049, AC-050.
//
// The whole Phase 1 envelope corpus is driven through the validator here: every
// accept fixture must be accepted, every reject fixture must be rejected with
// the exact class `tests/fixtures/index.json` names. That cross-reference is
// the point — a validator tested against examples the same person invented
// while writing it proves only that the two agree.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Protocol = require("../../Protocol.js")

const REPO = path.resolve(__dirname, "../..")
const FIXTURES = path.join(REPO, "tests/fixtures")
const INDEX = JSON.parse(fs.readFileSync(path.join(FIXTURES, "index.json"), "utf8"))

function load(key) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, key + ".json"), "utf8"))
}

function casesUnder(prefix) {
  return Object.keys(INDEX.cases).filter((k) => k.startsWith(prefix))
}

function shift(iso, seconds) {
  return new Date(Date.parse(iso) + seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z")
}

// Accept fixtures are bare envelopes; the batch context the service would have
// issued is reconstructed around them. A one-minute launch window and a
// one-second receipt delay, so the timestamps sit properly INSIDE the window
// rather than exactly on its edges — an off-by-one in the ordering checks would
// otherwise be invisible.
function batchFor(envelope) {
  return {
    nonce: envelope.nonce,
    exitStatus: envelope.ok === true ? 0 : 2,
    launchAt: shift(envelope.attemptedAt, -60),
    receiptAt: shift(envelope.observedAt || envelope.attemptedAt, 1)
  }
}

// The `stdout_oversized` case is synthesized rather than committed, so that a
// 256 KiB file does not ship inside a plugin folder that is cloned into
// ~/.config/omarchy/plugins/. Padding with trailing whitespace keeps the
// envelope otherwise VALID — trailing whitespace is legal JSON — so the case
// proves the size bound fires, and that nothing else does.
function synthesize(descriptor, envelope) {
  const text = JSON.stringify(envelope)
  assert.strictEqual(descriptor.kind, "padded_success",
    "unknown synthesize kind: " + descriptor.kind)
  return text + " ".repeat(Math.max(0, descriptor.bytes - text.length))
}

function runReject(fixture) {
  const batch = fixture.batch
  if (fixture.synthesize) {
    const donor = load("envelopes/accept/success_healthy")
    return Protocol.acceptStdout({
      stdout: synthesize(fixture.synthesize, donor), batch: batch
    })
  }
  if (fixture.stdout !== null && fixture.stdout !== undefined) {
    return Protocol.acceptStdout({ stdout: fixture.stdout, batch: batch })
  }
  return Protocol.acceptEnvelope({ envelope: fixture.envelope, batch: batch })
}

// --- the accept corpus ----------------------------------------------------

test("every accept fixture is accepted, and yields what the service needs", async (t) => {
  const keys = casesUnder("envelopes/accept/")
  assert.strictEqual(keys.length, 36, "the accept corpus changed size")

  for (const key of keys) {
    await t.test(key.split("/").pop(), () => {
      const envelope = load(key)
      const result = Protocol.acceptEnvelope({
        envelope: envelope, batch: batchFor(envelope)
      })
      assert.strictEqual(result.accepted, true,
        "rejected as " + result.rejectionClass + ": "
          + JSON.stringify(result.reasons.map((r) => r.detail)))

      // `meta` is present on BOTH shapes (DEV-2), so the panel can render
      // transport facts before any batch has succeeded.
      assert.ok(result.meta && typeof result.meta.helperVersion === "string",
        "meta.helperVersion must survive acceptance on both shapes")
      assert.ok(Array.isArray(result.warnings))

      if (envelope.ok === true) {
        assert.strictEqual(result.publishedKind, null)
        assert.ok(result.data && result.data.counts, "a success must yield a snapshot")
        assert.strictEqual(result.error, null)
      } else {
        assert.strictEqual(result.data, null, "a failure must not yield a snapshot")
        assert.strictEqual(result.publishedKind, INDEX.cases[key].errorKind,
          "the published kind must be the one the index names")
      }
    })
  }
})

test("the accept corpus covers all nineteen DATA-007 kinds", () => {
  const kinds = casesUnder("envelopes/accept/")
    .map((k) => INDEX.cases[k].errorKind)
    .filter((k) => k !== null)
  assert.strictEqual(new Set(kinds).size, 19,
    "expected one accepted failure envelope per DATA-007 kind")
})

// --- the reject corpus ----------------------------------------------------

test("every reject fixture is rejected with the class the index names", async (t) => {
  const keys = casesUnder("envelopes/reject/")
  assert.strictEqual(keys.length, 42, "the reject corpus changed size")

  for (const key of keys) {
    await t.test(key.split("/").pop(), () => {
      const result = runReject(load(key))
      const want = INDEX.cases[key].rejectionClass

      assert.strictEqual(result.accepted, false, "was ACCEPTED; expected " + want)
      assert.strictEqual(result.rejectionClass, want,
        "all reasons: " + result.reasons.map((r) => r.rejectionClass).join(", "))

      // AC-046's pure half. Every rejection, whatever the class, publishes the
      // one named kind REQ-020a makes retryable — never an unnamed state.
      assert.strictEqual(result.publishedKind, "malformed_response")
      assert.strictEqual(result.data, null,
        "a rejection must not hand the service a snapshot to display")
      assert.strictEqual(INDEX.cases[key].publishedAs, "malformed_response",
        "the index and the validator disagree about what is published")
    })
  }
})

test("AC-072: the corpus and the validator between them exercise all 36 rejection classes", () => {
  // Written out longhand. A set built by mapping over the corpus would report
  // whatever the corpus happens to contain, which is the false green N-16
  // records; this states the 36 independently and requires the corpus to meet
  // them. It is also what lets the AC-072 scan see the classes at all, since a
  // fixture key is not the class name.
  const EXPECTED = [
    "stdout_oversized", "stdout_not_json", "stdout_multiple_values",
    "stdout_trailing_garbage", "envelope_not_object", "envelope_unknown_key",
    "protocol_version_wrong", "nonce_missing", "nonce_mismatch", "meta_missing",
    "meta_helper_version_missing", "attempted_at_malformed",
    "attempted_at_too_early", "attempted_at_future", "ok_not_boolean",
    "success_exit_nonzero", "success_error_not_null",
    "success_observed_at_missing", "success_observed_at_unordered",
    "success_data_missing", "success_data_empty", "data_schema_violation",
    "byclass_sum_mismatch", "byclass_offline_mismatch", "failure_exit_zero",
    "failure_data_not_null", "failure_observed_at_not_null",
    "failure_error_missing", "error_kind_missing",
    "error_http_status_forbidden", "error_http_status_wrong",
    "error_retry_after_forbidden", "error_retryable_mismatch",
    "warnings_not_array", "warning_shape_invalid", "bound_exceeded"
  ]
  assert.strictEqual(EXPECTED.length, 36)
  assert.strictEqual(new Set(EXPECTED).size, 36, "a class is listed twice")

  const produced = new Set()
  for (const key of casesUnder("envelopes/reject/")) {
    produced.add(runReject(load(key)).rejectionClass)
  }

  const missing = EXPECTED.filter((c) => !produced.has(c))
  assert.deepStrictEqual(missing, [], "no fixture produces these classes")
  const extra = [...produced].filter((c) => EXPECTED.indexOf(c) === -1)
  assert.deepStrictEqual(extra, [], "the validator produced a class not in DATA-008")
})

// --- AC-044: the DATA-006 schema and the DATA-006b invariants -------------

test("AC-044: the success shape rejects each named schema defect", () => {
  const good = load("envelopes/accept/success_healthy")
  const batch = batchFor(good)

  function mutate(fn) {
    const copy = JSON.parse(JSON.stringify(good))
    fn(copy)
    return Protocol.acceptEnvelope({ envelope: copy, batch: batch })
  }

  assert.strictEqual(mutate((e) => { e.data = {} }).rejectionClass, "success_data_empty")
  assert.strictEqual(mutate((e) => { delete e.data.counts.gateways.online })
    .rejectionClass, "data_schema_violation", "a missing count bucket")
  assert.strictEqual(mutate((e) => { delete e.data.counts.byClass })
    .rejectionClass, "data_schema_violation", "a missing counts.byClass")
  assert.strictEqual(mutate((e) => { e.data.counts.devicesTotal = -1 })
    .rejectionClass, "data_schema_violation", "a negative devicesTotal")
  assert.strictEqual(mutate((e) => { e.data.wan.status = "flapping" })
    .rejectionClass, "data_schema_violation", "a wan.status outside its domain")

  // Both DATA-006b invariants. REQ-002 rule 4 reads byClass, so a partition
  // that does not add up would colour the bar item from numbers describing no
  // real site.
  assert.strictEqual(mutate((e) => { e.data.counts.byClass.online += 1 })
    .rejectionClass, "byclass_sum_mismatch")
  assert.strictEqual(mutate((e) => { e.data.counts.offlineTotal += 1 })
    .rejectionClass, "byclass_offline_mismatch")

  // `ok` by boolean identity, never truthiness. All three of these are what a
  // producer that stringified or coerced its booleans would emit, and `1` and
  // `"true"` are both truthy and both look right in a log.
  assert.strictEqual(mutate((e) => { e.ok = 1 }).rejectionClass, "ok_not_boolean")
  assert.strictEqual(mutate((e) => { e.ok = "true" }).rejectionClass, "ok_not_boolean")
  assert.strictEqual(mutate((e) => { delete e.ok }).rejectionClass, "ok_not_boolean")
})

test("`wan` is a closed key set, so an invented metric is rejected", () => {
  // DATA-006 names two metrics as absent from the model rather than
  // present-and-null, because no supported API version can populate them:
  // /v1/sites/{id}/wans returns {id, name} and nothing else. Checking the KEY
  // SET rather than those two names also catches a third one nobody has
  // thought of yet.
  const good = load("envelopes/accept/success_healthy")
  const batch = batchFor(good)
  assert.deepStrictEqual(Object.keys(good.data.wan).slice().sort(),
    Protocol.WAN_KEYS.slice().sort(), "the corpus and the key set disagree")

  for (const extra of ["latencyMs", "packetLossPct", "jitterMs", "somethingNew"]) {
    const copy = JSON.parse(JSON.stringify(good))
    copy.data.wan[extra] = null
    const result = Protocol.acceptEnvelope({ envelope: copy, batch: batch })
    assert.strictEqual(result.accepted, false, extra + " was accepted")
    assert.strictEqual(result.rejectionClass, "data_schema_violation", extra)
  }
})

test("AC-044: an accepted success is unchanged, so the snapshot is the helper's", () => {
  // The validator must not normalize, default or repair anything. If it did,
  // Health.js would be reading numbers this file invented rather than numbers
  // the controller reported.
  const envelope = load("envelopes/accept/success_healthy")
  const before = JSON.stringify(envelope)
  const result = Protocol.acceptEnvelope({ envelope: envelope, batch: batchFor(envelope) })
  assert.strictEqual(JSON.stringify(envelope), before, "the envelope was mutated")
  assert.strictEqual(result.data, envelope.data)
})

// --- AC-045: DATA-007a consistency ---------------------------------------

test("AC-045: `credential` with httpStatus 429 is rejected, not acted on as a rate limit", () => {
  const envelope = load("envelopes/accept/failure_credential")
  const copy = JSON.parse(JSON.stringify(envelope))
  copy.error.httpStatus = 429

  const result = Protocol.acceptEnvelope({ envelope: copy, batch: batchFor(envelope) })
  assert.strictEqual(result.accepted, false)
  assert.strictEqual(result.rejectionClass, "error_http_status_forbidden")
  assert.strictEqual(result.publishedKind, "malformed_response")
  // The point of the criterion: the scheduler must never see `rate_limited`
  // here. An envelope that can claim one kind and carry another kind's fields
  // can talk the service into any retry policy it likes.
  assert.notStrictEqual(result.publishedKind, "rate_limited")
})

test("AC-045: a `network` error with httpStatus absent OR null is accepted", () => {
  const envelope = load("envelopes/accept/failure_network")
  for (const variant of ["absent", "null"]) {
    const copy = JSON.parse(JSON.stringify(envelope))
    if (variant === "absent") delete copy.error.httpStatus
    else copy.error.httpStatus = null

    const result = Protocol.acceptEnvelope({ envelope: copy, batch: batchFor(envelope) })
    assert.strictEqual(result.accepted, true,
      variant + ": " + JSON.stringify(result.reasons))
    assert.strictEqual(result.publishedKind, "network")
  }
})

test("AC-045: `retryable` must agree with the kind's class", () => {
  // The one field the scheduler would otherwise take on trust. An envelope
  // claiming `credential` with `retryable: true` would be retried forever
  // against a controller that will never accept the key.
  const envelope = load("envelopes/accept/failure_credential")
  const copy = JSON.parse(JSON.stringify(envelope))
  copy.error.retryable = true
  assert.strictEqual(
    Protocol.acceptEnvelope({ envelope: copy, batch: batchFor(envelope) }).rejectionClass,
    "error_retryable_mismatch")
})

test("AC-045: `http` keeps its status-dependent class, and 401/403/429 are excluded", () => {
  const envelope = load("envelopes/accept/failure_http_transient")
  function withStatus(status, retryable) {
    const copy = JSON.parse(JSON.stringify(envelope))
    copy.error.httpStatus = status
    copy.error.retryable = retryable
    return Protocol.acceptEnvelope({ envelope: copy, batch: batchFor(envelope) })
  }
  for (const status of [500, 502, 503, 504]) {
    assert.strictEqual(withStatus(status, true).accepted, true, status + " is transient")
    assert.strictEqual(withStatus(status, false).rejectionClass, "error_retryable_mismatch")
  }
  for (const status of [404, 418, 501]) {
    assert.strictEqual(withStatus(status, false).accepted, true, status + " is fatal")
    assert.strictEqual(withStatus(status, true).rejectionClass, "error_retryable_mismatch")
  }
  // DATA-007 requires a received status to keep its typed kind, so an `http`
  // carrying 401 means the producer skipped that mapping — and acting on it
  // would report "controller unreachable" for what is actually a bad API key.
  for (const status of [401, 403, 429]) {
    assert.strictEqual(withStatus(status, false).rejectionClass, "error_http_status_wrong",
      status + " must not be reportable as `http`")
  }
})

test("AC-045: retryAfterSec is permitted only for rate_limited", () => {
  const limited = load("envelopes/accept/failure_rate_limited")
  assert.strictEqual(
    Protocol.acceptEnvelope({ envelope: limited, batch: batchFor(limited) }).accepted, true)

  const timeout = load("envelopes/accept/failure_timeout")
  const copy = JSON.parse(JSON.stringify(timeout))
  copy.error.retryAfterSec = 30
  assert.strictEqual(
    Protocol.acceptEnvelope({ envelope: copy, batch: batchFor(timeout) }).rejectionClass,
    "error_retry_after_forbidden")
})

// --- AC-020a: timestamps --------------------------------------------------

test("AC-020a: both shapes reject an invalid attemptedAt", () => {
  for (const key of ["envelopes/accept/success_healthy", "envelopes/accept/failure_network"]) {
    const envelope = load(key)
    const batch = batchFor(envelope)
    // Each entry isolates ONE relaxation. `2026-01-15t12:00:00z` was the only
    // lowercase case at first, and it cannot tell a tolerated `t` from a
    // tolerated `z` — a validator that accepted lowercase `z` alone still
    // passed it. Both spellings now appear separately.
    const overLong = "2026-01-15T12:00:00." + "0".repeat(50) + "Z"
    assert.ok(overLong.length > 64, "this case only bites past the 64-char bound")

    for (const bad of ["2026-01-15 12:00:00Z", "2026-01-15T12:00:00+00:00",
                       "2026-01-15T12:00:00", "2026-01-15t12:00:00Z",
                       "2026-01-15T12:00:00z", "2026-01-15t12:00:00z",
                       "2026-02-30T12:00:00Z", "2026-01-15T25:00:00Z",
                       overLong, "", 12345, null]) {
      const copy = JSON.parse(JSON.stringify(envelope))
      copy.attemptedAt = bad
      const result = Protocol.acceptEnvelope({ envelope: copy, batch: batch })
      assert.strictEqual(result.accepted, false,
        key + " accepted attemptedAt " + JSON.stringify(bad))
      assert.strictEqual(result.rejectionClass, "attempted_at_malformed",
        key + " / " + JSON.stringify(bad))
    }
  }
})

test("AC-020a: the success shape rejects a wrongly ordered or post-receipt observedAt", () => {
  const envelope = load("envelopes/accept/success_healthy")
  const batch = batchFor(envelope)

  const early = JSON.parse(JSON.stringify(envelope))
  early.observedAt = shift(envelope.attemptedAt, -1)
  assert.strictEqual(Protocol.acceptEnvelope({ envelope: early, batch: batch }).rejectionClass,
    "success_observed_at_unordered", "observedAt before attemptedAt")

  const late = JSON.parse(JSON.stringify(envelope))
  late.observedAt = shift(batch.receiptAt, 1)
  assert.strictEqual(Protocol.acceptEnvelope({ envelope: late, batch: batch }).rejectionClass,
    "success_observed_at_unordered", "observedAt later than receipt")

  const missing = JSON.parse(JSON.stringify(envelope))
  missing.observedAt = null
  assert.strictEqual(Protocol.acceptEnvelope({ envelope: missing, batch: batch }).rejectionClass,
    "success_observed_at_missing")
})

test("attemptedAt exactly at the launch boundary and at receipt is accepted", () => {
  // The 300 s window is inclusive at both ends. An exclusive comparison would
  // reject a batch launched at the instant the service started, which is the
  // normal case at shell startup.
  const envelope = load("envelopes/accept/failure_network")
  const at = envelope.attemptedAt
  const result = Protocol.acceptEnvelope({
    envelope: envelope,
    batch: { nonce: envelope.nonce, exitStatus: 2, launchAt: shift(at, 300), receiptAt: at }
  })
  assert.strictEqual(result.accepted, true, JSON.stringify(result.reasons))

  const past = Protocol.acceptEnvelope({
    envelope: envelope,
    batch: { nonce: envelope.nonce, exitStatus: 2, launchAt: shift(at, 301), receiptAt: at }
  })
  assert.strictEqual(past.rejectionClass, "attempted_at_too_early")
})

// --- AC-047: DATA-008a's one-shot re-baseline ----------------------------

test("AC-047: a batch rejected ONLY for attemptedAt-too-early re-baselines once", () => {
  // Without this, a laptop resuming with a 40-minute-slow RTC rejects every
  // batch permanently until the shell restarts — total, silent, and immune to
  // every retry the scheduler can make.
  const fixture = load("envelopes/reject/attempted_at_too_early")
  const first = runReject(fixture)

  assert.strictEqual(first.rejectionClass, "attempted_at_too_early")
  assert.strictEqual(first.reasons.length, 1,
    "the re-baseline is only safe while this is the ONLY reason; reasons: "
      + first.reasons.map((r) => r.rejectionClass).join(", "))
  assert.strictEqual(first.rebaselineEligible, true)

  const state = Protocol.createRebaseline()
  const one = Protocol.considerRebaseline(state, first, "2026-01-15T12:00:00Z")
  assert.strictEqual(one.rebaselined, true, "the first such rejection re-baselines")
  assert.strictEqual(one.launchAt, "2026-01-15T12:00:00Z")

  // A second CONSECUTIVE one is a genuine protocol error: a helper that keeps
  // sending timestamps from last week is not a clock problem.
  const two = Protocol.considerRebaseline(one.state, first, "2026-01-15T12:00:30Z")
  assert.strictEqual(two.rebaselined, false)
})

test("AC-047: any other outcome resets the counter, so `consecutive` means what it says", () => {
  const tooEarly = runReject(load("envelopes/reject/attempted_at_too_early"))
  const good = load("envelopes/accept/success_healthy")
  const success = Protocol.acceptEnvelope({ envelope: good, batch: batchFor(good) })

  let state = Protocol.createRebaseline()
  state = Protocol.considerRebaseline(state, tooEarly, "2026-01-15T12:00:00Z").state
  state = Protocol.considerRebaseline(state, success, "2026-01-15T12:01:00Z").state
  const again = Protocol.considerRebaseline(state, tooEarly, "2026-01-15T12:02:00Z")
  assert.strictEqual(again.rebaselined, true,
    "a clock correction hours later must not be blocked by one that happened before a success")
})

test("AC-047: a batch rejected for MORE than the timestamp does not re-baseline", () => {
  // The signature DATA-008a acts on is "a timestamp far in the past with
  // everything else well-formed". Re-baselining on anything else would let a
  // genuinely broken helper move the service's own launch time.
  const fixture = JSON.parse(JSON.stringify(load("envelopes/reject/attempted_at_too_early")))
  fixture.envelope.protocolVersion = 2
  const result = runReject(fixture)
  assert.ok(result.reasons.length > 1)
  assert.strictEqual(result.rebaselineEligible, false)
  assert.strictEqual(
    Protocol.considerRebaseline(Protocol.createRebaseline(), result, "2026-01-15T12:00:00Z")
      .rebaselined, false)
})

// --- AC-048: the nonce ----------------------------------------------------

test("AC-048: a nonce that was not issued for this batch is discarded", () => {
  // Including one carrying an otherwise perfectly valid success. DATA-005a is
  // what makes generation checking survive plugin hot-reload, which destroys
  // the service and resets any in-memory counter.
  const envelope = load("envelopes/accept/success_healthy")
  const batch = batchFor(envelope)

  const stale = { nonce: "a-nonce-from-a-previous-service-instance",
    exitStatus: batch.exitStatus, launchAt: batch.launchAt, receiptAt: batch.receiptAt }
  const result = Protocol.acceptEnvelope({ envelope: envelope, batch: stale })

  assert.strictEqual(result.accepted, false)
  assert.strictEqual(result.rejectionClass, "nonce_mismatch")
  assert.strictEqual(result.data, null,
    "an unmatched nonce must not deliver its snapshot, however valid the rest is")
  // And the same envelope against its own nonce is fine, so the rejection is
  // the nonce and nothing else.
  assert.strictEqual(Protocol.acceptEnvelope({ envelope: envelope, batch: batch }).accepted, true)
})

// --- AC-050 / REQ-017b: the completion join ------------------------------

test("AC-050: exit-before-streams and streams-before-exit parse identically", () => {
  // HC-7: the two signals have no guaranteed order. A service that parsed on
  // `onExited` would read a truncated envelope from a helper whose pipe still
  // had bytes in it — a JSON parse error for a batch that actually succeeded.
  const events = [
    { event: "exit", status: 0 },
    { event: "stdout", text: '{"a":1}' },
    { event: "stderr", text: "" }
  ]
  const orders = [
    [0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]
  ]

  const results = orders.map((order) => {
    let state = Protocol.createJoin()
    let terminalCount = 0
    let terminalAt = -1
    order.forEach((index, step) => {
      const out = Protocol.joinBatch(state, events[index])
      state = out.state
      if (out.terminal) { terminalCount++; terminalAt = step }
    })
    return { terminalCount: terminalCount, terminalAt: terminalAt, state: state }
  })

  for (const r of results) {
    assert.strictEqual(r.terminalCount, 1, "terminal must fire exactly once (risk R-E)")
    assert.strictEqual(r.terminalAt, 2, "terminal only after all three are observed")
    assert.strictEqual(r.state.exitStatus, 0)
    assert.strictEqual(r.state.stdout, '{"a":1}')
  }
})

test("AC-050: join(exit only) yields nothing until the stream arrives", () => {
  let state = Protocol.createJoin()
  const exited = Protocol.joinBatch(state, { event: "exit", status: 0 })
  assert.strictEqual(exited.terminal, false, "the protocol is never parsed from onExited alone")
  assert.strictEqual(exited.reason, "waiting")

  state = Protocol.joinBatch(exited.state, { event: "stdout", text: "{}" }).state
  const done = Protocol.joinBatch(state, { event: "stderr", text: "" })
  assert.strictEqual(done.terminal, true)
})

test("AC-050 / REQ-017a: the watchdog completes the batch without waiting for exit", () => {
  const state = Protocol.createJoin()
  const fired = Protocol.joinBatch(state, { event: "watchdog" })
  assert.strictEqual(fired.terminal, true)
  assert.strictEqual(fired.reason, "watchdog")
  assert.strictEqual(fired.state.exitSeen, false, "the watchdog does not wait for the process")
})

test("R-E: terminal fires at most once per batch, whatever arrives afterwards", () => {
  // The risk this closes: a second terminal starts a second follow-on batch,
  // and the one-authoritative-batch invariant (REQ-016) is gone. The abandoned
  // process's later onExited and stream output land here.
  let state = Protocol.createJoin()
  let count = 0
  const script = [
    { event: "watchdog" },
    { event: "exit", status: 143 },
    { event: "stdout", text: '{"late":true}' },
    { event: "stderr", text: "killed" },
    { event: "exit", status: 143 },
    { event: "watchdog" }
  ]
  for (const e of script) {
    const out = Protocol.joinBatch(state, e)
    state = out.state
    if (out.terminal) count++
  }
  assert.strictEqual(count, 1)
})

test("the join is total over its input and never throws", () => {
  let state = Protocol.createJoin()
  for (const e of [null, undefined, {}, { event: "nonsense" }, { event: "exit" },
                   { event: "stdout" }, { event: 7 }, "exit"]) {
    const out = Protocol.joinBatch(state, e)
    assert.ok(out && typeof out.terminal === "boolean", JSON.stringify(e))
    state = out.state
  }
  assert.strictEqual(state.terminated, false, "no malformed event may complete a batch")
})

// --- AC-049: stderr -------------------------------------------------------

test("AC-049: 64 KiB of stderr is capped at 8 KiB and marks the batch internal", () => {
  const verdict = Protocol.stderrVerdict("x".repeat(65536))
  assert.strictEqual(verdict.retainedLength, Protocol.STDERR_RETAIN_BYTES)
  assert.strictEqual(verdict.overBound, true)
  assert.strictEqual(verdict.kind, "internal")
  assert.strictEqual(verdict.warning.code, "stderr_bound_exceeded")
})

test("AC-049: stderr within the helper's own bound is retained without a verdict", () => {
  const verdict = Protocol.stderrVerdict("a warning line\n")
  assert.strictEqual(verdict.overBound, false)
  assert.strictEqual(verdict.kind, null)
  assert.strictEqual(verdict.warning, null)
})

test("AC-049: the verdict returns a length, never the text", () => {
  // DATA-005b: never parsed, never logged verbatim, never displayed. The likely
  // content of an over-long stderr is a traceback, which is exactly where a
  // request header carrying a credential would appear — so the verdict must not
  // hand the text on to a caller that might log it.
  const secretish = "Authorization: Bearer " + "z".repeat(9000)
  const verdict = Protocol.stderrVerdict(secretish)
  const serialized = JSON.stringify(verdict)
  assert.strictEqual(serialized.indexOf("zzzz"), -1, "the verdict carries the text")
  assert.strictEqual(serialized.indexOf("Authorization"), -1)
})

// --- stdout framing -------------------------------------------------------

test("stdout framing distinguishes not-JSON, a second value, and trailing garbage", () => {
  // Three different diagnoses, and the difference matters: a traceback, a
  // half-written batch, and a helper that printed after its envelope are three
  // different bugs to go and fix.
  const batch = { nonce: "n", exitStatus: 0, launchAt: null, receiptAt: null }
  const one = '{"protocolVersion":1}'

  assert.strictEqual(Protocol.acceptStdout({ stdout: "Traceback...", batch: batch })
    .rejectionClass, "stdout_not_json")
  assert.strictEqual(Protocol.acceptStdout({ stdout: one + one, batch: batch })
    .rejectionClass, "stdout_multiple_values")
  assert.strictEqual(Protocol.acceptStdout({ stdout: one + " oops", batch: batch })
    .rejectionClass, "stdout_trailing_garbage")
  assert.strictEqual(Protocol.acceptStdout({ stdout: "[1,2,3]\n", batch: batch })
    .rejectionClass, "envelope_not_object", "trailing whitespace alone is legal JSON")
})

test("the JSON value scanner is not confused by braces inside strings", () => {
  // A naive brace counter would end the first value early on a site named
  // `}{`, and report a perfectly good envelope as trailing garbage.
  const text = '{"name":"a } { \\" b"} rest'
  const end = Protocol.endOfFirstJsonValue(text)
  assert.strictEqual(text.slice(0, end), '{"name":"a } { \\" b"}')
  assert.doesNotThrow(() => JSON.parse(text.slice(0, end)))
})

test("stdout at the 256 KiB bound is accepted and one byte past it is not", () => {
  const donor = load("envelopes/accept/success_healthy")
  const batch = batchFor(donor)
  const text = JSON.stringify(donor)

  const atBound = text + " ".repeat(Protocol.STDOUT_MAX_BYTES - text.length)
  assert.strictEqual(atBound.length, Protocol.STDOUT_MAX_BYTES)
  assert.strictEqual(Protocol.acceptStdout({ stdout: atBound, batch: batch }).accepted, true)

  const past = atBound + " "
  const overBound = Protocol.acceptStdout({ stdout: past, batch: batch })
  assert.strictEqual(overBound.rejectionClass, "stdout_oversized")
  assert.strictEqual(overBound.reasons.length, 1,
    "the size bound must be decided before anything is parsed")
})

// --- AC-046: never throws -------------------------------------------------

test("AC-046: no input makes the validator throw, and none reaches the backstop", () => {
  const hostile = [
    undefined, null, {}, { envelope: undefined }, { envelope: null },
    { envelope: 0 }, { envelope: "" }, { envelope: [] }, { envelope: true },
    { envelope: { ok: true, data: { counts: { byClass: {} } } } },
    { envelope: { protocolVersion: 1, ok: false, error: { kind: {} } } },
    { envelope: { warnings: [null, 1, "x", {}] } },
    { stdout: "" }, { stdout: "null" }, { stdout: " " },
    { stdout: '{"a":' }, { stdout: "[[[[[[[[[[" }
  ]
  for (const input of hostile) {
    for (const fn of [Protocol.acceptEnvelope, Protocol.acceptStdout]) {
      let result
      assert.doesNotThrow(() => { result = fn(input) }, JSON.stringify(input))
      assert.strictEqual(result.accepted, false)
      assert.strictEqual(result.publishedKind, "malformed_response",
        "every rejection publishes the one named kind")
      assert.ok(result.rejectionClass !== null,
        "reached the unexpected-exception backstop on " + JSON.stringify(input)
          + " — the backstop is a floor, not a strategy")
    }
  }
})

test("AC-046: a deeply nested envelope is rejected by the depth bound, not by a stack overflow", () => {
  const nested = {}
  let cursor = nested
  for (let i = 0; i < 400; i++) { cursor.next = {}; cursor = cursor.next }
  const envelope = { protocolVersion: 1, ok: true, nonce: "n", attemptedAt: "2026-01-15T12:00:00Z",
    observedAt: "2026-01-15T12:00:01Z", meta: { helperVersion: "0.1.0" },
    data: nested, warnings: [], error: null }

  const result = Protocol.acceptEnvelope({ envelope: envelope, batch: null })
  assert.strictEqual(result.accepted, false)
  assert.ok(result.reasons.some((r) => r.rejectionClass === "bound_exceeded"),
    "expected the depth bound; got " + result.reasons.map((r) => r.rejectionClass).join(", "))
})

test("AC-046: no fixture in the corpus reaches the unexpected-exception backstop", () => {
  for (const key of casesUnder("envelopes/")) {
    const fixture = load(key)
    const result = INDEX.cases[key].verdict === "accept"
      ? Protocol.acceptEnvelope({ envelope: fixture, batch: batchFor(fixture) })
      : runReject(fixture)
    for (const reason of result.reasons) {
      assert.ok(reason.rejectionClass !== null,
        key + " produced an unexpected exception: " + reason.detail)
    }
  }
})
