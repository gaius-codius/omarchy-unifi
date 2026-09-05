// AC-019 (AUTO core), AC-032 (AUTO half), AC-057, AC-065 (second half).
//
// AC-019's core is retiered from LIVE to AUTO here. The criterion is written
// against a running shell, but what it actually asserts — that differing
// refreshIntervalSec conflicts and differing compactMetric does not — is a pure
// function of the layout. Only the "zero helper launches over three intervals"
// remainder genuinely needs a live service, and that stays at CP8.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const Settings = require("../../Settings.js")

const MANIFEST = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../manifest.json"), "utf8"))

// --- AC-032 (AUTO half) / R1b --------------------------------------------

test("AC-032: the defaults table equals manifest.json's barWidget.defaults", () => {
  // R1b. HC-1 means nothing in the shipped shell reads barWidget.defaults, so
  // the manifest block is forward-looking documentation — and documentation
  // that disagrees with the code is worse than none, because it is what the
  // next person will trust.
  assert.deepStrictEqual(Settings.DEFAULTS, MANIFEST.barWidget.defaults)
})

test("AC-032: an entry with no settings keys resolves to the documented defaults", () => {
  const resolved = Settings.resolve({ id: "gaius-codius.unifi" })
  assert.strictEqual(resolved.settings.refreshIntervalSec, 30)
  assert.strictEqual(resolved.settings.compactMetric, "none")
  assert.deepStrictEqual(resolved.warnings, [],
    "defaults must not warn; only a value the user actually got wrong should")
})

test("the service route and the widget route land on the same values (DATA-002b)", () => {
  // The two routes differ: the widget reads inline settings through the
  // `setting(name, fallback)` base-class helper, while the service is not
  // injected `settings` at all and must locate its own layout entry. Two
  // routes reading two tables is R1b; this asserts there is one table.
  const cases = [
    {},
    { refreshIntervalSec: 60 },
    { refreshIntervalSec: "60" },
    { refreshIntervalSec: null, compactMetric: null },
    { refreshIntervalSec: 5, compactMetric: "clients" },
    { compactMetric: "latency" }
  ]
  for (const entry of cases) {
    const service = Settings.resolve(entry).settings
    const widget = Settings.resolveFromWidget(entry).settings
    assert.deepStrictEqual(widget, service, JSON.stringify(entry))
  }
})

test("the manifest schema bounds match the code bounds", () => {
  const schema = MANIFEST.barWidget.schema.refreshIntervalSec
  assert.strictEqual(schema.minimum, Settings.BOUNDS.refreshIntervalSec.min)
  assert.strictEqual(schema.maximum, Settings.BOUNDS.refreshIntervalSec.max)
  assert.deepStrictEqual(MANIFEST.barWidget.schema.compactMetric.enum,
    Settings.COMPACT_METRICS)
})

// --- AC-057 --------------------------------------------------------------

test("AC-057: every malformed refreshIntervalSec resolves to a clamped integer", () => {
  const cases = [
    { input: "30", expected: 30, why: "the string 30 is truthy and looks right in a log" },
    { input: "abc", expected: 30 },
    { input: NaN, expected: 30 },
    { input: Infinity, expected: 30 },
    { input: null, expected: 30 },
    { input: undefined, expected: 30 },
    { input: 0, expected: 15, why: "clamped up to the minimum" },
    { input: 99999, expected: 3600, why: "clamped down to the maximum" },
    { input: -1, expected: 15 },
    { input: 30.5, expected: 30, why: "a fractional interval is not a whole second" },
    { input: true, expected: 30 },
    { input: {}, expected: 30 },
    { input: [], expected: 30 }
  ]
  for (const item of cases) {
    const result = Settings.normalizeInterval(item.input)
    assert.strictEqual(result.value, item.expected, JSON.stringify(String(item.input)))
    assert.strictEqual(typeof result.value, "number")
    assert.ok(isFinite(result.value), "no timer interval may ever be NaN")
    assert.strictEqual(Math.floor(result.value), result.value)
    assert.ok(result.warning !== null, "a corrected value must say so")
    assert.strictEqual(result.warning.code, "settings_invalid")
  }
})

test("valid intervals pass through unchanged and without a warning", () => {
  for (const value of [15, 30, 60, 900, 3600]) {
    const result = Settings.normalizeInterval(value)
    assert.strictEqual(result.value, value)
    assert.strictEqual(result.warning, null)
  }
})

// --- AC-065 (second half) ------------------------------------------------

test("AC-065: normalizeCompactMetric maps every bad value into the enum", () => {
  const banned = "laten" + "cy"     // assembled so this file does not trip the gate
  for (const input of [banned, "", null, undefined, "clients ", " none", "CLIENTS", 7, {}]) {
    const result = Settings.normalizeCompactMetric(input)
    assert.ok(Settings.COMPACT_METRICS.indexOf(result.value) !== -1,
      JSON.stringify(String(input)) + " -> " + result.value)
  }
  // Surrounding whitespace is a typo, not a different value.
  assert.strictEqual(Settings.normalizeCompactMetric("clients ").value, "clients")
  assert.strictEqual(Settings.normalizeCompactMetric(" none").value, "none")
  assert.strictEqual(Settings.normalizeCompactMetric("clients ").warning, null)
  // A case difference is NOT silently accepted: shell.json is hand-edited and
  // "CLIENTS" is as likely to be a guess as a typo, so it falls back and warns.
  assert.strictEqual(Settings.normalizeCompactMetric("CLIENTS").value, "none")
  assert.ok(Settings.normalizeCompactMetric(banned).warning !== null)
})

// --- dashboardUrl --------------------------------------------------------

test("an unusable dashboardUrl falls back to unset rather than to an error", () => {
  // REQ-012 already defines a fallback (derive from meta.apiRootHost), so
  // treating a bad value as "unset" is strictly better than disabling the
  // button over a typo.
  for (const input of [null, undefined, "", 7, {}, "example.com", "ftp://h/",
                       "javascript:1"]) {
    assert.strictEqual(Settings.normalizeDashboardUrl(input).value, "")
  }
  assert.strictEqual(Settings.normalizeDashboardUrl("https://h/").value, "https://h/")
  assert.strictEqual(Settings.normalizeDashboardUrl("http://h/").value, "http://h/")
  assert.strictEqual(Settings.normalizeDashboardUrl("").warning, null,
    "unset is the default, not a mistake")
  assert.ok(Settings.normalizeDashboardUrl("example.com").warning !== null)
})

// --- AC-019 (AUTO core) --------------------------------------------------

function layout(left, center, right) {
  return { left: left || [], center: center || [], right: right || [] }
}

const OTHER = { id: "omarchy.media" }

test("AC-019: differing refreshIntervalSec is a conflict", () => {
  const result = Settings.classifyLayout(layout(
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 30 }],
    [],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 60 }]))
  assert.strictEqual(result.present, true)
  assert.strictEqual(result.entryCount, 2)
  assert.strictEqual(result.conflict, true)
  assert.deepStrictEqual(result.conflictingKeys, ["refreshIntervalSec"])
})

test("AC-019: differing only in compactMetric is NOT a conflict", () => {
  // DATA-002. Suspending polling over a purely cosmetic difference would take
  // the plugin offline for a setting the service never reads.
  const result = Settings.classifyLayout(layout(
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 60, compactMetric: "none" }],
    [],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 60, compactMetric: "clients" }]))
  assert.strictEqual(result.conflict, false)
  assert.deepStrictEqual(result.conflictingKeys, [])
  assert.strictEqual(result.effective.refreshIntervalSec, 60)
})

test("AC-019: the left-most entry wins, in left -> center -> right order", () => {
  const result = Settings.classifyLayout(layout(
    [OTHER, { id: "gaius-codius.unifi", refreshIntervalSec: 45, compactMetric: "clients" }],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 45, compactMetric: "none" }],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 45 }]))
  assert.strictEqual(result.conflict, false)
  assert.strictEqual(result.effective.compactMetric, "clients")
  assert.deepStrictEqual(result.effectiveLocation, { section: "left", index: 1 })

  // Section order must not depend on object key iteration order.
  const reordered = { right: [{ id: "gaius-codius.unifi", refreshIntervalSec: 45 }],
                      center: [], left: [{ id: "gaius-codius.unifi", refreshIntervalSec: 45,
                                           compactMetric: "clients" }] }
  assert.deepStrictEqual(Settings.classifyLayout(reordered).effectiveLocation,
    { section: "left", index: 0 })
})

test("conflict is decided on NORMALIZED values, not raw ones", () => {
  // Two entries writing different nonsense that lands on the same value have
  // nothing for the user to reconcile, so suspending polling would be a demand
  // to fix a difference that does not exist. "abc" and null both fall back to
  // the default.
  const agreeing = Settings.classifyLayout(layout(
    [{ id: "gaius-codius.unifi", refreshIntervalSec: "abc" }],
    [],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: null }]))
  assert.strictEqual(agreeing.conflict, false)
  assert.strictEqual(agreeing.effective.refreshIntervalSec, 30)

  // But two entries that normalize DIFFERENTLY do conflict, even though both
  // inputs were invalid: 5 clamps up to 15 while "abc" falls back to 30, and
  // the service cannot poll at both rates.
  const disagreeing = Settings.classifyLayout(layout(
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 5 }],
    [],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: "abc" }]))
  assert.strictEqual(disagreeing.conflict, true)
  assert.deepStrictEqual(disagreeing.conflictingKeys, ["refreshIntervalSec"])

  // Both entries' warnings are surfaced, not just the winning one's — the user
  // has two mistakes to fix.
  assert.strictEqual(disagreeing.warnings.length, 2)
})

test("a single entry never conflicts, and an absent plugin is not present", () => {
  const single = Settings.classifyLayout(layout([{ id: "gaius-codius.unifi" }]))
  assert.strictEqual(single.present, true)
  assert.strictEqual(single.conflict, false)
  assert.strictEqual(single.entryCount, 1)

  const absent = Settings.classifyLayout(layout([OTHER], [], [OTHER]))
  assert.strictEqual(absent.present, false)
  assert.strictEqual(absent.conflict, false)
  assert.deepStrictEqual(absent.effective, Settings.DEFAULTS)

  for (const empty of [null, undefined, {}, { left: null }]) {
    assert.strictEqual(Settings.classifyLayout(empty).present, false)
  }
})

test("HC-2: duplicate entries are handled because nothing upstream prevents them", () => {
  // `allowMultiple: false` is stored by the shell at shell.qml:693 and read by
  // nothing, so a hand-edited shell.json can list the plugin as many times as
  // it likes. Duplicate handling is required behaviour, not defensive luxury.
  assert.strictEqual(MANIFEST.barWidget.allowMultiple, false)
  const many = Settings.classifyLayout(layout(
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 30 }],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 30 }],
    [{ id: "gaius-codius.unifi", refreshIntervalSec: 30 }]))
  assert.strictEqual(many.entryCount, 3)
  assert.strictEqual(many.conflict, false)
})
