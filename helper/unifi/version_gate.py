"""R2: the tested-version matrix, and what happens outside it.

`/v1/info` returns exactly one field, `applicationVersion` (api-contract.md).
There is no capability list, no feature flags, nothing else to gate on. So the
gate is a coarse version comparison against versions this plugin has actually
been run against, and anything else is reported as `unsupported` with a message
that says so — rather than being attempted and misreported as an authentication
or connectivity failure when a shape turns out to have changed.

The matrix now holds two entries: the synthetic one the fixture corpus was
generated against, and one observed against a real controller in Phase 12a.
"""

import re

from . import errors

# A leading `major.minor`, ignoring any patch or suffix. Coarse on purpose: the
# API surface this plugin reads has never varied by patch level, and pinning to
# a patch would make every controller update an outage.
_VERSION_RE = re.compile(r"\A(\d{1,4})\.(\d{1,4})(?:[.\-+].*)?\Z")

# (major, minor) pairs this plugin has been run against.
#
#   (9, 1)   SYNTHETIC — the version of the published specification the fixture
#            corpus was generated from (unifi-network-v1-readonly-subset.json,
#            UniFi Network 10.4.57 → the fixtures report applicationVersion
#            9.1.0). No controller has confirmed it.
#
#   (10, 6)  OBSERVED — `applicationVersion: "10.6.101"`, Phase 12a,
#            2026-09-06. A full batch succeeded against it: all six routes
#            answered, every page envelope carried the five documented fields,
#            and DATA-012's single-site auto-selection worked. One divergence
#            was found and it is NOT a version problem — the console device
#            reports `features: ["switching"]` rather than including `gateway`
#            — so it does not belong in this gate. See api-contract.md.
TESTED_VERSIONS = ((9, 1), (10, 6))

MAX_VERSION_CHARS = 64


def parse(application_version):
    """`"9.1.0"` -> `(9, 1)`. None when the string is not a version at all."""
    if not isinstance(application_version, str):
        return None
    text = application_version.strip()
    if not text or len(text) > MAX_VERSION_CHARS:
        return None
    match = _VERSION_RE.match(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def is_supported(application_version):
    return parse(application_version) in TESTED_VERSIONS


def check(info, tested=TESTED_VERSIONS):
    """Gate on `/v1/info`. Returns the version string, or raises `unsupported`.

    An absent or unparseable `applicationVersion` is `unsupported` too, not
    `malformed_response`: the helper cannot establish that it understands this
    controller, which is exactly what `unsupported` means, and it is the message
    that tells the user something actionable.
    """
    if not isinstance(info, dict):
        raise errors.MalformedResponseError(
            "The controller's version response was not a JSON object.")
    reported = info.get("applicationVersion")
    parsed = parse(reported)
    if parsed is None:
        raise errors.UnsupportedError(
            "The controller did not report a UniFi Network version this plugin "
            "recognises.")
    if parsed not in tested:
        raise errors.UnsupportedError(
            "UniFi Network %d.%d has not been tested with this plugin."
            % parsed)
    return reported
