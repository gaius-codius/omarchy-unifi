"""Expand a pagination fixture case into the page bodies a controller returns.

Most cases carry their pages verbatim in `responses`. Two do not: the cases
that prove the 64-page and 8 MiB collection bounds are enforced would need
thousands of near-identical device records to materialise, which would put ten
megabytes into a repository that `omarchy plugin add` clones into
~/.config/omarchy/plugins/ on every install. Those carry a `synthesize` block
instead, and this module expands it.

The DATA-009 invariants are enforced helper-side only (see docs/protocol-v1.md),
so this expander has exactly one consumer language and no cross-implementation
drift risk. It is imported by both tests/tools/gen_fixtures.py and
tests/test_fixtures.py so the generator and the suite cannot disagree about what
a case means.
"""

import json
import os


def _record(index, padding_bytes=0):
    """A minimally-shaped adopted-device record.

    Synthesized records only ever feed a case that must be REJECTED for a bound
    violation, so the reader stops before content matters. They still carry the
    required fields, because a reader that rejected them for the wrong reason
    would make the case pass while testing nothing.

    `padding_bytes` inflates the record so a case about DECODED BYTES actually
    reaches its bound. An unpadded page of 200 of these is ~62 KB, so the 64
    page limit is hit at ~3.8 MiB and the 8 MiB bound is never approached. The
    padding is a plain ASCII run in a field the reader ignores: it must not
    resemble a credential, a MAC, an address or a UUID, because
    tests/test_fixtures.py scans the corpus for all four.
    """
    record = {
        "id": "00000000-0000-5000-8000-%012x" % index,
        "name": "Bulk %06d" % index,
        "model": "USW-Lite-8-PoE",
        "macAddress": "02:00:00:%02x:%02x:%02x" % ((index >> 16) & 0xFF,
                                                   (index >> 8) & 0xFF, index & 0xFF),
        "ipAddress": "192.168.10.%d" % (1 + index % 254),
        "state": "ONLINE",
        "features": ["switching"],
        "interfaces": ["ports"],
        "firmwareVersion": "9.1.0",
        "firmwareUpdatable": False,
        "supported": True,
    }
    if padding_bytes:
        record["notes"] = "pad" * (padding_bytes // 3 + 1)
    return record


def responses_for(case):
    """Return the ordered page bodies for a pagination case."""
    if case.get("responses") is not None:
        return case["responses"]

    plan = case["synthesize"]
    per_page = plan["recordsPerPage"]
    padding = plan.get("paddingBytes", 0)
    pages = []
    for index in range(plan["pages"]):
        offset = index * per_page
        pages.append({
            "offset": offset,
            "limit": case["limit"],
            "count": per_page,
            "totalCount": plan["totalCount"],
            "data": [_record(offset + i, padding) for i in range(per_page)],
        })
    return pages


def load_case(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_all(directory):
    cases = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            case = load_case(os.path.join(directory, name))
            cases[case["id"]] = case
    return cases
