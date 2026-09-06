#!/usr/bin/env python3
"""A helper double for the quickshell harness. Behaves as a scenario file says.

`Service.qml` builds its argv from `manifest.__sourceDir`, so the harness
points that at a temp directory whose `helper/unifi_status.py` is this file. The
service therefore launches it exactly as it would launch the real helper —
same flags, same `--nonce`, same argv vector — and nothing in the service knows
the difference. That is the point: the launch path is under test too.

The behaviour comes from `scenario.json` beside this file rather than from the
environment or from argv, because the service controls both of those and the
harness must be able to change the stub's behaviour BETWEEN batches without
touching the service.

Nothing here is shipped. It lives under tests/ and is copied into a temp
directory at run time.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIO = os.path.join(HERE, "scenario.json")

ENVELOPE_KEYS = ("protocolVersion", "ok", "nonce", "attemptedAt", "observedAt",
                 "meta", "data", "warnings", "error")


def utc_now(offset=0.0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


def scenario():
    try:
        with open(SCENARIO, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"mode": "success"}


def nonce_from(argv):
    for index, token in enumerate(argv):
        if token == "--nonce" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--nonce="):
            return token.split("=", 1)[1]
    return ""


def meta():
    return {
        "commitGeneration": 1,
        "apiRootHost": "127.0.0.1",
        "siteId": "00000000-0000-5000-9000-000000000001",
        "allowInsecureTls": False,
        "customCaInUse": False,
        "helperVersion": "0.1.0",
    }


def success_data():
    """The `healthy` snapshot, in DATA-006 shape. Green by REQ-002 rule 5."""
    zero = {"online": 0, "transitional": 0, "down": 0, "impaired": 0, "unknown": 0}

    def cls(**kwargs):
        out = dict(zero)
        out.update(kwargs)
        return out

    return {
        "site": {"id": "00000000-0000-5000-9000-000000000001", "name": "Home"},
        "wan": {"status": "up", "uptimeSec": 864000,
                "downloadBps": 12000000, "uploadBps": 3000000},
        "gateways": [{
            "id": "00000000-0000-5000-9000-00000000000a", "name": "UDM Pro",
            "model": "UDM-Pro", "state": "ONLINE", "class": "online",
            "uptimeSec": 864000, "downloadBps": 12000000, "uploadBps": 3000000,
        }],
        "counts": {
            "clients": 42, "devicesTotal": 5, "offlineTotal": 0,
            "byClass": cls(online=5), "gateways": cls(online=1),
            "switches": cls(online=2), "accessPoints": cls(online=2),
        },
        "offlineDevices": [],
        "applicationVersion": "9.1.0",
    }


def degraded_data():
    """A snapshot with something in every list the panel can draw.

    The `healthy` snapshot above has no offline devices and no warnings, so
    every view case written against it exercised `DeviceList` and `WarningList`
    by rendering nothing — and a guard of the form `if (list.length > 0)` then
    passes for the wrong reason. This is the fixture that makes those two files
    actually run.

    The numbers satisfy DATA-006b in both directions, because the service
    rejects a success envelope that does not (DATA-008):

        sum(byClass) == devicesTotal            5 + 6 + 1      == 12
        byClass.down + byClass.impaired == offlineTotal  6 + 1 == 7

    and the ROLE rows deliberately sum to 13, not 12, because one device holds
    two roles. That is REQ-009's double count, and it is what makes AC-025's
    not-a-partition note appear.
    """
    zero = {"online": 0, "transitional": 0, "down": 0, "impaired": 0, "unknown": 0}

    def cls(**kwargs):
        out = dict(zero)
        out.update(kwargs)
        return out

    return {
        "site": {"id": "00000000-0000-5000-9000-000000000001", "name": "Home"},
        # REQ-008a: some but not all gateways down -> degraded, and the metrics
        # are the PRIMARY gateway's, which is the first online one by id.
        "wan": {"status": "degraded", "uptimeSec": 864000,
                "downloadBps": 12000000, "uploadBps": 3000000},
        "gateways": [
            {"id": "00000000-0000-5000-9000-00000000000a", "name": "UDM Pro",
             "model": "UDM-Pro", "state": "ONLINE", "class": "online",
             "uptimeSec": 864000, "downloadBps": 12000000, "uploadBps": 3000000},
            # Past the statistics bound in spirit: no metrics at all, which the
            # panel must render as "statistics not fetched" and not as three
            # separate unknowns.
            {"id": "00000000-0000-5000-9000-00000000000b", "name": "USG Backup",
             "model": "USG-3P", "state": "OFFLINE", "class": "down",
             "uptimeSec": None, "downloadBps": None, "uploadBps": None},
        ],
        "counts": {
            "clients": 42, "devicesTotal": 12, "offlineTotal": 7,
            "byClass": cls(online=5, down=6, impaired=1),
            "gateways": cls(online=1, down=1),
            "switches": cls(online=2, down=3),
            "accessPoints": cls(online=3, down=2, impaired=1),
        },
        # Two listed against a total of seven: REQ-010's "and 5 more" comes from
        # `offlineTotal`, never from this array's length.
        "offlineDevices": [
            {"id": "00000000-0000-5000-9000-00000000001a", "name": "Garage AP",
             "model": "U6-Lite", "state": "OFFLINE", "class": "down"},
            {"id": "00000000-0000-5000-9000-00000000001b", "name": "Loft Switch",
             "model": "USW-Flex", "state": "ISOLATED", "class": "impaired"},
        ],
        "applicationVersion": "9.1.0",
    }


DEGRADED_WARNINGS = [
    {"code": "offline_list_truncated",
     "message": "The offline device list is truncated; see the total.",
     "detail": {"listed": 2, "total": 7}},
    {"code": "insecure_tls",
     "message": "TLS verification is disabled for this controller.",
     "detail": None},
]


def envelope(nonce, ok=True, error=None, warnings=None, attempted=None,
             variant="healthy", insecure=False):
    attempted_at = attempted if attempted is not None else utc_now()
    body_meta = meta()
    if insecure:
        body_meta["allowInsecureTls"] = True
    if ok:
        body = degraded_data() if variant == "degraded" else success_data()
    else:
        body = None
    return {
        "protocolVersion": 1,
        "ok": ok,
        "nonce": nonce,
        "attemptedAt": attempted_at,
        "observedAt": utc_now() if ok else None,
        "meta": body_meta,
        "data": body,
        "warnings": warnings or [],
        "error": error,
    }


def emit(body):
    sys.stdout.write(json.dumps(body, sort_keys=True, separators=(",", ":")))


def advance(plan):
    """Rewrite scenario.json to `then`, if the plan carries one.

    A scenario that changes AFTER the first invocation cannot be expressed by a
    timer in the runner: DATA-008a's re-baseline retry is scheduled with
    `Qt.callLater`, so it launches a few milliseconds after the batch it
    retries, and no timer the harness can set is reliably shorter than that.
    Letting the stub advance its own scenario makes "the RTC was wrong on the
    first batch after a resume, and right on the second" exact rather than a
    race — and a race the runner would win most of the time is worse than one
    it loses, because it passes for the wrong reason.
    """
    nxt = plan.get("then")
    if not isinstance(nxt, dict):
        return
    try:
        with open(SCENARIO, "w", encoding="utf-8") as handle:
            json.dump(nxt, handle)
    except OSError:
        pass


def main(argv):
    plan = scenario()
    mode = plan.get("mode", "success")
    nonce = nonce_from(argv)
    advance(plan)

    delay = float(plan.get("delaySec", 0))
    if delay > 0:
        time.sleep(delay)

    if mode == "hang":
        # Outlives the 30 s watchdog. REQ-017a says the service marks the batch
        # abandoned and publishes `timeout` WITHOUT waiting for this to exit,
        # so the process is still alive when the assertion runs.
        time.sleep(float(plan.get("hangSec", 90)))
        return 0

    if mode == "malformed":
        sys.stdout.write("this is not an envelope")
        return 1

    if mode == "trailing":
        emit(envelope(nonce))
        sys.stdout.write("\ntrailing noise")
        return 0

    if mode == "bad-nonce":
        # DATA-005a. The service must discard this even though it is otherwise
        # a perfectly good success envelope.
        emit(envelope(nonce + "-wrong"))
        return 0

    if mode == "stderr-flood":
        sys.stderr.write("x" * int(plan.get("stderrBytes", 64 * 1024)))
        emit(envelope(nonce))
        return 0

    if mode == "failure":
        kind = plan.get("kind", "network")
        error = {"kind": kind, "message": plan.get("message", "stub failure"),
                 "retryable": bool(plan.get("retryable", True))}
        if plan.get("httpStatus") is not None:
            error["httpStatus"] = plan["httpStatus"]
        emit(envelope(nonce, ok=False, error=error))
        return 1

    if mode == "skew":
        # DATA-008a: `attemptedAt` far before the recorded launch time, which is
        # the signature of a backwards wall-clock correction rather than a bad
        # helper.
        emit(envelope(nonce, attempted=utc_now(-float(plan.get("skewSec", 3600)))))
        return 0

    if mode == "wrong-exit":
        # `ok: true` paired with a non-zero exit. Exit status is part of the
        # protocol and this must be rejected.
        emit(envelope(nonce))
        return 3

    # The default: a success envelope. `variant: "degraded"` swaps in the
    # snapshot that actually populates the offline list, the warning list and
    # the second gateway row; `insecureTls` sets the meta flag UX-009 reads.
    variant = plan.get("variant", "healthy")
    warnings = plan.get("warnings")
    if warnings is None and variant == "degraded":
        warnings = DEGRADED_WARNINGS
    emit(envelope(nonce, warnings=warnings, variant=variant,
                  insecure=bool(plan.get("insecureTls", False))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
