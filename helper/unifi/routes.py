"""BIZ-001 / SEC-009 / SEC-013: the six-route allowlist, as data.

The allowlist is a table, not a set of string literals scattered through the
collector, because BIZ-001's claim — "no other route may be constructed" — is
only checkable if there is one place that constructs routes.

SEC-013 is the reason this file is longer than a formatter would need to be. A
`"uuid"` type label is not a closed character set: a hand-edited
`"siteId": "x/../../v1/hotspots"` builds
`.../v1/sites/x/../../v1/hotspots/devices`, which a server resolves to
`/v1/hotspots` — a route outside the allowlist, reached with `X-API-Key`
attached. Three independent barriers stand in the way, in this order:

1. every interpolated value must match the canonical UUID pattern;
2. every interpolated value is percent-encoded, so a separator that somehow
   survived step 1 cannot act as one;
3. the assembled URL is taken apart again and re-matched against the allowlist,
   so a route that got built wrong is caught even if steps 1 and 2 were bypassed
   by a future edit.

Step 2 cannot fire while step 1 stands — a canonical UUID has nothing to encode.
That is stated rather than hidden: it is a barrier against a *change*, not
against today's input, and a barrier whose test never fires is one somebody
deletes. `encode_segment` is therefore tested directly.
"""

import re
from urllib.parse import quote, urlencode, urlsplit

from . import errors

METHOD = "GET"

SCHEME = "https"

API_PREFIX = "v1"

# SEC-013's pattern, verbatim from the spec. Anchored at both ends with \Z
# rather than $, because $ also matches before a trailing newline and
# "…-000000000000\n/../etc" would pass a $-anchored check.
UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

# Unreserved characters (RFC 3986 §2.3). The apiRoot's own path segments must be
# drawn from this set: it excludes "/", ".", "%" and every delimiter, so a root
# cannot smuggle traversal or a query into the assembled URL.
ROOT_SEGMENT_RE = re.compile(r"\A[A-Za-z0-9._~-]+\Z")

# api-contract.md's table. `params` is ordered, and names the segments the
# template interpolates; `paginated` decides whether offset/limit are legal.
ROUTES = {
    "info": {
        "template": "info",
        "params": (),
        "paginated": False,
    },
    "sites": {
        "template": "sites",
        "params": (),
        "paginated": True,
    },
    "devices": {
        "template": "sites/{siteId}/devices",
        "params": ("siteId",),
        "paginated": True,
    },
    "device_statistics": {
        "template": "sites/{siteId}/devices/{deviceId}/statistics/latest",
        "params": ("siteId", "deviceId"),
        "paginated": False,
    },
    "clients": {
        "template": "sites/{siteId}/clients",
        "params": ("siteId",),
        "paginated": True,
    },
    "wans": {
        "template": "sites/{siteId}/wans",
        "params": ("siteId",),
        "paginated": True,
    },
}

ROUTE_NAMES = tuple(sorted(ROUTES))

# DATA-009 / api-contract.md: the API's maximum, and what the helper always asks
# for.
PAGE_LIMIT = 200
OFFSET_MAX = 1000000


def _route_pattern(template):
    """Compile a template into the regex the post-assembly re-check uses."""
    parts = []
    for segment in template.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(UUID_RE.pattern[2:-2])
        else:
            parts.append(re.escape(segment))
    return re.compile(r"\A/%s/%s\Z" % (re.escape(API_PREFIX), "/".join(parts)))


ROUTE_PATTERNS = {name: _route_pattern(spec["template"])
                  for name, spec in ROUTES.items()}


class Request(object):
    """One assembled, re-checked GET. Immutable by convention and by slots."""

    __slots__ = ("route", "method", "url", "host", "port", "target")

    def __init__(self, route, url, host, port, target):
        self.route = route
        self.method = METHOD
        self.url = url
        self.host = host
        self.port = port
        self.target = target

    def __repr__(self):
        # The route name and nothing else. A repr that showed the URL would put
        # the controller's address into any traceback that formatted it.
        return "<Request %s>" % self.route


def encode_segment(value):
    """Percent-encode one path segment, treating every delimiter as data."""
    return quote(value, safe="")


def parse_api_root(api_root):
    """Validate `apiRoot` and return `(scheme, host, port, path)`.

    SEC-009: HTTPS only, no userinfo, no query, no fragment. The trailing slash
    is optional (AC-018) and normalized away, because `root + "/v1"` must not
    produce a `//` that changes the path.
    """
    if not isinstance(api_root, str) or not api_root.strip():
        raise errors.ConfigError(
            "apiRoot is missing. Run scripts/configure to set it.")

    parts = urlsplit(api_root.strip())
    if parts.scheme != SCHEME:
        raise errors.ConfigError(
            "apiRoot must be an https:// URL.")
    if parts.username is not None or parts.password is not None:
        raise errors.ConfigError(
            "apiRoot must not embed credentials.")
    if parts.query or parts.fragment:
        raise errors.ConfigError(
            "apiRoot must not carry a query string or fragment.")
    try:
        port = parts.port
    except ValueError:
        raise errors.ConfigError("apiRoot has an invalid port.")
    if not parts.hostname:
        raise errors.ConfigError("apiRoot has no host.")

    path = parts.path.rstrip("/")
    if path:
        if not path.startswith("/"):
            raise errors.ConfigError("apiRoot has a malformed path.")
        for segment in path.split("/")[1:]:
            # "." and "." + "." are made of unreserved characters, so the
            # character-set check alone accepts `/proxy/../../admin`. They are
            # named separately because they are the whole of the traversal.
            if segment in (".", ".."):
                raise errors.ConfigError(
                    "apiRoot's path may not contain relative segments.")
            if not ROOT_SEGMENT_RE.match(segment):
                raise errors.ConfigError(
                    "apiRoot's path may only contain unreserved characters.")
    return SCHEME, parts.hostname, port, path


def build(api_root, route, offset=None, limit=None, **params):
    """Assemble one allowlisted GET, or raise.

    Every rejection is `internal` or `unconfigured` — never a network kind —
    because nothing here depends on the controller. A route this refuses to
    build is a bug or a tampered configuration, and both are the user's problem
    to see rather than something to retry against.
    """
    if route not in ROUTES:
        raise errors.InternalError("Requested route is not in the allowlist.")
    spec = ROUTES[route]

    unexpected = set(params) - set(spec["params"])
    if unexpected:
        raise errors.InternalError("Route was given a parameter it has no slot for.")
    missing = set(spec["params"]) - set(params)
    if missing:
        raise errors.InternalError("Route is missing a required parameter.")

    encoded = {}
    for name in spec["params"]:
        value = params[name]
        if not isinstance(value, str) or not UUID_RE.match(value):
            # SEC-013. The message never quotes the value: a tampered config
            # could put anything in it, and this string reaches stderr.
            raise errors.ConfigError(
                "%s is not a canonical UUID. Run scripts/configure to set it."
                % name)
        encoded[name] = encode_segment(value)

    scheme, host, port, root_path = parse_api_root(api_root)

    path = "%s/%s/%s" % (root_path, API_PREFIX,
                         spec["template"].format(**encoded))

    query = ""
    if offset is not None or limit is not None:
        if not spec["paginated"]:
            raise errors.InternalError("Route does not accept pagination parameters.")
        query = urlencode(_pagination(offset, limit))

    netloc = host if port is None else "%s:%d" % (host, port)
    url = "%s://%s%s" % (scheme, netloc, path)
    if query:
        url = "%s?%s" % (url, query)

    _recheck(url, route, scheme, host, port, root_path, path, query)

    target = path if not query else "%s?%s" % (path, query)
    return Request(route, url, host, port, target)


def _pagination(offset, limit):
    offset = 0 if offset is None else offset
    limit = PAGE_LIMIT if limit is None else limit
    for name, value in (("offset", offset), ("limit", limit)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise errors.InternalError("Pagination %s must be an integer." % name)
    if not (0 <= offset <= OFFSET_MAX):
        raise errors.InternalError("Pagination offset is out of range.")
    if not (1 <= limit <= PAGE_LIMIT):
        raise errors.InternalError("Pagination limit is out of range.")
    return [("offset", offset), ("limit", limit)]


def _recheck(url, route, scheme, host, port, root_path, path, query):
    """Barrier 3: take the assembled URL apart and prove it is still the route.

    This repeats work the assembly above already did, and that is the point. It
    reads the finished string rather than the inputs, so it survives a future
    edit that changes how the string is built.
    """
    parts = urlsplit(url)
    if parts.scheme != scheme:
        raise errors.InternalError("Assembled URL changed scheme.")
    if parts.username is not None or parts.password is not None:
        raise errors.InternalError("Assembled URL carries credentials.")
    if parts.hostname != host or parts.port != port:
        raise errors.InternalError("Assembled URL changed host.")
    if parts.fragment:
        raise errors.InternalError("Assembled URL carries a fragment.")
    if parts.query != query:
        raise errors.InternalError("Assembled URL changed its query.")
    if parts.path != path:
        raise errors.InternalError("Assembled URL changed its path.")

    if root_path and not path.startswith(root_path + "/"):
        raise errors.InternalError("Assembled URL escaped the API root.")
    tail = path[len(root_path):]
    for segment in tail.split("/")[1:]:
        if segment in ("", ".", ".."):
            raise errors.InternalError("Assembled URL has an empty or relative segment.")

    matched = [name for name, pattern in ROUTE_PATTERNS.items()
               if pattern.match(tail)]
    if matched != [route]:
        raise errors.InternalError("Assembled URL is not the allowlisted route.")
