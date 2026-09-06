"""SEC-010: everything the helper says, bounded and stripped.

One rule shapes this module: **a message is a fixed phrase, never a formatted
exception.** `str(exc)` on an `OSError` carries the path or host it failed on;
on an `ssl.SSLError` it carries certificate subject material; on an
`http.client` exception it can carry a fragment of the response. None of that
belongs in an envelope that ends up in a QML log, and no amount of scrubbing
after the fact is as reliable as never interpolating it.

So `describe_exception` maps a type to a phrase and drops the instance. The
information lost is real — a debugger would want it — and it is deliberately not
recovered through a "safe" formatter, because a formatter is a thing later
changes get added to. What survives is the DATA-007 kind, which is the part any
consumer acts on.

`clean` exists for the values that must pass through: a site name, a device
model. Those come from the controller, so they are bounded, stripped of control
characters, and scanned for the two shapes that must never appear.
"""

import re
import socket
import ssl
from urllib.parse import urlsplit

# Mirrors docs/protocol-v1.md's "Envelope content" bounds. The service enforces
# the same two numbers in Protocol.js; both sides check, neither trusts.
MESSAGE_MAX_CHARS = 256
STRING_MAX_CHARS = 512

TRUNCATION_MARKER = "..."

# C0, DEL and C1. A newline inside a message would let a crafted device name
# forge a second log line; the rest are simply not text.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")

# Two shapes that must never reach an envelope even by accident. They are
# belt-and-braces over the fixed-phrase rule above, and they are what a test can
# assert about a value this module did not construct.
_URLISH = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]*")
# Everything from the token to the end of the line, not just the next word: an
# earlier version stopped at the first whitespace run, so "Authorization: Bearer
# sk-live-..." redacted the word "Bearer" and emitted the token after it.
_KEYISH = re.compile(r"(x-api-key|api[_-]?key|authorization|bearer)[^\r\n]*",
                     re.IGNORECASE)

REDACTED = "<redacted>"
ELIDED_URL = "<url>"


def clean(value, limit=STRING_MAX_CHARS):
    """Bound and strip a string that came from outside this process."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = _KEYISH.sub(REDACTED, value)
    value = _URLISH.sub(ELIDED_URL, value)
    value = _CONTROL.sub(" ", value)
    value = _WHITESPACE.sub(" ", value).strip()
    if len(value) > limit:
        value = value[:limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return value


def message(text):
    """A message field: the same rules at the tighter 256-char bound."""
    return clean(text, MESSAGE_MAX_CHARS)


# The exception phrase book. Order matters where the types nest — ssl.SSLError
# is an OSError and socket.timeout is an OSError, so the specific entries are
# looked up before the general ones.
_PHRASES = (
    (ssl.SSLCertVerificationError, "The controller's TLS certificate was not trusted."),
    (ssl.SSLError, "The TLS connection to the controller failed."),
    (socket.gaierror, "The controller's address could not be resolved."),
    (socket.herror, "The controller's address could not be resolved."),
    (ConnectionRefusedError, "The controller refused the connection."),
    (ConnectionResetError, "The controller closed the connection."),
    (ConnectionAbortedError, "The connection to the controller was aborted."),
    (BrokenPipeError, "The connection to the controller was closed early."),
)

_FALLBACK = "The controller could not be reached."


def describe_exception(exc):
    """A fixed phrase for an exception. The instance itself is discarded.

    Never returns `str(exc)`. See the module docstring: this is the single
    reason no request URL, host, path or certificate field can reach a message
    through the failure path.
    """
    for exception_type, phrase in _PHRASES:
        if isinstance(exc, exception_type):
            return phrase
    return _FALLBACK


def host_of(url):
    """The host component of a URL, with its port and without its userinfo.

    DATA-006a's `apiRootHost`: never the full URL, never credentials. The port
    is kept because REQ-012's dashboard fallback builds `https://<host>` from
    this value, and a controller on a non-default port would otherwise get a
    link that goes nowhere.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None:
        host = "%s:%d" % (host, port)
    return host
