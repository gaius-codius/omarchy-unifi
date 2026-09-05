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
