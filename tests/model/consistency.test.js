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
// this is where the copies are held to each other. If a fourth dual-use module
// appears, add it here first.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Health = require("../../Health.js")
const Settings = require("../../Settings.js")
const ViewModel = require("../../ViewModel.js")

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
  assert.ok(modules.length >= 3, "expected at least Health, Settings and ViewModel")
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
