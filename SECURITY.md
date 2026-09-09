# Security policy

This plugin holds a UniFi Network API key and makes authenticated HTTPS requests
to a device on your network, so a flaw here can cost you a credential rather
than a crash. Please report suspected vulnerabilities privately.

## Reporting

Use GitHub's private vulnerability reporting:
**[Security → Report a vulnerability](https://github.com/gaius-codius/omarchy-unifi/security/advisories/new)**.

That opens a private advisory visible only to the maintainer. Please do **not**
open a public issue for a security report.

Include, if you can: what you observed, how to reproduce it, and which versions
of the plugin, Omarchy and UniFi Network you were running.

**Never include your API key, a packet capture containing it, or an unredacted
console certificate in a report.** If a report requires showing that a
credential leaked somewhere, describe the location — the log line, the file, the
field — rather than pasting the value.

You should get an acknowledgement within a week. This is a single-maintainer
hobby project, not a vendor with an on-call rotation; please set expectations
accordingly. There is no bug bounty.

## What is in scope

- Any path by which the API key reaches somewhere it should not. The rule the
  code is built to is that the key appears only in `~/.config/omarchy-unifi/api-key`
  and in the `X-API-Key` header on the wire — never in Git, `manifest.json`, QML,
  `shell.json`, an environment variable, a command line, a log, an exception, a
  test output, or a panel message.
- Defeating TLS verification, or getting a certificate trusted that should not be.
- Reaching a URL, host, or HTTP method outside the six read-only GET routes the
  helper allows.
- Getting the helper to execute anything, write anywhere outside its config
  directory, or read a file it was not pointed at.
- Personal data — client names, IP addresses, MAC addresses — appearing anywhere
  other than the rendered panel and, on explicit user action, the clipboard.
- Anything a hostile or compromised controller can do to the machine running the
  plugin: unbounded memory, a wedged helper, a crash loop.

## What is out of scope

- The security of your UniFi console itself, or of Ubiquiti's API.
- `--allow-insecure-tls` behaving insecurely. It is documented as disabling
  verification, it warns persistently in the panel, and it is opt-in.
- Someone with write access to your `~/.config/` already having lost the game.
- Findings that require an attacker to already be root, or to already be you.

## Known design limits

These are deliberate, documented, and not vulnerabilities in themselves — though
a way to *exploit* one beyond its stated boundary would be:

- **Pinning a self-signed console certificate is trust-on-first-use.** The
  README's procedure captures whatever the console presents at that moment, so
  it asks you to compare the fingerprint against the console UI before
  configuring. An attacker on-path during that one step can have their
  certificate pinned.
- **The API key is stored on disk at mode `0600`,** unencrypted, in
  `~/.config/omarchy-unifi/api-key`. There is no keyring integration. Anyone who
  can read your home directory as you can read the key.
- **The plugin is read-only** — it issues only GETs and can change nothing on
  your controller. A key scoped to read-only in the UniFi console limits the
  blast radius further, and is recommended.
