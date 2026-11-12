# Spec addendum v1.2 — connecting by IP address

**Status: CONFIRMED 2026-11-12** by the user, who asked for the plugin to stop
depending on `unifi.local`. `SPEC.md` (spec-v1, frozen 2026-09-05) is unchanged
except where this document says otherwise. Gates, autonomy rules and the other
security requirements in `SPEC.md` §10 and §15 apply unchanged.

## 1. Why

A stock UniFi console presents a self-signed certificate naming `unifi.local`,
`localhost` and loopback addresses, and never its LAN address
(`api-contract.md`, "The IP form cannot be verified"). Until now, verified TLS
therefore required `unifi.local` to resolve.

On Omarchy that breaks on every `omarchy-settings` upgrade. The package copies
its own `/etc/nsswitch.conf` over the system's with no opt-out, and its
`hosts:` line puts `mdns_minimal [NOTFOUND=return]` ahead of `files`, so an
`/etc/hosts` entry for a `.local` name is never read. A static
`/etc/avahi/hosts` entry was tried on 2026-11-11 and failed: avahi refused to
publish the name (`Host name conflict for "unifi.local"`).

Connecting by IP address removes name resolution from the connection entirely.

## 2. Requirement

**SEC-014:** When the `apiRoot` host is an IPv4 literal **and** a
`customCaPath` is configured **and** `allowInsecureTls` is false, the helper
and the setup wizard replace the library's hostname check with an identity
check:

1. The certificate chain is still verified (`CERT_REQUIRED`, expiry included)
   against the configured file and nothing else. Only `check_hostname` is off.
2. After the handshake, and before any HTTP byte is written, the server's leaf
   certificate must be byte-for-byte one of the certificates in the configured
   file (compared by SHA-256 of the DER), **or** carry an IP-address
   subjectAltName equal to the address dialled.
3. The check runs inside the socket class the context hands out, so no caller
   can obtain the relaxed context without it. That context refuses a deferred
   handshake and refuses to wrap a BIO, since either would let the handshake
   complete without the check.

IPv6 literals are not covered yet: `routes.build` rejects a bracketed host
(a limit that predates this addendum), so an IPv6 `apiRoot` fails closed before
any connection. `tlsctx` already selects the pinned mode for one.

The pinned context also sets `OP_NO_RENEGOTIATION`, so a TLS 1.2 renegotiation
cannot present a different certificate after the check.

Every other case is unchanged: a hostname keeps the library's name check, an IP
literal with system trust keeps it too, and `allowInsecureTls` is unchanged.

Rule 2's second half preserves what a private-CA user with an IP SAN already
had. Without rule 2, turning off the name check would let a pinned private CA
vouch for every certificate it ever issued, for any name; that is the case the
tests are built around.

## 3. Setup wizard amendments

These amend `docs/feature-specs/setup-wizard/SPEC.md`:

- **REQ-001:** the default controller address is the IPv4 default gateway, read
  from `/proc/net/route`, instead of `unifi.local`. With no default route there
  is no default and an empty answer is refused.
- **REQ-003:** "Verify hostname and certificate" reads, for an IP address
  covered by SEC-014, as "verify the certificate against the approved pin".
  Verification is still never disabled on an authenticated request.
- The unresolvable-name message leads with the IP address and drops the
  `/etc/hosts` and `/etc/nsswitch.conf` steps, which the next Omarchy update
  would undo.

## 4. Acceptance

All AUTO, in `tests/test_unifi_status.py` (`PinnedByAddress`) and
`tests/test_setup.py`:

- **AC-IP1:** a console-style certificate (no IP SAN), pinned, is accepted by
  IP; the same connection with the library's name check fails (the control).
- **AC-IP2:** a pinned private CA does not vouch, by IP, for a leaf that does
  not name the address, or that names a different one. Mutation-checked:
  removing the pin check fails these.
- **AC-IP3:** through `transport.get_json`, a leaf from a pinned private CA that
  does not name the address fails as kind `tls`, and the server receives no
  request, so no `X-API-Key` is sent. Mutation-checked. (An impostor
  self-signed certificate is also refused, but by chain verification, before
  the pin is reached.)
- **AC-IP4:** a hostname, an IP without a configured file, and
  `allowInsecureTls` keep their previous behaviour.
- **AC-IP5:** setup approves a console-style certificate by IP with a matching
  fingerprint, and the prompt offers the default gateway.

**AC-IP6 (MAN): PASSED 2026-11-12.** With G-CONTROLLER approval, one
read-only `GET /v1/info` to the real console by IP address, pinned to its
stock certificate (which names only `unifi.local` and loopback), returned
`applicationVersion: 11.0.99`. The pinned context was in use
(`check_hostname` false).
