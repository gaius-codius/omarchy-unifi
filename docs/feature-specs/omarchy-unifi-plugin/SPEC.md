# Feature Spec: Omarchy UniFi status plugin

## 1. Status
Owner: gaius-codius
Date: 2026-09-05
State: **Approved (spec-v1)** — frozen 2026-09-05
Related issues / PRs: none — greenfield repository
Supersedes: `PLAN.md` at the repository root (the pre-skill design document, retained as the origin record)
Supporting documents: `api-contract.md`, `unifi-network-v1-readonly-subset.json` (this directory)

*Editorial note, 2026-09-10: the working documents this spec cites — `PLAN.md`
at the repository root and in this directory, `DEVIATION_LOG.md`, and
`docs/implementation-notes.md` — are the process record and are not published,
so the pointers to them here and in §11, §13, §14 and §15 lead outside the
repository. `docs/glossary.md` resolves every identifier they defined —
deviations, plan amendments, phases, checkpoints, gates and risks — and says
where each pointer's subject matter now lives. No requirement below changes.*

**Extended by `SPEC-v1.1-browse.md` (2026-09-07).** The Devices and Clients
views add requirements in a separate `REQ-B` / `AC-B` / `DATA-B` number space
and do not edit anything below, with four exceptions, each marked in place:
BIZ-001's and BIZ-004's route tables in **§8** (the allowlist changes membership
but not size — see the addendum's §4), DATA-006's `data` shape in **§9** (it
gains `devices[]` and `clients[]`), and AC-051 in **§12**.

## 2. Problem
A UniFi site owner running Omarchy has no ambient signal of network health on
the desktop. Discovering that the gateway dropped, that an access point went
offline, or that a switch is unreachable requires opening the UniFi dashboard in
a browser and reading it. There is no glanceable indicator and no fast path from
"something feels wrong" to "here is what is actually down".

Omarchy 4 ships a plugin system for exactly this kind of bar widget, but no
UniFi plugin exists.

## 3. Goals
- G1: A bar item whose colour communicates UniFi site health at a glance, using
  the active Omarchy theme.
- G2: A popup panel that answers "what is wrong" — device counts by role, the
  specific offline devices, gateway uptime and throughput, and client count.
- G3: Correctness under failure. Every failure mode is a distinct, named, honest
  state; nothing is ever silently reported as healthy or as an accurate zero.
- G4: The API key is never exposed — not in Git, QML, `shell.json`, process
  arguments, environment, logs, exceptions, test output, or UI text.
- G5: Exactly one poller per shell session regardless of monitor count, and at
  most one helper process in flight.
- G6: Read-only. The plugin cannot mutate UniFi state even if compromised.

## 4. Non-Goals
- Hosting, installing, or configuring the UniFi Network application.
- Any mutation: network / Wi-Fi / VLAN / firewall / device / client changes,
  device restart-adopt-remove-upgrade, client block/unblock/reconnect.
- Notifications, historical charts, or trend storage.
- UniFi Protect, Access, Talk, Connect, Mobility.
- Cloud / Site Manager access and multi-controller aggregation. The transport
  abstraction must not preclude it, but no cloud adapter ships in v1.
- Community marketplace publication.
- WAN latency and packet-loss display — see §5, these do not exist in the API.

## 5. Current Behavior

### 5.1 Existing state of this feature
None. The repository contains only the origin design document. There is no
prior UniFi integration in Omarchy to modify or replace.

### 5.2 Host platform baseline
Tested baseline is **Omarchy 4.0.3-1** with **Quickshell 0.3.1**
(`/usr/share/omarchy/version` reports `4.0.0.alpha`; the packaged version is
authoritative). *Amended 2026-09-10 (SPEC-AMD-11): originally 4.0.2-1; 4.0.3
sandboxes the injected `shell` and `manifest` (HC-22) and is now the supported
host.* Compatibility beyond this baseline is not assumed: each
additional Omarchy release must pass the §12 validation and runtime checks
before it is listed as supported.

The host contract this plugin depends on is documented in `host-contract.md`
in this directory, derived by reading `/usr/share/omarchy/shell/` and
`/usr/bin/omarchy-*` directly. Every host API this plugin calls is cited there
with a `path:line` reference; an uncited host API may not be used.

Host constraints HC-1 … HC-17 are recorded there; HC-11 … HC-17 were added
during review and planning (§16). Of the ten known at spec creation, the ones
that change requirements rather than implementation detail:

- **HC-1** — `barWidget.defaults`, `schema`, and `settingsForm` are registered
  as metadata and read by nothing in 4.0.2. There is no settings-form renderer,
  and manifest defaults are **not** merged into the `settings` object a widget
  receives. Defaults are applied in QML; users edit `shell.json`.
- **HC-2 / HC-4** — `allowMultiple` is unenforced and layout entries are keyed
  by plugin id alone, so duplicate entries are a reachable state with three
  different host handlers disagreeing about them.
- **HC-3** — a `service`-kind instance exists only while the plugin id appears
  in `bar.layout`. Placing the widget on the bar creates the service; removing
  it destroys the service.
- **HC-5** — `omarchy shell` exits 1 both when no shell is running and when the
  target is missing. Exit status alone cannot classify the outcome.
- **HC-7** — Quickshell gives no ordering guarantee between `Process.onExited`
  and `StdioCollector.onStreamFinished`.
- **HC-8** — there is no process-kill API. `running = false` is asynchronous and
  the child may outlive the request.
- **HC-9** — no backoff helper exists anywhere in the shell.
- **HC-10** — whether `qs.Commons` / `qs.Ui` resolve for a plugin loaded from
  `~/.config/omarchy/plugins/` is **unproven**. Every first-party importer lives
  inside `/usr/share/omarchy/shell/`. This is the single largest open technical
  risk (§14 R1). *Editorial note: resolved empirically on 2026-09-05 — see §14
  R1 and `host-contract.md` HC-10; the text above is retained as frozen.*

### 5.3 Upstream API behaviour that constrains the design
Established against the published `UniFi Network API 10.4.57` specification and
recorded in full in `api-contract.md`. The load-bearing facts:

- Local API root is `https://<host>/proxy/network/integration`, with operation
  paths under `/v1/`. Authentication is the `X-API-Key` header.
- Pagination envelope is `{offset, limit, count, totalCount, data}` with
  `limit` capped at **200**.
- `/v1/sites` returns `{id, internalReference, name}` — **no status field**.
- `/v1/sites/{siteId}/wans` returns `{id, name}` — **no status, no latency, no
  loss, no throughput**.
- Device `state` is a 10-value enum; `features` is a *set*, so one device can be
  gateway and switch and access point simultaneously.
- Device statistics expose optional `uptimeSec` and optional
  `uplink.rxRateBps` / `uplink.txRateBps`; only `interfaces` is required.
- The specification documents no non-200 responses and no rate-limit or
  `Retry-After` headers.

## 6. Target Behavior

### 6.0 Device state classification (basis for everything below)
REQ-000: Every adopted device is classified into exactly one class from its
`state` field. This classification is total — an unrecognized string lands in
`unknown`, never silently in another class.

| Class | `state` values |
|---|---|
| `online` | `ONLINE` |
| `transitional` | `PENDING_ADOPTION`, `UPDATING`, `GETTING_READY`, `ADOPTING`, `DELETING` |
| `down` | `OFFLINE`, `CONNECTION_INTERRUPTED` |
| `impaired` | `ISOLATED`, `U5G_INCORRECT_TOPOLOGY` |
| `unknown` | any other string; raises a warning |

A device is a **gateway** if its `features` array contains `gateway`, **or** if
it reports an `ipAddress` that is not on the site's LAN — that is, an address
outside RFC 1918 and IPv6 unique-local space, and not a loopback, link-local,
multicast or unspecified address.

*Amended 2026-09-06 (DEV-6), approved by the user, after Phase 12a observed a
real controller on which the second clause is the only one that fires.* UniFi
Network 10.6.101 reported `features: ["switching"]` for the UDM Pro routing the
site, so under the original rule the site had no gateway at all: `wan.status`
was permanently `unknown`, gateway uptime and throughput were permanently
absent, and REQ-002 rule 3 — the only route to **red** — was unreachable. The
gateway is nonetheless identifiable, because it is the one device that reports
its WAN address while every other device reports an RFC 1918 one.

The second clause is a heuristic and its failure mode is stated so it stays
that way: a console whose WAN address is itself RFC 1918 (double NAT) matches
nothing, and the result is exactly the behaviour of the first clause alone
rather than a wrong answer. Carrier-grade NAT space (RFC 6598, `100.64.0.0/10`)
is deliberately treated as off-LAN, because a console behind CGNAT is reporting
a real WAN address. A device that declares `gateway` is one whatever its
address is, so nothing that worked before the amendment behaves differently.

### Bar widget
REQ-001: A single bar item renders a UniFi glyph styled by overall site health
using **only** Omarchy theme tokens, never hard-coded hex values.

REQ-001a (palette constraint): The Omarchy theme exposes exactly
`Color.foreground`, `Color.background`, `Color.accent`, `Color.urgent`, and
`Color.muted` (`Commons/Color.qml:19-23`) — **there is no green, amber, or red
semantic token**. The shipped Network and Tailscale widgets use a binary idiom:
`bar.urgent` for a bad state, `bar.foreground` for normal, and
`Qt.darker(bar.foreground, 1.55)` for inactive
(`plugins/panels/network/Panel.qml:1237`, `:1662`;
`plugins/panels/tailscale/Panel.qml:40-44`). A literal green/amber/red scheme
would require hex literals, would break under light themes, and would violate
REQ-001. The four health levels are therefore rendered as four **theme-native
visual levels**, not four hues:

| Health | Rendering |
|---|---|
| healthy | glyph in `bar.foreground` |
| degraded | glyph in `bar.foreground` plus a persistent `bar.urgent` badge dot |
| critical | glyph in `bar.urgent` |
| unknown / stale | glyph in `Qt.darker(bar.foreground, 1.55)`, the established dim idiom |

The health *model* keeps the names `green`, `amber`, `red`, `grey` because they
are precise and testable; REQ-001a is the single mapping from model level to
theme token, so the pure health logic stays independent of presentation and the
mapping is the only thing that changes if Omarchy later adds semantic colours.

REQ-002: Health colour is the result of an **ordered, total decision
function** evaluated top to bottom; the first matching rule wins, so exactly one
colour is defined for every possible state.

| # | Condition | Colour |
|---|---|---|
| 1 | unconfigured, `site_unselected`, `uncommitted`, `configuration_conflict`, no complete snapshot yet, or the snapshot has passed `staleAt` | **grey** |
| 2 | the snapshot contains zero adopted devices | **grey** (empty state, UX-005) |
| 3 | at least one gateway exists **and** every gateway is `down` | **red** |
| 4 | `byClass.down + byClass.impaired + byClass.unknown > 0` | **amber** |
| 5 | otherwise | **green** |

Consequences that are intentional and must be preserved:
- A site with **no** gateway-featured device can never be red (rule 3 requires
  at least one gateway); it is judged on rules 4 and 5 alone and reports
  `wan.status = "unknown"`.
- A `transitional` device never degrades the colour — it is not matched by
  rule 4, so an `UPDATING` access point on an otherwise healthy site stays
  green. It is still listed as transitional in the panel.
- One gateway `ONLINE` and another `ISOLATED` is amber via rule 4, not red.
- Red strictly precedes amber, so an unrecognized state on a site whose
  gateways are all down does not downgrade red to amber.
- A configuration fault greys the widget immediately (rule 1) rather than
  leaving a stale green while polling is suspended.

REQ-003: A `transitional` device is reported as transitional, never as offline,
and never appears in the offline list. An `unknown` state raises a warning
(REQ-013a) naming the unrecognized value.

REQ-004: A failed refresh is surfaced immediately as a distinct warning overlay
while a still-fresh previous snapshot continues to determine the base health
colour from REQ-002. A failure never recolours the widget green and never
discards the last complete snapshot.

REQ-005: Optional compact text beside the glyph, controlled by `compactMetric`,
whose only values are `none` (default) and `clients`. Latency is not offered
because the API does not expose it (§5.3).

REQ-006: A tooltip reports site name, last successful refresh time, the latest
attempt's result, and a one-line status summary.

### Popup panel
REQ-007: Clicking the bar item opens a panel; clicking again, pressing Escape,
or losing focus closes it.

REQ-007a: At most one panel is open across all monitors. Opening the panel on
one monitor closes any panel open on another, so two `KeyboardPanel` instances
never contend for keyboard focus.

REQ-008: The panel shows site name, WAN state (REQ-008a), and — when the
controller supplies them — gateway uptime and current uplink download/upload
throughput. Absent optional metrics render as "unknown", never as `0`.

REQ-008a: `wan.status` has the domain `up`, `down`, `degraded`, `unknown`,
derived only from gateway devices:
- no gateway present → `unknown`;
- every gateway `down` → `down`;
- some but not all gateways `down`, `impaired`, or `unknown` → `degraded`;
- otherwise → `up`.

When several gateways exist, `wan` carries the aggregate status plus the metrics
of the **primary gateway**, defined as the first `online` gateway in ascending
`id` order, or the first gateway in ascending `id` order when none is online.
The panel additionally lists every gateway individually. Statistics are fetched
for at most **four** gateways per batch, ordered as above; any beyond that are
listed without metrics and raise a warning. This bounds route 4's contribution
to the REQ-017 time budget.

REQ-009: The panel shows the connected client count and, per role
(`gateway`, `switching`, `accessPoint`), counts in the five classes from
REQ-000. A device holding several roles is counted in **each** role it reports;
the panel labels these as role counts and separately shows the unique device
total, so the role rows are not expected to sum to it.

**SPEC-AMD-3 (2026-09-07):** only the classes with a non-zero count are
displayed, and a role with no devices at all is omitted entirely. Every non-zero
count is always shown. The model still computes all five classes and each row's
total is still summed over all five before the filter, so this changes what is
drawn and nothing that is decided.

REQ-010: The panel lists devices that are `down` or `impaired`, by name and
model, bounded to a maximum of 10 entries. The "and N more" line is computed
from `counts.offlineTotal`, an independent integer carried in the model, not
from the length of the truncated list, so a helper-side array bound can never
understate how many devices are down.

REQ-011: A Refresh button triggers an immediate manual refresh (REQ-018). It is
disabled, with an explanatory label, whenever polling is suspended (REQ-018a).

REQ-012: An "Open UniFi" button opens the dashboard via `Qt.openUrlExternally`,
never through a shell command or `execDetached`. When `dashboardUrl` is unset,
the button falls back to `https://<meta.apiRootHost>` derived from the committed
configuration (DATA-006a). When neither is available it is disabled with an
explanatory label.

REQ-013: The panel renders an explicit, distinctly worded state for each of:
`unconfigured`, `site_unselected`, `uncommitted`, `credential`, `unauthorized`,
`forbidden`, `tls`, `network`, `timeout`, `rate_limited`, `http`, `unsupported`,
`redirect`, `configuration_conflict`, `partial_response`, `oversized_response`,
`malformed_response`, `helper_unavailable`, `internal`, plus `loading`, `empty`,
`stale`, `service_unavailable` (REQ-013b), and `reconfiguring`.

REQ-013a: Warnings carried in the envelope are displayed in the panel as a
bounded, scrollable list. A warning never changes the health colour on its own.

REQ-013b: A widget whose `bar?.shell?.serviceFor("gaius-codius.unifi")` is null renders
`service_unavailable`: the muted glyph and a panel explaining that the service
did not load. This covers both the expected first-frame null and a genuine
service load failure, which the host surfaces only as a `console.warn`.

### Polling, scheduling, and staleness
REQ-014: A single shell-level `Service.qml` instance owns polling, cache, retry
state, and the helper process. Per-monitor bar widgets never poll.

REQ-014a: The bar widget registers **no** IPC target. It sets `manageIpc: false`
and declares no `IpcHandler`, so the `gaius-codius.unifi` target is owned unambiguously
by the singleton service and cannot be claimed by whichever monitor's widget
happens to construct first.

REQ-015: Default refresh interval 30 s; user-selectable 15–3600 s. The value is
type-validated and clamped at runtime (DATA-002a), never trusted from the
manifest, because no host component validates it (HC-1).

REQ-016: At most one **authoritative** helper batch is in flight. Because the
host provides no kill API and termination is asynchronous (HC-8), an abandoned
helper process may briefly outlive its replacement. The invariant is therefore
stated on authority, not on process count: at most one batch may affect service
state, and every other live process is marked abandoned and permanently
incapable of doing so. At most one abandonment may be outstanding per event.
Regular poll ticks that fire during an active batch are skipped.

REQ-017: The helper enforces a 25 s absolute deadline across every request and
every pagination page, checked between operations, with per-operation
connect/read limits no greater than the remaining budget. The service owns a
separate 30 s watchdog. Batch lifetime is independent of the refresh and backoff
intervals.

REQ-017a: The watchdog is armed **per batch**, tagged with that batch's
generation, and disarmed on batch completion. It is never left running across
batches. On expiry it marks the batch abandoned, publishes the `timeout` state
immediately, and does **not** wait for the process to exit. The abandoned
process's later `onExited` and stream output are discarded by generation check.

REQ-017b: Because `onExited` and `onStreamFinished` have no guaranteed order
(HC-7), a batch is complete only when the exit status **and** both stdio streams
have been observed, or the watchdog has fired. The protocol is never parsed from
`onExited` alone.

REQ-017c: The helper's own deadline is enforced between operations and cannot
interrupt a blocking `getaddrinfo`, which the standard library does not bound.
The 30 s QML watchdog is therefore the outer bound, and the helper deadline is
the inner, best-effort one. A read that keeps resetting its per-operation
timeout is caught by the absolute budget check before the next read.

REQ-018: A manual refresh starts immediately when the scheduler is idle. If a
batch is active, exactly one pending manual refresh is coalesced and starts when
it completes. A manual refresh bypasses the current wait once.

REQ-018a: A manual refresh is **refused** while polling is suspended by
`configuration_conflict`, `uncommitted`, or `site_unselected`, because there is
no unambiguous configuration to run it against. Suspended is not idle.

REQ-018b: A manual refresh that fails increments the attempt count but does
**not** advance the backoff exponent and does **not** move `nextAttemptAt`
later than it already was. Repeatedly clicking Refresh during an outage can
therefore never slow recovery.

REQ-019: Transient failures — HTTP 429, transient 5xx, timeouts, and `network`
errors — back off exponentially. The delay is
`min(interval × 2^failures, max(interval, 900 s))` with jitter. The cap is
`max(interval, 900 s)`, **not** a flat 900 s, so a failing controller is never
polled more often than a healthy one. `Retry-After` in delay-seconds or
HTTP-date form is honoured, using the larger of the exponential delay and the
normalized server delay; past dates and malformed values are ignored; an
otherwise valid server delay is clamped to 24 hours with a warning. Success
resets the failure count.

REQ-020: Fatal failures — `unconfigured`, `site_unselected`, `uncommitted`,
`credential`, `unauthorized`, `forbidden`, `tls`, `unsupported`,
`configuration_conflict`, `helper_unavailable`, and non-transient `http` — stay
visible and retry no more often than every `max(interval, 300 s)`, again so a
fatal condition never increases polling frequency. They are cleared only by a
configuration change, an acknowledged reload, or a successful manual refresh.

REQ-020a: Response-integrity failures — `partial_response`,
`oversized_response`, `malformed_response`, `redirect`, and `internal` — are
retryable and use the REQ-019 exponential schedule. No error kind is left
without a defined retry policy.

REQ-020b: When consecutive failures change class, `nextAttemptAt` is recomputed
from the new class's formula and then taken as
`max(newDeadline, existingDeadline)` — it is never shortened by a class change.
The exponential failure counter is shared, is incremented by every automatic
failure regardless of class, and is reset only by success.

REQ-021: The service preserves the last complete successful snapshot in memory
and records `lastAttemptAt`, `lastSuccessAt`, `staleAt`, `nextAttemptAt`, and
the latest error as separate values.

REQ-022: Freshness is defined by a single formula, evaluated live rather than
latched:

```
staleAt = lastSuccessAt + max(2 × min(intervalAtCompletion, currentInterval), 90 s)
```

where `intervalAtCompletion` is the interval in effect at the moment that batch
**completed**. Properties this gives, all required:
- Raising the interval above `intervalAtCompletion` has no effect, so an
  increase can never make an old snapshot fresh again.
- Lowering the interval tightens freshness immediately.
- Reverting a lowered interval restores the completion-based threshold rather
  than ratcheting the snapshot permanently stale.
- If a recomputation places `staleAt` in the past, the widget transitions to
  grey immediately, with no batch in flight and no failure. This is a defined
  transition, not an error.

REQ-023: The scheduler has exactly four states — **idle-normal**,
**active-batch**, **retry-wait** (REQ-019/REQ-020a), and **fatal-wait**
(REQ-020) — and interval changes are defined for all four:
- *idle-normal*: the next due time is recomputed as
  `lastCompletionAt + interval`. A decrease brings it forward and fires
  immediately if already overdue; an increase may move it later.
- *active-batch*: the change applies to the next normal cycle.
- *retry-wait* and *fatal-wait*: the deadline is **never shortened**; the new
  interval becomes the base only for subsequent calculations.

REQ-023a: The normal cadence anchors on batch **completion**, not launch:
`nextAttemptAt = lastCompletionAt + interval`. This guarantees at least one full
interval of idle between batches, which matters because the minimum interval
(15 s) is shorter than the maximum batch duration (25 s) and a launch-time
anchor would otherwise poll continuously with no gap.

REQ-023b: The first attempt after the service acquires a valid configuration
runs **immediately**, not after one interval. Following the host's startup-ramp
precedent, if that first attempt fails with a `network` error the service
retries every 2 s for up to 30 s before entering the normal REQ-019 schedule, to
cover the case where the shell starts before the network is up.

REQ-024: A one-shot wake timer is armed for the earliest **future** deadline
among `staleAt` and `nextAttemptAt`. It is **rearmed after every firing**, so
crossing `staleAt` at T+90 does not leave the service without a timer for a
`nextAttemptAt` at T+900. Deadlines already in the past are excluded from the
selection, which prevents an immediate-fire loop. When neither deadline exists —
unconfigured, or polling suspended — the timer is disarmed.

REQ-024a: A failed attempt never rewrites the snapshot's `observedAt`.
Scheduling deadlines use monotonic time. **Staleness uses wall-clock time**,
because a snapshot genuinely does age across a system suspend. On resume, if the
wall clock has advanced past `staleAt` the widget greys immediately, and if it
has advanced by more than one interval the service treats the schedule as
overdue and starts a batch.

## 7. User Experience
UX-001 (bar, healthy): themed UniFi glyph in the theme's success colour, plus
optional client count when `compactMetric` is `clients`.

UX-002 (bar, degraded / down / stale): the same glyph in the theme's warning,
error, or muted colour per REQ-002. Colour is never the sole signal — the
tooltip and panel always state the condition in words.

UX-003 (bar, refresh failed but snapshot fresh): a small warning affordance
composited over the health-coloured glyph, so "last refresh failed" is
distinguishable from "confirmed bad".

UX-004 (loading): first load before any snapshot shows the muted glyph and a
tooltip reading that the first refresh is in progress. A *refresh* while a
snapshot exists never blanks the widget. A *reload* (DATA-011) is the one
exception and renders `reconfiguring`, because the cached snapshot may belong to
a different controller entirely and continuing to display it would be a lie.

UX-005 (empty): a configured site with zero adopted devices shows an explicit
"no adopted devices" panel state and a grey bar item, distinct from a failed
fetch.

UX-006 (unconfigured): muted glyph; the panel explains that
`~/.config/omarchy-unifi` is not yet set up and names `scripts/configure`.

UX-006a (site not selected): when the controller is reachable and authenticated
but no `siteId` is committed and the controller has more than one site, the
panel lists the discovered site names and IDs and tells the user to run
`scripts/configure --site <id>`. A controller with exactly one site is
auto-selected with a warning rather than blocking the user.

UX-007 (panel error states): each REQ-013 state gets its own sentence naming
what failed and the one action that would fix it. Rate limiting and backoff show
`nextAttemptAt` as a relative time that updates at least every 15 s while the
panel is open, so it is never static stale text.

**SPEC-AMD-4 (2026-09-07):** "rate limiting and backoff" is the CONDITION for
drawing the countdown, not merely the motivation for its format. It is shown
only while the scheduler is in `retry-wait`, and is absent on a healthy
schedule.

UX-008 (keyboard): the bar item is focusable and activates with Enter/Space.
Inside the panel, Tab cycles Refresh and Open UniFi, Enter activates, Escape
closes and returns focus to the bar item.

UX-009 (insecure TLS): when the committed configuration sets
`allowInsecureTls`, the panel shows a persistent, non-dismissible warning row
for the whole session. This is driven by `meta.allowInsecureTls` in the envelope
(DATA-006a), which is present on **both** success and failure envelopes, so the
warning appears even when no batch has ever succeeded.

UX-010 (plain-HTTP dashboard): launching a `http://` dashboard URL sets a
warning state that is rendered before the launch occurs.

UX-011 (multi-monitor): every monitor's widget shows identical state, and a
refresh triggered from any panel updates all of them.

## 8. Business Rules
BIZ-001: Read-only. The helper enforces a GET-only method allowlist and a
six-route allowlist (`api-contract.md`). No other route may be constructed.

BIZ-002: Counts are reported only when provably complete. Incomplete pagination
yields `partial_response` for a required collection, or a `null` count plus a
warning for an optional one — never a smaller-but-plausible number and never
zero.

BIZ-003: Missing optional metrics are `null`/unknown. Zero is reserved for a
value the controller actually reported as zero.

BIZ-004: Collections are explicitly split into required and optional, because a
device-health indicator must not be disabled by an unavailable throughput
metric:

| Route | Class | Failure behaviour |
|---|---|---|
| `/v1/info` | **required** | batch fails |
| `/v1/sites` | **required** | batch fails |
| `/v1/sites/{id}/devices` | **required** | batch fails |
| `/v1/sites/{id}/clients` | optional | `counts.clients = null` + warning |
| `/v1/sites/{id}/devices/{id}/statistics/latest` | optional | metrics `null` + warning |
| `/v1/sites/{id}/devices/{id}` | optional | that device's `detail` `null` + warning |

**Amended by SPEC-v1.1-browse.md §4 (2026-09-07).** The third optional row was
`/v1/sites/{id}/wans`, whose failure omitted WAN names and raised
`wans_unavailable`. The route returns `{id, name}` and nothing else — confirmed
against hardware at Phase 12a — so it could never contribute to DATA-006, and
DEV-5 was resolved by dropping it. Route 6 replaced it; the table still has
three required rows and three optional ones.

A batch is successful when every **required** collection completed and passed
every DATA-009 invariant. An unsuccessful batch does not replace the last
complete snapshot. A successful batch with optional gaps **does** replace it,
carries warnings, and renders the missing values as unknown.

BIZ-005: Health is derived only from a complete fresh snapshot. Transport
failure to reach the controller is a *plugin* failure state, not a *site down*
verdict, and is never rendered as red site health.

BIZ-006: Exactly one site is displayed. A controller with several sites requires
`siteId` to be committed; discovery exists to help choose it, not to aggregate.

## 9. Data / API / Integration Contracts

### DATA-001: Configuration ownership
Each setting has exactly one authoritative owner.

Omarchy inline widget settings, stored inline on the plugin's `shell.json`
layout entry — non-secret, presentation and scheduling only. Per HC-1 the
manifest `schema` and `defaults` blocks are authored as forward-looking
declaration only: 4.0.2 renders no settings form and merges no defaults, so
every default below is applied in QML through `setting(name, fallback)` and the
README documents these as a `shell.json` edit or an
`omarchy shell setBarWidget` call.

| Key | Type | Default | Range |
|---|---|---|---|
| `refreshIntervalSec` | integer | 30 | 15–3600 |
| `compactMetric` | enum | `none` | `none`, `clients` |
| `dashboardUrl` | string (URL) | derived from `apiRoot` host | `http` or `https` only |

Plugin configuration at `~/.config/omarchy-unifi/config.json` — non-secret
controller and transport values, read by the helper only:

| Key | Type | Default |
|---|---|---|
| `apiRoot` | string | — |
| `siteId` | string (uuid) | absent during discovery |
| `customCaPath` | string | absent |
| `allowInsecureTls` | boolean | `false` |

Credential at `~/.config/omarchy-unifi/api-key` — the API key and nothing else.

DATA-002: The singleton service reads settings from the canonical `gaius-codius.unifi`
entry in the injected shell's bar layout and re-evaluates when that layout
changes. *Amended 2026-09-10 (SPEC-AMD-11):* on Omarchy 4.0.3 the layout is
`shell.barConfig.layout`. The service declares `property var shell` and binds
`shell.barConfig` so a `syncPluginApis` copy re-evaluates without replacing
`shell`; `barConfigChanged` is the matching signal (`PluginShellApi`).
`shell.shellConfig` / `shellConfigChanged` is the 4.0.2 ShellRoot shape; the
service accepts either. It accepts no configuration pushes from per-monitor
widgets. Duplicate layout entries are compared on **service-consumed settings
only** — currently just `refreshIntervalSec`. Duplicates that differ only in
presentation settings such as `compactMetric` are accepted, with the left-most
entry in left → center → right order winning for service purposes; each widget
still renders its own presentation settings. Only a conflict in a
service-consumed setting produces `configuration_conflict` and suspends polling.

DATA-002b: `setting(name, fallback)` is a `Ui/BarWidget` / `Ui/Panel` base-class
helper and is available only to the **widget**, which uses it for presentation
settings. The **service** is not injected `settings` at all (DATA-003), so it
resolves `refreshIntervalSec` itself by locating the `gaius-codius.unifi` entry in
the injected shell's bar layout (`shell.barConfig.layout` on 4.0.3;
`shell.shellConfig.bar.layout` on 4.0.2) and applying the same defaulting and
validation rules. The two paths must produce identical values for any shared key;
a test asserts this.

DATA-002a: Every inline setting is **type-validated**, not merely
presence-checked, because HC-1 guarantees no upstream validation. A value that
is not an integer (including the string `"30"`), or that is `NaN`, non-finite,
or out of range, falls back to the documented default and raises a warning. A
`compactMetric` outside its enum falls back to `none`. A non-string or
scheme-invalid `dashboardUrl` is treated as unset. A timer interval is never
computed from an unvalidated value.

DATA-003: Omarchy assigns `shell`, `manifest`, `omarchyPath`,
`barWidgetRegistry`, and `pluginRegistry` **after** constructing the service
object, so the service initializes from `onShellChanged` / `onManifestChanged`
(deferred with `Qt.callLater` where needed), never from an assumption that those
properties exist during `Component.onCompleted`. `settings` is **not** injected
into a service.

DATA-003a: A bar widget receives **only** `bar`, `moduleName`, and `settings`.
It reaches the singleton through `bar?.shell?.serviceFor("gaius-codius.unifi")`, with
optional chaining, because `bar` is null on the first frame. A null result
renders REQ-013b.

DATA-003b: Per HC-3, the service instance exists only while `gaius-codius.unifi`
appears in `bar.layout`. Removing the widget from the bar destroys the service.
The service releases its helper process, all timers, the watchdog, and its IPC
handler in `Component.onDestruction`, emitting one log line naming each released
resource so teardown is observable (AC-028).

DATA-003c: The service locates its own installed directory from
`manifest.__sourceDir` when the injected manifest still carries it, and otherwise
from `Qt.resolvedUrl()` on the service's own file, converted from a `file://`
URL with proper percent-decoding so a path containing spaces or `%` still
resolves. *Amended 2026-09-10 (SPEC-AMD-11):* Omarchy 4.0.3's scanner still
stamps `__sourceDir` (`PluginRegistry.qml:589`), then `publicPluginManifest`
deletes it before injection (`shell.qml:320`). The live path is therefore
`Qt.resolvedUrl`. The helper is launched by absolute path as an argv vector. If
neither mechanism yields a readable helper path, the service reports
`helper_unavailable`.

DATA-004: The three configuration files form **one committed set**.
`commit.json` holds a monotonically increasing `commitGeneration` plus SHA-256
digests of the exact `config.json` and `api-key` bytes. `scripts/configure`
atomically replaces the configuration and credential, fsyncs them and their
directory, then atomically replaces and fsyncs `commit.json` last as the commit
point. Before any network request the helper opens all three files through the
validated directory descriptor, reads each once, and verifies both digests
against the captured commit. A mismatch is reported as `uncommitted` and sends
no request.

DATA-004b: The digests are computed over the **exact raw file bytes**, before
any whitespace trimming. `scripts/configure` and the helper must agree on this
or verification fails permanently: writing `api-key` as `sk-abc\n` and hashing
the trimmed `sk-abc` on one side and the raw bytes on the other produces a set
that can never validate. The credential is trimmed (SEC-004) only *after* its
raw-byte digest has been verified, in memory.

DATA-004c: The helper reads `config.json` and `api-key` exactly **once**, at
batch start, verifies their digests, and then uses those **captured in-memory
bytes** for every request in the batch. It never re-opens or re-reads either
file mid-batch. A batch is many HTTP calls across several paginated
collections; re-reading between page 1 and page 2 of `/clients` while
`scripts/configure` is replacing the files would send controller A's URL with
controller B's key. Descriptor-safe opening does not close that window once the
descriptors are released — capture-once does.

DATA-004a: `scripts/configure` takes an exclusive `flock` on the configuration
directory for the whole of its read-modify-commit sequence, so two concurrent
invocations cannot interleave their renames and strand a permanently mismatched
digest set.

DATA-005: Helper protocol. Every invocation emits exactly one bounded JSON
object on stdout, `protocolVersion` integer `1`. The helper bounds its own
stdout to 256 KiB by construction; the service treats a larger buffer as
`oversized_response`. Success:
`{protocolVersion, ok: true, nonce, attemptedAt, observedAt, meta, data, warnings, error: null}`.
Failure:
`{protocolVersion, ok: false, nonce, attemptedAt, observedAt: null, meta, data: null, warnings, error: {...}}`.

DATA-005b: The helper bounds its own **stderr** to 4 KiB and writes only
sanitized text there. The service caps the stderr it retains at 8 KiB, discards
the remainder, and never parses, logs verbatim, or displays it. Without an
explicit bound, a helper emitting a large traceback containing a request header
would pull that material into the shell process and into any QML log — and
REQ-017b makes the service wait for the stderr stream, so an unbounded stream is
also a hang. Exceeding the bound raises a warning and marks the batch
`internal`.

DATA-005a: The service passes a fresh random `nonce` in the helper's argv on
every launch, and the helper echoes it verbatim. **The service accepts an
envelope only if the nonce matches the one it issued for that batch.** This is
what makes generation checking survive HC-8's asynchronous termination *and*
plugin hot-reload (which destroys and recreates the service, resetting any
in-memory counter): a nonce issued by a previous service instance can never
match a new one's.

DATA-006: `data` normalizes to:
`site {id, name}`,
`wan {status, uptimeSec|null, downloadBps|null, uploadBps|null}`,
`gateways [{id, name, model, state, class, uptimeSec|null, downloadBps|null, uploadBps|null}]`,
`counts {clients|null, devicesTotal, offlineTotal, byClass{...}, gateways{...}, switches{...}, accessPoints{...}}` where each role object is `{online, transitional, down, impaired, unknown}`,
`offlineDevices [{id, name, model, state, class}]` (bounded to 10; see REQ-010),
`applicationVersion`.
`wan.latencyMs` and `wan.packetLossPct` are **absent from the model**, not
present-and-null, because no supported API version can populate them.

DATA-006b: `counts.byClass` is a **unique-device partition** of the snapshot
over the five REQ-000 classes: `{online, transitional, down, impaired,
unknown}`. Every adopted device is counted exactly once, including a device
whose `features` array is empty. It exists because REQ-002 rule 4 asks a
question about unique devices that the per-role objects cannot answer — those
double-count by design (REQ-009) and omit featureless devices — and because
`offlineTotal` is `down + impaired` only and so cannot isolate the `unknown`
class. Two invariants are enforced and are checked by the service on every
success envelope (DATA-008):

```
sum(byClass) == devicesTotal
byClass.down + byClass.impaired == offlineTotal
```

`byClass` is a partition; the role objects are deliberately **not** one. That
contrast is what AC-025's `roleCountsAreNotAPartition` flag communicates to the
panel.

DATA-006a: `meta` is present on **both** envelope shapes, so the UI can render
transport facts even when no batch has succeeded:
`{commitGeneration|null, apiRootHost|null, siteId|null, allowInsecureTls|null, customCaInUse|null, helperVersion}`.

**`meta` itself is always present as an object, and every field except
`helperVersion` is nullable.** Every field other than `helperVersion` derives
from the committed configuration, and the two failure kinds that most need a
`meta` — `unconfigured` and `uncommitted` — are precisely the cases where that
configuration could not be read. A nullable field with an unconditional
container gives the validator one rule instead of two and removes the shape
ambiguity in which the Python producer and the JS consumer would otherwise
drift. `ViewModel` renders a null field as "unknown" rather than omitting the
row, so UX-009's insecure-TLS warning still appears whenever
`allowInsecureTls` was in fact readable.

`apiRootHost` is the host component only — never the full URL, never userinfo.
This is the data channel that makes UX-009 and REQ-012's dashboard fallback
implementable, since `config.json` is helper-only and QML cannot read it.

DATA-007: Error `kind` is one of `unconfigured`, `site_unselected`,
`uncommitted`, `credential`, `unauthorized`, `forbidden`, `tls`, `network`,
`timeout`, `rate_limited`, `http`, `unsupported`, `redirect`,
`configuration_conflict`, `partial_response`, `oversized_response`,
`malformed_response`, `helper_unavailable`, `internal` — nineteen kinds, each
with a distinct panel state in REQ-013. Unknown kinds map to `internal` in QML.
`network` means no HTTP response was received (DNS failure, connection refused
or reset, host/network unreachable); it is retryable and carries no
`httpStatus`. TLS verification failure is `tls`, time-budget expiry is
`timeout`, and a received HTTP status keeps its typed status kind or `http`.
Exception matching must preserve that precedence.

DATA-007a: Error fields must be mutually consistent, and an inconsistent error
object is a protocol violation rather than something the scheduler tries to act
on:
- `httpStatus` is present **only** for `unauthorized` (401), `forbidden` (403),
  `rate_limited` (429), and `http`; it is absent or null for every other kind.
- `retryAfterSec` is present **only** for `rate_limited`.
- `retryable` must equal the kind's class per REQ-019/REQ-020/REQ-020a; a
  mismatch is rejected.
An envelope claiming `kind: "credential"` with `httpStatus: 429` is therefore
rejected as `malformed_response`, not acted on as a rate limit.

DATA-008: Protocol validation. `protocolVersion` must be integer `1`. stdout
must parse as exactly one JSON value followed only by whitespace, within the
256 KiB bound. Both shapes require the issued `nonce`, a `meta` object, and an
RFC 3339 UTC `attemptedAt` ending in `Z`, at most 64 characters, no more than
five minutes before service launch and not after receipt. `ok: true`
additionally requires exit status zero, `error: null`, an `observedAt` with
`attemptedAt <= observedAt <= receiptTime`, and a `data` object that **satisfies
the DATA-006 schema** — `site.id`, `site.name`, `wan.status` in its domain, all
five count buckets present per role, `counts.byClass` present with all five
classes, `devicesTotal` and `offlineTotal` non-negative integers, and
`offlineDevices` an array. The two DATA-006b arithmetic invariants
(`sum(byClass) == devicesTotal` and
`byClass.down + byClass.impaired == offlineTotal`) are checked here and a
violation is rejected. A success envelope with
`data: {}` is rejected. `ok: false` requires a non-zero exit, `data: null`,
`observedAt: null`, and a DATA-007a-consistent error. Warnings, strings,
numbers, arrays, and nested object sizes are all bounded. `ok` must be the JSON boolean `true` or `false` — the service tests identity
against the boolean, never JavaScript truthiness, so `1`, `"true"`, or a missing
`ok` are rejected rather than accepted as success. `httpStatus` may be an
integer, `null`, or absent; for kinds that forbid it (DATA-007a), `null` and
absent are both accepted and an integer is rejected, so a legitimate `network`
failure that omits the field is never misread as a protocol violation.

Contradictory shapes,
invalid or timezone-less timestamps, unknown versions, success paired with
process failure, multiple JSON values, or trailing non-whitespace are protocol
errors. **Every QML-side protocol rejection is published as the named kind
`malformed_response`**, which REQ-020a makes retryable — protocol rejection is
never an unnamed state, never silently leaves the previous snapshot in place
without a failure overlay, and never throws. Protocol timestamps are display and audit data only; the service uses
its own monotonic clocks for deadlines.

DATA-008a: If `attemptedAt` is rejected **only** because it precedes the
recorded service launch time by more than five minutes — the signature of a
backwards wall-clock correction rather than a bad helper — the service
re-baselines its launch time to the current wall clock **once** and retries the
batch. A second consecutive such rejection is a genuine protocol error. Without
this, a laptop resuming with a 40-minute-slow RTC would reject every batch
permanently until the shell restarted.

DATA-009: Pagination. Request `limit=200`. Validate every page before
accumulation: returned `offset` equals the requested offset, `count` equals
`data.length`, `0 <= count <= limit`, non-terminal pages make positive progress,
records carry unique stable IDs, and `totalCount` is non-negative and stable
across pages. Advance by the validated count, never by untrusted response
arithmetic. Enforce maximum page count and maximum total decoded bytes.

DATA-009b: A genuinely empty collection is distinguished from a premature empty
page by `totalCount`, not by `count` alone. A first page of
`{offset: 0, limit: 200, count: 0, totalCount: 0, data: []}` is a **valid,
complete, empty** result and drives UX-005's "no adopted devices"; `count == 0`
while `offset < totalCount` is premature and is an error. Treating every
`count == 0` as premature would make a legitimately empty site permanently
unable to produce a successful batch.

DATA-009a: The stated invariants do not detect **offset drift at constant
cardinality** — if one record is deleted and another appended between two page
requests, every invariant above still passes while one record is silently
skipped. Each paginated collection therefore ends with a re-request of page 0
and compares its ID set and `totalCount` against the first read. A mismatch
retries the whole collection once from offset zero; a second mismatch is
`partial_response` for a required collection, or a `null` count plus a warning
for an optional one. A terminal result is complete only when the number of
unique accumulated records equals the terminal `totalCount` **and** this
re-read check passes.

DATA-010: The service — not the bar widget (REQ-014a) — registers an IPC target
`gaius-codius.unifi` exposing a `reload` method and a `status` method. `status` returns
the serialized snapshot and scheduler state, and exists so that AC-003, AC-019,
AC-028 and AC-032 have an observable channel rather than depending on visual
inspection.

DATA-010a: The `reload` handler must return within the 2 s IPC timeout and must
**never** block waiting for a process to exit. It marks the current batch
abandoned, resets state, schedules the new batch via `Qt.callLater`, and returns
immediately. A handler that waited for termination would time out on every
configuration change and be misreported as a failure.

DATA-010b: `scripts/configure` invokes `omarchy shell gaius-codius.unifi reload`
**without** `-q`. Per HC-5 exit status cannot classify the outcome, so configure
captures stderr and classifies on the exact message:

| stderr | Meaning | configure exit |
|---|---|---|
| (none, exit 0) | delivered and acknowledged | 0, reported as applied |
| `omarchy-shell is not running` | no shell | 0, reported as deferred to next shell start |
| `Target not found.` | service not loaded — the widget is not on the bar | 0, reported as deferred until the widget is added |
| `omarchy-shell is not responding` | IPC timeout | non-zero |
| `omarchy-shell is not ready` | shell still starting | non-zero |
| `Function not found.` | method missing / version mismatch | non-zero |
| any other | unknown failure | non-zero |

`Target not found.` is a **success** case: per HC-3 the service does not exist
until the widget is placed on the bar, so it is the expected outcome when a
first-time user configures before adding the widget. Treating it as a failure
would make correct setup report an error. Every non-zero path preserves the
committed files. A `--commit` mode revalidates hand-edited files, writes a new
commit marker, and performs the same acknowledged reload.

DATA-011: A valid reload increments the service generation, issues a new nonce,
marks any active batch abandoned, clears retry state and the cached snapshot,
renders `reconfiguring` (UX-004), and starts a new batch without waiting for the
old process. Every completion handler compares the envelope's `nonce` against
the one currently issued and discards non-matching output.

DATA-011a: There is **no directory watcher**. The earlier design named one, but
no host API for watching an arbitrary directory is citable in
`host-contract.md`, and R1a forbids uncited host APIs. It is unnecessary in any
case: every batch re-reads and re-verifies the committed set before sending a
request (DATA-004), so a configuration change is picked up no later than the
next poll even if the IPC reload never arrives. Acknowledged IPC is the fast
path; the per-batch commit check is the guaranteed path.

DATA-012: Site selection. When the committed configuration has no `siteId`, the
helper lists sites. Exactly one site → auto-select it, proceed, and emit a
warning naming the selected site. More than one → `site_unselected`, with the
discovered `{id, name}` pairs carried in `warnings` so UX-006a can list them.
Zero sites → `unsupported`.

## 10. Security / Privacy / Permissions
SEC-001: The API key never appears in Git, `manifest.json`, QML, `shell.json`,
environment variables, command-line arguments, logs, exceptions, stdout, stderr,
test output, or UI messages. The service invokes the helper with an argv vector,
never composed shell text; the helper reads the credential itself. The `nonce`
in argv is a random batch identifier and carries no secret material.

SEC-002: `~/.config/omarchy-unifi` must be a real directory owned by the current
user and not group/other-writable; `scripts/configure` creates it mode `0700`.
`config.json`, `api-key`, and `commit.json` must be non-symlinked regular files
owned by the current user with **no group or other permission bits at all**
(`mode & 0o077 == 0`), created mode `0600`. Forbidding only *writable* bits
would leave a `0644` credential — world-readable — passing validation, so the
check is on read as well as write.

SEC-003: The directory and all three files are opened and validated **by
descriptor** (`dir_fd`, `O_NOFOLLOW` where available, then `fstat`) so
validation and reading refer to the same objects. Reads are bounded before
parsing: 64 KiB `config.json`, 4 KiB `api-key`, 8 KiB `commit.json`.

SEC-004: After the bounded read and whitespace trim, the credential must be
1–4096 visible ASCII bytes (`0x21`–`0x7e`). Embedded whitespace, CR, LF, NUL,
and every other control or non-ASCII byte are rejected before a header is
constructed, so no library exception can echo secret material.

SEC-005: TLS verification is on by default. `allowInsecureTls` is an explicit
opt-in that raises a persistent warning (UX-009).

SEC-006: A configured `customCaPath` must be a regular, non-symlinked PEM file
of at most 1 MiB, owned by the current user or root, not group/other-writable,
with no untrusted-writable parent path. It is opened by the same
descriptor-safe pattern, read once, decoded, and the **captured contents** are
passed to `SSLContext.load_verify_locations(cadata=...)`. A pathname is never
validated and then handed to the SSL library to reopen.

SEC-007: **The helper must not call `ssl.create_default_context()`.** That
function reads `SSLKEYLOGFILE` from the environment and assigns
`context.keylog_filename` *during construction* — verified against the CPython
3.14 source, where an unwritable path raises `OSError` from inside the
constructor itself. Clearing `keylog_filename` afterwards is therefore too late:
the key-log file has already been created and opened. The helper instead
constructs `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` directly and sets
`check_hostname`, `verify_mode`, and `verify_flags` explicitly, so
`keylog_filename` is never assigned at all.

This applies to **every** context the helper creates, including the
`allowInsecureTls` path — that context is built by the same constructor with
verification disabled, never by `create_default_context()`. The helper must also
never let `urllib` build an implicit default context: every HTTPS call passes an
explicitly constructed context. A single unguarded context anywhere would write
session secrets for a credential-bearing request to an inherited
`SSLKEYLOGFILE`.

SEC-007a: The helper neutralizes inherited TLS environment before creating any
context: `SSLKEYLOGFILE`, `SSL_CERT_FILE`, and `SSL_CERT_DIR` are removed from
`os.environ`. `SSL_CERT_FILE` and `SSL_CERT_DIR` are load-bearing for security,
not merely hygiene — OpenSSL honours them when default trust is loaded, so an
inherited `SSL_CERT_FILE` pointing at a writable attacker CA would let a
man-in-the-middle receive the `X-API-Key` while `allowInsecureTls` is still
`false`. Trust is loaded **explicitly** in every case: the captured custom CA
when configured, otherwise `load_default_certs()` after the environment has been
cleaned.

SEC-008: Every HTTP 3xx is rejected before a new request is constructed.
`X-API-Key` is never forwarded across a redirect and HTTPS→HTTP downgrade is
never accepted. Redirect bodies are closed without unbounded reads and reported
as a typed, sanitized `redirect` error.

SEC-009: The helper rejects unknown URL schemes and credentials embedded in
URLs. API traffic is HTTPS only. The dashboard launcher accepts only `http` or
`https`, rejects URL userinfo, and warns visibly for plain HTTP.

SEC-010: Compressed and decoded response-size limits, bounded pagination,
defensive JSON parsing, and normalized type/range validation apply to every
response. Helper stderr and error messages are sanitized, length-limited, and
never include request headers, credentials, or the full `apiRoot`.

SEC-013: Every value interpolated into a route — `siteId` and `deviceId` — is
validated against a **strict canonical UUID pattern**
(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
before use, and every path segment is percent-encoded when the URL is built. A
"uuid" type label is not a closed character set: a hand-edited
`"siteId": "x/../../v1/hotspots"` would otherwise build
`.../v1/sites/x/../../v1/hotspots/devices` and send `X-API-Key` to a route
outside the six-route allowlist, defeating BIZ-001 entirely. The constructed URL
is additionally re-checked against the allowlist after assembly, and any path
traversal, empty segment, or host change is rejected as `internal`.

SEC-011: No real controller response containing private data is committed.
Fixtures are synthetic or redacted, with no real MAC addresses, IP addresses,
client names, site names, or UUIDs traceable to a real deployment.

SEC-012: Uninstall removes the plugin directory but **not**
`~/.config/omarchy-unifi/`, which still holds the API key. The README's removal
section must instruct the user to delete that directory and to revoke the API
key in the UniFi console, since deleting a local copy does not invalidate the
credential.

## 11. Compatibility / Migration
Backward compatibility: greenfield; nothing to preserve. `protocolVersion` and
the manifest `schemaVersion` are the forward-compatibility hinges.

Runtime dependency: **Python 3.9 or newer**, standard library only, no pip
packages. The floor is set by `dir_fd`/`O_NOFOLLOW` descriptor operations and
`ssl.SSLContext.keylog_filename`. Omarchy's plugin installer does not install
runtime packages, so `scripts/configure` checks for `python3`, verifies the
version, and the README documents the dependency. A missing or too-old
interpreter is reported as `helper_unavailable` with an actionable message, not
as `internal`.

Repository layout: the repository root **is** the plugin folder —
`manifest.json`, the QML entry points, and `helper/` sit at the root, because
`omarchy plugin validate` requires `manifest.json` at the folder root and
`omarchy plugin add` clones the repository directly into
`~/.config/omarchy/plugins/<id>/`. `docs/`, `tests/`, and `PLAN.md` are
therefore shipped alongside the plugin; they are inert, and the symlink
prohibition (HC-1 rules) applies to the whole tree, so the `qmllint` import root
(HC-12) must live outside the repository.

Supported matrix: Omarchy 4.0.3-1 / Quickshell 0.3.1 only, until a further
release passes the §12 checks. *Amended 2026-09-10 (SPEC-AMD-11): originally
4.0.2-1.* UniFi Network compatibility is recorded as a
tested-version matrix; an unsupported version or a missing required capability
produces an explicit `unsupported` error rather than being misreported as an
authentication or connectivity failure.

Rollout: installed as a **user** plugin under
`~/.config/omarchy/plugins/gaius-codius.unifi/`. No file under `/usr/share/omarchy/` is
edited and no symlink is relied upon. Per HC-3, the plugin does nothing until
the widget is placed on the bar — that is what instantiates the service.

Rollback: `omarchy plugin disable gaius-codius.unifi`, then remove the plugin
directory. This does **not** remove `~/.config/omarchy-unifi/`, which still
contains the API key; the README's removal section instructs the user to delete
that directory and to **revoke the API key in the UniFi console**, since
deleting a local copy does not invalidate the credential (SEC-012).

## 12. Acceptance Criteria

Each criterion carries a verification tier:
**AUTO** — runs locally with no graphical session and no controller.
**LIVE** — needs a running Omarchy session (`quickshell -p`, HC-11) or a real
controller; scripted, but not headless.
**MAN** — irreducibly human; the criterion states what the observer must see.

### Packaging and host integration
AC-001 (AUTO): `omarchy plugin validate <repo-root>` exits 0. Additional
Omarchy versions are a release-gate checklist item in §11, not a criterion.

AC-002 (LIVE): `sha256sum` over every file under `/usr/share/omarchy/` is
identical before and after install, and `omarchy plugin list --json` reports
`gaius-codius.unifi` with a `sourceDir` under `$HOME`. Requires user approval per §15.

AC-003 (LIVE): On a session with ≥2 outputs and `gaius-codius.unifi` on the bar,
`omarchy shell gaius-codius.unifi status` returns exactly one service instance id and
`serviceInstanceCount == 1`; `pgrep -fc unifi_status.py` never exceeds 1 when
sampled every 200 ms across three refresh cycles; after one
`omarchy shell gaius-codius.unifi reload`, `lastSuccessAt` advances exactly once, not
once per monitor.

AC-004 (LIVE): With a helper stub that sleeps 60 s, the service publishes
`error.kind == "timeout"` between 30.0 s and 31.5 s after batch launch,
**without** having observed `onExited`; the watchdog is armed exactly once for
that batch and disarmed on completion; the next scheduled batch launches on the
following tick and completes normally. The abandoned PID is recorded as
known-abandoned. This criterion deliberately does **not** assert "no orphan
process", which HC-8 and R6 make unachievable.

AC-026 (LIVE): A minimal plugin staged at `~/.config/omarchy/plugins/` imports
`qs.Commons` and `qs.Ui` and instantiates `Style`, `Color`, `BarIconButton`,
`Panel`, and `KeyboardPanel`, logging `PROBE_OK`; the shell log contains no
`Failed to import` or `is not a type` warning for it. HC-10 has already settled
the mechanism, so this is a confirmation, not a gate. Requires user approval
per §15.

AC-027 (AUTO): `find <repo-root> -name .git -prune -o -type l -print` outputs
nothing, and `omarchy plugin validate` exits 0.

AC-028 (LIVE): After removing `gaius-codius.unifi` from `bar.layout`:
`omarchy shell gaius-codius.unifi reload` prints `Target not found.`;
`pgrep -f unifi_status.py` is empty for 3× the refresh interval; the shell log
contains one `Component.onDestruction` line naming each released resource (poll
timer, watchdog, wake timer, process, IPC handler) and no later line from that
generation. Re-adding it yields `generation == previous + 1` and a first
successful snapshot within one interval.

### Health model
AC-020 (AUTO): The health function is **total and exclusive** — a table-driven
test enumerates the cross product of {0,1,2 gateways} × {each of the five
REQ-000 classes} × {fresh, stale} × {snapshot, no snapshot} and asserts exactly
one colour is returned for every cell, with no cell falling through.

AC-021 (AUTO): Red requires ≥1 gateway **and** every gateway in
`{OFFLINE, CONNECTION_INTERRUPTED}`. A fixture with zero gateways — no device
declaring the feature **and** none reporting an off-LAN address (DEV-6) — never
returns red and reports `wan.status == "unknown"`. A snapshot-less
transport failure returns the previous snapshot's colour, or grey, never red.

AC-022 (AUTO): A fixture with one `ONLINE` gateway and one `UPDATING` access
point returns **green** and lists the AP as transitional, not offline. Changing
that AP to `ISOLATED` returns **amber**. Changing it to `REBOOTING` (an
unrecognized value) returns **amber** and emits a warning naming the value.

AC-023 (AUTO): One gateway `ONLINE` and one gateway `OFFLINE` returns amber, not
red and not green. All gateways `OFFLINE` plus one switch in an unrecognized
state returns red, confirming red precedes amber.

AC-024 (AUTO): A fixture with zero adopted devices returns grey and the `empty`
panel state, distinct from any failure state.

AC-025 (AUTO): A device with `features: ["gateway","switching","accessPoint"]`
plus two AP-only devices yields `gateways.online == 1`, `switches.online == 1`,
`accessPoints.online == 3`, `devicesTotal == 3`; the role rows sum to 5 ≠ 3, the
model exposes `roleCountsAreNotAPartition == true`, and the panel label carries
the unique total.

AC-033 (AUTO): **Every dual-use `.js` module at the repository root** contains
no reference to `Qt.` or `qs.`, no `.pragma library`, no `.import`, and no
mutable top-level binding — enforced by a lint gate over the whole glob, not a
single filename — so the entire health, staleness, scheduler, protocol,
settings, and formatting layer executes under `node --test`. The glob form is
required by HC-16: dual-use files cannot import one another, so the layer is
necessarily several files rather than one.

AC-034 (AUTO): `grep -nE '#[0-9a-fA-F]{3,8}' *.qml` finds no colour literal, and
every health level maps to one of the four REQ-001a theme expressions.

### Scheduler and staleness
AC-005 (AUTO): With a stub helper returning success after 200 ms and
`refreshIntervalSec = 15`, batch N+1 launches 15.0–16.0 s after batch N
**completes** (REQ-023a). With the stub delayed to 20 s, the ticks at 15 s and
30 s produce zero additional launches, and the snapshot updates within 100 ms of
the stream+exit join.

AC-006 (AUTO): Decreasing the interval brings the next attempt and `staleAt`
forward. Increasing it above `intervalAtCompletion` moves neither. Reverting a
decrease restores the completion-based `staleAt` rather than leaving it
ratcheted. Neither direction shortens an active retry or `Retry-After` deadline.
A decrease that places `staleAt` in the past transitions to grey immediately.

AC-035 (AUTO): The backoff delay never falls below the configured interval —
with `refreshIntervalSec = 3600` and a 429, the computed delay is ≥ 3600 s, not
900 s. The fatal-error delay is likewise `max(interval, 300 s)`.

AC-036 (AUTO): Every one of the nineteen DATA-007 kinds maps to exactly one
retry class (REQ-019 / REQ-020 / REQ-020a) — a table test asserts no kind is
unclassified. Switching class between consecutive failures never shortens
`nextAttemptAt`.

AC-037 (AUTO): A failed manual refresh does not advance the backoff exponent and
does not move `nextAttemptAt` later; ten consecutive failed manual refreshes
leave the automatic schedule unchanged.

AC-038 (AUTO): A manual refresh is refused while polling is suspended by
`configuration_conflict`, `uncommitted`, or `site_unselected`, and launches no
helper.

AC-039 (AUTO): With `staleAt = T+90` and `nextAttemptAt = T+900`, the wake timer
fires at T+90, greys the widget, and **rearms** for T+900; the retry runs. Past
deadlines are excluded from selection, and with neither deadline present the
timer is disarmed.

AC-040 (AUTO): The first attempt after a valid configuration runs immediately,
not after one interval. A first attempt failing with `network` retries every 2 s
for at most 30 s before entering the normal schedule.

AC-041 (AUTO): With a virtual clock, advancing wall time past `staleAt` while
monotonic time is frozen — the system-suspend signature — greys the widget, and
an advance greater than one interval marks the schedule overdue.

AC-008 (AUTO): `staleAt == lastSuccessAt + max(2 × min(intervalAtCompletion,
currentInterval), 90 s)`. A failed attempt leaves `snapshot.observedAt`
byte-identical while `lastAttemptAt` advances. `healthLevel(now)` returns the
success level for `now < staleAt` and grey for `now >= staleAt`, including when
`nextAttemptAt > staleAt`.

### Protocol and data
AC-007 (AUTO): Against a 413-record 3-page fixture, `devicesTotal == 413` and
each role count matches its precomputed value. Against a fixture whose page 2
reports `count = 200` with `data.length = 199`, the batch yields
`partial_response`, leaves counts unknown, and does not replace the prior
snapshot. One fixture and one assertion per DATA-009 invariant.

AC-042 (AUTO): The DATA-009a re-read check detects offset drift at constant
cardinality — a fixture that deletes one record and appends another between
page 0 and page 1 is caught, retried once, then reported `partial_response` for
a required collection.

AC-043 (AUTO): `{offset:0, limit:200, count:0, totalCount:0, data:[]}` is
accepted as a complete empty collection and drives UX-005; `count == 0` with
`offset < totalCount` is rejected as premature.

AC-044 (AUTO): A success envelope with `data: {}`, with a missing count bucket,
with a missing `counts.byClass`, with a negative `devicesTotal`, or with
`wan.status` outside its domain is **rejected**. An envelope violating either
DATA-006b invariant — `sum(byClass) != devicesTotal`, or
`byClass.down + byClass.impaired != offlineTotal` — is rejected. `ok: 1`,
`ok: "true"`, and a missing `ok` are all rejected.

AC-045 (AUTO): An error object violating DATA-007a — for example
`kind: "credential"` with `httpStatus: 429` — is rejected as
`malformed_response` rather than acted on as a rate limit. A `network` error
with `httpStatus` absent **or** `null` is accepted.

AC-046 (AUTO): Every QML-side protocol rejection publishes the named kind
`malformed_response` with a failure overlay; none throws and none leaves the
previous snapshot displayed without a failure indication.

AC-020a (AUTO): Both shapes reject an invalid `attemptedAt`; the success shape
also rejects an invalid, future-skewed, or wrongly ordered `observedAt`,
including `observedAt` later than receipt time.

AC-047 (AUTO): A batch rejected **only** for `attemptedAt` preceding service
launch re-baselines the launch time once and retries; a second consecutive such
rejection is a protocol error (DATA-008a).

AC-048 (AUTO): An envelope whose `nonce` does not match the one issued for the
current batch is discarded, including one carrying an otherwise perfectly valid
success.

AC-049 (AUTO): A helper writing 64 KiB of stderr has it capped at 8 KiB, never
parsed, never logged verbatim, never displayed; the batch is marked `internal`
with a warning.

AC-050 (AUTO): A helper that exits before its stdout collector reports is parsed
identically to one that reports before exiting — the completion join is a pure
function tested in both orders, and `join(exit_only)` yields no snapshot until
the stream arrives or the watchdog fires.

AC-051 (AUTO): An optional-collection failure (`/clients`, `statistics/latest`,
`/devices/{id}` — `/wans` until SPEC-v1.1-browse.md §4 retired it) yields a
**successful** batch with the affected value `null`, a
warning, and the snapshot replaced. A required-collection failure (`/info`,
`/sites`, `/devices`) yields an unsuccessful batch that does not replace the
snapshot.

AC-052 (AUTO): `meta` is present as an object on **both** envelope shapes,
including on an `unconfigured` or `uncommitted` failure produced before any
request was sent, where every field except `helperVersion` is `null` and the
panel renders those rows as "unknown". Where the configuration *was* readable,
each field is populated.

### Configuration and IPC
AC-009 (AUTO): A helper sends traffic only when `config.json` and `api-key`
match one descriptor-safely read commit marker. An update interrupted between
the two replacements sends no request and reports `uncommitted`.

AC-053 (AUTO): Digests are computed over raw file bytes; an `api-key` written as
`sk-abc\n` validates, and the credential is trimmed only after verification.

AC-054 (AUTO): The helper reads `config.json` and `api-key` once per batch;
replacing both files mid-batch does not change the URL or key used by later
requests in that batch.

AC-055 (AUTO): Two concurrent `scripts/configure` runs serialise on the
directory `flock` and cannot leave a mismatched digest set.

AC-010 (AUTO): `scripts/configure` classifies every DATA-010b stderr outcome
correctly against a stub `omarchy` on `PATH`. Exactly three cases exit 0 —
delivered, `omarchy-shell is not running`, and `Target not found.` Every
non-zero path leaves all three files byte-identical to their pre-run digests and
prints the classified message on stderr.

AC-056 (LIVE): The `reload` IPC handler returns within the 2 s IPC timeout even
when a 25 s batch is in flight, and never blocks on process exit.

AC-019 (LIVE): With two `gaius-codius.unifi` entries whose `refreshIntervalSec` differ,
`status` reports `configuration_conflict`, `pollingSuspended == true`, and zero
helper launches over three interval periods. With two entries differing only in
`compactMetric`, polling proceeds normally using the left-most entry.

AC-032 (LIVE): With a layout entry of `{"id": "gaius-codius.unifi"}` and no settings
keys, `status` reports `refreshIntervalSec == 30` and `compactMetric == "none"`.
A separate AUTO test asserts the service-side and widget-side default tables are
equal, and that both equal `manifest.json`'s `barWidget.defaults` (R1b).

AC-057 (AUTO): `refreshIntervalSec` values of `"30"` (string), `"abc"`, `NaN`,
`null`, `0`, and `99999` all resolve to a valid clamped integer with a warning;
no timer interval is ever `NaN`.

AC-058 (AUTO): A committed configuration with no `siteId` against a one-site
controller auto-selects with a warning; against a multi-site controller yields
`site_unselected` carrying the `{id, name}` pairs; against a zero-site
controller yields `unsupported`.

### Security
AC-013 (AUTO): `gitleaks detect` over the working tree and over full history
reports zero findings, and a repo-wide grep for a defined pattern set finds
nothing outside fixtures. A **positive control** — a temporary commit containing
a canary token — is detected by the same invocation, proving the scan is live.

AC-059 (AUTO): A fixture-content scan finds no RFC1918-external IP, no
OUI-valid MAC outside a documented synthetic range, and no UUID from a real
deployment (SEC-011).

AC-014 (AUTO): No HTTP redirect is followed and `X-API-Key` is never copied to
another URL — verified for 301, 302, 303, 307, 308, same-origin, cross-origin,
HTTPS→HTTP downgrade, redirect loops, and oversized redirect bodies.

AC-015a (AUTO): With `SSLKEYLOGFILE` set, the helper completes a request against
a local TLS stub and the key-log path **does not exist**; no code path calls
`ssl.create_default_context()` (enforced by a grep gate); every context is
constructed explicitly, including the `allowInsecureTls` one.

AC-015b (AUTO): With `SSL_CERT_FILE` pointing at an attacker CA and
`allowInsecureTls == false`, a certificate signed by that CA is **rejected**;
the helper removes `SSLKEYLOGFILE`, `SSL_CERT_FILE`, and `SSL_CERT_DIR` from the
environment before creating any context.

AC-016 (AUTO): The SEC-006 negative matrix — symlinked CA, >1 MiB CA,
group-writable CA, group-writable parent, non-owner non-root CA — each rejected;
the accepted case verifies against a stub certificate signed by that CA.

AC-060 (AUTO): A `0644` `api-key` is **rejected** (SEC-002 forbids all group and
other bits, not merely writable ones), as are wrong-owner, symlinked, oversized,
and empty credential files, and keys containing whitespace, CR, LF, NUL,
control, or non-ASCII bytes.

AC-017 (AUTO): The adapter can construct only the six allowlisted GET routes;
building any other route or any non-GET method fails a test.

AC-061 (AUTO): `siteId` values `x/../../v1/hotspots`, `../`, an empty string, a
non-UUID string, and a UUID with appended path characters are all rejected
before a request is built; every path segment is percent-encoded; the assembled
URL is re-checked against the allowlist (SEC-013).

AC-018 (AUTO): The local-console API root produces exactly
`https://<host>/proxy/network/integration/v1/<route>` for all six routes,
fixture-tested with and without a trailing slash on `apiRoot`. The cloud
connector root is **not** tested because it is out of scope for v1.

AC-011 (AUTO + MAN): The dashboard launcher calls `Qt.openUrlExternally` and no
`Process`, `execDetached`, `execArgv`, or `omarchy-launch-browser` path —
asserted by an injected stub plus a grep gate. Inputs `https://a b`,
`https://u:p@h/`, `file:///etc/passwd`, `javascript:1`, and `https://h/;reboot`
are rejected without launching. `http://h/` sets `warnPlainHttp` before the
launch occurs. *Human observes:* the warning row is on screen before the browser
appears.

AC-012a (AUTO): A stub raising `socket.gaierror`, `ConnectionRefusedError`,
`ConnectionResetError`, and `OSError(ENETUNREACH)` / `OSError(EHOSTUNREACH)`
each yields `kind == "network"`, `retryable == true`, no `httpStatus`, and a
message containing no credential and no header.

AC-012b (LIVE): With the helper failing in each of those modes for 60
consecutive batches, `omarchy shell gaius-codius.unifi status` responds within the 2 s
IPC timeout every time, the quickshell process is alive, its RSS growth is under
5 MiB, and the shell log contains no unhandled QML exception.

### User-visible behaviour
AC-062 (AUTO): The tooltip string builder returns site name, last success time,
latest attempt result, and a one-line summary for each of: never-succeeded,
fresh-success, fresh-success-with-failed-refresh, and stale.

AC-063 (AUTO): The offline-list model truncates at 10 and computes "and N more"
from `counts.offlineTotal`, not from the truncated array — a fixture with 500
down devices and a helper array bound of 200 renders "and 490 more".

AC-064 (AUTO): `formatOptional(null) == "unknown"` and `formatOptional(0) == "0"`
for `uptimeSec`, `downloadBps`, `uploadBps`, and `clients`.

AC-065 (AUTO): `manifest.json`'s `compactMetric` enum is exactly
`["none","clients"]`, the repository contains no `latency` settings value, and
`normalizeCompactMetric` maps `"latency"`, `""`, `null`, and `"clients "` to a
valid enum value.

AC-066 (AUTO): Each of the twenty-four REQ-013 states produces a distinct,
non-empty panel sentence; a manifest test asserts the state list matches
DATA-007's kinds plus the five non-error states, so a removed state fails the
build.

AC-067 (AUTO): A null service resolves to `service_unavailable` rather than a
binding error or blank widget (REQ-013b).

AC-068 (LIVE + MAN): Opening the panel on one monitor closes any panel open on
another. *Human observes:* only one panel is ever visible, and Escape always
returns focus to the bar item on the monitor that owns it.

AC-069 (MAN): Across at least three Omarchy themes, including one light theme,
all four health levels are visually distinguishable and legible.
*Human observes:* the degraded badge is visible against the bar background, and
the dim level reads as inactive rather than as an artifact.

AC-070 (LIVE + MAN): With `allowInsecureTls: true`, the panel warning row is
present in the model for the entire session and has no dismiss handler.
*Human observes:* it is still on screen after closing and reopening the panel
and after a successful refresh.

AC-071 (AUTO): Every relative-time string the panel draws recomputes at least
every 15 s while the panel is open.

**SPEC-AMD-4 (2026-09-07):** originally worded against `nextAttemptAt`
specifically. That string is now absent on the healthy path, so the criterion is
stated over all of them and measured over `lastUpdateText`, which is present
whenever a snapshot is. The bound itself is unchanged.

### Suite integrity
AC-072 (AUTO): The suite contains at least one named, executing test per
DATA-009 invariant, per DATA-007 error kind, per DATA-008 rejection class, per
DATA-010b stderr outcome, and per REQ-013 panel state. A manifest test asserts
these enumerations match the spec lists, so deleting a case fails the build.
This replaces the previous "all tests pass" criterion, which asserted nothing
about what tests exist.

## 13. Test Strategy

### Harness reality (verified, HC-11 / HC-12)
`qmltestrunner` **cannot** be used: `Quickshell.Io` is linked statically into
`/usr/bin/quickshell` and has no loadable plugin, so any `TestCase` importing
`Process`, `StdioCollector`, `IpcHandler`, or `FileView` fails to compile. The
QML integration layer therefore runs under
`quickshell -p tests/harness/runner.qml`, which requires a **live graphical
Wayland session** — it is not a headless CI target. The runner arranges its own
exit path because `Qt.exit()` warns under a bare `ShellRoot`.

`qmllint` is run as
`/usr/lib/qt6/bin/qmllint -I <root> *.qml`, where `<root>` is a directory
outside the repository containing a `qs` symlink to `/usr/share/omarchy/shell`.
Bare invocation reports only `Failed to import qs.Commons`, which reads
deceptively like a clean result.

### Layers
**`tests/test_model.js` — `node --test`, AUTO.** All pure logic: the REQ-002
health function, REQ-000 classification, REQ-022 staleness, the REQ-019/020
scheduler and backoff, DATA-008 protocol acceptance, DATA-009 pagination
invariants, and every display-string formatter. A lint gate forbids `Qt.` and
`qs.` references in `Model.js` (AC-033) so this layer stays executable outside
QML — it is the only genuinely automatable layer and therefore carries as much
of §12 as possible, including all presentation *logic* (level→token selection,
state sentence selection, truncation, unknown-vs-zero formatting), leaving only
pixel appearance manual.

**`tests/test_unifi_status.py` — `python3 -m unittest`, AUTO.** Configuration,
credential, commit-marker and CA validation with every negative case; capture-
once semantics; TLS context construction and environment scrubbing; redirect
rejection; route and method allowlists; `siteId` injection; pagination against
fixtures; size and time bounds; normalization; the full nineteen-kind error
taxonomy; envelope construction; secret-free output assertions.

**`tests/harness/runner.qml` — `quickshell -p`, LIVE.** Protocol acceptance and
rejection end to end, watchdog arm/disarm and expiry, nonce-based rejection of
late output, manual-refresh coalescing, interval rescheduling in all four
scheduler states, duplicate and conflicting layout entries, reload while idle
and in flight, IPC handler latency, and service teardown.

**`tests/test_configure.sh` — `bash`, AUTO.** Atomic commit ordering, an update
interrupted between replacements, `flock` serialisation, `--commit` mode, and
each DATA-010b IPC outcome against a stub `omarchy` on `PATH`.

**`tests/run.sh`** is the single orchestrator. It runs the AUTO layers, the lint
gates, `omarchy plugin validate`, and the secret scan, and exits non-zero on any
failure. It takes `--live` to additionally run the `quickshell` harness. This is
what "run at every checkpoint" means operationally.

### Fixtures
Synthetic only (SEC-011), covering the local-console API root, healthy /
degraded / down / empty sites, multi-page collections at the >25 and >200
boundaries, every malformed-page case, multi-feature and multi-gateway devices,
and every device `state` value including an unrecognized one.

### Manual QA (`PLAN.md` Phase 13)
The manual set is exactly AC-069, AC-070, and the human-observation halves of
AC-011 and AC-068 — everything else has an automated or scripted assertion.
Each is run across at least three themes including one light theme, on a
multi-monitor session, with the observation recorded.

## 14. Risks and Open Questions

R1 (**RESOLVED**): HC-10 — `qs.Commons` / `qs.Ui` resolution from a third-party
plugin directory. Settled empirically: Quickshell registers the config root as
an engine-global import path, and a file outside that root resolved `qs.Commons`
via `Qt.createComponent`. See `host-contract.md` §8. AC-026 downgrades to a
confirmation.

R1a: The host contract is derived by reading Omarchy's source rather than
published plugin documentation. Any API not citable with a `path:line` reference
in `host-contract.md` may not be used.

R1b: HC-1 makes the manifest settings schema decorative in 4.0.2. If a later
release starts rendering it and merging `defaults`, the QML defaults and the
manifest defaults must agree. Mitigated by AC-032's equality assertion.

R2: `/v1/info` returns only `applicationVersion`, so capability gating is
coarse version-string comparison against a tested matrix. An untested version is
`unsupported` with an explicit message rather than best-effort parsing.

R3: The API documents no `Retry-After` header and no non-200 responses, so 429
and backoff handling can only be fixture-tested until observed on a real
controller.

R4 (**RESOLVED**): A gateway-less site is now explicitly handled by REQ-002
rule 3's "at least one gateway exists" precondition and asserted by AC-021.

R5 (open, **blocking by user decision**): the real controller's `apiRoot`,
Network version, site UUID, and controller type are not known. The user has
elected to stop implementation until these are supplied, so Phase 1 is a hard
gate. *Editorial note, 2026-09-06: the user confirmed that only `PLAN.md`
Phase 12 waits for these; every earlier phase proceeds. See `DEVIATION_LOG.md`
AMD-4 and its decisions table.*

R6: `Process` termination is advisory (HC-8) and the helper's own deadline
cannot interrupt a blocking `getaddrinfo` (REQ-017c). The QML watchdog is
therefore the **outer** bound and the helper deadline the inner, best-effort
one — the reverse of the earlier claim. A helper wedged in an uninterruptible
syscall cannot be reaped at all; the service stays functional by abandoning it.

R7: DATA-009a's end-of-collection re-read detects offset drift that changes
page 0, but a compensating change entirely beyond page 0 could still slip
through on a very large, very active collection. Accepted: the failure mode is a
transiently miscounted device on a site with hundreds of devices changing state
mid-poll, and the next poll corrects it. Documented rather than mitigated
further, because full consistency would require an API transaction the
controller does not offer.

R8 (**RESOLVED**): the Omarchy palette has no green/amber/red tokens, so health
is rendered as four theme-native visual levels (REQ-001a) rather than four hues.
Confirmed by the user on 2026-09-05: theme-native levels, matching the existing
Network and Tailscale idiom. No configurable colour override ships in v1.

Resolved during spec creation: WAN latency and packet loss removed (no API
support); red defined as all-gateways-down with an explicit non-empty
precondition; multi-feature devices counted in every role; `compactMetric`
reduced to `none`/`clients`; plugin ID `gaius-codius.unifi` accepted.

## 15. Agent Autonomy Rules

Deviations are classified by the three-tier model in `DEVIATION_LOG.md`:
**Tier 1** local adaptation (log and continue), **Tier 2** plan amendment
(update the plan, log, continue), **Tier 3** stop-and-ask (produce a deviation
packet and wait for a decision).

May decide without asking:
- Python and QML file, module, class, and function structure.
- Internal naming, error-message wording, and log formatting, provided SEC-001
  holds.
- Test file organization, fixture naming, and additional cases beyond §13.
- Choice of standard-library mechanism for anything §9/§10 specifies by outcome
  rather than by mechanism.
- Bounds and constants not fixed by this spec (maximum page count, maximum
  decoded bytes, jitter magnitude), chosen conservatively and documented in
  `implementation-notes.md`.
- Panel layout, spacing, and composition within the Omarchy theme primitives.
- Reordering work inside a phase, and splitting a phase task into several.

Must stop and ask (Tier 3):
- Any change to a requirement, acceptance criterion, business rule, or security
  behaviour in this spec.
- Adding any route, HTTP method, or outbound network destination beyond the six
  allowlisted GETs.
- Any write, mutation, or action endpoint.
- Adding a runtime dependency outside the Python standard library.
- Weakening any TLS, credential, file-permission, or descriptor-safety control.
- Changing the health rules, the definition of red, the REQ-001a colour mapping,
  or the device-count semantics.
- Anything requiring real controller credentials, a real API key, or live
  traffic to the user's controller.
- Installing, enabling, or staging the plugin into `~/.config/omarchy/plugins/`,
  including for AC-002 and AC-026.
- Initializing a remote, pushing, or publishing the repository anywhere.
- Any use of a host API that cannot be cited in `host-contract.md`.

## 16. Review Log

| Date | Stage | Reviewer | Finding | Action Taken |
|------|-------|----------|---------|--------------|
| 2026-09-05 | Spec creation | API contract | `/v1/wans` and `/v1/sites` expose no status, latency, or loss | Latency and loss removed from scope; red redefined on gateway state; `compactMetric` reduced |
| 2026-09-05 | Spec creation | API contract | `features` is a set | REQ-009 counts a device in every role, plus a unique total |
| 2026-09-05 | Spec creation | API contract | Device `state` has 10 values | REQ-000 defines a five-class total classification |
| 2026-09-05 | Spec creation | Host exploration | HC-1 manifest schema inert; HC-3 service tied to bar entry; HC-5 IPC exit codes ambiguous; HC-7 signal ordering; HC-8 no kill API; bar widgets get only three injected properties | DATA-001, DATA-003a/b, DATA-010b, REQ-017a/b rewritten |
| 2026-09-05 | Spec review | Claude (requirements+risk) | Health function not total: transitional, mixed-gateway, gateway-less, and empty-site states had no defined colour | REQ-002 replaced with an ordered total decision table; AC-020 enumerates the cross product |
| 2026-09-05 | Spec review | Claude, Codex, Cursor (all three) | Backoff cap of 900 s is below the 3600 s maximum interval, so failure polls faster than success | REQ-019 cap is now `max(interval, 900 s)`; REQ-020 floor is `max(interval, 300 s)`; AC-035 |
| 2026-09-05 | Spec review | Claude, Codex, Cursor | Most error kinds had no retry policy | REQ-020a classifies every kind; REQ-020b defines class transitions; AC-036 |
| 2026-09-05 | Spec review | Claude, Cursor | Wake timer never rearmed; stale transition could stop polling entirely | REQ-024 rearms after every firing and excludes past deadlines; AC-039 |
| 2026-09-05 | Spec review | Claude, Codex, Cursor | `staleAt` monotonicity ratcheted permanently and was order-dependent | REQ-022 replaced with a single live formula over `min(intervalAtCompletion, currentInterval)`; AC-006 |
| 2026-09-05 | Spec review | Claude, Codex | Reload could not both start a new batch and preserve the one-helper invariant under HC-8 | REQ-016 restated on *authority* not process count; DATA-005a adds a per-batch nonce; DATA-011 no longer waits |
| 2026-09-05 | Spec review | Claude | Watchdog was arm-once and never generation-tagged, so it fired against the wrong batch | REQ-017a arms per batch, tags by generation, disarms on completion; AC-004 |
| 2026-09-05 | Spec review | Claude | Plugin hot-reload destroys the service, resetting any in-memory generation counter | DATA-005a's nonce survives instance replacement where a counter cannot |
| 2026-09-05 | Spec review | Claude, Codex, Cursor | `ok: true` with `data: {}` passed every stated rule | DATA-008 requires `data` to satisfy the DATA-006 schema; `ok` must be a JSON boolean; AC-044 |
| 2026-09-05 | Spec review | Codex (verified against CPython source) | `SSLKEYLOGFILE` is consumed *inside* `create_default_context()`, so clearing `keylog_filename` afterwards is too late | SEC-007 bans `create_default_context()` entirely; every context built explicitly; AC-015a |
| 2026-09-05 | Spec review | Codex | Inherited `SSL_CERT_FILE` / `SSL_CERT_DIR` could trust an attacker CA while `allowInsecureTls` is false | SEC-007a scrubs the TLS environment; AC-015b |
| 2026-09-05 | Spec review | Cursor | `siteId` interpolated into routes with no closed character set — path traversal escapes the route allowlist | SEC-013 adds strict UUID validation, percent-encoding, and post-assembly re-check; AC-061 |
| 2026-09-05 | Spec review | Cursor | SEC-002 forbade only group/other *writable*, so a `0644` API key passed | SEC-002 now requires `mode & 0o077 == 0`; AC-060 |
| 2026-09-05 | Spec review | Cursor | Commit digest vs credential trim ordering unspecified — would fail verification permanently | DATA-004b fixes digests to raw bytes, trim after verification; AC-053 |
| 2026-09-05 | Spec review | Cursor | Digest checked once but the batch makes many requests; files could be re-read mid-batch | DATA-004c mandates capture-once; AC-054 |
| 2026-09-05 | Spec review | Cursor | A `FileView` directory watcher would load the API key into shell memory | DATA-011a removes the watcher entirely; the per-batch commit check is the guaranteed path |
| 2026-09-05 | Spec review | Cursor | Unbounded helper stderr could carry a header into the shell process and hang REQ-017b | DATA-005b bounds stderr on both sides; AC-049 |
| 2026-09-05 | Spec review | Cursor | The theme has no green/amber/red tokens, so REQ-002 could not be painted without hex | REQ-001a maps four health levels onto four theme-native visual levels; AC-034; raised as R8 |
| 2026-09-05 | Spec review | Codex, Cursor | "Required collection" undefined — an optional metric could veto device health | BIZ-004 splits the six routes into required and optional with explicit failure behaviour; AC-051 |
| 2026-09-05 | Spec review | Claude, Cursor | `allowInsecureTls` had no data channel to QML, making UX-009 unimplementable | DATA-006a adds `meta` to both envelope shapes; AC-052 |
| 2026-09-05 | Spec review | Claude, Codex | Multi-gateway sites had no selection or aggregation rule | REQ-008a defines the status domain, primary-gateway selection, and a four-gateway statistics bound |
| 2026-09-05 | Spec review | Codex, Cursor | Pagination invariants missed offset drift at constant cardinality | DATA-009a adds an end-of-collection page-0 re-read; residual documented as R7; AC-042 |
| 2026-09-05 | Spec review | Cursor | A legitimately empty collection was indistinguishable from a premature empty page | DATA-009b distinguishes on `totalCount`; AC-043 |
| 2026-09-05 | Spec review | Claude | `configure` reported `Target not found.` as failure, but that is the expected first-run state | DATA-010b reclassifies it as deferred success |
| 2026-09-05 | Spec review | Claude, Cursor | Site discovery was referenced but never specified | DATA-012 and UX-006a define it; new kind `site_unselected`; AC-058 |
| 2026-09-05 | Spec review | Claude | Bar widgets could claim the IPC target before the service | REQ-014a forbids the widget from registering any target |
| 2026-09-05 | Spec review | Claude, Cursor | The service cannot use `setting()` — it is a widget base-class helper | DATA-002b defines the service's own resolution path; AC-032 |
| 2026-09-05 | Spec review | Claude (testability, machine-verified) | `qmltestrunner` cannot load `Quickshell.Io`, so `test_service.qml` was impossible | §13 rewritten around `quickshell -p`; recorded as HC-11 |
| 2026-09-05 | Spec review | Claude (testability, machine-verified) | Bare `qmllint` reports only an import failure, which reads as a clean pass | §13 fixes the invocation with a synthetic import root; recorded as HC-12 |
| 2026-09-05 | Spec review | Claude (testability) | AC-004 demanded "no orphan process", which HC-8 makes unachievable | AC-004 rewritten to assert timing and abandonment instead |
| 2026-09-05 | Spec review | Claude (testability) | "All tests pass" was a tautology, and ~30 requirements had no criterion at all | §12 rebuilt with tiers and a suite-integrity criterion (AC-072); §17 traceability matrix added |
| 2026-09-05 | Spec review | Codex | Error field combinations were unconstrained | DATA-007a defines consistency rules; AC-045 |
| 2026-09-05 | Spec review | Claude | A backwards clock step would reject every batch permanently | DATA-008a re-baselines the launch time once; AC-047 |
| 2026-09-05 | Spec review | Claude | Rollback left the API key on disk with no revocation guidance | SEC-012 and §11 rollback updated |
| 2026-09-05 | Spec approval | User | R8 colour rendering decision | Theme-native visual levels confirmed; REQ-001a is final; no colour-override setting in v1 |
| 2026-09-05 | Spec approval | User | Spec approved and frozen as **spec-v1** | §1 State set to Approved; planning begins |
| 2026-09-10 | Implementation (SPEC-AMD-11) | User | Omarchy 4.0.3 sandboxes the injected `shell` and `manifest`: PluginShellApi has `barConfig` and no `shellConfig`; `publicPluginManifest` deletes `__sourceDir` | DATA-002 / DATA-002b / DATA-003c restated against the 4.0.3 surface; §5.2 and §11 matrix now 4.0.3-1; host-contract HC-22 |
| 2026-09-05 | Planning (DEV-1) | Architecture review, user-approved | REQ-002 rule 4 asked about unique devices but `counts` could not answer it — role objects double-count and omit featureless devices, `offlineTotal` cannot isolate `unknown` | DATA-006b adds `counts.byClass` as a unique-device partition with two enforced invariants; rule 4 restated against it; AC-044 extended |
| 2026-09-05 | Planning (DEV-2) | Architecture review, user-approved | `meta` was unsatisfiable on `unconfigured`/`uncommitted` failures, where its source configuration is by definition unreadable | DATA-006a makes every field except `helperVersion` nullable with `meta` always present; AC-052 restated |
| 2026-09-05 | Planning | Host verification | Earlier HC-13 was **wrong**: one `Bar` `Item` fans out via `Variants`, so `activePopout` is global | REQ-007a is satisfied by `Ui/Panel` + `KeyboardPanel` with no implementation; the service holds no panel state; AC-068 is a confirmation |
| 2026-09-05 | Planning | Host verification (HC-16) | Dual-use QML/Node `.js` files cannot import each other or hold top-level state | AC-033 generalized from the filename `Model.js` to a glob over all root dual-use modules — a strengthening of the gate |

Rejected findings:
- *"AC-018 should test a self-hosted API root"* — the published contract defines
  only "local console" and "cloud connector"; there is no third root shape to
  test. AC-018 now covers the local console root only, and cloud is out of scope.
- *"Directory watching should be specified rather than removed"* — no citable
  host API exists for it (R1a), and the idiomatic `FileView` would pull the
  credential into shell memory. The per-batch commit check makes it unnecessary.

## 17. Traceability Matrix

| Requirement | Criteria |
|---|---|
| REQ-000 | AC-020, AC-022 |
| REQ-001 / REQ-001a | AC-034, AC-069 |
| REQ-002 | AC-020, AC-021, AC-022, AC-023, AC-024, AC-008 |
| REQ-003 | AC-022 |
| REQ-004 | AC-008, AC-046 |
| REQ-005 | AC-065 |
| REQ-006 | AC-062 |
| REQ-007 / REQ-007a | AC-068 |
| REQ-008 / REQ-008a | AC-021, AC-064 |
| REQ-009 | AC-025 |
| REQ-010 | AC-063 |
| REQ-011 | AC-038 |
| REQ-012 | AC-011 |
| REQ-013 / 013a / 013b | AC-066, AC-067 |
| REQ-014 / REQ-014a | AC-003, AC-019 |
| REQ-015 | AC-057 |
| REQ-016 | AC-003, AC-048 |
| REQ-017 / 017a / 017b / 017c | AC-004, AC-050, AC-012b |
| REQ-018 / 018a / 018b | AC-037, AC-038 |
| REQ-019 | AC-035, AC-036 |
| REQ-020 / 020a / 020b | AC-035, AC-036 |
| REQ-021 | AC-008 |
| REQ-022 | AC-006, AC-008 |
| REQ-023 / 023a / 023b | AC-005, AC-006, AC-040 |
| REQ-024 / REQ-024a | AC-039, AC-041, AC-008 |
| UX-001 … UX-003 | AC-034, AC-069 |
| UX-004 | AC-062, AC-066 |
| UX-005 | AC-024, AC-043 |
| UX-006 / UX-006a | AC-058, AC-066 |
| UX-007 | AC-066, AC-071 |
| UX-008 | AC-068 |
| UX-009 | AC-070, AC-052 |
| UX-010 | AC-011 |
| UX-011 | AC-003, AC-068 |
| BIZ-001 | AC-017, AC-061 |
| BIZ-002 | AC-007, AC-042 |
| BIZ-003 | AC-064 |
| BIZ-004 | AC-051 |
| BIZ-005 | AC-021 |
| BIZ-006 | AC-058 |
| DATA-001 / 002 / 002a / 002b | AC-032, AC-057, AC-019 |
| DATA-003 / 003a / 003b / 003c | AC-028, AC-067 |
| DATA-004 / 004a / 004b / 004c | AC-009, AC-053, AC-054, AC-055 |
| DATA-005 / 005a / 005b | AC-048, AC-049 |
| DATA-006 / 006a | AC-044, AC-052 |
| DATA-007 / 007a | AC-036, AC-045, AC-066 |
| DATA-008 / 008a | AC-044, AC-046, AC-020a, AC-047 |
| DATA-009 / 009a / 009b | AC-007, AC-042, AC-043 |
| DATA-010 / 010a / 010b | AC-010, AC-056, AC-003 |
| DATA-011 / 011a | AC-048, AC-056 |
| DATA-012 | AC-058 |
| SEC-001 | AC-013, AC-049 |
| SEC-002 … SEC-004 | AC-060, AC-009 |
| SEC-005 … SEC-007a | AC-015a, AC-015b, AC-016, AC-070 |
| SEC-008 | AC-014 |
| SEC-009 | AC-011 |
| SEC-010 | AC-049 |
| SEC-011 | AC-059 |
| SEC-012 | §11 rollback, README |
| SEC-013 | AC-061 |
| §11 Python floor | AC-066 (`helper_unavailable`) |
