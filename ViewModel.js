// ViewModel.js — presentation LOGIC. Everything the panel and the bar item show
// that can be decided without touching a pixel.
//
// Dual-use (see the header of Health.js). Pushing this much into a pure module
// is deliberate: it is the only genuinely automatable layer, so it carries as
// much of SPEC §12 as possible and leaves only pixel appearance to manual QA.
//
// No Intl and no toLocaleString anywhere. QML's V4 and Node's V8 do not ship
// the same ICU data, so a locale-formatted string is not reproducible across
// the two engines this file's own test corpus runs on. Times are therefore
// rendered as relative strings, built by hand.

// --- REQ-001a ------------------------------------------------------------
// The Omarchy theme exposes foreground, background, accent, urgent and muted
// (Commons/Color.qml:19-23). There is NO green, amber or red semantic token, so
// the four health levels are rendered as four theme-native VISUAL levels rather
// than four hues. A literal palette would need hex, would break under light
// themes, and would violate REQ-001.
//
// This returns a DESCRIPTOR, not a colour: JS cannot call Qt.darker, and it
// must not try — tests/lint/no_qt_in_js.sh enforces that. QML reads the
// descriptor and applies the token.
const LEVEL_RENDERING = {
  green: { token: "bar.foreground", badge: false, darkenFactor: null },
  amber: { token: "bar.foreground", badge: true, darkenFactor: null },
  red: { token: "bar.urgent", badge: false, darkenFactor: null },
  // The established dim idiom, plugins/panels/tailscale/Panel.qml:40-44.
  grey: { token: "bar.foreground", badge: false, darkenFactor: 1.55 }
}

// REQ-000's five device classes, and REQ-008a's four WAN statuses, as the words
// the panel prints. They live here, beside LEVEL_WORD, for the same reason: the
// panel must never hold a string table, because a table in QML is a table
// `node --test` cannot read.
const CLASS_WORD = {
  online: "online",
  transitional: "updating",
  down: "down",
  impaired: "impaired",
  unknown: "unknown"
}

// The order the class cells are printed in. Fixed, and fixed deliberately: a
// row whose columns move between renders is unreadable, and `for (key in obj)`
// is not required to agree between V4 and V8.
const CLASS_ORDER = ["online", "transitional", "down", "impaired", "unknown"]

// REQ-B10a. Overview's count rows are entry points into a filtered Devices
// list, so each row must carry the role value `devices[].roles` actually uses.
// The two vocabularies differ — the counter's buckets are plural nouns and the
// device's roles are the API's feature names — and a view left to map between
// them would be computing (REQ-014), in the one place a typo produces an
// always-empty list rather than an error.
//
// Declared HERE, beside the other vocabularies, and not down with the rest of
// the browse code where it was written. `countRows` is several hundred lines
// above that point, and V4 warned:
//
//   qt.qml.usedbeforedeclared: ViewModel.js:375 Variable "ROLE_FOR_COUNT_KEY"
//   is used before its declaration at 521
//
// It worked, because a `const` is initialised when the module is evaluated and
// `countRows` is only ever CALLED afterwards. But it worked by luck of call
// order, in a file whose whole premise is that two engines run it unchanged —
// and V8 says nothing at all about it, so the warning would only ever have been
// seen by whoever next read a live QML log.
const ROLE_FOR_COUNT_KEY = {
  gateways: "gateway",
  switches: "switching",
  accessPoints: "accessPoint"
}

const ROLE_PLURAL = {
  gateway: "gateways",
  switching: "switches",
  accessPoint: "access points"
}

const WAN_STATUS_WORD = {
  up: "Up",
  down: "Down",
  degraded: "Degraded",
  unknown: "Unknown"
}

// UX-002: colour is never the sole signal. Every level also has words, so the
// bar item is readable to someone who cannot distinguish the levels at all.
const LEVEL_WORD = {
  green: "healthy",
  amber: "degraded",
  red: "down",
  grey: "unknown"
}

// --- REQ-013: twenty-four panel states -----------------------------------
// Nineteen DATA-007 error kinds plus five non-error states. UX-007 requires
// each to name what failed AND the one action that would fix it — a sentence
// that only restates the error leaves the user exactly where they started.
const PANEL_SENTENCES = {
  unconfigured:
    "UniFi is not set up yet. Run scripts/configure to add your controller "
    + "address and API key.",
  site_unselected:
    "This controller has several sites and none is selected. Run "
    + "scripts/configure --site <id> with one of the ids listed below.",
  uncommitted:
    "The configuration has changed but has not been committed. Run "
    + "scripts/configure --commit to apply it.",
  credential:
    "The API key could not be read. Check that ~/.config/omarchy-unifi/api-key "
    + "exists and is readable only by you.",
  unauthorized:
    "The controller rejected the API key. Create a new key in the UniFi console "
    + "and run scripts/configure.",
  forbidden:
    "The API key does not have permission for this site. Grant it access in the "
    + "UniFi console, or select a site it can read.",
  tls:
    "The controller's certificate could not be verified. Point customCaPath at "
    + "your CA in config.json, or set allowInsecureTls if you accept the risk.",
  network:
    "The controller could not be reached. Check that it is powered on and that "
    + "apiRoot points at it.",
  timeout:
    "The controller did not answer within the time budget. The next attempt is "
    + "already scheduled; if it keeps happening, check the controller's load or "
    + "raise refreshIntervalSec.",
  rate_limited:
    "The controller is rate limiting requests. Raise refreshIntervalSec if this "
    + "keeps happening.",
  http:
    "The controller returned an unexpected HTTP status. Check the controller's "
    + "own logs, and that apiRoot points at the Network integration API.",
  unsupported:
    "This controller's Network version is not supported by this plugin. Upgrade "
    + "the controller, or check the supported versions in the README.",
  redirect:
    "The controller answered with an unexpected redirect. Check that apiRoot "
    + "points directly at the console rather than at a proxy or portal.",
  configuration_conflict:
    "Two UniFi widgets on the bar disagree about refreshIntervalSec, so polling "
    + "is suspended. Make them match, or remove one, in "
    + "~/.config/omarchy/shell.json.",
  partial_response:
    "A required list could not be read completely, so the counts would have "
    + "been wrong rather than merely incomplete. Retrying automatically; if it "
    + "persists, please report it.",
  oversized_response:
    "The controller's response was larger than this plugin will read. If your "
    + "site really is that large, please report it.",
  malformed_response:
    "The helper's output could not be understood. Retrying automatically; if it "
    + "persists, please report it.",
  helper_unavailable:
    "Python 3.9 or newer is required and was not found. Install python3, then "
    + "remove and re-add the widget.",
  internal:
    "The plugin hit an internal error. Retrying automatically; if it persists, "
    + "please report it with the details below.",

  loading:
    "Fetching the first update from your UniFi controller.",
  empty:
    "This site has no adopted devices yet. Adopt one in the UniFi console and it "
    + "will appear here.",
  stale:
    "The last successful update is too old to trust, so it is no longer shown as "
    + "current. Check the refresh error below for why updates are failing.",
  service_unavailable:
    "The UniFi service is not running. Remove the widget from the bar and add it "
    + "again to start it.",
  reconfiguring:
    "Applying the new configuration. The previous reading has been discarded "
    + "because it may belong to a different controller."
}

// UX-004's one exception, spelled out where it is easy to find: a plain refresh
// while a snapshot exists never blanks the widget, but a RELOAD does, because
// the cached snapshot may belong to a different controller entirely and showing
// it would be a lie.
const STATES_THAT_DISCARD_THE_SNAPSHOT = ["reconfiguring"]

const PANEL_STATES = Object.keys(PANEL_SENTENCES)

function sentenceFor(state) {
  const known = PANEL_SENTENCES[state]
  // DATA-007: an unrecognised kind maps to `internal` rather than being
  // rendered blank. A newer helper must not brick an older panel.
  return known === undefined ? PANEL_SENTENCES.internal : known
}

function renderingFor(healthLevel) {
  const known = LEVEL_RENDERING[healthLevel]
  return known === undefined ? LEVEL_RENDERING.grey : known
}

function wordFor(healthLevel) {
  const known = LEVEL_WORD[healthLevel]
  return known === undefined ? LEVEL_WORD.grey : known
}

function classWord(deviceClass) {
  // `own`, not `CLASS_WORD[deviceClass]`. A class of "valueOf" returned a
  // FUNCTION, and `known === undefined` was then false — so the fallback did
  // not fire. On its own that rendered engine internals; once `browseDeviceRow`
  // began passing the result through `wordCase`, it THREW, and a throw inside
  // `build` costs the whole model rather than one word. `class` is validated by
  // `checkDeviceRecord`, so this is a backstop and not a live defect — but it
  // is a backstop that had stopped working.
  const known = own(CLASS_WORD, deviceClass)
  // REQ-003: an unrecognised state is reported as unknown, never dropped and
  // never silently rendered as the empty string, which would read as "fine".
  return known === undefined ? CLASS_WORD.unknown : known
}

function wanStatusWord(status) {
  const known = own(WAN_STATUS_WORD, status)
  return known === undefined ? WAN_STATUS_WORD.unknown : known
}

// --- BIZ-003 -------------------------------------------------------------
// Zero is reserved for a value the controller actually reported as zero.
// `null` means the controller did not tell us, and rendering that as "0" would
// state a fact nobody has. This is one function so the distinction cannot be
// made correctly in one place and wrongly in another.
function formatOptional(value) {
  if (value === null || value === undefined) return "unknown"
  if (typeof value === "number" && !isFinite(value)) return "unknown"
  return String(value)
}

function formatBps(value) {
  if (value === null || value === undefined) return "unknown"
  if (typeof value !== "number" || !isFinite(value)) return "unknown"
  if (value < 1000) return value + " bps"
  if (value < 1000000) return round1(value / 1000) + " kbps"
  if (value < 1000000000) return round1(value / 1000000) + " Mbps"
  return round1(value / 1000000000) + " Gbps"
}

function formatUptime(seconds) {
  if (seconds === null || seconds === undefined) return "unknown"
  if (typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) return "unknown"
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return days + "d " + hours + "h"
  if (hours > 0) return hours + "h " + minutes + "m"
  return minutes + "m"
}

function round1(value) {
  return String(Math.round(value * 10) / 10)
}

// --- AC-071 (string half) ------------------------------------------------
// Built by hand rather than with toLocaleString, which is banned: the two
// engines disagree. UX-007 requires this to be recomputed at least every 15 s
// while the panel is open — that is the QML half; this is the formatter it
// calls, and its boundaries are what the AUTO test pins.
function relativeFuture(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds)) return "unknown"
  if (seconds <= 0) return "now"
  if (seconds < 60) return "in " + Math.floor(seconds) + "s"
  if (seconds < 3600) return "in " + Math.floor(seconds / 60) + "m"
  if (seconds < 86400) return "in " + Math.floor(seconds / 3600) + "h"
  return "in " + Math.floor(seconds / 86400) + "d"
}

function relativePast(seconds) {
  if (typeof seconds !== "number" || !isFinite(seconds)) return "never"
  if (seconds < 0) return "just now"
  if (seconds < 10) return "just now"
  if (seconds < 60) return Math.floor(seconds) + "s ago"
  if (seconds < 3600) return Math.floor(seconds / 60) + "m ago"
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago"
  return Math.floor(seconds / 86400) + "d ago"
}

// --- AC-063 --------------------------------------------------------------
// The "and N more" line is computed from counts.offlineTotal, an independent
// integer, and NEVER from the length of the truncated array.
//
// This is not a nicety. The helper bounds the array to ten; a site with five
// hundred devices down would otherwise render "and 0 more" and report an
// outage as if ten machines were affected.
const OFFLINE_LIST_BOUND = 10

function offlineList(snapshot) {
  const data = snapshot || {}
  const counts = data.counts || {}
  const listed = (data.offlineDevices || []).slice(0, OFFLINE_LIST_BOUND)
  const total = typeof counts.offlineTotal === "number" ? counts.offlineTotal : listed.length
  const remainder = total - listed.length
  return {
    devices: listed.map(deviceRow),
    total: total,
    truncated: remainder > 0,
    moreLabel: remainder > 0 ? "and " + remainder + " more" : ""
  }
}

// A device as the panel prints it. The raw fields are kept alongside the
// rendered ones so the row stays inspectable in a test failure, and so a future
// column does not need a second pass over the array.
function deviceRow(device) {
  const entry = device || {}
  return {
    id: typeof entry.id === "string" ? entry.id : "",
    name: entry.name,
    model: entry.model,
    state: entry.state,
    class: entry.class,
    nameText: displayName(entry),
    modelText: typeof entry.model === "string" && entry.model !== ""
      ? entry.model : "unknown model",
    classText: classWord(entry.class)
  }
}

// A device with no name is not a formatting problem, it is the common case for
// a freshly adopted unit. Falling back to the id keeps the row identifiable;
// falling back to the empty string would print a blank line the user cannot act
// on.
function displayName(entry) {
  if (typeof entry.name === "string" && entry.name !== "") return entry.name
  if (typeof entry.id === "string" && entry.id !== "") return entry.id
  return "unnamed device"
}

// --- REQ-008 / REQ-008a: the WAN rows ------------------------------------
// Every row is always present. BIZ-003 forbids rendering an absent optional
// metric as 0, and REQ-008 forbids hiding it — a row that disappears reads as
// "there is no uplink", which is a different and wrong claim.
function wanRows(wan) {
  const data = wan || {}
  return [
    { key: "status", label: "WAN", value: wanStatusWord(data.status) },
    { key: "uptime", label: "Uptime", value: formatUptime(data.uptimeSec) },
    { key: "download", label: "Download", value: formatBps(data.downloadBps) },
    { key: "upload", label: "Upload", value: formatBps(data.uploadBps) }
  ]
}

// REQ-008a: `wan` carries the aggregate status plus the PRIMARY gateway's
// metrics, and the panel additionally lists every gateway individually. This is
// that list. Gateways beyond the fourth carry no statistics (they were never
// fetched), and say so rather than reporting them as unknown-for-some-reason.
function gatewayRows(gateways) {
  const list = Array.isArray(gateways) ? gateways : []
  const rows = []
  for (let i = 0; i < list.length; i++) {
    const entry = list[i] || {}
    const hasMetrics = entry.uptimeSec !== null && entry.uptimeSec !== undefined
      || entry.downloadBps !== null && entry.downloadBps !== undefined
      || entry.uploadBps !== null && entry.uploadBps !== undefined
    rows.push({
      id: typeof entry.id === "string" ? entry.id : "",
      nameText: displayName(entry),
      modelText: typeof entry.model === "string" && entry.model !== ""
        ? entry.model : "unknown model",
      classText: classWord(entry.class),
      uptimeText: formatUptime(entry.uptimeSec),
      downloadText: formatBps(entry.downloadBps),
      uploadText: formatBps(entry.uploadBps),
      hasMetrics: hasMetrics
    })
  }
  return rows
}

// --- REQ-009: the role count rows ----------------------------------------
function countRows(counts) {
  if (!counts) return []
  const roles = [
    { key: "gateways", label: "Gateways" },
    { key: "switches", label: "Switches" },
    { key: "accessPoints", label: "Access points" }
  ]
  const rows = []
  for (let i = 0; i < roles.length; i++) {
    const bucket = counts[roles[i].key]
    if (!bucket) continue
    const cells = []
    let total = 0
    for (let j = 0; j < CLASS_ORDER.length; j++) {
      const key = CLASS_ORDER[j]
      const value = typeof bucket[key] === "number" ? bucket[key] : 0
      total += value
      // SPEC-AMD-3: a class with no devices in it is DROPPED, not dimmed. On a
      // healthy site twelve of the fifteen cells read zero, and fifteen numbers
      // of which twelve are noise is harder to read than three. What is never
      // dropped is a non-zero count, and `total` is summed over all five
      // classes before the filter, so the AC-025 not-a-partition arithmetic is
      // unaffected by what is displayed.
      if (value === 0) continue
      cells.push({ key: key, label: CLASS_WORD[key], value: value })
    }
    // A role with no devices at all contributes an empty row. "Gateways" with
    // nothing after it is a question the panel cannot answer, so the row goes
    // too — the absence of the row is the same statement, with less ink.
    if (total === 0) continue
    // REQ-B10a. The row is an entry point: activating it opens Devices filtered
    // to this role, so it carries the role value `devices[].roles` uses rather
    // than leaving the view to translate the counter's plural noun into it.
    rows.push({ key: roles[i].key, role: ROLE_FOR_COUNT_KEY[roles[i].key],
      label: roles[i].label, total: total, cells: cells })
  }
  return rows
}

// AC-025: the label the not-a-partition flag is attached to. It names the unique
// total, because "these rows do not sum to the total" is only useful next to the
// number they do not sum to.
function roleCountsNote(counts) {
  if (!counts) return ""
  const total = typeof counts.devicesTotal === "number" ? counts.devicesTotal : 0
  return "A device is counted in every role it reports, so these rows total more "
    + "than the " + total + " adopted device" + (total === 1 ? "" : "s") + "."
}

// --- AC-052 / DATA-006a: the meta rows -----------------------------------
// Rendered as "unknown" rather than omitted, so an `unconfigured` failure — the
// case with the least information and the most need for it — still shows the
// same four rows in the same places.
// Codes the panel already renders through a dedicated affordance. Repeating
// them in the warning list is not extra information, it is the same sentence
// twice — `insecure_tls` appeared both as UX-009's permanent row and as a
// warning, and `custom_ca_in_use` both as its own line and as a warning.
//
// `site_auto_selected` is here for a different reason. DATA-012 raises it every
// batch for the ordinary single-site controller, so it was a permanent entry in
// a list headed "Warnings" for a decision that was made correctly and needs no
// action. It is still disclosed — as part of the Site row below — because the
// user should know the plugin chose for them; it is just not a warning.
// Approved by the user 2026-09-06; DATA-012's behaviour is unchanged.
const WARNINGS_WITH_THEIR_OWN_ROW = [
  "site_auto_selected",
  "custom_ca_in_use",
  "insecure_tls"
]

function hasWarning(warnings, code) {
  const list = Array.isArray(warnings) ? warnings : []
  for (let i = 0; i < list.length; i++) {
    if (list[i] && list[i].code === code) return true
  }
  return false
}

function metaRows(meta, snapshot, warnings) {
  const data = meta || {}
  // DATA-006a says `meta.siteId` is the COMMITTED value, and it is null when
  // DATA-012 auto-selected the controller's only site. The panel then reported
  // "Site id: unknown" for a site whose NAME it was displaying in the hero two
  // rows above — which is not a nullable field being rendered honestly, it is
  // the panel claiming not to know something it just told you. The snapshot
  // carries the id actually in use; `meta` remains the authority when it has
  // one, because a committed id is the thing the user configured.
  const site = snapshot && snapshot.site ? snapshot.site : null
  const siteId = data.siteId !== null && data.siteId !== undefined
    ? data.siteId : (site ? site.id : null)
  const siteName = site && typeof site.name === "string" && site.name !== ""
    ? site.name : null
  const autoSelected = hasWarning(warnings, "site_auto_selected")
  return withCopy([
    { key: "site", label: "Site",
      value: formatOptional(siteName)
        + (autoSelected && siteName !== null ? " (auto-selected)" : "") },
    { key: "apiRootHost", label: "Controller", value: formatOptional(data.apiRootHost) },
    { key: "siteId", label: "Site id", value: formatOptional(siteId),
      copy: copyableValue(siteId) },
    { key: "helperVersion", label: "Helper", value: formatOptional(data.helperVersion) },
    { key: "commitGeneration", label: "Config generation",
      value: formatOptional(data.commitGeneration) }
  ])
}

// --- REQ-013a: the warning rows ------------------------------------------
function warningRows(warnings) {
  const list = Array.isArray(warnings) ? warnings : []
  const rows = []
  for (let i = 0; i < list.length; i++) {
    const entry = list[i] || {}
    const code = typeof entry.code === "string" ? entry.code : "unknown"
    if (WARNINGS_WITH_THEIR_OWN_ROW.indexOf(code) !== -1) continue
    const message = typeof entry.message === "string" && entry.message !== ""
      ? entry.message : code
    // The code is kept beside the message because a bug report that quotes the
    // sentence is not searchable, and one that quotes the code is.
    rows.push({ key: code + "#" + i, code: code, text: message })
  }
  return rows
}

// --- UX-006a: the discovered sites ---------------------------------------
// DATA-012 carries the {id, name} pairs in a `sites_discovered` warning rather
// than in `data`, because the batch that discovers them has no `data` at all.
function sitesFromWarnings(warnings) {
  const list = Array.isArray(warnings) ? warnings : []
  const sites = []
  for (let i = 0; i < list.length; i++) {
    const entry = list[i] || {}
    if (entry.code !== "sites_discovered") continue
    const detail = entry.detail || {}
    const found = Array.isArray(detail.sites) ? detail.sites : []
    for (let j = 0; j < found.length; j++) {
      const site = found[j] || {}
      if (typeof site.id !== "string" || site.id === "") continue
      const name = typeof site.name === "string" && site.name !== ""
        ? site.name : "unnamed site"
      sites.push({ id: site.id, name: name, label: name + "  —  " + site.id })
    }
  }
  return sites
}

// --- v1.1: the browse lists (REQ-B11 … REQ-B17) --------------------------
//
// Everything the Devices and Clients pages render. It lives in this file rather
// than in a sixth dual-use module because HC-16 forbids these files importing
// one another: a `Browse.js` would have to carry its own copies of
// `formatOptional`, `formatUptime`, `relativePast`, `classWord` and
// `displayName`, and the entire reason those are single functions is that
// BIZ-003's null-is-not-zero rule must not be able to be right in one file and
// wrong in another.

// REQ-B11's class ranking, duplicated from `normalize.py`'s
// `_BROWSE_CLASS_RANK`. `tests/model/consistency.test.js` and
// `tests/test_unifi_status.py` both parse the order out of the REQ-B11 sentence
// in SPEC-v1.1-browse.md, so the two copies are held to one authority rather
// than to each other.
//
// It is NOT used to sort. REQ-B01 puts the ordering in the helper so that every
// consumer sees one order, and sorting again here would make a helper that
// ordered wrongly indistinguishable from one that ordered rightly — REQ-B01's
// guarantee would still be false and nothing would ever say so. So this ranking
// CHECKS, via `firstBrowseOrderViolation`, and the check is what AC-B07 asserts.
const BROWSE_CLASS_RANK = {
  down: 0,
  impaired: 1,
  unknown: 2,
  transitional: 3,
  online: 4
}

// The observed values are WIRED and WIRELESS; the published schema also lists
// VPN and TELEPORT. An unrecognised type renders AS ITSELF and never as
// "unknown", because — unlike a device `state`, which decides a health class —
// a client type decides nothing, and printing "unknown" over a value the
// controller stated plainly would be the panel losing information it has.
const CLIENT_TYPE_WORD = {
  WIRED: "Wired",
  WIRELESS: "Wireless",
  VPN: "VPN",
  TELEPORT: "Teleport"
}

const BROWSE_VIEWS = ["overview", "devices", "clients"]

// AC-B09's whole point. This sentence and an empty port table are different
// claims: this one says the helper never asked, and an empty table says the
// controller answered and there are no ports. Rendering the second for the
// first would assert, on a 48-port switch whose detail was dropped by
// DATA-B04's budget, that the switch has no ports.
const DETAIL_NOT_FETCHED = "Details not fetched for this device."

// --- REQ-B11 / REQ-B12: the order, as a check ----------------------------

// A lookup that cannot fall through to `Object.prototype`.
//
// Three of the maps below are indexed by strings that come from the controller
// and are NOT validated against a closed set: `clients[].type` (protocol-v1.md
// says so explicitly), `devices[].id` and `uplinkDeviceId`. `map[key]` for
// "valueOf" returns a FUNCTION, and `clientTypeWord("valueOf")` rendered
// `function valueOf() { [native code] }` into the client row. Not a security
// hole — everything here is display-only — but a row of engine internals where
// a word should be, from one word in one API response.
function own(map, key) {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined
}

function browseOrderKey(device) {
  const entry = device || {}
  const klass = typeof entry.class === "string" ? entry.class : ""
  const rank = own(BROWSE_CLASS_RANK, klass) === undefined
    ? CLASS_ORDER.length : BROWSE_CLASS_RANK[klass]
  return {
    rank: rank,
    // `is_gateway(device)` in the helper is `"gateway" in roles_of(device)`,
    // and `roles` is emitted from that same set — so the emitted record is
    // enough to reproduce the helper's key. If the two ever part company this
    // reproduction stops being valid, which is why it is asserted over the
    // whole accept corpus rather than over a hand-written device.
    gateway: hasRole(entry.roles, "gateway") ? 0 : 1,
    name: lowerOf(entry.name),
    id: typeof entry.id === "string" ? entry.id : ""
  }
}

function compareBrowseOrder(a, b) {
  const left = browseOrderKey(a)
  const right = browseOrderKey(b)
  if (left.rank !== right.rank) return left.rank < right.rank ? -1 : 1
  if (left.gateway !== right.gateway) return left.gateway < right.gateway ? -1 : 1
  if (left.name !== right.name) return left.name < right.name ? -1 : 1
  if (left.id !== right.id) return left.id < right.id ? -1 : 1
  return 0
}

// REQ-B12. Clients are ordered by name, case-insensitively, then by id — with
// the SAME fallback chain the row renders, so the list is sorted by the string
// the user is actually looking at. Sorting by the raw `name` would scatter every
// unnamed client to the front under the empty string while the panel showed
// them by IP address, and the order would look arbitrary.
function clientOrderKey(client) {
  const entry = client || {}
  return {
    name: lowerOf(clientDisplayName(entry)),
    id: typeof entry.id === "string" ? entry.id : ""
  }
}

function compareClientOrder(a, b) {
  const left = clientOrderKey(a)
  const right = clientOrderKey(b)
  if (left.name !== right.name) return left.name < right.name ? -1 : 1
  if (left.id !== right.id) return left.id < right.id ? -1 : 1
  return 0
}

// The index of the first entry that is out of order, or -1. An index rather
// than a boolean: when this fires in a corpus of two hundred devices, "not
// ordered" is not a usable failure message and "entry 137 sorts before entry
// 136" is.
function firstOutOfOrder(entries, compare) {
  const list = entries || []
  for (let i = 1; i < list.length; i++) {
    if (compare(list[i - 1], list[i]) > 0) return i
  }
  return -1
}

function firstBrowseOrderViolation(devices) {
  return firstOutOfOrder(devices, compareBrowseOrder)
}

function firstClientOrderViolation(clients) {
  return firstOutOfOrder(clients, compareClientOrder)
}

function hasRole(roles, role) {
  const list = roles || []
  for (let i = 0; i < list.length; i++) {
    if (list[i] === role) return true
  }
  return false
}

// --- REQ-B13: search -----------------------------------------------------
//
// Substring, case-insensitive, and deliberately not fuzzy: a user must be able
// to say why a row matched. `indexOf` is the whole algorithm, and that is the
// requirement rather than a shortcut.

function lowerOf(value) {
  return typeof value === "string" ? value.toLowerCase() : ""
}

function matchesSearch(haystack, term) {
  if (term === "") return true
  return haystack.indexOf(term) !== -1
}

// Fields are joined with a newline the search term can never contain — the
// field is single-line and the term is trimmed — so "co\nmodel" cannot match
// across the seam between two fields and produce a row the user cannot explain.
function haystackOf(fields) {
  const parts = []
  for (let i = 0; i < fields.length; i++) {
    const value = fields[i]
    if (typeof value === "string" && value !== "") parts.push(value.toLowerCase())
  }
  return parts.join("\n")
}

// --- REQ-B14: the rows ---------------------------------------------------

// The device name a row prints, resolved once so the sort key, the search
// haystack and the label can never be three different strings.
function browseDeviceRow(device) {
  const entry = device || {}
  const metrics = entry.metrics || null
  const nameText = displayName(entry)
  const ip = typeof entry.ipAddress === "string" && entry.ipAddress !== ""
    ? entry.ipAddress : ""
  const uptime = metrics ? metrics.uptimeSec : null
  const uptimeKnown = typeof uptime === "number" && isFinite(uptime) && uptime >= 0
  // The class word is NOT in here. It is `tokenText`, rendered as a fixed
  // right-hand column so the eye can run down one edge and find every broken
  // device — which is what a list ordered by brokenness is for. Leaving it at
  // the head of a `·`-joined line made it one more word in a sentence.
  const segments = []
  segments.push(ip !== "" ? ip : "IP unknown")
  // "uptime when known" (REQ-B14). Omitted rather than printed as "unknown",
  // because the row's job is a glance and a third of the line saying nothing is
  // worse than a shorter line. The value is still on the row object as
  // `uptimeText`, where BIZ-003's "unknown" is what it says.
  if (uptimeKnown) segments.push("up " + formatUptime(uptime))
  return {
    id: typeof entry.id === "string" ? entry.id : "",
    name: entry.name,
    model: entry.model,
    state: entry.state,
    class: entry.class,
    roles: entry.roles || [],
    nameText: nameText,
    modelText: typeof entry.model === "string" && entry.model !== ""
      ? entry.model : "unknown model",
    // The same name the client row uses for the same job — the second line of
    // identity under the name — so Phase B3's row delegate is genuinely shared
    // rather than two delegates that look alike. `modelText` stays beside it
    // for anything that wants the field by its own name.
    secondaryText: typeof entry.model === "string" && entry.model !== ""
      ? entry.model : "unknown model",
    classText: wordCase(classWord(entry.class)),
    // The status word, carried under the SAME name the client row uses for the
    // same job, so the shared row delegate reads one field rather than
    // branching on which list it is drawing.
    tokenText: wordCase(classWord(entry.class)),
    // Whether that token is the reason the row sorts where it does. The
    // delegate colours on this rather than comparing class words itself
    // (REQ-014), and `down` is not the only value that must stand out.
    tokenUrgent: entry.class === "down" || entry.class === "impaired",
    ipText: formatOptional(ip === "" ? null : ip),
    uptimeText: formatUptime(uptime),
    metaText: segments.join("  ·  "),
    // REQ-B21: the MAC is not on the row, only in the detail. It is in the
    // search haystack, which renders nothing.
    searchText: haystackOf([nameText, entry.model, entry.ipAddress,
      entry.macAddress]),
    // AC-B09's discriminator, as a boolean rather than a `detail !== null` in
    // the view: `null` and `{ports: []}` must reach different renderings, and a
    // truthiness test in QML would collapse them the moment `detail` were `{}`.
    detailFetched: entry.detail !== null && entry.detail !== undefined,
    // `firmwareUpdatable` is nullable, so `=== true` and not truthiness: `null`
    // is "the controller did not say", and marking a device as updatable on
    // that basis would be inventing the fact.
    updateAvailable: entry.firmwareUpdatable === true
  }
}

// AC-B11. A client with no name renders its IP; with neither, its id; never the
// empty string, which would be an unclickable blank row in a list whose whole
// purpose is finding one machine.
function clientDisplayName(entry) {
  if (typeof entry.name === "string" && entry.name !== "") return entry.name
  if (typeof entry.ipAddress === "string" && entry.ipAddress !== "") return entry.ipAddress
  if (typeof entry.id === "string" && entry.id !== "") return entry.id
  return "unnamed client"
}

function clientTypeWord(type) {
  if (typeof type !== "string" || type === "") return "unknown"
  const known = own(CLIENT_TYPE_WORD, type)
  return known === undefined ? type : known
}

function browseClientRow(client, uplinkName, nowWall) {
  const entry = client || {}
  const nameText = clientDisplayName(entry)
  const ip = typeof entry.ipAddress === "string" && entry.ipAddress !== ""
    ? entry.ipAddress : ""
  // Not repeated when the name already IS the IP address (AC-B11's fallback),
  // which would print the same string twice on one row.
  const ipText = ip !== "" && ip !== nameText ? ip : ""
  const connected = connectedText(entry.connectedAt, nowWall)
  // The type word is `tokenText`, not the head of this line — see the device
  // row for why.
  const segments = []
  if (uplinkName !== "") segments.push("via " + uplinkName)
  if (connected !== "") segments.push("connected " + connected)
  return {
    id: typeof entry.id === "string" ? entry.id : "",
    name: entry.name,
    type: entry.type,
    nameText: nameText,
    ipText: ipText,
    // The same name the device row uses for the same job — the second line of
    // identity under the name — so Phase B3's row delegate is genuinely shared
    // rather than two delegates that look alike.
    secondaryText: ipText,
    typeText: clientTypeWord(entry.type),
    tokenText: clientTypeWord(entry.type),
    // A client is never urgent on this axis: it is connected, or it is not in
    // the list at all. Present so the two row shapes stay identical.
    tokenUrgent: false,
    uplinkText: uplinkName,
    connectedText: connected,
    metaText: segments.join("  ·  "),
    searchText: haystackOf([nameText, entry.ipAddress, entry.macAddress,
      entry.type, uplinkName])
  }
}

// REQ-B14's "via <uplink device name>", and REQ-B13's client search field of
// the same name. Both need the device list to resolve an id, so it is resolved
// once per build and not once per row.
//
// The unresolved case is real and is not an error: `devices[]` is bounded at
// DEVICES_LISTED_MAX and by DATA-B04's byte budget, so on a large site a client
// can legitimately be uplinked to a device that was not listed. It says so,
// rather than printing a bare uuid the user cannot act on or dropping the
// segment, which would read as "connected to nothing".
function uplinkNameFor(names, id) {
  if (typeof id !== "string" || id === "") return ""
  const known = own(names, id)
  return known === undefined ? "an unlisted device" : known
}

function uplinkNames(devices) {
  const names = {}
  const list = devices || []
  for (let i = 0; i < list.length; i++) {
    const entry = list[i] || {}
    if (typeof entry.id === "string" && entry.id !== "") {
      names[entry.id] = displayName(entry)
    }
  }
  return names
}

// --- REQ-B14: the detail blocks ------------------------------------------

// The inverse of `uplinkNames`: for each device, the names of the devices that
// plug INTO it.
//
// Derived, because the API does not carry it. `interfaces.ports[]` is
// `{idx, connector, maxSpeedMbps, state, poe}` and nothing more — no peer, no
// port label — so "port 5 goes to the kitchen switch" is not expressible from
// the six allowlisted GETs and is not attempted here. `uplink.deviceId` is
// device-level topology (api-contract.md:337), and inverting it is the whole of
// what can honestly be said.
//
// Built from the LISTED devices only. A downlink outside `devices[]` — dropped
// by DEVICES_LISTED_MAX or the DATA-B04 budget — cannot be named, and inventing
// a count that includes records the panel does not hold would make the row
// disagree with the list beside it.
// REQ-B24 (SPEC-AMD-6). A row is copyable when there is something real to
// copy — never the BIZ-003 placeholder. Copying the word "unknown" onto the
// clipboard is worse than nothing: it silently replaces whatever the user had.
//
// The RAW value, not the rendered one. What lands on the clipboard should be
// what the reader would have typed, so a formatted string that happens to read
// well in a two-column layout is not it.
function copyableValue(value) {
  return typeof value === "string" && value !== "" ? value : ""
}

// Every row carries the field, whether or not it has anything in it, so the
// view can bind one name and never test for its presence (REQ-014).
function withCopy(rows) {
  for (let i = 0; i < rows.length; i++) {
    if (typeof rows[i].copy !== "string") rows[i].copy = ""
  }
  return rows
}

function downlinkNames(devices) {
  const found = {}
  const list = devices || []
  for (let i = 0; i < list.length; i++) {
    const entry = list[i] || {}
    const up = entry.uplinkDeviceId
    if (typeof up !== "string" || up === "") continue
    // A device that claims itself as its own uplink would otherwise be listed
    // as its own downlink. The controller has never done this; the guard costs
    // one comparison and the alternative is a row that reads as a loop.
    if (up === entry.id) continue
    if (!Object.prototype.hasOwnProperty.call(found, up)) found[up] = []
    found[up].push(displayName(entry))
  }
  return found
}

// REQ-B14. "3 devices" and then the names, rather than names alone: the count
// is the fact worth having at a glance on a gateway with twenty of them, and it
// is read from the array the panel actually holds so it cannot disagree with
// what is listed below it.
const DOWNLINKS_NAMED_MAX = 4

function downlinkText(downlinks, id) {
  const kids = uniqueSorted(own(downlinks, id) || [])
  if (kids.length === 0) return "none"
  const shown = kids.slice(0, DOWNLINKS_NAMED_MAX)
  const rest = kids.length - shown.length
  const noun = kids.length === 1 ? " device" : " devices"
  return kids.length + noun + " — " + shown.join(", ")
    + (rest > 0 ? " and " + rest + " more" : "")
}

function uniqueSorted(values) {
  const out = values.slice(0)
  out.sort(function (a, b) {
    const left = String(a).toLowerCase()
    const right = String(b).toLowerCase()
    if (left < right) return -1
    if (left > right) return 1
    return 0
  })
  return out
}

function deviceDetail(device, names, downlinks) {
  const entry = device || {}
  const detail = entry.detail === undefined ? null : entry.detail
  const metrics = entry.metrics || null
  // `!== null` rather than truthiness, and here — unlike `firmwareUpdatable`
  // below — the two are equivalent: `checkDeviceRecord` rejects a `detail` that
  // is neither an object nor an explicit null, so no falsy non-null value
  // reaches this line. Recorded because the mutation survives and a later
  // reader would otherwise spend the same twenty minutes finding out why.
  const fetched = detail !== null
  const ports = fetched ? (detail.ports || []) : []
  const radios = fetched ? (detail.radios || []) : []
  return {
    id: typeof entry.id === "string" ? entry.id : "",
    nameText: displayName(entry),
    fetched: fetched,
    // Non-empty exactly when `fetched` is false. AC-B09 asserts both halves,
    // because a sentence that is always present and a sentence that is never
    // present both pass a test that only looks at one device.
    unavailableText: fetched ? "" : DETAIL_NOT_FETCHED,
    updateAvailable: entry.firmwareUpdatable === true,
    updateText: entry.firmwareUpdatable === true ? "update available" : "",
    rows: withCopy([
      { key: "firmware", label: "Firmware", value: formatOptional(entry.firmwareVersion) },
      // The device's own address. It is on the collapsed row inside the
      // `·`-joined context line, which is fine to glance at and useless to
      // copy from — the same gap the client rows had.
      { key: "ip", label: "IP", value: formatOptional(entry.ipAddress),
        copy: copyableValue(entry.ipAddress) },
      { key: "mac", label: "MAC", value: formatOptional(entry.macAddress),
        copy: copyableValue(entry.macAddress) },
      { key: "uplink", label: "Uplink", value: uplinkLabel(names, entry.uplinkDeviceId) },
      { key: "downlinks", label: "Downlinks",
        value: downlinkText(downlinks || {}, entry.id) },
      { key: "cpu", label: "CPU", value: formatPct(metrics ? metrics.cpuUtilizationPct : null) },
      { key: "memory", label: "Memory", value: formatPct(metrics ? metrics.memoryUtilizationPct : null) },
      { key: "download", label: "Download", value: formatBps(metrics ? metrics.downloadBps : null) },
      { key: "upload", label: "Upload", value: formatBps(metrics ? metrics.uploadBps : null) }
    ]),
    ports: mapRows(ports, portRow),
    radios: mapRows(radios, radioRow),
    // "The controller answered and there are none" — only ever said when the
    // detail was actually fetched.
    portsEmptyText: fetched && ports.length === 0 ? "No ports reported." : "",
    radiosEmptyText: fetched && radios.length === 0 ? "No radios reported." : ""
  }
}

// `uplinkDeviceId` is null whenever `detail` is (it is read from route 6's
// body), so "unknown" here means either "this device has no uplink" or "we did
// not fetch its detail" — and the detail block says which, two lines up.
function uplinkLabel(names, id) {
  if (typeof id !== "string" || id === "") return "unknown"
  return uplinkNameFor(names, id)
}

function clientDetail(client, uplinkName) {
  const entry = client || {}
  return {
    id: typeof entry.id === "string" ? entry.id : "",
    nameText: clientDisplayName(entry),
    rows: withCopy([
      // The IP is on the collapsed row too, as `secondaryText` — but there it
      // is unlabelled, in a column that elides, and an address the reader has
      // to infer the meaning of is not one they can act on.
      { key: "ip", label: "IP", value: formatOptional(entry.ipAddress),
        copy: copyableValue(entry.ipAddress) },
      // REQ-B21 / D3: this is the only place a client MAC address is rendered,
      // and it is rendered only because the user expanded the row.
      { key: "mac", label: "MAC", value: formatOptional(entry.macAddress),
        copy: copyableValue(entry.macAddress) },
      { key: "access", label: "Access", value: formatOptional(entry.accessType) },
      { key: "uplink", label: "Uplink", value: uplinkName === "" ? "unknown" : uplinkName },
      { key: "connected", label: "Connected", value: formatInstant(entry.connectedAt) }
    ])
  }
}

function mapRows(entries, builder) {
  const rows = []
  for (let i = 0; i < entries.length; i++) rows.push(builder(entries[i]))
  return rows
}

function portRow(port) {
  const entry = port || {}
  return {
    idx: entry.idx,
    idxText: typeof entry.idx === "number" ? String(entry.idx) : "?",
    connectorText: formatOptional(entry.connector),
    stateText: formatOptional(entry.state),
    speedText: formatSpeedMbps(entry.maxSpeedMbps),
    poeText: poeText(entry.poe),
    isUp: entry.state === "UP"
  }
}

// A port with `poe: null` reports no PoE at all; a port with `enabled: false`
// has PoE and it is switched off. These are a property of the hardware and a
// setting respectively, and someone working out why a camera has no power needs
// to be able to tell them apart — so the first renders nothing and the second
// says so.
function poeText(poe) {
  if (poe === null || poe === undefined) return ""
  if (poe.enabled !== true) return "PoE off"
  const parts = ["PoE"]
  if (typeof poe.standard === "string" && poe.standard !== "") parts.push(poe.standard)
  if (typeof poe.state === "string" && poe.state !== "") parts.push(poe.state)
  return parts.join(" ")
}

function radioRow(radio) {
  const entry = radio || {}
  const frequency = entry.frequencyGHz
  return {
    frequencyText: typeof frequency === "number" && isFinite(frequency)
      ? round1(frequency) + " GHz" : "unknown",
    retriesText: formatPct(entry.txRetriesPct)
  }
}

// AC-B10. `null` is unknown, never 0 — the same rule `formatOptional` and
// `formatBps` apply, extended to the two utilisation percentages, which are the
// fields most likely to be absent because `statistics/latest` is the collection
// DATA-B04's budget drops first.
function formatPct(value) {
  if (value === null || value === undefined) return "unknown"
  if (typeof value !== "number" || !isFinite(value)) return "unknown"
  return round1(value) + "%"
}

function formatSpeedMbps(value) {
  if (value === null || value === undefined) return "unknown"
  if (typeof value !== "number" || !isFinite(value) || value < 0) return "unknown"
  if (value < 1000) return value + " Mbps"
  return round1(value / 1000) + " Gbps"
}

// --- REQ-B17: the two time renderings ------------------------------------
//
// `connectedAt` is an RFC 3339 string and the panel needs both a relative form
// for the row and an absolute one for the detail. Both are built by hand:
// `toLocaleString` is banned (V4 and V8 do not ship the same ICU data) and
// `Date.parse` has implementation-defined behaviour outside the ISO grammar,
// so the grammar is matched explicitly and `Date.UTC` — which is fully
// specified — does the arithmetic.
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:([Zz])|([+-])(\d{2}):(\d{2}))$/

function parseRfc3339(value) {
  if (typeof value !== "string") return null
  const m = RFC3339.exec(value)
  if (!m) return null
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  const hour = Number(m[4])
  const minute = Number(m[5])
  const second = Number(m[6])
  const ms = Date.UTC(year, month - 1, day, hour, minute, second)
  if (!isFinite(ms)) return null
  // `Date.UTC` ROLLS OVER rather than failing: month 13 becomes the following
  // January, 30 February becomes 2 March, hour 25 becomes tomorrow. So
  // "2026-13-99T09:14:00Z" produced "2027-04-09 09:14 UTC" — a plausible
  // instant, confidently wrong, which is worse than "unknown" precisely because
  // it looks right. The grammar above accepts two digits; only this rejects a
  // date that does not exist. A round trip rather than six range checks,
  // because it also catches 30 February, which no range check does.
  const back = new Date(ms)
  if (back.getUTCFullYear() !== year || back.getUTCMonth() + 1 !== month
      || back.getUTCDate() !== day || back.getUTCHours() !== hour
      || back.getUTCMinutes() !== minute || back.getUTCSeconds() !== second) {
    return null
  }
  const offset = m[7] ? 0 : (Number(m[9]) * 3600 + Number(m[10]) * 60)
    * (m[8] === "-" ? -1 : 1)
  return ms / 1000 - offset
}

// The absolute instant, in the reader's LOCAL time and unlabelled.
//
// This was UTC first, on the argument that a local rendering makes a pure
// module's output depend on the process time zone — the same class of hidden
// environmental input the `Intl` ban exists to prevent. The argument is true and
// is not the deciding one: the panel exists to be compared against the
// controller's own UI and against the reader's clock, and both are local. A
// "UTC" suffix is a conversion the reader has to do in their head, at the one
// moment they are trying to line an event up against something else.
//
// The cost is paid in the tests rather than avoided. `TZ` is pinned to
// `Asia/Kolkata` in both engines' runners — a +05:30 zone with no DST, so a
// mutation back to `getUTC*` shifts BOTH the hour and the minute and a
// half-applied offset shifts only the minute. Each runner asserts the pin took
// effect, because a missing tzdata would silently make local time equal UTC and
// every assertion here would pass while proving nothing.
//
// The `Intl` ban still stands and this does not weaken it: `getHours` is
// specified against the local time zone and returns a number, where
// `toLocaleString` returns ICU-formatted text that the two engines disagree
// about. The digits are assembled here.
function formatInstant(value) {
  const seconds = parseRfc3339(value)
  if (seconds === null) return "unknown"
  const at = new Date(seconds * 1000)
  return at.getFullYear()
    + "-" + pad2(at.getMonth() + 1)
    + "-" + pad2(at.getDate())
    + " " + pad2(at.getHours())
    + ":" + pad2(at.getMinutes())
}

function pad2(value) {
  return value < 10 ? "0" + value : String(value)
}

// "3d ago", recomputed on the service's existing freshness tick because
// `nowWall` is an input (REQ-014, UX-011: no widget owns a timer). The empty
// string, not "never": a client whose `connectedAt` the controller omitted has
// no connection time to report, and "connected never" beside a client that is
// plainly connected is a contradiction the panel would be printing itself.
function connectedText(value, nowWall) {
  if (typeof nowWall !== "number" || !isFinite(nowWall)) return ""
  const seconds = parseRfc3339(value)
  if (seconds === null) return ""
  return relativePast(nowWall - seconds)
}

// --- REQ-B16: the lists, with their empty and truncated states -----------

function deviceListModel(snapshot, ui) {
  const data = snapshot || {}
  const counts = data.counts || {}
  const options = ui || {}
  const listed = data.devices || []
  const total = typeof counts.devicesTotal === "number"
    ? counts.devicesTotal : listed.length
  const term = searchTerm(options.search)
  const role = typeof options.role === "string" ? options.role : ""
  const names = uplinkNames(listed)
  const downlinks = downlinkNames(listed)
  const rows = []
  for (let i = 0; i < listed.length; i++) {
    const row = browseDeviceRow(listed[i])
    if (role !== "" && !hasRole(row.roles, role)) continue
    if (!matchesSearch(row.searchText, term)) continue
    rows.push(row)
  }
  const plural = role === "" ? undefined : own(ROLE_PLURAL, role)
  const noun = plural === undefined ? "devices" : plural
  return {
    rows: rows,
    listed: listed.length,
    total: total,
    matched: rows.length,
    searchText: term,
    role: role,
    truncated: total > listed.length,
    truncationText: truncationText(listed.length, total, "device"),
    emptyText: rows.length === 0
      ? emptyText(noun, term, listed.length, total) : "",
    expandedId: expandedIdIn(rows, options.expandedId),
    expandedDetail: detailFor(rows, listed, options.expandedId, function (device) {
      return deviceDetail(device, names, downlinks)
    })
  }
}

function clientListModel(snapshot, ui) {
  const data = snapshot || {}
  const counts = data.counts || {}
  const options = ui || {}
  const listed = data.clients || []
  const total = typeof counts.clients === "number" ? counts.clients : listed.length
  const term = searchTerm(options.search)
  const nowWall = typeof options.nowWall === "number" ? options.nowWall : null
  const names = uplinkNames(data.devices || [])
  const rows = []
  for (let i = 0; i < listed.length; i++) {
    const uplink = uplinkNameFor(names, (listed[i] || {}).uplinkDeviceId)
    const row = browseClientRow(listed[i], uplink, nowWall)
    if (!matchesSearch(row.searchText, term)) continue
    rows.push(row)
  }
  return {
    rows: rows,
    listed: listed.length,
    total: total,
    matched: rows.length,
    searchText: term,
    role: "",
    truncated: total > listed.length,
    truncationText: truncationText(listed.length, total, "client"),
    emptyText: rows.length === 0
      ? emptyText("clients", term, listed.length, total) : "",
    expandedId: expandedIdIn(rows, options.expandedId),
    expandedDetail: detailFor(rows, listed, options.expandedId, function (client) {
      return clientDetail(client, uplinkNameFor(names, client.uplinkDeviceId))
    })
  }
}

function searchTerm(value) {
  if (typeof value !== "string") return ""
  return value.trim().toLowerCase()
}

// REQ-B16, and the REQ-010/AC-063 rule applied a third time: the total comes
// from `counts`, an independently carried integer, and NEVER from the array's
// length. A site of 412 devices whose list was bounded at 200 must not be able
// to say "showing 200 of 200".
function truncationText(listedCount, total, noun) {
  if (total <= listedCount) return ""
  return "showing " + listedCount + " of " + total + " "
    + noun + (total === 1 ? "" : "s")
}

// The four things an empty list can mean, kept apart because they lead to
// different actions.
//
// The truncated-and-filtered case is the one worth the extra clause: on a site
// whose list was bounded, "no devices match" is not true — the device may exist
// and simply not be listed — and a panel that answers a search with a confident
// wrong "no" is worse than one that admits the bound.
function emptyText(noun, term, listedCount, total) {
  // DATA-B04's boundary case: the budget admitted nothing, so the array is
  // empty while the total is not. "No devices" beside "showing 0 of 300
  // devices" is the panel contradicting itself in two adjacent lines, and it is
  // the wrong one of the two that the user would read first.
  const bound = total > listedCount
    ? " among the " + listedCount + " listed of " + total : ""
  if (term === "") {
    if (bound === "") return "No " + noun + " to show."
    return "No " + noun + bound + "."
  }
  return "No " + noun + " match \"" + term + "\"" + bound + "."
}

// REQ-B14: one row expanded at a time, and only ever a row that is on screen.
// A row filtered out by a search must not keep its detail block open — the
// panel would be showing a detail for a row the user cannot see.
function expandedIdIn(rows, id) {
  if (typeof id !== "string" || id === "") return ""
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].id === id) return id
  }
  return ""
}

// The detail is built for the ONE expanded row, not for every row. Two hundred
// device details rebuilt on every keystroke would be the model doing the work
// the search is meant to avoid — and building it here rather than exposing a
// function for QML to call is what keeps AC-B15's "the view computes nothing"
// literally true.
function detailFor(rows, entries, id, builder) {
  if (expandedIdIn(rows, id) === "") return null
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i] || {}
    if (entry.id === id) return builder(entry)
  }
  return null
}

// The value of `deviceList` and `clientList` when there is nothing to show.
// A FUNCTION returning a fresh object, not a shared constant: `EMPTY_MODEL` uses
// it for both keys, and one object behind both would make a mutation through
// either visible through the other.
function emptyBrowseList() {
  return {
    rows: [],
    listed: 0,
    total: 0,
    matched: 0,
    searchText: "",
    role: "",
    truncated: false,
    truncationText: "",
    emptyText: "",
    expandedId: "",
    expandedDetail: null
  }
}

// --- REQ-B15: the focus order ---------------------------------------------
//
// Here rather than in `Panel.qml` for the reason the whole pure layer exists:
// the order depends on which page is showing, wraps in both directions, and has
// to survive a page change that removes the stop the cursor is on. That is a
// state machine, and a state machine reachable only through a five-minute live
// harness is one that gets tested once.
//
// The view still owns FOCUS itself — which item has `activeFocus`, and what a
// `Ui/TextField` does with a keystroke. What is decided here is the order and
// the arithmetic.

// REQ-B15's order, verbatim: segmented control, search, list, Refresh, Open
// UniFi, wrapping. Overview has no search field and no list, so it has three
// stops rather than five.
const FOCUS_SEGMENTS = "segments"
const FOCUS_SEARCH = "search"
const FOCUS_LIST = "list"
const FOCUS_REFRESH = "refresh"
const FOCUS_DASHBOARD = "dashboard"

function focusStops(view) {
  if (browseView(view) === "overview") {
    return [FOCUS_SEGMENTS, FOCUS_REFRESH, FOCUS_DASHBOARD]
  }
  return [FOCUS_SEGMENTS, FOCUS_SEARCH, FOCUS_LIST, FOCUS_REFRESH, FOCUS_DASHBOARD]
}

// The stop `direction` places along, wrapping. Takes and returns a NAME rather
// than an index: an index only means anything against one view's stop list, and
// the thing this has to get right is surviving a view change that shortens the
// list under the cursor.
function nextFocus(view, stop, direction) {
  const stops = focusStops(view)
  const at = stops.indexOf(stop)
  if (at === -1) return stops[0]
  if (direction === 0) return stop
  const step = direction > 0 ? 1 : -1
  return stops[(at + step + stops.length) % stops.length]
}

// Where the cursor lands when the view changes. Keeping the same stop is right
// when it still exists — switching Devices to Clients should not move focus out
// of the list — and impossible when it does not, so Overview takes anything
// that was on the search field or the list back to the segmented control, which
// is the control that got the user here.
function focusAfterViewChange(view, stop) {
  return focusStops(view).indexOf(stop) === -1 ? FOCUS_SEGMENTS : stop
}

// REQ-B10. The panel's current page, defaulting to Overview — which is also
// where it returns when it closes, because the widget's job is health and
// reopening it should answer that question rather than resume a browse.
function browseView(value) {
  for (let i = 0; i < BROWSE_VIEWS.length; i++) {
    if (BROWSE_VIEWS[i] === value) return value
  }
  return "overview"
}

// REQ-B15 (amended, SPEC-AMD-5). Left/Right move between pages from ANY focus
// stop, so the arithmetic moved here out of the panel — where it sat inside a
// branch that only ran while the segmented control happened to hold focus.
//
// CLAMPED, not wrapped. Three chips in a row are a position, not a cycle: the
// user can see both ends, and a Right at the last one that silently reappeared
// at the first would lose them the only landmark the control has.
function nextBrowseView(view, direction) {
  const at = BROWSE_VIEWS.indexOf(browseView(view))
  const step = direction > 0 ? 1 : (direction < 0 ? -1 : 0)
  const to = Math.min(BROWSE_VIEWS.length - 1, Math.max(0, at + step))
  return BROWSE_VIEWS[to]
}

// --- AC-011 / SEC-009 / UX-010 -------------------------------------------
// The dashboard launcher accepts http or https only, rejects URL userinfo, and
// warns visibly for plain HTTP.
//
// The character allowlist below is deliberately NARROWER than RFC 3986. A
// dashboard URL needs none of the exotic sub-delimiters, and `;` in particular
// is a legacy path-parameter separator that has no business here — AC-011
// requires `https://h/;reboot` to be rejected. Nothing is being defended
// against a shell (Qt.openUrlExternally does not use one); the point is that a
// string shaped like an injection attempt is not a URL the user meant to type,
// and REQ-012 already has a safe fallback when we refuse.
const URL_ALLOWED = /^[A-Za-z0-9\-._~:/?#[\]@%!$&'()*+,=]*$/

function acceptDashboardUrl(candidate) {
  if (typeof candidate !== "string" || candidate === "") {
    return reject("no dashboard URL is configured")
  }
  if (/[\s]/.test(candidate)) {
    return reject("a URL may not contain whitespace")
  }
  // Control characters, including the ones that survive a copy-paste out of a
  // terminal and are invisible in a settings file.
  for (let i = 0; i < candidate.length; i++) {
    const code = candidate.charCodeAt(i)
    if (code < 0x20 || code === 0x7f) return reject("a URL may not contain control characters")
  }
  if (!URL_ALLOWED.test(candidate)) {
    return reject("a URL may not contain that character")
  }

  const scheme = /^([A-Za-z][A-Za-z0-9+.\-]*):\/\//.exec(candidate)
  if (!scheme) return reject("a dashboard URL must start with http:// or https://")
  const protocol = scheme[1].toLowerCase()
  if (protocol !== "http" && protocol !== "https") {
    return reject("only http and https dashboard URLs are allowed")
  }

  const rest = candidate.slice(scheme[0].length)
  const authorityEnd = firstIndexOfAny(rest, ["/", "?", "#"])
  const authority = authorityEnd === -1 ? rest : rest.slice(0, authorityEnd)
  if (authority === "") return reject("the URL has no host")
  if (authority.indexOf("@") !== -1) {
    // SEC-009. Credentials in a URL would be handed to whatever opens it and
    // would sit in the user's shell.json in clear text.
    return reject("a dashboard URL may not embed credentials")
  }
  if (authority.indexOf(";") !== -1) return reject("the URL host is malformed")

  return {
    accepted: true,
    url: candidate,
    // UX-010 requires this to be RENDERED BEFORE the launch, so it is returned
    // with the acceptance rather than raised afterwards — a caller cannot open
    // the URL without having been handed the warning first.
    warnPlainHttp: protocol === "http",
    reason: null
  }
}

function firstIndexOfAny(text, characters) {
  let best = -1
  for (let i = 0; i < characters.length; i++) {
    const at = text.indexOf(characters[i])
    if (at !== -1 && (best === -1 || at < best)) best = at
  }
  return best
}

function reject(reason) {
  return { accepted: false, url: null, warnPlainHttp: false, reason: reason }
}

// REQ-012's fallback: derive https://<host> from the committed configuration,
// which reaches QML only through meta.apiRootHost (DATA-006a).
function dashboardUrlFor(configured, apiRootHost) {
  const direct = acceptDashboardUrl(configured)
  if (direct.accepted) return direct
  if (typeof apiRootHost === "string" && apiRootHost !== "") {
    const derived = acceptDashboardUrl("https://" + apiRootHost)
    if (derived.accepted) return derived
  }
  // REQ-012: when neither is available the button is disabled with an
  // explanatory label rather than silently doing nothing when clicked.
  return reject("no dashboard URL is configured and the controller host is unknown")
}

// --- AC-062: the tooltip -------------------------------------------------
// Four variants, and the third is the one that matters: a failed refresh over a
// still-fresh snapshot must say BOTH things. Collapsing it to either one alone
// is REQ-004's whole failure mode — either the user thinks the site is fine, or
// they think it is down when only the poll failed.
function tooltip(model) {
  const state = model || {}
  const siteName = state.siteName || "UniFi"
  const lines = [siteName]

  if (!state.hasSnapshot) {
    lines.push("Last update: never")
    lines.push("Latest attempt: " + attemptLine(state))
    lines.push(state.errorKind ? sentenceFor(state.errorKind) : sentenceFor("loading"))
    return lines.join("\n")
  }

  lines.push("Last update: " + relativePast(state.secondsSinceSuccess))
  lines.push("Latest attempt: " + attemptLine(state))

  if (state.isStale) {
    lines.push(sentenceFor("stale"))
  } else if (state.errorKind) {
    // Fresh snapshot, failed refresh. Both facts, in this order.
    lines.push(wordCase(wordFor(state.healthLevel)) + " as of the last update; "
      + "the most recent refresh failed.")
  } else {
    lines.push(wordCase(wordFor(state.healthLevel)) + ".")
  }
  return lines.join("\n")
}

// The panel hero's one-line summary. It repeats the tooltip's last line on
// purpose: the tooltip is for the bar item, the hero is for the open panel, and
// a user who opened the panel should not have to hover the thing they just
// clicked to find out what it says.
function headline(healthLevel, hasSnapshot, lastUpdateText) {
  const word = wordCase(wordFor(healthLevel))
  return hasSnapshot ? word + "  \u00b7  updated " + lastUpdateText
    : word + "  \u00b7  never updated"
}

function attemptLine(state) {
  if (state.errorKind) return "failed (" + state.errorKind + ")"
  if (state.hasSnapshot) return "succeeded"
  return "in progress"
}

function wordCase(word) {
  return word.charAt(0).toUpperCase() + word.slice(1)
}

// --- REQ-013b / AC-067 ---------------------------------------------------
// `bar?.shell?.serviceFor("gaius-codius.unifi")` is null on the first frame and stays
// null if the service failed to construct. Returning a complete view model
// here — rather than letting QML bind against null — is what turns a blank
// widget or a binding error into a stated condition.
function forNullService() {
  // REQ-013b. The SAME SHAPE `build` returns, filled in for "there is no
  // service", because a widget binds to one object and cannot have half its
  // bindings become undefined on the first frame — which is exactly the frame
  // where `bar?.shell?.serviceFor()` is null.
  return complete({
    state: "service_unavailable",
    sentence: sentenceFor("service_unavailable"),
    healthLevel: "grey",
    rendering: renderingFor("grey"),
    word: wordFor("grey"),
    compactText: "",
    hasSnapshot: false,
    tooltip: "UniFi\nLast update: never\nLatest attempt: unavailable\n"
      + sentenceFor("service_unavailable"),
    headline: headline("grey", false, "never")
  })
}

// Every key a widget may bind to, and the value that means "nothing to show".
// Written out rather than built from `build`'s output, so a key added to one
// and not the other is a visible difference between two lists rather than an
// undefined binding on the first frame.
const EMPTY_MODEL = {
  state: "service_unavailable",
  sentence: "",
  healthLevel: "grey",
  rendering: null,
  word: "",
  compactText: "",
  hasSnapshot: false,
  tooltip: "",
  headline: "",
  siteName: "",
  wan: null,
  wanRows: [],
  gateways: [],
  gatewayRows: [],
  counts: null,
  countRows: [],
  clientsText: "unknown",
  devicesTotalText: "unknown",
  offline: { devices: [], total: 0, truncated: false, moreLabel: "" },
  roleCountsAreNotAPartition: false,
  roleCountsNote: "",
  warnings: [],
  warningRows: [],
  sites: [],
  errorKind: null,
  errorMessage: "",
  isStale: false,
  pollingSuspended: false,
  refreshEnabled: false,
  refreshDisabledReason: "",
  nextAttemptText: "",
  lastUpdateText: "never",
  dashboard: null,
  meta: null,
  metaRows: [],
  insecureTls: false,
  customCaInUse: false,
  // REQ-B10's two pages. `deviceList` and `clientList` rather than `devices`
  // and `clients`, because the snapshot already has fields by those names
  // holding the RAW arrays — and a widget binding to the wrong one of the two
  // would get an array of records where it expected a list model, which reads
  // as an empty page rather than as an error.
  //
  // WHICH page is showing is deliberately NOT here. It was, and it was a second
  // source of truth for something the panel already owns: with no service the
  // model is `forNullService()`, so `vm.view` sat at "overview" while the panel
  // was on Devices, and the two disagreed with nothing to reconcile them. That
  // is the shape of both bugs in N-102. The panel owns which page is showing,
  // `browseView()` sanitises it, and the model owns what each page contains.
  deviceList: emptyBrowseList(),
  clientList: emptyBrowseList()
}

function complete(partial) {
  const model = {}
  for (const key in EMPTY_MODEL) {
    model[key] = Object.prototype.hasOwnProperty.call(partial, key)
      ? partial[key] : EMPTY_MODEL[key]
  }
  return model
}

// --- the composition (HC-16) ---------------------------------------------
//
// `Service.qml` calls five pure modules in order and publishes one property.
// This is the last of the five, and it lives here rather than in QML for the
// reason the whole pure layer exists: the panel's contents are decided by
// something `node --test` can execute.
//
// It takes what the service knows — a snapshot, a level from `Health.js`, an
// error kind, the scheduler's deadlines — and returns the object a widget
// binds to. It computes no health and no schedule of its own; passing `level`
// in rather than deriving it is what keeps REQ-002 in one place.
function build(input) {
  const state = input || {}
  const snapshot = state.snapshot || null
  const counts = snapshot ? (snapshot.counts || null) : null
  const level = state.level || {}
  const healthLevel = typeof level.level === "string" ? level.level : "grey"
  const settings = state.settings || {}
  const meta = state.meta || null
  const errorKind = state.errorKind === undefined ? null : state.errorKind
  const hasSnapshot = snapshot !== null

  const panel = panelState({
    reconfiguring: state.reconfiguring === true,
    serviceAvailable: true,
    errorKind: errorKind,
    hasSnapshot: hasSnapshot,
    isStale: state.isStale === true,
    devicesTotal: counts ? counts.devicesTotal : undefined
  })

  const nowWall = typeof state.nowWall === "number" ? state.nowWall : null
  const secondsSinceSuccess = nowWall !== null && typeof state.lastSuccessAt === "number"
    ? nowWall - state.lastSuccessAt : null

  const tooltipModel = {
    siteName: snapshot && snapshot.site ? snapshot.site.name : null,
    hasSnapshot: hasSnapshot,
    errorKind: errorKind,
    isStale: state.isStale === true,
    healthLevel: healthLevel,
    secondsSinceSuccess: secondsSinceSuccess
  }

  // REQ-011: the Refresh button is disabled, with an explanatory label,
  // whenever polling is suspended (REQ-018a). "Suspended is not idle."
  const suspended = state.pollingSuspended === true
  // REQ-B10's UI state: which page, what is typed into each search field, which
  // row is expanded. It is passed IN rather than held here, because this module
  // is a function and holding it would make the panel's contents depend on how
  // many times the function had been called.
  const browse = state.browse || {}
  const warnings = state.warnings || []
  const error = state.error || null
  return complete({
    state: panel,
    // `ok` is the twenty-fifth state and deliberately NOT in PANEL_SENTENCES:
    // REQ-013 enumerates the twenty-four conditions that need explaining, and a
    // healthy site is not one of them. It must still be given the empty string
    // rather than being left to sentenceFor's DATA-007 fallback, which would
    // hand a perfectly healthy panel the internal-error sentence.
    sentence: panel === "ok" ? "" : sentenceFor(panel),
    healthLevel: healthLevel,
    rendering: renderingFor(healthLevel),
    word: wordFor(healthLevel),
    compactText: compactText(settings.compactMetric, counts),
    hasSnapshot: hasSnapshot,
    tooltip: tooltip(tooltipModel),
    headline: headline(healthLevel, hasSnapshot, relativePast(secondsSinceSuccess)),
    siteName: snapshot && snapshot.site ? snapshot.site.name : "",
    wan: snapshot ? snapshot.wan : null,
    wanRows: snapshot ? wanRows(snapshot.wan) : [],
    gateways: snapshot ? (snapshot.gateways || []) : [],
    gatewayRows: snapshot ? gatewayRows(snapshot.gateways) : [],
    counts: counts,
    countRows: countRows(counts),
    clientsText: formatOptional(counts ? counts.clients : null),
    devicesTotalText: formatOptional(counts ? counts.devicesTotal : null),
    offline: offlineList(snapshot),
    roleCountsAreNotAPartition: roleCountsAreNotAPartition(counts),
    roleCountsNote: roleCountsAreNotAPartition(counts) ? roleCountsNote(counts) : "",
    warnings: warnings,
    warningRows: warningRows(warnings),
    sites: sitesFromWarnings(warnings),
    errorKind: errorKind,
    errorMessage: error && typeof error.message === "string" ? error.message : "",
    isStale: state.isStale === true,
    pollingSuspended: suspended,
    refreshEnabled: !suspended,
    refreshDisabledReason: suspended ? sentenceFor(errorKind || "internal") : "",
    nextAttemptText: nextAttemptText(state),
    lastUpdateText: hasSnapshot ? relativePast(secondsSinceSuccess) : "never",
    dashboard: dashboardUrlFor(settings.dashboardUrl,
      meta ? meta.apiRootHost : null),
    meta: meta,
    metaRows: metaRows(meta, snapshot, warnings),
    // UX-009 / AC-070: a boolean, not a chain. The panel row is bound to one
    // property with no `&&` in it, so there is no arrangement of a null `meta`
    // in which the row quietly becomes undefined instead of false.
    insecureTls: meta ? meta.allowInsecureTls === true : false,
    customCaInUse: meta ? meta.customCaInUse === true : false,
    deviceList: deviceListModel(snapshot, {
      search: browse.deviceSearch,
      role: browse.role,
      expandedId: browse.expandedDeviceId
    }),
    // REQ-B17: `nowWall` is an INPUT, so "connected 3d ago" recomputes on the
    // service's existing freshness tick and no widget owns a timer (REQ-014,
    // UX-011). It is the same clock `lastUpdateText` reads, which is what makes
    // AC-071's guarantee cover the new strings without a second mechanism.
    clientList: clientListModel(snapshot, {
      search: browse.clientSearch,
      expandedId: browse.expandedClientId,
      nowWall: nowWall
    })
  })
}

// UX-007: rate limiting and backoff show `nextAttemptAt` as a RELATIVE time, so
// the panel never displays a static instant that quietly becomes wrong.
//
// SPEC-AMD-4: "rate limiting and backoff" is now the CONDITION and not merely
// the motivation. A healthy widget on a 30 s interval printed "Next attempt in
// 26s" permanently, which answers a question nobody asked and reads, next to a
// health word, as though something were being waited on. The line exists to
// say "we are not stuck, we are waiting until T" — and that is only true, and
// only reassuring, while the scheduler is actually backing off.
//
// `backingOff` is passed in rather than inferred from `errorKind`, because the
// two disagree exactly where it matters: a failed refresh over a fresh snapshot
// has an `errorKind` and may already have retried successfully, and REQ-023b's
// startup ramp has no `errorKind` at all. The scheduler's own `retry-wait` is
// the fact; anything derived here would be a second opinion about it.
function nextAttemptText(state) {
  if (state.pollingSuspended === true) return ""
  if (state.backingOff !== true) return ""
  if (typeof state.nextAttemptAt !== "number") return ""
  if (typeof state.now !== "number") return ""
  return relativeFuture(state.nextAttemptAt - state.now)
}

// AC-025's flag. The role rows deliberately sum to more than the unique device
// total, and rows summing to 5 above a total of 3 otherwise read as a bug.
function roleCountsAreNotAPartition(counts) {
  if (!counts) return false
  const roles = ["gateways", "switches", "accessPoints"]
  let sum = 0
  for (let i = 0; i < roles.length; i++) {
    const bucket = counts[roles[i]]
    if (!bucket) continue
    for (const key in bucket) {
      if (typeof bucket[key] === "number") sum += bucket[key]
    }
  }
  return sum !== counts.devicesTotal
}

// The bar item's optional text beside the glyph (REQ-005).
function compactText(metric, counts) {
  if (metric !== "clients") return ""
  const value = counts ? counts.clients : null
  // BIZ-002: a null client count means the optional collection failed. It is
  // rendered as unknown, never as 0, and never omitted silently — the row
  // disappearing would read as "no clients".
  return formatOptional(value)
}

// Which of the twenty-four states the panel should render, given everything the
// service knows. Ordered, and the order is the point: a configuration fault
// outranks a stale snapshot, which outranks a transport error, which outranks
// the empty state.
function panelState(model) {
  const state = model || {}
  if (state.reconfiguring) return "reconfiguring"
  if (!state.serviceAvailable) return "service_unavailable"
  if (state.errorKind && isConfigFaultKind(state.errorKind)) return state.errorKind
  if (!state.hasSnapshot) return state.errorKind ? state.errorKind : "loading"
  if (state.isStale) return "stale"
  if (state.errorKind) return state.errorKind
  if (state.devicesTotal === 0) return "empty"
  return "ok"
}

// Duplicated from Health.js on purpose: HC-16 forbids these two files importing
// one another. tests/model/consistency.test.js requires both and asserts the
// lists are identical, which is the only thing keeping them honest.
const CONFIG_FAULT_KINDS = [
  "unconfigured",
  "site_unselected",
  "uncommitted",
  "configuration_conflict"
]

function isConfigFaultKind(kind) {
  for (let i = 0; i < CONFIG_FAULT_KINDS.length; i++) {
    if (CONFIG_FAULT_KINDS[i] === kind) return true
  }
  return false
}

if (typeof module !== "undefined") module.exports = {
  LEVEL_RENDERING: LEVEL_RENDERING,
  LEVEL_WORD: LEVEL_WORD,
  PANEL_SENTENCES: PANEL_SENTENCES,
  PANEL_STATES: PANEL_STATES,
  STATES_THAT_DISCARD_THE_SNAPSHOT: STATES_THAT_DISCARD_THE_SNAPSHOT,
  CONFIG_FAULT_KINDS: CONFIG_FAULT_KINDS,
  OFFLINE_LIST_BOUND: OFFLINE_LIST_BOUND,
  sentenceFor: sentenceFor,
  renderingFor: renderingFor,
  wordFor: wordFor,
  formatOptional: formatOptional,
  formatBps: formatBps,
  formatUptime: formatUptime,
  relativeFuture: relativeFuture,
  relativePast: relativePast,
  offlineList: offlineList,
  deviceRow: deviceRow,
  displayName: displayName,
  classWord: classWord,
  wanStatusWord: wanStatusWord,
  wanRows: wanRows,
  gatewayRows: gatewayRows,
  countRows: countRows,
  roleCountsNote: roleCountsNote,
  metaRows: metaRows,
  warningRows: warningRows,
  hasWarning: hasWarning,
  WARNINGS_WITH_THEIR_OWN_ROW: WARNINGS_WITH_THEIR_OWN_ROW,
  sitesFromWarnings: sitesFromWarnings,
  CLASS_WORD: CLASS_WORD,
  CLASS_ORDER: CLASS_ORDER,
  WAN_STATUS_WORD: WAN_STATUS_WORD,
  acceptDashboardUrl: acceptDashboardUrl,
  dashboardUrlFor: dashboardUrlFor,
  tooltip: tooltip,
  headline: headline,
  forNullService: forNullService,
  EMPTY_MODEL: EMPTY_MODEL,
  build: build,
  nextAttemptText: nextAttemptText,
  compactText: compactText,
  panelState: panelState,
  isConfigFaultKind: isConfigFaultKind,
  BROWSE_CLASS_RANK: BROWSE_CLASS_RANK,
  BROWSE_VIEWS: BROWSE_VIEWS,
  downlinkNames: downlinkNames,
  downlinkText: downlinkText,
  nextBrowseView: nextBrowseView,
  ROLE_FOR_COUNT_KEY: ROLE_FOR_COUNT_KEY,
  ROLE_PLURAL: ROLE_PLURAL,
  CLIENT_TYPE_WORD: CLIENT_TYPE_WORD,
  DETAIL_NOT_FETCHED: DETAIL_NOT_FETCHED,
  browseOrderKey: browseOrderKey,
  compareBrowseOrder: compareBrowseOrder,
  clientOrderKey: clientOrderKey,
  compareClientOrder: compareClientOrder,
  firstBrowseOrderViolation: firstBrowseOrderViolation,
  firstClientOrderViolation: firstClientOrderViolation,
  hasRole: hasRole,
  matchesSearch: matchesSearch,
  haystackOf: haystackOf,
  browseDeviceRow: browseDeviceRow,
  browseClientRow: browseClientRow,
  clientDisplayName: clientDisplayName,
  clientTypeWord: clientTypeWord,
  uplinkNames: uplinkNames,
  uplinkNameFor: uplinkNameFor,
  deviceDetail: deviceDetail,
  clientDetail: clientDetail,
  portRow: portRow,
  poeText: poeText,
  radioRow: radioRow,
  formatPct: formatPct,
  formatSpeedMbps: formatSpeedMbps,
  parseRfc3339: parseRfc3339,
  formatInstant: formatInstant,
  connectedText: connectedText,
  deviceListModel: deviceListModel,
  clientListModel: clientListModel,
  truncationText: truncationText,
  emptyText: emptyText,
  emptyBrowseList: emptyBrowseList,
  browseView: browseView,
  FOCUS_SEGMENTS: FOCUS_SEGMENTS,
  FOCUS_SEARCH: FOCUS_SEARCH,
  FOCUS_LIST: FOCUS_LIST,
  FOCUS_REFRESH: FOCUS_REFRESH,
  FOCUS_DASHBOARD: FOCUS_DASHBOARD,
  focusStops: focusStops,
  nextFocus: nextFocus,
  focusAfterViewChange: focusAfterViewChange
}
