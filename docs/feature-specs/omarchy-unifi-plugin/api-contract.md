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

### The IP form cannot be verified (observed 2026-09-06, Phase 12a)

`https://{consoleIP}/...` is the form the specification advertises and the form
the worked example above uses. **On a stock UniFi console it cannot be used with
TLS verification enabled**, and this is not a local quirk — it follows from the
certificate the console ships with.

Observed against a real console: the certificate is self-signed with
`CN=unifi.local` and

```
X509v3 Subject Alternative Name:
    DNS:unifi.local, DNS:localhost, DNS:[::1],
    IP Address:127.0.0.1, IP Address:FE80::1
```

There is **no IP SAN for the console's LAN address**. Pinning that certificate
as a CA establishes trust — the chain verifies — but connecting by IP then fails
hostname verification:

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: IP address mismatch,
certificate is not valid for '192.168.1.1'
```

Connecting to the same host as `unifi.local`, with the same pinned certificate,
verifies cleanly over TLSv1.3.

So the correct local configuration is the **name the certificate carries**, with
that certificate pinned:

```
apiRoot      https://unifi.local/proxy/network/integration
customCaPath the console's certificate, in PEM
```

and `unifi.local` must resolve to the console. It is not advertised over mDNS —
avahi running and `nss-mdns` installed was not enough — so it needs a `/etc/hosts`
entry or a local DNS record. A console's reverse-DNS name (`unifi.home` on the
observed network) does **not** work: it is not in the certificate either.

`allowInsecureTls` remains the documented fallback for a console whose name
cannot be made to resolve, and SEC-005/UX-009 keep it an explicit opt-in with a
permanent panel warning. It should not be the first thing a user reaches for,
which is what the IP-form example was quietly encouraging.

Authentication is the `X-API-Key` request header. The specification file
declares no `securitySchemes` and no `security` block, so the header name is
taken from the controller's Integrations documentation, not from this file.

## Read-only route allowlist (GET only)

| # | Route | Purpose | Paginated |
|---|---|---|---|
| 1 | `/v1/info` | detected `applicationVersion`, capability gate | no |
| 2 | `/v1/sites` | site discovery / name resolution | yes |
| 3 | `/v1/sites/{siteId}/devices` | adopted device inventory and state | yes |
| 4 | `/v1/sites/{siteId}/devices/{deviceId}/statistics/latest` | per-device uptime, load and uplink throughput | no |
| 5 | `/v1/sites/{siteId}/clients` | connected client list and count | yes |
| 6 | `/v1/sites/{siteId}/devices/{deviceId}` | per-device detail: ports, PoE, uplink | no |

No other route may be constructed. No method other than `GET` is implemented.

**Row 6 was `/v1/sites/{siteId}/wans` until 2026-09-07.** It returned
`{id, name}` and nothing else — inferred at Phase 5, confirmed against hardware
at Phase 12a — so it could never contribute to `DATA-006`, while costing two
HTTPS requests per batch under DATA-009a's end-of-collection re-read. Removed as
DEV-5 Option B. Row 4's purpose line said "gateway" for the same span of time;
§12b records that the route is per-device and always was.

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
7. **A device can hold several `features` at once.** The specification's enum
   is `switching | accessPoint | gateway` with `uniqueItems`, so per-role counts
   cannot be a partition of the device list. **The claim that a UniFi Dream
   Machine reports all three together was inferred from the specification and is
   contradicted by observation — see §12a below.**

Consequences for the design are tracked as open decisions in `SPEC.md §14`.


## 12a. Observed against a real controller (2026-09-06)

`applicationVersion: "10.6.101"`, nine adopted devices, 44 clients, two WANs,
TLS verified against the console's own pinned certificate. A full batch
succeeded: exit 0, `ok: true`, empty stderr.

### Confirmed

| Claim | Observation |
|---|---|
| `X-API-Key` is the header name | every request authorised; the name is not in the specification file and was taken from the Integrations docs |
| The page envelope is `{offset, limit, count, totalCount, data}` | all five fields present on all four paginated routes |
| `/v1/info` returns exactly one field | `{"applicationVersion": "10.6.101"}` |
| `/v1/sites/{id}/wans` returns identity only | `{id, name}` and nothing else, for both WANs |
| Route 4 returns uptime and uplink throughput | `uptimeSec`, `uplink.{txRateBps, rxRateBps}`, plus `cpuUtilizationPct`, `memoryUtilizationPct`, `loadAverage{1,5,15}Min`, `lastHeartbeatAt`, `nextHeartbeatAt`, `interfaces` |
| The device record's fields | `features, firmwareUpdatable, firmwareVersion, id, interfaces, ipAddress, macAddress, model, name, state, supported` |
| DATA-009a re-reads the collection | nine requests for one `info` plus four collections, each fetched twice |
| DATA-012 auto-selects a single site | `site_auto_selected` warning raised, batch proceeded |

The `state` values seen were `ONLINE` and `OFFLINE` only — a subset of the
documented enum, which is expected on a healthy site and confirms nothing about
the rest.

### Divergence: the console does not report the `gateway` feature

Every device on the observed controller reported exactly one feature:

```
['accessPoint']  x4        AC HD, U6 Lite x3
['switching']    x5        UDM Pro, US 24 PoE 250W, US 8 PoE 150W, USW Flex Mini x2
```

**The UDM Pro — which is the gateway — reports `features: ["switching"]`.** No
device on the site reports `gateway`, although the site has two WANs configured
and the specification declares `gateway` in the enum.

Whether this is a 10.6 change, or whether the Network application has never
described the console's routing role as a device *feature*, cannot be
established from one controller.

REQ-000 defines a gateway as a device whose `features` contains `gateway`, so on
this controller the consequences are:

- `wan.status` is permanently `unknown`, and `wan.uptimeSec`, `downloadBps` and
  `uploadBps` are permanently `null` (REQ-008a's "no gateway present" branch).
- REQ-002 rule 3 — the only route to **red** — is unreachable. The site is judged
  on rules 4 and 5 alone.
- `counts.gateways` is all zeros, and the panel's gateway list is empty.
- Route 4 is never requested, because it is fetched per gateway. It was verified
  here by calling it directly.

The plugin behaves exactly as specified; the specification's assumption about
the data is what is wrong. This is a Tier 3 divergence and is raised as DEV-6.

**One observation that bears on any fix:** the console device is the only one
whose `ipAddress` is not an RFC 1918 address — it reports its WAN address, a
public one, while every other device reports a `192.168.1.x` address. The
address itself is deliberately not recorded here: this repository is intended to
be published, and someone's public IP is not a fact a design document needs. Route 4 for that device returns the WAN uplink rates. So the
gateway is identifiable and its metrics are available; only the documented
signal for finding it is absent.

### DEV-5 is settled by observation

`/v1/sites/{id}/wans` returned `{id, name}` for two WANs and nothing else: no
status, no throughput, no link to a device. It cost two of the batch's nine
requests — DATA-009a fetches it twice — and its body was discarded, exactly as
DEV-5 predicted from the specification. Nothing in the response can be joined to
`gateways`, so option C (render WAN identity in the panel) has no data to render
beyond a name.

## §12b — route discovery probe, 2026-09-07

Read-only GETs against the same console (Network **10.6.101**), with the user's
approval, to establish what the integration API actually exposes rather than
what its published subset documents. **Field names only were recorded**; no
values, so nothing identifying entered this file or any transcript. The probe
scripts were shredded and nothing was written to the repository.

Full findings and their consequences are in `SPEC-v1.1-browse.md` §3. Recorded
here because they are API facts and this is the file that owns them.

### Routes that exist and were not in the published subset

| Route | Result |
|---|---|
| `GET /v1/sites/{siteId}/devices/{deviceId}` | 200 — a **superset** of the list record |
| `GET /v1/sites/{siteId}/clients/{clientId}` | 200 — **identical** to the list record |
| `GET /v1/sites/{siteId}/networks` | 200 — `{default, enabled, id, management, metadata, name, vlanId, zoneId}` |
| `GET /v1/sites/{siteId}/firewall/policies` | 200 |
| `GET /v1/sites/{siteId}/hotspot/vouchers` | 200 |

`GET /devices/{deviceId}` adds `configurationId`, `provisionedAt`,
`uplink.deviceId`, `features.switching.lags[]` and
`interfaces.ports[].{idx, connector, maxSpeedMbps, state, poe.{enabled, standard, state, type}}`.

`uplink.deviceId` is **topology** — what each device is plugged into. Nothing in
the published subset carries it.

### Route 4 is not gateway-only

`statistics/latest` returns 200 for **every** adopted device, not only gateways:
`uptimeSec`, `cpuUtilizationPct`, `memoryUtilizationPct`,
`loadAverage{1,5,15}Min`, `lastHeartbeatAt`, `nextHeartbeatAt`,
`uplink.{rxRateBps, txRateBps}`, plus `interfaces.radios[].{frequencyGHz,
txRetriesPct}` on access points.

The field map above describes this route under "Route 4 returns uptime and
uplink throughput", in a section reached from the gateway discussion. That
framing was an inference, and REQ-008a's four-gateway cap inherited it. The
route is per-device.

### The device list record carries more than the field map states

`features[]`, `firmwareVersion`, `firmwareUpdatable`, `supported`, `id`,
`ipAddress`, `macAddress`, `model`, `name`, `state`, and `interfaces` — the last
**as a list of strings** (`["ports"]`, `["radios"]`), a capability hint rather
than data. Detail requires the per-device GET.

### The client record is eight fields, and that is all there is

`id`, `name`, `type`, `access.type`, `ipAddress`, `macAddress`,
`uplinkDeviceId`, `connectedAt`. Types observed: `WIRED`, `WIRELESS`. There is
no signal strength, no byte counter, no satisfaction score. **`GET /clients/{id}`
returns nothing the list does not already carry**, which is why
`SPEC-v1.1-browse.md` §4 declines to add it to the allowlist.

### Limitations 1, 2 and 4 are now observed rather than inferred

`/v1/sites/{siteId}/events`, `/alarms`, `/health`, `/statistics`,
`/isp-metrics` and `/wan-metrics` all return **404**. So do `/v2/info` and
`/v2/sites`. No OpenAPI or Swagger document is served at any of the five
plausible paths.

**There is no history, no latency and no packet loss on this API.** Not
"undocumented" — absent.

### The private controller API is present and credential-gated

`/proxy/network/api/s/default/stat/health`, `/proxy/network/api/self/sites` and
`/proxy/network/v2/api/site/default/dashboard` all return **401, not 404**. It
exists on this console. **No login was attempted** and no field of it has been
observed; every description of its contents elsewhere in these documents is from
general knowledge and is explicitly unverified. See `SPEC-v1.1-browse.md` §11.

### Measured request cost

Nine devices, local console, sequential, each request its own handshake
(`Connection: close`):

```
GET /devices/{id}                    n=9  mean 13ms  max 15ms  total 0.12s
GET /devices/{id}/statistics/latest  n=9  mean 12ms  max 15ms  total 0.11s
a current-shape 6-request batch                                total 0.09s
```

Against REQ-017's 25 s budget this is negligible here, and the bound in
`SPEC-v1.1-browse.md` REQ-B02 exists for consoles that are not.
