# UniFi Network Integration API v1 — endpoint and field map

Source of truth for this document: the versioned official specification
`https://developer.ui.com/network/v10.4.57/openapi.json` (`UniFi Network API 10.4.57`),
extracted to `unifi-network-v1-readonly-subset.json` in this directory (6 GET
operations, 15 schemas). The installed controller's own
**Network → Integrations** documentation remains authoritative for its exact
shapes; anything below that the installed controller contradicts must be
re-verified in Phase 1 against the real device.

## API roots

The specification advertises exactly two servers:

| Family | Root | Notes |
|---|---|---|
| Local console | `https://{consoleIP}/proxy/network/integration` | MVP target |
| Cloud connector | `https://api.ui.com/v1/connector/consoles/{consoleId}/proxy/network/integration` | out of scope for v1 |

All operation paths are then `/v1/...`, so a full local URL is
`https://192.168.1.1/proxy/network/integration/v1/sites`.

`apiRoot` in `config.json` is the complete root **without** the trailing `/v1`.
The helper appends `/v1/<route>` from its route allowlist. Both families are
fixture-tested for request construction.

Authentication is the `X-API-Key` request header. The specification file
declares no `securitySchemes` and no `security` block, so the header name is
taken from the controller's Integrations documentation, not from this file.

## Read-only route allowlist (GET only)

| # | Route | Purpose | Paginated |
|---|---|---|---|
| 1 | `/v1/info` | detected `applicationVersion`, capability gate | no |
| 2 | `/v1/sites` | site discovery / name resolution | yes |
| 3 | `/v1/sites/{siteId}/devices` | adopted device inventory and state | yes |
| 4 | `/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | gateway uptime and uplink throughput | no |
| 5 | `/v1/sites/{siteId}/clients` | connected client count | yes |
| 6 | `/v1/sites/{siteId}/wans` | WAN identity only (see limitations) | yes |

No other route may be constructed. No method other than `GET` is implemented.

## Pagination contract

Every paginated response is the same page envelope, with all five fields
required:

```json
{ "offset": 0, "limit": 200, "count": 200, "totalCount": 412, "data": [ ... ] }
```

Query parameters are `offset` (integer, minimum 0, default 0) and `limit`
(integer, minimum 0, **maximum 200**, default 25). The helper always requests
`limit=200`. `filter` exists on `/v1/sites`, `/v1/sites/{siteId}/devices` and
`/v1/sites/{siteId}/clients` but is not used — filtering happens locally so the
request surface stays minimal and predictable.

This confirms the page invariants already specified in the design: validate
`offset == requested offset`, `count == data.length`, `0 <= count <= limit`,
positive progress on non-terminal pages, unique stable `id` per record, and
non-negative stable `totalCount`.

## Field map

### `/v1/info` → `Application info`
| Field | Type | Required |
|---|---|---|
| `applicationVersion` | string, e.g. `9.1.0` | yes |

This is the only capability signal the API offers. There is no feature or
capability list.

### `/v1/sites` → `Site overview`
| Field | Type | Required |
|---|---|---|
| `id` | uuid | yes |
| `internalReference` | string (legacy site name) | yes |
| `name` | string | yes |

**There is no site status or health field.**

### `/v1/sites/{siteId}/devices` → `Adopted device overview`
| Field | Type | Required |
|---|---|---|
| `id` | uuid | yes |
| `name` | string | yes |
| `model` | string | yes |
| `macAddress` | string | yes |
| `ipAddress` | string | yes |
| `state` | enum (below) | yes |
| `features` | array of `switching` \| `accessPoint` \| `gateway`, unique | yes |
| `interfaces` | array of `ports` \| `radios`, unique | yes |
| `firmwareVersion` | string | no |
| `firmwareUpdatable` | boolean | yes |
| `supported` | boolean | yes |

`state` enum, complete: `ONLINE`, `OFFLINE`, `PENDING_ADOPTION`, `UPDATING`,
`GETTING_READY`, `ADOPTING`, `DELETING`, `CONNECTION_INTERRUPTED`, `ISOLATED`,
`U5G_INCORRECT_TOPOLOGY`.

An unrecognized future `state` value is treated as `unknown` — neither online
nor offline — and raises a warning, rather than being silently counted online.

### `/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` → `Latest statistics for a device`
| Field | Type | Required |
|---|---|---|
| `interfaces` | object (`radios` array) | yes |
| `uptimeSec` | int64 | no |
| `uplink.rxRateBps` | int64 | no |
| `uplink.txRateBps` | int64 | no |
| `cpuUtilizationPct` | double | no |
| `memoryUtilizationPct` | double | no |
| `loadAverage1Min` / `5Min` / `15Min` | double | no |
| `lastHeartbeatAt` / `nextHeartbeatAt` | date-time | no |

Only `interfaces` is required; every metric this plugin displays from here is
optional and may legitimately be absent.

### `/v1/sites/{siteId}/clients` → `Client overview`
Polymorphic on `type` (`WIRED`, `WIRELESS`, `VPN`, `TELEPORT`). Common
required fields are `id`, `name`, `access`, plus `type` on the base schema.
`connectedAt` and `ipAddress` are optional. Wired/wireless variants add
required `macAddress` and `uplinkDeviceId`.

The plugin needs the **count** only. It is read from the terminal page's
`totalCount` after every page invariant has been satisfied — never from an
arithmetic shortcut.

### `/v1/sites/{siteId}/wans` → `WAN overview`
| Field | Type | Required |
|---|---|---|
| `id` | uuid | yes |
| `name` | string, e.g. `Internet 1` | yes |

**That is the entire schema.** No status, no latency, no loss, no throughput.

### Errors → `Error Message`
| Field | Type |
|---|---|
| `statusCode` | int32 |
| `statusName` | string, e.g. `UNAUTHORIZED` |
| `code` | string, e.g. `api.authentication.missing-credentials` |
| `message` | string |
| `requestId` | uuid (500s only) |
| `requestPath` | string |
| `timestamp` | date-time |

The specification documents no non-200 responses per operation and no
`Retry-After` or rate-limit response headers. Error handling therefore keys off
the HTTP status and this body shape, and the 429/`Retry-After` handling is
written defensively against a header that may never arrive.

## Capability limitations that change the product

These are properties of the API, confirmed against the published contract, not
implementation shortcuts.

1. **WAN latency is not available.** No endpoint in the read-only surface
   returns a latency figure.
2. **WAN packet loss is not available.**
3. **There is no WAN status field.** `/wans` returns identity only.
4. **There is no site status or health field.** `/sites` returns identity only.
5. **Throughput is per gateway device uplink**, from
   `statistics/latest.uplink.{rxRateBps,txRateBps}` — not a WAN-scoped metric,
   and optional.
6. **Uptime is gateway device uptime** (`uptimeSec`), not WAN link uptime, and
   optional.
7. **A device can hold several `features` at once.** A UniFi Dream Machine
   reports `gateway`, `switching` and `accessPoint` together, so per-role
   counts cannot be a partition of the device list.

Consequences for the design are tracked as open decisions in `SPEC.md §14`.
