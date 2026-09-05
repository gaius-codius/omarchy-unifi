# Feature Spec: Omarchy UniFi status plugin

## 1. Status
Owner: gaius-codius
Date: 2026-09-05
State: Draft
Related issues / PRs: none — greenfield repository
Supersedes: `PLAN.md` at the repository root (the pre-skill design document, retained as the origin record)
Supporting documents: `api-contract.md`, `unifi-network-v1-readonly-subset.json` (this directory)

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
Tested baseline is **Omarchy 4.0.2-1** with **Quickshell 0.3.1**
(`/usr/share/omarchy/version` reports `4.0.0.alpha`; the packaged version is
authoritative). Compatibility beyond this baseline is not assumed: each
additional Omarchy release must pass the §12 validation and runtime checks
before it is listed as supported.

The host contract this plugin depends on is documented in `host-contract.md`
in this directory, derived by reading `/usr/share/omarchy/shell/` and
`/usr/bin/omarchy-*` directly. Every host API this plugin calls is cited there
with a `path:line` reference; an uncited host API may not be used.

Ten host constraints (HC-1 … HC-10) materially shape this design. The ones that
change requirements rather than implementation detail:

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
  risk (§14 R1).

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

### Bar widget
REQ-001: A single bar item renders a UniFi glyph coloured by overall site
health, drawn from the active Omarchy theme's tokens, never from hard-coded
hex values.

REQ-002: Health colour is computed only from the **latest complete successful
snapshot**, and only while that snapshot is fresh:
- **green** — snapshot fresh, at least one gateway `ONLINE`, and every managed
  device `ONLINE`;
- **amber** — snapshot fresh, at least one gateway `ONLINE`, and one or more
  non-gateway devices not `ONLINE`, or any device in an unrecognized state;
- **red** — snapshot fresh and **every** gateway device is `OFFLINE` or
  `CONNECTION_INTERRUPTED`;
- **muted grey** — unconfigured, no successful snapshot has ever been taken, or
  the last successful snapshot has passed `staleAt`.

REQ-003: A device in a transitional state (`UPDATING`, `GETTING_READY`,
`ADOPTING`, `PENDING_ADOPTION`, `DELETING`) is neither online nor offline. It
does not force amber on its own, and it is named as transitional in the panel.
`ISOLATED` and `U5G_INCORRECT_TOPOLOGY` count as not-online and do force amber.
An unrecognized future `state` string counts as not-online, forces amber, and
raises a warning.

REQ-004: A failed refresh is surfaced immediately as a distinct warning overlay
while a still-fresh previous snapshot continues to determine the base health
colour. A failure never recolours the widget green and never discards the last
complete snapshot.

REQ-005: Optional compact text beside the glyph, controlled by `compactMetric`,
whose only values are `none` (default) and `clients`. Latency is not offered
because the API does not expose it (§5.3).

REQ-006: A tooltip reports site name, last successful refresh time, the latest
attempt's result, and a one-line status summary.

### Popup panel
REQ-007: Clicking the bar item opens a panel; clicking again, pressing Escape,
or losing focus closes it.

REQ-008: The panel shows site name, derived WAN/gateway state, and — when the
controller supplies them — gateway uptime and current uplink download/upload
throughput. Absent optional metrics render as "unknown", never as `0`.

REQ-009: The panel shows the connected client count and, per role
(`gateway`, `switching`, `accessPoint`), online and offline counts. A device
holding several roles is counted in **each** role it reports; the panel labels
these as role counts and separately shows the unique device total, so the role
rows are not expected to sum to it.

REQ-010: The panel lists offline devices by name and model, bounded to a
maximum of 10 entries with an "and N more" line beyond that.

REQ-011: A Refresh button triggers an immediate manual refresh (REQ-018).

REQ-012: An "Open UniFi" button opens `dashboardUrl` via
`Qt.openUrlExternally`, never through a shell command.

REQ-013: The panel renders an explicit, distinctly worded state for each of:
unconfigured, credential, unauthorized, forbidden, tls, network, timeout,
rate_limited, http, unsupported, redirect, configuration_conflict,
partial_response, oversized_response, malformed_response, internal, loading,
empty, and stale.

### Polling, scheduling, and staleness
REQ-014: A single shell-level `Service.qml` instance owns polling, cache, retry
state, and the helper process. Per-monitor bar widgets never poll.

REQ-015: Default refresh interval 30 s; user-selectable 15–3600 s, clamped
again at runtime rather than trusting manifest validation.

REQ-016: At most one helper batch runs at a time across the shell session.
Regular poll ticks that fire during an active batch are skipped.

REQ-017: The helper enforces a 25 s absolute monotonic deadline across every
request and every pagination page, with per-operation connect/read limits no
greater than the remaining budget. The service owns a separate 30 s watchdog as
a last-resort process reaper. Batch lifetime is independent of the refresh and
backoff intervals.

REQ-017a: Because the host offers no kill API and `running = false` is
asynchronous (HC-8), the watchdog requests termination, marks the batch
abandoned, immediately publishes the `timeout` state, and does **not** wait for
the process to exit. The abandoned process's later `onExited` is matched against
the captured generation and discarded. The watchdog is armed once on the launch
that needs watching and is not restarted on subsequent ticks, so a hung process
cannot push its own deadline forward.

REQ-017b: Because `onExited` and `onStreamFinished` have no guaranteed order
(HC-7), the service treats a batch as complete only when both the exit status
and both stdio streams have been observed, or the watchdog fires. It never
parses the protocol from `onExited` alone.

REQ-018: A manual refresh starts immediately when idle. If a batch is active,
exactly one pending manual refresh is coalesced and starts when it completes.
Manual refresh bypasses the current retry wait once but does not erase failure
history unless it succeeds.

REQ-019: HTTP 429, transient 5xx, timeouts, and transient network errors back
off exponentially from the configured interval to a 15-minute cap, with jitter.
`Retry-After` in delay-seconds or HTTP-date form is honoured, using the larger
of the exponential delay and the normalized server delay; past dates and
malformed values are ignored; an otherwise valid server delay is clamped to
24 hours with a warning. Success resets the failure count.

REQ-020: Configuration, credential, authorization, and TLS-policy errors stay
visible and retry no more often than every five minutes unless configuration
changes or the user requests a manual refresh.

REQ-021: The service preserves the last complete successful snapshot in memory
and records `lastAttemptAt`, `lastSuccessAt`, `staleAt`, `nextAttemptAt`, and
the latest error as separate values.

REQ-022: On success, `staleAt = lastSuccessAt + max(2 × the interval in effect
for that batch, 90 s)`. A later interval **increase** must never make an old
snapshot fresh again. A later **decrease** moves `staleAt` to the minimum of its
existing value and the threshold computed from the new interval. `staleAt` is
therefore monotonically non-increasing for a given snapshot.

REQ-023: Interval rescheduling is defined by scheduler state. Idle on normal
cadence: recompute the next due time from the last completed attempt — a
decrease brings it forward and fires immediately if already overdue, an increase
may move it later. During an active batch: the change applies to the next normal
cycle. During retry/backoff or a normalized `Retry-After` wait: the deadline is
never shortened; the new interval becomes the base only for subsequent retry
calculations.

REQ-024: A one-shot wake timer fires at the earlier of `staleAt` and
`nextAttemptAt`, so the widget goes grey on time even during a long backoff. A
failed attempt never rewrites the snapshot's original `observedAt`. All
scheduling uses service-local monotonic deadlines, never protocol timestamps.

## 7. User Experience
UX-001 (bar, healthy): themed UniFi glyph in the theme's success colour, plus
optional client count when `compactMetric` is `clients`.

UX-002 (bar, degraded / down / stale): the same glyph in the theme's warning,
error, or muted colour per REQ-002. Colour is never the sole signal — the
tooltip and panel always state the condition in words.

UX-003 (bar, refresh failed but snapshot fresh): a small warning affordance
composited over the health-coloured glyph, so "stale-ish" is distinguishable
from "confirmed bad".

UX-004 (loading): first load before any snapshot shows the muted glyph and a
tooltip reading that the first refresh is in progress. A refresh while a
snapshot exists never blanks the widget.

UX-005 (empty): a configured site with zero adopted devices shows an explicit
"no adopted devices" panel state, distinct from a failed fetch.

UX-006 (unconfigured): muted glyph; the panel explains that
`~/.config/omarchy-unifi` is not yet set up and names `scripts/configure`. No
partially-configured half-state is shown as an error.

UX-007 (panel error states): each §REQ-013 kind gets its own sentence naming
what failed and the one action that would fix it. Rate limiting and backoff show
`nextAttemptAt` as a relative time.

UX-008 (keyboard): the bar item is focusable and activates with Enter/Space.
Inside the panel, Tab cycles Refresh and Open UniFi, Enter activates, Escape
closes and returns focus to the bar item.

UX-009 (insecure TLS): when `allowInsecureTls` is true, the panel shows a
persistent, non-dismissible warning row for the whole session.

UX-010 (plain-HTTP dashboard): launching a `http://` `dashboardUrl` shows a
visible warning before opening.

UX-011 (multi-monitor): every monitor's widget shows identical state, and a
refresh triggered from any panel updates all of them.

## 8. Business Rules
BIZ-001: Read-only. The helper enforces a GET-only method allowlist and a
six-route allowlist (`api-contract.md`). No other route may be constructed.

BIZ-002: Counts are reported only when provably complete. Incomplete pagination
yields `partial_response`, and the affected count renders as unknown — never as
a smaller-but-plausible number and never as zero.

BIZ-003: Missing optional metrics are `null`/unknown. Zero is reserved for a
value the controller actually reported as zero.

BIZ-004: A batch is successful only if every required collection completed. A
partial batch does not replace the last complete snapshot.

BIZ-005: Health is derived only from a complete fresh snapshot. Transport
failure to reach the controller is a *plugin* failure state, not a *site down*
verdict, and must not be rendered as red site health.

BIZ-006: Exactly one site is displayed. Multi-site controllers require `siteId`
to be configured; site discovery exists to help choose it, not to aggregate.

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
| `dashboardUrl` | string (URL) | — | `http` or `https` only |

Plugin configuration at `~/.config/omarchy-unifi/config.json` — non-secret
controller and transport values, read by the helper only:
| Key | Type | Default |
|---|---|---|
| `apiRoot` | string | — |
| `siteId` | string (uuid) | absent during discovery |
| `customCaPath` | string | absent |
| `allowInsecureTls` | boolean | `false` |

Credential at `~/.config/omarchy-unifi/api-key` — the API key and nothing else.

DATA-002: The singleton service reads `refreshIntervalSec` from the canonical
`gaius-codius.unifi` entry in its injected `shell.shellConfig`, normalizes a missing
value to 30, and re-evaluates on `shellConfigChanged`. It accepts no competing
configuration pushes from per-monitor widgets. Duplicate layout entries with
identical normalized settings are accepted deterministically in
left → center → right order; conflicting duplicates produce
`configuration_conflict` and suspend polling until corrected.

DATA-003: Omarchy assigns `shell`, `manifest`, `omarchyPath`,
`barWidgetRegistry`, and `pluginRegistry` **after** constructing the service
object, so the service initializes from `onShellChanged` / `onManifestChanged`
(deferred with `Qt.callLater` where needed), never from an assumption that those
properties exist during `Component.onCompleted`. `settings` is **not** injected
into a service.

DATA-003a: A bar widget receives **only** `bar`, `moduleName`, and `settings`.
It does not receive `shell`, `manifest`, `service`, or `shellConfig`. It reaches
the singleton through `bar?.shell?.serviceFor("gaius-codius.unifi")`, with optional
chaining, because `bar` is null on the first frame. `serviceFor` lives on the
shell root, not on `bar`.

DATA-003b: Per HC-3, the service instance exists only while `gaius-codius.unifi`
appears in `bar.layout`. Removing the widget from the bar destroys the service.
The service must therefore release its helper process, timers, watchdog, and IPC
handler cleanly on destruction, and the README must state that the widget's
presence on the bar is what enables polling.

DATA-004: The three configuration files form **one committed set**.
`commit.json` holds a fresh generation identifier plus SHA-256 digests of the
exact `config.json` and `api-key` bytes. `scripts/configure` atomically replaces
the configuration and credential, fsyncs them and their directory, then
atomically replaces and fsyncs `commit.json` last as the commit point. Before
any network request the helper opens all three files through the validated
directory descriptor, reads each once, and verifies both digests against the
captured commit. A mismatch is an uncommitted-configuration error and sends no
request.

DATA-005: Helper protocol. Every invocation emits exactly one bounded JSON
object on stdout, `protocolVersion` integer `1`. Success:
`{protocolVersion, ok: true, attemptedAt, observedAt, data, warnings, error: null}`.
Failure:
`{protocolVersion, ok: false, attemptedAt, observedAt: null, data: null, warnings, error: {kind, message, retryable, httpStatus, retryAfterSec}}`.

DATA-006: `data` normalizes to:
`site {id, name}`,
`wan {status, uptimeSec|null, downloadBps|null, uploadBps|null}`,
`counts {clients|null, gateways{online,offline,transitional}, switches{...}, accessPoints{...}, devicesTotal}`,
`offlineDevices [{id, name, model, state}]`,
`applicationVersion`.
`wan.latencyMs` and `wan.packetLossPct` are **absent from the model**, not
present-and-null, because no supported API version can populate them.

DATA-007: Error `kind` is one of `unconfigured`, `credential`, `unauthorized`,
`forbidden`, `tls`, `network`, `timeout`, `rate_limited`, `http`, `unsupported`,
`redirect`, `configuration_conflict`, `partial_response`, `oversized_response`,
`malformed_response`, `internal`. Unknown kinds map to `internal` in QML.
`network` means no HTTP response was received (DNS failure, connection refused
or reset, host/network unreachable); it is retryable and carries no
`httpStatus`. TLS verification failure is `tls`, time-budget expiry is
`timeout`, and a received HTTP status keeps its typed status kind or `http`.
Exception matching must preserve that precedence.

DATA-008: Protocol validation in QML. `protocolVersion` must be integer `1`.
stdout must parse as exactly one JSON value followed only by whitespace. Both
shapes require an RFC 3339 UTC `attemptedAt` ending in `Z`, at most 64
characters, no more than five minutes before service launch and not after
receipt. `ok: true` additionally requires exit status zero, a `data` object,
`error: null`, and an equally bounded `observedAt` with
`attemptedAt <= observedAt` and at most five minutes of future skew. `ok: false`
requires a non-zero exit, `data: null`, `observedAt: null`, and a structurally
valid error. Warnings, strings, numbers, arrays, and nested object sizes are all
bounded. Contradictory shapes, invalid or timezone-less timestamps, unknown
versions, success paired with process failure, multiple JSON values, or trailing
non-whitespace are protocol errors. Protocol timestamps are display and audit
data only; the service records its own launch and completion times and uses
monotonic clocks for every deadline.

DATA-009: Pagination. Request `limit=200`. Validate every page before
accumulation: returned `offset` equals the requested offset, `count` equals
`data.length`, `0 <= count <= limit`, non-terminal pages make positive progress,
records carry unique stable IDs, and `totalCount` is non-negative and stable
across pages. Advance by the validated count, never by untrusted response
arithmetic. If a live collection's total changes mid-read, retry that collection
once from offset zero; repeated instability is `partial_response`. A terminal
result is complete only when the number of unique accumulated records equals the
terminal `totalCount`. Enforce maximum page count and maximum total decoded
bytes. Empty premature pages, inconsistent totals, repeated records or pages,
and safety-cap hits are errors — never an accurate zero or a complete count.

DATA-010: The service — not the bar widget — registers an IPC target
`gaius-codius.unifi` exposing a `reload` method. It must be the service because a bar
surface exists per monitor, a target admits only one handler, and
`omarchy shell call` does not reach bar widgets at all (HC-6).

`scripts/configure` invokes `omarchy shell gaius-codius.unifi reload` **without** `-q`
after the commit point. Per HC-5 the exit status cannot classify the outcome —
both "no shell running" and "target missing" exit 1 — so configure captures
stderr and classifies on the exact message:

| stderr | Meaning | configure exit |
|---|---|---|
| (none, exit 0) | delivered and acknowledged | 0, reported as applied |
| `omarchy-shell is not running` | no shell | 0, reported as deferred to next shell start |
| `omarchy-shell is not responding` | IPC timeout | non-zero |
| `omarchy-shell is not ready` | shell still starting | non-zero |
| `Target not found.` | service not loaded (widget not on the bar) | non-zero |
| `Function not found.` | method missing / version mismatch | non-zero |
| any other | unknown failure | non-zero |

Only the absent-shell case may report success. Every non-zero path preserves the
committed files, so a failed live reload never destroys a valid commit and never
reports as applied. A `--commit` mode revalidates hand-edited files, writes a new
commit marker, and performs the same acknowledged reload.

DATA-011: A valid reload increments a service generation, terminates any active
old-generation helper, conservatively clears retry state and the cached
snapshot, and starts a new batch. Every completion handler compares its captured
generation and discards obsolete output, including late output from a terminated
process. Directory watching is a debounced recovery path; the commit marker is
the transaction boundary and acknowledged IPC is the live-update path.

## 10. Security / Privacy / Permissions
SEC-001: The API key never appears in Git, `manifest.json`, QML, `shell.json`,
environment variables, command-line arguments, logs, exceptions, stdout, stderr,
test output, or UI messages. The service invokes the helper with an argv vector,
never composed shell text; the helper reads the credential itself.

SEC-002: `~/.config/omarchy-unifi` must be a real directory owned by the current
user and not group/other-writable; `scripts/configure` creates it mode `0700`.
`config.json`, `api-key`, and `commit.json` must be non-symlinked regular files
owned by the current user and not group/other-writable; created mode `0600`.

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

SEC-007: Immediately after creating an SSL context, `keylog_filename` is set to
`None` so an inherited `SSLKEYLOGFILE` cannot record session secrets for
credential-bearing requests.

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
never include request headers or credentials.

SEC-011: No real controller response containing private data is committed.
Fixtures are synthetic or redacted, with no real MAC addresses, IP addresses,
client names, site names, or UUIDs traceable to a real deployment.

## 11. Compatibility / Migration
Backward compatibility: greenfield; nothing to preserve. `protocolVersion` and
the manifest `schema` version are the forward-compatibility hinges.

Runtime dependency: Python 3 standard library only. No pip packages. Omarchy's
plugin installer does not install runtime packages, so `scripts/configure` must
check for Python 3 and the README must document it.

Supported matrix: Omarchy 4.0.2-1 / Quickshell 0.3.1 only, until a further
release passes the §12 checks. UniFi Network compatibility is recorded as a
tested-version matrix; an unsupported version or a missing required capability
produces an explicit `unsupported` error rather than being misreported as an
authentication or connectivity failure.

Rollout: installed as a **user** plugin under
`~/.config/omarchy/plugins/gaius-codius.unifi/`. No file under `/usr/share/omarchy/` is
edited and no symlink is relied upon.

Rollback: `omarchy plugin disable` then remove the plugin directory. The plugin
holds no state outside `~/.config/omarchy-unifi/` and its `shell.json` entry.

## 12. Acceptance Criteria
AC-001: `omarchy plugin validate <repo-root>` succeeds on
every explicitly supported Omarchy version, beginning with 4.0.2-1.

AC-002: The plugin installs as a user plugin with no edits under
`/usr/share/omarchy/`.

AC-003: On a multi-monitor session exactly one UniFi service object exists and
at most one helper process runs at a time; every widget shows identical state
and a refresh from any panel updates all panels.

AC-004: The helper enforces its 25 s total deadline independently of polling;
the 30 s QML watchdog reaps a hung helper, leaves no orphan process, produces a
typed `timeout` state, and does not prevent later refreshes.

AC-005: With valid configuration and a healthy controller, a batch starts within
one configured refresh interval; a slower valid batch updates on completion
within the 25 s helper deadline while intervening ticks remain skipped. During
backoff the UI reports `nextAttemptAt`, and a successful manual refresh restores
normal scheduling.

AC-006: Decreasing the interval brings the next normal attempt and `staleAt`
forward; increasing it never makes an existing snapshot fresh again; neither
direction shortens an already-active retry or `Retry-After` deadline.

AC-007: Device and client counts are accurate across all pages. Incomplete
pagination reports partial/unknown rather than a complete count, and every
page invariant in DATA-009 is fixture-tested.

AC-008: A failed refresh preserves the last complete snapshot, reports the
current failure immediately, and turns the widget grey at `staleAt` even when
the next attempt is delayed further by backoff.

AC-009: A helper sends traffic only after `config.json` and `api-key` match one
descriptor-safely read commit marker. An update interrupted between the two file
replacements sends no request.

AC-010: An acknowledged reload invalidates old cached state, bypasses a prior
fatal-error delay, and cannot accept output from an older generation. An absent
shell is reported as deferred to next startup and exits zero; every other IPC
delivery failure exits non-zero and is visible.

AC-011: "Open UniFi" validates and opens the configured HTTP(S) dashboard with
no shell interpretation, and warns before opening a plain-HTTP URL.

AC-012: DNS failure, connection refused/reset, and host/network unreachable each
produce a typed retryable `network` error carrying no HTTP status. No network or
API failure crashes or synchronously blocks `omarchy-shell`.

AC-013: A repository-wide scan finds no API key or credential in Git history,
QML, settings, process arguments, environment, logs, stdout, stderr, exceptions,
tests, or UI strings.

AC-014: No HTTP redirect is followed and `X-API-Key` is never copied to another
URL, verified for 301, 302, 303, 307, 308, same-origin, cross-origin, HTTPS
downgrade, redirect loops, and oversized redirect bodies.

AC-015: TLS verification is on by default; a custom CA loads only from bounded
descriptor-captured contents that pass the SEC-006 trust checks; insecure TLS is
opt-in and visibly warned; with `SSLKEYLOGFILE` set in the environment the
helper creates no key log and leaves `SSLContext.keylog_filename` disabled.

AC-016: The configuration directory, `config.json`, `api-key`, `commit.json`,
and the custom CA each pass their size, ownership, permission, regular-file,
content, and substitution-safe descriptor checks, with a negative test per
check.

AC-017: The adapter can construct only the six allowlisted GET routes; an
attempt to build any other route or method fails a test.

AC-018: Console and self-hosted request roots are both fixture-tested for exact
constructed URLs.

AC-019: Conflicting duplicate bar entries produce `configuration_conflict` and
suspend polling, rather than racing the singleton scheduler.

AC-020: Both protocol shapes reject an invalid `attemptedAt`; the success shape
additionally rejects an invalid, future-skewed, or wrongly ordered `observedAt`.

AC-021: A device reporting several `features` is counted in each of its roles,
the panel's unique device total is correct, and the role rows are labelled so
they are not read as a partition.

AC-022: A device in a transitional state does not by itself turn the widget
amber, and appears as transitional rather than offline in the panel.

AC-023: Red requires that every gateway be `OFFLINE` or
`CONNECTION_INTERRUPTED` in a fresh complete snapshot. A transport failure
reaching the controller never renders as red site health.

AC-024: `compactMetric` accepts only `none` and `clients`; the manifest settings
schema contains no `latency` value, and the panel renders unavailable optional
metrics as "unknown" rather than `0`.

AC-025: All fixture, model, service, watchdog, transaction and reload-generation,
IPC, redirect, TLS-keylog, and protocol tests pass.

AC-026: A minimal plugin loaded from `~/.config/omarchy/plugins/` successfully
imports `qs.Commons` and `qs.Ui` and resolves `Style`, `Color`, `BarIconButton`,
`Panel`, and `KeyboardPanel` (HC-10). This is verified before any UI work
depends on it; failure triggers a Tier 3 deviation.

AC-027: The plugin folder contains no symlink outside `.git`, and
`omarchy plugin validate` is run as part of every checkpoint.

AC-028: Removing the widget from the bar destroys the service and leaves no
running helper process, no armed timer, and no registered IPC target; re-adding
it starts a clean service. Verified with a process check.

AC-029: The watchdog publishes the `timeout` state without waiting for the
helper to exit, and the abandoned helper's later output is discarded by
generation check rather than applied (HC-8).

AC-030: A helper that exits before its stdout collector reports is still parsed
correctly; the service never reads the protocol from `onExited` alone (HC-7).

AC-031: `scripts/configure` classifies every IPC outcome in DATA-010 correctly
from stderr text, and only the absent-shell case exits zero. Verified with a
stub `omarchy` on `PATH` producing each message.

AC-032: Every default in DATA-001 is applied in QML via `setting(name,
fallback)` and is correct when the `shell.json` entry contains no settings keys
at all (HC-1).

## 13. Test Strategy
Unit (Python, `tests/test_unifi_status.py`): configuration/credential/commit/CA
validation including every negative case; TLS behaviour; redirect rejection;
pagination invariants; size and time bounds; response normalization; the full
error-kind taxonomy including separated `network` sub-cases; envelope
construction; secret-free output assertions.

Unit (JS, `tests/test_model.js`): pure health, staleness, scheduler, and backoff
transitions and safe display-string formatting from `Model.js`, run outside QML.

Integration (QML, `tests/test_service.qml`): protocol acceptance and rejection,
watchdog expiry, generation-based rejection of late output, manual-refresh
coalescing, interval rescheduling in all three scheduler states, duplicate and
conflicting `shell.json` entries, and committed-reload handling while idle and
in flight.

Integration (shell, `tests/test_configure.sh`): the configure script's atomic
commit ordering, an update interrupted between replacements, `--commit` mode,
and each IPC outcome (delivered, shell absent, shell starting, target missing,
method missing, timeout).

E2E / manual QA (Phase 5): the §12 runtime scenarios on a real controller and a
real multi-monitor Omarchy session, across multiple themes, with recorded
versions and screenshots.

Regression: `omarchy plugin validate`, `qmllint` where available, and a
repository secret scan run on every checkpoint.

Fixtures: synthetic, covering both API-root families, healthy / degraded / down,
multi-page (>25 and >200 records), and every malformed-page case. No real
controller data is committed (SEC-011).

## 14. Risks and Open Questions
R1 (highest technical risk): **HC-10** — it is unproven that `qs.Commons` and
`qs.Ui` resolve for a plugin loaded from `~/.config/omarchy/plugins/`. Every
first-party importer lives inside `/usr/share/omarchy/shell/`, and no
third-party plugin in the tree demonstrates it. If the imports do not resolve,
the entire theming and component strategy changes and the UI phase must be
re-planned. Mitigation: AC-026 settles this with a throwaway minimal plugin
**before** any UI work begins, as the first gate of the UI phase.

R1a: The rest of the host contract is likewise derived by reading Omarchy's
source rather than published plugin documentation. Any API that cannot be cited
with a `path:line` reference in `host-contract.md` may not be used.

R1b: HC-1 means the manifest settings schema is decorative in 4.0.2. If a later
Omarchy release starts rendering it and merging `defaults`, the QML defaults and
the manifest defaults must agree or users will see values change under them.
Mitigation: a single source of default values, asserted equal in a test.

R2: `/v1/info` returns only `applicationVersion`. Capability gating is therefore
version-string comparison against a tested matrix, which is coarse. Mitigation:
treat an untested version as `unsupported` with an explicit message rather than
attempting best-effort parsing.

R3: The API documents no `Retry-After` header and no non-200 responses. The 429
and backoff handling is written defensively and can only be fixture-tested, not
verified against the published contract, until observed on a real controller.

R4: Identifying "the gateway device" relies on `features` containing `gateway`.
A site with no gateway-featured device (an isolated switch/AP site) has no WAN
signal at all. Mitigation: such a site reports WAN state `unknown` and never
red, and this is asserted by a fixture test.

R5 (open, **blocking by user decision**): the real controller's `apiRoot`,
Network version, site UUID, and controller type are not known. The user has
elected to stop implementation until these are supplied, so the plan's Phase 1
is a hard gate rather than an advisory one.

R6: `Process` termination is advisory (HC-8). A helper wedged in an
uninterruptible state cannot be reaped by the shell at all. Mitigation: the
helper's own 25 s internal deadline is the primary bound and the QML watchdog is
only a backstop; the service stays functional after abandoning a process.

Resolved during spec creation:
- WAN latency and packet loss: **removed from scope**, no API support (§5.3).
- Red-state definition: **all gateways offline or connection-interrupted**.
- Multi-feature devices: **counted in every role**, with a separate unique total.
- `compactMetric`: **`none` | `clients`**, `latency` removed.
- Plugin ID: **`gaius-codius.unifi`** accepted (avoids the reserved `omarchy.*`
  namespace).

## 15. Agent Autonomy Rules
May decide without asking:
- Python and QML file, module, class, and function structure.
- Internal naming, error-message wording, and log formatting, provided SEC-001
  holds.
- Test file organization, fixture naming, and additional test cases beyond §13.
- Choice of standard-library mechanism for any behaviour §9/§10 specifies by
  outcome rather than by mechanism.
- Bounds and constants not fixed by this spec (maximum page count, maximum
  decoded bytes, jitter magnitude), chosen conservatively and documented in
  `implementation-notes.md`.
- Panel layout, spacing, and component composition within the Omarchy theme
  primitives.
- Reordering work inside a phase, and splitting a phase task into several.

Must stop and ask about:
- Any change to a requirement, acceptance criterion, business rule, or security
  behaviour in this spec.
- Adding any route, HTTP method, or outbound network destination beyond the six
  allowlisted GETs.
- Any write, mutation, or action endpoint.
- Adding a runtime dependency outside the Python standard library.
- Weakening any TLS, credential, file-permission, or descriptor-safety control.
- Changing the health colour rules, the definition of red, or the device-count
  semantics.
- Anything requiring real controller credentials, a real API key, or live
  traffic to the user's controller.
- Installing, enabling, or staging the plugin into `~/.config/omarchy/plugins/`.
- Initializing a remote, pushing, or publishing the repository anywhere.
- Any use of a host API that cannot be cited in `host-contract.md`.
- Proceeding with UI work if AC-026 (HC-10 import resolution) fails.

## 16. Review Log
| Date | Stage | Reviewer | Finding | Action Taken |
|------|-------|----------|---------|--------------|
| 2026-09-05 | Spec creation | API contract extraction | `/v1/wans` and `/v1/sites` expose no status, latency, or loss fields | Latency and packet loss removed from scope; red redefined on gateway state (REQ-002); `compactMetric` enum reduced (REQ-005) |
| 2026-09-05 | Spec creation | API contract extraction | `features` is a set, so role counts cannot partition the device list | REQ-009 counts a device in every role and adds a unique device total |
| 2026-09-05 | Spec creation | API contract extraction | Device `state` has 10 values, not 2 | REQ-003 defines transitional, not-online, and unknown handling |
| 2026-09-05 | Spec creation | Host exploration (HC-1) | Manifest `defaults`/`schema`/`settingsForm` have no runtime consumer in 4.0.2 | DATA-001 rewritten; defaults applied in QML; AC-032 added |
| 2026-09-05 | Spec creation | Host exploration (HC-3) | Service lifetime is tied to the bar layout entry | DATA-003b and AC-028 added |
| 2026-09-05 | Spec creation | Host exploration (HC-5) | `omarchy shell` exit code cannot distinguish absent shell from missing target | DATA-010 rewritten to classify on stderr text; AC-031 added |
| 2026-09-05 | Spec creation | Host exploration (HC-7) | `onExited` / `onStreamFinished` ordering is unspecified | REQ-017b and AC-030 added |
| 2026-09-05 | Spec creation | Host exploration (HC-8) | No kill API; `running = false` is asynchronous | REQ-017a and AC-029 added; R6 raised |
| 2026-09-05 | Spec creation | Host exploration (HC-10) | `qs.Commons` / `qs.Ui` resolution from a third-party plugin dir is unproven | Promoted to R1, the highest technical risk; AC-026 gates the UI phase |
| 2026-09-05 | Spec creation | Host exploration | Bar widgets receive only `bar`, `moduleName`, `settings` | DATA-003a corrects the service-resolution path to `bar?.shell?.serviceFor()` |
