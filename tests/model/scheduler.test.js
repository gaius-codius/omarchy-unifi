// AC-006, AC-008, AC-035, AC-036, AC-037, AC-038, AC-039, AC-040, AC-041.
//
// Every deadline here is asserted as an exact number against a virtual clock,
// never as "roughly one interval". HC-9 says the shell ships no backoff helper,
// so none of this logic has a working reference implementation to be checked
// against — the arithmetic in the requirement is the only oracle there is, and
// an approximate assertion would not notice an off-by-one-rung backoff.
//
// AC-005 is deliberately NOT here: its assertions are about a real Process and
// real Timers, and the plan assigns it to CP6b alone rather than letting a pure
// approximation of it be counted twice.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Schedule = require("../../Schedule.js")
const Health = require("../../Health.js")
const { createClock } = require("./clock.js")

const REPO = path.resolve(__dirname, "../..")
const SPEC = fs.readFileSync(
  path.join(REPO, "docs/feature-specs/omarchy-unifi-plugin/SPEC.md"), "utf8")

// A configured scheduler that has just completed one successful batch, which is
// the state most of the requirements below are written against.
function settled(clock, intervalSec) {
  let st = Schedule.create({ intervalSec: intervalSec, configured: false })
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  st = Schedule.tick(st, clock.at()).state
  return Schedule.onSuccess(st, clock.at({ generation: st.generation })).state
}

// --- AC-036: every kind classified, exactly once --------------------------

test("AC-036: the nineteen DATA-007 kinds are exactly the kinds this module knows", () => {
  // Parsed from SPEC.md rather than copied, so that adding a twentieth kind to
  // the spec fails here instead of being silently unclassified at runtime.
  const block = /DATA-007: Error `kind` is one of ([\s\S]*?) — nineteen kinds/.exec(SPEC)
  assert.ok(block, "SPEC.md: could not locate the DATA-007 kind list")
  const specKinds = block[1].match(/`[a-z_]+`/g).map((s) => s.replace(/`/g, ""))

  assert.strictEqual(specKinds.length, 19,
    "the spec calls this list nineteen kinds; it parsed as " + specKinds.length)
  assert.deepStrictEqual(Schedule.ERROR_KINDS, specKinds,
    "Schedule.ERROR_KINDS must match DATA-007 in content AND order")
})

test("AC-036: every kind maps to exactly one retry class, and none is left out", () => {
  for (const kind of Schedule.ERROR_KINDS) {
    // `http` is the one kind whose class depends on its status, so it is
    // probed at both ends rather than once.
    const classes = kind === "http"
      ? [Schedule.retryClassFor(kind, 503), Schedule.retryClassFor(kind, 404)]
      : [Schedule.retryClassFor(kind, null)]

    for (const cls of classes) {
      assert.ok(Schedule.RETRY_CLASSES.indexOf(cls) !== -1,
        kind + " classified as " + JSON.stringify(cls) + ", which is not a retry class")
    }
  }

  // The constant table must not carry an `http` entry: a constant there would
  // make the loop above pass while contradicting the status-dependent rule.
  assert.strictEqual(KIND_TABLE_HAS_HTTP(), false,
    "KIND_RETRY_CLASS must not give `http` a constant class")

  // And it must not carry a kind DATA-007 does not define.
  for (const key of Object.keys(Schedule.KIND_RETRY_CLASS)) {
    assert.ok(Schedule.ERROR_KINDS.indexOf(key) !== -1,
      key + " is classified but is not a DATA-007 kind")
  }
})

function KIND_TABLE_HAS_HTTP() {
  return Object.prototype.hasOwnProperty.call(Schedule.KIND_RETRY_CLASS, "http")
}

test("AC-036: each of the nineteen kinds, named, with the deadline it produces", () => {
  // Written out longhand rather than looped over ERROR_KINDS. The loop above
  // proves TOTALITY — no kind is unclassified — but it cannot notice a kind
  // that is classified WRONGLY, because it derives its expectation from the
  // same table it is checking. This one states each answer independently.
  //
  // It is also what makes AC-072's coverage scan honest: a scan over test
  // sources cannot see a kind that only ever appears as a loop variable, and
  // the fix for that is to name the kinds, not to widen the scan until the gap
  // disappears.
  const EXPECTED = {
    // fatal (REQ-020) — max(interval, 300 s)
    unconfigured:           { cls: "fatal",      status: null, delay: 300 },
    credential:             { cls: "fatal",      status: null, delay: 300 },
    unauthorized:           { cls: "fatal",      status: 401,  delay: 300 },
    forbidden:              { cls: "fatal",      status: 403,  delay: 300 },
    tls:                    { cls: "fatal",      status: null, delay: 300 },
    unsupported:            { cls: "fatal",      status: null, delay: 300 },
    helper_unavailable:     { cls: "fatal",      status: null, delay: 300 },

    // fatal AND suspending (REQ-018a) — no deadline at all
    site_unselected:        { cls: "fatal",      status: null, delay: null },
    uncommitted:            { cls: "fatal",      status: null, delay: null },
    configuration_conflict: { cls: "fatal",      status: null, delay: null },

    // transient (REQ-019) — interval × 2^failures, capped at max(interval, 900 s)
    network:                { cls: "transient",  status: null, delay: 60 },
    timeout:                { cls: "transient",  status: null, delay: 60 },
    rate_limited:           { cls: "transient",  status: 429,  delay: 60 },

    // integrity (REQ-020a) — the same schedule as transient
    redirect:               { cls: "integrity",  status: null, delay: 60 },
    partial_response:       { cls: "integrity",  status: null, delay: 60 },
    oversized_response:     { cls: "integrity",  status: null, delay: 60 },
    malformed_response:     { cls: "integrity",  status: null, delay: 60 },
    internal:               { cls: "integrity",  status: null, delay: 60 },

    // status-dependent (DATA-007a's sub-table)
    http:                   { cls: "transient",  status: 503,  delay: 60 }
  }

  assert.deepStrictEqual(Object.keys(EXPECTED).sort(), Schedule.ERROR_KINDS.slice().sort(),
    "this table and DATA-007 cover different kinds")

  for (const kind of Object.keys(EXPECTED)) {
    const want = EXPECTED[kind]
    assert.strictEqual(Schedule.retryClassFor(kind, want.status), want.cls, kind + ": class")

    const clock = createClock()
    let st = settled(clock, 30)
    clock.advance(30)
    st = Schedule.tick(st, clock.at()).state
    st = Schedule.onFailure(st, clock.at({ kind: kind, httpStatus: want.status })).state

    if (want.delay === null) {
      assert.strictEqual(st.nextAttemptAt, null, kind + ": must suspend, not schedule")
      assert.strictEqual(st.suspended, true, kind + ": must suspend polling")
    } else {
      assert.strictEqual(st.nextAttemptAt, clock.now() + want.delay, kind + ": deadline")
      assert.strictEqual(st.suspended, false, kind + ": must not suspend polling")
    }
  }

  // `http` is the only kind whose answer changes with its status.
  assert.strictEqual(Schedule.retryClassFor("http", 404), "fatal",
    "a non-transient http status is fatal, not retried forever")
})

test("AC-036: an unknown kind is retried as `internal`, not treated as fatal", () => {
  // DATA-007: unknown kinds map to `internal` in QML. Classifying an
  // unrecognised kind as fatal would let a newer helper stop the poll loop.
  assert.strictEqual(Schedule.retryClassFor("kind_from_a_future_helper", null), "integrity")
  assert.strictEqual(Schedule.isRetryable("kind_from_a_future_helper", null), true)
})

test("AC-036: `retryable` derives from the class, so DATA-007a has one authority", () => {
  assert.strictEqual(Schedule.isRetryable("credential", null), false)
  assert.strictEqual(Schedule.isRetryable("rate_limited", 429), true)
  assert.strictEqual(Schedule.isRetryable("http", 503), true)
  assert.strictEqual(Schedule.isRetryable("http", 404), false)
})

test("AC-036: switching class between consecutive failures never shortens the deadline", () => {
  // Both orders. The fatal→transient order is the one that matters: the fatal
  // floor (300 s) is longer than an early transient delay (60 s at a 30 s
  // interval), so a naive recompute would pull the deadline forward and poll a
  // 403-ing controller harder the moment it emits one 503.
  const clock = createClock()
  let st = settled(clock, 30)

  st = Schedule.tick(st, clock.at()).state
  const afterFatal = Schedule.onFailure(st, clock.at({ kind: "forbidden", httpStatus: 403 })).state
  assert.strictEqual(afterFatal.state, "fatal-wait")
  assert.strictEqual(afterFatal.nextAttemptAt, clock.now() + 300)

  clock.advance(300)
  let st2 = Schedule.tick(afterFatal, clock.at()).state
  assert.strictEqual(st2.state, "active-batch")
  const afterTransient = Schedule.onFailure(st2, clock.at({ kind: "network" })).state
  // failures is now 2, so the transient formula gives 30 × 2² = 120 s.
  assert.strictEqual(afterTransient.failures, 2)
  assert.ok(afterTransient.nextAttemptAt >= afterFatal.nextAttemptAt,
    "a class change must never move the deadline earlier than it already was")

  // The reverse order, where the recompute genuinely is longer.
  const clock2 = createClock()
  let stB = settled(clock2, 30)
  stB = Schedule.tick(stB, clock2.at()).state
  const t1 = Schedule.onFailure(stB, clock2.at({ kind: "network" })).state
  assert.strictEqual(t1.nextAttemptAt, clock2.now() + 60)
  clock2.advance(60)
  let stC = Schedule.tick(t1, clock2.at()).state
  const f1 = Schedule.onFailure(stC, clock2.at({ kind: "tls" })).state
  assert.strictEqual(f1.state, "fatal-wait")
  assert.strictEqual(f1.nextAttemptAt, clock2.now() + 300)
})

// --- AC-035: the floors are relative to the interval, never flat ----------

test("REQ-020b / REQ-024a: a class change on a resume-overdue attempt cannot shorten the deadline", () => {
  // This is the ONLY route to REQ-020b's max(new, existing) clause, and it is
  // why the clause exists at all. Every ordinary failure happens at or after
  // the deadline it is replacing, so the recomputed deadline is naturally
  // later; a manual failure reschedules nothing (REQ-018b). Only REQ-024a's
  // resume rule launches a batch BEFORE the current deadline, and a
  // fatal→transient change there computes 120 s against a 300 s wait already
  // in progress. Without the clamp, a controller returning one 503 after a
  // string of 403s would be polled harder than one returning only 403s.
  const clock = createClock()
  let st = settled(clock, 30)

  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "forbidden", httpStatus: 403 })).state
  const fatalDeadline = st.nextAttemptAt
  assert.strictEqual(fatalDeadline, clock.now() + 300)

  // The machine sleeps. Wall time passes; monotonic time does not, so the
  // 300 s fatal wait still has its full 300 s left on the scheduling axis.
  clock.suspend(600)
  assert.strictEqual(Schedule.wallOverdue(st, clock.nowWall()), true)

  const launch = Schedule.tick(st, clock.at({ overdue: true }))
  assert.strictEqual(launch.launched, true, "REQ-024a: an overdue schedule starts a batch")
  assert.strictEqual(launch.reason, "overdue")

  const failed = Schedule.onFailure(launch.state, clock.at({ kind: "network" })).state
  assert.strictEqual(failed.retryClass, "transient", "the class changed")
  assert.strictEqual(failed.failures, 2)
  // The transient formula alone would give now + 30×2² = now + 120.
  assert.ok(120 < 300, "this test is only meaningful while the recompute is shorter")
  assert.strictEqual(failed.nextAttemptAt, fatalDeadline,
    "a class change must take max(new, existing), never the shorter of the two")
})

test("REQ-020b: a SAME-class failure cannot shorten the deadline either", () => {
  // The same defect without a class change, reached the same way. REQ-020b
  // names only the class-change case, but a `rate_limited` failure carrying a
  // 1800 s Retry-After followed by another after a suspend would otherwise
  // fall back to the 120 s exponential delay and throw away the server's
  // instruction — the one thing a client must not do to a controller that has
  // just told it to slow down.
  const clock = createClock()
  let st = settled(clock, 30)

  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({
    kind: "rate_limited", httpStatus: 429, retryAfter: 1800
  })).state
  const serverDeadline = st.nextAttemptAt
  assert.strictEqual(serverDeadline, clock.now() + 1800)

  clock.suspend(600)
  const launch = Schedule.tick(st, clock.at({ overdue: true }))
  assert.strictEqual(launch.launched, true)
  const failed = Schedule.onFailure(launch.state, clock.at({ kind: "rate_limited", httpStatus: 429 })).state

  assert.strictEqual(failed.retryClass, "transient", "the class did NOT change")
  assert.strictEqual(failed.nextAttemptAt, serverDeadline,
    "the server's Retry-After must survive a second failure of the same class")
})

test("REQ-024a: an overdue launch is still refused while suspended or unconfigured", () => {
  // `overdue` bypasses the DEADLINE, not the refusals. AC-038 says a suspended
  // service launches no helper, and a resume is not an exception to that.
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  const suspended = Schedule.onFailure(st, clock.at({ kind: "site_unselected" })).state
  assert.strictEqual(Schedule.tick(suspended, clock.at({ overdue: true })).launched, false)

  const unconfigured = Schedule.create({ intervalSec: 30, configured: false })
  assert.strictEqual(Schedule.tick(unconfigured, clock.at({ overdue: true })).launched, false)

  // And it does not start a second batch on top of a running one (REQ-016).
  const active = Schedule.tick(settled(createClock(), 30), clock.at({ overdue: true })).state
  assert.strictEqual(active.state, "active-batch")
  const again = Schedule.tick(active, clock.at({ overdue: true }))
  assert.strictEqual(again.launched, false)
  assert.strictEqual(again.reason, "batch-active")
})

test("AC-035: with interval 3600 and a 429, the delay is >= 3600 s, not 900 s", () => {
  // The defect this pins: a flat 900 s cap polls a FAILING controller every
  // 15 minutes while a healthy one is polled hourly, so failure is noisier
  // than success.
  const delay = Schedule.backoffDelay({
    intervalSec: 3600, failures: 1, retryClass: "transient"
  })
  assert.ok(delay >= 3600, "expected >= 3600, got " + delay)
  assert.strictEqual(delay, 3600, "cap is max(interval, 900) = 3600, and 3600×2 exceeds it")
})

test("AC-035: the fatal delay is max(interval, 300 s) in both directions", () => {
  assert.strictEqual(Schedule.backoffDelay(
    { intervalSec: 30, failures: 1, retryClass: "fatal" }), 300)
  assert.strictEqual(Schedule.backoffDelay(
    { intervalSec: 3600, failures: 1, retryClass: "fatal" }), 3600)
  // A fatal condition is cleared by a configuration change, not by waiting
  // longer, so the floor does not escalate with the failure count.
  assert.strictEqual(Schedule.backoffDelay(
    { intervalSec: 30, failures: 9, retryClass: "fatal" }), 300)
})

test("AC-035: the transient sequence doubles and then stops at max(interval, 900)", () => {
  const seen = []
  for (let f = 1; f <= 8; f++) {
    seen.push(Schedule.backoffDelay({ intervalSec: 30, failures: f, retryClass: "transient" }))
  }
  assert.deepStrictEqual(seen, [60, 120, 240, 480, 900, 900, 900, 900])
})

test("AC-035: integrity failures use the transient schedule (REQ-020a)", () => {
  assert.strictEqual(
    Schedule.backoffDelay({ intervalSec: 30, failures: 2, retryClass: "integrity" }),
    Schedule.backoffDelay({ intervalSec: 30, failures: 2, retryClass: "transient" }))
})

test("AC-035: a huge failure count cannot overflow the delay", () => {
  const delay = Schedule.backoffDelay(
    { intervalSec: 15, failures: 5000, retryClass: "transient" })
  assert.ok(isFinite(delay), "delay overflowed to " + delay)
  assert.strictEqual(delay, 900)
})

test("jitter only ever lengthens a delay, so no floor in REQ-019 or REQ-020 can be crossed", () => {
  // Symmetric jitter around the delay would let a 3600 s floor produce 3400 s,
  // which is the exact thing AC-035 forbids. This is asserted rather than
  // commented because it is a one-character change away at all times.
  const base = Schedule.backoffDelay({ intervalSec: 3600, failures: 1, retryClass: "transient" })
  for (const unit of [0, 0.001, 0.5, 0.9999, 1]) {
    const jittered = Schedule.backoffDelay(
      { intervalSec: 3600, failures: 1, retryClass: "transient" }, unit)
    assert.ok(jittered >= base, "jitter shortened the delay at unit=" + unit)
    assert.ok(jittered <= base * (1 + Schedule.JITTER_FRACTION),
      "jitter exceeded its stated fraction at unit=" + unit)
  }
})

// --- Retry-After (REQ-019) -------------------------------------------------

test("Retry-After: the delay-seconds form is honoured when it is larger", () => {
  // "using the larger of the exponential delay and the normalized server
  // delay" — the exponential delay here is 60 s.
  const withServer = Schedule.backoffDelay({
    intervalSec: 30, failures: 1, retryClass: "transient", serverDelaySec: 600
  })
  assert.strictEqual(withServer, 600)

  const smaller = Schedule.backoffDelay({
    intervalSec: 30, failures: 1, retryClass: "transient", serverDelaySec: 5
  })
  assert.strictEqual(smaller, 60, "a server delay shorter than our own must not shorten it")
})

test("Retry-After: a server delay is not subject to the 900 s cap", () => {
  // Ignoring a controller that asks for an hour is how a client gets itself
  // rate-limited harder, so the cap bounds OUR escalation, not the server's ask.
  const delay = Schedule.backoffDelay({
    intervalSec: 30, failures: 1, retryClass: "transient", serverDelaySec: 3600
  })
  assert.strictEqual(delay, 3600)
})

test("Retry-After: both REQ-019 forms normalize to the same seconds", () => {
  const clock = createClock()          // wall = 2026-01-01T00:00:00Z
  const numeric = Schedule.normalizeRetryAfter(120, clock.nowWall())
  const dated = Schedule.normalizeRetryAfter("Thu, 01 Jan 2026 00:02:00 GMT", clock.nowWall())
  assert.strictEqual(numeric.delaySec, 120)
  assert.strictEqual(dated.delaySec, 120)
  assert.strictEqual(numeric.warning, null)
  assert.strictEqual(dated.warning, null)
})

test("Retry-After: a past date is ignored without a warning", () => {
  const clock = createClock()
  const past = Schedule.normalizeRetryAfter("Wed, 31 Dec 2025 23:59:00 GMT", clock.nowWall())
  assert.strictEqual(past.delaySec, null)
  // The ordinary outcome of a slow response, not something the user can act
  // on, so it is silent rather than a warning that would appear during every
  // rate-limit episode.
  assert.strictEqual(past.warning, null)
})

test("Retry-After: malformed values are ignored and say so", () => {
  const clock = createClock()
  for (const bad of ["soon", "-30", "1 Jan 2026", "Thu, 32 Jan 2026 00:00:00 GMT",
                     "Thu, 30 Feb 2026 00:00:00 GMT", "Thu, 01 Jan 2026 25:00:00 GMT",
                     -30, 1.5, NaN, Infinity, true, {}]) {
    const out = Schedule.normalizeRetryAfter(bad, clock.nowWall())
    assert.strictEqual(out.delaySec, null, "accepted " + JSON.stringify(String(bad)))
    assert.ok(out.warning !== null && out.warning.code === "retry_after_ignored",
      "no retry_after_ignored warning for " + JSON.stringify(String(bad)))
  }
})

test("Retry-After: 31 February is rejected rather than rolled into March", () => {
  // Date.UTC normalises an impossible date into a real one, which would turn a
  // malformed header into a delay of roughly three days.
  assert.strictEqual(Schedule.parseHttpDate("Sat, 31 Feb 2026 00:00:00 GMT"), null)
  assert.strictEqual(Schedule.parseHttpDate("Thu, 01 Jan 2026 00:00:00 GMT"), 1767225600)
})

test("Retry-After: an otherwise valid delay beyond 24 h is clamped, with a warning", () => {
  const clock = createClock()
  const out = Schedule.normalizeRetryAfter(200000, clock.nowWall())
  assert.strictEqual(out.delaySec, 86400)
  assert.strictEqual(out.warning.code, "retry_after_clamped")
  assert.deepStrictEqual(out.warning.detail, { requestedSec: 200000, clampedSec: 86400 })
})

test("Retry-After: the clamp warning reaches the caller through onFailure", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  const out = Schedule.onFailure(st, clock.at({
    kind: "rate_limited", httpStatus: 429, retryAfter: 200000
  }))
  assert.strictEqual(out.state.nextAttemptAt, clock.now() + 86400)
  assert.strictEqual(out.warnings.length, 1)
  assert.strictEqual(out.warnings[0].code, "retry_after_clamped")
})

// --- AC-037: a failed manual refresh changes nothing ----------------------

test("AC-037: ten consecutive failed manual refreshes leave the automatic schedule unchanged", () => {
  const clock = createClock()
  let st = settled(clock, 30)

  // Put the scheduler into a real retry-wait first, so there is an automatic
  // schedule with something to lose.
  st = Schedule.tick(st, clock.at()).state
  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  assert.strictEqual(st.state, "retry-wait")

  const frozen = { nextAttemptAt: st.nextAttemptAt, failures: st.failures }

  for (let i = 0; i < 10; i++) {
    clock.advance(1)
    const launch = Schedule.requestManual(st, clock.at())
    assert.strictEqual(launch.launched, true, "a manual refresh must bypass the wait once")
    st = launch.state
    st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  }

  assert.strictEqual(st.nextAttemptAt, frozen.nextAttemptAt,
    "the automatic deadline moved after ten failed manual refreshes")
  assert.strictEqual(st.failures, frozen.failures,
    "a manual failure must not advance the backoff exponent")
  assert.strictEqual(st.state, "retry-wait", "the scheduler must return to the wait it was in")
})

test("AC-037: a manual failure from idle-normal leaves the normal cadence alone", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  const due = st.nextAttemptAt

  clock.advance(5)
  st = Schedule.requestManual(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "timeout" })).state

  assert.strictEqual(st.nextAttemptAt, due,
    "the completion-anchored deadline must survive a failed manual refresh")
  assert.strictEqual(st.failures, 0)
  assert.strictEqual(st.state, "idle-normal")
  // The error is still recorded — REQ-004 surfaces it — it just does not
  // reschedule anything.
  assert.strictEqual(st.lastErrorKind, "timeout")
})

test("REQ-024a: a failed attempt leaves the snapshot's success record byte-identical", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  const before = {
    lastSuccessAt: st.lastSuccessAt,
    lastCompletionAt: st.lastCompletionAt,
    intervalAtCompletion: st.intervalAtCompletion,
    staleAt: Schedule.staleAt(st)
  }

  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  const attemptedAt = st.lastAttemptAt
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state

  assert.strictEqual(st.lastSuccessAt, before.lastSuccessAt)
  assert.strictEqual(st.lastCompletionAt, before.lastCompletionAt)
  assert.strictEqual(st.intervalAtCompletion, before.intervalAtCompletion)
  assert.strictEqual(Schedule.staleAt(st), before.staleAt,
    "a failure must not push staleAt forward; the snapshot on screen is no fresher")
  assert.ok(st.lastAttemptAt > before.lastCompletionAt, "lastAttemptAt must still advance")
  assert.strictEqual(st.lastAttemptAt, attemptedAt)
})

// --- AC-038: refusal while suspended --------------------------------------

test("AC-038: a manual refresh is refused, and launches nothing, while suspended", () => {
  for (const kind of ["configuration_conflict", "uncommitted", "site_unselected"]) {
    const clock = createClock()
    let st = settled(clock, 30)
    st = Schedule.tick(st, clock.at()).state
    st = Schedule.onFailure(st, clock.at({ kind: kind })).state

    assert.strictEqual(st.suspended, true, kind + " must suspend polling")
    assert.strictEqual(st.nextAttemptAt, null,
      kind + ": there is no configuration to schedule an attempt against")

    const out = Schedule.requestManual(st, clock.at())
    assert.strictEqual(out.launched, false, kind + ": a helper was launched")
    assert.strictEqual(out.reason, "suspended")
    assert.strictEqual(out.state.generation, st.generation,
      kind + ": the generation advanced, so a batch was begun")

    // "Suspended is not idle" — the automatic tick is refused too.
    const auto = Schedule.tick(st, clock.at())
    assert.strictEqual(auto.launched, false)
    assert.strictEqual(auto.reason, "suspended")
  }
})

test("AC-038: the three suspending kinds are exactly REQ-018a's list", () => {
  const sentence = /REQ-018a: A manual refresh is \*\*refused\*\* while polling is suspended by\n([\s\S]*?)because there is/.exec(SPEC)
  assert.ok(sentence, "SPEC.md: could not locate REQ-018a's kind list")
  const kinds = sentence[1].match(/`[a-z_]+`/g).map((s) => s.replace(/`/g, ""))
  assert.deepStrictEqual(Schedule.SUSPENDING_KINDS.slice().sort(), kinds.slice().sort())
})

test("a fatal kind that does NOT suspend still retries on the fatal floor", () => {
  // The distinction matters: `credential` is fatal but leaves a coherent
  // configuration, so it keeps a deadline and recovers on its own once the key
  // is fixed. Suspension is reserved for "there is nothing to poll".
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "credential" })).state
  assert.strictEqual(st.suspended, false)
  assert.strictEqual(st.state, "fatal-wait")
  assert.strictEqual(st.nextAttemptAt, clock.now() + 300)
  assert.strictEqual(Schedule.requestManual(st, clock.at()).launched, true)
})

test("REQ-018: a coalesced manual refresh is queued once, however many times it is asked for", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  assert.strictEqual(st.state, "active-batch")

  for (let i = 0; i < 5; i++) {
    const out = Schedule.requestManual(st, clock.at())
    assert.strictEqual(out.launched, false)
    assert.strictEqual(out.reason, "coalesced")
    st = out.state
  }
  assert.strictEqual(st.pendingManual, true)

  const done = Schedule.onSuccess(st, clock.at({ generation: st.generation })).state
  const resumed = Schedule.takePendingManual(done, clock.at())
  assert.strictEqual(resumed.launched, true, "the one queued refresh must run at completion")
  assert.strictEqual(resumed.state.pendingManual, false)
  // And only one: a second call finds nothing pending.
  assert.strictEqual(
    Schedule.takePendingManual(resumed.state, clock.at()).launched, false)
})

test("REQ-016: a poll tick during an active batch is skipped, not queued", () => {
  const clock = createClock()
  let st = settled(clock, 15)
  clock.advance(15)
  st = Schedule.tick(st, clock.at()).state
  const gen = st.generation

  // The minimum interval (15 s) is shorter than the maximum batch duration
  // (25 s), so ticks DO land inside a batch in normal operation.
  clock.advance(15)
  const skipped = Schedule.tick(st, clock.at())
  assert.strictEqual(skipped.launched, false)
  assert.strictEqual(skipped.reason, "batch-active")
  assert.strictEqual(skipped.state.generation, gen, "a second batch was begun")
  assert.strictEqual(skipped.state.pendingManual, false,
    "an automatic tick must not queue itself; a backlog would fire as a burst")
})

test("REQ-016: a completion from an abandoned generation is discarded", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  const stale = st.generation - 1

  const late = Schedule.onSuccess(st, clock.at({ generation: stale }))
  assert.strictEqual(late.ignored, true)
  assert.strictEqual(late.state, st, "an abandoned batch must not affect service state")

  const lateFail = Schedule.onFailure(st, clock.at({ generation: stale, kind: "network" }))
  assert.strictEqual(lateFail.ignored, true)
  assert.strictEqual(lateFail.state, st)
})

// --- AC-039: wake selection and rearming ----------------------------------

test("AC-039: crossing staleAt at T+90 rearms for nextAttemptAt at T+900", () => {
  // The bug this pins is the one the spec review found: the wake timer was
  // armed once and never rearmed, so going grey at T+90 left the service with
  // no timer at all and it stopped polling permanently.
  const clock = createClock()

  // Constructed rather than driven, and deliberately so. staleAt anchors on the
  // last SUCCESS and nextAttemptAt on the last FAILURE, and a failure cannot
  // occur at the same instant as the success before it — so no history a
  // service can actually produce puts these two deadlines exactly 90 s and
  // 900 s from one shared T. The criterion is about the wake SELECTION given
  // those two deadlines, not about how they arose; the test below this one
  // drives the same rearm through a history the reducers do generate.
  const st = Object.assign(Schedule.create({ intervalSec: 30, configured: true }), {
    state: "retry-wait",
    retryClass: "transient",
    failures: 5,                       // 30 × 2⁵ = 960, capped at 900
    lastSuccessAt: clock.nowWall(),
    lastCompletionAt: clock.now(),
    lastCompletionAtWall: clock.nowWall(),
    intervalAtCompletion: 30,
    nextAttemptAt: clock.now() + 900
  })

  const T0 = clock.now()
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 90)

  const first = Schedule.wake(st, clock.at())
  assert.strictEqual(first.armed, true)
  assert.strictEqual(first.delaySec, 90)
  assert.strictEqual(first.reason, "stale", "the nearer of the two deadlines is staleAt")

  clock.advance(90)
  const fired = Schedule.onWake(st, clock.at())
  assert.strictEqual(fired.stale, true, "crossing staleAt must grey the widget")
  assert.strictEqual(fired.launched, false, "the retry is not due yet")
  assert.strictEqual(fired.rearm.armed, true, "the timer must be REARMED after firing")
  assert.strictEqual(fired.rearm.delaySec, 810)
  assert.strictEqual(fired.rearm.reason, "attempt")

  clock.advance(810)
  assert.strictEqual(clock.now(), T0 + 900)
  const retry = Schedule.onWake(fired.state, clock.at())
  assert.strictEqual(retry.launched, true, "the retry must run at T+900")
  assert.strictEqual(retry.state.state, "active-batch")
})

test("AC-039: the rearm also holds on a history the reducers actually produce", () => {
  // The same property as above, driven end to end: five real transient
  // failures push nextAttemptAt to the 900 s cap, the widget goes grey while
  // that wait is still running, and the wake timer must survive the greying.
  const clock = createClock()
  let st = settled(clock, 30)

  for (let i = 0; i < 5; i++) {
    // Monotonic only: the machine is awake and polling, so wall and monotonic
    // would normally move together — but freezing wall here keeps staleAt a
    // fixed 90 s away, which is what makes the two deadlines comparable below.
    clock.advanceMonotonic(st.nextAttemptAt - clock.now())
    const launch = Schedule.tick(st, clock.at())
    assert.strictEqual(launch.launched, true, "failure " + i + " did not launch")
    st = Schedule.onFailure(launch.state, clock.at({ kind: "network" })).state
  }
  assert.strictEqual(st.failures, 5)
  assert.strictEqual(st.nextAttemptAt - clock.now(), 900, "five failures reach the cap")

  // Monotonic time has moved; wall time has not, so the snapshot is still
  // fresh and staleAt is still ahead of us.
  const armed = Schedule.wake(st, clock.at())
  assert.strictEqual(armed.armed, true)
  assert.strictEqual(armed.reason, "stale", "staleAt is the nearer deadline")

  clock.suspend(armed.delaySec)
  const fired = Schedule.onWake(st, clock.at())
  assert.strictEqual(fired.stale, true)
  assert.strictEqual(fired.launched, false)
  assert.strictEqual(fired.rearm.armed, true,
    "going grey must not leave the service without a timer for its pending retry")
  assert.strictEqual(fired.rearm.reason, "attempt")
  assert.strictEqual(fired.rearm.delaySec, st.nextAttemptAt - clock.now())
})

test("AC-039: deadlines already in the past are excluded from the selection", () => {
  // Including them arms a zero-delay timer that fires, finds the deadline still
  // past, and arms another — an immediate-fire loop whose only symptom is a
  // warm laptop.
  const clock = createClock()
  let st = settled(clock, 30)
  clock.advance(10000)                 // both deadlines now far in the past

  assert.ok(Schedule.staleAt(st) < clock.nowWall())
  assert.ok(st.nextAttemptAt < clock.now())
  const armed = Schedule.wake(st, clock.at())
  assert.strictEqual(armed.armed, false)
  assert.strictEqual(armed.delaySec, null)
})

test("AC-039: with neither deadline present the timer is disarmed", () => {
  const clock = createClock()

  // Unconfigured: nothing to poll and nothing to go stale.
  const fresh = Schedule.create({ intervalSec: 30, configured: false })
  assert.strictEqual(Schedule.wake(fresh, clock.at()).armed, false)

  // Suspended, with no successful snapshot to age.
  let st = Schedule.setConfigured(fresh, clock.at({ configured: true })).state
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "configuration_conflict" })).state
  assert.strictEqual(st.nextAttemptAt, null)
  assert.strictEqual(Schedule.wake(st, clock.at()).armed, false)
})

test("AC-039: a suspended service still wakes for a pending staleAt", () => {
  // Suspension removes the ATTEMPT deadline, not the widget's obligation to go
  // grey. Disarming entirely would leave a stale snapshot rendered as current.
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "uncommitted" })).state

  const armed = Schedule.wake(st, clock.at())
  assert.strictEqual(armed.armed, true)
  assert.strictEqual(armed.reason, "stale")
  assert.strictEqual(armed.delaySec, 90)
})

// --- AC-040: the startup ramp ---------------------------------------------

test("AC-040: the first attempt after a valid configuration runs immediately", () => {
  const clock = createClock()
  let st = Schedule.create({ intervalSec: 3600, configured: false })

  const armed = Schedule.setConfigured(st, clock.at({ configured: true }))
  st = armed.state
  assert.strictEqual(st.nextAttemptAt, clock.now(),
    "the first attempt must be due now, not one interval from now")

  const launch = Schedule.tick(st, clock.at())
  assert.strictEqual(launch.launched, true)
  assert.strictEqual(launch.reason, "due")
})

test("AC-040: a first attempt failing with `network` retries every 2 s for at most 30 s", () => {
  const clock = createClock()
  let st = Schedule.create({ intervalSec: 30, configured: false })
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  const rampStart = clock.now()

  let attempts = 0
  while (clock.now() < rampStart + Schedule.RAMP_WINDOW_SEC) {
    const launch = Schedule.tick(st, clock.at())
    assert.strictEqual(launch.launched, true, "ramp attempt " + attempts + " did not launch")
    st = launch.state
    st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
    assert.strictEqual(st.nextAttemptAt, clock.now() + 2,
      "ramp retry " + attempts + " was not 2 s away")
    attempts++
    clock.advance(2)
  }

  assert.strictEqual(attempts, 15, "30 s at 2 s per retry is fifteen ramp attempts")

  // The window has closed: the next failure enters the normal REQ-019 schedule.
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  assert.strictEqual(st.nextAttemptAt, clock.now() + 60,
    "the first post-ramp retry must be one rung of REQ-019, not another 2 s")
})

test("AC-040 / DEV-4: ramp failures do not advance the shared backoff exponent", () => {
  // If they did, fifteen ramp retries would leave `failures` at 15 and pin the
  // first post-ramp wait at the 900 s cap — a 30-second ramp followed by a
  // fifteen-minute silence, which inverts what REQ-023b is for. Recorded as
  // DEV-4 because REQ-020b's "every automatic failure" and REQ-023b's separate
  // schedule cannot both be read literally here.
  const clock = createClock()
  let st = Schedule.create({ intervalSec: 30, configured: false })
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state

  for (let i = 0; i < 5; i++) {
    st = Schedule.tick(st, clock.at()).state
    st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
    clock.advance(2)
  }
  assert.strictEqual(st.failures, 0, "the ramp is a schedule of its own, not REQ-019's first rungs")
})

test("AC-040: a non-network failure during the ramp goes straight to the normal schedule", () => {
  // The ramp covers "the shell started before the network did". A 403 is not
  // that, and retrying it every 2 s for 30 s would hammer a controller that has
  // already given a definitive answer.
  const clock = createClock()
  let st = Schedule.create({ intervalSec: 30, configured: false })
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "forbidden", httpStatus: 403 })).state

  assert.strictEqual(st.state, "fatal-wait")
  assert.strictEqual(st.nextAttemptAt, clock.now() + 300)
  assert.strictEqual(st.failures, 1)
})

test("AC-040: a success closes the ramp, so a later network blip backs off normally", () => {
  const clock = createClock()
  let st = Schedule.create({ intervalSec: 30, configured: false })
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onSuccess(st, clock.at({ generation: st.generation })).state
  assert.strictEqual(st.rampUntil, null)

  clock.advance(5)                     // still inside the original 30 s window
  st = Schedule.requestManual(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  // A manual failure changes no schedule (REQ-018b), so drive an automatic one.
  clock.advance(25)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  assert.strictEqual(st.nextAttemptAt, clock.now() + 60,
    "after a success the ramp is over, whatever the wall clock says")
})

// --- AC-041: the suspend signature ----------------------------------------

test("AC-041: wall time past staleAt with monotonic frozen greys the widget", () => {
  // The system-suspend signature. A scheduler that used one clock for both
  // axes would either never grey (monotonic, which did not move) or reschedule
  // as though 40 minutes of polling had happened (wall). Both are wrong.
  const clock = createClock()
  const st = settled(clock, 30)

  assert.strictEqual(Schedule.isStale(st, clock.nowWall()), false)

  const monoBefore = clock.now()
  clock.suspend(2400)                  // 40 minutes asleep
  assert.strictEqual(clock.now(), monoBefore, "monotonic time must not have moved")

  assert.strictEqual(Schedule.isStale(st, clock.nowWall()), true,
    "a snapshot ages while the machine is asleep")

  // And the monotonic deadline is untouched, so we do not fire a burst of the
  // attempts that "should" have happened during the suspend.
  assert.strictEqual(st.nextAttemptAt, monoBefore + 30,
    "the monotonic deadline must be exactly where it was before the suspend")
  assert.strictEqual(Schedule.tick(st, clock.at()).launched, false,
    "40 minutes of wall time must not fire the eighty attempts that did not happen")
})

test("AC-041: a wall advance greater than one interval marks the schedule overdue", () => {
  const clock = createClock()
  const st = settled(clock, 30)

  clock.suspend(20)
  assert.strictEqual(Schedule.wallOverdue(st, clock.nowWall()), false,
    "less than one interval is not overdue")

  clock.suspend(20)                    // 40 s total, one interval is 30 s
  assert.strictEqual(Schedule.wallOverdue(st, clock.nowWall()), true)
})

test("AC-041: a wall clock stepped BACKWARD does not stall the schedule", () => {
  // NTP or a user correcting a badly-set clock. Scheduling is monotonic, so a
  // backward step must not postpone the next attempt.
  const clock = createClock({ mono: 1000, wall: 1767225600 })
  const st = settled(clock, 30)
  const due = st.nextAttemptAt

  clock.suspend(-86400)                // wall jumps back a day
  clock.advanceMonotonic(30)
  assert.strictEqual(st.nextAttemptAt, due)
  assert.strictEqual(Schedule.tick(st, clock.at()).launched, true,
    "the attempt is due on the monotonic axis regardless of the wall clock")
})

// --- AC-006 / AC-008: the staleness formula -------------------------------

test("AC-008: staleAt is exactly the REQ-022 formula", () => {
  const clock = createClock()
  for (const interval of [15, 30, 45, 46, 300, 3600]) {
    const c = createClock()
    const st = settled(c, interval)
    const expected = st.lastSuccessAt + Math.max(2 * Math.min(interval, interval), 90)
    assert.strictEqual(Schedule.staleAt(st), expected, "interval " + interval)
  }
  // The 90 s floor binds below an interval of 45 s, and only below it.
  assert.strictEqual(Schedule.staleAt(settled(createClock(), 15)) - 1767225600, 90)
  assert.strictEqual(Schedule.staleAt(settled(createClock(), 45)) - 1767225600, 90)
  assert.strictEqual(Schedule.staleAt(settled(createClock(), 46)) - 1767225600, 92)
  assert.ok(clock)
})

test("AC-006: decreasing the interval brings the next attempt and staleAt forward", () => {
  const clock = createClock()
  let st = settled(clock, 600)         // staleAt = +1200, nextAttemptAt = +600
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 1200)
  assert.strictEqual(st.nextAttemptAt - clock.now(), 600)

  const changed = Schedule.changeInterval(st, clock.at({ intervalSec: 300 }))
  st = changed.state
  assert.strictEqual(st.nextAttemptAt - clock.now(), 300, "the attempt must come forward")
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 600,
    "lowering the interval tightens freshness immediately")
  assert.strictEqual(changed.dueNow, false)
})

test("AC-006: increasing the interval above intervalAtCompletion moves staleAt not at all", () => {
  const clock = createClock()
  let st = settled(clock, 300)
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 600)

  st = Schedule.changeInterval(st, clock.at({ intervalSec: 3600 })).state
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 600,
    "an increase must never make an old snapshot fresh again")
  // The next ATTEMPT does move later — REQ-023 says so for idle-normal — but
  // freshness is pinned by the interval in force when the batch completed.
  assert.strictEqual(st.nextAttemptAt - clock.now(), 3600)
})

test("AC-006: reverting a decrease restores the completion-based staleAt", () => {
  // The defect this pins: an earlier design ratcheted staleAt monotonically, so
  // briefly lowering the interval left the snapshot permanently stale and the
  // widget grey until the next success.
  const clock = createClock()
  let st = settled(clock, 600)
  const original = Schedule.staleAt(st)

  st = Schedule.changeInterval(st, clock.at({ intervalSec: 15 })).state
  assert.strictEqual(Schedule.staleAt(st) - clock.nowWall(), 90)

  st = Schedule.changeInterval(st, clock.at({ intervalSec: 600 })).state
  assert.strictEqual(Schedule.staleAt(st), original,
    "reverting must restore the threshold, not leave it ratcheted")
})

test("AC-006: a decrease that places staleAt in the past greys immediately", () => {
  // "This is a defined transition, not an error" — no batch in flight and no
  // failure, just a widget that goes grey the moment the user drags a slider.
  const clock = createClock()
  let st = settled(clock, 3600)
  clock.advance(400)
  assert.strictEqual(Schedule.isStale(st, clock.nowWall()), false)

  st = Schedule.changeInterval(st, clock.at({ intervalSec: 15 })).state
  assert.strictEqual(Schedule.isStale(st, clock.nowWall()), true)
  assert.strictEqual(st.state, "idle-normal", "no failure and no batch: this is not an error")
  assert.strictEqual(st.lastErrorKind, null)
})

test("AC-006: neither direction shortens an active retry deadline", () => {
  for (const newInterval of [15, 3600]) {
    const clock = createClock()
    let st = settled(clock, 300)
    st = Schedule.tick(st, clock.at()).state
    st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
    const deadline = st.nextAttemptAt
    assert.strictEqual(st.state, "retry-wait")

    st = Schedule.changeInterval(st, clock.at({ intervalSec: newInterval })).state
    assert.strictEqual(st.nextAttemptAt, deadline,
      "interval " + newInterval + " moved a retry deadline")
    assert.strictEqual(st.intervalSec, newInterval,
      "the new interval must still become the base for subsequent calculations")
  }
})

test("AC-006: neither direction shortens an active Retry-After deadline", () => {
  const clock = createClock()
  let st = settled(clock, 300)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({
    kind: "rate_limited", httpStatus: 429, retryAfter: 1800
  })).state
  assert.strictEqual(st.nextAttemptAt, clock.now() + 1800)

  st = Schedule.changeInterval(st, clock.at({ intervalSec: 15 })).state
  assert.strictEqual(st.nextAttemptAt, clock.now() + 1800,
    "a server's Retry-After must survive the user changing the interval")
})

test("AC-006: a decrease that leaves the next attempt overdue reports dueNow", () => {
  const clock = createClock()
  let st = settled(clock, 3600)
  clock.advance(400)

  const changed = Schedule.changeInterval(st, clock.at({ intervalSec: 300 }))
  assert.strictEqual(changed.dueNow, true, "300 s after a completion 400 s ago is overdue")
  assert.strictEqual(Schedule.tick(changed.state, clock.at()).launched, true)
})

test("REQ-023: an interval change during a batch applies to the next normal cycle", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  clock.advance(30)
  st = Schedule.tick(st, clock.at()).state
  assert.strictEqual(st.state, "active-batch")

  const changed = Schedule.changeInterval(st, clock.at({ intervalSec: 600 }))
  st = changed.state
  assert.strictEqual(changed.dueNow, false)

  clock.advance(5)
  st = Schedule.onSuccess(st, clock.at({ generation: st.generation })).state
  assert.strictEqual(st.nextAttemptAt, clock.now() + 600,
    "the completed batch anchors the next cycle at the NEW interval")
  assert.strictEqual(st.intervalAtCompletion, 600)
})

test("REQ-023a: the cadence anchors on completion, not launch", () => {
  // With the minimum interval (15 s) shorter than the maximum batch duration
  // (25 s), a launch anchor would poll continuously with no idle gap at all.
  const clock = createClock()
  let st = settled(clock, 15)
  clock.advance(15)
  st = Schedule.tick(st, clock.at()).state
  const launchedAt = clock.now()

  clock.advance(25)                    // a batch that ran to the deadline
  st = Schedule.onSuccess(st, clock.at({ generation: st.generation })).state

  assert.strictEqual(st.nextAttemptAt, launchedAt + 25 + 15)
  assert.strictEqual(st.nextAttemptAt - clock.now(), 15,
    "a full interval of idle must follow every batch")
})

// --- AC-008: composition with the health function -------------------------

test("AC-008: healthLevel returns the success level before staleAt and grey after", () => {
  // Health.js takes `isStale` as a boolean, so AC-008's `healthLevel(now)` is
  // the composition of Schedule.staleAt and Health.healthLevel. Neither module
  // can assert it alone, so it is asserted here where both are in scope.
  const clock = createClock()
  const st = settled(clock, 30)
  const snapshot = {
    counts: {
      devicesTotal: 3, offlineTotal: 0,
      byClass: { online: 3, transitional: 0, down: 0, impaired: 0, unknown: 0 }
    },
    gateways: [{ id: "g1", class: "online" }]
  }

  const fresh = Health.healthLevel({
    snapshot: snapshot, errorKind: null, isStale: Schedule.isStale(st, clock.nowWall())
  })
  assert.strictEqual(fresh.level, "green")
  assert.strictEqual(fresh.rule, 5)

  clock.suspend(90)
  const stale = Health.healthLevel({
    snapshot: snapshot, errorKind: null, isStale: Schedule.isStale(st, clock.nowWall())
  })
  assert.strictEqual(stale.level, "grey")
  assert.strictEqual(stale.rule, 1)
})

test("AC-008: grey wins even when nextAttemptAt is later than staleAt", () => {
  // The case the criterion calls out by name. A long backoff must not keep a
  // stale snapshot rendered as current until the retry lands — the widget goes
  // grey at staleAt and stays grey.
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "network" })).state
  st = Object.assign({}, st, { nextAttemptAt: clock.now() + 900 })

  const staleAt = Schedule.staleAt(st)
  assert.ok(st.nextAttemptAt - clock.now() > staleAt - clock.nowWall(),
    "this test is only meaningful when the retry lands after staleAt")

  clock.advance(90)
  assert.strictEqual(Schedule.isStale(st, clock.nowWall()), true)
  const level = Health.healthLevel({
    snapshot: { counts: { devicesTotal: 1, byClass: { online: 1 } } },
    errorKind: "network",
    isStale: true
  })
  assert.strictEqual(level.level, "grey")
})

test("no snapshot is not the same thing as a stale one", () => {
  // Reporting stale before the first batch finishes would grey a widget that
  // has simply not run yet, and REQ-013 gives that its own panel state.
  const clock = createClock()
  const fresh = Schedule.create({ intervalSec: 30, configured: true })
  assert.strictEqual(Schedule.staleAt(fresh), null)
  assert.strictEqual(Schedule.isStale(fresh, clock.nowWall()), false)
})

// --- the four states are total --------------------------------------------

test("REQ-023: every state the reducers can produce is one of the four named states", () => {
  const clock = createClock()
  const reached = {}
  let st = Schedule.create({ intervalSec: 30, configured: false })
  reached[st.state] = true

  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  reached[st.state] = true
  st = Schedule.tick(st, clock.at()).state
  reached[st.state] = true

  // Every kind, from a fresh active batch each time.
  for (const kind of Schedule.ERROR_KINDS) {
    const statuses = kind === "http" ? [503, 404]
      : (kind === "unauthorized" ? [401] : (kind === "forbidden" ? [403]
        : (kind === "rate_limited" ? [429] : [null])))
    for (const status of statuses) {
      let probe = settled(createClock(), 30)
      probe = Schedule.tick(probe, clock.at()).state
      const out = Schedule.onFailure(probe, clock.at({ kind: kind, httpStatus: status }))
      reached[out.state.state] = true
      assert.ok(Schedule.STATES.indexOf(out.state.state) !== -1,
        kind + "/" + status + " produced state " + JSON.stringify(out.state.state))
      assert.ok(Schedule.RETRY_CLASSES.indexOf(out.state.retryClass) !== -1,
        kind + "/" + status + " produced retry class " + JSON.stringify(out.state.retryClass))
    }
  }

  let ok = settled(createClock(), 30)
  reached[ok.state] = true

  assert.deepStrictEqual(Object.keys(reached).sort(), Schedule.STATES.slice().sort(),
    "all four REQ-023 states must be reachable, and no fifth one")
})

test("the interval bounds are enforced however the value arrives", () => {
  const clock = createClock()
  assert.strictEqual(Schedule.create({ intervalSec: 5 }).intervalSec, 15)
  assert.strictEqual(Schedule.create({ intervalSec: 99999 }).intervalSec, 3600)
  assert.strictEqual(Schedule.create({ intervalSec: "30" }).intervalSec, 30)
  assert.strictEqual(Schedule.create({}).intervalSec, 30)
  assert.strictEqual(Schedule.create({ intervalSec: NaN }).intervalSec, 30)

  const st = Schedule.create({ intervalSec: 30, configured: true })
  assert.strictEqual(Schedule.changeInterval(st, clock.at({ intervalSec: -1 })).state.intervalSec, 15)
})

test("losing configuration disarms the schedule (REQ-024's unconfigured case)", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  assert.strictEqual(Schedule.wake(st, clock.at()).armed, true)

  st = Schedule.setConfigured(st, clock.at({ configured: false })).state
  assert.strictEqual(st.nextAttemptAt, null)
  assert.strictEqual(Schedule.wake(st, clock.at()).armed, false)
  assert.strictEqual(Schedule.tick(st, clock.at()).launched, false)
  assert.strictEqual(Schedule.requestManual(st, clock.at()).launched, false)
})

test("regaining configuration clears a suspension and runs immediately (REQ-020)", () => {
  // REQ-020: fatal failures "are cleared only by a configuration change, an
  // acknowledged reload, or a successful manual refresh". The first two arrive
  // here as setConfigured.
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "configuration_conflict" })).state
  assert.strictEqual(st.suspended, true)

  st = Schedule.setConfigured(st, clock.at({ configured: false })).state
  st = Schedule.setConfigured(st, clock.at({ configured: true })).state
  assert.strictEqual(st.suspended, false)
  assert.strictEqual(st.failures, 0)
  assert.strictEqual(st.nextAttemptAt, clock.now())
  assert.strictEqual(Schedule.requestManual(st, clock.at()).launched, true)
})

test("a successful manual refresh clears a fatal condition (REQ-020)", () => {
  const clock = createClock()
  let st = settled(clock, 30)
  st = Schedule.tick(st, clock.at()).state
  st = Schedule.onFailure(st, clock.at({ kind: "credential" })).state
  assert.strictEqual(st.state, "fatal-wait")

  st = Schedule.requestManual(st, clock.at()).state
  st = Schedule.onSuccess(st, clock.at({ generation: st.generation })).state
  assert.strictEqual(st.state, "idle-normal")
  assert.strictEqual(st.lastErrorKind, null)
  assert.strictEqual(st.retryClass, null)
  assert.strictEqual(st.failures, 0)
})

test("the reducers never mutate the state they are given", () => {
  // Every assertion above that compares a "before" to an "after" depends on
  // this. If a reducer mutated in place, the before-value would change too and
  // the comparison would pass vacuously.
  const clock = createClock()
  const st = settled(clock, 30)
  const snapshot = JSON.stringify(st)

  Schedule.tick(st, clock.at())
  Schedule.requestManual(st, clock.at())
  Schedule.changeInterval(st, clock.at({ intervalSec: 900 }))
  Schedule.onSuccess(st, clock.at({ generation: st.generation }))
  Schedule.onFailure(st, clock.at({ kind: "network" }))
  Schedule.setConfigured(st, clock.at({ configured: false }))

  assert.strictEqual(JSON.stringify(st), snapshot)
})

// --- the monotonic axis, built from a wall clock --------------------------
//
// `Service.qml` has one clock and this file's reducers need two. The axis is
// here rather than in QML for the reason the whole pure layer exists: a
// backwards clock is not a thing a live harness can arrange on demand, and a
// scheduler that stalls for an hour after an NTP correction is exactly the
// failure REQ-024's monotonic axis is for.

test("REQ-024: a backwards wall step never moves the monotonic axis back", () => {
  let clock = Schedule.createClock(1000)
  clock = Schedule.advanceClock(clock, 1010)
  assert.strictEqual(clock.mono, 10)

  // An NTP correction of one hour backwards.
  clock = Schedule.advanceClock(clock, 1010 - 3600)
  assert.strictEqual(clock.mono, 10, "the axis went backwards")

  // And it resumes from where it was, rather than replaying the hour.
  clock = Schedule.advanceClock(clock, 1010 - 3600 + 5)
  assert.strictEqual(clock.mono, 15)
})

test("a backwards step cannot postpone a deadline that was already due", () => {
  // The consequence, stated as the scheduler sees it. Without the clamp the
  // monotonic clock would drop an hour, `now < nextAttemptAt` would become
  // true again, and polling would stop until the hour had passed a second time.
  let clock = Schedule.createClock(1000)
  const state = Schedule.create({ configured: true, intervalSec: 30 })
  const armed = Schedule.changeInterval(
    Schedule.onSuccess(Schedule.tick(state, { now: 0, nowWall: 1000 }).state,
      { now: 0, nowWall: 1000, generation: 1 }).state,
    { now: 0, intervalSec: 30 })

  clock = Schedule.advanceClock(clock, 1040)        // 40 s of real time
  assert.ok(clock.mono >= armed.state.nextAttemptAt,
    "the deadline should be due after 40 s")
  clock = Schedule.advanceClock(clock, 1040 - 3600) // the clock steps back
  assert.ok(clock.mono >= armed.state.nextAttemptAt,
    "a backwards clock step un-due'd a deadline that had passed")
})

test("the axis advances at wall rate while the wall clock behaves", () => {
  let clock = Schedule.createClock(500)
  for (let i = 1; i <= 10; i++) clock = Schedule.advanceClock(clock, 500 + i)
  assert.strictEqual(clock.mono, 10)
  assert.strictEqual(clock.lastWall, 510)
})

test("a forward jump is accepted, because a suspend is indistinguishable", () => {
  // Documented rather than defended against: a suspend and a forward NTP step
  // look identical from here. For a suspend, being due early is correct and is
  // what REQ-024a wants; for an NTP step it costs one early poll.
  let clock = Schedule.createClock(1000)
  clock = Schedule.advanceClock(clock, 1000 + 3600)
  assert.strictEqual(clock.mono, 3600)
})

test("a non-numeric reading leaves the axis exactly where it was", () => {
  const clock = Schedule.createClock(1000)
  for (const bad of [undefined, null, NaN, Infinity, "1010", {}]) {
    assert.deepStrictEqual(Schedule.advanceClock(clock, bad), clock,
      String(bad))
  }
})
