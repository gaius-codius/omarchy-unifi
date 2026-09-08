# Spec addendum v1.1 — Devices and Clients

**Status: CONFIRMED 2026-09-07. Phases B0 … B4 complete; AC-B21 (MAN) is the
one criterion outstanding, and it moves to Phase 13.** Sections
marked "*Corrected during Phase B0*" record where implementation contradicted
what was confirmed — each is a change to this document, made deliberately and
with its reason stated. `SPEC.md` (spec-v1, frozen 2026-09-05) is unchanged by this
document except where a numbered amendment says otherwise; this file adds
requirements rather than editing them, and uses its own `REQ-B` / `AC-B` /
`DATA-B` number space so the frozen numbering stays stable.

Read after `SPEC.md`. Gates, autonomy rules and security requirements from
`SPEC.md` §10 and §15 apply here unchanged.

---

## 1. What this adds

Two browsable views inside the existing panel: every adopted **device**, and
every connected **client**, each searchable, each expandable to a detail block.

The product statement in `SPEC.md` §2 is unchanged and still governs: *answer
"is my network fine?" from the bar, without logging in, and when it isn't fine,
say what.* Overview remains the answer to that question. Devices and Clients
exist for the step immediately after it — you have seen that something is wrong,
or you want to find one machine, and the alternative is opening a browser.

**What this does not add.** No writes, no actions, no adopt/restart/upgrade. No
history, no events, no "down since" — the integration API has none (§3). No
per-client statistics; they do not exist on this API. No networks, firewall
policies or vouchers, though all three are reachable (§3).

---

## 2. Decisions taken

Recorded here because they were made in conversation on 2026-09-07 and are
otherwise undiscoverable.

| # | Decision |
|---|---|
| D1 | The pages ship **in v1**. Phase 13 (manual QA, packaging, release) runs after them, so the manual observations are made once, against the final panel. |
| D2 | Navigation is a **segmented control** at the top of the panel — Overview / Devices / Clients — not a drill-in stack and not a second bar item. |
| D3 | Client rows show **name, IP and uplink**; the MAC address appears only when a row is expanded. |
| D4 | Per-device detail **is** fetched, reversing an earlier recommendation. See §4. |
| D5 | The **private controller API is deferred**, not rejected. See §11. |

---

## 3. Evidence: the 2026-09-07 probe

Read-only GETs against the user's UniFi Network **10.6.101**, with their
approval. Field *names* only were recorded; no values, so nothing identifying
entered any transcript or file. The probe scripts were shredded and nothing was
written to the repository.

**Routes that exist and were not previously known:**

| Route | Returns |
|---|---|
| `GET /v1/sites/{siteId}/devices/{deviceId}` | `configurationId`, `firmwareVersion`, `firmwareUpdatable`, `supported`, `provisionedAt`, `ipAddress`, `macAddress`, `model`, `name`, `state`, `uplink.deviceId`, `interfaces.ports[].{idx, connector, maxSpeedMbps, state, poe.{enabled, standard, state, type}}`, `features.switching.lags[]` |
| `GET /v1/sites/{siteId}/clients/{clientId}` | Exactly the fields the list already carries — see below |
| `GET /v1/sites/{siteId}/networks` | out of scope |
| `GET /v1/sites/{siteId}/firewall/policies` | out of scope |
| `GET /v1/sites/{siteId}/hotspot/vouchers` | out of scope |

**`statistics/latest` is not gateway-only.** Every adopted device returns
`uptimeSec`, `cpuUtilizationPct`, `memoryUtilizationPct`,
`loadAverage{1,5,15}Min`, `lastHeartbeatAt`, `nextHeartbeatAt` and
`uplink.{rxRateBps, txRateBps}`. Access points additionally return
`interfaces.radios[].{frequencyGHz, txRetriesPct}`. The existing four-gateway
cap was built on the assumption that this was a gateway route. It is not.

**The device LIST record** carries `features[]`, `firmwareVersion`,
`firmwareUpdatable`, `supported`, `id`, `ipAddress`, `macAddress`, `model`,
`name`, `state`, and `interfaces` **as a list of strings** — a capability hint
(`["ports"]`, `["radios"]`), not data. Detail requires the per-device GET.

**The client record is eight fields, and that is all there is:**
`id`, `name`, `type`, `access.type`, `ipAddress`, `macAddress`,
`uplinkDeviceId`, `connectedAt`. Types observed: `WIRED`, `WIRELESS`.
`GET /clients/{id}` returns **nothing the list does not already carry**.

**Confirmed absent at site level**: `/events`, `/alarms`, `/health`,
`/statistics`, `/isp-metrics`, `/wan-metrics`. No `/v2`. No OpenAPI document is
served. `api-contract.md`'s limitations 1, 2 and 4 are therefore **observed**
rather than inferred: there is no history, no latency and no packet loss on this
API.

**The private API is present.** `/proxy/network/api/s/default/stat/health`,
`/proxy/network/api/self/sites` and `/proxy/network/v2/api/site/default/dashboard`
all return **401, not 404**. No login was attempted. See §11.

**Measured cost** (9 devices, local console):

```
GET /devices/{id}                    n=9  mean 13ms  max 15ms  total 0.12s
GET /devices/{id}/statistics/latest  n=9  mean 12ms  max 15ms  total 0.11s
current 6-request batch                                        total 0.09s
```

---

## 4. The route allowlist — and why it stays at six

`SPEC.md` §15 makes any change to the route allowlist stop-and-ask. The
conversational approval was for **eight** routes. The probe found two reasons to
ask for less:

1. **`GET /clients/{id}` buys nothing.** It returns exactly the fields the list
   record already carries. Adding it would widen the request surface for no
   information. *(Caveat: only `WIRED` and `WIRELESS` clients exist on the
   observed site. If a `VPN` or `TELEPORT` client turns out to carry more, this
   can be revisited on evidence.)*
2. **`/v1/sites/{siteId}/wans` should go.** DEV-5 had been open since Phase 5.
   Phase 12a settled the *factual* question by observation — the route returns
   `{id, name}` and nothing else — but the change was never applied, and as
   this addendum was drafted the route was **still fetched on every batch**.
   Phase B0 removed it. It cannot contribute to `DATA-006`
   and costs two HTTPS requests per batch (DATA-009a's end-of-collection
   re-read, each with its own handshake). This addendum resolves DEV-5 as
   **Option B — drop it**.

So the allowlist changes membership but not size:

| # | Route | Status |
|---|---|---|
| 1 | `GET /v1/info` | unchanged |
| 2 | `GET /v1/sites` | unchanged |
| 3 | `GET /v1/sites/{siteId}/devices` | unchanged |
| 4 | `GET /v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | cap raised (REQ-B02) |
| 5 | `GET /v1/sites/{siteId}/clients` | body now used, not just `totalCount` |
| 6 | **`GET /v1/sites/{siteId}/devices/{deviceId}`** | **NEW** |
| — | ~~`GET /v1/sites/{siteId}/wans`~~ | **REMOVED — DEV-5 Option B** |

`GET` remains the only method. No route may be constructed outside this table.

---

## 5. Requirements — data

**REQ-B01.** The helper carries the full adopted-device list in the envelope,
not only gateways and offline devices. Bounded by `DEVICES_LISTED_MAX = 200`,
with the unique total carried independently in `counts.devicesTotal` — never
derived from the array's length. Ordered per REQ-B11 by the **helper**, so
every consumer sees the same order.

**REQ-B02.** `statistics/latest` is fetched for adopted devices generally, not
gateways only, bounded by `DEVICE_DETAIL_MAX = 40`. The same bound governs
`GET /devices/{deviceId}`, and within this tier the two are fetched for the
*same* set of devices, so a device can never have ports without stats.

*Corrected during Phase B0.* The reverse does not hold, and stating it as a
symmetric invariant was wrong. **REQ-008a's four-gateway statistics pass is
retained as an independent tier and runs first.** It has to be: browse order
sorts `down` devices first, so on a site with a hundred failed switches and one
healthy gateway the gateway falls past this bound, and deriving REQ-008a's set
from the browse head — which reads as a tidy simplification — would cost the
panel its WAN reading exactly when the site is at its worst. A gateway can
therefore carry `metrics` with `detail: null`.

A consequence worth naming: **REQ-008a's `gateway_statistics_truncated` warning
now counts what is missing rather than what its cap declined.** With two tiers a
fifth gateway can be picked up by the browse pass, and a warning raised at the
cap would have reported "fetched 4 of 5" beside an envelope carrying five.

**REQ-B02a — the truncation is ordered by importance, not by array order.** The
detail set is selected in REQ-B11's order: every `down`, `impaired` and
`unknown` device first, then gateways, then the rest. A site of 200 devices with
three broken ones fetches detail for all three. Truncation raises
`device_detail_truncated` with `{fetched, total}`.

**REQ-B02b — detail collection is optional in BIZ-004's sense and yields to the
budget.** It is attempted only while the remaining REQ-017 deadline exceeds
`DETAIL_RESERVE_SEC = 8`, and stops — with the truncation warning — rather than
consuming the budget the required collections need. *A device browser must never
be able to break the health indicator.* An individual detail request that fails
omits that device's detail and warns; it does not fail the batch.

**REQ-B03.** The helper carries the connected-client list. Bounded by
`CLIENTS_LISTED_MAX = 500`, with the count still read from the terminal page's
`totalCount` exactly as today and carried independently in `counts.clients`.

**DATA-B01 — `data.devices[]`**, each entry:

```
{ id, name|null, model|null, state, class, roles[],
  ipAddress|null, macAddress|null,
  firmwareVersion|null, firmwareUpdatable|null,
  uplinkDeviceId|null,
  detail: null | { provisionedAt|null,
                   ports: [ { idx, connector|null, state|null,
                              maxSpeedMbps|null,
                              poe: null | { enabled, standard|null, state|null } } ],
                   radios: [ { frequencyGHz|null, txRetriesPct|null } ] },
  metrics: null | { uptimeSec|null, cpuUtilizationPct|null,
                    memoryUtilizationPct|null,
                    downloadBps|null, uploadBps|null } }
```

`class` and `roles` are REQ-000's, computed once in `normalize.py`, so the
browser and the health rules can never disagree about what a device is. `detail`
and `metrics` are `null` when not fetched — distinct from a fetched-and-empty
`ports: []`, because "not asked" and "no ports" are different facts (BIZ-003).

**DATA-B02 — `data.clients[]`**, each entry:

```
{ id, name|null, type, accessType|null,
  ipAddress|null, macAddress|null,
  uplinkDeviceId|null, connectedAt|null }
```

**DATA-B03.** `data.gateways[]` and `data.offlineDevices[]` are **retained
unchanged**. They are what REQ-008a and REQ-010 read, they are already covered
by the accept corpus, and re-deriving them from `devices[]` in the consumer
would move a decision out of the helper. Where the same device appears in both,
the two representations must agree; a cross-check asserts it.

**DATA-B04.** The envelope is assembled against a **224 KiB** byte budget.

*Corrected 2026-09-07, during Phase B0.* The confirmed figure was 4 MiB, which
is not reachable: DATA-005 caps helper stdout at **256 KiB**, and
`envelope.encode` enforces it as a **cliff** — one byte over and the entire
envelope is replaced by an `oversized_response` failure, so the panel greys and
the user gets no reading at all rather than a shorter list. 4 MiB would have
guaranteed that outcome on any large site. Raising DATA-005 was the alternative;
it is a bound in the frozen spec and this does not need it.

224 KiB is a guard rail 32 KiB in front of that cliff, and the reserve is spent
on three things the projection cannot see exactly: `json.dumps` separator
overhead, the six-bytes-per-character `\uXXXX` expansion of a non-ASCII device
name under `ensure_ascii=True`, and the warning that truncation itself appends —
a truncating envelope grows a warning at the moment it can least afford one.

**The count caps in REQ-B01/B02/B03 are upper limits; bytes bind first, and are
meant to.** A site of nine small devices gets detail for all nine; a site of two
hundred switches gets it for as many as fit, broken ones first. A pure count cap
would have to be set for the worst imaginable site and would starve every
ordinary one. Content is dropped in `bounds.ASSEMBLY_ORDER` — never the health
reading, then base device records, then clients, then per-device detail — each
raising the warning that names what went. `DATA-009`'s per-response 8 MiB bound
is unchanged; it bounds raw API traffic, not this.

**Warning codes added:** `device_detail_truncated`, `device_detail_unavailable`,
`clients_truncated`, `devices_truncated`, `envelope_truncated`.
**Retired:** `wans_unavailable` (DEV-5).

---

## 6. Requirements — user experience

**REQ-B10 — navigation.** A three-option segmented control at the top of the
panel: Overview, Devices, Clients. Overview is the default and is unchanged from
v1. The panel returns to Overview and clears any search text when it closes: the
widget's job is health, and reopening it should answer that question, not resume
a browse.

**REQ-B10a.** Overview's role-count rows are entry points. Activating
"Access points — 3 online, 1 down" opens Devices filtered to access points.

**REQ-B11 — device ordering.** Total and stable, applied by the helper:
`down`, `impaired`, `unknown`, `transitional`, `online`; within a class,
gateways first, then by name case-insensitively, then by `id`. The list exists
to surface what is wrong; alphabetical-only buries it.

**REQ-B12 — client ordering.** By name case-insensitively, then by `id`. A
client with no name falls back to its IP address, then its id — never to the
empty string, which would render as an unclickable blank row.

*Corrected during Phase B2.* This is applied **by the helper**, as REQ-B11 is,
and the requirement should have said so. It does not merely order the rendered
list: `CLIENTS_LISTED_MAX` and DATA-B04's byte budget take the *head* of the
client list, so with no order applied before the cut, which 500 of 900 clients
survive is whichever ones the controller paginated first — a set that can differ
between two polls with nothing on the network having changed. `ViewModel.js`
asserts the order rather than re-applying it, for the reason REQ-B01 gives.

The sort key is the name the **row renders**, not the raw `name` field. Sorting
by the raw field gathers every unnamed client at the front under the empty
string while the panel shows them by address, so the visible order looks
arbitrary.

**REQ-B13 — search.** A single field per list. Case-insensitive **substring**,
never fuzzy: a user must be able to explain why a row matched. Devices match on
name, model, IP and MAC; clients on name, IP, MAC, type and the resolved uplink
device name. Filtering is local, against the current snapshot; it issues no
request.

**REQ-B14 — rows and detail.** One row may be expanded at a time, so panel
height stays predictable.

*Device row:* name and model; state word, IP address, and uptime when known.
*Device detail:* firmware version, an "update available" mark when
`firmwareUpdatable`, MAC, the resolved uplink device name, CPU and memory
utilisation, throughput, the port table for a switch, the radio table for an
access point. A `null` `detail` renders as "details not fetched for this device"
naming the truncation — never as an empty port table, which would assert the
device has no ports.

*Client row:* name and IP; type word, "via <uplink device name>", and
connected-since as a relative time.
*Client detail:* MAC address, access type, and the absolute `connectedAt`.

**REQ-B15 — keyboard (extends UX-008).** Tab cycles segmented control → search
→ list → Refresh → Open UniFi and wraps. Left/Right move within the segmented
control; Up/Down within the list; Enter expands or collapses the focused row;
`/` focuses the search field. Escape clears a non-empty search **before** it
closes the panel. While the search field holds focus the panel's own key
handling is suspended, or typing "ap" drives the panel cursor instead of
filtering.

**REQ-B16 — empty and truncated states.** A filtered list with no matches says
so and names the term. An unfiltered list with no entries says so. A truncated
list shows "showing 200 of 412 devices", computed from the independently carried
total and never from the array's length (the REQ-010/AC-063 rule, applied
again).

**REQ-B17 — relative times.** "Connected 3d ago" and every other relative string
recompute on the service's existing freshness tick. No widget owns a timer
(REQ-014, UX-011). This is AC-071's guarantee, extended to the new strings.

---

## 7. Security and privacy

**REQ-B20.** Client names, IP addresses and MAC addresses are personal data.
They may appear in the rendered panel and nowhere else: not in warning messages,
not in error messages, not in any log line, not in `status`'s IPC output. The
sanitizer covers them, and a test asserts a corpus containing them produces
diagnostic output containing none of them.

**REQ-B21.** Per D3, the MAC address is not rendered until a row is expanded.

**REQ-B22.** SEC-011 is unchanged: no real controller data in fixtures. New
fixtures use RFC 5737 addresses and MAC addresses from `02:00:00:`.

*Corrected during Phase B0.* This originally specified the IANA documentation
range `00:00:5E:00:53:00–FF`. The repository's existing convention is stronger
and was already in place: `02:` has the IEEE 802 locally-administered bit set,
so the address is drawn from **no assigned OUI at all** — there is no vendor it
could belong to, rather than a vendor who has agreed not to use it. `00:00:5E`
is a real OUI assigned to IANA. The existing convention stands and the
documentation range is used only as a negative-control input.

The corpus guard already rejected MACs outside `02:00:00:`; what it lacked, and
now has, is a **canary**. Two independent ways for it to be useless would both
have passed silently: the MAC regex failing to match the shape a real address
has, or the prefix being one everything starts with.

---

## 8. Acceptance criteria

| # | Tier | Criterion |
|---|---|---|
| AC-B01 | AUTO | The route table contains exactly the six routes in §4. A test parses this document's table and asserts `routes.py` matches it; `/wans` appears in neither. |
| AC-B02 | AUTO | A 200-device corpus fetches detail for exactly 40, and every `down`, `impaired` and `unknown` device is among them (REQ-B02a). |
| AC-B03 | AUTO | With the deadline artificially reduced below `DETAIL_RESERVE_SEC`, the batch still succeeds, `devices[]` is complete, every `detail` is `null`, and `device_detail_truncated` is raised. |
| AC-B04 | AUTO | A single failing `GET /devices/{id}` omits that device's detail, warns, and does not fail the batch or affect any other device. |
| AC-B05 | AUTO | `devices[]` and `gateways[]`/`offlineDevices[]` agree for every device present in both, over the whole accept corpus. |
| AC-B06 | AUTO | Truncated `devices[]` and `clients[]` still report the true totals, and the panel renders "showing N of M" from them. Shortening the arrays does not change either number. |
| AC-B07 | AUTO | Device order is the REQ-B11 total order, asserted against a corpus containing every class and a multi-role device. Two devices differing only in `id` sort stably. |
| AC-B08 | AUTO | Search is substring and case-insensitive over each named field, and a term matching nothing yields the empty result rather than the unfiltered list. |
| AC-B09 | AUTO | A device with `detail: null` renders the truncation sentence; a device with `detail.ports: []` renders an empty port table. The two are never confused. |
| AC-B10 | AUTO | `metrics: null` renders as "unknown" throughout and never as `0` (BIZ-003, applied to the new fields). |
| AC-B11 | AUTO | A client with no name renders its IP; with neither, its id; never the empty string. |
| AC-B12 | AUTO | A site whose content exceeds the 224 KiB budget drops it in `bounds.ASSEMBLY_ORDER`, raises the warning naming what went, remains schema-valid, and **never reaches `envelope.encode`'s replacement path** — asserted by encoding the result and measuring it. |
| AC-B12a | AUTO | `bounds.ENVELOPE_BUDGET_BYTES` is strictly below `envelope.STDOUT_MAX_BYTES`, and the two constants are declared independently so that raising one does not silently raise the other. |
| AC-B13 | AUTO | A corpus of clients with names, IPs and MACs produces warnings, error messages and `status` output containing none of them (REQ-B20). |
| AC-B14 | AUTO | The corpus privacy guard rejects a MAC outside the `02:00:00:` locally-administered range, proven by a seeded canary that includes an IANA-documentation-range address — which is a real assigned OUI and must therefore be rejected too. |
| AC-B23 | AUTO | Every warning code appearing in the fixture corpus is one `docs/protocol-v1.md` defines, proven by a seeded canary. `wans_unavailable` outlived its own removal inside an accept envelope because nothing checked this. |
| AC-B15 | LIVE | The segmented control switches views; each view renders from `vm` and computes nothing (REQ-014). |
| AC-B16 | LIVE | Typing in the search field filters the list and does not drive the panel cursor. Escape with a non-empty search clears it and leaves the panel open; Escape again closes it. |
| AC-B17 | LIVE | Tab reaches every control in REQ-B15's order and wraps. Up/Down move the list cursor; Enter expands the focused row; exactly one row is expanded at a time. |
| AC-B18 | LIVE | A list longer than the panel scrolls, and one shorter than it does not become interactive (the `contentHeight > height` rule). |
| AC-B19 | LIVE | Activating an Overview role-count row opens Devices filtered to that role. |
| AC-B20 | LIVE | A full batch against the real controller completes within the REQ-017 budget with detail fetched for every device, and the measured wall time is recorded. |
| AC-B21 | MAN | Both lists are legible in ≥3 themes including one light theme (AC-069's rule, extended). |
| AC-B22 | AUTO | `tests/test_suite_integrity.js` reports a named, executing test for every criterion in this table. |

---

## 9. Phases

Inserted **before** the existing Phase 13, which remains the final release
phase (D1).

**Phase B0 — contract and fixtures.** `api-contract.md` §12b written from §3.
`routes.py` table updated and `/wans` removed. Protocol schema extended for
`devices[]` and `clients[]`. Accept and reject corpora extended, including the
200-device and 4 MiB cases. Privacy guard extended to MACs. *Exit: corpus
generates, every existing test still passes, `/wans` gone from code and docs.*

**Phase B1 — helper.** `collect.py` fetches the detail set under REQ-B02a/B02b;
`normalize.py` produces `devices[]` and `clients[]` with `class` and `roles`
shared with the existing counters; envelope bound and the five new warnings.
*Exit: AC-B01 … AC-B06, AC-B12 … AC-B14. Mutation-tested.*

**Phase B2 — model.** `ViewModel.js` gains the row builders, the search filter,
the ordering assertions, the empty/truncated strings and the detail formatting.
No QML. *Exit: AC-B07 … AC-B11. Mutation-tested.*

**Phase B3 — view.** Segmented control, both lists, search field, expandable
rows, keyboard. `host-contract.md` citations added for `Ui/ButtonGroup`,
`Ui/TextField`, `Ui/PanelKeyCatcher` and the `ListView` height idiom **before**
any of them is used (§15's "any use of a host API that cannot be cited"). *Exit:
AC-B15 … AC-B19 under the live harness.*

**Phase B4 — live and staged.** Against the real controller and the real shell.
*Exit: AC-B20, AC-B21, a clean `tests/run.sh --live`, and* **Checkpoint CPB**.

**Phase 13 — manual QA, packaging, release.** Unchanged, and now runs against
the final panel.

---

## 10. Risks

**R-B1 — the detail bound is the whole performance story.** Measured at 13 ms
per request on a local console; a remote or loaded one could be 20× that. 40
devices is 80 requests, which at 200 ms each is 16 s against a 25 s budget —
inside it, but not comfortably. REQ-B02b's reserve is what makes the failure
mode "fewer details" rather than "no health reading". AC-B03 tests exactly that,
and AC-B20 measures the real number.

**R-B2 — two representations of one device.** `devices[]` alongside
`gateways[]`/`offlineDevices[]` is duplication, and duplication drifts. Mitigated
by DATA-B03's cross-check over the whole corpus, which is the same mitigation
already used for the pure modules' duplicated constants.

**R-B3 — panel height.** Three views of very different heights in a dropdown
that sizes to content. The bluetooth panel's bounded-`ListView` idiom is the
precedent, but the transition between views has not been observed.

**R-B4 — the client list is the first personal data this plugin has held.**
REQ-B20 and AC-B13 are the control; the risk is a future warning message
interpolating a client name without anyone noticing, which is why the test
asserts over diagnostic output as a whole rather than over specific messages.

---

## 11. Deferred: the private controller API

**Not rejected — deferred, with the evidence recorded so the decision can be
retaken without redoing the work.**

The probe established that `/proxy/network/api/...` is **present and
credential-gated** (401, not 404) on this console. No login was attempted and no
field of it has been observed. Everything below is from general knowledge and is
**unverified against this hardware**.

**What it would add that nothing else can, locally:** `stat/event` and
`list/alarm` give timestamped history — "AP down *since 09:14*", the largest
remaining gap in the product. `stat/health` gives a WAN subsystem with latency,
throughput and speedtest results, addressing `api-contract.md` limitations 1 and
2. `stat/device` and `stat/sta` are supersets of the integration API's records,
adding per-client signal strength and byte counters.

**Why it is not a swap:**

1. **The vocabulary differs, and the health model is built on it.** REQ-000's
   five classes are a total mapping over the integration API's ten documented
   `state` strings. The private API reports a numeric state with no published
   meaning. Porting is not translation; it is re-deriving the classification
   with nothing to check the result against.
2. **Roles differ.** There is no `features[]`; device type comes from `type`
   (`ugw`/`usw`/`uap`/`udm`) plus capability flags. Though DEV-6 showed the
   integration API's `features` is itself unreliable, so this might be an
   improvement.
3. **It cannot be checked.** Every claim in `api-contract.md` cites a published
   schema. Against an undocumented API, "the contract" degrades to what one
   firmware returned on one afternoon, and it changes between releases.
4. **The credential changes class.** No API key exists for this surface: it is
   `POST /api/auth/login` with a username and password, a session cookie and a
   CSRF token, with session renewal and lockout risk. SEC-001 would still hold,
   but it would be guarding something that can *change* the network rather than
   something that can only read it.

**The shape to adopt if it is ever taken up:** two sources with different jobs,
not a replacement. The integration API stays the sole input to every health
decision, colour rule and count — documented, key-authenticated, checkable. The
private API becomes a strictly optional enrichment for what nothing else
provides, configured separately, allowed to be absent, and degrading to exactly
today's behaviour when it fails. That preserves the property that has been worth
most on this project: **everything that decides anything derives from a contract
that can be checked.**

**What would settle it:** a limited or read-only local admin account, if UniFi
Network still offers one, and a read-only probe reporting field names only —
the same method as §3. That converts the four objections above from recollection
into observation.

**The cloud Site Manager API** (`api.ui.com`, key-authenticated, documented)
remains the alternative for WAN latency and loss specifically. It is official
and does not need a password, but it is cloud: a new outbound destination, a
requirement that the console be cloud-connected, and coarser granularity.

---

## 12. Confirmations sought

1. **Six routes, not eight** — `GET /clients/{id}` dropped as redundant, and
   `/v1/wans` removed, closing DEV-5 as Option B (§4).
2. **The bounds**: 200 devices listed, 500 clients listed, detail for 40, 8 s
   deadline reserve (§5). ~~4 MiB envelope~~ — corrected to 224 KiB during
   Phase B0; see DATA-B04 for why 4 MiB was unreachable.
3. **The panel resets to Overview and clears the search when it closes**
   (REQ-B10).
4. **Order by brokenness, not alphabetically**, for devices (REQ-B11).
