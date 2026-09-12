# Setup wizard implementation

Implemented `scripts/setup` and `scripts/setup.py`, using the existing TLS
context builder, bounded transport, pagination, version gate and configuration
transaction. Authenticated requests always verify TLS. Unfamiliar certificates
are retrieved without credentials and require an independently verified SHA-256
fingerprint. Certificate files are written completely before atomic publication
under immutable content-derived names.

The wizard discovers sites and validates device-list access before saving. It
passes the key to configure through stdin, enables the plugin and places the
widget on the bar. Nondefault configuration directories require `--skip-bar`.
Shell-placement failures report that configuration has already been saved.
It reports API verification separately from the user's visual panel check.

Added non-interactive agent instructions and made the wizard the README's
primary setup flow. Manual certificate/configuration guides remain available.

## Validation

- 17 setup tests pass on Python 3.9.25 and 3.14.7.
- Real loopback TLS tests cover approved/refused fingerprints, hostname mismatch,
  redirects, unsupported versions and configuration writes through the existing
  transaction. Other tests cover hidden-input fallback refusal, site selection,
  secret stdin, bar failure and interrupted certificate-file recovery.
- `bash tests/run.sh --gates`: SUITE PASS.
- Documentation links, heading anchors and Bash examples checked.
- `git diff --check`: passed.

A local independent review found that interrupted certificate writes could
poison their final filenames. Atomic publication fixed the issue and regression
tests cover failed writes and preservation of existing certificate contents.
External Claude review was rejected by automatic approval review because it
could export repository contents; no external review was performed.

No live controller requests, installation or desktop configuration changes were
made during development. Hostname resolution and private-CA repair remain
manual when the wizard cannot establish verified TLS. The wizard checks API
access, not the rendered panel or the shell's first scheduled refresh.
