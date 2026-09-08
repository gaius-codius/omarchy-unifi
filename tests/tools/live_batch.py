#!/usr/bin/env python3
"""AC-B20 — measure a full batch against the user's REAL controller.

This is the only tool in the tree that sends a packet off the machine, so it is
behind G-CONTROLLER (SPEC.md §15) and refuses to run without
`OMARCHY_UNIFI_CONTROLLER_APPROVED=1`. It is read-only: it spawns the helper
exactly as `Service.qml` does, and the helper only ever issues the six
allowlisted GETs.

    OMARCHY_UNIFI_CONTROLLER_APPROVED=1 python3 -B -E -s tests/tools/live_batch.py

What AC-B20 asks for is three things: that a full batch completes inside the
REQ-017 budget, that detail was fetched for every device, and that the wall time
is recorded. The first two are assertions; the third is why this is a committed
script and not a shell one-liner — a number quoted in a commit message cannot be
re-measured, and R-B1 says the detail bound is the whole performance story, so
the number is the point.

**Nothing here prints personal data.** REQ-B20 makes client names, IP addresses
and MAC addresses renderable in the panel and nowhere else, and a terminal is
nowhere else. The report is built from counts and warning codes only, and then
`_assert_no_personal_data` re-reads the finished report looking for every name,
address and MAC the envelope actually carried. That check is the control: this
file is meant to grow more diagnostics over time, and the next person to add one
should have it fail rather than have it leak.
"""

import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(REPO, "helper", "unifi_status.py")

APPROVAL_ENV = "OMARCHY_UNIFI_CONTROLLER_APPROVED"

# REQ-017. The helper enforces this on itself; measuring it from outside is the
# point of the exercise, because the helper's own clock stops at its last
# operation and the service's watchdog is what the user actually waits on.
BUDGET_SEC = 25.0

# Three, not one. A single sample cannot distinguish "fast" from "lucky", and
# R-B1's worry is per-request latency, which varies. Three read-only batches is
# nine to eighty-odd GETs against the user's own console — the same traffic one
# poll cycle produces every thirty seconds in normal use.
RUNS = 3


def approved():
    return os.environ.get(APPROVAL_ENV) == "1"


def run_batch(index):
    """One batch. Returns `(wall_sec, exit_code, envelope_or_None, stderr)`."""
    nonce = "acb20-%d-%s" % (index, os.urandom(6).hex())
    argv = [sys.executable, "-B", "-E", "-s", HELPER, "--nonce", nonce]
    started = time.monotonic()
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    wall = time.monotonic() - started
    try:
        envelope = json.loads(proc.stdout.decode("utf-8"))
    except Exception:
        envelope = None
    return wall, proc.returncode, envelope, proc.stderr.decode("utf-8", "replace")


def personal_strings(envelope):
    """Every string in the envelope that REQ-B20 calls personal data.

    Device names and addresses are included alongside the client ones. REQ-B20
    names clients, but a device is named by a person too — "kids-room-ap" is a
    fact about a household — and there is no reason for either to reach a
    terminal.
    """
    found = set()
    data = (envelope or {}).get("data") or {}
    lists = (data.get("devices") or []) + (data.get("clients") or [])
    lists = lists + (data.get("gateways") or []) + (data.get("offlineDevices") or [])
    for entry in lists:
        if not isinstance(entry, dict):
            continue
        for key in ("name", "ipAddress", "macAddress"):
            value = entry.get(key)
            if isinstance(value, str) and value != "":
                found.add(value)
    site = data.get("site")
    if isinstance(site, dict) and isinstance(site.get("name"), str):
        found.add(site["name"])
    return found


def _assert_no_personal_data(report, personal):
    """The control described in the module docstring. Raises rather than warns.

    Takes the accumulated set rather than one envelope. Keeping only the last
    envelope would mean a final run that failed to parse left the guard with
    nothing to look for, while the report still carried the previous run's
    summary — a guard that switches itself off exactly when something has gone
    wrong is the wrong shape for a guard.
    """
    haystack = report.lower()
    leaked = [s for s in personal if s.lower() in haystack]
    if leaked:
        # The leaked value is NOT named in this message: an assertion that
        # reports a leak by repeating it has leaked it.
        raise SystemExit(
            "live_batch.py: REFUSING to print — the report contains %d value(s) "
            "REQ-B20 confines to the panel. The report was discarded." % len(leaked))


def summarise(envelope):
    """Counts and codes. No strings from the site itself."""
    data = (envelope or {}).get("data") or {}
    counts = data.get("counts") or {}
    devices = data.get("devices") or []
    clients = data.get("clients") or []
    detailed = sum(1 for d in devices if isinstance(d, dict) and d.get("detail"))
    metriced = sum(1 for d in devices if isinstance(d, dict) and d.get("metrics"))
    # The evidence that the two detail requests per device really happened.
    # `detailFetched` alone would count a `detail` that arrived empty, and a
    # batch that measured 620 ms because it fetched nothing is exactly the
    # result R-B1 would be most pleased and least entitled to.
    ports = radios = 0
    for entry in devices:
        detail = entry.get("detail") if isinstance(entry, dict) else None
        if isinstance(detail, dict):
            ports += len(detail.get("ports") or [])
            radios += len(detail.get("radios") or [])
    return {
        "ok": (envelope or {}).get("ok"),
        "devicesListed": len(devices),
        "devicesTotal": counts.get("devicesTotal"),
        "detailFetched": detailed,
        "withMetrics": metriced,
        "ports": ports,
        "radios": radios,
        "clientsListed": len(clients),
        "clientsCount": counts.get("clients"),
        "offlineTotal": counts.get("offlineTotal"),
        "warnings": sorted({w.get("code") for w in (envelope or {}).get("warnings") or []}),
        "errorKind": ((envelope or {}).get("error") or {}).get("kind"),
    }


def main():
    if not approved():
        sys.stderr.write(
            "live_batch.py: REFUSED.\n\n"
            "    This sends live traffic to the user's own UniFi controller.\n"
            "    SPEC.md §15 classifies that as stop-and-ask (gate G-CONTROLLER).\n"
            "    Obtain explicit approval, then re-run with %s=1.\n\n"
            "    Nothing was sent.\n" % APPROVAL_ENV)
        return 2

    lines = []
    out = lines.append
    out("AC-B20 — a full batch against the real controller")
    out("  REQ-017 budget: %.1f s   runs: %d" % (BUDGET_SEC, RUNS))
    out("")

    failures = []
    walls = []
    personal = set()
    summary = None

    for index in range(1, RUNS + 1):
        wall, code, envelope, stderr = run_batch(index)
        walls.append(wall)
        personal |= personal_strings(envelope)
        if envelope is None:
            failures.append("run %d: stdout was not a JSON envelope" % index)
            out("  run %d: %6.0f ms  exit %d  UNPARSEABLE" % (index, wall * 1000, code))
            continue
        summary = summarise(envelope)
        out("  run %d: %6.0f ms  exit %d  ok=%s  devices %d/%s detail %d  clients %d"
            % (index, wall * 1000, code, summary["ok"], summary["devicesListed"],
               summary["devicesTotal"], summary["detailFetched"],
               summary["clientsListed"]))
        if stderr:
            # DATA-005b bounds stderr; any content at all is worth knowing about,
            # but the content itself may name a host, so only its size is shown.
            failures.append("run %d: helper wrote %d byte(s) to stderr"
                            % (index, len(stderr)))
        if summary["ok"] is not True:
            failures.append("run %d: ok=%s errorKind=%s"
                            % (index, summary["ok"], summary["errorKind"]))
        if wall > BUDGET_SEC:
            failures.append("run %d: %.1f s exceeds the REQ-017 budget"
                            % (index, wall))

    out("")
    if summary is not None:
        out("  last batch:")
        out("    devices listed %s of %s, detail fetched for %s, metrics for %s"
            % (summary["devicesListed"], summary["devicesTotal"],
               summary["detailFetched"], summary["withMetrics"]))
        out("    detail carried %s port(s) and %s radio(s) in total"
            % (summary["ports"], summary["radios"]))
        out("    clients listed %s, counts.clients %s, offlineTotal %s"
            % (summary["clientsListed"], summary["clientsCount"],
               summary["offlineTotal"]))
        out("    warnings: %s" % (", ".join(summary["warnings"]) or "none"))

        # AC-B20's second clause. On a site inside DEVICE_DETAIL_MAX this must
        # hold outright; if the site has outgrown the bound the criterion is not
        # measurable here and says so rather than passing on a technicality.
        if summary["devicesListed"] != summary["devicesTotal"]:
            failures.append("devices[] is truncated (%s of %s) — AC-B20 wants a "
                            "full batch" % (summary["devicesListed"],
                                            summary["devicesTotal"]))
        if summary["detailFetched"] != summary["devicesListed"]:
            failures.append("detail fetched for %s of %s devices — AC-B20 wants "
                            "every device" % (summary["detailFetched"],
                                              summary["devicesListed"]))
        # An empty `detail` object on every device would satisfy the count above
        # and mean nothing was fetched.
        if summary["detailFetched"] and summary["ports"] + summary["radios"] == 0:
            failures.append("every detail is empty — no ports and no radios "
                            "across %s device(s)" % summary["detailFetched"])
        if "device_detail_truncated" in summary["warnings"]:
            failures.append("device_detail_truncated was raised")

    if walls:
        out("")
        out("  wall time: min %.0f ms  max %.0f ms  mean %.0f ms  (budget %.0f ms)"
            % (min(walls) * 1000, max(walls) * 1000,
               sum(walls) / len(walls) * 1000, BUDGET_SEC * 1000))
        out("  headroom at the worst run: %.1f%% of the budget used"
            % (max(walls) / BUDGET_SEC * 100))

    out("")
    if failures:
        out("AC-B20 FAIL — %d finding(s):" % len(failures))
        for line in failures:
            out("  - %s" % line)
    else:
        out("AC-B20 PASS")

    report = "\n".join(lines)
    _assert_no_personal_data(report, personal)
    print(report)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
