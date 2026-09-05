#!/usr/bin/env python3
"""Generate the synthetic UniFi API fixture corpus.

Three properties, each of which the corpus is useless without:

DETERMINISTIC.  No randomness, no wall clock. Re-running produces byte-identical
    files, so `git diff` after a regeneration shows exactly what a code change
    did to the corpus and nothing else. tests/test_fixtures.py asserts this by
    regenerating into a temp tree and comparing.

SCHEMA-DRIVEN.  Every record emitted is validated against
    docs/feature-specs/omarchy-unifi-plugin/unifi-network-v1-readonly-subset.json
    before it is written. Fixtures that drift from the published API contract
    are worse than no fixtures: they make the helper pass against a controller
    that does not exist. The validator is deliberately written here rather than
    pulled in, because SPEC §11 forbids runtime dependencies outside the
    standard library and the test tooling honours the same rule.

SYNTHETIC BY CONSTRUCTION (SEC-011 / AC-059).  Not "scrubbed" — generated from
    a documented namespace so that a test can prove any identifier in the corpus
    is reproducible from that namespace and therefore cannot have come from a
    real deployment. See IDENTITY below.

Usage:  python3 -B tests/tools/gen_fixtures.py [--out DIR] [--check]

  --check  regenerate into a temp directory and diff against the committed
           corpus, exiting non-zero on any difference. This is what makes the
           corpus tamper-evident rather than merely present.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC_JSON = os.path.join(
    REPO, "docs", "feature-specs", "omarchy-unifi-plugin",
    "unifi-network-v1-readonly-subset.json",
)
DEFAULT_OUT = os.path.join(REPO, "tests", "fixtures", "api")

# --------------------------------------------------------------------------
# IDENTITY — the SEC-011 construction
# --------------------------------------------------------------------------
# Every UUID is uuid5(NAMESPACE, label), where NAMESPACE is itself uuid5 of a
# .invalid DNS name. RFC 2606 reserves .invalid permanently, so the namespace
# can never correspond to a real host, and every identifier below is a pure
# function of a label committed to this file. AC-059's check is therefore not a
# heuristic: it regenerates and compares.
FIXTURE_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "fixtures.omarchy-unifi.invalid")

# MACs come from 02:00:00:xx:xx:xx. The 0x02 first octet has the
# locally-administered bit set, which by IEEE 802 definition means the address
# is NOT drawn from any assigned OUI. There is no vendor it could belong to.
MAC_PREFIX = (0x02, 0x00, 0x00)

# All addresses are RFC1918. Devices sit on 192.168.10.0/24, clients on
# 192.168.20.0/24.
DEVICE_SUBNET = "192.168.10"
CLIENT_SUBNET = "192.168.20"

BASE_TIME = "2026-01-15T12:00:00Z"

DEVICE_STATES = [
    "ONLINE", "OFFLINE", "PENDING_ADOPTION", "UPDATING", "GETTING_READY",
    "ADOPTING", "DELETING", "CONNECTION_INTERRUPTED", "ISOLATED",
    "U5G_INCORRECT_TOPOLOGY",
]
# Not in the enum. REQ-000 requires it to land in `unknown` and raise a warning
# rather than being counted online, so the corpus must contain one.
UNKNOWN_STATE = "REBOOTING"

PAGE_LIMIT = 200


# Every label ever passed to uid() is recorded and shipped in the corpus
# manifest. That turns AC-059's "no UUID from a real deployment" from a
# pattern-matching heuristic into a proof: tests/test_fixtures.py recomputes
# uuid5(FIXTURE_NS, label) for each recorded label and asserts the corpus
# contains no identifier outside that derived set.
MINTED_LABELS = set()


def uid(label):
    MINTED_LABELS.add(label)
    return str(uuid.uuid5(FIXTURE_NS, label))


def mac(index):
    return "%02x:%02x:%02x:%02x:%02x:%02x" % (
        MAC_PREFIX[0], MAC_PREFIX[1], MAC_PREFIX[2],
        (index >> 16) & 0xFF, (index >> 8) & 0xFF, index & 0xFF,
    )


def device_ip(index):
    return "%s.%d" % (DEVICE_SUBNET, 1 + (index % 254))


def client_ip(index):
    return "%s.%d" % (CLIENT_SUBNET, 1 + (index % 254))


# --------------------------------------------------------------------------
# A minimal OpenAPI 3 validator: type, required, enum, format, $ref, arrays.
# --------------------------------------------------------------------------

class SchemaError(Exception):
    pass


class Validator(object):
    def __init__(self, document):
        self.schemas = document["components"]["schemas"]

    def resolve(self, schema):
        while "$ref" in schema:
            ref = schema["$ref"]
            if not ref.startswith("#/components/schemas/"):
                raise SchemaError("unsupported $ref: %s" % ref)
            name = ref[len("#/components/schemas/"):]
            if name not in self.schemas:
                # The subset extraction stopped at 15 schemas; a $ref outside it
                # is not an error in the fixture, it is a gap in the extract.
                # Say so rather than silently accepting anything.
                raise SchemaError("$ref outside the extracted subset: %s" % name)
            schema = self.schemas[name]
        return schema

    def check(self, value, schema, path="$", relaxed=frozenset(), relax_enum=False):
        """Validate `value` against `schema`.

        `relaxed` names properties whose `enum` check is skipped; `relax_enum`
        carries that decision down into the value itself. It exists for exactly
        one fixture: REQ-000 requires an unrecognised future device state to
        land in `unknown` rather than be counted online, so the corpus MUST
        contain a state outside the published enum. Every use is recorded in
        the corpus manifest, so the relaxation is visible rather than a
        validator quietly not running.
        """
        schema = self.resolve(schema)
        kind = schema.get("type")

        if kind == "object":
            if not isinstance(value, dict):
                raise SchemaError("%s: expected object, got %s" % (path, type(value).__name__))
            for name in schema.get("required", []):
                if name not in value:
                    raise SchemaError("%s: missing required property %r" % (path, name))
            props = schema.get("properties", {})
            for name, item in value.items():
                if name in props:
                    self.check(item, props[name], "%s.%s" % (path, name),
                               relaxed, name in relaxed)
            return

        if kind == "array":
            if not isinstance(value, list):
                raise SchemaError("%s: expected array" % path)
            items = schema.get("items")
            if items is not None:
                for i, item in enumerate(value):
                    self.check(item, items, "%s[%d]" % (path, i), relaxed, relax_enum)
            if schema.get("uniqueItems"):
                keys = [json.dumps(v, sort_keys=True) for v in value]
                if len(set(keys)) != len(keys):
                    raise SchemaError("%s: uniqueItems violated" % path)
            return

        if kind == "string":
            if not isinstance(value, str):
                raise SchemaError("%s: expected string, got %s" % (path, type(value).__name__))
            if "enum" in schema and value not in schema["enum"] and not relax_enum:
                raise SchemaError("%s: %r is not in the enum %r" % (path, value, schema["enum"]))
            if schema.get("format") == "uuid":
                try:
                    uuid.UUID(value)
                except (ValueError, AttributeError, TypeError):
                    raise SchemaError("%s: %r is not a uuid" % (path, value))
            return

        if kind == "integer":
            # bool is a subclass of int in Python; the API means neither.
            if isinstance(value, bool) or not isinstance(value, int):
                raise SchemaError("%s: expected integer" % path)
            return

        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError("%s: expected number" % path)
            return

        if kind == "boolean":
            if not isinstance(value, bool):
                raise SchemaError("%s: expected boolean" % path)
            return

        if kind is None:
            return

        raise SchemaError("%s: unsupported schema type %r" % (path, kind))


# --------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------

def device(label, state, features, index, name=None, model=None):
    return {
        "id": uid("device/" + label),
        "name": name or label.replace("-", " ").title(),
        "model": model or "USW-Lite-8-PoE",
        "macAddress": mac(index),
        "ipAddress": device_ip(index),
        "state": state,
        "features": list(features),
        "interfaces": ["ports"] if "gateway" in features or "switching" in features else ["radios"],
        "firmwareVersion": "9.1.0",
        "firmwareUpdatable": False,
        "supported": True,
    }


def site(label, name):
    return {"id": uid("site/" + label), "internalReference": label, "name": name}


def wan(label, name):
    return {"id": uid("wan/" + label), "name": name}


def client(index):
    return {
        "id": uid("client/%d" % index),
        "name": "Client %03d" % index,
        "type": "WIRED" if index % 2 == 0 else "WIRELESS",
        "access": {"type": "DEFAULT"},
        "ipAddress": client_ip(index),
        "macAddress": mac(0x100000 + index),
        "uplinkDeviceId": uid("device/switch-1"),
        "connectedAt": BASE_TIME,
    }


def statistics(uptime_sec, rx_bps, tx_bps):
    return {
        "interfaces": {"radios": []},
        "uptimeSec": uptime_sec,
        "uplink": {"rxRateBps": rx_bps, "txRateBps": tx_bps},
    }


def page(records, offset=0, limit=PAGE_LIMIT, total=None):
    return {
        "offset": offset,
        "limit": limit,
        "count": len(records),
        "totalCount": len(records) if total is None else total,
        "data": records,
    }


def paginate(records, limit=PAGE_LIMIT):
    """Split a record list into well-formed pages. Always emits at least one."""
    total = len(records)
    if total == 0:
        return [page([], 0, limit, 0)]
    pages = []
    for offset in range(0, total, limit):
        pages.append(page(records[offset:offset + limit], offset, limit, total))
    return pages


# --------------------------------------------------------------------------
# Scenarios — a whole controller state, one directory each
# --------------------------------------------------------------------------

def gateway(label, state, index, model="UDM-Pro"):
    return device(label, state, ["gateway", "switching"], index, model=model)


def scenarios():
    """Each entry is (name, note, devices, sites, wans, clients) with an
    optional seventh element: a stated schema deviation, recorded in the
    manifest. Only one scenario has one."""
    out = []

    healthy = [
        gateway("gw-1", "ONLINE", 1),
        device("switch-1", "ONLINE", ["switching"], 2),
        device("switch-2", "ONLINE", ["switching"], 3),
        device("ap-1", "ONLINE", ["accessPoint"], 4, model="U6-Pro"),
        device("ap-2", "ONLINE", ["accessPoint"], 5, model="U6-Pro"),
    ]
    out.append(("healthy", "Every device online, one gateway. REQ-002 rule 5 (green).", healthy, 1, 1, 42))

    degraded = [
        gateway("gw-1", "ONLINE", 1),
        device("switch-1", "ONLINE", ["switching"], 2),
        device("switch-2", "OFFLINE", ["switching"], 3),
        device("ap-1", "ISOLATED", ["accessPoint"], 4, model="U6-Pro"),
        device("ap-2", "UPDATING", ["accessPoint"], 5, model="U6-Pro"),
    ]
    out.append(("degraded", "One down, one impaired, one transitional. REQ-002 rule 4 (amber); "
                            "the UPDATING AP must NOT contribute.", degraded, 1, 1, 31))

    all_down = [
        gateway("gw-1", "OFFLINE", 1),
        device("switch-1", "OFFLINE", ["switching"], 2),
        device("ap-1", "OFFLINE", ["accessPoint"], 3, model="U6-Pro"),
    ]
    out.append(("all-down", "Every gateway down. REQ-002 rule 3 (red), which strictly precedes amber.",
                all_down, 1, 1, 0))

    out.append(("empty-site", "Zero adopted devices. REQ-002 rule 2 (grey) and UX-005; the page is a "
                              "VALID empty result, not a premature one (DATA-009b).", [], 1, 1, 0))

    no_gateway = [
        device("switch-1", "ONLINE", ["switching"], 2),
        device("ap-1", "OFFLINE", ["accessPoint"], 3, model="U6-Pro"),
    ]
    out.append(("no-gateway", "No gateway-featured device: can never be red (rule 3 needs one) and "
                              "reports wan.status == unknown.", no_gateway, 1, 1, 7))

    two_gw = [
        gateway("gw-1", "ONLINE", 1),
        gateway("gw-2", "ISOLATED", 2, model="UXG-Pro"),
        device("switch-1", "ONLINE", ["switching"], 3),
    ]
    out.append(("two-gateways", "One ONLINE, one ISOLATED: amber via rule 4, NOT red, and "
                                "wan.status == degraded.", two_gw, 1, 2, 12))

    five_gw = [gateway("gw-%d" % i, "ONLINE", i) for i in range(1, 6)]
    five_gw.append(device("ap-1", "ONLINE", ["accessPoint"], 6, model="U6-Pro"))
    out.append(("five-gateways", "Five gateways: statistics are fetched for at most four (REQ-008a), "
                                 "the fifth listed without metrics plus a warning.", five_gw, 1, 2, 20))

    # One device per state, plus the unknown one. Ordering is the enum order so
    # a diff shows which state changed.
    all_states = [
        gateway("gw-1", "ONLINE", 1),
    ]
    for i, state in enumerate(DEVICE_STATES):
        all_states.append(device("state-%s" % state.lower(), state, ["switching"], 10 + i))
    all_states.append(device("state-unknown", UNKNOWN_STATE, ["switching"], 30))
    out.append(("all-states", "One device in each of the ten API states plus %s, which is outside the "
                              "enum and must land in `unknown` with a warning (REQ-000)." % UNKNOWN_STATE,
                all_states, 1, 1, 5, "device.state enum relaxed: %s is deliberately outside the "
                                     "published enum, because REQ-000's `unknown` class has no other "
                                     "way to be exercised." % UNKNOWN_STATE))

    multi_feature = [
        device("udm-1", "ONLINE", ["gateway", "switching", "accessPoint"], 1, model="UDM-Pro"),
        device("ap-only", "ONLINE", ["accessPoint"], 2, model="U6-Pro"),
        device("featureless", "ONLINE", [], 3, model="UNKNOWN-DEV"),
    ]
    out.append(("multi-feature", "A Dream Machine reporting all three roles at once, an AP-only device, "
                                 "and a device with an EMPTY features array. byClass must count all "
                                 "three exactly once; the role objects must not sum to devicesTotal.",
                multi_feature, 1, 1, 18))

    # 500 devices, all down: three pages at limit 200, and the envelope's
    # offlineDevices array bounded to 10 while offlineTotal stays 500.
    large = [gateway("gw-1", "OFFLINE", 0)]
    large += [device("bulk-%03d" % i, "OFFLINE", ["switching"], 100 + i) for i in range(1, 500)]
    out.append(("large-500-down", "500 devices, every one down. Three pages at limit 200; the envelope "
                                  "bounds offlineDevices to 10 while offlineTotal stays 500, which is "
                                  "what AC-063's \"and 490 more\" is computed from.",
                large, 1, 1, 0))

    out.append(("multi-site", "Several sites and no committed siteId: DATA-012 requires site_unselected "
                              "with the {id, name} pairs carried in warnings.", healthy, 3, 1, 42))
    out.append(("zero-site", "Zero sites: DATA-012 requires `unsupported`, not an empty success.",
                healthy, 0, 1, 42))

    return out


# --------------------------------------------------------------------------
# Pagination cases — one per DATA-009 invariant, plus the accept cases
# --------------------------------------------------------------------------

def bulk(n, first=0, limit=PAGE_LIMIT):
    return [device("page-%04d" % i, "ONLINE", ["switching"], 1000 + i)
            for i in range(first, first + n)]


# Violation cases use a deliberately TINY collection: five records over pages of
# two. An invariant is exactly as broken with five records as with four hundred,
# and at this size the mutation is visible when you open the file instead of
# being buried in 200 near-identical device objects. The realistic scale that
# matters — the >25 and >200 boundaries — is carried by the accept cases, which
# is where it actually proves something.
SMALL = 5
SMALL_LIMIT = 2


def small_pages():
    return paginate(bulk(SMALL, limit=SMALL_LIMIT), limit=SMALL_LIMIT)


def clone(pages):
    return json.loads(json.dumps(pages))


def pagination_cases():
    """Each case: (id, expectation, note, responses, synthesize).

    `expectation` is "accept" or the DATA-009 invariant id that must reject it.
    Exactly one of `responses` / `synthesize` is non-None; see
    tests/tools/fixture_pages.py for why the second exists.
    """
    cases = []

    def case(case_id, expect, note, responses=None, synthesize=None):
        cases.append((case_id, expect, note, responses, synthesize))

    # ---- accept: the boundaries that need realistic scale ----------------
    case("accept_single_page_25", "accept",
         "25 records: one page, at the >25 default-limit boundary.",
         paginate(bulk(25)))
    case("accept_boundary_200", "accept",
         "Exactly 200 records: one page filled to the API maximum limit. The "
         "terminal page is FULL, so a reader that stops only on a short page "
         "loops forever here.",
         paginate(bulk(200)))
    case("accept_boundary_201", "accept",
         "201 records: the first record past the limit, so page two holds "
         "exactly one.",
         paginate(bulk(201)))
    case("accept_three_pages_413", "accept",
         "413 records over three pages (200 + 200 + 13).",
         paginate(bulk(413)))
    case("accept_empty_collection", "accept",
         "A genuinely empty collection: count == 0 AND totalCount == 0, which "
         "DATA-009b makes a COMPLETE result. The empty site is exactly the site "
         "whose owner is setting the plugin up for the first time.",
         [page([], 0, PAGE_LIMIT, 0)])

    # ---- one per invariant, at small scale -------------------------------
    bad = clone(small_pages()); bad[1]["offset"] = 1
    case("offset_matches_request", "offset_matches_request",
         "Page 2 answers with offset 1 for a request at offset 2.", bad)

    bad = clone(small_pages()); bad[0]["count"] = 1
    case("count_equals_data_length", "count_equals_data_length",
         "count says 1 while data holds 2.", bad)

    bad = clone(small_pages())
    bad[0]["count"] = 3
    bad[0]["data"] = bulk(3, limit=SMALL_LIMIT)
    case("count_within_limit", "count_within_limit",
         "A page returns three records for a limit of two.", bad)

    bad = clone(small_pages()); bad[1]["count"] = 0; bad[1]["data"] = []
    case("non_terminal_page_progresses", "non_terminal_page_progresses",
         "A middle page is empty while offset < totalCount: premature, and the "
         "deliberate twin of accept_empty_collection.", bad)

    bad = clone(small_pages()); bad[0]["data"][1]["id"] = bad[0]["data"][0]["id"]
    case("record_ids_unique", "record_ids_unique",
         "Two records on one page share an id, so accumulation reaches four "
         "unique records for a totalCount of five and never terminates.", bad)

    bad = clone(small_pages())
    for pg in bad:
        pg["totalCount"] = -1
    case("total_count_non_negative", "total_count_non_negative",
         "A negative totalCount.", bad)

    bad = clone(small_pages()); bad[2]["totalCount"] = 99
    case("total_count_stable", "total_count_stable",
         "totalCount changes between the first page and the last.", bad)

    bad = clone(small_pages()); bad[0]["limit"] = 4
    case("advance_by_validated_count", "advance_by_validated_count",
         "The response echoes a limit of four when two was requested. Advancing "
         "by response arithmetic rather than the validated count skips records.",
         bad)

    case("empty_is_valid_not_premature", "empty_is_valid_not_premature",
         "count == 0 while offset < totalCount. Read together with "
         "accept_empty_collection this pair is the whole of DATA-009b; either "
         "one alone can be satisfied by a wrong rule.",
         [page([], 0, SMALL_LIMIT, SMALL)])

    # OFFSET DRIFT AT CONSTANT CARDINALITY, modelled faithfully rather than just
    # made to differ. The collection starts as [r0..r4]. After page 0 is read,
    # r0 is deleted and r900 appended, so cardinality is unchanged at 5 and
    # totalCount never moves — but every later page slides down by one.
    #
    #   read offset 0 -> [r0, r1]        (before the change)
    #   read offset 2 -> [r3, r4]        (r2 has slid to offset 1; SKIPPED)
    #   read offset 4 -> [r900]
    #   accumulated    -> r0 r1 r3 r4 r900 = 5 unique, totalCount 5
    #
    # Offsets match, counts match, ids are unique, totalCount is stable and
    # non-negative, the terminal page is short, and the unique count equals
    # totalCount. Every stated invariant passes and r2 is gone. The DATA-009a
    # re-read of page 0 returns [r1, r2], which differs from [r0, r1], and is
    # the only thing in the contract that notices.
    original = bulk(SMALL, limit=SMALL_LIMIT)
    after = original[1:] + bulk(1, first=900, limit=SMALL_LIMIT)
    drift = [
        page(original[0:2], 0, SMALL_LIMIT, SMALL),
        page(after[2:4], 2, SMALL_LIMIT, SMALL),
        page(after[4:5], 4, SMALL_LIMIT, SMALL),
        page(after[0:2], 0, SMALL_LIMIT, SMALL),   # the DATA-009a re-read
    ]
    case("reread_page_zero_matches", "reread_page_zero_matches",
         "OFFSET DRIFT AT CONSTANT CARDINALITY. One record is deleted and "
         "another appended after page 0 is read, so every later page slides by "
         "one and exactly one record is never returned. Offsets, counts, id "
         "uniqueness, totalCount stability and terminal completeness ALL still "
         "pass. The trailing response is the DATA-009a re-read of page 0, which "
         "is the only thing in the contract that catches this.",
         drift)

    bad = clone(small_pages()); bad[-1]["totalCount"] = SMALL
    for pg in bad[:-1]:
        pg["totalCount"] = SMALL
    bad[-1]["data"] = []
    bad[-1]["count"] = 0
    case("terminal_completeness", "terminal_completeness",
         "The collection terminates with fewer unique records accumulated than "
         "the terminal totalCount claims.", bad)

    # ---- the two bound cases, synthesized -------------------------------
    case("max_pages_enforced", "max_pages_enforced",
         "65 pages: one past the 64-page bound. A controller that never reports "
         "a terminal page must not hang the batch. Synthesized rather than "
         "committed — materialising it is 6 MB of near-identical records in a "
         "repository that gets cloned into the plugins directory on install.",
         None, {"pages": 65, "recordsPerPage": PAGE_LIMIT, "totalCount": 100000})

    case("max_decoded_bytes_enforced", "max_decoded_bytes_enforced",
         "Pages whose accumulated decoded size crosses the 8 MiB per-collection "
         "bound before the page count does. Synthesized, as above.",
         None, {"pages": 64, "recordsPerPage": PAGE_LIMIT, "totalCount": 100000})

    return cases


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------

def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def generate(out_dir):
    with open(SPEC_JSON, encoding="utf-8") as handle:
        validator = Validator(json.load(handle))

    manifest = {"scenarios": {}, "pagination": {}}

    for entry in scenarios():
        name, note, devices, site_count, wan_count, client_count = entry[:6]
        deviation = entry[6] if len(entry) > 6 else None
        relaxed = frozenset(["state"]) if deviation else frozenset()
        base = os.path.join(out_dir, "scenarios", name)

        info = {"applicationVersion": "9.1.0"}
        validator.check(info, {"$ref": "#/components/schemas/Application info"})
        write_json(os.path.join(base, "info.json"), info)

        sites = [site("%s-%d" % (name, i), "Site %d" % (i + 1)) for i in range(site_count)]
        site_pages = paginate(sites)
        for i, pg in enumerate(site_pages):
            validator.check(pg, {"$ref": "#/components/schemas/Site overview page"})
            write_json(os.path.join(base, "sites.page%d.json" % i), pg)

        device_pages = paginate(devices)
        for i, pg in enumerate(device_pages):
            validator.check(pg, {"$ref": "#/components/schemas/Adopted device overview page"},
                            relaxed=relaxed)
            write_json(os.path.join(base, "devices.page%d.json" % i), pg)

        wans = [wan("%s-%d" % (name, i), "Internet %d" % (i + 1)) for i in range(wan_count)]
        wan_pages = paginate(wans)
        for i, pg in enumerate(wan_pages):
            validator.check(pg, {"$ref": "#/components/schemas/WAN overview page"})
            write_json(os.path.join(base, "wans.page%d.json" % i), pg)

        clients = [client(i) for i in range(client_count)]
        client_pages = paginate(clients)
        for i, pg in enumerate(client_pages):
            validator.check(pg, {"$ref": "#/components/schemas/Client overview page"})
            write_json(os.path.join(base, "clients.page%d.json" % i), pg)

        # Statistics for every gateway, so REQ-008a's four-gateway bound has
        # more than four to choose from in the five-gateway scenario.
        stats_written = []
        gateways = [d for d in devices if "gateway" in d["features"]]
        for i, gw in enumerate(sorted(gateways, key=lambda d: d["id"])):
            body = statistics(864000 + i * 3600, 12000000 + i * 1000, 3000000 + i * 1000)
            validator.check(body, {"$ref": "#/components/schemas/Latest statistics for a device"})
            write_json(os.path.join(base, "statistics.%s.json" % gw["id"]), body)
            stats_written.append(gw["id"])

        manifest["scenarios"][name] = {
            "note": note,
            "devices": len(devices),
            "sites": site_count,
            "wans": wan_count,
            "clients": client_count,
            "devicePages": len(device_pages),
            "clientPages": len(client_pages),
            "gatewayStatistics": stats_written,
            "schemaDeviation": deviation,
        }

    for case_id, expectation, note, responses, synthesize in pagination_cases():
        body = {
            "id": case_id,
            "collection": "devices",
            "limit": SMALL_LIMIT if (responses and responses[0]["limit"] == SMALL_LIMIT) else PAGE_LIMIT,
            "expect": expectation,
            "note": note,
            "responses": responses,
            "synthesize": synthesize,
        }
        # Only cases that must be ACCEPTED are schema-checked. A case whose whole
        # purpose is a broken envelope must not be "corrected" into validity by
        # its own generator.
        if expectation == "accept":
            for pg in responses:
                validator.check(pg, {"$ref": "#/components/schemas/Adopted device overview page"})
        write_json(os.path.join(out_dir, "pagination", "%s.json" % case_id), body)
        manifest["pagination"][case_id] = {
            "expect": expectation,
            "note": note,
            "responses": len(responses) if responses is not None else synthesize["pages"],
            "synthesized": synthesize is not None,
        }

    manifest["identity"] = {
        "namespace": str(FIXTURE_NS),
        "namespaceDerivedFrom": "uuid5(NAMESPACE_DNS, 'fixtures.omarchy-unifi.invalid')",
        "note": "RFC 2606 reserves .invalid permanently, so this namespace can "
                "never correspond to a real host. Every uuid below is "
                "uuid5(namespace, label) and is therefore reproducible from this "
                "file alone (SEC-011 / AC-059).",
        "labels": sorted(MINTED_LABELS),
    }
    write_json(os.path.join(out_dir, "manifest.json"), manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true",
                        help="regenerate into a temp dir and diff against --out")
    args = parser.parse_args(argv)

    if not args.check:
        if os.path.isdir(args.out):
            shutil.rmtree(args.out)
        manifest = generate(args.out)
        print("wrote %d scenario(s) and %d pagination case(s) to %s"
              % (len(manifest["scenarios"]), len(manifest["pagination"]), args.out))
        return 0

    tmp = tempfile.mkdtemp(prefix="unifi-fixtures-check.")
    try:
        generate(tmp)
        differences = []
        for root, _dirs, files in os.walk(tmp):
            for name in files:
                fresh = os.path.join(root, name)
                rel = os.path.relpath(fresh, tmp)
                committed = os.path.join(args.out, rel)
                if not os.path.exists(committed):
                    differences.append("missing from the corpus: %s" % rel)
                    continue
                with open(fresh, "rb") as a, open(committed, "rb") as b:
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
