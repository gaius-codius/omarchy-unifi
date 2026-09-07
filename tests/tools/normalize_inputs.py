"""The API-shaped inputs that must normalize to the accept corpus' `data`.

This is the producer side of risk R-F. `tests/fixtures/envelopes/accept/` holds
hand-authored envelopes carrying the DATA-006 shape; the model tests feed those
to `Health.js` as input, and `tests/test_unifi_status.py` asserts them as
`normalize.py`'s output. This module supplies the other end: the device lists,
site records and statistics bodies a controller would have returned.

It is deliberately NOT derived from the envelopes. Every count in the corpus is
a literal, and every count here has to be produced by classifying states and
reading `features` — so the test compares two independently authored things.
Building the device list out of the envelope's own counts would make the
assertion tautological, which is the failure mode this whole corpus exists to
prevent.

Only what `normalize.py` reads is populated. A device's `macAddress`,
`ipAddress`, `firmwareVersion` and the rest were part of the API schema and
irrelevant to the model, so they were omitted rather than invented — an omitted
field that mattered would show up as a mismatch.

**SPEC-v1.1-browse.md changed which of those matter.** `macAddress`,
`firmwareVersion` and `firmwareUpdatable` are now read by DATA-B01, so the
browse cases populate them through `browse_device()`. Every pre-v1.1 case still
uses `device()` and still omits them, which is deliberate: those inputs must
keep producing byte-identical envelopes, and that is a stronger statement than
"the new fields default to null".
"""


import os
import sys

# `browse_order` is REQ-B11's rule and lives in the module under test.
# Importing it rather than restating it is the point: an input authored
# against a COPY of the ordering would agree with a wrong copy.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "helper"))
from unifi import normalize  # noqa: E402

def uid(suffix):
    """The same synthetic UUID namespace gen_envelopes.py uses (SEC-011)."""
    return "00000000-0000-5000-9000-%012x" % suffix


def device(index, state, features, name=None, model=None, ip=None):
    body = {
        "id": uid(index),
        "name": name if name is not None else "Device %d" % index,
        "model": model if model is not None else "GENERIC",
        "state": state,
        "features": list(features),
    }
    # Only the DEV-6 case supplies one. Every other input omits `ipAddress`
    # entirely, which is the shape that must keep behaving exactly as it did
    # before the gateway rule was widened.
    if ip is not None:
        body["ipAddress"] = ip
    return body


def browse_device(index, state, features, name=None, model=None, ip=None,
                  mac=None, firmware="9.1.0", updatable=False):
    """A device record carrying the fields DATA-B01 reads.

    Separate from `device()` on purpose. Every pre-v1.1 input omits
    `ipAddress`, `macAddress` and the firmware pair entirely, and that shape has
    to keep producing exactly the envelope it produced before — which is a
    stronger statement than "the new fields default to null" and is the one the
    byte-for-byte corpus check makes.
    """
    body = device(index, state, features, name=name, model=model, ip=ip)
    if mac is not None:
        body["macAddress"] = mac
    body["firmwareVersion"] = firmware
    body["firmwareUpdatable"] = updatable
    return body


def detail(ports=0, radios=0, uplink=None,
           provisioned="2026-01-02T08:00:00Z"):
    """A route-6 body, as the controller returns it.

    Authored in the API's shape and not the model's, so `normalize` has to do
    the mapping the corpus asserts — the same rule the rest of this file
    follows. A helper that emitted the model shape would make the test compare
    the fixture against itself.
    """
    body = {"provisionedAt": provisioned, "interfaces": {}}
    if uplink is not None:
        body["uplink"] = {"deviceId": uplink}
    if ports:
        body["interfaces"]["ports"] = [
            {"idx": i + 1, "connector": "RJ45",
             "state": "UP" if i else "DOWN", "maxSpeedMbps": 1000,
             "poe": ({"enabled": bool(i), "standard": "802.3at",
                      "state": "GOOD" if i else "OFF", "type": 4}
                     if i < ports - 1 else None)}
            for i in range(ports)
        ]
    if radios:
        body["interfaces"]["radios"] = [
            {"frequencyGHz": 2.4 if i == 0 else 5.0, "txRetriesPct": 1.5 + i}
            for i in range(radios)
        ]
    return body


def client(index, name, kind, ip=None, mac=None, uplink=None,
           connected="2026-01-12T09:14:00Z", access="DEFAULT"):
    body = {"id": uid(index), "name": name, "type": kind}
    if access is not None:
        body["access"] = {"type": access}
    if ip is not None:
        body["ipAddress"] = ip
    if mac is not None:
        body["macAddress"] = mac
    if uplink is not None:
        body["uplinkDeviceId"] = uplink
    if connected is not None:
        body["connectedAt"] = connected
    return body


def stats(uptime=None, rx=None, tx=None, cpu=None, memory=None):
    body = {"interfaces": {"radios": []}}
    if uptime is not None:
        body["uptimeSec"] = uptime
    if cpu is not None:
        body["cpuUtilizationPct"] = cpu
    if memory is not None:
        body["memoryUtilizationPct"] = memory
    if rx is not None or tx is not None:
        body["uplink"] = {}
        if rx is not None:
            body["uplink"]["rxRateBps"] = rx
        if tx is not None:
            body["uplink"]["txRateBps"] = tx
    return body


GATEWAY = ["gateway"]
SWITCH = ["switching"]
ACCESS_POINT = ["accessPoint"]

UDM = ("UDM Pro", "UDM-Pro")
FULL_STATS = stats(864000, 12000000, 3000000)


def _healthy():
    return {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", GATEWAY, *UDM),
            device(20, "ONLINE", SWITCH),
            device(21, "ONLINE", SWITCH),
            device(30, "ONLINE", ACCESS_POINT),
            device(31, "ONLINE", ACCESS_POINT),
        ],
        "clients": 42,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }


def cases():
    """Return {envelope case id: normalize.build kwargs}."""
    out = {}

    # REQ-002 rule 5. Three cases share this data: the two `meta` variants carry
    # the same snapshot and differ only in the envelope around it.
    for name in ("success_healthy", "success_insecure_tls", "success_meta_all_null"):
        out[name] = _healthy()

    # BIZ-004: every optional collection failed. Same devices, but no client
    # count and no statistics — so the metrics are null and NOT zero, while the
    # device health the panel actually needs is unaffected.
    gaps = _healthy()
    gaps["clients"] = None
    gaps["statistics"] = {}
    out["success_optional_gaps"] = gaps

    # REQ-002 rule 4. The UPDATING access point is transitional and must stay
    # out of both the amber condition and the offline list (REQ-003).
    out["success_degraded"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", GATEWAY, *UDM),
            device(20, "ONLINE", SWITCH),
            device(21, "OFFLINE", SWITCH, "Garage Switch", "USW-Lite-8-PoE"),
            device(22, "ISOLATED", ACCESS_POINT, "Attic AP", "U6-Pro"),
            device(23, "UPDATING", ACCESS_POINT),
        ],
        "clients": 31,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }

    # REQ-002 rule 3. Every gateway down, and the gateway is itself the first
    # entry of the offline list.
    out["success_all_gateways_down"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "OFFLINE", GATEWAY, *UDM),
            device(21, "OFFLINE", SWITCH, "Garage Switch", "USW-Lite-8-PoE"),
            device(22, "OFFLINE", ACCESS_POINT, "Attic AP", "U6-Pro"),
        ],
        "clients": 0,
        "statistics": {},
        "applicationVersion": "9.1.0",
    }

    # UX-005. A complete, successful, empty result.
    out["success_empty_site"] = {
        "site": {"id": uid(1), "name": "New Site"},
        "devices": [],
        "clients": 0,
        "statistics": {},
        "applicationVersion": "9.1.0",
    }

    # No gateway at all: rule 3 can never fire and wan.status is unknown.
    out["success_no_gateway"] = {
        "site": {"id": uid(1), "name": "Switch Closet"},
        "devices": [
            device(20, "ONLINE", SWITCH),
            device(22, "OFFLINE", ACCESS_POINT, "Attic AP", "U6-Pro"),
        ],
        "clients": 7,
        "statistics": {},
        "applicationVersion": "9.1.0",
    }

    # DEV-6: the console advertises only `switching` and is identified as the
    # gateway by reporting a global address (TEST-NET-1 stands in for a real
    # WAN address). It then holds two roles, so the role rows total 5 over 4
    # unique devices.
    out["success_console_without_gateway_feature"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(31, "ONLINE", SWITCH, "Console", "UDM-Pro", ip="192.0.2.1"),
            device(32, "ONLINE", SWITCH),
            device(33, "ONLINE", ACCESS_POINT),
            device(34, "OFFLINE", ACCESS_POINT, "Attic AP", "U6-Pro"),
        ],
        "clients": 44,
        "statistics": {uid(31): stats(1103341, 29584, 25464)},
        "applicationVersion": "9.1.0",
    }

    # One gateway online and one isolated: degraded, not down. The UDM also
    # switches, so it is counted in two roles.
    out["success_mixed_gateways"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", ["gateway", "switching"], *UDM),
            device(11, "ISOLATED", GATEWAY, "UXG Backup", "UXG-Pro"),
            device(20, "ONLINE", SWITCH),
        ],
        "clients": 12,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }

    # Every non-online device is transitional: green, offlineTotal 0, and an
    # empty offline list.
    out["success_transitional_only"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", GATEWAY, *UDM),
            device(20, "ONLINE", SWITCH),
            device(21, "UPDATING", SWITCH),
            device(30, "ADOPTING", ACCESS_POINT),
        ],
        "clients": 19,
        "statistics": {uid(10): stats(3600, 9000000, 2000000)},
        "applicationVersion": "9.1.0",
    }

    # A state outside the ten-value enum. REQ-000 puts it in `unknown`, which
    # rule 4 matches but offlineTotal does not count.
    out["success_unknown_state"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", GATEWAY, *UDM),
            device(20, "ONLINE", SWITCH),
            device(23, "REBOOTING", SWITCH),
        ],
        "clients": 8,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }

    # AC-025 exactly: one device holding all three roles, plus two AP-only
    # devices. The role rows sum to 5 above a devicesTotal of 3.
    out["success_multi_feature_roles"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", ["gateway", "switching", "accessPoint"], *UDM),
            device(30, "ONLINE", ACCESS_POINT),
            device(31, "ONLINE", ACCESS_POINT),
        ],
        "clients": 18,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }

    # DATA-006b: a device with an EMPTY features array appears in no role object
    # at all, so byClass is the only place it is visible.
    out["success_featureless_device"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": [
            device(10, "ONLINE", GATEWAY, *UDM),
            device(20, "ONLINE", SWITCH),
            device(40, "ONLINE", []),
        ],
        "clients": 6,
        "statistics": {uid(10): FULL_STATS},
        "applicationVersion": "9.1.0",
    }

    # REQ-008a: five gateways, statistics for the first four in primary order.
    gateways = [device(10 + i, "ONLINE", ["gateway", "switching"],
                       "Gateway %d" % (i + 1), "UXG-Pro") for i in range(5)]
    out["success_five_gateways_truncated"] = {
        "site": {"id": uid(1), "name": "Campus"},
        "devices": gateways + [device(30, "ONLINE", ACCESS_POINT)],
        "clients": 20,
        "statistics": {uid(10 + i): stats(864000 + i * 3600, 12000000, 3000000)
                       for i in range(4)},
        "applicationVersion": "9.1.0",
    }

    # REQ-010 / AC-063: 500 devices down, a list bounded to ten, and an
    # offlineTotal that is not the list's length.
    bulk = [device(100 + i, "OFFLINE", SWITCH, "Bulk Switch %03d" % i,
                   "USW-Lite-8-PoE") for i in range(499)]
    out["success_500_down_bounded_list"] = {
        "site": {"id": uid(1), "name": "Campus"},
        "devices": [device(10, "OFFLINE", GATEWAY, *UDM)] + bulk,
        "clients": 0,
        "statistics": {},
        "applicationVersion": "9.1.0",
    }

    # --- SPEC-v1.1-browse.md ------------------------------------------------
    #
    # The devices are authored in a DELIBERATELY WRONG order — online first,
    # alphabetically — so that reproducing the fixture requires `browse_order`
    # to actually reorder them. Authoring them pre-sorted would let a build()
    # that ignored REQ-B11 entirely pass byte for byte.
    browse_devices = [
        browse_device(32, "ONLINE", ACCESS_POINT, "Attic AP", "U6-Pro",
                      ip="192.168.10.32", mac="02:00:00:00:00:20"),
        browse_device(34, "ONLINE", [], "Unknown Device", "UNKNOWN-DEV"),
        browse_device(30, "ONLINE", ["gateway", "switching"], "UDM Pro",
                      "UDM-Pro", ip="192.0.2.1", mac="02:00:00:00:00:1e"),
        browse_device(33, "ONLINE", ACCESS_POINT, "Shed AP", "U6-Lite",
                      ip="192.168.10.33", mac="02:00:00:00:00:21"),
        browse_device(31, "OFFLINE", SWITCH, "Garage Switch", "USW-Lite-8-PoE",
                      ip="192.168.10.31", mac="02:00:00:00:00:1f",
                      updatable=True),
    ]
    browse_details = {
        uid(30): detail(ports=10),
        uid(31): detail(ports=10, uplink=uid(30)),
        uid(32): detail(radios=2, uplink=uid(30)),
        # 33 and 34 are past REQ-B02's bound: no entry, so `detail` is null.
    }
    browse_stats = {
        uid(30): stats(864000, 12000000, 3000000, cpu=4.5, memory=38.0),
        uid(31): stats(cpu=None, memory=None),
        uid(32): stats(432000, 900000, 400000, cpu=11.0, memory=44.5),
    }
    browse_clients = [
        client(40, "workshop-pi", "WIRED", ip="192.168.20.40",
               mac="02:00:00:00:01:28", uplink=uid(31)),
        client(41, "phone", "WIRELESS", ip="192.168.20.41",
               mac="02:00:00:00:01:29", uplink=uid(32)),
        client(42, None, "WIRELESS", uplink=uid(32), connected=None),
        client(43, "road-laptop", "TELEPORT", ip="192.168.20.43", uplink=None,
               access="DEFAULT"),
    ]
    out["success_browse_full"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": browse_devices,
        "clients": 4,
        "statistics": browse_stats,
        "details": browse_details,
        "listedDevices": normalize.browse_order(browse_devices),
        "clientRecords": browse_clients,
        "applicationVersion": "9.1.0",
    }

    # Truncation. The COUNTS describe a 300-device site while the lists hold two
    # devices and one client: the arrays are a bounded selection and the counts
    # are not derived from them. A build() that computed `devicesTotal` from
    # `len(devices)` would report 2 here, which is the defect DATA-B04 exists to
    # make impossible.
    bulk_300 = ([browse_device(30, "ONLINE", ["gateway", "switching"], "UDM Pro",
                               "UDM-Pro", ip="192.0.2.1", mac="02:00:00:00:00:1e")]
                + [browse_device(200 + i, "ONLINE", SWITCH,
                                 "Bulk %03d" % i, "USW-Lite-8-PoE")
                   for i in range(200)]
                + [browse_device(500 + i, "ONLINE", ACCESS_POINT,
                                 "AP %03d" % i, "U6-Lite") for i in range(99)])
    ordered_300 = normalize.browse_order(bulk_300)
    out["success_browse_truncated"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": bulk_300,
        "clients": 900,
        "statistics": {uid(30): stats(864000, 12000000, 3000000)},
        # No `details` at all. REQ-B02b: on a site this size the byte budget is
        # spent before route 6 is reached, so every `detail` is null while
        # `metrics` — one cheap request for the gateway — survives. The two
        # bounds are independent and this is the case that shows it.
        "listedDevices": ordered_300[:2],
        "clientRecords": browse_clients[:1],
        "applicationVersion": "9.1.0",
    }

    # The boundary: the budget admitted nothing at all.
    out["success_browse_empty_lists"] = {
        "site": {"id": uid(1), "name": "Home"},
        "devices": bulk_300,
        "clients": 900,
        "statistics": {uid(30): stats(864000, 12000000, 3000000)},
        "applicationVersion": "9.1.0",
    }
    return out
