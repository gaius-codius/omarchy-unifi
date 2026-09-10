// Settings.js — DATA-001/002/002a/002b. One defaults table, one clamp.
//
// Dual-use (see the header of Health.js for what that constrains and why).
//
// The point of this module is that there is exactly ONE of everything. HC-1
// means the host merges no defaults and validates nothing, so every
// default and every bound is applied here in QML — and DATA-002b says the
// SERVICE and the WIDGET resolve settings by different routes (the widget
// through the `setting()` base-class helper, the service by locating its own
// entry in the injected shell's bar layout, because a service is not injected
// `settings` at all). Two routes reading two tables is R1b, and R1b is why
// AC-032 asserts they are equal. Keeping one table is cheaper than testing
// that two agree.

const DEFAULTS = {
  refreshIntervalSec: 30,
  compactMetric: "none",
  // Empty means "derive from meta.apiRootHost" (REQ-012). It is not a URL and
  // must not be treated as one.
  dashboardUrl: ""
}

const BOUNDS = {
  refreshIntervalSec: { min: 15, max: 3600 }
}

// REQ-005. `latency` is deliberately absent: no endpoint in the read-only
// surface returns one, so the setting would be a control that silently does
// nothing. tests/lint/no_latency_metric.sh keeps it that way.
const COMPACT_METRICS = ["none", "clients"]

// DATA-002. Only settings the SERVICE consumes can produce a
// configuration_conflict. Two bar entries differing only in `compactMetric` are
// each rendered with their own value and polling proceeds — treating that as a
// conflict would suspend polling over a purely cosmetic difference.
const SERVICE_CONSUMED_KEYS = ["refreshIntervalSec"]

// Left → center → right. The order is the tie-break for duplicates, so it is
// named once here rather than assumed at each call site.
const SECTIONS = ["left", "center", "right"]

const PLUGIN_ID = "gaius-codius.unifi"

function warn(code, message, detail) {
  return { code: code, message: message, detail: detail === undefined ? null : detail }
}

// DATA-002a. TYPE-validated, not presence-checked, because HC-1 guarantees no
// upstream validation: whatever is in shell.json arrives exactly as the user
// typed it. The string "30" is the case that matters — it is truthy, it looks
// right in a log, and `setInterval("30" * 2)` quietly works, so a
// presence-check would let a wrong type through until something else broke.
function normalizeInterval(value) {
  const bounds = BOUNDS.refreshIntervalSec
  const fallback = DEFAULTS.refreshIntervalSec

  if (typeof value !== "number" || !isFinite(value) || Math.floor(value) !== value) {
    return {
      value: fallback,
      warning: warn("settings_invalid",
        "refreshIntervalSec must be a whole number of seconds; using " + fallback + ".",
        { key: "refreshIntervalSec", received: describe(value) })
    }
  }
  if (value < bounds.min || value > bounds.max) {
    const clamped = value < bounds.min ? bounds.min : bounds.max
    return {
      value: clamped,
      warning: warn("settings_invalid",
        "refreshIntervalSec must be between " + bounds.min + " and " + bounds.max
          + " seconds; using " + clamped + ".",
        { key: "refreshIntervalSec", received: value, clamped: clamped })
    }
  }
  return { value: value, warning: null }
}

// AC-065. Lives here rather than in ViewModel.js so the compactMetric enum
// exists in exactly one module — HC-16 forbids the two importing each other, so
// a copy in each is a copy that can drift. QML may import both files, so the
// widget can still reach it.
function normalizeCompactMetric(value) {
  const trimmed = typeof value === "string" ? value.replace(/^\s+|\s+$/g, "") : value
  for (let i = 0; i < COMPACT_METRICS.length; i++) {
    if (trimmed === COMPACT_METRICS[i]) return { value: trimmed, warning: null }
  }
  return {
    value: DEFAULTS.compactMetric,
    warning: warn("settings_invalid",
      "compactMetric must be one of " + COMPACT_METRICS.join(", ")
        + "; using " + DEFAULTS.compactMetric + ".",
      { key: "compactMetric", received: describe(value) })
  }
}

// A non-string or scheme-invalid dashboardUrl is treated as UNSET, not as an
// error: REQ-012 already has a defined fallback (derive from meta.apiRootHost),
// so falling back is strictly better than disabling the button.
function normalizeDashboardUrl(value) {
  if (value === undefined || value === null || value === "") {
    return { value: "", warning: null }
  }
  if (typeof value !== "string") {
    return {
      value: "",
      warning: warn("settings_invalid",
        "dashboardUrl must be a string; falling back to the controller host.",
        { key: "dashboardUrl", received: describe(value) })
    }
  }
  if (!/^https?:\/\//i.test(value)) {
    return {
      value: "",
      warning: warn("settings_invalid",
        "dashboardUrl must start with http:// or https://; falling back to the "
          + "controller host.",
        { key: "dashboardUrl", received: value })
    }
  }
  return { value: value, warning: null }
}

function describe(value) {
  if (value === null) return "null"
  if (value === undefined) return "absent"
  if (typeof value === "number" && !isFinite(value)) return "not-a-number"
  if (typeof value === "string") return "string " + JSON.stringify(value)
  return typeof value
}

// Resolve one layout entry into a complete, valid settings object. Every key
// passes through its normalizer, so nothing downstream ever sees a raw value —
// which is what makes "a timer interval is never computed from an unvalidated
// value" (DATA-002a) a property of the code rather than a rule to remember.
function resolve(entry) {
  const source = entry || {}
  const interval = normalizeInterval(source.refreshIntervalSec === undefined
    ? DEFAULTS.refreshIntervalSec : source.refreshIntervalSec)
  const metric = normalizeCompactMetric(source.compactMetric === undefined
    ? DEFAULTS.compactMetric : source.compactMetric)
  const dashboard = normalizeDashboardUrl(source.dashboardUrl === undefined
    ? DEFAULTS.dashboardUrl : source.dashboardUrl)

  const warnings = []
  if (interval.warning) warnings.push(interval.warning)
  if (metric.warning) warnings.push(metric.warning)
  if (dashboard.warning) warnings.push(dashboard.warning)

  return {
    settings: {
      refreshIntervalSec: interval.value,
      compactMetric: metric.value,
      dashboardUrl: dashboard.value
    },
    warnings: warnings
  }
}

// The widget's route (DATA-002b): `setting(name, fallback)` returns the raw
// inline value, so the widget must still normalize it. Modelled here so the
// AUTO layer can assert both routes land on the same value — R1b.
function settingOf(settings, name) {
  const value = settings ? settings[name] : undefined
  return value === undefined || value === null ? DEFAULTS[name] : value
}

function resolveFromWidget(settings) {
  return resolve({
    refreshIntervalSec: settingOf(settings, "refreshIntervalSec"),
    compactMetric: settingOf(settings, "compactMetric"),
    dashboardUrl: settingOf(settings, "dashboardUrl")
  })
}

// DATA-002 / AC-019. Scan bar.layout for this plugin's entries and decide
// whether they conflict.
//
// The scan is over left → center → right in that order, and the LEFT-MOST entry
// wins, so the answer does not depend on object key iteration order. Duplicates
// are compared on SERVICE-CONSUMED keys only.
//
// HC-2 is why this exists at all: `allowMultiple: false` is stored by the shell
// and read by nothing, so a hand-edited shell.json can list the plugin twice
// and nothing upstream will stop it.
function classifyLayout(layout) {
  const entries = []
  const source = layout || {}
  for (let s = 0; s < SECTIONS.length; s++) {
    const section = source[SECTIONS[s]]
    if (!section || typeof section.length !== "number") continue
    for (let i = 0; i < section.length; i++) {
      const entry = section[i]
      if (entry && entry.id === PLUGIN_ID) {
        entries.push({ section: SECTIONS[s], index: i, entry: entry })
      }
    }
  }

  if (entries.length === 0) {
    return {
      present: false,
      conflict: false,
      entryCount: 0,
      effective: resolve({}).settings,
      conflictingKeys: [],
      warnings: []
    }
  }

  const resolved = []
  const warnings = []
  for (let i = 0; i < entries.length; i++) {
    const result = resolve(entries[i].entry)
    resolved.push(result.settings)
    for (let w = 0; w < result.warnings.length; w++) warnings.push(result.warnings[w])
  }

  // Compare the NORMALIZED values, not the raw ones. Two entries writing 5 and
  // "abc" both clamp to 30 and genuinely do not conflict; comparing raw values
  // would suspend polling over two different ways of writing the same mistake.
  const conflictingKeys = []
  for (let k = 0; k < SERVICE_CONSUMED_KEYS.length; k++) {
    const key = SERVICE_CONSUMED_KEYS[k]
    for (let i = 1; i < resolved.length; i++) {
      if (resolved[i][key] !== resolved[0][key]) {
        conflictingKeys.push(key)
        break
      }
    }
  }

  return {
    present: true,
    conflict: conflictingKeys.length > 0,
    entryCount: entries.length,
    effective: resolved[0],          // left-most in left → center → right order
    effectiveLocation: { section: entries[0].section, index: entries[0].index },
    conflictingKeys: conflictingKeys,
    warnings: warnings
  }
}

if (typeof module !== "undefined") module.exports = {
  DEFAULTS: DEFAULTS,
  BOUNDS: BOUNDS,
  COMPACT_METRICS: COMPACT_METRICS,
  SERVICE_CONSUMED_KEYS: SERVICE_CONSUMED_KEYS,
  SECTIONS: SECTIONS,
  PLUGIN_ID: PLUGIN_ID,
  normalizeInterval: normalizeInterval,
  normalizeCompactMetric: normalizeCompactMetric,
  normalizeDashboardUrl: normalizeDashboardUrl,
  resolve: resolve,
  settingOf: settingOf,
  resolveFromWidget: resolveFromWidget,
  classifyLayout: classifyLayout
}
