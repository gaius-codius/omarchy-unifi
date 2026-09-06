// Schedule.js — REQ-015..REQ-024a. The scheduler, as a pure reducer.
//
// Dual-use: this file runs unchanged under QML's V4 engine and under Node's V8
// (see the header of Health.js for what that constrains and why). HC-16 forbids
// dual-use modules from importing one another, so anything shared with
// Settings.js or ViewModel.js is duplicated here on purpose and held to the
// original by tests/model/consistency.test.js.
//
// Two decisions govern everything below.
//
// **No timers.** This module owns no `Timer`, arms nothing and fires nothing.
// It answers "what should happen, and when" and returns a new state; Phase 9
// wires a QML `Timer` to `wake()`'s answer. HC-9 says the shell ships no backoff
// helper, so all of this is written from scratch — which is exactly why it is
// worth having every rule of it reachable from `node --test` with no compositor.
//
// **All times are SECONDS**, on two independent axes:
//
//   `now`     monotonic — every scheduling deadline (REQ-024a). Frozen across a
//             system suspend, which is the point: a suspend must not be
//             mistaken for elapsed polling time.
//   `nowWall` wall clock — staleness only (REQ-024a), because a snapshot
//             genuinely does age while the machine is asleep.
//
// Mixing the two axes is the defect this module is shaped to prevent, so no
// function takes an ambiguous `time` and no state field holds an unlabelled
// one. `wake()` never compares a wall deadline to a monotonic one directly; it
// converts both to *delays* first. AC-041 advances one axis while freezing the
// other, which is the only way that mistake shows up as a test failure.

// --- Interval bounds (duplicated from Settings.js — HC-16) ----------------
// Settings.js owns validation of the user's value. These exist so that a
// caller who bypasses Settings cannot drive the schedule with a non-number,
// and so the arithmetic below never has to consider one.
// consistency.test.js asserts these equal Settings.BOUNDS/DEFAULTS.
const INTERVAL_MIN_SEC = 15
const INTERVAL_MAX_SEC = 3600
const INTERVAL_DEFAULT_SEC = 30

// --- REQ-023 ---------------------------------------------------------------
const STATES = ["idle-normal", "active-batch", "retry-wait", "fatal-wait"]

// --- REQ-019 / REQ-020 / REQ-020a -----------------------------------------
const RETRY_CLASSES = ["transient", "fatal", "integrity"]

// REQ-019's cap and REQ-020's floor are both relative to the configured
// interval, never flat. A flat 900 s cap would poll a *failing* controller
// every 15 minutes while a healthy one at `refreshIntervalSec = 3600` is polled
// hourly — failure would be noisier than success. That inversion is what
// AC-035 pins.
const TRANSIENT_CAP_SEC = 900
const FATAL_FLOOR_SEC = 300

// REQ-022. Two intervals, but never less than this.
const STALE_FLOOR_SEC = 90

// REQ-023b. The host's own startup-ramp precedent: the shell can come up before
// the network does.
const RAMP_WINDOW_SEC = 30
const RAMP_DELAY_SEC = 2

// REQ-019. A server delay beyond this is clamped, with a warning.
const RETRY_AFTER_MAX_SEC = 86400

// REQ-019 says "with jitter" and does not say how much. Jitter here is
// strictly ADDITIVE — a spread of [0, JITTER_FRACTION] *above* the computed
// delay, never around it. Symmetric jitter would let a delay fall under
// `max(interval, 900 s)`, and that floor is the whole subject of AC-035; a
// scheme that violates the requirement it is decorating is not jitter, it is a
// bug with a statistical alibi.
const JITTER_FRACTION = 0.1

// --- DATA-007: the nineteen kinds -----------------------------------------
// Listed in the spec's order so the two can be diffed by eye. The retry class
// of each is the table in docs/protocol-v1.md; consistency.test.js parses that
// table and requires this map to reproduce it exactly, in both directions.
const ERROR_KINDS = [
  "unconfigured", "site_unselected", "uncommitted", "credential",
  "unauthorized", "forbidden", "tls", "network", "timeout", "rate_limited",
  "http", "unsupported", "redirect", "configuration_conflict",
  "partial_response", "oversized_response", "malformed_response",
  "helper_unavailable", "internal"
]

// `http` is deliberately absent: it is the one kind whose class depends on its
// status, so it has no constant entry and `retryClassFor` must be consulted.
// Leaving it out is what makes the completeness assertion in AC-036 meaningful
// — a table with a wrong constant for `http` would still be "complete".
const KIND_RETRY_CLASS = {
  unconfigured: "fatal",
  site_unselected: "fatal",
  uncommitted: "fatal",
  credential: "fatal",
  unauthorized: "fatal",
  forbidden: "fatal",
  tls: "fatal",
  network: "transient",
  timeout: "transient",
  rate_limited: "transient",
  unsupported: "fatal",
  redirect: "integrity",
  configuration_conflict: "fatal",
  partial_response: "integrity",
  oversized_response: "integrity",
  malformed_response: "integrity",
  helper_unavailable: "fatal",
  internal: "integrity"
}

// REQ-019's "transient 5xx". 501 (not implemented) and 505 are excluded because
// retrying them cannot succeed; these four are the ones a load balancer or a
// restarting controller emits while coming back.
const TRANSIENT_HTTP_STATUSES = [500, 502, 503, 504]

// REQ-018a. All three are fatal kinds, but they are more than fatal: there is
// no unambiguous configuration to run *any* request against, so polling stops
// entirely rather than retrying on the fatal floor. `unconfigured` is not in
// this list — it is handled by `configured: false`, which is a different thing
// (nothing to poll yet, as against a configuration that contradicts itself).
const SUSPENDING_KINDS = ["configuration_conflict", "uncommitted", "site_unselected"]

const MONTH_INDEX = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11
}

// --- small helpers ---------------------------------------------------------

function isFiniteNumber(value) {
  return typeof value === "number" && isFinite(value)
}

function contains(list, value) {
  for (let i = 0; i < list.length; i++) if (list[i] === value) return true
  return false
}

function warn(code, message, detail) {
  return { code: code, message: message, detail: detail === undefined ? null : detail }
}

// --- retry classification (REQ-019 / REQ-020 / REQ-020a, DATA-007a) --------

// Total over every input, including inputs DATA-007 does not permit. DATA-007
// says an unknown kind maps to `internal` in QML, and `internal` is retryable,
// so an unrecognised kind is retried rather than treated as fatal. That is the
// safe direction: a kind this build has never heard of is far more likely to be
// a newer helper than a permanent misconfiguration, and treating it as fatal
// would silently stop polling.
function retryClassFor(kind, httpStatus) {
  if (kind === "http") {
    return contains(TRANSIENT_HTTP_STATUSES, httpStatus) ? "transient" : "fatal"
  }
  const known = KIND_RETRY_CLASS[kind]
  if (known === undefined) return KIND_RETRY_CLASS.internal
  return known
}

// DATA-007a requires the envelope's `retryable` to equal the kind's class.
// Protocol.js (Phase 4) checks the envelope against this function rather than
// against a second opinion of its own.
function isRetryable(kind, httpStatus) {
  return retryClassFor(kind, httpStatus) !== "fatal"
}

// --- Retry-After (REQ-019) -------------------------------------------------

// IMF-fixdate only — `Sun, 06 Nov 1994 08:49:37 GMT`. RFC 7231 requires senders
// to use exactly this form and permits recipients to accept two obsolete ones;
// neither is accepted here. The cost of that is bounded and one-directional: an
// unparsed header is *ignored*, which falls back to the exponential delay, so
// the worst case is that we retry on our own schedule instead of the server's.
//
// Date.parse is not used because its handling of non-ISO input is
// implementation-defined and V4 and V8 need not agree. Date.UTC is specified
// exactly, so this parser gives the same answer in both engines.
function parseHttpDate(text) {
  if (typeof text !== "string") return null
  const m = /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT$/.exec(text)
  if (m === null) return null

  const day = Number(m[1])
  const month = MONTH_INDEX[m[2]]
  const year = Number(m[3])
  const hh = Number(m[4])
  const mm = Number(m[5])
  const ss = Number(m[6])

  if (hh > 23 || mm > 59 || ss > 60) return null
  // A leap second is a real HTTP-date; clamping it is closer than rejecting it.
  const seconds = ss === 60 ? 59 : ss

  const ms = Date.UTC(year, month, day, hh, mm, seconds)
  const round = new Date(ms)
  // Date.UTC silently rolls 31 February into March. Round-tripping is the only
  // way to tell a real date from one that was normalised into existence.
  if (round.getUTCFullYear() !== year || round.getUTCMonth() !== month
      || round.getUTCDate() !== day) {
    return null
  }
  return ms / 1000
}

// Accepts both REQ-019 forms and returns whole seconds, or null for "ignore
// this". Returns a warning only for the 24-hour clamp, which REQ-019 names;
// a malformed or past value emits `retry_after_ignored`, which the spec's
// warning table already reserves.
//
// `raw` arrives as a number on the shipping path — DATA-007a defines
// `retryAfterSec` as a number and the helper normalises the header — so the
// date branch is the duplicated half of a rule the helper also implements
// (Phase 6). It is here because REQ-019 states both forms as service
// behaviour, and because a rule with one implementation and no second reader
// is a rule nothing checks.
function normalizeRetryAfter(raw, nowWall) {
  if (raw === null || raw === undefined) return { delaySec: null, warning: null }

  let seconds = null

  if (typeof raw === "number") {
    if (!isFinite(raw) || raw < 0 || Math.floor(raw) !== raw) {
      return { delaySec: null, warning: warn("retry_after_ignored",
        "The server's Retry-After value was not a whole number of seconds; using the normal retry schedule.") }
    }
    seconds = raw
  } else if (typeof raw === "string") {
    const trimmed = raw.replace(/^[ \t]+|[ \t]+$/g, "")
    if (/^\d+$/.test(trimmed)) {
      seconds = Number(trimmed)
    } else {
      const at = parseHttpDate(trimmed)
      if (at === null) {
        return { delaySec: null, warning: warn("retry_after_ignored",
          "The server's Retry-After value could not be read; using the normal retry schedule.") }
      }
      if (!isFiniteNumber(nowWall)) return { delaySec: null, warning: null }
      seconds = at - nowWall
      if (seconds <= 0) {
        // A date already in the past. Not an error and not worth a warning
        // the user can act on: it is the ordinary outcome of a slow response.
        return { delaySec: null, warning: null }
      }
      seconds = Math.ceil(seconds)
    }
  } else {
    return { delaySec: null, warning: warn("retry_after_ignored",
      "The server's Retry-After value was not a number or a date; using the normal retry schedule.") }
  }

  if (seconds > RETRY_AFTER_MAX_SEC) {
    return {
      delaySec: RETRY_AFTER_MAX_SEC,
      warning: warn("retry_after_clamped",
        "The controller asked for a retry delay longer than 24 hours; waiting 24 hours instead.",
        { requestedSec: seconds, clampedSec: RETRY_AFTER_MAX_SEC })
    }
  }
  return { delaySec: seconds, warning: null }
}

// --- backoff (REQ-019 / REQ-020 / REQ-020a) -------------------------------

// `failures` is the consecutive-failure count AFTER the failure being scheduled
// has been counted, so the first retry of a 30 s interval lands at 60 s, not
// 30 s. That is REQ-019's formula read literally (`interval × 2^failures`); the
// alternative — treating the first retry as one plain interval — would make the
// backoff sequence start a step behind the requirement it cites.
//
// `jitterUnit` is a caller-supplied number in [0, 1). It is a parameter rather
// than a call to Math.random inside this function so that every test in
// scheduler.test.js is exact, and so the module stays pure. Omit it for no
// jitter; Phase 9 passes Math.random().
function backoffDelay(input, jitterUnit) {
  const intervalSec = isFiniteNumber(input.intervalSec) ? input.intervalSec : INTERVAL_DEFAULT_SEC
  const retryClass = input.retryClass
  const failures = isFiniteNumber(input.failures) && input.failures > 0 ? Math.floor(input.failures) : 0

  let base

  if (retryClass === "fatal") {
    // REQ-020 is a FLOOR, not a curve: a fatal condition is cleared by a
    // configuration change, not by waiting longer, so escalating the delay
    // would only slow the recovery the user is actively working towards.
    base = Math.max(intervalSec, FATAL_FLOOR_SEC)
  } else {
    const cap = Math.max(intervalSec, TRANSIENT_CAP_SEC)
    // The exponent is bounded rather than left to overflow into Infinity.
    // min() would still return the cap, but relying on that is relying on
    // IEEE-754 behaviour at the edge of two engines to implement a
    // requirement, and this is cheaper to read than to reason about.
    const exponent = failures > 30 ? 30 : failures
    base = Math.min(intervalSec * Math.pow(2, exponent), cap)
  }

  // REQ-019: "the larger of the exponential delay and the normalized server
  // delay". The server delay is NOT subject to the cap — a controller that
  // asks for an hour gets an hour (up to the 24 h clamp), because ignoring it
  // is how a client gets itself rate-limited harder.
  const serverDelay = input.serverDelaySec
  if (isFiniteNumber(serverDelay) && serverDelay > base) base = serverDelay

  if (isFiniteNumber(jitterUnit) && jitterUnit > 0) {
    const unit = jitterUnit >= 1 ? 1 : jitterUnit
    base = base + base * JITTER_FRACTION * unit
  }
  return base
}

// --- state -----------------------------------------------------------------

function clampInterval(value) {
  if (!isFiniteNumber(value)) return INTERVAL_DEFAULT_SEC
  if (value < INTERVAL_MIN_SEC) return INTERVAL_MIN_SEC
  if (value > INTERVAL_MAX_SEC) return INTERVAL_MAX_SEC
  return Math.floor(value)
}

// Every reducer below returns a NEW state; none mutates its argument. Tests
// therefore hold onto earlier states and compare, which is how AC-008's
// "byte-identical observedAt" and AC-037's "unchanged schedule" are asserted
// rather than described.
function copy(st, changes) {
  const out = {}
  for (const key in st) if (Object.prototype.hasOwnProperty.call(st, key)) out[key] = st[key]
  for (const key in changes) if (Object.prototype.hasOwnProperty.call(changes, key)) out[key] = changes[key]
  return out
}

function create(input) {
  const src = input || {}
  return {
    configured: src.configured === true,
    state: "idle-normal",
    intervalSec: clampInterval(src.intervalSec),
    generation: 0,

    // REQ-020b: one counter, shared across classes, incremented by every
    // automatic failure and reset only by success.
    failures: 0,
    retryClass: null,
    lastErrorKind: null,
    suspended: false,

    // REQ-018: at most ONE pending manual refresh is ever outstanding.
    pendingManual: false,
    manualInFlight: false,

    nextAttemptAt: null,          // monotonic
    lastAttemptAt: null,          // monotonic
    lastCompletionAt: null,       // monotonic
    lastCompletionAtWall: null,   // wall — REQ-024a's overdue test
    intervalAtCompletion: null,   // REQ-022
    lastSuccessAt: null,          // WALL — REQ-022 ages a snapshot in real time
    rampUntil: null               // monotonic — REQ-023b
  }
}

// REQ-023b. Acquiring a valid configuration makes the first attempt due
// immediately, not one interval from now, and opens the startup ramp.
function setConfigured(st, input) {
  const now = input.now
  if (input.configured !== true) {
    // Losing configuration disarms everything (REQ-024's "unconfigured" case).
    return {
      state: copy(st, {
        configured: false, state: "idle-normal", nextAttemptAt: null,
        rampUntil: null, pendingManual: false, failures: 0, retryClass: null
      })
    }
  }
  if (st.configured === true) return { state: st }
  return {
    state: copy(st, {
      configured: true,
      state: "idle-normal",
      suspended: false,
      failures: 0,
      retryClass: null,
      lastErrorKind: null,
      nextAttemptAt: now,
      rampUntil: now + RAMP_WINDOW_SEC
    })
  }
}

// REQ-023: the interval-change rule, stated once per state.
function changeInterval(st, input) {
  const intervalSec = clampInterval(input.intervalSec)
  const now = input.now
  const next = copy(st, { intervalSec: intervalSec })

  if (st.state === "idle-normal") {
    if (st.lastCompletionAt !== null) {
      next.nextAttemptAt = st.lastCompletionAt + intervalSec
    }
    // A decrease that lands in the past is not an error — it is simply
    // overdue, and `tick` will launch on the next evaluation.
    return { state: next, dueNow: next.nextAttemptAt !== null && next.nextAttemptAt <= now }
  }

  if (st.state === "active-batch") {
    // "applies to the next normal cycle" — and it does, because REQ-023a
    // recomputes nextAttemptAt from the interval in force at completion.
    return { state: next, dueNow: false }
  }

  // retry-wait and fatal-wait: the deadline is NEVER shortened, in either
  // direction. Not "not shortened by a decrease" — untouched. Lengthening it on
  // an increase would punish a user who widens the interval while an outage is
  // in progress, and REQ-023 gives the new interval effect only on "subsequent
  // calculations", which the next failure will perform.
  return { state: next, dueNow: false }
}

// --- launching (REQ-016 / REQ-018 / REQ-018a) ------------------------------

function beginBatch(st, input, manual) {
  return copy(st, {
    state: "active-batch",
    generation: st.generation + 1,
    lastAttemptAt: input.now,
    manualInFlight: manual === true,
    pendingManual: false
  })
}

// An automatic poll tick. REQ-016: ticks that fire during an active batch are
// SKIPPED, not queued — queueing them would let a slow controller build a
// backlog that then fires as a burst.
//
// `input.overdue` is REQ-024a's resume rule: after a suspend that advanced the
// wall clock by more than one interval, the schedule is overdue and a batch
// starts even though the monotonic deadline has not been reached — monotonic
// time did not pass during the suspend, so waiting for it would leave a
// machine that slept for an hour showing an hour-old snapshot. Callers pass
// `wallOverdue(st, nowWall)`. It bypasses the deadline and nothing else: an
// unconfigured, suspended or already-active scheduler still refuses.
function tick(st, input) {
  const now = input.now
  if (st.configured !== true) return { state: st, launched: false, reason: "unconfigured" }
  if (st.suspended === true) return { state: st, launched: false, reason: "suspended" }
  if (st.state === "active-batch") return { state: st, launched: false, reason: "batch-active" }
  if (input.overdue === true) return { state: beginBatch(st, input, false), launched: true, reason: "overdue" }
  if (st.nextAttemptAt === null) return { state: st, launched: false, reason: "no-deadline" }
  if (now < st.nextAttemptAt) return { state: st, launched: false, reason: "not-due" }
  return { state: beginBatch(st, input, false), launched: true, reason: "due" }
}

// REQ-018 / REQ-018a. The three outcomes are: refused, coalesced, launched.
function requestManual(st, input) {
  if (st.configured !== true) {
    return { state: st, launched: false, reason: "unconfigured" }
  }
  if (st.suspended === true) {
    // AC-038. "Suspended is not idle" — the refusal is the whole point, and it
    // must launch no helper, so this returns before beginBatch can run.
    return { state: st, launched: false, reason: "suspended" }
  }
  if (st.state === "active-batch") {
    // Exactly one pending manual refresh, however many times the button is
    // pressed. `pendingManual` is a boolean and not a count for that reason.
    return { state: copy(st, { pendingManual: true }), launched: false, reason: "coalesced" }
  }
  // "A manual refresh bypasses the current wait once" — including a retry-wait
  // or fatal-wait deadline. nextAttemptAt is deliberately left alone here; if
  // this batch fails, REQ-018b requires the automatic schedule to be exactly
  // where it was.
  return { state: beginBatch(st, input, true), launched: true, reason: "manual" }
}

// After a batch completes, the coalesced manual refresh (if any) runs.
function takePendingManual(st, input) {
  if (st.pendingManual !== true) return { state: st, launched: false, reason: "none-pending" }
  if (st.suspended === true) {
    // The batch that just finished is what suspended us. Drop the pending
    // refresh rather than launching it into a configuration that cannot serve
    // it — AC-038 says a suspended service launches no helper, and a queued
    // request is not an exception to that.
    return { state: copy(st, { pendingManual: false }), launched: false, reason: "suspended" }
  }
  return { state: beginBatch(st, input, true), launched: true, reason: "manual" }
}

// --- completion ------------------------------------------------------------

// REQ-016 authority check. A batch abandoned by the watchdog (REQ-017a) may
// still exit and still produce output; its generation no longer matches, so
// everything it says is discarded here rather than at each call site.
function isCurrent(st, input) {
  return input.generation === undefined || input.generation === st.generation
}

// REQ-023a: the normal cadence anchors on COMPLETION, not launch. With the
// minimum interval (15 s) shorter than the maximum batch duration (25 s), a
// launch anchor would poll continuously with no idle gap at all.
function onSuccess(st, input) {
  if (!isCurrent(st, input)) return { state: st, ignored: true }
  const now = input.now
  const nowWall = input.nowWall
  return {
    state: copy(st, {
      state: "idle-normal",
      failures: 0,
      retryClass: null,
      lastErrorKind: null,
      suspended: false,
      manualInFlight: false,
      rampUntil: null,
      lastCompletionAt: now,
      lastCompletionAtWall: nowWall,
      intervalAtCompletion: st.intervalSec,
      lastSuccessAt: nowWall,
      nextAttemptAt: now + st.intervalSec
    }),
    ignored: false
  }
}

// REQ-018b / REQ-019 / REQ-020 / REQ-020a / REQ-020b / REQ-023b / REQ-024a.
//
// `input`: { now, nowWall, kind, httpStatus, retryAfter, generation, jitterUnit }
function onFailure(st, input) {
  if (!isCurrent(st, input)) return { state: st, ignored: true, warnings: [] }

  const now = input.now
  const nowWall = input.nowWall
  const kind = input.kind
  const newClass = retryClassFor(kind, input.httpStatus)
  const suspends = contains(SUSPENDING_KINDS, kind)
  const warnings = []

  // REQ-024a: a failed attempt never rewrites the snapshot. lastSuccessAt,
  // intervalAtCompletion and lastCompletionAt* are all left exactly as they
  // were, so staleAt keeps ageing from the last real success and the snapshot
  // the panel is showing does not silently become "fresh" because we tried.
  const base = {
    manualInFlight: false,
    lastErrorKind: kind,
    retryClass: newClass,
    suspended: suspends
  }

  if (st.manualInFlight === true) {
    // REQ-018b / AC-037. A manual failure increments nothing and moves
    // nothing: not the exponent, not nextAttemptAt. Ten of them in a row leave
    // the automatic schedule byte-for-byte where it was, so hammering Refresh
    // during an outage can never slow recovery.
    //
    // Suspension is the one exception, and it is not an exception to the rule
    // above but to a different question: `configuration_conflict` means there
    // is no configuration to poll, so the deadline is cleared rather than
    // postponed. Keeping a deadline here would schedule an attempt that
    // REQ-018a then refuses.
    const resumed = suspends ? "fatal-wait"
      : (st.retryClass === null ? "idle-normal"
        : (st.retryClass === "fatal" ? "fatal-wait" : "retry-wait"))
    return {
      state: copy(st, copy(base, {
        state: resumed,
        retryClass: suspends ? newClass : st.retryClass,
        nextAttemptAt: suspends ? null : st.nextAttemptAt
      })),
      ignored: false,
      warnings: warnings
    }
  }

  if (suspends) {
    // REQ-024's second no-deadline case. Cleared by a configuration change or
    // an acknowledged reload (REQ-020), both of which arrive as setConfigured.
    return {
      state: copy(st, copy(base, {
        state: "fatal-wait", nextAttemptAt: null, rampUntil: null,
        failures: st.failures + 1
      })),
      ignored: false,
      warnings: warnings
    }
  }

  // REQ-023b's ramp. It is a schedule of its own, not the first rung of
  // REQ-019's, so a ramp failure does NOT advance the shared exponent. If it
  // did, fifteen 2-second retries would leave `failures` at 15 and the first
  // post-ramp wait pinned at the 900 s cap — a 30-second ramp followed by a
  // fifteen-minute silence, which is the opposite of what REQ-023b is for.
  // Raised as DEV-4; REQ-020b's "every automatic failure" and REQ-023b's
  // separate schedule cannot both be read literally here.
  const inRamp = st.rampUntil !== null && now < st.rampUntil && kind === "network"
  if (inRamp) {
    return {
      state: copy(st, copy(base, {
        state: "retry-wait",
        nextAttemptAt: now + RAMP_DELAY_SEC
      })),
      ignored: false,
      warnings: warnings
    }
  }

  const failures = st.failures + 1

  const normalized = normalizeRetryAfter(input.retryAfter, nowWall)
  if (normalized.warning !== null) warnings.push(normalized.warning)

  const delay = backoffDelay({
    intervalSec: st.intervalSec,
    failures: failures,
    retryClass: newClass,
    serverDelaySec: normalized.delaySec
  }, input.jitterUnit)

  let deadline = now + delay

  // REQ-020b names this for a CLASS CHANGE — a fatal failure followed by a
  // transient one at a low interval would otherwise pull a 300 s fatal
  // deadline forward to 30 s, and a controller returning one 503 after a
  // string of 403s would be polled harder than one returning only 403s.
  //
  // It is applied unconditionally here, not only on a class change, because
  // the same-class case is reachable by the same route (REQ-024a's
  // resume-overdue launch) and has the same defect: a `rate_limited` failure
  // carrying `Retry-After: 1800`, followed by another after a suspend, would
  // fall back to the 120 s exponential delay and discard the server's
  // instruction — which is how a client gets itself rate-limited harder.
  // REQ-020b is satisfied as written; this is the general rule of which its
  // sentence is the case the spec happened to name.
  if (st.nextAttemptAt !== null && st.nextAttemptAt > deadline) {
    deadline = st.nextAttemptAt
  }

  return {
    state: copy(st, copy(base, {
      state: newClass === "fatal" ? "fatal-wait" : "retry-wait",
      failures: failures,
      rampUntil: null,
      nextAttemptAt: deadline
    })),
    ignored: false,
    warnings: warnings
  }
}

// --- staleness (REQ-022) ---------------------------------------------------

// Evaluated live, never latched. The `min` is what makes the four properties
// REQ-022 lists all hold at once: raising the interval above the value in force
// at completion cannot make an old snapshot fresh again, lowering it tightens
// freshness at once, and reverting a lowering restores the completion-based
// threshold instead of ratcheting the snapshot permanently stale.
//
// Returns WALL seconds, because a snapshot ages across a suspend (REQ-024a).
function staleAt(st) {
  if (st.lastSuccessAt === null || st.intervalAtCompletion === null) return null
  const window = 2 * Math.min(st.intervalAtCompletion, st.intervalSec)
  return st.lastSuccessAt + Math.max(window, STALE_FLOOR_SEC)
}

// No snapshot is not "stale" — it is a different panel state entirely
// (ViewModel's `starting`/`unconfigured`), and reporting stale here would grey
// a widget that has simply not finished its first batch yet.
function isStale(st, nowWall) {
  const at = staleAt(st)
  if (at === null) return false
  return nowWall >= at
}

// REQ-024a's resume rule: wall time advanced by more than one interval since
// the last completion means the schedule is overdue, whatever the monotonic
// clock says. This is the half of a suspend the monotonic axis cannot see.
function wallOverdue(st, nowWall) {
  if (st.lastCompletionAtWall === null) return false
  return (nowWall - st.lastCompletionAtWall) > st.intervalSec
}

// --- the monotonic axis (REQ-024) ------------------------------------------

// QML has one clock and this file needs two. No monotonic source is citable in
// `host-contract.md`, and R1a forbids using an uncited host API, so the
// monotonic axis is BUILT from the wall clock by accumulating only
// NON-NEGATIVE deltas.
//
// That gets the property REQ-024 actually needs: a backwards NTP correction can
// never push `nextAttemptAt` an hour into the future and stall polling. What it
// deliberately cannot do is tell a forward NTP step from a system suspend —
// both inflate it, and both then make the schedule due early. For a suspend
// that is the right answer, and REQ-024a's `wallOverdue` covers it explicitly
// on the other axis; for an NTP step the cost is one early poll.
//
// It lives here, as a reducer, rather than inside `Service.qml`, because it is
// the one piece of genuine logic the service would otherwise own — and a
// backwards clock is not a thing a live harness can arrange on demand.
function createClock(nowWall) {
  return { mono: 0, lastWall: typeof nowWall === "number" ? nowWall : 0 }
}

function advanceClock(state, nowWall) {
  if (typeof nowWall !== "number" || !isFinite(nowWall)) return state
  const delta = nowWall - state.lastWall
  return {
    mono: delta > 0 ? state.mono + delta : state.mono,
    lastWall: nowWall
  }
}

// --- wake selection (REQ-024) ----------------------------------------------

// One shot, earliest FUTURE deadline, rearmed after every firing.
//
// `staleAt` is a wall deadline and `nextAttemptAt` is a monotonic one, so they
// are never compared as instants — each is converted to a delay from its own
// axis first, and the delays are compared. Comparing the raw numbers would
// "work" whenever the two clocks happened to be close and fail silently after
// a suspend, which is precisely the case AC-041 exists to catch.
//
// Past deadlines are excluded. Including them would arm a zero-delay timer that
// fires, finds the deadline still past, and arms another — an immediate-fire
// loop that pins a CPU with no visible symptom other than a warm laptop.
function wake(st, input) {
  const now = input.now
  const nowWall = input.nowWall

  if (st.configured !== true) return { armed: false, delaySec: null, reason: null }

  let best = null
  let reason = null

  const at = staleAt(st)
  if (at !== null) {
    const delay = at - nowWall
    if (delay > 0) { best = delay; reason = "stale" }
  }

  // Suspended means no attempt deadline exists at all (REQ-024), but a stale
  // deadline may still be pending, so this narrows rather than returns.
  if (st.suspended !== true && st.nextAttemptAt !== null) {
    const delay = st.nextAttemptAt - now
    if (delay > 0 && (best === null || delay < best)) { best = delay; reason = "attempt" }
  }

  if (best === null) return { armed: false, delaySec: null, reason: null }
  return { armed: true, delaySec: best, reason: reason }
}

// What to do when the timer the previous `wake()` armed actually fires.
// `rearm` is the answer for the NEXT timer and is always present — REQ-024's
// "rearmed after every firing" is the fix for a bug where crossing staleAt at
// T+90 left the service with no timer for a nextAttemptAt at T+900, i.e. it
// stopped polling forever after going grey (AC-039).
function onWake(st, input) {
  const launched = tick(st, input)
  const next = launched.state
  return {
    state: next,
    launched: launched.launched,
    reason: launched.reason,
    stale: isStale(next, input.nowWall),
    rearm: wake(next, input)
  }
}

if (typeof module !== "undefined") module.exports = {
  INTERVAL_MIN_SEC: INTERVAL_MIN_SEC,
  INTERVAL_MAX_SEC: INTERVAL_MAX_SEC,
  INTERVAL_DEFAULT_SEC: INTERVAL_DEFAULT_SEC,
  STATES: STATES,
  RETRY_CLASSES: RETRY_CLASSES,
  TRANSIENT_CAP_SEC: TRANSIENT_CAP_SEC,
  FATAL_FLOOR_SEC: FATAL_FLOOR_SEC,
  STALE_FLOOR_SEC: STALE_FLOOR_SEC,
  RAMP_WINDOW_SEC: RAMP_WINDOW_SEC,
  RAMP_DELAY_SEC: RAMP_DELAY_SEC,
  RETRY_AFTER_MAX_SEC: RETRY_AFTER_MAX_SEC,
  JITTER_FRACTION: JITTER_FRACTION,
  ERROR_KINDS: ERROR_KINDS,
  KIND_RETRY_CLASS: KIND_RETRY_CLASS,
  TRANSIENT_HTTP_STATUSES: TRANSIENT_HTTP_STATUSES,
  SUSPENDING_KINDS: SUSPENDING_KINDS,
  retryClassFor: retryClassFor,
  isRetryable: isRetryable,
  parseHttpDate: parseHttpDate,
  normalizeRetryAfter: normalizeRetryAfter,
  backoffDelay: backoffDelay,
  create: create,
  setConfigured: setConfigured,
  changeInterval: changeInterval,
  tick: tick,
  requestManual: requestManual,
  takePendingManual: takePendingManual,
  onSuccess: onSuccess,
  onFailure: onFailure,
  createClock: createClock,
  advanceClock: advanceClock,
  staleAt: staleAt,
  isStale: isStale,
  wallOverdue: wallOverdue,
  wake: wake,
  onWake: onWake
}
