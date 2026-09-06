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


def is_gateway(device):
    return FEATURE_GATEWAY in features_of(device)


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
          warnings=None):
    """Produce the DATA-006 `data` object.

    `statistics` maps a device id to that device's `statistics/latest` body, and
    holds an entry only for gateways whose call SUCCEEDED. A gateway absent from
    it gets null metrics — which is BIZ-003, and is why the argument is a map
    rather than a list positionally aligned with anything.
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
        for feature in features_of(device):
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
        "applicationVersion": sanitize.clean(application_version),
    }
    _check_invariants(data)
    return data


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
