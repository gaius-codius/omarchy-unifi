"""The only `ssl.SSLContext` call site (SEC-005/006/007/007a)."""

# WHY THIS FILE DOES NOT USE THE CONVENIENCE CONSTRUCTOR
#
# `ssl.create_default_context()` is never called, from any path, including the
# `allowInsecureTls` one. That is not a style preference. CPython reads
# SSLKEYLOGFILE from the environment and assigns `context.keylog_filename`
# *during construction* — verified against the 3.14 source, where an unwritable
# path raises OSError from inside the constructor. Clearing keylog_filename
# afterwards is too late: the file has already been created and opened, and
# every session key for a request carrying X-API-Key goes into it.
#
# tests/lint/no_default_ssl_context.sh enforces the absence by grep over
# helper/ and scripts/, because the failure is invisible at runtime — the
# handshake succeeds, the request works, and the keys are simply also written
# somewhere else.
#
# That gate reads source lines and skips `#` comments, which is why this
# explanation is a comment block rather than the module docstring: a docstring
# is a string literal, so the gate would flag the paragraph above for naming
# the function it forbids. Rewording it to dodge the gate would have deleted
# the one explanation a future reader needs, and exempting the file would have
# put a hole exactly where somebody routing around the rule would look.
#
# The environment scrub lives INSIDE build_context, unconditionally, before the
# constructor. A scrub the caller has to remember is a scrub that a future code
# path will skip, and SSL_CERT_FILE is not hygiene: OpenSSL honours it when
# default trust is loaded, so an inherited value pointing at a writable
# attacker CA hands the credential to a man-in-the-middle while
# `allowInsecureTls` is still false.

import os
import ssl

from . import errors

# SEC-007a. All three, always, before any context exists.
#
# SSLKEYLOGFILE  writes session keys, which decrypt the credential-bearing
#                request for anyone who can read the file.
# SSL_CERT_FILE  and
# SSL_CERT_DIR   are honoured by OpenSSL when default trust is loaded, so either
#                one can substitute an attacker's trust anchor entirely.
SCRUBBED_ENV_VARS = ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR")


def scrub_environment(environ=None):
    """Remove the three TLS environment variables. Returns the names removed."""
    target = os.environ if environ is None else environ
    removed = []
    for name in SCRUBBED_ENV_VARS:
        if name in target:
            del target[name]
            removed.append(name)
    return removed


def build_context(allow_insecure=False, ca_pem=None):
    """Build the one kind of TLS context this helper uses.

    `ca_pem` is the CA bundle's *captured text* (SEC-006), never a path: a
    pathname that was validated and then handed back to the SSL library to
    reopen is a TOCTOU window with extra steps.
    """
    # Unconditional, and first. Not `if not scrubbed_yet` and not the caller's
    # job — this is the line that makes "no context is ever built from a
    # hostile environment" a property of the code rather than of a convention.
    scrub_environment()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    # TLS 1.2 floor. Below it the negotiated ciphers are ones nobody should be
    # protecting a credential with, and a UniFi controller that cannot manage
    # 1.2 is old enough that the version gate will reject it anyway.
    context.minimum_version = ssl.TLSVersion.TLSv1_2

    if allow_insecure:
        # UX-009 raises a persistent warning for this; it is an explicit opt-in,
        # not a fallback, and nothing here decides to take it.
        #
        # ORDER MATTERS: `verify_mode = CERT_NONE` raises while `check_hostname`
        # is still True, so hostname checking is disabled first.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        # Still built by the same constructor. An insecure context created by
        # `create_default_context()` would write session keys exactly like a
        # verifying one, and the path most likely to be taken during
        # troubleshooting is the last place to relax this.
        return context

    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    # Set explicitly rather than inherited, per SEC-007. VERIFY_X509_STRICT is
    # deliberately NOT added: UniFi controllers commonly present certificates
    # that fail strict encoding checks, and rejecting them would push users to
    # `allowInsecureTls`, which is far worse than accepting a loosely encoded
    # certificate that still chains to a trusted root.
    context.verify_flags = ssl.VERIFY_DEFAULT

    # SEC-007a: trust is loaded EXPLICITLY in every case, after the scrub.
    if ca_pem is not None:
        try:
            context.load_verify_locations(cadata=ca_pem)
        except ssl.SSLError as exc:
            raise errors.TlsError(
                "The configured CA file is not usable as a trust anchor (%s)." % exc)
    else:
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)

    return context
