#!/usr/bin/env python3
"""Compose tests/fixtures/index.json from the two corpora.

The plan asks for one index naming, for every reject case, WHICH SIDE must
reject it and UNDER WHICH RULE. That question has two different answers and
they are easy to conflate:

  * DATA-009 pagination cases are enforced HELPER-side. The helper walks the
    pages, so only it can see an offset drift; by the time an envelope reaches
    QML the pages are gone.
  * DATA-008 envelope cases are enforced SERVICE-side. The service is the
    consumer, and every one of these publishes as the single named kind
    `malformed_response`.

Writing them into one file with an explicit `enforcedBy` is what stops a test
being written against the wrong side — which would pass, and would leave the
rule untested in the side that actually implements it.

Usage:  python3 -B tests/tools/gen_index.py [--check]
"""

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(REPO, "tests", "fixtures")
API_MANIFEST = os.path.join(FIXTURES, "api", "manifest.json")
ENVELOPE_INDEX = os.path.join(FIXTURES, "envelopes", "index.json")
OUT = os.path.join(FIXTURES, "index.json")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compose():
    api = load(API_MANIFEST)
    envelopes = load(ENVELOPE_INDEX)

    index = {
        "generatedBy": "tests/tools/gen_index.py",
        "regenerate": [
            "python3 -B tests/tools/gen_fixtures.py",
            "python3 -B tests/tools/gen_envelopes.py",
            "python3 -B tests/tools/gen_index.py",
        ],
        "contracts": {
            "api": "docs/feature-specs/omarchy-unifi-plugin/api-contract.md",
            "protocol": "docs/protocol-v1.md",
        },
        "privacy": {
            "rule": "SEC-011 / AC-059",
            "uuids": "uuid5 under a namespace derived from fixtures.omarchy-unifi.invalid "
                     "(RFC 2606 reserves .invalid, so it can never be a real host). "
                     "tests/test_fixtures.py regenerates and compares rather than "
                     "pattern-matching, so this is proof and not a heuristic.",
            "macs": "02:00:00:xx:xx:xx only. The 0x02 first octet sets the "
                    "locally-administered bit, which by IEEE 802 definition means the "
                    "address belongs to no assigned OUI.",
            "ipv4": "RFC1918 only: 192.168.10.0/24 for devices, 192.168.20.0/24 for clients.",
        },
        "cases": {},
    }

    for name, entry in sorted(api["scenarios"].items()):
        index["cases"]["api/scenarios/%s" % name] = {
            "verdict": "accept",
            "enforcedBy": "helper",
            "rule": "BIZ-004 / REQ-000 / REQ-008a",
            "note": entry["note"],
            "schemaDeviation": entry.get("schemaDeviation"),
        }

    for name, entry in sorted(api["pagination"].items()):
        accept = entry["expect"] == "accept"
        index["cases"]["api/pagination/%s" % name] = {
            "verdict": "accept" if accept else "reject",
            # Helper-side, always: the pages do not survive into the envelope,
            # so QML could not check these even if it wanted to.
            "enforcedBy": "helper",
            "rule": "DATA-009" if accept else "DATA-009 / %s" % entry["expect"],
            "invariant": None if accept else entry["expect"],
            "note": entry["note"],
            "synthesized": entry.get("synthesized", False),
        }

    for name, entry in sorted(envelopes["accept"].items()):
        index["cases"]["envelopes/accept/%s" % name] = {
            "verdict": "accept",
            # Both sides, and for opposite reasons — see the note in the file.
            "enforcedBy": "service and helper",
            "rule": "DATA-005 / DATA-006 / DATA-008",
            "errorKind": entry.get("errorKind"),
            "note": entry["note"],
        }

    for name, entry in sorted(envelopes["reject"].items()):
        index["cases"]["envelopes/reject/%s" % name] = {
            "verdict": "reject",
            "enforcedBy": "service",
            "rule": entry["rule"],
            "rejectionClass": entry["rejectionClass"],
            "publishedAs": entry["publishedAs"],
            "note": entry["note"],
        }

    accepts = [c for c in index["cases"].values() if c["verdict"] == "accept"]
    rejects = [c for c in index["cases"].values() if c["verdict"] == "reject"]
    index["coverage"] = {
        "total": len(index["cases"]),
        "accept": len(accepts),
        "reject": len(rejects),
        "rejectEnforcedByHelper": len([c for c in rejects if c["enforcedBy"] == "helper"]),
        "rejectEnforcedByService": len([c for c in rejects if c["enforcedBy"] == "service"]),
        "errorKinds": envelopes["coverage"]["errorKinds"],
        "rejectionClasses": envelopes["coverage"]["rejectionClasses"],
    }
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    index = compose()
    fresh = json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if args.check:
        if not os.path.exists(OUT):
            print("FAIL: tests/fixtures/index.json is missing", file=sys.stderr)
            return 1
        with open(OUT, encoding="utf-8") as handle:
            if handle.read() != fresh:
                print("FAIL: tests/fixtures/index.json does not match the corpora",
                      file=sys.stderr)
                return 1
        print("ok: the fixture index matches both corpora")
        return 0

    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(fresh)
    print("wrote %d case(s) to %s" % (index["coverage"]["total"], OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
