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


def envelope(nonce, ok=True, error=None, warnings=None, attempted=None):
    attempted_at = attempted if attempted is not None else utc_now()
    return {
        "protocolVersion": 1,
        "ok": ok,
        "nonce": nonce,
        "attemptedAt": attempted_at,
        "observedAt": utc_now() if ok else None,
        "meta": meta(),
        "data": success_data() if ok else None,
        "warnings": warnings or [],
        "error": error,
    }


def emit(body):
    sys.stdout.write(json.dumps(body, sort_keys=True, separators=(",", ":")))


def main(argv):
    plan = scenario()
    mode = plan.get("mode", "success")
    nonce = nonce_from(argv)

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

    emit(envelope(nonce, warnings=plan.get("warnings")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
