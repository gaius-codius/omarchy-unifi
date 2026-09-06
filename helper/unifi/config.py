"""`config.json`: four keys, every one type-validated before it is used.

Not in Phase 6's or Phase 7's task list, and separate from `paths.py` on
purpose. `paths.py` answers "may this file be read at all" — ownership, mode,
symlinks, bounds — and returns bytes. This answers "is what it contains usable",
and the two questions have different failure modes: the first is a security
verdict, the second is a configuration one, and folding them together would mean
a typo in `apiRoot` and a world-writable credential file reported the same way.

No key here is trusted. `apiRoot` is re-validated by `routes.parse_api_root` at
every URL assembly, `siteId` by the UUID pattern at every interpolation. This
module rejects the obvious cases early so the message names the file rather than
the request that failed.
"""

import json

from . import errors

API_ROOT = "apiRoot"
SITE_ID = "siteId"
CUSTOM_CA_PATH = "customCaPath"
ALLOW_INSECURE_TLS = "allowInsecureTls"

KEYS = (API_ROOT, SITE_ID, CUSTOM_CA_PATH, ALLOW_INSECURE_TLS)

# A path long enough to be absurd is a sign of a corrupted file, and the value
# reaches an `open()` call.
CA_PATH_MAX_CHARS = 4096


class Config(object):
    """The parsed configuration. Immutable, because a batch reads once."""

    __slots__ = ("api_root", "site_id", "custom_ca_path", "allow_insecure_tls")

    def __init__(self, api_root, site_id, custom_ca_path, allow_insecure_tls):
        object.__setattr__(self, "api_root", api_root)
        object.__setattr__(self, "site_id", site_id)
        object.__setattr__(self, "custom_ca_path", custom_ca_path)
        object.__setattr__(self, "allow_insecure_tls", allow_insecure_tls)

    def __setattr__(self, name, value):
        raise AttributeError("Config is immutable")

    def __delattr__(self, name):
        raise AttributeError("Config is immutable")

    def __repr__(self):
        # Never the apiRoot: a repr can end up in a traceback, and the
        # controller's address is the one thing in this object worth not
        # printing by accident.
        return ("<Config site=%s customCa=%s insecure=%r>"
                % ("set" if self.site_id else "unset",
                   "set" if self.custom_ca_path else "unset",
                   self.allow_insecure_tls))


def parse(raw):
    """Parse `config.json`'s bytes. Every failure is `unconfigured`.

    `unconfigured` rather than `internal` because every one of these has the
    same next step for the user — run `scripts/configure` — and a retry helps
    with none of them.
    """
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        raise errors.ConfigError(
            "config.json is not valid UTF-8. Run scripts/configure to rewrite it.")
    try:
        body = json.loads(text)
    except ValueError:
        raise errors.ConfigError(
            "config.json is not valid JSON. Run scripts/configure to rewrite it.")
    if not isinstance(body, dict):
        raise errors.ConfigError(
            "config.json is not a JSON object. Run scripts/configure to rewrite it.")

    unknown = sorted(set(body) - set(KEYS))
    if unknown:
        # Named, not ignored. An unknown key is either a typo — in which case
        # the setting the user thinks they set is not in force — or a newer
        # configure writing a key this helper does not honour.
        raise errors.ConfigError(
            "config.json has a key this version does not use: %s." % unknown[0])

    api_root = body.get(API_ROOT)
    if not isinstance(api_root, str) or not api_root.strip():
        raise errors.ConfigError(
            "config.json has no apiRoot. Run scripts/configure to set it.")

    site_id = body.get(SITE_ID)
    if site_id is not None and (not isinstance(site_id, str) or not site_id.strip()):
        raise errors.ConfigError(
            "config.json has an unusable siteId. Run scripts/configure --site.")

    custom_ca_path = body.get(CUSTOM_CA_PATH)
    if custom_ca_path is not None:
        if not isinstance(custom_ca_path, str) or not custom_ca_path.strip():
            raise errors.ConfigError(
                "config.json has an unusable customCaPath.")
        if len(custom_ca_path) > CA_PATH_MAX_CHARS:
            raise errors.ConfigError("config.json's customCaPath is too long.")

    allow_insecure_tls = body.get(ALLOW_INSECURE_TLS, False)
    if allow_insecure_tls is None:
        allow_insecure_tls = False
    if not isinstance(allow_insecure_tls, bool):
        # Not coerced. A string "false" is truthy in most languages' idea of a
        # cast, and silently disabling TLS verification is not a thing to guess
        # at (SEC-005).
        raise errors.ConfigError(
            "config.json's allowInsecureTls must be true or false.")

    return Config(api_root.strip(),
                  site_id.strip() if isinstance(site_id, str) else None,
                  custom_ca_path.strip() if isinstance(custom_ca_path, str) else None,
                  allow_insecure_tls)
