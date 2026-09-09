"""One GET, with every bound and every exception mapped to a DATA-007 kind.

`http.client` rather than `urllib.request`, for two reasons that are both
security rather than taste:

**No implicit context.** SEC-007 forbids letting `urllib` construct a default
TLS context. `urlopen` will build one if none is passed, and the guard against
that is a convention. `HTTPSConnection` takes `context` as a required argument
here, so there is no path that reaches a socket without the context this helper
built.

**No redirect handler to remove.** `urllib`'s opener follows 3xx by default, and
SEC-008 requires the opposite. Disabling it means constructing an
`OpenerDirector` without `HTTPRedirectHandler` and trusting that no later edit
re-adds it. `http.client` has no redirect machinery at all: following one would
have to be written on purpose.

The credential enters exactly one dictionary, at one line, from
`Credential.header_value()`. Nothing else in this module touches it, and the
`Request.__repr__` in `routes.py` is deliberately incapable of printing the URL
the header goes to.
"""

import email.utils
import errno
import http.client
import json
import socket
import ssl
import time

from . import errors
from . import sanitize
from . import warn
from . import HELPER_VERSION

# [chosen] The largest single response the helper will hold. A page of 200
# device records is ~62 KB, so this is thirty times the largest legitimate
# response and still small enough that a hostile controller cannot exhaust
# memory. It is separate from the per-collection and per-batch bounds in
# `pagination.py`: this one is what stops a SINGLE response, before any
# accumulation, and it is checked twice — against Content-Length before reading,
# and against the bytes actually received, because Content-Length is a claim.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# The `Retry-After` ceiling, matching Schedule.js's RETRY_AFTER_MAX_SEC. A
# controller asking for a week is either broken or hostile; REQ-019 caps it and
# raises `retry_after_clamped` rather than honouring it.
RETRY_AFTER_MAX_SEC = 86400

ACCEPT = "application/json"
CONTENT_TYPE_PREFIX = "application/json"
USER_AGENT = "omarchy-unifi/%s" % HELPER_VERSION

# Explicitly `identity`. The helper never asks for compression, so a compressed
# body is either a misconfigured proxy or a decompression bomb; either way this
# module refuses to be the thing that expands it.
ACCEPT_ENCODING = "identity"

OK_STATUS = 200

# `network` means "no HTTP response was received" (DATA-007). These are the
# errno values that describe exactly that.
#
# ETIMEDOUT is deliberately NOT here. A first version classified a kernel
# connect timeout as `network` and reserved `timeout` for this helper's own
# clock — and that distinction is not expressible. Since 3.10 `socket.timeout`
# IS `TimeoutError`, and PEP 3151 makes `OSError(ETIMEDOUT, ...)` construct a
# `TimeoutError` too, so on 3.10+ the two are the same class while on 3.9 they
# are not. Splitting them would mean the same failure reported a different kind
# depending on the user's interpreter. Both are `timeout`, both are transient,
# and nothing downstream distinguishes them.
_NETWORK_ERRNO_NAMES = (
    "ECONNREFUSED", "ECONNRESET", "ECONNABORTED", "EHOSTUNREACH",
    "ENETUNREACH", "ENETDOWN", "EHOSTDOWN", "ENOTCONN", "EPIPE",
    "EADDRNOTAVAIL", "ESHUTDOWN",
)
_NETWORK_ERRNOS = frozenset(
    getattr(errno, name) for name in _NETWORK_ERRNO_NAMES if hasattr(errno, name))


def connect_https(host, port, timeout, context):
    """The default connection factory. Injectable so tests can fail on demand."""
    return http.client.HTTPSConnection(
        host, port=port, timeout=timeout, context=context)


def request_headers(credential):
    """The complete header set. The single place the credential is read."""
    return {
        "X-API-Key": credential.header_value(),
        "Accept": ACCEPT,
        "Accept-Encoding": ACCEPT_ENCODING,
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }


def get_json(request, credential, context, deadline, warnings=None,
             connect=connect_https, max_bytes=MAX_RESPONSE_BYTES):
    """Perform one allowlisted GET and return `(body, decoded_bytes)`.

    Raises a `HelperError` whose kind follows DATA-007's precedence. Never
    follows a redirect, never retries, never reads an unbounded body.
    """
    if request.method != "GET":
        # BIZ-001's method allowlist. Unreachable through `routes.build`, which
        # is why it is here: this is the only function that opens a socket, so
        # it is the only place the guarantee can be made rather than assumed.
        raise errors.InternalError("Only GET requests may be sent.")

    timeout = deadline.timeout_for(request.route)
    connection = None
    try:
        connection = connect(request.host, request.port, timeout, context)
        connection.request("GET", request.target, headers=request_headers(credential))
        response = connection.getresponse()
        return _read(response, request, deadline, warnings, max_bytes)
    except errors.HelperError:
        raise
    except Exception as exc:  # noqa: BLE001 — mapped exhaustively below
        raise _classify(exc)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 — teardown must not mask the cause
                pass


def _read(response, request, deadline, warnings, max_bytes):
    status = response.status

    if 300 <= status < 400:
        # SEC-008. The body is discarded unread and the Location header is never
        # looked at: the only way X-API-Key reaches a second URL is if something
        # constructs a second request, so nothing here does.
        _discard(response)
        raise errors.RedirectError(
            "The controller redirected the request, which is not followed.",
            detail={"route": request.route, "status": status})

    if status == 401:
        _discard(response)
        raise errors.UnauthorizedError(
            "The controller rejected the API key. Create a new key and re-run "
            "scripts/configure.")
    if status == 403:
        _discard(response)
        raise errors.ForbiddenError(
            "The API key is not permitted to read this site.")
    if status == 429:
        retry_after = _retry_after(response, warnings)
        _discard(response)
        raise errors.RateLimitedError(
            "The controller is rate limiting requests.",
            retry_after_sec=retry_after)
    if status != OK_STATUS:
        _discard(response)
        # A status outside 100..599 is not an HTTP status, it is a malformed
        # status line that `http.client` happens to parse — it accepts anything
        # up to 999. Raising `HttpError` with one was fatal in a way worth
        # spelling out: `errors.error_object` validates the range and raises
        # `InconsistentError`, which is a ValueError and NOT a HelperError, and
        # it did so from INSIDE `unifi_status.run`'s `except errors.HelperError`
        # handler — where the sibling `except Exception` cannot catch it. The
        # helper died with a traceback on stderr and NOTHING on stdout, which
        # are exactly the two outcomes that handler exists to prevent.
        if not (errors.HTTP_STATUS_MIN <= status <= errors.HTTP_STATUS_MAX):
            raise errors.MalformedResponseError(
                "The controller returned a status line that is not HTTP.")
        raise errors.HttpError(
            "The controller returned an unexpected HTTP status.", status)

    encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
    if encoding not in ("", "identity"):
        _discard(response)
        raise errors.MalformedResponseError(
            "The controller sent a compressed response, which was not requested.")

    content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
    if content_type != CONTENT_TYPE_PREFIX:
        # A captive portal or a login page answering 200 with HTML is the case
        # this catches. Parsing it would fail anyway; failing here says why.
        _discard(response)
        raise errors.MalformedResponseError(
            "The controller did not return JSON.")

    declared = response.getheader("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                _discard(response)
                raise errors.OversizedResponseError(
                    "The controller's response was larger than the helper accepts.")
        except ValueError:
            _discard(response)
            raise errors.MalformedResponseError(
                "The controller sent an unreadable Content-Length.")

    # One byte past the bound, so "exactly at the bound" and "over it" are
    # distinguishable. Content-Length was a claim; this is the measurement.
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise errors.OversizedResponseError(
            "The controller's response was larger than the helper accepts.")

    deadline.check(request.route)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise errors.MalformedResponseError(
            "The controller's response was not valid UTF-8.")
    try:
        body = json.loads(text)
    except ValueError:
        raise errors.MalformedResponseError(
            "The controller's response was not valid JSON.")
    except RecursionError:
        # Deeply nested JSON is a stack exhaustion attempt, not a parse error.
        raise errors.MalformedResponseError(
            "The controller's response was nested too deeply to parse.")

    if not isinstance(body, dict):
        raise errors.MalformedResponseError(
            "The controller's response was not a JSON object.")
    return body, len(raw)


def _discard(response):
    """Close a response without an unbounded read (SEC-008, SEC-010)."""
    try:
        response.read(0)
    except Exception:  # noqa: BLE001
        pass
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


def _retry_after(response, warnings):
    """Normalize the `Retry-After` header, or return None.

    R3: the published API documents no such header, so every branch here is
    written against something that may never arrive. That is the reason it is
    defensive rather than trusting: an unvalidated value taken from a header
    goes straight into the scheduler's next deadline.
    """
    raw = response.getheader("Retry-After")
    if raw is None:
        return None
    seconds = parse_retry_after(raw)
    if seconds is None:
        if warnings is not None:
            warnings.add("retry_after_ignored")
        return None
    if seconds > RETRY_AFTER_MAX_SEC:
        if warnings is not None:
            warnings.add("retry_after_clamped",
                         {"requestedSec": seconds,
                          "clampedSec": RETRY_AFTER_MAX_SEC})
        return RETRY_AFTER_MAX_SEC
    return seconds


def parse_retry_after(raw, now=None):
    """RFC 7231 `Retry-After`: delta-seconds or an HTTP-date. None if unusable.

    A date in the past yields None rather than zero — "retry immediately" is not
    what a rate limiter means, and zero would be indistinguishable from a header
    that said so.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        # An HTTP-date without a zone is malformed; guessing local time would
        # silently shift the delay by the machine's UTC offset.
        return None
    delta = when.timestamp() - (time.time() if now is None else now)
    if delta <= 0:
        return None
    return int(delta)


def _classify(exc):
    """Map an exception to its DATA-007 kind, in the spec's precedence order.

    ssl.SSLError is an OSError and socket.timeout is an OSError, so the order
    below is not stylistic: reversing any two of these first four lines turns a
    certificate verification failure into a generic network error and loses the
    one signal that tells a user their controller is being intercepted.
    """
    if isinstance(exc, ssl.SSLError):
        return errors.TlsError(sanitize.describe_exception(exc))
    if isinstance(exc, (socket.timeout, TimeoutError)):
        # Both names, so the behaviour is identical on 3.9 (where they differ)
        # and on 3.10+ (where they are the same class). See _NETWORK_ERRNO_NAMES.
        return errors.DeadlineError(
            "The controller did not respond within the time budget.")
    if isinstance(exc, (socket.gaierror, socket.herror)):
        return errors.NetworkError(sanitize.describe_exception(exc))
    if isinstance(exc, ConnectionError):
        return errors.NetworkError(sanitize.describe_exception(exc))
    if isinstance(exc, http.client.HTTPException):
        # BadStatusLine, IncompleteRead, LineTooLong: the connection produced
        # no complete HTTP response, which is what `network` means.
        return errors.NetworkError(
            "The controller's response could not be read.")
    if isinstance(exc, OSError):
        if exc.errno in _NETWORK_ERRNOS:
            return errors.NetworkError(sanitize.describe_exception(exc))
        # A permission or descriptor error is a bug here, not a network fault.
        return errors.InternalError("The request could not be made.")
    return errors.InternalError("The request failed unexpectedly.")
