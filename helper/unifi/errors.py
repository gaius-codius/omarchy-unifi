"""Error kinds the helper can report, as exceptions.

Created in Phase 5 and extended in Phase 6 (the plan assigns `errors.py` to
Phase 6; see AMD-9). Phase 5 needs the credential and configuration kinds
before the transport taxonomy exists, and one shared base is better than four
modules inventing their own and Phase 6 unifying them afterwards.

SEC-001 governs every message in this file and every message that constructs
one: **the credential never appears in an exception**. Not truncated, not
length-prefixed, not "starts with". A message here can reach stderr, a
traceback, or a log, and DATA-005b caps stderr precisely because it is the
channel most likely to carry material nobody meant to emit.
"""

# Every kind here is one of the nineteen in DATA-007. `internal` is the
# deliberate default rather than a sentinel: an exception whose kind nobody
# chose is a bug in this helper, which is exactly what `internal` means.
KIND_INTERNAL = "internal"
KIND_UNCONFIGURED = "unconfigured"
KIND_UNCOMMITTED = "uncommitted"
KIND_CREDENTIAL = "credential"
KIND_TLS = "tls"


class HelperError(Exception):
    """Base for every error the helper reports as a DATA-007 kind."""

    kind = KIND_INTERNAL

    def __init__(self, message, detail=None):
        super(HelperError, self).__init__(message)
        self.message = message
        self.detail = detail


class ConfigError(HelperError):
    """The configuration directory or its files cannot be used as they are.

    SEC-002/SEC-003. Reported as `unconfigured` because the user's next step is
    to run `scripts/configure`, not to wait for a retry.
    """

    kind = KIND_UNCONFIGURED


class UncommittedError(HelperError):
    """The three files do not form one committed set (DATA-004).

    Distinct from ConfigError: the files are readable and well-formed, but a
    write was interrupted between the two replacements, so the configuration on
    disk is a mixture of two. Sending a request would use one controller's URL
    with another's key.
    """

    kind = KIND_UNCOMMITTED


class CredentialError(HelperError):
    """The API key is unusable (SEC-004).

    The message says what is wrong with the SHAPE of the credential and never
    quotes any part of it.
    """

    kind = KIND_CREDENTIAL


class TlsError(HelperError):
    """A TLS context could not be built, or verification failed (SEC-005/006)."""

    kind = KIND_TLS


# ---------------------------------------------------------------------------
# Phase 6: the full DATA-007 taxonomy
# ---------------------------------------------------------------------------

KIND_SITE_UNSELECTED = "site_unselected"
KIND_UNAUTHORIZED = "unauthorized"
KIND_FORBIDDEN = "forbidden"
KIND_NETWORK = "network"
KIND_TIMEOUT = "timeout"
KIND_RATE_LIMITED = "rate_limited"
KIND_HTTP = "http"
KIND_UNSUPPORTED = "unsupported"
KIND_REDIRECT = "redirect"
KIND_CONFIGURATION_CONFLICT = "configuration_conflict"
KIND_PARTIAL_RESPONSE = "partial_response"
KIND_OVERSIZED_RESPONSE = "oversized_response"
KIND_MALFORMED_RESPONSE = "malformed_response"
KIND_HELPER_UNAVAILABLE = "helper_unavailable"

# The nineteen, in DATA-007's order. Written longhand rather than derived from
# the class hierarchy: the spec's list is the authority, and a kind that loses
# its exception class must still fail the count.
KINDS = (
    KIND_UNCONFIGURED,
    KIND_SITE_UNSELECTED,
    KIND_UNCOMMITTED,
    KIND_CREDENTIAL,
    KIND_UNAUTHORIZED,
    KIND_FORBIDDEN,
    KIND_TLS,
    KIND_NETWORK,
    KIND_TIMEOUT,
    KIND_RATE_LIMITED,
    KIND_HTTP,
    KIND_UNSUPPORTED,
    KIND_REDIRECT,
    KIND_CONFIGURATION_CONFLICT,
    KIND_PARTIAL_RESPONSE,
    KIND_OVERSIZED_RESPONSE,
    KIND_MALFORMED_RESPONSE,
    KIND_HELPER_UNAVAILABLE,
    KIND_INTERNAL,
)

CLASS_TRANSIENT = "transient"
CLASS_FATAL = "fatal"
CLASS_INTEGRITY = "integrity"

# The DATA-007a matrix, minus `http` — its class depends on its status, which is
# why it is absent here rather than present with a placeholder. A lookup that
# forgets to pass a status therefore raises instead of guessing.
RETRY_CLASS = {
    KIND_UNCONFIGURED: CLASS_FATAL,
    KIND_SITE_UNSELECTED: CLASS_FATAL,
    KIND_UNCOMMITTED: CLASS_FATAL,
    KIND_CREDENTIAL: CLASS_FATAL,
    KIND_UNAUTHORIZED: CLASS_FATAL,
    KIND_FORBIDDEN: CLASS_FATAL,
    KIND_TLS: CLASS_FATAL,
    KIND_NETWORK: CLASS_TRANSIENT,
    KIND_TIMEOUT: CLASS_TRANSIENT,
    KIND_RATE_LIMITED: CLASS_TRANSIENT,
    KIND_UNSUPPORTED: CLASS_FATAL,
    KIND_REDIRECT: CLASS_INTEGRITY,
    KIND_CONFIGURATION_CONFLICT: CLASS_FATAL,
    KIND_PARTIAL_RESPONSE: CLASS_INTEGRITY,
    KIND_OVERSIZED_RESPONSE: CLASS_INTEGRITY,
    KIND_MALFORMED_RESPONSE: CLASS_INTEGRITY,
    KIND_HELPER_UNAVAILABLE: CLASS_FATAL,
    KIND_INTERNAL: CLASS_INTEGRITY,
}

# REQ-019 makes a transient 5xx retryable; REQ-020 makes everything else fatal.
TRANSIENT_HTTP_STATUSES = (500, 502, 503, 504)

# Kinds that may carry an httpStatus, and the exact value each requires. `http`
# is the open one, bounded by range and excluding the three typed statuses.
HTTP_STATUS_REQUIRED = {
    KIND_UNAUTHORIZED: 401,
    KIND_FORBIDDEN: 403,
    KIND_RATE_LIMITED: 429,
}
HTTP_STATUS_MIN = 100
HTTP_STATUS_MAX = 599
TYPED_HTTP_STATUSES = tuple(sorted(HTTP_STATUS_REQUIRED.values()))


def retry_class_for(kind, http_status=None):
    """The DATA-007a retry class of a kind.

    `http` is the only kind whose class depends on its status, so it is the only
    one for which `http_status` is consulted. An unknown kind is `internal`'s
    class, matching DATA-007's forward-compatibility mapping.
    """
    if kind == KIND_HTTP:
        if http_status in TRANSIENT_HTTP_STATUSES:
            return CLASS_TRANSIENT
        return CLASS_FATAL
    return RETRY_CLASS.get(kind, RETRY_CLASS[KIND_INTERNAL])


def retryable_for(kind, http_status=None):
    """`error.retryable`, derived and never passed in.

    DATA-007a requires `retryable` to equal the kind's class. Deriving it here
    is what makes that structural: there is no argument a caller could get
    wrong, so the envelope cannot contradict the matrix the service checks it
    against.
    """
    return retry_class_for(kind, http_status) != CLASS_FATAL


class InconsistentError(ValueError):
    """`error_object` was asked to build something DATA-007a rejects.

    Raised rather than silently corrected. A corrected error object would be a
    plausible-looking lie about what went wrong; the caller's `internal`
    fallback is at least honest about the fact that the helper has a bug.
    """


def error_object(kind, message, http_status=None, retry_after_sec=None):
    """Build the DATA-007a-consistent `error` member of a failure envelope.

    Fields that the matrix forbids are OMITTED rather than emitted as null, and
    an integer supplied for a forbidden field raises: DATA-007a makes an
    envelope claiming `credential` with `httpStatus: 429` a protocol violation,
    so producing one is a bug to be found in this process, not a shape for the
    service to reject later.
    """
    if kind not in KINDS:
        raise InconsistentError("unknown error kind")

    if kind in HTTP_STATUS_REQUIRED:
        required = HTTP_STATUS_REQUIRED[kind]
        if http_status is not None and http_status != required:
            raise InconsistentError(
                "kind %s requires httpStatus %d" % (kind, required))
        http_status = required
    elif kind == KIND_HTTP:
        if not isinstance(http_status, int) or isinstance(http_status, bool):
            raise InconsistentError("kind http requires an integer httpStatus")
        if not (HTTP_STATUS_MIN <= http_status <= HTTP_STATUS_MAX):
            raise InconsistentError("httpStatus out of range")
        if http_status in TYPED_HTTP_STATUSES:
            # DATA-007 requires a received status to keep its typed kind, so an
            # `http` carrying 401 means the producer skipped that mapping.
            raise InconsistentError(
                "httpStatus %d has its own kind" % http_status)
    elif http_status is not None:
        raise InconsistentError("kind %s forbids httpStatus" % kind)

    if retry_after_sec is not None and kind != KIND_RATE_LIMITED:
        raise InconsistentError("kind %s forbids retryAfterSec" % kind)

    error = {
        "kind": kind,
        "message": message,
        "retryable": retryable_for(kind, http_status),
    }
    if http_status is not None:
        error["httpStatus"] = http_status
    if retry_after_sec is not None:
        error["retryAfterSec"] = retry_after_sec
    return error


class SiteUnselectedError(HelperError):
    """No `siteId` is committed and the controller has several sites (DATA-012)."""

    kind = KIND_SITE_UNSELECTED


class UnauthorizedError(HelperError):
    """HTTP 401. The API key was rejected."""

    kind = KIND_UNAUTHORIZED
    http_status = 401


class ForbiddenError(HelperError):
    """HTTP 403. The API key is valid but not permitted here."""

    kind = KIND_FORBIDDEN
    http_status = 403


class NetworkError(HelperError):
    """No HTTP response was received at all (DATA-007).

    DNS failure, connection refused or reset, host or network unreachable. It
    carries no `httpStatus` by definition: there was no status.
    """

    kind = KIND_NETWORK


class DeadlineError(HelperError):
    """The REQ-017 time budget expired.

    Named for the budget rather than for `timeout` so it does not shadow the
    builtin, and so the distinction from a kernel ETIMEDOUT stays visible at
    every call site: this is OUR clock running out, which is what DATA-007
    reserves the `timeout` kind for.
    """

    kind = KIND_TIMEOUT


class RateLimitedError(HelperError):
    """HTTP 429, optionally carrying a normalized `Retry-After`."""

    kind = KIND_RATE_LIMITED
    http_status = 429

    def __init__(self, message, retry_after_sec=None, detail=None):
        super(RateLimitedError, self).__init__(message, detail)
        self.retry_after_sec = retry_after_sec


class HttpError(HelperError):
    """Any other HTTP status, carrying it verbatim."""

    kind = KIND_HTTP

    def __init__(self, message, http_status, detail=None):
        super(HttpError, self).__init__(message, detail)
        self.http_status = http_status


class UnsupportedError(HelperError):
    """The controller version is outside the tested matrix (R2), or has no sites."""

    kind = KIND_UNSUPPORTED


class RedirectError(HelperError):
    """A 3xx was received. SEC-008: it is reported, never followed."""

    kind = KIND_REDIRECT


class ConfigurationConflictError(HelperError):
    """The committed configuration contradicts itself (REQ-021)."""

    kind = KIND_CONFIGURATION_CONFLICT


class PartialResponseError(HelperError):
    """A required collection could not be proven complete (BIZ-002)."""

    kind = KIND_PARTIAL_RESPONSE


class OversizedResponseError(HelperError):
    """A response, collection, or batch crossed its byte bound (SEC-010)."""

    kind = KIND_OVERSIZED_RESPONSE


class MalformedResponseError(HelperError):
    """A response was not the JSON shape the contract requires."""

    kind = KIND_MALFORMED_RESPONSE


class HelperUnavailableError(HelperError):
    """The helper cannot run at all — no interpreter, or too old (SPEC §11)."""

    kind = KIND_HELPER_UNAVAILABLE


class InternalError(HelperError):
    """A bug in this helper. The default kind, never a fallback for a known one."""

    kind = KIND_INTERNAL


def to_error_object(exc):
    """Convert a HelperError into its envelope `error` member.

    The single conversion point, so `retryable` and the httpStatus rules are
    applied identically no matter which module raised.
    """
    return error_object(
        exc.kind,
        exc.message,
        http_status=getattr(exc, "http_status", None),
        retry_after_sec=getattr(exc, "retry_after_sec", None))
