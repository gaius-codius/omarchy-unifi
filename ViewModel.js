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
    devices: listed,
    total: total,
    truncated: remainder > 0,
    moreLabel: remainder > 0 ? "and " + remainder + " more" : ""
  }
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
      + sentenceFor("service_unavailable")
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
  siteName: "",
  wan: null,
  gateways: [],
  counts: null,
  offline: { devices: [], total: 0, truncated: false, moreLabel: "" },
  roleCountsAreNotAPartition: false,
  warnings: [],
  errorKind: null,
  isStale: false,
  pollingSuspended: false,
  refreshEnabled: false,
  refreshDisabledReason: "",
  nextAttemptText: "",
  lastUpdateText: "never",
  dashboard: null,
  meta: null
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
  return complete({
    state: panel,
    sentence: sentenceFor(panel),
    healthLevel: healthLevel,
    rendering: renderingFor(healthLevel),
    word: wordFor(healthLevel),
    compactText: compactText(settings.compactMetric, counts),
    hasSnapshot: hasSnapshot,
    tooltip: tooltip(tooltipModel),
    siteName: snapshot && snapshot.site ? snapshot.site.name : "",
    wan: snapshot ? snapshot.wan : null,
    gateways: snapshot ? (snapshot.gateways || []) : [],
    counts: counts,
    offline: offlineList(snapshot),
    roleCountsAreNotAPartition: roleCountsAreNotAPartition(counts),
    warnings: state.warnings || [],
    errorKind: errorKind,
    isStale: state.isStale === true,
    pollingSuspended: suspended,
    refreshEnabled: !suspended,
    refreshDisabledReason: suspended ? sentenceFor(errorKind || "internal") : "",
    nextAttemptText: nextAttemptText(state),
    lastUpdateText: hasSnapshot ? relativePast(secondsSinceSuccess) : "never",
    dashboard: dashboardUrlFor(settings.dashboardUrl,
      meta ? meta.apiRootHost : null),
    meta: meta
  })
}

// UX-007: rate limiting and backoff show `nextAttemptAt` as a RELATIVE time, so
// the panel never displays a static instant that quietly becomes wrong.
function nextAttemptText(state) {
  if (state.pollingSuspended === true) return ""
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
  acceptDashboardUrl: acceptDashboardUrl,
  dashboardUrlFor: dashboardUrlFor,
  tooltip: tooltip,
  forNullService: forNullService,
  EMPTY_MODEL: EMPTY_MODEL,
  build: build,
  nextAttemptText: nextAttemptText,
  compactText: compactText,
  panelState: panelState,
  isConfigFaultKind: isConfigFaultKind
}
