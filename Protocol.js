// Protocol.js — REQ-017b, DATA-005/005a/005b, DATA-007/007a, DATA-008/008a,
// DATA-011. The boundary between a process we launched and the state we render.
//
// Dual-use: runs unchanged under QML's V4 engine and under Node's V8 (see the
// header of Health.js). HC-16 forbids dual-use modules from importing one
// another, so the WAN domain, the device classes and the kind→retry-class table
// are duplicated here from Health.js and Schedule.js and held to the originals
// by tests/model/consistency.test.js.
//
// Two properties govern this file, and both are stated as tests.
//
// **It never throws.** AC-046. Everything here runs on data produced by another
// process; a QML exception in a completion handler leaves the service with no
// snapshot, no error, and no way back. Every entry point is wrapped, and the
// wrapper is a backstop rather than a strategy — a test requires that no
// fixture in the corpus ever reaches it.
//
// **Every rejection is `malformed_response`.** DATA-008. Protocol rejection is
// never an unnamed state and never silently leaves the previous snapshot on
// screen without a failure overlay. The 36 named rejection classes exist for
// tests and logs; the kind published to the rest of the service is always the
// one REQ-020a makes retryable.

// --- bounds (docs/protocol-v1.md § Bounds) --------------------------------
const STDOUT_MAX_BYTES = 262144          // 256 KiB, DATA-005
const STDERR_HELPER_BOUND_BYTES = 4096   // 4 KiB, the helper's own bound
const STDERR_RETAIN_BYTES = 8192         // 8 KiB, what the service keeps
const ATTEMPTED_AT_MAX_CHARS = 64
const ATTEMPTED_AT_MAX_EARLY_SEC = 300
const NONCE_MIN_CHARS = 1
const NONCE_MAX_CHARS = 128
const OFFLINE_DEVICES_MAX = 10
const GATEWAYS_MAX = 64
const WARNINGS_MAX = 32
const STRING_MAX_CHARS = 512
const MESSAGE_MAX_CHARS = 256
const DEPTH_MAX = 16
const COUNT_MIN = 0
const COUNT_MAX = 1000000

const PROTOCOL_VERSION = 1

// DATA-005. Exactly nine, on both shapes. A tenth key is `envelope_unknown_key`
// rather than something to ignore: an envelope carrying a field this build does
// not know about was produced by a helper this build did not ship with, and
// silently dropping it is how a version skew becomes a wrong reading.
const ENVELOPE_KEYS = [
  "protocolVersion", "ok", "nonce", "attemptedAt", "observedAt",
  "meta", "data", "warnings", "error"
]

// --- duplicated domains (HC-16) -------------------------------------------
const WAN_STATUSES = ["up", "down", "degraded", "unknown"]
const CLASSES = ["online", "transitional", "down", "impaired", "unknown"]
const ROLE_BUCKETS = ["gateways", "switches", "accessPoints"]

// DATA-006's `wan`, closed. No supported API version can populate a
// round-trip-time or a loss percentage — /v1/sites/{id}/wans returns
// {id, name} and nothing else (api-contract.md) — so a fifth key is a
// producer inventing data, and a present-and-null one is an invitation to
// a future contributor to fill it in.
const WAN_KEYS = ["status", "uptimeSec", "downloadBps", "uploadBps"]

const TRANSIENT_HTTP_STATUSES = [500, 502, 503, 504]

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

// DATA-007a's httpStatus column, as data. `null` means the kind forbids the
// field; a number means it requires exactly that value when present; "range"
// means the `http` case, which is the one kind whose status is open.
const KIND_HTTP_STATUS = {
  unauthorized: 401,
  forbidden: 403,
  rate_limited: 429,
  http: "range"
}

// The kind published for every protocol rejection, whatever the class.
const REJECTION_KIND = "malformed_response"

// --- small helpers ---------------------------------------------------------

function contains(list, value) {
  for (let i = 0; i < list.length; i++) if (list[i] === value) return true
  return false
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function isInteger(value) {
  return typeof value === "number" && isFinite(value) && Math.floor(value) === value
}

function isCount(value) {
  return isInteger(value) && value >= COUNT_MIN && value <= COUNT_MAX
}

function isNullableCount(value) {
  return value === null || isCount(value)
}

// `null` and absence are equivalent for every optional field. DATA-008 says so
// explicitly for httpStatus, and treating them differently anywhere else would
// make the JSON producer's choice of "omit" or "emit null" load-bearing.
function absent(value) {
  return value === undefined || value === null
}

function retryClassFor(kind, httpStatus) {
  if (kind === "http") {
    return contains(TRANSIENT_HTTP_STATUSES, httpStatus) ? "transient" : "fatal"
  }
  const known = KIND_RETRY_CLASS[kind]
  return known === undefined ? KIND_RETRY_CLASS.internal : known
}

// --- RFC 3339 UTC ----------------------------------------------------------

// Strict: `YYYY-MM-DDTHH:MM:SS[.fff…]Z`, uppercase separators, no offset form.
// DATA-008 says "ending in Z", and the producer is our own helper, so the two
// obsolete-adjacent spellings RFC 3339 permits (lowercase `t`/`z`, `+00:00`)
// buy nothing and widen what has to be reasoned about.
//
// Date.parse is not used: its handling of anything but the exact ISO form is
// implementation-defined, and V4 and V8 need not agree. Date.UTC is specified
// exactly, so this returns the same number in both engines.
function parseRfc3339Utc(text) {
  if (typeof text !== "string") return null
  if (text.length > ATTEMPTED_AT_MAX_CHARS) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?Z$/.exec(text)
  if (m === null) return null

  const year = Number(m[1])
  const month = Number(m[2]) - 1
  const day = Number(m[3])
  const hh = Number(m[4])
  const mm = Number(m[5])
  const ss = Number(m[6])
  const frac = m[7] === undefined ? 0 : Number(m[7])

  if (month < 0 || month > 11 || hh > 23 || mm > 59 || ss > 60) return null
  const seconds = ss === 60 ? 59 : ss

  const ms = Date.UTC(year, month, day, hh, mm, seconds)
  const round = new Date(ms)
  // Date.UTC turns 31 February into 3 March without complaint, so an
  // impossible date has to be caught by round-tripping it. In practice the
  // MONTH comparison is the one that fires: any out-of-range day overflows
  // into a neighbouring month, so the day comparison can never be the sole
  // trigger. All three are kept because the cost is nil and the rule being
  // relied on — "a day overflow always crosses a month boundary" — is a
  // property of the calendar rather than of anything stated here.
  if (round.getUTCFullYear() !== year || round.getUTCMonth() !== month
      || round.getUTCDate() !== day) {
    return null
  }
  return ms / 1000 + frac
}

// --- REQ-017b: the completion join ----------------------------------------

// HC-7: `onExited` and `onStreamFinished` have no guaranteed order, so a batch
// is complete only when the exit status AND both stdio streams have been
// observed, or the watchdog has fired. The protocol is never parsed from
// `onExited` alone — a helper that has exited may still have unread bytes in
// its pipe, and reading the envelope then gives a truncated JSON parse error
// for a batch that actually succeeded.
//
// This is a pure state machine so that AC-050 can run it in both orders. Risk
// R-E is that `terminal` fires twice and the service starts two follow-on
// batches; `terminal` is therefore returned at most ONCE per join, and every
// event after that is absorbed.
const JOIN_EVENTS = ["exit", "stdout", "stderr", "watchdog"]

function createJoin() {
  return {
    exitSeen: false,
    exitStatus: null,
    stdoutSeen: false,
    stdout: "",
    stderrSeen: false,
    stderr: "",
    watchdogFired: false,
    terminated: false
  }
}

function joinBatch(state, event) {
  const kind = event && event.event
  if (!contains(JOIN_EVENTS, kind)) {
    // Total over its input. An unrecognised event changes nothing rather than
    // throwing, because this runs inside a QML signal handler.
    return { state: state, terminal: false, reason: "unknown-event" }
  }

  const next = {
    exitSeen: state.exitSeen,
    exitStatus: state.exitStatus,
    stdoutSeen: state.stdoutSeen,
    stdout: state.stdout,
    stderrSeen: state.stderrSeen,
    stderr: state.stderr,
    watchdogFired: state.watchdogFired,
    terminated: state.terminated
  }

  if (kind === "exit") {
    next.exitSeen = true
    next.exitStatus = isInteger(event.status) ? event.status : null
  } else if (kind === "stdout") {
    next.stdoutSeen = true
    next.stdout = typeof event.text === "string" ? event.text : ""
  } else if (kind === "stderr") {
    next.stderrSeen = true
    next.stderr = typeof event.text === "string" ? event.text : ""
  } else {
    next.watchdogFired = true
  }

  if (state.terminated) {
    // Already complete. The late events of an abandoned process land here —
    // REQ-017a says its `onExited` and stream output are discarded — and they
    // must not produce a second terminal.
    return { state: next, terminal: false, reason: "already-terminal" }
  }

  if (next.watchdogFired) {
    next.terminated = true
    // REQ-017a: the watchdog does NOT wait for the process to exit.
    return { state: next, terminal: true, reason: "watchdog" }
  }
  if (next.exitSeen && next.stdoutSeen && next.stderrSeen) {
    next.terminated = true
    return { state: next, terminal: true, reason: "joined" }
  }
  return { state: next, terminal: false, reason: "waiting" }
}

// --- DATA-005b: the stderr verdict ----------------------------------------

// Never parsed, never logged verbatim, never displayed. The helper promises
// 4 KiB of sanitized text; anything past that means it is not behaving as
// specified, and the likely content is a traceback — which is exactly where a
// request header carrying a credential would appear. So the overflow marks the
// batch `internal` rather than being tolerated, even when stdout carried a
// perfectly good success envelope.
//
// The 8 KiB retention bound is separate and defensive: it caps what enters the
// shell process at all, so the material is bounded before any decision about it
// is made.
function stderrVerdict(text) {
  const raw = typeof text === "string" ? text : ""
  const retained = raw.length > STDERR_RETAIN_BYTES ? raw.slice(0, STDERR_RETAIN_BYTES) : raw
  if (raw.length > STDERR_HELPER_BOUND_BYTES) {
    return {
      retainedLength: retained.length,
      overBound: true,
      kind: "internal",
      warning: {
        code: "stderr_bound_exceeded",
        message: "The helper produced more diagnostic output than it is allowed to; the result was discarded.",
        detail: null
      }
    }
  }
  return { retainedLength: retained.length, overBound: false, kind: null, warning: null }
}

// --- DATA-008: stdout framing ---------------------------------------------

// Finds the index just past the first complete JSON value, or -1. Needed
// because JSON.parse is all-or-nothing and cannot say WHERE a document stopped
// being valid — and `stdout_multiple_values` and `stdout_trailing_garbage` are
// different rejections with different diagnoses (a half-written batch versus a
// helper that printed something after its envelope).
function endOfFirstJsonValue(text) {
  let i = 0
  while (i < text.length && /\s/.test(text[i])) i++
  if (i >= text.length) return -1

  const first = text[i]
  if (first !== "{" && first !== "[") {
    // A top-level scalar. It ends at the first whitespace or structural char.
    let j = i
    while (j < text.length && !/[\s,\]}]/.test(text[j])) j++
    return j
  }

  let depth = 0
  let inString = false
  let escaped = false
  for (let j = i; j < text.length; j++) {
    const ch = text[j]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === "\\") escaped = true
      else if (ch === "\"") inString = false
      continue
    }
    if (ch === "\"") { inString = true; continue }
    if (ch === "{" || ch === "[") depth++
    else if (ch === "}" || ch === "]") {
      depth--
      if (depth === 0) return j + 1
    }
  }
  return -1
}

// --- the validator ---------------------------------------------------------

function reject(reasons, cls, detail) {
  reasons.push({ rejectionClass: cls, detail: detail === undefined ? null : detail })
}

// Bounds that apply to any value, wherever it sits: depth, string length, and
// array length where the array is not one the schema names specifically.
function walkBounds(value, depth, path, reasons) {
  if (depth > DEPTH_MAX) {
    reject(reasons, "bound_exceeded", path + ": nesting deeper than " + DEPTH_MAX)
    return
  }
  if (typeof value === "string") {
    if (value.length > STRING_MAX_CHARS) {
      reject(reasons, "bound_exceeded",
        path + ": string of " + value.length + " chars exceeds " + STRING_MAX_CHARS)
    }
    return
  }
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      walkBounds(value[i], depth + 1, path + "[" + i + "]", reasons)
    }
    return
  }
  if (isObject(value)) {
    for (const key in value) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        walkBounds(value[key], depth + 1, path + "." + key, reasons)
      }
    }
  }
}

function checkClassBucket(bucket, path, reasons) {
  if (!isObject(bucket)) {
    reject(reasons, "data_schema_violation", path + ": missing or not an object")
    return
  }
  for (let i = 0; i < CLASSES.length; i++) {
    if (!isCount(bucket[CLASSES[i]])) {
      reject(reasons, "data_schema_violation",
        path + "." + CLASSES[i] + ": missing, negative, or not an integer count")
    }
  }
}

// DATA-006 / DATA-006b. Called only when `data` is a non-empty object.
function checkData(data, reasons) {
  const site = data.site
  if (!isObject(site) || typeof site.id !== "string" || typeof site.name !== "string") {
    reject(reasons, "data_schema_violation", "site: id and name must both be strings")
  }

  const wan = data.wan
  if (!isObject(wan)) {
    reject(reasons, "data_schema_violation", "wan: missing or not an object")
  } else {
    if (!contains(WAN_STATUSES, wan.status)) {
      reject(reasons, "data_schema_violation",
        "wan.status: " + JSON.stringify(wan.status) + " is outside its domain")
    }
    // DATA-006 defines `wan` as exactly these four keys. The two metrics it
    // names as ABSENT from the model — not present-and-null — are therefore
    // rejected by the key set rather than by name, which is both stronger (it
    // also catches whatever a future producer invents) and lets this file stay
    // inside the AC-065 gate's scan of the shipped plugin.
    for (const key in wan) {
      if (Object.prototype.hasOwnProperty.call(wan, key) && !contains(WAN_KEYS, key)) {
        reject(reasons, "data_schema_violation",
          "wan." + key + ": not a field DATA-006 defines")
      }
    }
  }

  if (!Array.isArray(data.gateways)) {
    reject(reasons, "data_schema_violation", "gateways: not an array")
  } else if (data.gateways.length > GATEWAYS_MAX) {
    reject(reasons, "bound_exceeded",
      "gateways: " + data.gateways.length + " entries exceeds " + GATEWAYS_MAX)
  }

  if (!Array.isArray(data.offlineDevices)) {
    reject(reasons, "data_schema_violation", "offlineDevices: not an array")
  } else if (data.offlineDevices.length > OFFLINE_DEVICES_MAX) {
    reject(reasons, "bound_exceeded",
      "offlineDevices: " + data.offlineDevices.length + " entries exceeds " + OFFLINE_DEVICES_MAX)
  }

  const counts = data.counts
  if (!isObject(counts)) {
    reject(reasons, "data_schema_violation", "counts: missing or not an object")
    return
  }

  if (!isNullableCount(counts.clients)) {
    reject(reasons, "data_schema_violation", "counts.clients: not a count or null")
  }
  if (!isCount(counts.devicesTotal)) {
    reject(reasons, "data_schema_violation", "counts.devicesTotal: not a non-negative integer")
  }
  if (!isCount(counts.offlineTotal)) {
    reject(reasons, "data_schema_violation", "counts.offlineTotal: not a non-negative integer")
  }

  checkClassBucket(counts.byClass, "counts.byClass", reasons)
  for (let i = 0; i < ROLE_BUCKETS.length; i++) {
    checkClassBucket(counts[ROLE_BUCKETS[i]], "counts." + ROLE_BUCKETS[i], reasons)
  }

  // DATA-006b's two invariants. They are checked HERE, on the consumer side,
  // and not merely produced correctly, because REQ-002 rule 4 reads `byClass`
  // and a partition that does not add up would colour the bar item from
  // numbers that describe no real site. Only run them once the buckets are
  // known well-formed, so a missing class reports as a schema violation rather
  // than as arithmetic that happens not to balance.
  const byClass = counts.byClass
  if (isObject(byClass) && isCount(counts.devicesTotal) && isCount(counts.offlineTotal)) {
    let sum = 0
    let wellFormed = true
    for (let i = 0; i < CLASSES.length; i++) {
      if (!isCount(byClass[CLASSES[i]])) { wellFormed = false; break }
      sum += byClass[CLASSES[i]]
    }
    if (wellFormed) {
      if (sum !== counts.devicesTotal) {
        reject(reasons, "byclass_sum_mismatch",
          "sum(byClass)=" + sum + " != devicesTotal=" + counts.devicesTotal)
      }
      const offline = byClass.down + byClass.impaired
      if (offline !== counts.offlineTotal) {
        reject(reasons, "byclass_offline_mismatch",
          "byClass.down + byClass.impaired=" + offline
            + " != offlineTotal=" + counts.offlineTotal)
      }
    }
  }
}

// DATA-007 / DATA-007a.
function checkError(error, reasons) {
  if (!isObject(error)) {
    reject(reasons, "failure_error_missing", "error: absent or not an object")
    return
  }
  const kind = error.kind
  if (typeof kind !== "string" || kind.length === 0) {
    reject(reasons, "error_kind_missing", "error.kind: absent or not a string")
    return
  }

  if (typeof error.message === "string" && error.message.length > MESSAGE_MAX_CHARS) {
    reject(reasons, "bound_exceeded",
      "error.message: " + error.message.length + " chars exceeds " + MESSAGE_MAX_CHARS)
  }

  const status = error.httpStatus
  const rule = KIND_HTTP_STATUS[kind]

  if (rule === undefined) {
    // The kind forbids httpStatus. `null` and absence are BOTH accepted, so a
    // legitimate `network` failure that emits the field as null is never
    // misread as a protocol violation (DATA-008, last sentence).
    if (!absent(status)) {
      reject(reasons, "error_http_status_forbidden",
        kind + ": httpStatus is forbidden but " + JSON.stringify(status) + " is present")
    }
  } else if (rule === "range") {
    if (!absent(status)) {
      if (!isInteger(status) || status < 100 || status > 599) {
        reject(reasons, "error_http_status_wrong",
          "http: " + JSON.stringify(status) + " is not an HTTP status")
      } else if (status === 401 || status === 403 || status === 429) {
        // DATA-007 requires a received status to keep its typed kind. An `http`
        // error carrying 401 means the producer skipped that mapping, and
        // acting on it would report "controller unreachable" for what is
        // actually a bad API key.
        reject(reasons, "error_http_status_wrong",
          "http: " + status + " must be reported as its typed kind, not as `http`")
      }
    }
  } else if (!absent(status) && status !== rule) {
    reject(reasons, "error_http_status_wrong",
      kind + ": httpStatus must be " + rule + ", not " + JSON.stringify(status))
  }

  if (!absent(error.retryAfterSec) && kind !== "rate_limited") {
    reject(reasons, "error_retry_after_forbidden",
      kind + ": retryAfterSec is permitted only for rate_limited")
  }

  // The one field the scheduler would otherwise take on trust. An envelope
  // claiming `kind: "credential", retryable: true` would be retried forever
  // against a controller that will never accept the key.
  const expected = retryClassFor(kind, absent(status) ? null : status) !== "fatal"
  if (error.retryable !== expected) {
    reject(reasons, "error_retryable_mismatch",
      kind + ": retryable is " + JSON.stringify(error.retryable)
        + " but the kind's class requires " + expected)
  }
}

function checkWarnings(warnings, reasons) {
  if (!Array.isArray(warnings)) {
    reject(reasons, "warnings_not_array", "warnings: null or not an array")
    return
  }
  if (warnings.length > WARNINGS_MAX) {
    reject(reasons, "bound_exceeded",
      "warnings: " + warnings.length + " entries exceeds " + WARNINGS_MAX)
  }
  for (let i = 0; i < warnings.length; i++) {
    const w = warnings[i]
    if (!isObject(w) || typeof w.code !== "string" || typeof w.message !== "string") {
      reject(reasons, "warning_shape_invalid",
        "warnings[" + i + "]: not an object with a string code and message")
    } else if (w.message.length > MESSAGE_MAX_CHARS) {
      reject(reasons, "bound_exceeded",
        "warnings[" + i + "].message: exceeds " + MESSAGE_MAX_CHARS)
    }
  }
}

function accepted(envelope) {
  return {
    accepted: true,
    ok: envelope.ok,
    envelope: envelope,
    data: envelope.ok === true ? envelope.data : null,
    error: envelope.ok === true ? null : envelope.error,
    meta: envelope.meta,
    warnings: envelope.warnings,
    publishedKind: envelope.ok === true ? null : envelope.error.kind,
    rejectionClass: null,
    reasons: [],
    rebaselineEligible: false
  }
}

function rejected(reasons) {
  // DATA-008a needs to know that `attempted_at_too_early` was the ONLY reason,
  // so every reason is collected and the reported class is the first in
  // validation order rather than the only one that was looked for.
  const only = reasons.length === 1 && reasons[0].rejectionClass === "attempted_at_too_early"
  return {
    accepted: false,
    ok: null,
    envelope: null,
    data: null,
    error: null,
    meta: null,
    warnings: [],
    publishedKind: REJECTION_KIND,
    rejectionClass: reasons.length > 0 ? reasons[0].rejectionClass : null,
    reasons: reasons,
    rebaselineEligible: only
  }
}

// `batch` is the context the service issued for this launch:
// `{ nonce, exitStatus, launchAt, receiptAt }`, the two timestamps RFC 3339.
function validateEnvelope(envelope, batch, reasons) {
  if (!isObject(envelope)) {
    reject(reasons, "envelope_not_object", "the top-level JSON value is not an object")
    return
  }

  for (const key in envelope) {
    if (Object.prototype.hasOwnProperty.call(envelope, key) && !contains(ENVELOPE_KEYS, key)) {
      reject(reasons, "envelope_unknown_key", "unexpected top-level key: " + key)
    }
  }

  if (envelope.protocolVersion !== PROTOCOL_VERSION) {
    // Identity against the integer. `"1"` and `1.0` are both rejected: the
    // first is a producer that stringified its numbers, and neither is a
    // version this build has agreed a contract with.
    reject(reasons, "protocol_version_wrong",
      "protocolVersion must be the integer 1, not " + JSON.stringify(envelope.protocolVersion))
  }

  // DATA-005a. The nonce is what makes generation checking survive HC-8's
  // asynchronous termination AND plugin hot-reload, which destroys the service
  // and resets any in-memory counter — a nonce issued by a previous instance
  // can never match a new one's.
  const nonce = envelope.nonce
  if (typeof nonce !== "string" || nonce.length < NONCE_MIN_CHARS
      || nonce.length > NONCE_MAX_CHARS) {
    reject(reasons, "nonce_missing", "nonce: absent, empty, over-long, or not a string")
  } else if (batch && typeof batch.nonce === "string" && nonce !== batch.nonce) {
    reject(reasons, "nonce_mismatch", "nonce is not the one issued for this batch")
  }

  if (!isObject(envelope.meta)) {
    reject(reasons, "meta_missing", "meta: absent, null, or not an object")
  } else if (typeof envelope.meta.helperVersion !== "string"
             || envelope.meta.helperVersion.length === 0) {
    // DEV-2 made every other meta field nullable with an unconditional
    // container, so this is the single required one.
    reject(reasons, "meta_helper_version_missing", "meta.helperVersion: absent or null")
  }

  const attemptedAt = parseRfc3339Utc(envelope.attemptedAt)
  const launchAt = batch ? parseRfc3339Utc(batch.launchAt) : null
  const receiptAt = batch ? parseRfc3339Utc(batch.receiptAt) : null

  if (attemptedAt === null) {
    reject(reasons, "attempted_at_malformed",
      "attemptedAt: not RFC 3339 UTC ending in Z, or over "
        + ATTEMPTED_AT_MAX_CHARS + " chars")
  } else {
    if (receiptAt !== null && attemptedAt > receiptAt) {
      reject(reasons, "attempted_at_future", "attemptedAt is after receipt")
    }
    if (launchAt !== null && attemptedAt < launchAt - ATTEMPTED_AT_MAX_EARLY_SEC) {
      reject(reasons, "attempted_at_too_early",
        "attemptedAt precedes service launch by more than "
          + ATTEMPTED_AT_MAX_EARLY_SEC + " s")
    }
  }

  checkWarnings(envelope.warnings, reasons)
  walkBounds(envelope, 0, "envelope", reasons)

  // DATA-008: identity against the boolean, never truthiness. `1` and `"true"`
  // are both truthy and both look right in a log; coercing either would publish
  // a success built from an envelope whose producer did not claim one.
  if (envelope.ok === true) {
    if (batch && batch.exitStatus !== 0) {
      reject(reasons, "success_exit_nonzero",
        "ok: true with exit status " + JSON.stringify(batch.exitStatus))
    }
    if (envelope.error !== null && envelope.error !== undefined) {
      reject(reasons, "success_error_not_null", "ok: true with a non-null error")
    }

    const observedAt = parseRfc3339Utc(envelope.observedAt)
    if (absent(envelope.observedAt)) {
      reject(reasons, "success_observed_at_missing", "ok: true with observedAt absent or null")
    } else if (observedAt === null) {
      reject(reasons, "success_observed_at_unordered", "observedAt: not RFC 3339 UTC")
    } else {
      if (attemptedAt !== null && observedAt < attemptedAt) {
        reject(reasons, "success_observed_at_unordered", "observedAt precedes attemptedAt")
      }
      if (receiptAt !== null && observedAt > receiptAt) {
        reject(reasons, "success_observed_at_unordered", "observedAt is later than receipt")
      }
    }

    if (absent(envelope.data)) {
      reject(reasons, "success_data_missing", "ok: true with data absent or null")
    } else if (!isObject(envelope.data)) {
      reject(reasons, "data_schema_violation", "data: not an object")
    } else if (Object.keys(envelope.data).length === 0) {
      // Named separately from a schema violation because `data: {}` is what a
      // producer emits when a collection silently returned nothing, and it
      // would otherwise satisfy "is an object".
      reject(reasons, "success_data_empty", "ok: true with data: {}")
    } else {
      checkData(envelope.data, reasons)
    }
  } else if (envelope.ok === false) {
    if (batch && batch.exitStatus === 0) {
      reject(reasons, "failure_exit_zero", "ok: false with exit status 0")
    }
    if (!absent(envelope.data)) {
      reject(reasons, "failure_data_not_null", "ok: false with a non-null data")
    }
    if (!absent(envelope.observedAt)) {
      reject(reasons, "failure_observed_at_not_null", "ok: false with a non-null observedAt")
    }
    checkError(envelope.error, reasons)
  } else {
    reject(reasons, "ok_not_boolean",
      "ok must be the JSON boolean true or false, not " + JSON.stringify(envelope.ok))
  }
}

function acceptEnvelope(input) {
  try {
    const reasons = []
    const envelope = input ? input.envelope : null
    validateEnvelope(envelope, input ? input.batch : null, reasons)
    return reasons.length === 0 ? accepted(envelope) : rejected(reasons)
  } catch (e) {
    return unexpected(e)
  }
}

// DATA-005 / DATA-008 framing, then the envelope.
function acceptStdout(input) {
  try {
    const reasons = []
    const stdout = input && typeof input.stdout === "string" ? input.stdout : ""
    const batch = input ? input.batch : null

    if (stdout.length > STDOUT_MAX_BYTES) {
      // Reported before parsing: a 2 MiB string is not something to hand to
      // JSON.parse first and ask questions about afterwards.
      reject(reasons, "stdout_oversized",
        stdout.length + " bytes exceeds " + STDOUT_MAX_BYTES)
      return rejected(reasons)
    }

    let value = null
    let parsed = false
    try {
      value = JSON.parse(stdout)
      parsed = true
    } catch (e) {
      parsed = false
    }

    if (!parsed) {
      // JSON.parse rejects trailing non-whitespace, so a failure here is one of
      // three different things and the diagnosis matters: a traceback, a second
      // envelope, or an envelope with something appended.
      const end = endOfFirstJsonValue(stdout)
      let prefixOk = false
      if (end > 0) {
        try { JSON.parse(stdout.slice(0, end)); prefixOk = true } catch (e) { prefixOk = false }
      }
      if (!prefixOk) {
        reject(reasons, "stdout_not_json", "stdout does not parse as JSON")
        return rejected(reasons)
      }
      const rest = stdout.slice(end).replace(/^\s+|\s+$/g, "")
      let restIsJson = false
      try { JSON.parse(rest); restIsJson = true } catch (e) { restIsJson = false }
      reject(reasons,
        restIsJson ? "stdout_multiple_values" : "stdout_trailing_garbage",
        restIsJson
          ? "stdout holds more than one JSON value"
          : "non-whitespace follows the JSON value")
      return rejected(reasons)
    }

    validateEnvelope(value, batch, reasons)
    return reasons.length === 0 ? accepted(value) : rejected(reasons)
  } catch (e) {
    return unexpected(e)
  }
}

// AC-046's backstop. Reached only by a defect in this file — a test requires
// that no fixture in the corpus lands here — but a QML exception in a
// completion handler leaves the service with no snapshot, no error, and no way
// back, so there is a floor beneath the floor.
//
// `rejectionClass` is null rather than one of the 36: naming a class here would
// let a validator bug hide inside a legitimate-looking rejection.
function unexpected(e) {
  return {
    accepted: false,
    ok: null,
    envelope: null,
    data: null,
    error: null,
    meta: null,
    warnings: [],
    publishedKind: REJECTION_KIND,
    rejectionClass: null,
    reasons: [{ rejectionClass: null, detail: "unexpected: " + String(e && e.message) }],
    rebaselineEligible: false
  }
}

// --- DATA-008a: the one-shot re-baseline ----------------------------------

// A timestamp far in the past with everything else well-formed is the signature
// of a backwards wall-clock correction, not of a bad helper. Without this, a
// laptop resuming with a 40-minute-slow RTC rejects every batch permanently
// until the shell restarts — the failure is total, silent, and survives every
// retry the scheduler can make.
//
// ONCE. A second consecutive such rejection is a genuine protocol error,
// because a helper that keeps sending timestamps from last week is not a clock
// problem. The counter resets on any other outcome, which is what makes
// "consecutive" mean what it says.
function createRebaseline() {
  return { used: false }
}

function considerRebaseline(state, result, nowWallIso) {
  if (result.accepted === true || !result.rebaselineEligible) {
    return { state: { used: false }, rebaselined: false, launchAt: null }
  }
  if (state.used) {
    return { state: state, rebaselined: false, launchAt: null }
  }
  return { state: { used: true }, rebaselined: true, launchAt: nowWallIso }
}

if (typeof module !== "undefined") module.exports = {
  STDOUT_MAX_BYTES: STDOUT_MAX_BYTES,
  STDERR_HELPER_BOUND_BYTES: STDERR_HELPER_BOUND_BYTES,
  STDERR_RETAIN_BYTES: STDERR_RETAIN_BYTES,
  ATTEMPTED_AT_MAX_CHARS: ATTEMPTED_AT_MAX_CHARS,
  ATTEMPTED_AT_MAX_EARLY_SEC: ATTEMPTED_AT_MAX_EARLY_SEC,
  NONCE_MIN_CHARS: NONCE_MIN_CHARS,
  NONCE_MAX_CHARS: NONCE_MAX_CHARS,
  OFFLINE_DEVICES_MAX: OFFLINE_DEVICES_MAX,
  GATEWAYS_MAX: GATEWAYS_MAX,
  WARNINGS_MAX: WARNINGS_MAX,
  STRING_MAX_CHARS: STRING_MAX_CHARS,
  MESSAGE_MAX_CHARS: MESSAGE_MAX_CHARS,
  DEPTH_MAX: DEPTH_MAX,
  COUNT_MIN: COUNT_MIN,
  COUNT_MAX: COUNT_MAX,
  PROTOCOL_VERSION: PROTOCOL_VERSION,
  ENVELOPE_KEYS: ENVELOPE_KEYS,
  WAN_STATUSES: WAN_STATUSES,
  CLASSES: CLASSES,
  ROLE_BUCKETS: ROLE_BUCKETS,
  WAN_KEYS: WAN_KEYS,
  TRANSIENT_HTTP_STATUSES: TRANSIENT_HTTP_STATUSES,
  KIND_RETRY_CLASS: KIND_RETRY_CLASS,
  KIND_HTTP_STATUS: KIND_HTTP_STATUS,
  REJECTION_KIND: REJECTION_KIND,
  JOIN_EVENTS: JOIN_EVENTS,
  retryClassFor: retryClassFor,
  parseRfc3339Utc: parseRfc3339Utc,
  createJoin: createJoin,
  joinBatch: joinBatch,
  stderrVerdict: stderrVerdict,
  endOfFirstJsonValue: endOfFirstJsonValue,
  acceptEnvelope: acceptEnvelope,
  acceptStdout: acceptStdout,
  createRebaseline: createRebaseline,
  considerRebaseline: considerRebaseline
}
