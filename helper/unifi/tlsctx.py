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

import hashlib
import ipaddress
import os
import re
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


# SEC-014: CONNECTING BY IP ADDRESS
#
# A stock UniFi console ships a self-signed certificate naming `unifi.local`
# and loopback addresses, never its LAN address (api-contract.md, "The IP form
# cannot be verified"). Requiring that name meant every user had to make
# `unifi.local` resolve, and on Omarchy that breaks on every update, because
# omarchy-settings rewrites /etc/nsswitch.conf.
#
# When the apiRoot host is an IP literal AND a certificate file is pinned, the
# name check is replaced by an identity check that is at least as strict:
#
#   1. The chain is still verified against the pinned file and nothing else
#      (CERT_REQUIRED, expiry included). Only the name check is off.
#   2. After the handshake, and before a byte of HTTP is written, the server's
#      leaf must be byte-for-byte one of the certificates in the pinned file,
#      OR carry an IP-address SAN equal to the address that was dialled.
#
# Rule 2's first half is the pin: the handshake proves the server holds that
# certificate's private key, so it is the console whatever address answered.
# Rule 2's second half keeps a private CA with an IP SAN working exactly as it
# did when the check was the library's.
#
# Turning check_hostname off WITHOUT rule 2 would be a hole, not a shortcut: a
# pinned private CA would then vouch for every certificate it ever issued, for
# any name. That is why the check lives in the socket class the context hands
# out, so no caller can get the relaxed context without it.

PEM_CERT_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")


def is_ip_literal(host):
    """True for an IPv4 or IPv6 address, as `urlsplit().hostname` gives it."""
    if not isinstance(host, str) or not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def pinned_fingerprints(ca_pem):
    """SHA-256 of every certificate in the captured PEM text, as hex."""
    pins = set()
    for block in PEM_CERT_RE.findall(ca_pem):
        try:
            der = ssl.PEM_cert_to_DER_cert(block)
        except ValueError:
            raise errors.TlsError(
                "The configured CA file holds a certificate that cannot be read.")
        pins.add(hashlib.sha256(der).hexdigest())
    if not pins:
        raise errors.TlsError("The configured CA file holds no certificate.")
    return frozenset(pins)


def _ip_san_matches(peer, address):
    """True when the verified peer certificate names `address` as an IP SAN."""
    try:
        wanted = ipaddress.ip_address(address)
    except ValueError:
        return False
    for kind, value in (peer or {}).get("subjectAltName", ()):
        if kind != "IP Address":
            continue
        try:
            if ipaddress.ip_address(value.strip()) == wanted:
                return True
        except ValueError:
            continue
    return False


class _PinnedSocket(ssl.SSLSocket):
    """Rule 2 of SEC-014, run at the end of every handshake."""

    def do_handshake(self, *args, **kwargs):
        super(_PinnedSocket, self).do_handshake(*args, **kwargs)
        der = self.getpeercert(binary_form=True)
        if der is not None and hashlib.sha256(der).hexdigest() in self.context.pins:
            return
        if der is not None and _ip_san_matches(self.getpeercert(), self.server_hostname):
            return
        # The message is fixed: sanitize.describe_exception discards it anyway,
        # and nothing about the presented certificate is worth echoing.
        raise ssl.SSLCertVerificationError(
            "certificate is not the pinned certificate and does not name this address")


class _PinnedContext(ssl.SSLContext):
    """A client context whose sockets enforce SEC-014 after the handshake.

    Built by the same direct constructor as every other context here, so
    SEC-007 holds: `keylog_filename` is never assigned.
    """

    sslsocket_class = _PinnedSocket

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                    suppress_ragged_eofs=True, server_hostname=None, session=None):
        # A deferred handshake would let the first read or write complete it
        # inside OpenSSL, past the override that checks the pin.
        if not do_handshake_on_connect:
            raise errors.InternalError(
                "A pinned connection must complete its handshake on connect.")
        return super(_PinnedContext, self).wrap_socket(
            sock, server_side=server_side, do_handshake_on_connect=True,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=server_hostname, session=session)

    def wrap_bio(self, *args, **kwargs):
        # SSLObject has no handshake hook to put the pin on.
        raise errors.InternalError("A pinned context cannot wrap a BIO.")


def build_context(allow_insecure=False, ca_pem=None, host=None):
    """Build the one kind of TLS context this helper uses.

    `ca_pem` is the CA bundle's *captured text* (SEC-006), never a path: a
    pathname that was validated and then handed back to the SSL library to
    reopen is a TOCTOU window with extra steps.

    `host` is the apiRoot host the context will connect to. It only selects
    the mode: an IP literal with `ca_pem` gets SEC-014's pinned check, and
    anything else gets the library's hostname check, as before.
    """
    # Unconditional, and first. Not `if not scrubbed_yet` and not the caller's
    # job — this is the line that makes "no context is ever built from a
    # hostile environment" a property of the code rather than of a convention.
    scrub_environment()

    pinned = (not allow_insecure and ca_pem is not None and is_ip_literal(host))
    constructor = _PinnedContext if pinned else ssl.SSLContext
    context = constructor(ssl.PROTOCOL_TLS_CLIENT)

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

    # SEC-014 replaces the name check with the pin; see the block above. The
    # chain is still verified either way.
    context.check_hostname = not pinned
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

    if pinned:
        context.pins = pinned_fingerprints(ca_pem)
        # The pin is checked once, after the first handshake. A TLS 1.2
        # renegotiation could present a different certificate afterwards.
        context.options |= ssl.OP_NO_RENEGOTIATION

    return context
