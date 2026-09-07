"""DATA-006: the API's shapes in, the plugin's model out.

This is the producer half of risk R-F. The same accept corpus that
`tests/model/*.test.js` feeds to `Health.js` as INPUT is asserted here as this
module's OUTPUT, byte for byte. Without that, Phase 2 and Phase 7 each invent a
shape and DEV-1 — where `counts` could not answer the health question that read
it — happens again in the other direction.

Three rules that are easy to get subtly wrong:

**Missing is `null`, never `0`.** BIZ-003. A gateway whose statistics call
failed has `uptimeSec: null`, not `uptimeSec: 0`, because zero is a value the
controller can actually report and "unknown" is not.

**`byClass` is a partition; the role objects are not.** A Dream Machine reports
`gateway`, `switching` and `accessPoint` at once and is counted in all three
(REQ-009), so the role objects deliberately do not sum to `devicesTotal`.
`byClass` counts every device exactly once, including one whose `features` array
is empty — which is the only thing that can answer REQ-002 rule 4.

**`offlineTotal` is independent of `offlineDevices`.** The list is bounded to
ten (REQ-010); the total is not. AC-063's "and 490 more" is computed from the
total, and computing it from the array length would understate a 500-device
outage by 98%.
"""

import ipaddress

from . import errors
from . import sanitize

CLASS_ONLINE = "online"
CLASS_TRANSITIONAL = "transitional"
CLASS_DOWN = "down"
CLASS_IMPAIRED = "impaired"
CLASS_UNKNOWN = "unknown"

# REQ-000's five classes, in the order every other module uses them.
CLASSES = (CLASS_ONLINE, CLASS_TRANSITIONAL, CLASS_DOWN, CLASS_IMPAIRED,
           CLASS_UNKNOWN)

# The ten documented API states. Anything else lands in `unknown` and raises a
# warning naming the value — never silently in another class, and in particular
# never silently in `online`.
STATE_CLASS = {
    "ONLINE": CLASS_ONLINE,
    "PENDING_ADOPTION": CLASS_TRANSITIONAL,
    "UPDATING": CLASS_TRANSITIONAL,
    "GETTING_READY": CLASS_TRANSITIONAL,
    "ADOPTING": CLASS_TRANSITIONAL,
    "DELETING": CLASS_TRANSITIONAL,
    "OFFLINE": CLASS_DOWN,
    "CONNECTION_INTERRUPTED": CLASS_DOWN,
    "ISOLATED": CLASS_IMPAIRED,
    "U5G_INCORRECT_TOPOLOGY": CLASS_IMPAIRED,
}

FEATURE_GATEWAY = "gateway"
FEATURE_SWITCHING = "switching"
FEATURE_ACCESS_POINT = "accessPoint"

# REQ-008a's domain.
WAN_UP = "up"
WAN_DOWN = "down"
WAN_DEGRADED = "degraded"
WAN_UNKNOWN = "unknown"

# REQ-010. The list is bounded; `counts.offlineTotal` is not.
OFFLINE_LIST_MAX = 10

# REQ-008a. A bound on route 4's contribution to the REQ-017 time budget.
GATEWAY_STATISTICS_MAX = 4

# SPEC-v1.1-browse.md. Mirrored in `bounds.py`, `Protocol.js` and
# `docs/protocol-v1.md`; `consistency.test.js` is what keeps the four in step.
# Truncating here rather than rejecting: a 96-port chassis nobody has shipped
# yet should cost the user the ports past 64, not the whole reading.
PORTS_PER_DEVICE_MAX = 64
RADIOS_PER_DEVICE_MAX = 8

# Classes that put a device on the offline list. `transitional` is excluded by
# REQ-003: a device that is updating is not a device that is down.
OFFLINE_CLASSES = (CLASS_DOWN, CLASS_IMPAIRED)


def classify_state(state):
    """REQ-000. Returns a class name; anything unrecognised is `unknown`."""
    if not isinstance(state, str):
        return CLASS_UNKNOWN
    return STATE_CLASS.get(state, CLASS_UNKNOWN)


def is_known_state(state):
    return isinstance(state, str) and state in STATE_CLASS


def features_of(device):
    value = device.get("features")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


# --- DEV-6 -----------------------------------------------------------------
# REQ-000 defines a gateway as a device whose `features` contains `gateway`,
# which is what the published specification declares. Observed against a real
# controller (UniFi Network 10.6.101, api-contract.md §12a), NO device reports
# it: the UDM Pro that routes the site comes back as `features: ["switching"]`.
#
# Taken literally, that leaves a site with a working gateway and two WANs
# reporting `wan.status = "unknown"`, no uptime, no throughput, an empty gateway
# list, and REQ-002 rule 3 — the only route to `red` — permanently unreachable.
#
# The gateway is still identifiable. It is the one device reporting an address
# that is not on the site's LAN, because it reports its WAN address while
# everything else reports an RFC 1918 one. Approved by the user 2026-09-06.
#
# The predicate is written out rather than delegated to `ipaddress.is_global`,
# which was the first attempt and is WRONG here in a way worth recording: it
# treats carrier-grade NAT space (100.64.0.0/10, RFC 6598) as private, so a
# console behind CGNAT — an increasingly ordinary situation — would report a
# perfectly real WAN address and not be recognised. What matters is "not on this
# LAN", not "globally routable".
#
# The heuristic's failure mode is deliberately the safe one: a console whose WAN
# address IS RFC 1918 (double NAT) matches nothing, and the result is exactly
# the pre-DEV-6 behaviour rather than a wrong answer.
_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),        # IPv6 unique local
)


def reports_an_offlan_address(device):
    value = device.get("ipAddress")
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        # A controller is free to put anything here. An unparseable address is
        # not evidence of anything, least of all of being a gateway.
        return False
    # None of these is a device's real address, and none is evidence of a WAN.
    if (address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_unspecified):
        return False
    return not any(address in network for network in _LAN_NETWORKS
                   if network.version == address.version)


def roles_of(device):
    """The roles a device holds: its `features`, plus DEV-6's inferred gateway.

    ONE function, because `is_gateway` and the per-role counts must agree. An
    earlier shape had `is_gateway` widened and the counter still reading
    `features`, so the device appeared in `wan` and in the gateway list while
    `counts.gateways` stayed at zero — a panel disagreeing with itself.
    """
    roles = set(features_of(device))
    if reports_an_offlan_address(device):
        roles.add(FEATURE_GATEWAY)
    return roles


def is_gateway(device):
    return FEATURE_GATEWAY in roles_of(device)


def gateway_order(devices):
    """The gateways, in REQ-008a's order: online first, then the rest, by id.

    One ordering serves two purposes, which is why it is a named function.
    `collect.py` takes the first four to fetch statistics for; this module takes
    the first as the primary gateway whose metrics become `wan`'s. Deriving them
    from the same order is what guarantees the primary is always among the four
    that were fetched — with two orderings, the primary could be the one gateway
    whose statistics nobody asked for.
    """
    gateways = [device for device in devices if is_gateway(device)]
    gateways.sort(key=lambda device: (
        0 if classify_state(device.get("state")) == CLASS_ONLINE else 1,
        _identifier(device)))
    return gateways


def _identifier(device):
    value = device.get("id")
    return value if isinstance(value, str) else ""


def _zero_counts():
    return {name: 0 for name in CLASSES}


def build(site, devices, clients, statistics, application_version,
          warnings=None, details=None, listed_devices=None,
          client_records=None):
    """Produce the DATA-006 `data` object, extended by DATA-B01/B02.

    `statistics` maps a device id to that device's `statistics/latest` body, and
    holds an entry only for devices whose call SUCCEEDED. A device absent from
    it gets null metrics — which is BIZ-003, and is why the argument is a map
    rather than a list positionally aligned with anything. `details` is the same
    arrangement for route 6.

    `clients` is the COUNT and `client_records` is the list. They are separate
    arguments because they come from different places and can legitimately
    disagree: the count is `totalCount` off the terminal page and survives a
    truncated list, and BIZ-002 forbids ever reporting the count as the length
    of something that was bounded.
    """
    by_class = _zero_counts()
    roles = {
        FEATURE_GATEWAY: _zero_counts(),
        FEATURE_SWITCHING: _zero_counts(),
        FEATURE_ACCESS_POINT: _zero_counts(),
    }
    offline = []

    for device in devices:
        state = device.get("state")
        klass = classify_state(state)
        if not is_known_state(state) and warnings is not None:
            warnings.add("unknown_device_state",
                         {"state": sanitize.clean(state),
                          "deviceId": sanitize.clean(_identifier(device))},
                         message="Device reported an unrecognised state: %s."
                                 % sanitize.clean(state))
        by_class[klass] += 1
        for feature in roles_of(device):
            if feature in roles:
                roles[feature][klass] += 1
        if klass in OFFLINE_CLASSES:
            offline.append(_offline_entry(device, klass))

    devices_total = len(devices)
    offline_total = by_class[CLASS_DOWN] + by_class[CLASS_IMPAIRED]

    # Ascending id. SPEC leaves the order of this list open; a deterministic one
    # is required because the list is TRUNCATED, and an unstable order would
    # change which devices a user sees between two identical polls.
    offline.sort(key=lambda entry: entry["id"])
    if len(offline) > OFFLINE_LIST_MAX and warnings is not None:
        warnings.add("offline_list_truncated",
                     {"listed": OFFLINE_LIST_MAX, "total": offline_total},
                     message="Showing %d of %d offline devices."
                             % (OFFLINE_LIST_MAX, offline_total))
    offline = offline[:OFFLINE_LIST_MAX]

    ordered_gateways = gateway_order(devices)
    gateways = [_gateway_entry(device, statistics) for device in ordered_gateways]
    gateways.sort(key=lambda entry: entry["id"])

    # REQ-B01's list is a bounded SELECTION, and the bound is a byte budget only
    # `collect.py` can evaluate — so this function maps whatever it is handed
    # and chooses nothing. Handed nothing, it emits an empty array, which
    # DATA-B04 defines as the fully-truncated reading rather than as "no
    # devices": `counts.devicesTotal` is what says how many there are.
    #
    # The ORDER is still decided here, by `browse_order`, because REQ-B11 is a
    # rule about the model and not about the budget.
    browse = [_device_entry(device, statistics, details or {})
              for device in (listed_devices or [])]

    data = {
        "site": {"id": sanitize.clean(site.get("id")),
                 "name": sanitize.clean(site.get("name"))},
        "wan": _wan(ordered_gateways, statistics),
        "gateways": gateways,
        "counts": {
            "clients": clients,
            "devicesTotal": devices_total,
            "offlineTotal": offline_total,
            "byClass": by_class,
            "gateways": roles[FEATURE_GATEWAY],
            "switches": roles[FEATURE_SWITCHING],
            "accessPoints": roles[FEATURE_ACCESS_POINT],
        },
        "offlineDevices": offline,
        "devices": browse,
        "clients": [_client_entry(record) for record in (client_records or [])],
        "applicationVersion": sanitize.clean(application_version),
    }
    _check_invariants(data)
    return data


# REQ-B11. `unknown` sorts above `transitional` on purpose: a device in a state
# this build does not recognise is a thing to look at, and an UPDATING one is
# not. This is the same judgement REQ-002 rule 4 makes, which reads `unknown`
# and does not read `transitional`.
_BROWSE_CLASS_RANK = {
    CLASS_DOWN: 0,
    CLASS_IMPAIRED: 1,
    CLASS_UNKNOWN: 2,
    CLASS_TRANSITIONAL: 3,
    CLASS_ONLINE: 4,
}


def browse_order(devices):
    """REQ-B11's total order. Public, because `collect.py` selects from it.

    Applying the order here and the BOUND in the collector is what makes
    REQ-B02a work: the detail budget is spent on the head of this list, so the
    devices that survive a squeeze are the broken ones rather than the
    alphabetically early ones.
    """
    return sorted(devices, key=_browse_order)


def _browse_order(device):
    klass = classify_state(device.get("state"))
    name = device.get("name")
    return (
        _BROWSE_CLASS_RANK.get(klass, len(_BROWSE_CLASS_RANK)),
        0 if is_gateway(device) else 1,
        (name or "").lower(),
        _identifier(device) or "",
    )


def _device_entry(device, statistics, details):
    """DATA-B01. One adopted device as the browser reads it.

    `class` and `roles` are taken from the same functions the counters use, so a
    device cannot be `down` in the list and `online` in `byClass`. That is not a
    nicety: the two are rendered a few hundred pixels apart.
    """
    identifier = _identifier(device)
    detail = details.get(identifier)
    metrics = statistics.get(identifier)
    roles = [feature for feature in (FEATURE_GATEWAY, FEATURE_SWITCHING,
                                     FEATURE_ACCESS_POINT)
             if feature in roles_of(device)]
    return {
        "id": sanitize.clean(identifier),
        "name": sanitize.clean(device.get("name")),
        "model": sanitize.clean(device.get("model")),
        "state": sanitize.clean(device.get("state")),
        "class": classify_state(device.get("state")),
        "roles": roles,
        "ipAddress": sanitize.clean(device.get("ipAddress")),
        "macAddress": sanitize.clean(device.get("macAddress")),
        "firmwareVersion": sanitize.clean(device.get("firmwareVersion")),
        "firmwareUpdatable": _as_bool(device.get("firmwareUpdatable")),
        "uplinkDeviceId": _uplink_id(detail),
        # Both null when not fetched. The KEY is always present: absent and null
        # are different statements, and only one of them is acceptable.
        "detail": _detail_entry(detail),
        "metrics": _device_metrics(metrics),
    }


def _uplink_id(detail):
    if not isinstance(detail, dict):
        return None
    uplink = detail.get("uplink")
    if not isinstance(uplink, dict):
        return None
    return sanitize.clean(uplink.get("deviceId"))


def _detail_entry(detail):
    if not isinstance(detail, dict):
        return None
    interfaces = detail.get("interfaces")
    interfaces = interfaces if isinstance(interfaces, dict) else {}
    ports = interfaces.get("ports")
    radios = interfaces.get("radios")
    return {
        "provisionedAt": sanitize.clean(detail.get("provisionedAt")),
        "ports": [_port_entry(port) for port in (ports or [])
                  if isinstance(port, dict)][:PORTS_PER_DEVICE_MAX],
        "radios": [_radio_entry(radio) for radio in (radios or [])
                   if isinstance(radio, dict)][:RADIOS_PER_DEVICE_MAX],
    }


def _port_entry(port):
    poe = port.get("poe")
    return {
        "idx": _count(port.get("idx")),
        "connector": sanitize.clean(port.get("connector")),
        "state": sanitize.clean(port.get("state")),
        "maxSpeedMbps": _count(port.get("maxSpeedMbps")),
        "poe": ({"enabled": _as_bool(poe.get("enabled")),
                 "standard": sanitize.clean(poe.get("standard")),
                 "state": sanitize.clean(poe.get("state"))}
                if isinstance(poe, dict) else None),
    }


def _radio_entry(radio):
    return {
        "frequencyGHz": _number(radio.get("frequencyGHz")),
        "txRetriesPct": _number(radio.get("txRetriesPct")),
    }


def _device_metrics(body):
    if not isinstance(body, dict):
        return None
    uplink = body.get("uplink")
    uplink = uplink if isinstance(uplink, dict) else {}
    return {
        "uptimeSec": _count(body.get("uptimeSec")),
        "cpuUtilizationPct": _number(body.get("cpuUtilizationPct")),
        "memoryUtilizationPct": _number(body.get("memoryUtilizationPct")),
        "downloadBps": _count(uplink.get("rxRateBps")),
        "uploadBps": _count(uplink.get("txRateBps")),
    }


def _client_entry(record):
    """DATA-B02. Eight fields, because eight is all the API has (api-contract §12b).

    Every one of these is personal data (REQ-B20). They reach the rendered panel
    and nothing else: no warning, no error, no log line carries a client field,
    which is why nothing in this function calls `warnings.add`.
    """
    access = record.get("access")
    return {
        "id": sanitize.clean(_identifier(record)),
        "name": sanitize.clean(record.get("name")),
        "type": sanitize.clean(record.get("type")),
        "accessType": (sanitize.clean(access.get("type"))
                       if isinstance(access, dict) else None),
        "ipAddress": sanitize.clean(record.get("ipAddress")),
        "macAddress": sanitize.clean(record.get("macAddress")),
        "uplinkDeviceId": sanitize.clean(record.get("uplinkDeviceId")),
        "connectedAt": sanitize.clean(record.get("connectedAt")),
    }


def _as_bool(value):
    return value if isinstance(value, bool) else None


def _number(value):
    """A float or int as-is; anything else null.

    Deliberately NOT `_count`: utilisation percentages and radio frequencies are
    fractional, and coercing 4.5 to 4 would be a silent lie about a number the
    panel prints.
    """
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _offline_entry(device, klass):
    return {
        "id": sanitize.clean(_identifier(device)),
        "name": sanitize.clean(device.get("name")),
        "model": sanitize.clean(device.get("model")),
        "state": sanitize.clean(device.get("state")),
        "class": klass,
    }


def _gateway_entry(device, statistics):
    metrics = _metrics(statistics.get(_identifier(device)))
    return {
        "id": sanitize.clean(_identifier(device)),
        "name": sanitize.clean(device.get("name")),
        "model": sanitize.clean(device.get("model")),
        "state": sanitize.clean(device.get("state")),
        "class": classify_state(device.get("state")),
        "uptimeSec": metrics["uptimeSec"],
        "downloadBps": metrics["downloadBps"],
        "uploadBps": metrics["uploadBps"],
    }


def _metrics(body):
    """Pull the three optional metrics out of a `statistics/latest` body.

    Every one is optional in the API (api-contract.md), so every one is `null`
    when absent. `uplink` is an object that may itself be missing.
    """
    empty = {"uptimeSec": None, "downloadBps": None, "uploadBps": None}
    if not isinstance(body, dict):
        return empty
    uplink = body.get("uplink")
    uplink = uplink if isinstance(uplink, dict) else {}
    return {
        "uptimeSec": _count(body.get("uptimeSec")),
        "downloadBps": _count(uplink.get("rxRateBps")),
        "uploadBps": _count(uplink.get("txRateBps")),
    }


def _count(value):
    """A non-negative integer, or None. Never a float, never a bool.

    `True` is an `int` in Python, and a controller sending `true` for a byte
    rate would otherwise be normalized to 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


# The four keys below are the whole of `wan`. The two metrics DATA-006 calls
# out as absent — the round-trip figure and the loss percentage — are not
# present-and-null here, they are not present at all. No supported API version
# can populate them: /v1/sites/{id}/wans returns {id, name} and nothing else
# (api-contract.md limitations 1 and 2). A present-and-null field would be an
# invitation to fill it in.
#
# tests/lint/no_latency_metric.sh enforces this, and it flagged the paragraph
# above when it was a docstring — a docstring is a string literal, not a
# comment, so the gate reads it as code. Same resolution as N-25 and N-31:
# every word kept, moved to where the gate agrees it is prose.
def _wan(ordered_gateways, statistics):
    """REQ-008a: aggregate status, primary gateway's metrics. See above."""
    if not ordered_gateways:
        return {"status": WAN_UNKNOWN, "uptimeSec": None,
                "downloadBps": None, "uploadBps": None}

    classes = [classify_state(device.get("state")) for device in ordered_gateways]
    if all(klass == CLASS_DOWN for klass in classes):
        status = WAN_DOWN
    elif any(klass in (CLASS_DOWN, CLASS_IMPAIRED, CLASS_UNKNOWN)
             for klass in classes):
        status = WAN_DEGRADED
    else:
        status = WAN_UP

    primary = _metrics(statistics.get(_identifier(ordered_gateways[0])))
    return {
        "status": status,
        "uptimeSec": primary["uptimeSec"],
        "downloadBps": primary["downloadBps"],
        "uploadBps": primary["uploadBps"],
    }


def _check_invariants(data):
    """DATA-006b, enforced by the producer as well as the consumer.

    The service rejects a success envelope that violates either of these as
    `malformed_response`. Checking here means the helper fails with `internal`
    — which names the process that has the bug — instead of shipping an
    envelope that gets rejected for a reason that points at the wire.
    """
    counts = data["counts"]
    by_class = counts["byClass"]
    if sum(by_class.values()) != counts["devicesTotal"]:
        raise errors.InternalError(
            "The device class partition does not sum to the device total.")
    if by_class[CLASS_DOWN] + by_class[CLASS_IMPAIRED] != counts["offlineTotal"]:
        raise errors.InternalError(
            "The offline total does not match the down and impaired classes.")
