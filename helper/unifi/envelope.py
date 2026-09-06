"""DATA-005: the one JSON object this process writes, and its bounds.

Two shapes, nine keys, and the same nine on both. The shape is not conditional
and neither is `meta` (DEV-2): the two failure kinds that most need transport
facts on screen — `unconfigured` and `uncommitted` — are exactly the ones where
the configuration those facts come from could not be read, so every `meta` field
but `helperVersion` is nullable and the container is unconditional. One rule for
the validator instead of two, and no shape for the two implementations to
disagree about.

**Exit status is part of the protocol.** Zero means, and only means, "I produced
a success envelope". The service joins exit status with both streams before
parsing (REQ-017b), and a failure envelope paired with exit 0 is a rejection.
So the status is returned from here rather than chosen at the call site.

The 256 KiB stdout bound is enforced by construction. If the encoded envelope
exceeds it, this module emits an `oversized_response` failure envelope instead —
which is small by construction — rather than truncating, because a truncated
JSON object is a parse error on the other side and the service would report
`malformed_response` for what is really a size problem.
"""

import json

from . import errors
from . import sanitize
from . import warn
from . import HELPER_VERSION

PROTOCOL_VERSION = 1

# docs/protocol-v1.md, "Envelope and process". Both sides enforce both numbers.
STDOUT_MAX_BYTES = 256 * 1024
STDERR_MAX_BYTES = 4 * 1024

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

KEYS = ("protocolVersion", "ok", "nonce", "attemptedAt", "observedAt", "meta",
        "data", "warnings", "error")


def meta(config=None, commit=None, custom_ca_in_use=None):
    """DATA-006a. Always an object; every field but `helperVersion` nullable."""
    api_root_host = None
    site_id = None
    allow_insecure = None
    if config is not None:
        api_root_host = sanitize.host_of(config.api_root)
        site_id = config.site_id
        allow_insecure = config.allow_insecure_tls
        if custom_ca_in_use is None:
            custom_ca_in_use = config.custom_ca_path is not None
    return {
        "commitGeneration": commit.generation if commit is not None else None,
        "apiRootHost": api_root_host,
        "siteId": site_id,
        "allowInsecureTls": allow_insecure,
        "customCaInUse": custom_ca_in_use,
        "helperVersion": HELPER_VERSION,
    }


def success(nonce, attempted_at, observed_at, meta_object, data, warnings):
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "ok": True,
        "nonce": nonce,
        "attemptedAt": attempted_at,
        "observedAt": observed_at,
        "meta": meta_object,
        "data": data,
        "warnings": _warnings(warnings),
        "error": None,
    }


def failure(nonce, attempted_at, meta_object, error, warnings):
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "ok": False,
        "nonce": nonce,
        "attemptedAt": attempted_at,
        "observedAt": None,
        "meta": meta_object,
        "data": None,
        "warnings": _warnings(warnings),
        "error": error,
    }


def _warnings(warnings):
    if warnings is None:
        return []
    if isinstance(warnings, warn.Warnings):
        return warnings.to_list()
    return list(warnings)


def encode(envelope):
    """Serialize, and enforce the stdout bound by construction.

    Returns `(text, exit_status)`. A `separators` argument, because the default
    `json.dumps` puts a space after every separator and a 500-device envelope
    pays for several thousand of them against a bound this has to fit inside.
    """
    text = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    encoded = text.encode("utf-8")
    if len(encoded) > STDOUT_MAX_BYTES:
        # Replaced, never truncated: half a JSON object is a parse error, and
        # the service would report `malformed_response` for a size problem.
        replacement = failure(
            envelope.get("nonce"), envelope.get("attemptedAt"),
            envelope.get("meta") or meta(),
            errors.to_error_object(errors.OversizedResponseError(
                "The helper's own output exceeded its size bound.")),
            [])
        text = json.dumps(replacement, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
        return text, EXIT_FAILURE
    return text, (EXIT_SUCCESS if envelope["ok"] is True else EXIT_FAILURE)


def bounded_stderr(text):
    """DATA-005b: the helper's own stderr, sanitized and capped at 4 KiB.

    The service waits for this stream (REQ-017b), so an unbounded one is a hang
    as well as a disclosure. Returns `(text, exceeded)` so the caller can raise
    `stderr_bound_exceeded` rather than this module inventing a warning sink.
    """
    # Measured BEFORE cleaning. `sanitize.clean` truncates to the same bound, so
    # checking afterwards could never be true and the caller would never learn
    # that anything was dropped — the warning would be unreachable and the
    # stream would look complete.
    original = text if isinstance(text, str) else str(text)
    exceeded = len(original.encode("utf-8")) > STDERR_MAX_BYTES
    cleaned = sanitize.clean(original, STDERR_MAX_BYTES)
    encoded = cleaned.encode("utf-8")
    if len(encoded) > STDERR_MAX_BYTES:
        cleaned = encoded[:STDERR_MAX_BYTES].decode("utf-8", "ignore")
        exceeded = True
    return cleaned, exceeded
