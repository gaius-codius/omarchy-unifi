// The HC-16 mitigation.
//
// Dual-use modules cannot import one another: `.import` is a QML directive and
// a syntax error to Node, so the pure layer is several independent files and
// anything shared between them is DUPLICATED on purpose (AMD-1, AMD-5).
//
// Duplication that nothing checks is drift waiting to happen — and drift
// between two definitions of the same list is exactly the shape of DEV-1, where
// `counts` could not answer the question `healthLevel` asked of it.
//
// A TEST file has no such restriction: node can require all of them at once. So
// this is where the copies are held to each other. If a sixth dual-use module
// appears, add it here first.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Health = require("../../Health.js")
const Settings = require("../../Settings.js")
const ViewModel = require("../../ViewModel.js")
const Schedule = require("../../Schedule.js")
const Protocol = require("../../Protocol.js")

const REPO = path.resolve(__dirname, "../..")
const SPEC = fs.readFileSync(
  path.join(REPO, "docs/feature-specs/omarchy-unifi-plugin/SPEC.md"), "utf8")
const PROTOCOL = fs.readFileSync(path.join(REPO, "docs/protocol-v1.md"), "utf8")
const MANIFEST = JSON.parse(fs.readFileSync(path.join(REPO, "manifest.json"), "utf8"))

test("CONFIG_FAULT_KINDS is identical in Health.js and ViewModel.js", () => {
  // Health.js uses it to decide rule 1; ViewModel.js uses it to order the panel
  // state. If they diverge, a fault greys the bar item while the panel shows a
  // different state — or worse, the reverse.
  assert.deepStrictEqual(Health.CONFIG_FAULT_KINDS, ViewModel.CONFIG_FAULT_KINDS)
  for (const kind of Health.CONFIG_FAULT_KINDS) {
    assert.strictEqual(ViewModel.isConfigFaultKind(kind), true, kind)
    assert.strictEqual(Health.isConfigFault(kind), true, kind)
  }
  for (const kind of ["network", "timeout", "internal", "tls"]) {
    assert.strictEqual(ViewModel.isConfigFaultKind(kind), false, kind)
    assert.strictEqual(Health.isConfigFault(kind), false, kind)
  }
})

test("every module's view of the five REQ-000 classes agrees with the spec", () => {
  const table = /\| Class \| `state` values \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(SPEC)
  assert.ok(table, "SPEC.md: could not locate the REQ-000 class table")
  const specClasses = table[1].split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|")[1].trim().replace(/`/g, ""))
  assert.deepStrictEqual(Health.CLASSES, specClasses)

  // Same five, same order, in the protocol contract the helper implements.
  const protocolTable = /\| Class \| `state` values \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(PROTOCOL)
  assert.ok(protocolTable, "protocol-v1.md: could not locate the class table")
  const protocolClasses = protocolTable[1].split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|")[1].trim().replace(/`/g, ""))
  assert.deepStrictEqual(protocolClasses, specClasses,
    "SPEC.md and protocol-v1.md disagree about the classes the helper must emit")
})

test("the state-to-class map agrees with protocol-v1.md, both ways", () => {
  // Both directions, because a one-way check misses the failure that matters:
  // a state present in the contract but absent from the map falls into
  // `unknown` and quietly ambers a healthy site.
  const section = /## DATA-009 invariants/.test(PROTOCOL)
  assert.ok(section, "protocol-v1.md: sanity check on the document read")

  const rows = /\| `online` \| `ONLINE` \|\n((?:\|.*\n)+)/.exec(PROTOCOL)
  assert.ok(rows, "protocol-v1.md: could not locate the class rows")

  const fromContract = {}
  const classTable = /\| Class \| `state` values \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(PROTOCOL)[1]
  for (const line of classTable.split("\n").filter((l) => l.startsWith("|"))) {
    const cells = line.split("|")
    const klass = cells[1].trim().replace(/`/g, "")
    if (klass === "unknown") continue          // "any other string", not a list
    for (const match of cells[2].matchAll(/`([A-Z0-9_]+)`/g)) {
      fromContract[match[1]] = klass
    }
  }

  assert.deepStrictEqual(Health.STATE_TO_CLASS, fromContract)
  assert.strictEqual(Object.keys(fromContract).length, 10,
    "the API publishes exactly ten device states")
})

test("the WAN status domain agrees between Health.js and the spec", () => {
  // The list is wrapped across a line break in SPEC.md, so the separator before
  // "derived" is a comma followed by a NEWLINE, not a space.
  const stated = /`wan\.status` has the domain ((?:`[a-z]+`,?\s*)+)derived/.exec(SPEC)
  assert.ok(stated, "SPEC.md: could not locate the wan.status domain")
  const domain = [...stated[1].matchAll(/`([a-z]+)`/g)].map((m) => m[1])
  assert.deepStrictEqual(Health.WAN_STATUSES.slice().sort(), domain.slice().sort())
})

test("compactMetric is defined once, and the manifest agrees", () => {
  // AC-065. The enum lives in Settings.js alone — ViewModel.js deliberately does
  // NOT carry a copy, because a copy is a thing that can drift and QML can
  // import both files anyway.
  assert.deepStrictEqual(Settings.COMPACT_METRICS,
    MANIFEST.barWidget.schema.compactMetric.enum)
  assert.ok(Settings.COMPACT_METRICS.indexOf(Settings.DEFAULTS.compactMetric) !== -1)

  const source = fs.readFileSync(path.join(REPO, "ViewModel.js"), "utf8")
  assert.ok(source.indexOf("COMPACT_METRICS") === -1,
    "ViewModel.js must not carry a second copy of the compactMetric enum")
})

test("every health level Health.js can return has a ViewModel rendering", () => {
  // Neither direction may be short: a level with no rendering paints nothing,
  // and a rendering with no level is dead code that outlives the reason for it.
  assert.deepStrictEqual(Health.LEVELS.slice().sort(),
    Object.keys(ViewModel.LEVEL_RENDERING).sort())
  assert.deepStrictEqual(Health.LEVELS.slice().sort(),
    Object.keys(ViewModel.LEVEL_WORD).sort())
})

test("the dual-export tail is present in every root module", () => {
  // js_dialect.sh gates this, and it is asserted again here because the failure
  // is invisible: without the tail, `node --test` imports an empty object and
  // EVERY assertion in this directory passes while testing nothing. It is the
  // largest silent failure available in this plan, so it gets two checks.
  const modules = fs.readdirSync(REPO).filter((f) => f.endsWith(".js"))
  assert.ok(modules.length >= 5,
    "expected at least Health, Settings, ViewModel, Schedule and Protocol")
  for (const name of modules) {
    const module = require(path.join(REPO, name))
    assert.ok(module && typeof module === "object", name + ": exported nothing")
    assert.ok(Object.keys(module).length > 0,
      name + ": exported an EMPTY object — every test against it is vacuous")
  }
})

test("no dual-use module imports another (HC-16)", () => {
  for (const name of fs.readdirSync(REPO).filter((f) => f.endsWith(".js"))) {
    const source = fs.readFileSync(path.join(REPO, name), "utf8")
    // `.import` and `.pragma` are QML directives and are syntax errors to Node;
    // `require` would work under Node and fail under QML. Either way the module
    // stops being dual-use, which is the property the whole AUTO layer rests on.
    assert.ok(!/^\s*\.(import|pragma)\b/m.test(source), name + ": QML-only directive")
    assert.ok(!/(^|[^.\w])require\s*\(/.test(source), name + ": require() is not dual-use")
  }
})

// --- Schedule.js (Phase 3) ------------------------------------------------

test("Schedule.js's interval bounds are the ones Settings.js validates against", () => {
  // Settings.js owns the user-facing clamp; Schedule.js re-clamps because a
  // caller can reach it without going through Settings. Two copies of a bound
  // is exactly the drift this file exists to catch — a Schedule that allowed
  // 5 s would poll five times faster than any setting the user can express.
  assert.strictEqual(Schedule.INTERVAL_MIN_SEC, Settings.BOUNDS.refreshIntervalSec.min)
  assert.strictEqual(Schedule.INTERVAL_MAX_SEC, Settings.BOUNDS.refreshIntervalSec.max)
  assert.strictEqual(Schedule.INTERVAL_DEFAULT_SEC, Settings.DEFAULTS.refreshIntervalSec)
})

test("the retry class of every kind agrees with the DATA-007a matrix, both ways", () => {
  // protocol-v1.md's matrix is what Protocol.js (Phase 4) validates an
  // envelope's `retryable` against, and what Schedule.js schedules from. If the
  // two disagree, an envelope the helper considers retryable is scheduled as
  // fatal — the controller recovers and the widget never notices.
  const rows = /\| kind \| httpStatus \| retryAfterSec \| retryable \| retry class \|\n\|[-| ]+\|\n((?:\|.*\n)+)/
    .exec(PROTOCOL)
  assert.ok(rows, "protocol-v1.md: could not locate the DATA-007a matrix")

  const seen = []
  for (const line of rows[1].split("\n")) {
    if (!line.startsWith("|")) continue
    const cells = line.split("|").map((c) => c.trim())
    const kind = cells[1].replace(/`/g, "")
    const retryable = cells[4].replace(/`/g, "")
    const cls = cells[5]
    seen.push(kind)

    if (kind === "http") {
      // The one status-dependent kind; its own sub-table is checked below.
      assert.strictEqual(cls, "see below",
        "the matrix stopped deferring `http` to its sub-table")
      continue
    }
    assert.strictEqual(Schedule.retryClassFor(kind, null), cls,
      kind + ": Schedule.js and protocol-v1.md disagree on the retry class")
    assert.strictEqual(String(Schedule.isRetryable(kind, null)), retryable,
      kind + ": `retryable` disagrees with the class")
  }

  assert.deepStrictEqual(seen.slice().sort(), Schedule.ERROR_KINDS.slice().sort(),
    "the matrix and Schedule.ERROR_KINDS cover different kinds")
})

test("the `http` sub-table's transient statuses are the ones Schedule.js retries", () => {
  const sub = /\| `httpStatus` \| `retryable` \| class \|\n\|[-| ]+\|\n\| ([^|]+) \| `true` \| transient \|/
    .exec(PROTOCOL)
  assert.ok(sub, "protocol-v1.md: could not locate the `http` sub-table")
  const statuses = sub[1].match(/\d{3}/g).map(Number)

  assert.deepStrictEqual(statuses.slice().sort(), Schedule.TRANSIENT_HTTP_STATUSES.slice().sort())
  for (const status of statuses) {
    assert.strictEqual(Schedule.retryClassFor("http", status), "transient", String(status))
  }
  // "anything else" is fatal — including the three statuses the matrix excludes
  // from `http` entirely, which must never be treated as transient here.
  for (const status of [200, 400, 401, 403, 404, 429, 501, 505, 599]) {
    assert.strictEqual(Schedule.retryClassFor("http", status), "fatal", String(status))
  }
})

test("every DATA-007 kind Schedule.js schedules has a REQ-013 panel state", () => {
  // The two enumerations are duplicated across ViewModel.js and Schedule.js.
  // A kind the scheduler knows but the panel does not would back off correctly
  // while showing the user the fallback sentence for `internal`.
  for (const kind of Schedule.ERROR_KINDS) {
    assert.ok(ViewModel.PANEL_STATES.indexOf(kind) !== -1,
      kind + ": scheduled but has no panel state")
  }
})

test("the suspending kinds are a subset of the fatal kinds", () => {
  // Suspension is strictly stronger than fatal: it removes the deadline
  // entirely. A suspending kind that classified as retryable would suspend
  // polling and then never resume it, with no error the user could act on.
  for (const kind of Schedule.SUSPENDING_KINDS) {
    assert.strictEqual(Schedule.retryClassFor(kind, null), "fatal", kind)
    assert.ok(Health.CONFIG_FAULT_KINDS.indexOf(kind) !== -1,
      kind + ": suspends polling but is not a REQ-002 rule 1 configuration fault")
  }
})

test("the warning codes Schedule.js emits are in protocol-v1.md's closed enumeration", () => {
  // AMD-8 made `warnings` a closed enumeration — 14 values then, 18 since
  // SPEC-v1.1-browse.md. A code invented here
  // would be rejected by the same validator that accepts the helper's.
  for (const code of ["retry_after_clamped", "retry_after_ignored"]) {
    assert.ok(PROTOCOL.indexOf("| `" + code + "` |") !== -1,
      code + ": not in the protocol-v1.md warning table")
  }
})

// --- Protocol.js (Phase 4) ------------------------------------------------

test("Protocol.js's copies of the REQ-000 domains match Health.js's", () => {
  // Protocol.js validates `wan.status` and the class buckets; Health.js decides
  // colour from them. A domain that drifted would either reject a snapshot
  // Health can read, or admit one it cannot.
  assert.deepStrictEqual(Protocol.WAN_STATUSES, Health.WAN_STATUSES)
  assert.deepStrictEqual(Protocol.CLASSES, Health.CLASSES)
})

test("Protocol.js and Schedule.js agree on the retry class of every kind", () => {
  // DATA-007a makes the envelope's `retryable` a claim the consumer CHECKS, and
  // Schedule.js is what acts on the kind afterwards. If the two tables drifted,
  // Protocol would accept an envelope whose `retryable` Schedule then
  // contradicts — the validator would be certifying the opposite of what the
  // scheduler does.
  assert.deepStrictEqual(Protocol.KIND_RETRY_CLASS, Schedule.KIND_RETRY_CLASS)
  assert.deepStrictEqual(Protocol.TRANSIENT_HTTP_STATUSES, Schedule.TRANSIENT_HTTP_STATUSES)

  for (const kind of Schedule.ERROR_KINDS) {
    for (const status of [null, 401, 403, 429, 404, 500, 502, 503, 504, 418]) {
      assert.strictEqual(Protocol.retryClassFor(kind, status),
        Schedule.retryClassFor(kind, status), kind + " / " + status)
    }
  }
})

test("the nine envelope keys match protocol-v1.md's key table", () => {
  const table = /\| Key \| Type \| Notes \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(PROTOCOL)
  assert.ok(table, "protocol-v1.md: could not locate the envelope key table")
  const keys = table[1].split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|")[1].trim().replace(/`/g, ""))

  assert.strictEqual(keys.length, 9, "DATA-005 says nine top-level keys")
  assert.deepStrictEqual(Protocol.ENVELOPE_KEYS.slice().sort(), keys.slice().sort())
})

test("every bound in Protocol.js is the number protocol-v1.md states", () => {
  // The bounds are enforced on BOTH sides (the helper in Python, the service
  // here), so the document is the shared source and neither implementation is
  // free to drift from it quietly.
  const BOUNDS = {
    "stdout total": Protocol.STDOUT_MAX_BYTES / 1024,
    "helper's own stderr": Protocol.STDERR_HELPER_BOUND_BYTES / 1024,
    "stderr retained by the service": Protocol.STDERR_RETAIN_BYTES / 1024
  }
  for (const label of Object.keys(BOUNDS)) {
    const row = new RegExp("\\| " + label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      + " \\| (\\d+) KiB \\|").exec(PROTOCOL)
    assert.ok(row, "protocol-v1.md: no bound row for " + label)
    assert.strictEqual(BOUNDS[label], Number(row[1]), label)
  }

  const named = {
    "`attemptedAt` string length": [Protocol.ATTEMPTED_AT_MAX_CHARS, "chars"],
    "`attemptedAt` earliest, before service launch": [Protocol.ATTEMPTED_AT_MAX_EARLY_SEC, "s"],
    "`offlineDevices` entries": [Protocol.OFFLINE_DEVICES_MAX, ""],
    "`gateways` array entries": [Protocol.GATEWAYS_MAX, ""],
    "`warnings` entries": [Protocol.WARNINGS_MAX, ""],
    "any string value": [Protocol.STRING_MAX_CHARS, "chars"],
    "`message` fields specifically": [Protocol.MESSAGE_MAX_CHARS, "chars"],
    "JSON nesting depth": [Protocol.DEPTH_MAX, ""],
    // SPEC-v1.1-browse.md. Four copies of each of these now exist —
    // protocol-v1.md, Protocol.js, helper/unifi/bounds.py and
    // helper/unifi/normalize.py — because HC-16 forbids the dual-use modules
    // importing anything and the helper is a different language. The document
    // is the shared source; this is what stops the copies drifting.
    "`devices` array entries": [Protocol.DEVICES_LISTED_MAX, ""],
    "`clients` array entries": [Protocol.CLIENTS_LISTED_MAX, ""],
    "`detail.ports` entries per device": [Protocol.PORTS_PER_DEVICE_MAX, ""],
    "`detail.radios` entries per device": [Protocol.RADIOS_PER_DEVICE_MAX, ""]
  }
  for (const label of Object.keys(named)) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const row = new RegExp("\\| " + escaped + " \\| ([\\d ]+)").exec(PROTOCOL)
    assert.ok(row, "protocol-v1.md: no bound row for " + label)
    assert.strictEqual(named[label][0], Number(row[1].replace(/\s/g, "")), label)
  }
})

test("every rejection class Protocol.js can produce is one protocol-v1.md defines", () => {
  const table = /\| id \| Rejected when \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(PROTOCOL)
  assert.ok(table, "protocol-v1.md: could not locate the DATA-008 rejection table")
  const documented = table[1].split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|")[1].trim().replace(/`/g, ""))

  // 37 since SPEC-v1.1-browse.md added `list_exceeds_total`. The literal is
  // here rather than derived so that adding a class to the document without a
  // fixture, or a fixture without a document row, fails this build.
  assert.strictEqual(documented.length, 37, "DATA-008 defines 37 rejection classes")

  // Every class the SOURCE can emit, scraped from the calls themselves rather
  // than from a list this file also maintains — a second list would just be
  // one more copy to drift.
  const source = fs.readFileSync(path.join(REPO, "Protocol.js"), "utf8")
  const emitted = new Set()
  const call = /reject\(reasons, "([a-z_]+)"/g
  let m
  while ((m = call.exec(source)) !== null) emitted.add(m[1])

  for (const cls of emitted) {
    assert.ok(documented.indexOf(cls) !== -1,
      cls + ": emitted by Protocol.js but not defined in DATA-008")
  }
  assert.ok(emitted.size >= 30,
    "only " + emitted.size + " classes are reachable in the source; the scrape is broken")
})

test("the DATA-007a httpStatus column matches Protocol.js's rule table", () => {
  const rows = /\| kind \| httpStatus \| retryAfterSec \| retryable \| retry class \|\n\|[-| ]+\|\n((?:\|.*\n)+)/
    .exec(PROTOCOL)
  assert.ok(rows, "protocol-v1.md: could not locate the DATA-007a matrix")

  for (const line of rows[1].split("\n")) {
    if (!line.startsWith("|")) continue
    const cells = line.split("|").map((c) => c.trim())
    const kind = cells[1].replace(/`/g, "")
    const statusCell = cells[2]
    const rule = Protocol.KIND_HTTP_STATUS[kind]

    if (statusCell === "forbidden") {
      assert.strictEqual(rule, undefined, kind + ": document forbids httpStatus, code does not")
    } else if (kind === "http") {
      assert.strictEqual(rule, "range", "http is the one open-status kind")
    } else {
      const want = Number(/`(\d{3})`/.exec(statusCell)[1])
      assert.strictEqual(rule, want, kind + ": required httpStatus")
    }
  }
})

// --- the cross-LANGUAGE seam (Phase 6) -------------------------------------
//
// Everything above holds two JavaScript modules to each other. The helper is
// Python, so its copies of the same numbers and the same tables cannot be
// required — they are read as source text. That is cruder, and it is still the
// only thing standing between a bound the service enforces at 8 KiB and a
// helper that emits 16, which would look exactly like a working plugin until
// the day a controller returned enough data.

const HELPER = (name) =>
  fs.readFileSync(path.join(REPO, "helper/unifi", name), "utf8")

function pyConst(source, name) {
  const match = new RegExp("^" + name + " = ([^#\\n]+)", "m").exec(source)
  assert.ok(match, "helper: no constant named " + name)
  // Only the arithmetic the constants actually use: integers, and products of
  // integers. Deliberately not eval().
  const text = match[1].trim()
  const product = /^(\d+)(?:\s*\*\s*(\d+))?(?:\s*\*\s*(\d+))?$/.exec(text)
  assert.ok(product, "helper: " + name + " is not a plain numeric constant: " + text)
  return product.slice(1).filter(Boolean).map(Number).reduce((a, b) => a * b, 1)
}

test("the helper's pagination bounds are the numbers protocol-v1.md states", () => {
  const source = HELPER("pagination.py")
  const rows = {
    "requested `limit`": ["PAGE_LIMIT", 1],
    "pages per collection": ["MAX_PAGES", 1],
    "decoded bytes per collection": ["MAX_DECODED_BYTES", 1024 * 1024],
    "decoded bytes per batch": ["MAX_BATCH_DECODED_BYTES", 1024 * 1024]
  }
  for (const label of Object.keys(rows)) {
    const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    const row = new RegExp("\\| " + escaped + " \\| ([\\d ]+)").exec(PROTOCOL)
    assert.ok(row, "protocol-v1.md: no pagination bound row for " + label)
    const documented = Number(row[1].replace(/\s/g, "")) * rows[label][1]
    assert.strictEqual(pyConst(source, rows[label][0]), documented, label)
  }
  // The row reads "0 <en dash> 1 000 000", so the cell is taken whole and the
  // last run of digits is the ceiling. Splitting on the dash would depend on
  // which dash the document happens to use.
  const totals = /\| accepted `totalCount` \| ([^|]+)\|/.exec(PROTOCOL)
  assert.ok(totals, "protocol-v1.md: no accepted totalCount row")
  const ceiling = totals[1].replace(/\*\*\[chosen\]\*\*/, "").match(/[\d ]+$/)
  assert.ok(ceiling, "protocol-v1.md: unreadable totalCount ceiling")
  assert.strictEqual(pyConst(source, "TOTAL_COUNT_MAX"),
    Number(ceiling[0].replace(/\s/g, "")))
})

test("the helper and Schedule.js agree on the Retry-After ceiling", () => {
  // The helper normalizes the header; the scheduler normalizes what the helper
  // reports. Two ceilings would mean a value one side clamped and the other
  // accepted, and the warning would name a number nothing enforced.
  assert.strictEqual(pyConst(HELPER("transport.py"), "RETRY_AFTER_MAX_SEC"),
    Schedule.RETRY_AFTER_MAX_SEC)
})

test("the helper's DATA-007 kind list is the same nineteen Protocol.js knows", () => {
  const source = HELPER("errors.py")
  const block = /^KINDS = \(([\s\S]*?)\n\)/m.exec(source)
  assert.ok(block, "errors.py: no KINDS tuple")
  const names = block[1].match(/KIND_[A-Z_]+/g) || []
  const kinds = names.map((name) => {
    const value = new RegExp("^" + name + ' = "([a-z_]+)"', "m").exec(source)
    assert.ok(value, "errors.py: " + name + " has no string value")
    return value[1]
  })
  assert.strictEqual(kinds.length, 19)
  assert.deepStrictEqual([...kinds].sort(), [...Schedule.ERROR_KINDS].sort())
})

test("the helper's retry classes are the ones Schedule.js schedules by", () => {
  // The producer sets `error.retryable` from its table; the consumer rejects an
  // envelope whose `retryable` disagrees with its own (DATA-007a). If the two
  // tables differ, every failure of that kind is rejected as malformed and the
  // real reason never reaches the panel.
  const source = HELPER("errors.py")
  const block = /^RETRY_CLASS = \{([\s\S]*?)\n\}/m.exec(source)
  assert.ok(block, "errors.py: no RETRY_CLASS map")
  const pairs = [...block[1].matchAll(/KIND_([A-Z_]+): CLASS_([A-Z]+)/g)]
  assert.strictEqual(pairs.length, 18, "http is status-dependent and must be absent")
  for (const [, kindName, className] of pairs) {
    const kind = new RegExp("^KIND_" + kindName + ' = "([a-z_]+)"', "m").exec(source)[1]
    assert.strictEqual(Schedule.retryClassFor(kind), className.toLowerCase(), kind)
  }
  const transient = /^TRANSIENT_HTTP_STATUSES = \(([\d, ]+)\)/m.exec(source)
  assert.ok(transient, "errors.py: no TRANSIENT_HTTP_STATUSES")
  assert.deepStrictEqual(transient[1].split(",").map((s) => Number(s.trim())),
    Schedule.TRANSIENT_HTTP_STATUSES)
})

test("the helper emits only warning codes protocol-v1.md enumerates", () => {
  const source = HELPER("warn.py")
  const block = /^CODES = \(([\s\S]*?)\n\)/m.exec(source)
  assert.ok(block, "warn.py: no CODES tuple")
  const codes = (block[1].match(/"([a-z_]+)"/g) || []).map((q) => q.slice(1, -1))
  const table = /\| `code` \| Raised when \| `detail` \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(PROTOCOL)
  assert.ok(table, "protocol-v1.md: could not locate the warning code table")
  const documented = table[1].split("\n")
    .filter((line) => line.startsWith("|"))
    .map((line) => line.split("|")[1].trim().replace(/`/g, ""))
  assert.deepStrictEqual([...codes].sort(), [...documented].sort())
})

test("the helper's route allowlist is the six in api-contract.md", () => {
  const contract = fs.readFileSync(
    path.join(REPO, "docs/feature-specs/omarchy-unifi-plugin/api-contract.md"), "utf8")
  const table = /\| # \| Route \| Purpose \| Paginated \|\n\|[-| ]+\|\n((?:\|.*\n)+)/.exec(contract)
  assert.ok(table, "api-contract.md: could not locate the route table")
  const rows = table[1].split("\n").filter((line) => line.startsWith("|"))
  assert.strictEqual(rows.length, 6, "the allowlist is six routes")

  const source = HELPER("routes.py")
  for (const row of rows) {
    const cells = row.split("|").map((cell) => cell.trim())
    const route = cells[2].replace(/`/g, "")
    // "/v1/sites/{siteId}/devices" -> the template routes.py stores.
    const template = route.replace(/^\/v1\//, "")
    assert.ok(source.includes('"template": "' + template + '"'),
      "routes.py has no template for " + route)
    const paginated = cells[4].toLowerCase() === "yes"
    const spec = new RegExp('"template": "' + template.replace(/[{}]/g, "\\$&")
      + '",\\s*\\n\\s*"params": \\([^)]*\\),\\s*\\n\\s*"paginated": (True|False)')
      .exec(source)
    assert.ok(spec, "routes.py: cannot read the paginated flag for " + route)
    assert.strictEqual(spec[1] === "True", paginated, route + " pagination")
  }
})
