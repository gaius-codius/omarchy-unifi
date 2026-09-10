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
// Enforcing as of CP10. A spec enumeration without a named test fails this
// file, which is the property AC-072 is asking for. Report-only was the
// intermediate state: enforcing earlier would have left the suite red at
// every checkpoint before the last test existed. That moment has passed.
// SUITE_INTEGRITY_ENFORCE=0 restores the old report-only scan if a gap needs
// inspecting without failing the run; it is not the default.

const { test } = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const REPO = path.resolve(__dirname, "..")
const SPEC = path.join(REPO, "docs/feature-specs/omarchy-unifi-plugin/SPEC.md")
const PROTOCOL = path.join(REPO, "docs/protocol-v1.md")
const ENFORCE = process.env.SUITE_INTEGRITY_ENFORCE !== "0"

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
  // SOURCE ONLY. An earlier version also counted fixture FILENAMES, on the
  // theory that a table-driven suite names its subtests after the case ids in
  // the corpus. It immediately reported DATA-008 as 36/36 and DATA-009 as 13/13
  // while no test asserted a single one of those cases — the reject and
  // pagination corpora existed, and that was enough to satisfy the scan.
  //
  // A false green here is worse than a reported gap, because the gap is the
  // only thing that will make anyone write the missing test. So the scan stays
  // narrow, and the burden is on the suites: see the note above for how a
  // table-driven suite is expected to make its coverage visible.
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
//
// TRAP FOR PHASES 4 AND 5. The natural way to consume the fixture corpus is a
// TABLE-DRIVEN test that reads tests/fixtures/ and loops. Written that way, the
// case names live in fixture FILENAMES and never appear in the test source — so
// this check would report gaps for cases that are in fact fully covered, and the
// obvious response would be to weaken or delete it.
//
// Do neither. Have the table-driven suite name each subtest after its case
// (`test(\`rejects ${c.id}\`, ...)` / `with self.subTest(case=c["id"])`) AND
// assert the enumerated case list against an explicit list written out in the
// suite source. The explicit list is what this check reads, and it is a real
// assertion in its own right: deleting a fixture then fails the build, which is
// exactly what AC-072 asks for.
//
// Do not be tempted to make this check read the corpus directory instead. That
// was tried, and it reported 36/36 and 13/13 the moment the fixtures existed,
// while nothing asserted any of them. tests/model/viewmodel.test.js shows the
// intended pattern for the subtest half.
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
  console.log(`  (${count} test file(s) scanned)`)

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
