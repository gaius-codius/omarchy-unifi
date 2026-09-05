// AC-072. A manifest test: it asserts that the suite contains a named,
// executing test for every item in the spec's enumerations, and that those
// enumerations still say what this test thinks they say.
//
// The enumerations are PARSED OUT OF SPEC.md rather than copied here. A
// hand-copied list drifts from the spec silently — it is the same failure mode
// as DEV-1, where `counts` could not evaluate the health rule that read it, and
// the copy is exactly the artifact that would have hidden it. Parsing means
// adding a twentieth error kind to the spec fails this test until a test for it
// exists, which is the property AC-072 is asking for.
//
// REPORT-ONLY UNTIL CP10. Set SUITE_INTEGRITY_ENFORCE=1 to make gaps fail.
// AC-072 cannot be satisfied until the last test is written, so an enforcing
// gate would leave tests/run.sh red at every intermediate checkpoint and
// destroy the only signal the checkpoints have.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const REPO = path.resolve(__dirname, "..")
const SPEC = path.join(REPO, "docs/feature-specs/omarchy-unifi-plugin/SPEC.md")
const PROTOCOL = path.join(REPO, "docs/protocol-v1.md")
const ENFORCE = process.env.SUITE_INTEGRITY_ENFORCE === "1"

const specText = fs.readFileSync(SPEC, "utf8")

// --- parsing ---------------------------------------------------------------

function backticked(s) {
  return [...s.matchAll(/`([^`]+)`/g)].map((m) => m[1])
}

// "DATA-007: Error `kind` is one of `a`, `b`, ... — nineteen kinds"
function errorKinds() {
  const m = specText.match(/DATA-007: Error `kind` is one of([\s\S]*?)—/)
  assert.ok(m, "SPEC.md: could not locate the DATA-007 kind list")
  return backticked(m[1])
}

// "REQ-013: The panel renders ... for each of: `a`, ... and `reconfiguring`."
function panelStates() {
  const m = specText.match(/REQ-013: The panel renders[\s\S]*?for each of:([\s\S]*?)\.\n/)
  assert.ok(m, "SPEC.md: could not locate the REQ-013 state list")
  return backticked(m[1]).filter((s) => !/^REQ-/.test(s))
}

// The DATA-010b table's first column.
function configureStderrOutcomes() {
  const m = specText.match(/DATA-010b:[\s\S]*?\|---\|---\|---\|\n([\s\S]*?)\n\n/)
  assert.ok(m, "SPEC.md: could not locate the DATA-010b outcome table")
  return m[1]
    .split("\n")
    .filter((l) => l.startsWith("|"))
    .map((l) => l.split("|")[1].trim().replace(/`/g, ""))
    .filter(Boolean)
}

// --- test discovery --------------------------------------------------------

function collect(dir, re, acc = []) {
  if (!fs.existsSync(dir)) return acc
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) collect(p, re, acc)
    else if (re.test(e.name)) acc.push(p)
  }
  return acc
}

function suiteText() {
  const files = [
    ...collect(path.join(REPO, "tests"), /\.test\.js$/),
    ...collect(path.join(REPO, "tests"), /^test_.*\.py$/),
    ...collect(path.join(REPO, "tests"), /^test_.*\.sh$/),
  ].filter((f) => path.basename(f) !== path.basename(__filename))
  return { count: files.length, text: files.map((f) => fs.readFileSync(f, "utf8")).join("\n") }
}

function report(label, missing, total) {
  const covered = total - missing.length
  const line = `${label}: ${covered}/${total} covered`
  if (missing.length === 0) return console.log(`  ok   ${line}`)
  console.log(`  gap  ${line} — missing: ${missing.join(", ")}`)
  if (ENFORCE) assert.fail(`${label}: no named test for ${missing.join(", ")}`)
}

// A term is covered if a test file mentions it verbatim. Deliberately crude:
// the point is to notice a DELETED case, and a term that appears nowhere in
// any test file certainly has no test.
function uncovered(terms, text) {
  return terms.filter((t) => !text.includes(t))
}

// --- the checks ------------------------------------------------------------

test("spec enumerations parse and are internally consistent", () => {
  const kinds = errorKinds()
  const states = panelStates()
  const outcomes = configureStderrOutcomes()

  // These counts are asserted in SPEC.md's own prose (DATA-007 "nineteen
  // kinds", AC-066 "twenty-four REQ-013 states"). Checking them here catches a
  // spec edit that adds an item to one list and forgets the other.
  assert.strictEqual(kinds.length, 19, `expected 19 DATA-007 kinds, parsed ${kinds.length}`)
  assert.strictEqual(states.length, 24, `expected 24 REQ-013 states, parsed ${states.length}`)
  assert.ok(outcomes.length >= 6, `expected the DATA-010b table, parsed ${outcomes.length} rows`)

  for (const k of kinds) {
    assert.ok(states.includes(k), `DATA-007 kind "${k}" has no REQ-013 panel state`)
  }
  assert.strictEqual(
    states.length - kinds.length, 5,
    "REQ-013 should be the 19 error kinds plus exactly 5 non-error states",
  )
})

test("every enumerated case has a named test", () => {
  const { count, text } = suiteText()
  console.log(`  (report-only until CP10; ${count} test file(s) scanned)`)

  const kinds = errorKinds()
  const states = panelStates()
  const outcomes = configureStderrOutcomes()

  report("DATA-007 error kinds", uncovered(kinds, text), kinds.length)
  report("REQ-013 panel states", uncovered(states, text), states.length)
  report("DATA-010b outcomes", uncovered(outcomes, text), outcomes.length)

  // DATA-008 rejection classes and DATA-009 invariants are stated in SPEC.md as
  // prose, not as a list, so they cannot be parsed from it. They are enumerated
  // as tables in docs/protocol-v1.md, which Phase 1 delivers and CP1 gates.
  // Until that file exists this half of AC-072 has no source, and saying so is
  // the honest report: silently checking nothing would look identical to
  // passing.
  if (!fs.existsSync(PROTOCOL)) {
    console.log("  gap  DATA-008 rejection classes / DATA-009 invariants: docs/protocol-v1.md not written yet (Phase 1)")
    if (ENFORCE) assert.fail("docs/protocol-v1.md is required for the DATA-008 and DATA-009 halves of AC-072")
    return
  }
  const proto = fs.readFileSync(PROTOCOL, "utf8")
  for (const [label, heading] of [
    ["DATA-008 rejection classes", /##+\s*DATA-008 rejection classes([\s\S]*?)(\n##|$)/],
    ["DATA-009 invariants", /##+\s*DATA-009 invariants([\s\S]*?)(\n##|$)/],
  ]) {
    const m = proto.match(heading)
    if (!m) {
      console.log(`  gap  ${label}: no such section in docs/protocol-v1.md`)
      if (ENFORCE) assert.fail(`docs/protocol-v1.md is missing the "${label}" section`)
      continue
    }
    const terms = m[1].split("\n").filter((l) => l.startsWith("|"))
      .map((l) => l.split("|")[1].trim().replace(/`/g, ""))
      .filter((t) => t && t !== "id" && !/^-+$/.test(t))
    report(label, uncovered(terms, text), terms.length)
  }
})
