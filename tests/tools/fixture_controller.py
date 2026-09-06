#!/usr/bin/env python3
"""Run the REAL helper against a TLS stub serving the Phase 1 API fixtures.

This is the producer half of the end-to-end slice. It mints a CA, serves one of
`tests/fixtures/api/scenarios/` over HTTPS on 127.0.0.1, writes a committed
configuration directory under a temp root, launches `helper/unifi_status.py` as
a subprocess with the exact flags the service uses, and prints what came back.

The output is one JSON object on stdout:

    {"stdout": "<the helper's literal stdout>", "stderr": "...",
     "exitCode": 0, "nonce": "...", "launchAt": "...", "receiptAt": "...",
     "requests": ["/proxy/.../v1/info", ...]}

`stdout` is JSON-encoded, so the consumer gets the helper's bytes back exactly.
`tests/test_end_to_end.js` decodes it and feeds it to `Protocol.acceptStdout`
under Node — which is the point of the whole exercise: the envelope CPython
produced is validated by the V8 engine that will validate it in the shell, with
no Python assertion standing in between.

Nothing here is shipped, and nothing here is written into the repository.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, parse_qs

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_REPO, "helper"))

import tls_stub  # noqa: E402
from unifi import commitset  # noqa: E402

SCENARIOS = os.path.join(_REPO, "tests", "fixtures", "api", "scenarios")

API_KEY = b"sk-fixture-not-a-real-key\n"
NONCE = "e2e0123456789abc"

# The helper's launch flags, verbatim from docs/protocol-v1.md. Asserted rather
# than assumed: running it any other way here would test something the service
# never does.
PYTHON_FLAGS = ["-B", "-E", "-s"]


class FailRoute(Exception):
    """The router was told to answer this route with an HTTP failure."""

    def __init__(self, status):
        super(FailRoute, self).__init__(status)
        self.status = status


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class Controller(object):
    """Routes a request path to a fixture file. Knows nothing about HTTP."""

    def __init__(self, scenario, fail_route=None, fail_status=500,
                 on_request=None):
        self.root = os.path.join(SCENARIOS, scenario)
        if not os.path.isdir(self.root):
            raise SystemExit("no such scenario: %s" % scenario)
        self.requests = []
        self.api_keys = []
        self.fail_route = fail_route
        self.fail_status = fail_status
        self.on_request = on_request

    def answer(self, target):
        self.requests.append(target)
        if self.on_request is not None:
            self.on_request(len(self.requests))
        parts = urlsplit(target)
        query = parse_qs(parts.query)
        offset = int(query.get("offset", ["0"])[0])
        segments = [seg for seg in parts.path.split("/") if seg]

        # Everything after the /v1 marker. The prefix is the apiRoot's own path
        # and is the transport's business, not this router's.
        if "v1" not in segments:
            return None
        tail = segments[segments.index("v1") + 1:]

        if self.fail_route is not None and self._matches(tail, self.fail_route):
            raise FailRoute(self.fail_status)

        if tail == ["info"]:
            return load(os.path.join(self.root, "info.json"))
        if tail == ["sites"]:
            return self._page("sites", offset)
        if len(tail) == 3 and tail[0] == "sites" and tail[2] in ("devices", "clients", "wans"):
            return self._page(tail[2], offset)
        if (len(tail) == 6 and tail[0] == "sites" and tail[2] == "devices"
                and tail[4:] == ["statistics", "latest"]):
            path = os.path.join(self.root, "statistics.%s.json" % tail[3])
            if not os.path.exists(path):
                return None
            return load(path)
        return None

    @staticmethod
    def _matches(tail, route):
        """`route` names a collection the way BIZ-004's table does."""
        if route == "info":
            return tail == ["info"]
        if route == "sites":
            return tail == ["sites"]
        if route == "device_statistics":
            return len(tail) == 6 and tail[4:] == ["statistics", "latest"]
        return len(tail) == 3 and tail[2] == route

    def _page(self, collection, offset):
        """Serve the page whose own `offset` field matches the request.

        Offset-addressed rather than sequential, because the DATA-009a re-read
        asks for page 0 again after the last page and a sequential server would
        answer it with whatever came next.
        """
        index = 0
        while True:
            path = os.path.join(self.root, "%s.page%d.json" % (collection, index))
            if not os.path.exists(path):
                return None
            page = load(path)
            if page.get("offset") == offset:
                return page
            index += 1


def write_config(config_dir, api_root, site_id, ca_path):
    os.mkdir(config_dir, 0o700)
    config = {"apiRoot": api_root}
    if site_id is not None:
        config["siteId"] = site_id
    if ca_path is not None:
        config["customCaPath"] = ca_path
    config_bytes = json.dumps(config, sort_keys=True).encode("utf-8")

    _write(os.path.join(config_dir, "config.json"), config_bytes)
    _write(os.path.join(config_dir, "api-key"), API_KEY)
    commit = commitset.build_commit(1, config_bytes, API_KEY)
    _write(os.path.join(config_dir, "commit.json"),
           json.dumps(commit, sort_keys=True).encode("utf-8"))


def _rotate(config_dir):
    """Replace the whole committed set with a different credential."""
    replacement = b"sk-rotated-different-key\n"
    config_path = os.path.join(config_dir, "config.json")
    with open(config_path, "rb") as handle:
        config_bytes = handle.read()
    _write(os.path.join(config_dir, "api-key"), replacement)
    commit = commitset.build_commit(2, config_bytes, replacement)
    _write(os.path.join(config_dir, "commit.json"),
           json.dumps(commit, sort_keys=True).encode("utf-8"))


def _write(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--site", default=None,
                        help="commit this siteId; omit to exercise DATA-012 discovery")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--fail-route", default=None,
                        help="answer this BIZ-004 collection with --fail-status")
    parser.add_argument("--fail-status", type=int, default=500)
    parser.add_argument("--corrupt", default=None,
                        choices=["api-key", "config.json", "commit.json"],
                        help="break the committed set AFTER it is written (AC-009)")
    parser.add_argument("--rotate-after", type=int, default=0,
                        help="replace the credential mid-batch, after N requests (AC-054)")
    parser.add_argument("--keylog", default=None,
                        help="set SSLKEYLOGFILE for the helper (AC-015a)")
    args = parser.parse_args(argv)

    workdir = tempfile.mkdtemp(prefix="omarchy-unifi-e2e-")
    rotation = {"done": False, "dir": None}

    def on_request(count):
        # DATA-004c / AC-054. The credential is replaced on disk WHILE the batch
        # is in flight, which is the window `scripts/configure` actually opens.
        # A helper that re-read it would send controller A's URL with
        # controller B's key from this request onward.
        if not args.rotate_after or rotation["done"]:
            return
        if count < args.rotate_after:
            return
        rotation["done"] = True
        _rotate(rotation["dir"])

    controller = Controller(args.scenario, fail_route=args.fail_route,
                            fail_status=args.fail_status,
                            on_request=on_request if args.rotate_after else None)
    try:
        authority = tls_stub.mint_ca(workdir, "e2e")
        certfile, keyfile = tls_stub.mint_server_cert(workdir, authority)

        def handler(request, index):
            controller.api_keys.append(request["headers"].get("x-api-key"))
            try:
                body = controller.answer(request["target"])
            except FailRoute as injected:
                return tls_stub.response(injected.status, "Injected",
                                         body=b'{"statusCode":%d}' % injected.status)
            if body is None:
                return tls_stub.response(404, "Not Found", body=b'{"statusCode":404}')
            payload = json.dumps(body).encode("utf-8")
            return tls_stub.response(200, "OK", body=payload)

        with tls_stub.TlsStub(certfile, keyfile, handler=handler) as stub:
            api_root = "https://127.0.0.1:%d/proxy/network/integration" % stub.port
            config_dir = os.path.join(workdir, "omarchy-unifi")
            write_config(config_dir, api_root, args.site, authority.cert_path)
            rotation["dir"] = config_dir
            if args.corrupt:
                # Written AFTER a valid commit, so the digests no longer agree —
                # which is the interrupted-write state DATA-004 defines, not a
                # malformed file.
                _write(os.path.join(config_dir, args.corrupt), b"tampered\n")

            environment = dict(os.environ)
            if args.keylog:
                environment["SSLKEYLOGFILE"] = args.keylog

            launch_at = utc_now()
            completed = subprocess.run(
                [args.python] + PYTHON_FLAGS
                + [os.path.join(_REPO, "helper", "unifi_status.py"),
                   "--nonce", NONCE, "--config-dir", config_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                env=environment)
            receipt_at = utc_now()

        sys.stdout.write(json.dumps({
            "stdout": completed.stdout.decode("utf-8", "replace"),
            "stderr": completed.stderr.decode("utf-8", "replace"),
            "exitCode": completed.returncode,
            "nonce": NONCE,
            "launchAt": launch_at,
            "receiptAt": receipt_at,
            "requests": controller.requests,
            "apiKeys": controller.api_keys,
            "keylogExists": bool(args.keylog) and os.path.exists(args.keylog),
            # Reported so the consumer can assert the rotation ACTUALLY fired.
            # A test that only checks "one distinct key" passes trivially when
            # the credential was never replaced.
            "rotated": rotation["done"],
        }))
        return 0
    finally:
        shutil.rmtree(workdir, True)


if __name__ == "__main__":
    sys.exit(main())
