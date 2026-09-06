#!/usr/bin/env python3
"""Generate the helper-envelope fixture corpus.

This corpus is the artifact that closes risk R-F. The same accept files are
consumed in two directions:

  * tests/model/*.test.js feeds them to Health.js as INPUT.
  * tests/test_unifi_status.py asserts them as normalize.py OUTPUT.

One corpus, two directions. Without it, Phase 2 and Phase 5 each invent a shape
from the same prose and DEV-1 recurs — where `counts` could not answer the
health question that read it, and nothing noticed because each side was
self-consistent.

Two rules govern everything below.

AUTHORED FROM docs/protocol-v1.md, NOT FROM THE OpenAPI SUBSET. The OpenAPI
    document describes the UniFi API and cannot express the plugin's own `data`
    shape. Where the protocol document states a matrix — the DATA-007a
    consistency rules — this generator PARSES IT rather than restating it, so
    the corpus cannot drift from the normative text.

INVARIANTS ARE ASSERTED, NEVER COMPUTED. `devicesTotal`, `offlineTotal` and
    every `byClass` bucket are written out as literals, and the generator then
    asserts sum(byClass) == devicesTotal and byClass.down + byClass.impaired ==
    offlineTotal. Deriving one from the other would make the invariant vacuous:
    every fixture would satisfy it by construction and a fixture encoding a
    wrong count would look correct. Getting this backwards is precisely how
    DEV-1 survived review.

Usage:  python3 -B tests/tools/gen_envelopes.py [--out DIR] [--check]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROTOCOL_MD = os.path.join(REPO, "docs", "protocol-v1.md")
DEFAULT_OUT = os.path.join(REPO, "tests", "fixtures", "envelopes")

NONCE = "b7c1d4e9f2a68035"          # the nonce the service is deemed to have issued
LAUNCH_AT = "2026-01-15T11:58:00Z"  # service launch, for the DATA-008 skew window
ATTEMPTED_AT = "2026-01-15T12:00:00Z"
OBSERVED_AT = "2026-01-15T12:00:02Z"
RECEIPT_AT = "2026-01-15T12:00:03Z"
HELPER_VERSION = "0.1.0"

CLASSES = ["online", "transitional", "down", "impaired", "unknown"]


def uid(suffix):
    """Fixture UUIDs share the synthetic prefix of the API corpus (SEC-011)."""
    return "00000000-0000-5000-9000-%012x" % suffix


# --------------------------------------------------------------------------
# The DATA-007a matrix, parsed out of docs/protocol-v1.md
# --------------------------------------------------------------------------

def load_error_matrix():
    """Return {kind: {httpStatus, retryAfterSec, retryable, retryClass}}.

    Parsed, not restated. If someone edits the matrix in protocol-v1.md, the
    corpus regenerates to match or this function fails loudly — either is
    better than two documents quietly disagreeing about when an envelope is
    consistent.
    """
    with open(PROTOCOL_MD, encoding="utf-8") as handle:
        text = handle.read()
    section = re.search(
        r"## DATA-007a consistency matrix(.*?)\n## ", text, re.S)
    if not section:
        raise SystemExit("protocol-v1.md: DATA-007a matrix section not found")

    rows = {}
    for line in section.group(1).splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        kind = cells[0].strip("`")
        http, retry_after, retryable, retry_class = cells[1:]

        def allowed(cell):
            return not cell.startswith("forbidden")

        # A cell may name ONE fixed status ("allowed, `401`") or a RANGE
        # ("allowed, `100`-`599`, not `401`/`403`/`429`"). Reading the first
        # three-digit number in a range cell yields 100, which is a legal status
        # but not a transient 5xx — so the generated envelope would claim
        # retryable: true alongside a status the matrix calls fatal, and would
        # be rejected by a correct consumer while sitting in the ACCEPT corpus.
        # That bug was live until it was read off a generated fixture.
        codes = [int(c) for c in re.findall(r"`(\d{3})`", http)]
        is_range = "–" in http or "-" in http.replace("`-`", "")
        status = None if (is_range or not codes) else codes[0]

        rows[kind] = {
            "httpAllowed": allowed(http),
            "httpStatus": status,
            "httpIsRange": is_range,
            "retryAfterAllowed": allowed(retry_after),
            "retryable": {"`true`": True, "`false`": False}.get(retryable),
            "retryClass": retry_class,
        }
    if len(rows) != 19:
        raise SystemExit("protocol-v1.md: parsed %d error kinds, expected 19" % len(rows))

    # `http` is the one kind whose class depends on its status. The transient
    # list is parsed from the sub-matrix that follows, not restated here.
    sub = re.search(r"\| `httpStatus` \| `retryable` \| class \|\n\|[-| ]+\|\n(.*?)\n\n",
                    section.group(1), re.S)
    if not sub:
        raise SystemExit("protocol-v1.md: the `http` status sub-matrix was not found")
    transient = []
    for line in sub.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[1] == "`true`":
            transient = [int(c) for c in re.findall(r"`(\d{3})`", cells[0])]
    if not transient:
        raise SystemExit("protocol-v1.md: no transient statuses parsed for `http`")
    rows["http"]["transientStatuses"] = sorted(transient)
    return rows


# --------------------------------------------------------------------------
# Envelope construction
# --------------------------------------------------------------------------

def meta(populated=True):
    if not populated:
        # DEV-2: `meta` is always an object; every field but helperVersion is
        # nullable. This is the shape produced on `unconfigured` and
        # `uncommitted`, where the configuration those fields derive from is by
        # definition unreadable.
        return {
            "commitGeneration": None, "apiRootHost": None, "siteId": None,
            "allowInsecureTls": None, "customCaInUse": None,
            "helperVersion": HELPER_VERSION,
        }
    return {
        "commitGeneration": 7,
        "apiRootHost": "192.168.1.1",
        "siteId": uid(1),
        "allowInsecureTls": False,
        "customCaInUse": False,
        "helperVersion": HELPER_VERSION,
    }


def counts(clients, devices_total, offline_total, by_class,
           gateways, switches, access_points):
    """Assemble `counts` and CHECK the DEV-1 invariants.

    Every number here is supplied by the caller as a literal. Nothing is
    derived. The assertions below are the only thing standing between a wrong
    fixture and a corpus that certifies wrong behaviour in both languages.
    """
    def bucket(values):
        assert set(values) == set(CLASSES), "a class object must carry all five classes"
        return {name: values[name] for name in CLASSES}

    by_class = bucket(by_class)
    total = sum(by_class.values())
    assert total == devices_total, (
        "sum(byClass)=%d != devicesTotal=%d — byClass is a unique-device "
        "partition (DATA-006b)" % (total, devices_total))
    assert by_class["down"] + by_class["impaired"] == offline_total, (
        "byClass.down+impaired=%d != offlineTotal=%d (DATA-006b)"
        % (by_class["down"] + by_class["impaired"], offline_total))

    return {
        "clients": clients,
        "devicesTotal": devices_total,
        "offlineTotal": offline_total,
        "byClass": by_class,
        "gateways": bucket(gateways),
        "switches": bucket(switches),
        "accessPoints": bucket(access_points),
    }


def zero():
    return {name: 0 for name in CLASSES}


def cls(**kwargs):
    out = zero()
    out.update(kwargs)
    return out


def gateway(index, name, model, state, klass, uptime=None, down=None, up=None):
    return {"id": uid(index), "name": name, "model": model, "state": state,
            "class": klass, "uptimeSec": uptime, "downloadBps": down, "uploadBps": up}


def offline_device(index, name, model, state, klass):
    return {"id": uid(index), "name": name, "model": model, "state": state, "class": klass}


def warning(code, message, detail=None):
    return {"code": code, "message": message, "detail": detail}


def success(data, warnings=None, meta_populated=True):
    return {
        "protocolVersion": 1,
        "ok": True,
        "nonce": NONCE,
        "attemptedAt": ATTEMPTED_AT,
        "observedAt": OBSERVED_AT,
        "meta": meta(meta_populated),
        "data": data,
        "warnings": warnings or [],
        "error": None,
    }


def failure(error, warnings=None, meta_populated=True):
    return {
        "protocolVersion": 1,
        "ok": False,
        "nonce": NONCE,
        "attemptedAt": ATTEMPTED_AT,
        "observedAt": None,
        "meta": meta(meta_populated),
        "data": None,
        "warnings": warnings or [],
        "error": error,
    }


def data(site_name, wan, gateways, count_block, offline, version="9.1.0"):
    return {
        "site": {"id": uid(1), "name": site_name},
        "wan": wan,
        "gateways": gateways,
        "counts": count_block,
        "offlineDevices": offline,
        "applicationVersion": version,
    }


def wan(status, uptime=None, down=None, up=None):
    return {"status": status, "uptimeSec": uptime, "downloadBps": down, "uploadBps": up}


# --------------------------------------------------------------------------
# Accept corpus — envelopes the consumer MUST accept
# --------------------------------------------------------------------------
# Each entry names the REQ-002 rule it exercises, because the health decision
# table is the product and every one of its five rules needs an input that
# reaches it. Counts are literals; the assertions in counts() check them.

def accept_success():
    out = []

    out.append(("success_healthy",
                "REQ-002 rule 5 (green): one gateway online, everything up.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(42, 5, 0,
                           cls(online=5),
                           cls(online=1), cls(online=2), cls(online=2)),
                    [],
                ))))

    out.append(("success_degraded",
                "REQ-002 rule 4 (amber): one down, one impaired, one transitional. "
                "The UPDATING access point must NOT reach rule 4 — transitional is "
                "excluded by design, so an updating AP on a healthy site stays out "
                "of the amber condition.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(31, 5, 2,
                           cls(online=2, transitional=1, down=1, impaired=1),
                           cls(online=1), cls(online=1, down=1),
                           cls(transitional=1, impaired=1)),
                    [offline_device(21, "Garage Switch", "USW-Lite-8-PoE", "OFFLINE", "down"),
                     offline_device(22, "Attic AP", "U6-Pro", "ISOLATED", "impaired")],
                ))))

    out.append(("success_all_gateways_down",
                "REQ-002 rule 3 (red): at least one gateway exists and every "
                "gateway is down. Rule 3 strictly precedes rule 4, so the "
                "presence of other non-online devices must not downgrade this to "
                "amber.",
                success(data(
                    "Home",
                    wan("down"),
                    [gateway(10, "UDM Pro", "UDM-Pro", "OFFLINE", "down")],
                    counts(0, 3, 3,
                           cls(down=3),
                           cls(down=1), cls(down=1), cls(down=1)),
                    [offline_device(10, "UDM Pro", "UDM-Pro", "OFFLINE", "down"),
                     offline_device(21, "Garage Switch", "USW-Lite-8-PoE", "OFFLINE", "down"),
                     offline_device(22, "Attic AP", "U6-Pro", "OFFLINE", "down")],
                ))))

    out.append(("success_empty_site",
                "REQ-002 rule 2 (grey) and UX-005: zero adopted devices. A "
                "complete, successful, empty result — not a failure.",
                success(data(
                    "New Site",
                    wan("unknown"),
                    [],
                    counts(0, 0, 0, zero(), zero(), zero(), zero()),
                    [],
                ))))

    out.append(("success_no_gateway",
                "No gateway-featured device. Rule 3 requires at least one, so "
                "this site can NEVER be red however bad it gets; it is judged on "
                "rules 4 and 5 alone and reports wan.status == unknown.",
                success(data(
                    "Switch Closet",
                    wan("unknown"),
                    [],
                    counts(7, 2, 1,
                           cls(online=1, down=1),
                           zero(), cls(online=1), cls(down=1)),
                    [offline_device(22, "Attic AP", "U6-Pro", "OFFLINE", "down")],
                ))))

    out.append(("success_mixed_gateways",
                "One gateway ONLINE and another ISOLATED. Amber via rule 4, NOT "
                "red — rule 3 needs EVERY gateway down — and wan.status degraded.",
                success(data(
                    "Home",
                    wan("degraded", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000),
                     gateway(11, "UXG Backup", "UXG-Pro", "ISOLATED", "impaired")],
                    counts(12, 3, 1,
                           cls(online=2, impaired=1),
                           cls(online=1, impaired=1), cls(online=2), zero()),
                    [offline_device(11, "UXG Backup", "UXG-Pro", "ISOLATED", "impaired")],
                ))))

    out.append(("success_transitional_only",
                "Every non-online device is transitional. Rule 4 does not match "
                "transitional, so this is GREEN. offlineTotal is 0 and the "
                "offline list is empty, because REQ-003 keeps a transitional "
                "device out of it entirely.",
                success(data(
                    "Home",
                    wan("up", 3600, 9000000, 2000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             3600, 9000000, 2000000)],
                    counts(19, 4, 0,
                           cls(online=2, transitional=2),
                           cls(online=1), cls(online=1, transitional=1),
                           cls(transitional=1)),
                    [],
                ))))

    out.append(("success_unknown_state",
                "A device whose API state is outside the ten-value enum. REQ-000 "
                "puts it in `unknown`, which rule 4 DOES match, so this is amber. "
                "It is not in offlineTotal, which is down+impaired only — the "
                "exact gap that made byClass necessary (DEV-1).",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(8, 3, 0,
                           cls(online=2, unknown=1),
                           cls(online=1), cls(online=1, unknown=1), zero()),
                    [],
                ),
                [warning("unknown_device_state",
                         "Device reported an unrecognised state: REBOOTING.",
                         {"state": "REBOOTING", "deviceId": uid(23)})])))

    # AC-025 states this composition and its exact numbers: one device holding
    # all three roles plus TWO AP-only devices. gateways.online == 1,
    # switches.online == 1, accessPoints.online == 3, devicesTotal == 3, and the
    # role rows sum to 5. Do not fold the featureless device into this fixture —
    # it changes accessPoints.online to 2 and the criterion no longer has an
    # input. It has its own fixture below.
    out.append(("success_multi_feature_roles",
                "AC-025 exactly: a Dream Machine reporting gateway + switching + "
                "accessPoint at once, plus two AP-only devices. byClass counts 3 "
                "unique devices while the role rows sum to 5. They are NOT a "
                "partition and are not expected to agree — that contrast is what "
                "roleCountsAreNotAPartition tells the panel, because rows summing "
                "to 5 above a total of 3 otherwise read as a bug.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(18, 3, 0,
                           cls(online=3),
                           cls(online=1), cls(online=1), cls(online=3)),
                    [],
                ))))

    # DATA-006b requires byClass to count a device whose `features` array is
    # EMPTY. Such a device appears in no role object at all, so byClass is the
    # only place it is visible — and a partition that silently dropped it would
    # satisfy every role-based check while under-reporting the site.
    out.append(("success_featureless_device",
                "A device with an EMPTY features array, which appears in no role "
                "object at all. byClass must still count it exactly once "
                "(DATA-006b), so the role rows sum to 2 while devicesTotal is 3.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(6, 3, 0,
                           cls(online=3),
                           cls(online=1), cls(online=1), zero()),
                    [],
                ))))

    out.append(("success_500_down_bounded_list",
                "500 devices, every one down. offlineDevices is bounded to 10 by "
                "REQ-010 while offlineTotal stays 500, which is what AC-063's "
                "\"and 490 more\" is computed from. Computing it from the array "
                "length instead would understate an outage by 98%.",
                success(data(
                    "Campus",
                    wan("down"),
                    [gateway(10, "UDM Pro", "UDM-Pro", "OFFLINE", "down")],
                    counts(0, 500, 500,
                           cls(down=500),
                           cls(down=1), cls(down=499), zero()),
                    # The GATEWAY is down too, and its id sorts first, so it
                    # heads the list. An earlier version listed ten bulk
                    # switches and silently dropped it — which would have made
                    # the one device whose failure decides REQ-002 rule 3 the
                    # one device the panel never shows.
                    [offline_device(10, "UDM Pro", "UDM-Pro", "OFFLINE", "down")]
                    + [offline_device(100 + i, "Bulk Switch %03d" % i,
                                      "USW-Lite-8-PoE", "OFFLINE", "down")
                       for i in range(9)],
                ),
                [warning("offline_list_truncated",
                         "Showing 10 of 500 offline devices.",
                         {"listed": 10, "total": 500})])))

    out.append(("success_optional_gaps",
                "BIZ-004: every optional collection failed. clients is null, the "
                "WAN and gateway metrics are null, and warnings say so — but the "
                "batch still SUCCEEDS and still replaces the snapshot. A device "
                "health indicator must not be disabled by an unavailable "
                "throughput metric.",
                success(data(
                    "Home",
                    wan("up"),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online")],
                    counts(None, 5, 0,
                           cls(online=5),
                           cls(online=1), cls(online=2), cls(online=2)),
                    [],
                ),
                [warning("clients_unavailable", "Client list unavailable; count shown as unknown."),
                 warning("statistics_unavailable", "Gateway statistics unavailable.",
                         {"deviceId": uid(10)}),
                 warning("wans_unavailable", "WAN names unavailable.")])))

    out.append(("success_five_gateways_truncated",
                "Five gateways: REQ-008a fetches statistics for at most four, in "
                "primary order. The fifth is listed with null metrics and a "
                "warning — a bound on route 4's contribution to the REQ-017 time "
                "budget, not a failure.",
                success(data(
                    "Campus",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10 + i, "Gateway %d" % (i + 1), "UXG-Pro", "ONLINE", "online",
                             864000 + i * 3600, 12000000, 3000000) for i in range(4)]
                    + [gateway(14, "Gateway 5", "UXG-Pro", "ONLINE", "online")],
                    counts(20, 6, 0,
                           cls(online=6),
                           cls(online=5), cls(online=5), cls(online=1)),
                    [],
                ),
                # `total` is the GATEWAY count, not devicesTotal. This case
                # has five gateways among six devices, and said "4 of 6".
                [warning("gateway_statistics_truncated",
                         "Statistics fetched for 4 of 5 gateways.",
                         {"fetched": 4, "total": 5})])))

    out.append(("success_insecure_tls",
                "allowInsecureTls in force. UX-009 needs this to reach the panel, "
                "and meta is the only channel that can carry it — config.json is "
                "helper-only and QML cannot read it.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(42, 5, 0,
                           cls(online=5),
                           cls(online=1), cls(online=2), cls(online=2)),
                    [],
                ),
                [warning("insecure_tls",
                         "TLS certificate verification is disabled for this controller.")])))

    # meta in its all-null variant on a SUCCESS envelope. DEV-2 makes every
    # field but helperVersion nullable unconditionally, so this shape is legal
    # even where it is unlikely, and the validator must have one rule, not two.
    out.append(("success_meta_all_null",
                "DEV-2: meta present as an object with every field except "
                "helperVersion null, on a success envelope. A conditional "
                "container would give the validator two rules and the two "
                "implementations a shape to disagree about.",
                success(data(
                    "Home",
                    wan("up", 864000, 12000000, 3000000),
                    [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online",
                             864000, 12000000, 3000000)],
                    counts(42, 5, 0,
                           cls(online=5),
                           cls(online=1), cls(online=2), cls(online=2)),
                    [],
                ), None, meta_populated=False)))

    return out


def accept_failures(matrix):
    """One protocol-VALID failure envelope per DATA-007 kind.

    These belong in the accept corpus even though they report an error: the
    protocol layer must ACCEPT them and publish the named kind. A consumer that
    rejected them would convert every real controller failure into
    malformed_response and lose the diagnosis.

    Field presence comes from the parsed matrix, so an edit to protocol-v1.md
    propagates here rather than leaving the corpus asserting the old rules.
    """
    out = []
    messages = {
        "unconfigured": "No controller is configured yet.",
        "site_unselected": "Several sites are available; choose one.",
        "uncommitted": "Configuration has changed but has not been committed.",
        "credential": "The API key file could not be read.",
        "unauthorized": "The controller rejected the API key.",
        "forbidden": "The API key lacks permission for this site.",
        "tls": "The controller's certificate could not be verified.",
        "network": "The controller could not be reached.",
        "timeout": "The controller did not answer within the time budget.",
        "rate_limited": "The controller is rate limiting requests.",
        "http": "The controller returned an unexpected status.",
        "unsupported": "This controller version is not supported.",
        "redirect": "The controller answered with an unexpected redirect.",
        "configuration_conflict": "Two bar entries disagree about the refresh interval.",
        "partial_response": "A required collection could not be read completely.",
        "oversized_response": "The controller's response exceeded the size bound.",
        "malformed_response": "The controller's response could not be parsed.",
        "helper_unavailable": "Python 3.9 or newer is required.",
        "internal": "An internal error occurred.",
    }

    # `http` needs BOTH branches. Its class follows its status, so one fixture
    # would leave half the rule — and the half that decides whether a failing
    # controller is polled every 15 minutes or every 5 — with no input at all.
    variants = []
    for kind in sorted(matrix):
        if kind == "http":
            transient = matrix[kind]["transientStatuses"]
            variants.append(("failure_http_transient", kind, transient[0], True,
                             "a transient 5xx, so retryable"))
            variants.append(("failure_http_fatal", kind, 404, False,
                             "not a transient 5xx, so fatal"))
        else:
            variants.append(("failure_%s" % kind, kind, None, None, None))

    for name, kind, status_override, retryable_override, branch_note in variants:
        rules = matrix[kind]
        retryable = rules["retryable"] if retryable_override is None else retryable_override
        error = {"kind": kind, "message": messages[kind], "retryable": retryable}

        if rules["httpAllowed"]:
            status = status_override if status_override is not None else rules["httpStatus"]
            if status is None:
                raise SystemExit("no httpStatus decided for kind %r" % kind)
            error["httpStatus"] = status
        if rules["retryAfterAllowed"]:
            error["retryAfterSec"] = 30

        warnings = []
        populated = kind not in ("unconfigured", "uncommitted")
        if kind == "site_unselected":
            warnings.append(warning(
                "sites_discovered",
                "Several sites are available.",
                {"sites": [{"id": uid(1), "name": "Home"},
                           {"id": uid(2), "name": "Office"}]}))

        assert_matrix_consistent(error, matrix)
        out.append((
            name,
            "DATA-007 kind `%s` (%s class)%s. Protocol-VALID: the consumer must "
            "accept it and publish the kind.%s" % (
                kind, rules["retryClass"],
                ", " + branch_note if branch_note else "",
                "" if populated else " meta is all-null here because the "
                                    "configuration it derives from is by "
                                    "definition unreadable for this kind (DEV-2)."),
            failure(error, warnings or None, meta_populated=populated),
        ))
    return out


def assert_matrix_consistent(error, matrix):
    """Check an error object against the parsed DATA-007a matrix.

    Every accept fixture passes through here. An envelope in the ACCEPT corpus
    that a correct consumer would reject is worse than a missing fixture: it
    certifies the wrong behaviour in both languages at once, and reading one is
    how the `http` status bug above was found rather than shipped.
    """
    kind = error["kind"]
    rules = matrix[kind]
    status = error.get("httpStatus")

    if not rules["httpAllowed"]:
        assert status is None, "%s: httpStatus is forbidden for this kind" % kind
    elif rules["httpStatus"] is not None:
        assert status == rules["httpStatus"], (
            "%s: httpStatus must be %s, got %s" % (kind, rules["httpStatus"], status))
    else:
        assert status is not None and 100 <= status <= 599, (
            "%s: httpStatus %s outside 100-599" % (kind, status))
        assert status not in (401, 403, 429), (
            "%s: %s must use its typed kind, not `http`" % (kind, status))

    if not rules["retryAfterAllowed"]:
        assert "retryAfterSec" not in error, (
            "%s: retryAfterSec is forbidden for this kind" % kind)

    if rules["retryable"] is not None:
        assert error["retryable"] is rules["retryable"], (
            "%s: retryable must be %s" % (kind, rules["retryable"]))
    else:
        expected = status in rules["transientStatuses"]
        assert error["retryable"] is expected, (
            "%s with status %s: retryable must be %s" % (kind, status, expected))


# --------------------------------------------------------------------------
# Reject corpus — one per DATA-008 rejection class
# --------------------------------------------------------------------------
# Every one of these is published by the consumer as the single named kind
# `malformed_response`. The rejection CLASS is what the fixture records, so a
# test can assert the reader rejected for the right reason and not by accident.
#
# Some classes are not about the JSON body at all — they are about raw stdout
# bytes, the exit status, or the nonce the service issued. Those carry a
# `batch` context and, where they must, raw `stdout` text instead of a parsed
# envelope. Reducing them all to "an object with a bad field" would silently
# drop the classes that matter most.

def load_rejection_classes():
    """The 36 class ids, parsed out of docs/protocol-v1.md."""
    with open(PROTOCOL_MD, encoding="utf-8") as handle:
        text = handle.read()
    section = re.search(r"## DATA-008 rejection classes(.*?)\n## ", text, re.S)
    if not section:
        raise SystemExit("protocol-v1.md: DATA-008 rejection-class section not found")
    ids = []
    for line in section.group(1).splitlines():
        if line.startswith("| `"):
            ids.append(line.strip().strip("|").split("|")[0].strip().strip("`"))
    if not ids:
        raise SystemExit("protocol-v1.md: no rejection classes parsed")
    return ids


def base_batch(exit_status=0):
    return {
        "nonce": NONCE,
        "launchAt": LAUNCH_AT,
        "receiptAt": RECEIPT_AT,
        "exitStatus": exit_status,
    }


def valid_success():
    return success(data(
        "Home",
        wan("up", 864000, 12000000, 3000000),
        [gateway(10, "UDM Pro", "UDM-Pro", "ONLINE", "online", 864000, 12000000, 3000000)],
        counts(42, 5, 0, cls(online=5), cls(online=1), cls(online=2), cls(online=2)),
        [],
    ))


def valid_failure():
    return failure({"kind": "network", "message": "The controller could not be reached.",
                    "retryable": True})


def mutate(envelope, **changes):
    out = json.loads(json.dumps(envelope))
    for path, value in changes.items():
        target = out
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        if value is DELETE:
            del target[parts[-1]]
        else:
            target[parts[-1]] = value
    return out


class _Delete(object):
    def __repr__(self):
        return "<delete>"


DELETE = _Delete()


def reject_cases():
    """(id, rejection class, note, batch, envelope-or-None, stdout-or-None, synth-or-None)."""
    out = []

    def case(case_id, klass, note, envelope=None, batch=None, stdout=None, synthesize=None):
        out.append((case_id, klass, note, batch or base_batch(), envelope, stdout, synthesize))

    ok = valid_success()
    bad_exit = base_batch(exit_status=1)

    # ---- raw stdout ------------------------------------------------------
    case("stdout_oversized", "stdout_oversized",
         "A valid envelope padded past the 256 KiB stdout bound. Synthesized "
         "rather than committed so the corpus does not carry a quarter-megabyte "
         "file into every user's plugins directory.",
         synthesize={"kind": "padded_success", "bytes": 262145})
    case("stdout_not_json", "stdout_not_json",
         "stdout is not JSON. The likely real cause is a Python traceback, which "
         "is exactly the case that must not throw.",
         stdout="Traceback (most recent call last):\n  File \"unifi_status.py\"\n")
    case("stdout_multiple_values", "stdout_multiple_values",
         "Two JSON objects on stdout. A reader taking the first would act on a "
         "half-written batch.",
         stdout=json.dumps(ok) + "\n" + json.dumps(ok) + "\n")
    case("stdout_trailing_garbage", "stdout_trailing_garbage",
         "A valid envelope followed by non-whitespace.",
         stdout=json.dumps(ok) + " oops\n")
    case("envelope_not_object", "envelope_not_object",
         "The top-level JSON value is an array.", stdout="[1, 2, 3]\n")

    # ---- envelope keys ---------------------------------------------------
    case("envelope_unknown_key", "envelope_unknown_key",
         "A tenth top-level key. Unknown keys are rejected rather than ignored, "
         "so a future protocol cannot be half-read by an old consumer.",
         mutate(ok, unexpectedKey="whatever"))
    case("protocol_version_wrong", "protocol_version_wrong",
         "protocolVersion is 2.", mutate(ok, protocolVersion=2))
    case("protocol_version_string", "protocol_version_wrong",
         "protocolVersion is the STRING \"1\". It must be the integer.",
         mutate(ok, protocolVersion="1"))
    case("nonce_missing", "nonce_missing", "nonce absent.", mutate(ok, nonce=DELETE))
    case("nonce_mismatch", "nonce_mismatch",
         "A well-formed envelope carrying a nonce the service never issued. This "
         "is the shape of output from a PREVIOUS service instance after a plugin "
         "hot-reload, which an in-memory generation counter cannot detect because "
         "the reload reset it (DATA-005a).",
         mutate(ok, nonce="0000000000000000"))
    case("meta_missing", "meta_missing", "meta is null.", mutate(ok, meta=None))
    case("meta_helper_version_missing", "meta_helper_version_missing",
         "meta.helperVersion is null. It is the ONE field DEV-2 does not make "
         "nullable.",
         mutate(ok, **{"meta.helperVersion": None}))

    # ---- timestamps ------------------------------------------------------
    case("attempted_at_malformed", "attempted_at_malformed",
         "A timezone-less timestamp. Accepting it would silently reinterpret the "
         "helper's clock as local time.",
         mutate(ok, attemptedAt="2026-01-15 12:00:00"))
    case("attempted_at_too_early", "attempted_at_too_early",
         "attemptedAt is 40 minutes before service launch. DATA-008a makes this "
         "the ONE rejection that re-baselines and retries once, because it is "
         "the signature of a laptop resuming with a slow RTC rather than a bad "
         "helper. A second consecutive one is a genuine protocol error.",
         mutate(ok, attemptedAt="2026-01-15T11:18:00Z"))
    case("attempted_at_future", "attempted_at_future",
         "attemptedAt is after receipt.", mutate(ok, attemptedAt="2026-01-15T12:05:00Z"))

    # ---- ok identity -----------------------------------------------------
    case("ok_not_boolean_integer", "ok_not_boolean",
         "ok is the integer 1. Truthiness would accept it; identity must not.",
         mutate(ok, ok=1))
    case("ok_not_boolean_string", "ok_not_boolean",
         "ok is the string \"true\".", mutate(ok, ok="true"))
    case("ok_not_boolean_missing", "ok_not_boolean",
         "ok is absent.", mutate(ok, ok=DELETE))

    # ---- success shape ---------------------------------------------------
    case("success_exit_nonzero", "success_exit_nonzero",
         "ok: true paired with a non-zero exit status.", ok, batch=bad_exit)
    case("success_error_not_null", "success_error_not_null",
         "ok: true with a populated error.",
         mutate(ok, error={"kind": "network", "message": "x", "retryable": True}))
    case("success_observed_at_missing", "success_observed_at_missing",
         "ok: true with observedAt null.", mutate(ok, observedAt=None))
    case("success_observed_at_unordered", "success_observed_at_unordered",
         "observedAt precedes attemptedAt.",
         mutate(ok, observedAt="2026-01-15T11:59:00Z"))
    case("success_data_missing", "success_data_missing",
         "ok: true with data null.", mutate(ok, data=None))
    case("success_data_empty", "success_data_empty",
         "ok: true with data: {}. This passed EVERY stated rule in the original "
         "spec draft and is why DATA-008 now requires data to satisfy the "
         "DATA-006 schema.",
         mutate(ok, data={}))

    # ---- data schema and the DEV-1 invariants ---------------------------
    case("data_schema_violation_wan_status", "data_schema_violation",
         "wan.status is \"flapping\", outside the four-value domain.",
         mutate(ok, **{"data.wan.status": "flapping"}))
    case("data_schema_violation_missing_class", "data_schema_violation",
         "counts.byClass omits `impaired`. Absence must never be read as zero.",
         mutate(ok, **{"data.counts.byClass": {"online": 5, "transitional": 0,
                                               "down": 0, "unknown": 0}}))
    case("data_schema_violation_negative", "data_schema_violation",
         "devicesTotal is negative.",
         mutate(ok, **{"data.counts.devicesTotal": -1,
                       "data.counts.byClass": {"online": -1, "transitional": 0,
                                               "down": 0, "impaired": 0, "unknown": 0}}))
    case("byclass_sum_mismatch", "byclass_sum_mismatch",
         "byClass sums to 4 while devicesTotal says 5. Constructed by mutating a "
         "valid envelope, because counts() refuses to build it — which is the "
         "point: the generator asserts the invariant, so only an explicit "
         "mutation can violate it.",
         mutate(ok, **{"data.counts.byClass": {"online": 4, "transitional": 0,
                                               "down": 0, "impaired": 0, "unknown": 0}}))
    case("byclass_offline_mismatch", "byclass_offline_mismatch",
         "byClass.down + byClass.impaired is 2 while offlineTotal says 0.",
         mutate(ok, **{"data.counts.byClass": {"online": 3, "transitional": 0,
                                               "down": 1, "impaired": 1, "unknown": 0}}))

    # ---- failure shape ---------------------------------------------------
    fail = valid_failure()
    case("failure_exit_zero", "failure_exit_zero",
         "ok: false with exit status 0.", fail, batch=base_batch(0))
    case("failure_data_not_null", "failure_data_not_null",
         "ok: false carrying data.",
         mutate(fail, data=ok["data"]), batch=bad_exit)
    case("failure_observed_at_not_null", "failure_observed_at_not_null",
         "ok: false carrying observedAt.",
         mutate(fail, observedAt=OBSERVED_AT), batch=bad_exit)
    case("failure_error_missing", "failure_error_missing",
         "ok: false with error null.", mutate(fail, error=None), batch=bad_exit)
    case("error_kind_missing", "error_kind_missing",
         "error without a kind.",
         mutate(fail, error={"message": "something", "retryable": True}), batch=bad_exit)

    # ---- DATA-007a consistency ------------------------------------------
    case("error_http_status_forbidden", "error_http_status_forbidden",
         "kind `credential` carrying httpStatus 429. The spec's own example: it "
         "is rejected as malformed_response, NOT acted on as a rate limit. "
         "Acting on it would let a bad envelope steer the scheduler.",
         mutate(fail, error={"kind": "credential", "message": "x",
                             "httpStatus": 429, "retryable": False}),
         batch=bad_exit)
    case("error_http_status_wrong", "error_http_status_wrong",
         "kind `unauthorized` carrying httpStatus 500 instead of 401.",
         mutate(fail, error={"kind": "unauthorized", "message": "x",
                             "httpStatus": 500, "retryable": False}),
         batch=bad_exit)
    case("error_http_status_typed_as_http", "error_http_status_wrong",
         "kind `http` carrying 401. DATA-007 requires a received status to keep "
         "its TYPED kind, so 401 must be `unauthorized` and never `http`.",
         mutate(fail, error={"kind": "http", "message": "x",
                             "httpStatus": 401, "retryable": False}),
         batch=bad_exit)
    case("error_retry_after_forbidden", "error_retry_after_forbidden",
         "kind `timeout` carrying retryAfterSec, which only rate_limited may.",
         mutate(fail, error={"kind": "timeout", "message": "x",
                             "retryAfterSec": 30, "retryable": True}),
         batch=bad_exit)
    case("error_retryable_mismatch", "error_retryable_mismatch",
         "kind `credential` claiming retryable: true. The kind is fatal, so this "
         "would turn a permanent misconfiguration into an endless poll.",
         mutate(fail, error={"kind": "credential", "message": "x", "retryable": True}),
         batch=bad_exit)

    # ---- warnings and bounds --------------------------------------------
    case("warnings_not_array", "warnings_not_array",
         "warnings is null rather than an empty array.", mutate(ok, warnings=None))
    case("warning_shape_invalid", "warning_shape_invalid",
         "A warning is a bare string. The panel would have to parse prose, and "
         "DATA-012's {id, name} site pairs could not be carried at all.",
         mutate(ok, warnings=["just a string"]))
    case("bound_exceeded", "bound_exceeded",
         "A 513-character site name, one past the 512-char string bound.",
         mutate(ok, **{"data.site.name": "N" * 513}))

    return out


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------

def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def generate(out_dir):
    matrix = load_error_matrix()
    declared_classes = load_rejection_classes()

    index = {
        "generatedBy": "tests/tools/gen_envelopes.py",
        "contract": "docs/protocol-v1.md",
        "note": (
            "The accept corpus is consumed in TWO directions: tests/model/*.test.js "
            "feeds it to Health.js as input, and tests/test_unifi_status.py asserts "
            "it as normalize.py output. That is what stops the Python producer and "
            "the JS consumer inventing two shapes from the same prose (risk R-F, "
            "and the mechanism by which DEV-1 went unnoticed)."
        ),
        "accept": {},
        "reject": {},
    }

    for name, note, envelope in accept_success() + accept_failures(matrix):
        write_json(os.path.join(out_dir, "accept", "%s.json" % name), envelope)
        index["accept"][name] = {
            "verdict": "accept",
            "shape": "success" if envelope["ok"] else "failure",
            # Both sides are bound by an accept fixture, and for opposite
            # reasons: the consumer must not reject it, the producer must be
            # able to emit exactly it.
            "consumedBy": ["service (Protocol.js must accept)",
                           "helper (normalize.py must be able to produce)"],
            "errorKind": (envelope["error"] or {}).get("kind"),
            "note": note,
        }

    seen_classes = set()
    for case_id, klass, note, batch, envelope, stdout, synthesize in reject_cases():
        body = {
            "id": case_id,
            "rejectionClass": klass,
            # Every envelope rejection is the SERVICE's to make: it is the
            # consumer, and DATA-008 makes every one of them publish as the
            # single named kind malformed_response. The helper's obligation is
            # the mirror image — never to emit one — which is why the field
            # below names both and says which is which.
            "rejectedBy": "service",
            "neverEmittedBy": "helper",
            "publishedAs": "malformed_response",
            "rule": "DATA-008 / %s" % klass,
            "note": note,
            "batch": batch,
            "envelope": envelope,
            "stdout": stdout,
            "synthesize": synthesize,
        }
        write_json(os.path.join(out_dir, "reject", "%s.json" % case_id), body)
        index["reject"][case_id] = {
            "rejectionClass": klass,
            "rejectedBy": "service",
            "rule": body["rule"],
            "publishedAs": "malformed_response",
            "note": note,
        }
        seen_classes.add(klass)

    # Every class declared in protocol-v1.md must have at least one fixture.
    # A class with no fixture is a rule with no test, and AC-072 would report a
    # gap it could not explain.
    missing = [c for c in declared_classes if c not in seen_classes]
    if missing:
        raise SystemExit("no reject fixture for DATA-008 class(es): %s" % ", ".join(missing))
    extra = [c for c in sorted(seen_classes) if c not in declared_classes]
    if extra:
        raise SystemExit("reject fixture names a class absent from protocol-v1.md: %s"
                         % ", ".join(extra))

    index["coverage"] = {
        "errorKinds": len(matrix),
        "rejectionClasses": len(declared_classes),
        "acceptFixtures": len(index["accept"]),
        "rejectFixtures": len(index["reject"]),
    }
    write_json(os.path.join(out_dir, "index.json"), index)
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if not args.check:
        if os.path.isdir(args.out):
            shutil.rmtree(args.out)
        index = generate(args.out)
        print("wrote %d accept and %d reject envelope(s) to %s"
              % (index["coverage"]["acceptFixtures"],
                 index["coverage"]["rejectFixtures"], args.out))
        return 0

    tmp = tempfile.mkdtemp(prefix="unifi-envelopes-check.")
    try:
        generate(tmp)
        differences = []
        for root, _dirs, files in os.walk(tmp):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), tmp)
                committed = os.path.join(args.out, rel)
                if not os.path.exists(committed):
                    differences.append("missing from the corpus: %s" % rel)
                    continue
                with open(os.path.join(root, name), "rb") as a, open(committed, "rb") as b:
                    if a.read() != b.read():
                        differences.append("differs: %s" % rel)
        for root, _dirs, files in os.walk(args.out):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), args.out)
                if not os.path.exists(os.path.join(tmp, rel)):
                    differences.append("not produced by the generator: %s" % rel)
        if differences:
            for line in sorted(differences):
                print("  " + line, file=sys.stderr)
            print("FAIL: the committed corpus does not match the generator", file=sys.stderr)
            return 1
        print("ok: the committed corpus regenerates byte-identically")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
