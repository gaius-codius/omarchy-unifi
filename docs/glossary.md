# Glossary: identifiers cited in the code

This plugin was built against a written spec, and the code, tests and fixture
notes cite that paperwork by identifier — `DEV-4`, `CP8`, `Phase 12a`, `risk
R-F`, `G-STAGING`, `AMD-3`. Most of those identifiers resolve in published
documents. Six schemes do not: they were defined in the working documents (the
implementation plan, the deviation log, the implementation notes), which are the
process record — task lists, checkpoint transcripts, day-to-day minutes — and
are not published.

The decisions those documents recorded still bind the shipped code. A comment
saying "raised as DEV-4" is the only thing standing between a rule and a
maintainer who reads it as arbitrary. So this file resolves every orphaned
identifier a reader can meet in a tracked file, with the substance of the
decision rather than an expansion of the acronym.

Identifiers that already resolve are **not** repeated here. `REQ-`, `AC-`,
`DATA-`, `BIZ-`, `SEC-`, `UX-`, `SPEC-AMD-` and the risks `R1`…`R8` are in
[`SPEC.md`](feature-specs/omarchy-unifi-plugin/SPEC.md); `REQ-B`/`AC-B`/`DATA-B`
and phases `B0`…`B4` are in
[`SPEC-v1.1-browse.md`](feature-specs/omarchy-unifi-plugin/SPEC-v1.1-browse.md);
`HC-` host facts are in
[`host-contract.md`](feature-specs/omarchy-unifi-plugin/host-contract.md); the
envelope contract is [`protocol-v1.md`](protocol-v1.md).

## Two collisions to know about first

**`AMD-n` is not `SPEC-AMD-n`.** They are different series with overlapping
numbers, and resolving one against the other returns a confident wrong answer.
`AMD-n` (below) is a **plan amendment**: implementation order, module structure,
test approach. `SPEC-AMD-n` is an **amendment to the frozen spec**, marked in
place in the section it amends, in `SPEC.md` or `SPEC-v1.1-browse.md` — with the
two exceptions recorded below. `AMD-3` pins the toolchain in `mise.toml`;
`SPEC-AMD-3` is an unrelated rule about dropping zero-count device classes.

**`R-<letter>` is not `R<digit>`.** `R-B`, `R-E`, `R-F`, `R-G` and `R-I` (below)
are implementation risks from the plan. `R1`…`R8` are the spec's own risk
register, `SPEC.md` §14. They are unrelated lists.

## Deviations — `DEV-1` … `DEV-8`

A deviation was raised when implementation found something the frozen spec could
not answer, or answered two ways. Each was decided by the owner before the code
changed. Numbers are chronological, not thematic.

**`DEV-1` — `counts` could not answer the health rule that read it.**
Resolved 2026-09-05. REQ-002 rule 4 asks about unique devices, but the role
counters double-count multi-role devices and omit featureless ones, and
`offlineTotal` cannot isolate `unknown`. `DATA-006b` adds `counts.byClass` as a
unique-device partition with two invariants enforced on both sides of the wire.
Stated at `protocol-v1.md` §"invariants" and in `SPEC.md` §16's decisions table.
DEV-1 is cited throughout the tests as the canonical failure shape: a value that
cannot evaluate the question asked of it, hidden because producer and consumer
were each checked against a copy rather than against each other.

**`DEV-2` — `meta` was unsatisfiable on an early failure.**
Resolved 2026-09-05. On an `unconfigured` or `uncommitted` failure the source
configuration is by definition unreadable, so `meta`'s fields cannot be
populated. `DATA-006a` makes every field except `helperVersion` nullable and
`meta` always present, rather than making `meta` itself optional. See
`protocol-v1.md` — "`meta` is unconditional" — and `SPEC.md` §16.

**`DEV-3` — REQ-008a's third bullet had two readings, and one contradicted
REQ-002.** Approved 2026-09-06, option B. REQ-008a derives `wan.status` from
"some but not all gateways `down`, `impaired`, or `unknown` → `degraded`". Read
as "some but not all of {down, impaired, unknown}", a site where *every* gateway
is impaired matches no bullet and falls through to `up`. But REQ-002 rule 4
counts `down + impaired + unknown > 0` and colours that same site amber, so the
panel would print `WAN: up` beside an amber glyph — and the wrong half is the one
the user acts on. Implemented as the ordered, total reading: no gateways →
`unknown`; every gateway down → `down`; **any** gateway down, impaired or unknown
→ `degraded`; otherwise `up`. Agrees with the other reading on every cell that
reading defines. Pinned by name in `tests/model/health.test.js`.

**`DEV-4` — the startup ramp and the shared backoff counter cannot both be read
literally.** Approved 2026-09-06, option B. REQ-023b retries every 2 s for up to
30 s when the first attempt fails with a `network` error, "before entering the
normal REQ-019 schedule". REQ-020b increments the exponential failure counter on
"every automatic failure regardless of class". Ramp retries are automatic
failures, so read literally the ramp leaves `failures` at 15 and the first
post-ramp wait pinned at the 900 s cap: a laptop whose Wi-Fi associates 35
seconds after login gets a 30-second flurry and then **fifteen minutes of
silence** — strictly worse than having no ramp at all. Implemented so that ramp
failures do not advance the shared exponent; the ramp is its own schedule, which
is what "before entering the normal schedule" says. A ramp attempt failing with
anything other than `network` leaves the ramp and is counted normally; a success
closes it. This is the rule `Schedule.js` guards and
`tests/model/scheduler.test.js` pins under AC-040.

**`DEV-5` — `/v1/wans` was fetched and its result had nowhere to go.**
Resolved 2026-09-07, option B: drop the route. The route returns `{id, name}`
and nothing else — no status, latency, loss or throughput — while `DATA-006`'s
`wan` object has no field for a name, so the call could only fail, never
contribute. It cost two HTTPS requests per batch against REQ-017's 25 s budget
(DATA-009a re-reads the end of every collection). Applied in Phase B0; see
`SPEC-v1.1-browse.md` §4 and `api-contract.md` §"DEV-5 is settled by
observation". The warning code `wans_unavailable` was retired with it.

**`DEV-6` — no device reports the `gateway` feature.**
Resolved 2026-09-06, option B, on first contact with real hardware. REQ-000 is
amended (`SPEC-AMD-1`): a device is a gateway if its `features` contains
`gateway` **or** it reports an `ipAddress` that is not on the site's LAN.
Implemented once in `normalize.roles_of`, shared by `is_gateway` and the
per-role counter so the gateway list and `counts.gateways` cannot disagree. The
off-LAN predicate is written out rather than using `ipaddress.is_global`, which
treats CGNAT space (RFC 6598) as private and would have failed to recognise a
console behind carrier-grade NAT. See `SPEC.md` §"REQ-000" and `api-contract.md`.

**`DEV-7` — eight `devices[]` fields the contract declared and nothing
validated.** Approved 2026-09-08, option B. `checkDeviceRecord` validated `id`,
`class`, `roles`, `detail` and `metrics`; DATA-B01 also declares types for
`state`, `name`, `model`, `ipAddress`, `macAddress`, `firmwareVersion`,
`firmwareUpdatable` and `uplinkDeviceId`, none of them checked. It surfaced
because three mutations of `entry.firmwareUpdatable === true` to
`!!entry.firmwareUpdatable` survived the whole suite: over the values the
contract *allows* they agree exactly, and they differ only on values the
contract forbids and nothing rejected — `!!"false"` is `true`, so a producer
sending the string `"false"` would have the panel report a firmware update on
the strength of a word. It was stop-and-ask rather than a tidy-up because
rejection here is whole-envelope (DATA-008): stricter validation trades "a wrong
value renders" for "a wrong value costs the whole reading". Resolution: validate
all eight, **and** the six `clients[]` fields with the same gap (`name`,
`accessType`, `ipAddress`, `macAddress`, `uplinkDeviceId`, `connectedAt`) — one
gap in one function, and those six carry the personal data REQ-B20 protects.
Two boundaries drawn deliberately: `connectedAt` is checked as a string and not
against the RFC 3339 grammar (`ViewModel.formatInstant` renders anything it
cannot parse as "unknown", and a consumer stricter than its own contract would
throw away a reading over an unusual timestamp); and an absent key rejects,
matching `detail` and `metrics`. Fixed in the same pass:
`checkBrowseLists` returned early on a broken `devices`, so an envelope broken
in both lists reported only the first reason. Summarised at `protocol-v1.md`
§"Every field's declared type is enforced".

**`DEV-8` — never raised.** The single citation, in `Protocol.js`, is to a
deviation that does not exist: the `clients[]` half of DEV-7 was included in the
approved device fix "rather than left for a DEV-8". Read it as "rather than
deferred to a second deviation". There is no DEV-8 packet and no decision under
that number.

## Plan amendments — `AMD-n`

Tier 2 changes: implementation order, module structure or test approach changed,
while the approved spec stayed fully satisfiable as written. Not spec changes —
for those see `SPEC-AMD-n` in `SPEC.md` §16. Only the amendments cited from
tracked files are recorded here.

**`AMD-1`** — AC-033's lint gate applies to a **glob** over every root dual-use
`.js` module, not to the literal filename `Model.js`. HC-16 stops dual-use
QML/Node files importing one another, so the pure layer is necessarily several
files. Strengthens the gate; verifies no less. Cited by
`tests/lint/no_qt_in_js.sh` and `tests/model/consistency.test.js`.

**`AMD-3`** — the toolchain is pinned in a **project-scoped `mise.toml`**, and
the helper suite runs under Python 3.9.25 *and* 3.14.7. `gitleaks` was absent
from `PATH`, so AC-013's named scanner was unavailable, and the §11 Python floor
was an untested assertion. Both resolve in user space without sudo; the global
mise config is never touched, so the user's other projects are unaffected.
**Correction carried from the record:** the dual-version run does *not* exercise
HC-15 — that concerns `python3 -I`, and the helper is launched with `-B -E -s`,
never `-I`. It is justified solely by testing the declared floor and the current
ceiling. Cited by `mise.toml`.

**`AMD-4`** — the two gates were rescoped. G-CONTROLLER moved from "Phase 1" to
Phase 12a/12b and was extended to require an API key **and** explicit
authorisation for live traffic; G-STAGING moved from "every LIVE-tier criterion"
to the named live-staged criteria. HC-17 proved the spec's LIVE tier is really
two tiers — `quickshell -p` needs a Wayland session but not staging — so without
the remap the spec and the plan gave contradictory permissions for AC-004. This
is the amendment `SPEC.md` §14's R5 editorial note points at.

**`AMD-5`** — the pure layer is `tests/model/*.test.js`, not `SPEC.md` §13's
single `tests/test_model.js`. Same HC-16 reason as AMD-1, and AC-033's gate is
already a glob, so coverage is unchanged.

**`AMD-8`** — `warnings` elements are objects `{code, message, detail}` with a
**closed** `code` enumeration (14 values then, 18 since v1.1), defined in
`docs/protocol-v1.md`. DATA-005 names `warnings` but never defines the element
shape, while DATA-012 requires discovered `{id, name}` site pairs to be carried
*in* `warnings` for UX-006a to list them — a bare string cannot satisfy that
without the panel parsing prose. The enumeration is derived from warning
conditions the spec already states; it adds no behaviour. Cited by
`tests/model/consistency.test.js`.

**`AMD-9`** — `helper/unifi/errors.py` is created in Phase 5 rather than Phase 6,
and `helper/unifi/__init__.py` is added although no task listed it. Phase 5's
modules need the `unconfigured`, `uncommitted`, `credential` and `tls` kinds
before Phase 6's transport taxonomy exists; four modules inventing their own
exception bases for Phase 6 to unify afterwards is worse than one base created
early and extended. `__init__.py` is a correction, not an addition: without it
`unifi` is a PEP 420 namespace package, which merges across every `sys.path`
entry — and `unifi` is a real name on PyPI.

**`AMD-13`** — Phase 10's view amendments, of which the code cites one: UX-007's
15 s relative-time tick **is** the service's existing 5 s freshness tick, not a
timer in `Panel.qml`. A timer in the view would be one clock per monitor, each
with its own idea of "now", against REQ-014's rule that per-monitor widgets hold
no state — UX-011's "every monitor shows identical state" would stop being a
consequence of the singleton and become a coincidence of timer phase. The same
amendment added the `ViewModel.js` row builders (a string assembled in a `.qml`
file is a string `node --test` cannot read) and `HealthColor.qml` as a seventh
view file, because REQ-001a's descriptor-to-colour table must be reachable from
both the bar glyph and the panel hero and only one of those is a bar item.
Cited by `tests/harness/runner.qml`.

## Spec amendments cited but not marked in place — `SPEC-AMD-1`, `SPEC-AMD-2`

`SPEC-AMD-3` onwards are marked in the spec text they amend. The first two were
approved during Phase 12, before that convention started, and the amended text
carries the reason rather than the number — so the code cites a label the spec
never prints.

**`SPEC-AMD-1`** — REQ-000's gateway definition gains a second clause: a device
is a gateway if `features` contains `gateway` **or** it reports an off-LAN
`ipAddress`. AC-021's wording follows. This is DEV-6's resolution; `SPEC.md`
§6's REQ-000 carries it as "*Amended 2026-09-06 (DEV-6)*". Cited by
`helper/unifi/normalize.py` and `tests/test_unifi_status.py`.

**`SPEC-AMD-2`** — three warning codes (`site_auto_selected`, `custom_ca_in_use`,
`insecure_tls`) are no longer listed in the panel's warning list. Each is
rendered by a dedicated element instead: the first inside the Details block's
Site row, the other two as their own rows. Presentation only — DATA-012, UX-009
and the helper are unchanged, and every code is still in `envelope.warnings`.
`insecure_tls` and `custom_ca_in_use` were appearing **twice** on screen, and
DATA-012's auto-selection sat permanently under a heading reading "Warnings" for
a decision that was correct and needed no action. Approved 2026-09-06. Cited by
`tests/harness/runner.qml` against REQ-013a.

## The three deviation tiers

`SPEC.md` §15 opens by classifying deviations "by the three-tier model in
`DEVIATION_LOG.md`". The model is:

- **Tier 1 — local adaptation.** An engineering surprise that changes no
  requirement, UX workflow, business rule, data contract, security behaviour or
  acceptance criterion. Logged; work continues.
- **Tier 2 — plan amendment.** Changes implementation order, module structure,
  test approach or checkpoint timing, while the approved spec remains fully
  satisfiable as written. These are the `AMD-n` entries above.
- **Tier 3 — stop-and-ask.** Anything that would change product behaviour, UX,
  business rules, the data model, permissions, acceptance criteria or rollout
  risk, plus every item in §15's stop-and-ask list. Work stops and a deviation
  packet is presented for a decision. These are the `DEV-n` entries above.

## Gates — `G-CONTROLLER` and `G-STAGING`

Two named stop points, agreed before implementation began. `SPEC.md` §15
classifies both underlying actions as stop-and-ask but does not use the names;
the names are the plan's, and they are what the test harness prints when it
refuses to act. Scopes below are as narrowed by AMD-4.

| Gate | Blocks | Cleared by |
|---|---|---|
| **G-CONTROLLER** | Any traffic to a real controller: Phase 12a/12b, `AC-B20`, the R3 rate-limit observation, and the *value* of the version-matrix constant. Nothing earlier waits on it. | The owner supplies controller type, Network version, `apiRoot`, site UUID, an API key, **and** explicit authorisation to send live traffic to their controller. |
| **G-STAGING** | Installing the plugin under the running shell: Phase 11, Phase 12b, and Phase 13's manual set — the live-staged criteria AC-002, AC-003, AC-012b, AC-019, AC-026, AC-028, AC-032 (LIVE half), AC-056, AC-068, AC-069, AC-070, AC-B21. | The owner approves staging into `~/.config/omarchy/plugins/`. |

Everything else — Phase 0 through Phase 10, and 61 of spec-v1's 72 acceptance
criteria — is reachable without either gate. The distinction the gates rest on
(HC-17) is that `SPEC.md` §12's LIVE tier is really two:

- **live-harness** — needs a graphical Wayland session, stages **nothing**. Runs
  under `quickshell -p` against a harness root outside the repository.
  `tests/run.sh --live-harness`.
- **live-staged** — needs the plugin installed under the real shell. The named
  criteria above, and nothing else. `tests/run.sh --live-staged`, which refuses
  without `OMARCHY_UNIFI_STAGING_APPROVED=1`.

`tests/tools/live_batch.py` is behind G-CONTROLLER and refuses the same way.
Both refusals are deliberate: the gate exists so that a test run cannot decide
to install software or send traffic on the owner's behalf.

## Phases

Implementation order. Phases are cited in comments to say when and why a module
came to exist, and which contract it was written against; nothing in the code
depends on the numbering. Phases `B0`…`B4` and `13` are defined in
`SPEC-v1.1-browse.md` §9 and are not repeated here.

| Phase | Delivers |
|---|---|
| **0** | Skeleton, `manifest.json`, and the lint gates themselves — a repository that passes `omarchy plugin validate`, plus every `tests/lint/*.sh`. |
| **1** | The fixture corpus and the frozen protocol contract (`docs/protocol-v1.md`): the substitute for a controller phase that was gated, and the wire contract two languages then implement independently. |
| **2** | `Health.js`, `Settings.js`, `ViewModel.js` — the largest single block of automated coverage. |
| **3** | `Schedule.js`, the highest-risk pure logic: polling, backoff, staleness, the startup ramp. |
| **4** | `Protocol.js` — envelope validation and the completion join. |
| **5** | The helper's security core: credentials, TLS context, paths, file modes. |
| **6** | Transport, routes, pagination, the error taxonomy. |
| **7** | `normalize.py`, `collect.py`, `envelope.py` — the first end-to-end automated slice. |
| **8** | `scripts/configure`, and the commit-and-reload contract. |
| **9** | `Service.qml`, the effect interpreter: process pool, watchdog, IPC, teardown. |
| **10** | The views: `Panel.qml`, `BarItem.qml` and the rest. |
| **11** | Live staging under the real shell. **Behind G-STAGING.** |
| **12** | Controller integration. **Behind G-CONTROLLER.** Split in two: **12a** is controller-only with nothing staged (route re-verification, the `X-API-Key` header name, the observed `applicationVersion` for the version matrix, a real `scripts/configure` run, DATA-012's single-site auto-selection); **12b** is the full stack staged *and* pointed at the controller, so it needs both gates. |

Phase 12a is the source of the "confirmed against hardware" notes in
`api-contract.md`, `helper/unifi/collect.py`, `helper/unifi/routes.py` and
`helper/unifi/version_gate.py`. It raised two divergences, both escalated and
both resolved by the owner: DEV-6, and the warning-list duplication
(`SPEC-AMD-2`). One R3 item was deliberately **not** done — provoking rate
limiting on the owner's own controller is not something to do for a test, so 429
and `Retry-After` remain covered against a stub.

## Checkpoints

A checkpoint is a gate between phases with named exit criteria, each asserted by
a test naming it. Cited in comments to explain where a test's obligation comes
from, and why a criterion is asserted at one layer rather than another.

| Checkpoint | After | Purpose |
|---|---|---|
| **CP1** | Phases 0 and 1 | Contract and gate baseline: freeze what two languages will both implement, and prove the gates bite. Requires `docs/protocol-v1.md` complete and every `tests/lint/*.sh` demonstrably failing on a seeded violation — the criterion `tests/lint/selftest.sh` exists for. |
| **CP2** | Phase 2 | Consumer contract frozen. |
| **CP3** | Phases 3 and 4 | The high-risk pure logic. HC-9 records that no backoff helper exists anywhere in the shell, so `Schedule.js` is written from scratch; HC-7 ordering and DATA-008 are where spec review found the most defects. |
| **CP4** | Phase 5 | Security behaviour established, reviewed at source level. |
| **CP4b** | Phase 6 | The transport seam. Phase 7 consumes transport, routing, pagination, the deadline and the taxonomy at once; without a gate here CP5 cannot separate a producer defect from an integration defect. |
| **CP5** | Phase 7 | The first end-to-end vertical slice — until here the producer and the consumer are two halves that have never met. |
| **CP6** | Phase 8 | The commit-and-reload contract. |
| **CP6b** | Phase 9 | The service layer, before the views build on it. Runs under `quickshell -p`, **no staging**. This is where anything needing real timers lands — AC-004's 30 s watchdog, AC-005's 15 s cadence — rather than in the pure layer, where a fake clock would assert only itself. |
| **CP7** | Phase 10 | The view layer under `quickshell -p`, still no staging: every panel string rendered from `vm` with no computation in QML, no hex literal in any `.qml`, `qmllint` green. |
| **CP8** | Phase 11 | Live-staged. **Behind G-STAGING.** `tests/harness/staged.sh` is CP8 written as executable checks rather than a transcript: it backs up `shell.json`, `~/.config/omarchy-unifi` and the plugin folder, restores all three from an EXIT trap including on failure, and refuses to start if a backup from an interrupted run is present. |
| **CP9** | Phase 12 | Controller integration. **Behind G-CONTROLLER.** |
| **CP10** | Phase 13 | Release: the manual QA set (AC-069, AC-070, and the human halves of AC-011 and AC-068), a final `gitleaks` pass, AC-072, and a clean `tests/run.sh --live`. This is the point `tests/test_suite_integrity.js` means by "report-only until CP10" — AC-072 cannot be satisfied until the last test is written, so enforcing it earlier would leave the suite red at every intermediate checkpoint and destroy the only signal the checkpoints have. |
| **CPB** | Phase B4 | The v1.1 browse equivalent of CP8/CP9, named in `SPEC-v1.1-browse.md` §9. Its criteria are that section's: AC-B20, AC-B21 and a clean `tests/run.sh --live`. |

## Risks — `R-B`, `R-E`, `R-F`, `R-G`, `R-I`

Implementation risks the plan tracked, and the reason several tests exist at all.
**The risk register itself was not preserved**, so the entries below are
reconstructed from the plan's own descriptions of what closed each risk and from
the code that cites them; the wording is not the original. The lettering had
gaps by the time these were cited — `R-A`, `R-C`, `R-D` and `R-H` are cited
nowhere in this repository and are **not recoverable**. Do not confuse this list
with `SPEC.md` §14's `R1`…`R8`.

**`R-B` — two engines, one file.** The dual-use `.js` modules run under Node's
V8 in the test suite and under Quickshell's V4 in the shell, and there is no
reason to assume they agree. Closed by `tests/harness/runner.qml`, which re-runs
the protocol corpus through `Protocol.js` under V4 against the verdicts
`node --test` recorded under V8. `tests/test_end_to_end.js` is the other half:
the real helper's output parsed by the real `Protocol.js`, with no Python
assertion standing between them.

**`R-E` — `terminal` fires twice for one batch.** The completion join must
report terminal exactly once across `{exit, stdout, stderr, watchdog}` in any
order, including after a watchdog. Two would start two follow-on batches for one
launch; none would stall the scheduler. Closed by making `joinBatch` a total
state machine that returns `terminal: true` at most once per join and absorbs
every later event — `Protocol.js`, replayed in all 4! orderings by the harness.

**`R-F` — the producer and the consumer inventing two shapes from the same
prose.** `normalize.py` (Python) and `Health.js` (JavaScript) were written in
different phases from the same written contract. Closed by one corpus used in
two directions: `tests/fixtures/envelopes/accept/` is fed to `Health.js` as
input by `tests/model/*.test.js` **and** asserted as `normalize.py`'s output,
byte for byte, by `tests/test_unifi_status.py`. Generating the expectation from
the implementation would make the test tautological, which is exactly the
mechanism by which DEV-1 went unnoticed.

**`R-G` — a skipped test that looks green.** A TLS test that skips when
`openssl` is missing reports success on a machine where the whole SEC-006 /
SEC-007a matrix never ran, and that matrix is what decides whether an
attacker-supplied CA can intercept a credential-bearing request. A silent skip
there is worse than a failure, because a failure gets fixed. `tls_stub.py`'s
`require_openssl()` therefore raises rather than skipping.

**`R-I` — the first poll running before there is anything to poll with.**
Anchoring REQ-023b's "immediately" on `Component.onCompleted` would fire before
`shell` exists, so the first batch would run against no configuration, fail, and
put the scheduler into a backoff it never needed. `Service.qml` anchors on the
rising edge of a derived `ready` predicate instead.

## Pointers in SPEC.md that lead outside this repository

`SPEC.md` is frozen and is left as written. Seven of its pointers name working
documents that are not published. What each was for, and where to look now:

| In `SPEC.md` | Names | Read it as |
|---|---|---|
| §1 "Supersedes" | root `PLAN.md` | The pre-skill design sketch this spec replaced. It has no authority over anything in the repository and is not published. Nothing cites it for a decision. |
| §11 "Repository layout" | `PLAN.md` shipped alongside the plugin | Now simply not present. The point being made survives: the repository root **is** the plugin folder, so `docs/` and `tests/` ship with it and are inert. |
| §13 "Manual QA (`PLAN.md` Phase 13)" | Phase 13 | The release phase; see the phase table above. The manual set itself is stated in §13 and does not depend on the plan. |
| §14 R5 editorial note | `PLAN.md` Phase 12, `DEVIATION_LOG.md` AMD-4 | See **AMD-4** and **Phase 12** above. The substance: no phase before 12 waits on controller details, an API key or live-traffic authorisation. |
| §15 opening | the three-tier model in `DEVIATION_LOG.md` | See **The three deviation tiers** above. |
| §15 "May decide without asking" | bounds and constants "documented in `implementation-notes.md`" | Documented at their definition sites in code instead, each with the reasoning: `helper/unifi/bounds.py` (the envelope byte budget and list caps), `helper/unifi/pagination.py` (maximum pages, maximum decoded bytes), `helper/unifi/deadline.py` (the request budget), `Schedule.js` (jitter magnitude and the backoff cap). |

`tests/lint/no_latency_metric.sh` also mentions `PLAN.md`, in a comment
explaining why `docs/` sits outside that gate's scan scope. The scope is stated
positively — manifest, root QML/JS, `helper/`, `scripts/`, `README.md` — so the
absent file changes nothing about what the gate checks.
