#!/usr/bin/env python3
"""A long-running loopback fixture controller, for CP8's live-staged checks.

`fixture_controller.py` is one-shot: it mints a CA, serves one scenario, runs
the helper once and exits. Phase 11 needs the other shape — a server the STAGED
service can poll on its own schedule, for as long as the checks take — so this
wraps the same `tls_stub` and the same `Controller` in a process that stays up.

It is loopback only, serves the committed fixture corpus, and uses a CA it mints
itself with a key that is not a credential. G-CONTROLLER is not engaged: nothing
here talks to anything outside 127.0.0.1, and no real API key exists.

    tests/tools/live_controller.py --scenario healthy --state /tmp/unifi-live

Writes `<state>/state.json` with the port, the apiRoot, the CA path and the
site id, then serves until SIGTERM. The scenario can be changed at runtime by
writing a new name into `<state>/scenario`, which is polled between requests —
so a check can move the site from healthy to degraded without restarting the
service under test.
"""

import argparse
import json
import os
import signal
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO, "helper"))

import tls_stub  # noqa: E402
from fixture_controller import Controller, FailRoute, SCENARIOS  # noqa: E402


def first_site(scenario):
    """The scenario's own site id, so the committed config never disagrees with
    the corpus it is pointed at — a mismatch is `site_unselected`, which would
    suspend polling and make every check below assert against a suspended
    service."""
    path = os.path.join(SCENARIOS, scenario, "sites.page0.json")
    with open(path, encoding="utf-8") as handle:
        page = json.load(handle)
    entries = page.get("data") or []
    if len(entries) != 1:
        return None
    return entries[0].get("id")


def main(argv):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--state", default="/tmp/unifi-live")
    parser.add_argument("--site", default=None,
                        help="the site UUID to advertise; defaults to the scenario's")
    args = parser.parse_args(argv[1:])

    state_dir = os.path.abspath(args.state)
    os.makedirs(state_dir, exist_ok=True)
    scenario_file = os.path.join(state_dir, "scenario")
    if not os.path.exists(scenario_file):
        with open(scenario_file, "w", encoding="utf-8") as handle:
            handle.write(args.scenario)

    tls_stub.require_openssl()
    authority = tls_stub.mint_ca(state_dir, "live")
    certfile, keyfile = tls_stub.mint_server_cert(state_dir, authority)

    current = {"name": None, "controller": None}

    def controller_for(name):
        if current["name"] != name:
            current["controller"] = Controller(name)
            current["name"] = name
        return current["controller"]

    delay_file = os.path.join(state_dir, "delay")

    def handler(request, index):
        # Per-request delay, read from a file so a check can make the batch slow
        # and fast again without restarting the service under test. AC-056 needs
        # a batch that is genuinely in flight when `reload` arrives, and the
        # honest way to get one is a slow SERVER on loopback — not an
        # unroutable address, which would put packets on the wire.
        try:
            with open(delay_file, encoding="utf-8") as handle:
                seconds = float(handle.read().strip() or "0")
        except (OSError, ValueError):
            seconds = 0.0
        if seconds > 0:
            time.sleep(min(seconds, 20.0))
        try:
            with open(scenario_file, encoding="utf-8") as handle:
                name = handle.read().strip() or args.scenario
        except OSError:
            name = args.scenario
        try:
            controller = controller_for(name)
        except Exception as error:                      # noqa: BLE001
            return tls_stub.response(500, "Scenario", body=json.dumps(
                {"statusCode": 500, "message": str(error)}).encode("utf-8"))
        try:
            body = controller.answer(request["target"])
        except FailRoute as injected:
            return tls_stub.response(injected.status, "Injected",
                                     body=b'{"statusCode":%d}' % injected.status)
        if body is None:
            return tls_stub.response(404, "Not Found", body=b'{"statusCode":404}')
        return tls_stub.response(200, "OK",
                                 body=json.dumps(body).encode("utf-8"))

    stopped = threading.Event()

    def stop(signum, frame):                            # noqa: ARG001
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with tls_stub.TlsStub(certfile, keyfile, handler=handler) as stub:
        site = args.site or first_site(args.scenario)
        state = {
            "port": stub.port,
            "apiRoot": "https://127.0.0.1:%d/proxy/network/integration" % stub.port,
            "caPath": authority.cert_path,
            "siteId": site,
            "pid": os.getpid(),
        }
        with open(os.path.join(state_dir, "state.json"), "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1)
        sys.stdout.write(json.dumps(state) + "\n")
        sys.stdout.flush()
        stopped.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
