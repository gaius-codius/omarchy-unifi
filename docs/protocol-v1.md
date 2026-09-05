# Helper protocol v1 — normative contract

This is the wire contract between the Python helper (`helper/unifi_status.py`,
the producer) and the QML service (`Service.qml` + `Protocol.js`, the consumer).

**It is the single source both sides implement.** They are written in different
languages, tested by different runners, and built in different phases, so
anything left to each side to infer will diverge — which is exactly what
happened in DEV-1, where `counts` could not answer the health question that read
it. Every shape, bound and rejection rule below is stated once, here.

Authority: `SPEC.md` spec-v1 (frozen 2026-09-05) is authoritative for
*requirements*. This document is authoritative for the *encoding* of those
requirements, and fills in the bounds `SPEC.md` §15 leaves to implementation.
Where a value here has no `SPEC.md` source it is marked **[chosen]** and its
reasoning is in `implementation-notes.md`.

Contents:

- [Transport](#transport)
- [Envelope](#envelope)
- [`meta`](#meta)
- [`data`](#data)
- [`warnings`](#warnings)
- [`error`](#error)
- [DATA-007a consistency matrix](#data-007a-consistency-matrix)
- [Bounds](#bounds)
- [DATA-008 rejection classes](#data-008-rejection-classes)
- [DATA-009 invariants](#data-009-invariants)

---

## Transport

The service launches the helper as an argv vector — never composed shell text:

```
python3 -B -E -s <abs path>/helper/unifi_status.py --nonce <nonce>
```

`-B` because a `__pycache__` write inside a staged plugin folder hot-reloads the
plugin mid-poll (HC-14). `-E` and `-s` because the environment and the user site
directory must not be able to steer the helper.

The helper writes **exactly one** JSON object to stdout and exits. Exit status
zero means and only means "I produced a success envelope"; any failure envelope
is paired with a non-zero exit. The service joins `exit`, `stdout`,
`stderr` and `watchdog` before parsing, because `onExited` and
`onStreamFinished` have no specified ordering (HC-7).

The `nonce` is a fresh random token per batch, passed in argv and echoed
verbatim. It carries no secret material (SEC-001). An envelope whose nonce does
not match the one issued for that batch is discarded — which is what makes
generation checking survive both HC-8's asynchronous termination and a plugin
hot-reload that destroys the service and resets any in-memory counter.

## Envelope

Both shapes carry the same nine keys. No other key is permitted at the top
level.

**Success** — requires exit status 0:

```json
{
  "protocolVersion": 1,
  "ok": true,
  "nonce": "<echoed>",
  "attemptedAt": "2026-09-06T09:15:04Z",
  "observedAt": "2026-09-06T09:15:06Z",
  "meta": { },
  "data": { },
  "warnings": [ ],
  "error": null
}
```

**Failure** — requires a non-zero exit status:

```json
{
  "protocolVersion": 1,
  "ok": false,
  "nonce": "<echoed>",
  "attemptedAt": "2026-09-06T09:15:04Z",
  "observedAt": null,
  "meta": { },
  "data": null,
  "warnings": [ ],
  "error": { }
}
```

| Key | Type | Notes |
|---|---|---|
| `protocolVersion` | integer | exactly `1`. Not `"1"`, not `1.0`. |
| `ok` | boolean | tested by **identity** against `true`/`false`. `1`, `"true"` and absence are rejected, never coerced. |
| `nonce` | string | echoed verbatim; 1–128 chars. |
| `attemptedAt` | string | RFC 3339 UTC, `Z` suffix, ≤64 chars. |
| `observedAt` | string \| null | success: required, `attemptedAt <= observedAt <= receiptTime`. failure: `null`. |
| `meta` | object | **always present**, never null. |
| `data` | object \| null | success: satisfies [`data`](#data). failure: `null`. |
| `warnings` | array | possibly empty; never null. |
| `error` | object \| null | success: `null`. failure: satisfies [`error`](#error). |

Timestamps are **display and audit data only**. The service uses its own
monotonic clocks for every deadline, so a helper with a wrong clock delays
nothing.

## `meta`

Present as an object on **both** shapes, so the panel can show transport facts
before any batch has succeeded (DATA-006a, amended by DEV-2).

```json
{
  "commitGeneration": 7,
  "apiRootHost": "192.168.1.1",
  "siteId": "8f14e45f-ceea-4b47-9f2a-1c3d5e7a9b0c",
  "allowInsecureTls": false,
  "customCaInUse": false,
  "helperVersion": "0.1.0"
}
```

| Field | Type | Nullable |
|---|---|---|
| `commitGeneration` | integer ≥ 0 | yes |
| `apiRootHost` | string | yes |
| `siteId` | string (uuid) | yes |
| `allowInsecureTls` | boolean | yes |
| `customCaInUse` | boolean | yes |
| `helperVersion` | string | **no** |

**Every field except `helperVersion` is nullable, and the container is
unconditional.** This is DEV-2, and the reason is not tidiness: every field but
`helperVersion` derives from the committed configuration, and the two failure
kinds that most need a `meta` — `unconfigured` and `uncommitted` — are precisely
the cases where that configuration could not be read. A conditional container
would give the validator two rules and the two implementations a shape to
disagree about.

`apiRootHost` is the **host component only**. Never a full URL, never userinfo,
never a port-and-path. QML cannot read `config.json`, so this is the only
channel by which UX-009's insecure-TLS warning and REQ-012's dashboard fallback
can work.

`ViewModel` renders a null field as "unknown" rather than omitting its row.

## `data`

Present only on success, and only in this exact shape (DATA-006).

```json
{
  "site":    { "id": "…", "name": "Home" },
  "wan":     { "status": "up", "uptimeSec": 864000, "downloadBps": 12000000, "uploadBps": 3000000 },
  "gateways": [
    { "id": "…", "name": "UDM Pro", "model": "UDMPRO", "state": "ONLINE",
      "class": "online", "uptimeSec": 864000, "downloadBps": 12000000, "uploadBps": 3000000 }
  ],
  "counts": {
    "clients": 42,
    "devicesTotal": 9,
    "offlineTotal": 1,
    "byClass":      { "online": 8, "transitional": 0, "down": 1, "impaired": 0, "unknown": 0 },
    "gateways":     { "online": 1, "transitional": 0, "down": 0, "impaired": 0, "unknown": 0 },
    "switches":     { "online": 3, "transitional": 0, "down": 1, "impaired": 0, "unknown": 0 },
    "accessPoints": { "online": 5, "transitional": 0, "down": 0, "impaired": 0, "unknown": 0 }
  },
  "offlineDevices": [
    { "id": "…", "name": "Garage Switch", "model": "USW-Lite-8-PoE", "state": "OFFLINE", "class": "down" }
  ],
  "applicationVersion": "9.1.0"
}
```

`wan.latencyMs` and `wan.packetLossPct` are **absent from the model** — not
present-and-null. No supported API version can populate them: `/v1/sites/{id}/wans`
returns `{id, name}` and nothing else (`api-contract.md`). A present-and-null
field would invite a future contributor to fill it in.

### Device class (REQ-000)

Total over the ten API `state` values, with everything else landing in
`unknown`. Never silently in another class.

| Class | `state` values |
|---|---|
| `online` | `ONLINE` |
| `transitional` | `PENDING_ADOPTION`, `UPDATING`, `GETTING_READY`, `ADOPTING`, `DELETING` |
| `down` | `OFFLINE`, `CONNECTION_INTERRUPTED` |
| `impaired` | `ISOLATED`, `U5G_INCORRECT_TOPOLOGY` |
| `unknown` | any other string; emits a `unknown_device_state` warning naming the value |

A device is a **gateway** when its `features` array contains `gateway`.

### `wan.status` (REQ-008a)

Domain `up`, `down`, `degraded`, `unknown`, derived from gateway devices only:

| Condition | Status |
|---|---|
| no gateway present | `unknown` |
| every gateway is `down` | `down` |
| some but not all gateways are `down`, `impaired` or `unknown` | `degraded` |
| otherwise | `up` |

`wan`'s metrics are those of the **primary gateway**: the first `online` gateway
in ascending `id` order, or the first gateway in ascending `id` order when none
is online. Statistics are fetched for at most four gateways per batch, in that
order; the rest are listed without metrics and raise
`gateway_statistics_truncated`.

### `counts`

| Field | Type | Nullable |
|---|---|---|
| `clients` | integer ≥ 0 | yes — `null` when `/clients` failed (BIZ-004) |
| `devicesTotal` | integer ≥ 0 | no |
| `offlineTotal` | integer ≥ 0 | no |
| `byClass` | class object | no |
| `gateways` | class object | no |
| `switches` | class object | no |
| `accessPoints` | class object | no |

A **class object** has all five REQ-000 classes present as integers ≥ 0. No
class may be absent, and absence is never read as zero.

`byClass` is a **unique-device partition**: every adopted device counted exactly
once, including a device whose `features` array is empty. The three role objects
are deliberately **not** a partition — a Dream Machine reports `gateway`,
`switching` and `accessPoint` simultaneously and is counted in each (REQ-009).
That contrast is what `roleCountsAreNotAPartition` communicates to the panel.

Two invariants, enforced by **both** sides on every success envelope (DEV-1):

```
sum(byClass) == devicesTotal
byClass.down + byClass.impaired == offlineTotal
```

`offlineTotal` is `down + impaired` only, which is why it cannot substitute for
`byClass` in REQ-002 rule 4 — that rule also asks about `unknown`.

### `offlineDevices`

Devices in class `down` or `impaired`, bounded to **10** entries. A
`transitional` device never appears here (REQ-003).

The panel's "and N more" line is computed from `counts.offlineTotal`, **not**
from this array's length, so a helper-side bound can never understate how many
devices are down.

## `warnings`

An array of objects. Empty is normal; `null` is a rejection.

```json
{ "code": "clients_unavailable", "message": "Client list unavailable; count shown as unknown.", "detail": null }
```

| Field | Type | Notes |
|---|---|---|
| `code` | string | from the table below |
| `message` | string | human-readable, ≤256 chars, contains no credential material |
| `detail` | object \| null | bounded structured payload |

An object rather than a bare string because DATA-012 must carry discovered
`{id, name}` site pairs for UX-006a to list, and a string would force the panel
to parse prose.

| `code` | Raised when | `detail` |
|---|---|---|
| `unknown_device_state` | a `state` outside the ten known values | `{ "state": "…", "deviceId": "…" }` |
| `site_auto_selected` | no `siteId` committed and exactly one site exists (DATA-012) | `{ "id": "…", "name": "…" }` |
| `sites_discovered` | no `siteId` committed and several sites exist; paired with `site_unselected` | `{ "sites": [ { "id": "…", "name": "…" } ] }` |
| `clients_unavailable` | optional `/clients` failed (BIZ-004) | `null` |
| `statistics_unavailable` | optional `statistics/latest` failed | `{ "deviceId": "…" }` |
| `wans_unavailable` | optional `/wans` failed | `null` |
| `gateway_statistics_truncated` | more than four gateways (REQ-008a) | `{ "fetched": 4, "total": 6 }` |
| `offline_list_truncated` | more than ten offline devices (REQ-010) | `{ "listed": 10, "total": 17 }` |
| `page_reread_mismatch` | the DATA-009a page-0 re-read disagreed once | `{ "collection": "devices" }` |
| `insecure_tls` | `allowInsecureTls` is in force (UX-009) | `null` |
| `custom_ca_in_use` | a `customCaPath` is in force | `null` |
| `retry_after_clamped` | a server `Retry-After` exceeded 24 h (REQ-019) | `{ "requestedSec": …, "clampedSec": 86400 }` |
| `retry_after_ignored` | `Retry-After` was malformed or in the past | `null` |
| `stderr_bound_exceeded` | the helper's own stderr hit 4 KiB (DATA-005b) | `null` |

The service may append warnings of the same shape for conditions it owns —
invalid inline settings (DATA-002a), a plain-`http` dashboard URL (UX-010) — so
the panel has one list to render. Those codes are service-side and never appear
in a helper envelope.

## `error`

Present only on failure, paired with a non-zero exit.

```json
{ "kind": "rate_limited", "message": "Controller is rate limiting requests.",
  "httpStatus": 429, "retryAfterSec": 30, "retryable": true }
```

| Field | Type | Notes |
|---|---|---|
| `kind` | string | one of the nineteen DATA-007 kinds |
| `message` | string | ≤256 chars, no credential material, no raw response body |
| `httpStatus` | integer \| null \| absent | governed by the matrix below |
| `retryAfterSec` | integer \| null \| absent | governed by the matrix below |
| `retryable` | boolean | must equal the kind's class |

A `kind` the consumer does not recognise maps to `internal` in QML (DATA-007).
That is a **forward-compatibility mapping, not a rejection** — a newer helper
must not brick an older service.

## DATA-007a consistency matrix

Machine-readable. An error object contradicting any row is rejected as
`malformed_response` rather than acted on — an envelope claiming
`kind: "credential"` with `httpStatus: 429` is a bug or an attack, not a rate
limit.

`httpStatus` and `retryAfterSec` are **optional even where allowed**: `null` and
absent are both accepted, so a legitimate `network` failure that omits the field
is never misread as a protocol violation. Where the column says *forbidden*, an
integer is rejected.

| kind | httpStatus | retryAfterSec | retryable | retry class |
|---|---|---|---|---|
| `unconfigured` | forbidden | forbidden | `false` | fatal |
| `site_unselected` | forbidden | forbidden | `false` | fatal |
| `uncommitted` | forbidden | forbidden | `false` | fatal |
| `credential` | forbidden | forbidden | `false` | fatal |
| `unauthorized` | allowed, `401` | forbidden | `false` | fatal |
| `forbidden` | allowed, `403` | forbidden | `false` | fatal |
| `tls` | forbidden | forbidden | `false` | fatal |
| `network` | forbidden | forbidden | `true` | transient |
| `timeout` | forbidden | forbidden | `true` | transient |
| `rate_limited` | allowed, `429` | allowed | `true` | transient |
| `http` | allowed, `100`–`599`, not `401`/`403`/`429` | forbidden | see below | see below |
| `unsupported` | forbidden | forbidden | `false` | fatal |
| `redirect` | forbidden | forbidden | `true` | integrity |
| `configuration_conflict` | forbidden | forbidden | `false` | fatal |
| `partial_response` | forbidden | forbidden | `true` | integrity |
| `oversized_response` | forbidden | forbidden | `true` | integrity |
| `malformed_response` | forbidden | forbidden | `true` | integrity |
| `helper_unavailable` | forbidden | forbidden | `false` | fatal |
| `internal` | forbidden | forbidden | `true` | integrity |

`http` is the one kind whose class depends on its status, because REQ-019 makes
transient 5xx retryable while REQ-020 makes other statuses fatal:

| `httpStatus` | `retryable` | class |
|---|---|---|
| `500`, `502`, `503`, `504` **[chosen]** | `true` | transient |
| anything else | `false` | fatal |

401, 403 and 429 are excluded from `http` entirely because DATA-007 requires a
received status to keep its typed kind. An `http` error carrying 401 means the
producer skipped that mapping, and is rejected.

Retry classes drive the scheduler: **transient** uses REQ-019's exponential
schedule capped at `max(interval, 900 s)`; **fatal** retries no more often than
`max(interval, 300 s)` (REQ-020); **integrity** uses the same exponential
schedule as transient (REQ-020a). Both caps are relative to the configured
interval so that a failing controller is never polled more often than a healthy
one.

## Bounds

Every bound is enforced on **both** sides. A value marked **[chosen]** is not
fixed by `SPEC.md`; §15 delegates it, and the reasoning is in
`implementation-notes.md`.

### Envelope and process

| Bound | Value | Source |
|---|---|---|
| stdout total | 256 KiB | DATA-005 |
| helper's own stderr | 4 KiB | DATA-005b |
| stderr retained by the service | 8 KiB | DATA-005b |
| helper absolute deadline, whole batch | 25 s | REQ-017 |
| service watchdog | 30 s | REQ-017 |
| `attemptedAt` string length | 64 chars | DATA-008 |
| `attemptedAt` earliest, before service launch | 300 s | DATA-008 |
| `nonce` length | 1–128 chars **[chosen]** | — |

### Envelope content

| Bound | Value | Source |
|---|---|---|
| `offlineDevices` entries | 10 | REQ-010 |
| gateways fetched with statistics | 4 | REQ-008a |
| `gateways` array entries | 64 **[chosen]** | — |
| `warnings` entries | 32 **[chosen]** | — |
| any string value | 512 chars **[chosen]** | — |
| `message` fields specifically | 256 chars **[chosen]** | — |
| JSON nesting depth | 16 **[chosen]** | — |
| any integer count | 0 – 1 000 000 **[chosen]** | — |

### Pagination (helper-side, during collection)

These bound the **raw API traffic**, which is far larger than the normalized
envelope, so they are separate numbers and not the 256 KiB stdout bound.

| Bound | Value | Source |
|---|---|---|
| requested `limit` | 200 | DATA-009 / API maximum |
| pages per collection | 64 **[chosen]** | — |
| decoded bytes per collection | 8 MiB **[chosen]** | — |
| decoded bytes per batch | 16 MiB **[chosen]** | — |
| accepted `totalCount` | 0 – 1 000 000 **[chosen]** | — |

## DATA-008 rejection classes

Every row is a distinct reason the consumer refuses an envelope. All of them
are published as the single named kind **`malformed_response`** — protocol
rejection is never an unnamed state, never silently leaves the previous snapshot
in place without a failure overlay, and never throws.

The `id` column is the identifier used by fixture filenames, by
`tests/fixtures/index.json`, and by test names, so that AC-072 can check one
exists for each.

| id | Rejected when |
|---|---|
| `stdout_oversized` | stdout exceeds 256 KiB |
| `stdout_not_json` | stdout does not parse as JSON |
| `stdout_multiple_values` | stdout holds more than one JSON value |
| `stdout_trailing_garbage` | non-whitespace follows the JSON value |
| `envelope_not_object` | the top-level JSON value is not an object |
| `envelope_unknown_key` | a top-level key outside the nine permitted |
| `protocol_version_wrong` | `protocolVersion` is not the integer `1` |
| `nonce_missing` | `nonce` absent, empty, or not a string |
| `nonce_mismatch` | `nonce` is not the one issued for this batch |
| `meta_missing` | `meta` absent, `null`, or not an object |
| `meta_helper_version_missing` | `meta.helperVersion` absent or `null` |
| `attempted_at_malformed` | not RFC 3339 UTC ending `Z`, or over 64 chars |
| `attempted_at_too_early` | over 300 s before service launch (see DATA-008a) |
| `attempted_at_future` | after receipt time |
| `ok_not_boolean` | `ok` is `1`, `"true"`, `null`, or absent |
| `success_exit_nonzero` | `ok: true` with a non-zero exit status |
| `success_error_not_null` | `ok: true` with a non-null `error` |
| `success_observed_at_missing` | `ok: true` with `observedAt` absent or `null` |
| `success_observed_at_unordered` | not `attemptedAt <= observedAt <= receiptTime` |
| `success_data_missing` | `ok: true` with `data` absent or `null` |
| `success_data_empty` | `ok: true` with `data: {}` |
| `data_schema_violation` | `data` fails the DATA-006 shape — missing `site.id`/`site.name`, a `wan.status` outside its domain, a missing count bucket, a missing class within a bucket, a negative or non-integer total, `offlineDevices` not an array |
| `byclass_sum_mismatch` | `sum(byClass) != devicesTotal` |
| `byclass_offline_mismatch` | `byClass.down + byClass.impaired != offlineTotal` |
| `failure_exit_zero` | `ok: false` with exit status 0 |
| `failure_data_not_null` | `ok: false` with a non-null `data` |
| `failure_observed_at_not_null` | `ok: false` with a non-null `observedAt` |
| `failure_error_missing` | `ok: false` with `error` absent or `null` |
| `error_kind_missing` | `error.kind` absent or not a string |
| `error_http_status_forbidden` | `httpStatus` is an integer for a kind that forbids it |
| `error_http_status_wrong` | `httpStatus` present but not the value the kind requires |
| `error_retry_after_forbidden` | `retryAfterSec` present for a kind other than `rate_limited` |
| `error_retryable_mismatch` | `retryable` disagrees with the kind's class |
| `warnings_not_array` | `warnings` is `null` or not an array |
| `warning_shape_invalid` | a warning is not an object with a string `code` and `message` |
| `bound_exceeded` | any [Bounds](#bounds) limit on strings, arrays, depth or integers |

`attempted_at_too_early` has one exception, DATA-008a: when it is the **only**
reason for rejection, the service re-baselines its recorded launch time to the
current wall clock **once** and retries the batch. That signature — a timestamp
far in the past with everything else well-formed — is a backwards wall-clock
correction, not a bad helper. A second consecutive such rejection is a genuine
protocol error. Without it, a laptop resuming with a 40-minute-slow RTC would
reject every batch permanently until the shell restarted.

## DATA-009 invariants

Enforced **helper-side**, on every page, before accumulation. A violation fails
the collection; whether that fails the batch depends on whether BIZ-004 calls
the collection required.

The `id` column is used by fixture filenames and test names, as above.

| id | Invariant |
|---|---|
| `offset_matches_request` | the returned `offset` equals the requested offset |
| `count_equals_data_length` | `count == data.length` |
| `count_within_limit` | `0 <= count <= limit` |
| `non_terminal_page_progresses` | a non-terminal page has `count > 0` |
| `record_ids_unique` | every record carries a stable `id`, unique within the collection |
| `total_count_non_negative` | `totalCount >= 0` |
| `total_count_stable` | `totalCount` is identical on every page of one collection |
| `advance_by_validated_count` | the next offset is the previous offset plus the **validated** count, never response arithmetic |
| `max_pages_enforced` | the collection stops at 64 pages |
| `max_decoded_bytes_enforced` | the collection stops at 8 MiB decoded |
| `empty_is_valid_not_premature` | `{offset: 0, count: 0, totalCount: 0}` is a **complete empty** result; `count == 0` while `offset < totalCount` is premature and is an error |
| `reread_page_zero_matches` | after the terminal page, page 0 is re-requested and its id set and `totalCount` compared against the first read |
| `terminal_completeness` | unique accumulated records equal the terminal `totalCount` **and** the re-read passed |

`empty_is_valid_not_premature` is the one that bites if it is got wrong in the
obvious direction: treating every `count == 0` as premature makes a legitimately
empty site permanently unable to produce a successful batch, and the site that
is empty is exactly the site whose owner is most likely to be setting the plugin
up for the first time.

`reread_page_zero_matches` exists because the other invariants do **not** detect
**offset drift at constant cardinality** — delete one record and append another
between two page requests and every invariant above still passes while one
record is silently skipped. A first mismatch retries the whole collection once
from offset zero and raises `page_reread_mismatch`; a second is
`partial_response` for a required collection, or a `null` count plus a warning
for an optional one.
