// Health.js — REQ-000 classification and the REQ-002 health decision function.
//
// Dual-use: this file runs unchanged under QML's V4 engine and under Node's V8,
// so `node --test` can execute it with no graphical session. That is what the
// AC-033 gates enforce, and it is why there are no `Qt.`/`qs.` references, no
// `.pragma library`, no `.import`, and no mutable top-level binding here.
//
// HC-16 forbids dual-use modules from importing one another, so the pure layer
// is several independent files and anything shared between them is duplicated
// on purpose. tests/model/consistency.test.js requires all of them at once and
// asserts the duplicates agree — that cross-check is the whole mitigation, so
// do not delete it when adding a fourth module.

// --- REQ-000 -------------------------------------------------------------
// The five classes, in the order every class object lists them.
const CLASSES = ["online", "transitional", "down", "impaired", "unknown"]

// Total over the ten API `state` values. Anything else lands in `unknown` —
// never silently in another class, and specifically never in `online`, which is
// what a permissive default would do to a device state invented by a future
// controller firmware.
const STATE_TO_CLASS = {
  ONLINE: "online",

  PENDING_ADOPTION: "transitional",
  UPDATING: "transitional",
  GETTING_READY: "transitional",
  ADOPTING: "transitional",
  DELETING: "transitional",

  OFFLINE: "down",
  CONNECTION_INTERRUPTED: "down",

  ISOLATED: "impaired",
  U5G_INCORRECT_TOPOLOGY: "impaired"
}

// The health model keeps these names because they are precise and testable.
// REQ-001a is the single mapping from a level to a theme token, and it lives in
// ViewModel.js so that this file stays free of presentation.
const LEVELS = ["green", "amber", "red", "grey"]

const WAN_STATUSES = ["up", "down", "degraded", "unknown"]

// REQ-002 rule 1. These are CONFIGURATION faults, not transport failures. The
// distinction is the whole of REQ-004 and AC-021: a network error with a fresh
// snapshot must keep showing that snapshot's colour, while a configuration
// fault greys the widget immediately because polling is suspended and whatever
// is on screen has stopped meaning anything.
const CONFIG_FAULT_KINDS = [
  "unconfigured",
  "site_unselected",
  "uncommitted",
  "configuration_conflict"
]

function classifyState(state) {
  if (typeof state !== "string") return "unknown"
  const known = STATE_TO_CLASS[state]
  return known === undefined ? "unknown" : known
}

// Returns the class and, when the state was not recognised, the REQ-003 warning
// naming the value. Returning the warning here rather than leaving it to the
// caller is what stops an unrecognised state being counted and then forgotten.
function classifyDevice(device) {
  const state = device && device.state
  const klass = classifyState(state)
  if (klass !== "unknown") return { klass: klass, warning: null }
  return {
    klass: klass,
    warning: {
      code: "unknown_device_state",
      message: "Device reported an unrecognised state: " + String(state) + ".",
      detail: { state: state === undefined ? null : state,
                deviceId: (device && device.id) || null }
    }
  }
}

function isGateway(device) {
  if (!device || !device.features || typeof device.features.length !== "number") return false
  for (let i = 0; i < device.features.length; i++) {
    if (device.features[i] === "gateway") return true
  }
  return false
}

function everyGatewayDown(gateways) {
  if (!gateways || gateways.length === 0) return false
  for (let i = 0; i < gateways.length; i++) {
    if (gateways[i].class !== "down") return false
  }
  return true
}

function anyGatewayNotWell(gateways) {
  for (let i = 0; i < (gateways ? gateways.length : 0); i++) {
    const klass = gateways[i].class
    if (klass === "down" || klass === "impaired" || klass === "unknown") return true
  }
  return false
}

// REQ-008a. Stated there as four bullets whose third reads "some but not all
// gateways `down`, `impaired`, or `unknown` → degraded".
//
// That sentence has two parses and they disagree on one cell: every gateway
// ISOLATED and none OFFLINE. Read as "some-but-not-all of {down, impaired,
// unknown}" it falls through to `up`; read as "some-but-not-all down, OR any
// impaired, OR any unknown" it is `degraded`.
//
// This implements the second. The first would report a healthy WAN while every
// gateway on the site is isolated — and REQ-002 rule 4 would colour that same
// snapshot amber, so the panel would contradict the bar item. Flagged in
// DEVIATION_LOG.md as DEV-3 for confirmation.
function wanStatusFor(gateways) {
  if (!gateways || gateways.length === 0) return "unknown"
  if (everyGatewayDown(gateways)) return "down"
  if (anyGatewayNotWell(gateways)) return "degraded"
  return "up"
}

// REQ-008a: the first `online` gateway in ascending `id` order, or the first
// gateway in ascending `id` order when none is online. Ascending id — not
// array order — so the choice is stable across batches whatever order the
// controller returns.
function primaryGateway(gateways) {
  if (!gateways || gateways.length === 0) return null
  const sorted = gateways.slice().sort(function (a, b) {
    const left = String(a && a.id)
    const right = String(b && b.id)
    return left < right ? -1 : (left > right ? 1 : 0)
  })
  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i].class === "online") return sorted[i]
  }
  return sorted[0]
}

function isConfigFault(errorKind) {
  for (let i = 0; i < CONFIG_FAULT_KINDS.length; i++) {
    if (CONFIG_FAULT_KINDS[i] === errorKind) return true
  }
  return false
}

// REQ-002. An ORDERED, TOTAL decision function: the first matching rule wins,
// and rule 5 matches unconditionally, so exactly one colour is defined for
// every reachable state. The rule NUMBER is returned alongside the level
// because "which rule fired" is the only way a test can tell a correct green
// from a green reached by falling through a rule that should have matched.
//
//   1  configuration fault, no snapshot, or past staleAt      grey
//   2  snapshot contains zero adopted devices                 grey
//   3  at least one gateway AND every gateway down            red
//   4  byClass.down + byClass.impaired + byClass.unknown > 0  amber
//   5  otherwise                                              green
//
// Consequences that are deliberate and must survive refactoring:
//   * A site with no gateway can never be red — rule 3 requires one.
//   * A transitional device never degrades the colour: rule 4 does not read
//     `transitional`, so an UPDATING access point stays green.
//   * Red strictly precedes amber, so an unrecognised state on a site whose
//     gateways are all down does not downgrade red to amber.
//   * Rule 4 reads `byClass`, NOT the per-role objects. The role objects
//     double-count multi-role devices and omit featureless ones, and
//     `offlineTotal` is down+impaired only so it cannot see `unknown`. That
//     gap is why DATA-006b exists (DEV-1).
function healthLevel(input) {
  const snapshot = input && input.snapshot
  const errorKind = input && input.errorKind
  const isStale = !!(input && input.isStale)

  if (isConfigFault(errorKind)) {
    return level("grey", 1, "configuration fault: " + String(errorKind))
  }
  if (!snapshot) {
    return level("grey", 1, "no complete snapshot yet")
  }
  if (isStale) {
    return level("grey", 1, "snapshot is past staleAt")
  }

  const counts = snapshot.counts
  if (!counts || typeof counts.devicesTotal !== "number") {
    // A snapshot without counts cannot answer rules 2-4. It should never reach
    // here — DATA-008 rejects it — but returning grey with a named reason
    // beats returning green by falling through.
    return level("grey", 1, "snapshot carries no counts")
  }
  if (counts.devicesTotal === 0) {
    return level("grey", 2, "no adopted devices")
  }
  if (everyGatewayDown(snapshot.gateways)) {
    return level("red", 3, "every gateway is down")
  }

  const byClass = counts.byClass || {}
  const bad = num(byClass.down) + num(byClass.impaired) + num(byClass.unknown)
  if (bad > 0) {
    return level("amber", 4, bad + " device(s) down, impaired or in an unknown state")
  }
  return level("green", 5, "all devices online or transitional")
}

function num(value) {
  return typeof value === "number" && isFinite(value) ? value : 0
}

function level(name, rule, reason) {
  return { level: name, rule: rule, reason: reason }
}

// AC-025. The three role objects are NOT a partition of the device list: a
// Dream Machine reports gateway + switching + accessPoint and is counted in
// each, and a device with an empty `features` array is counted in none. The
// panel needs to say so, or a user reading rows that sum to 5 above a total of
// 3 will reasonably conclude the plugin is broken.
function roleCountsAreNotAPartition(counts) {
  if (!counts) return false
  const roles = ["gateways", "switches", "accessPoints"]
  let sum = 0
  for (let r = 0; r < roles.length; r++) {
    const bucket = counts[roles[r]] || {}
    for (let c = 0; c < CLASSES.length; c++) sum += num(bucket[CLASSES[c]])
  }
  return sum !== num(counts.devicesTotal)
}

if (typeof module !== "undefined") module.exports = {
  CLASSES: CLASSES,
  STATE_TO_CLASS: STATE_TO_CLASS,
  LEVELS: LEVELS,
  WAN_STATUSES: WAN_STATUSES,
  CONFIG_FAULT_KINDS: CONFIG_FAULT_KINDS,
  classifyState: classifyState,
  classifyDevice: classifyDevice,
  isGateway: isGateway,
  everyGatewayDown: everyGatewayDown,
  wanStatusFor: wanStatusFor,
  primaryGateway: primaryGateway,
  isConfigFault: isConfigFault,
  healthLevel: healthLevel,
  roleCountsAreNotAPartition: roleCountsAreNotAPartition
}
