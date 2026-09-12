# Feature spec: setup wizard

Date: 2026-09-12. Scope approved in the installation-wizard conversation.

## Problem and goals

Manual credential, certificate and site setup makes installation harder than it
needs to be. Provide an interactive wizard and a predictable agent interface
using the existing configuration transaction and read-only API client.

## Requirements

- REQ-001: Ask for controller address (default `unifi.local`) and hidden API key.
  Accept an API-key file for non-interactive use; never accept the key as argv.
- REQ-002: Verify ordinary system-trusted TLS automatically. For unfamiliar
  certificates, retrieve without credentials, display SHA-256 fingerprint and
  require independent user verification or a previously approved fingerprint.
- REQ-003: Verify hostname and certificate before authenticating. Never disable
  verification on authenticated requests; never follow redirects.
- REQ-004: Discover supported version and sites with the existing bounded client.
  Select a sole site automatically; prompt for multiple sites or accept a UUID.
- REQ-005: Verify access before saving; write through `scripts/configure` using
  secret stdin. Keep existing settings and trust intact on failed setup.
- REQ-006: Enable/add the installed widget and accurately report connection and
  bar-placement results. Do not claim a rendered panel was tested by an API call.
- REQ-007: Provide agent instructions with a non-interactive path that cannot
  approve unknown trust, choose an ambiguous site or echo an API key by default.

## Boundaries and compatibility

Python 3.9+, standard library and existing Omarchy tools. No new runtime Python
packages. Preserve configure's non-interactive interface, configuration format
and helper version gate. Never change controller settings or host DNS/hosts files.
A controller address and trust decision may still be required; an API key alone
cannot identify a controller. The plugin must be installed before bar placement.

## Acceptance and tests

AC-001: Known-trust single-site setup succeeds with no manual file creation.
AC-002: Refused/mismatched fingerprints and hostname mismatches never send a key.
AC-003: Multiple sites require explicit selection; unsupported versions fail.
AC-004: Failed connection does not replace a working configuration or certificate.
AC-005: Keys do not appear in child argv, environment or diagnostic output.
AC-006: Non-interactive missing input fails clearly rather than prompting.
AC-007: Bar placement failure is reported separately from saved configuration.

Validate using isolated configuration directories, mocks and local TLS stubs,
including both supported Python interpreters and the existing regression suite.
Do not run against the user's live controller or alter their installed plugin
while developing this feature.

## Autonomy

The user authorised implementation of the proposed wizard and agent guide.
Routine implementation and test choices are delegated. Expanding scope to trust
bypasses, automatic host configuration changes or live controller mutations
requires a new user decision.
