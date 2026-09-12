# Install with an agent

Use the setup wizard to handle credentials, certificates and site selection.
It uses the existing read-only helper and configuration transaction. Do not
replace it with ad hoc HTTP requests or edits to credential files.

## Install the plugin

```bash
omarchy plugin add https://github.com/gaius-codius/omarchy-unifi
omarchy plugin enable gaius-codius.unifi
```

Then run the wizard in a terminal the user can interact with:

```bash
~/.config/omarchy/plugins/gaius-codius.unifi/scripts/setup
```

Let the user enter the API key directly at the hidden prompt. Prefer a dedicated
key for this plugin. Do not ask them to paste it into the conversation, put it
in a command argument, or export it to the environment.

## Non-interactive setup

Use this only when the controller address, credential file and any required
trust decision are already supplied. `--api-key-file` names a private local
file containing the key; the key itself must not appear in the command.

```bash
~/.config/omarchy/plugins/gaius-codius.unifi/scripts/setup \
  --non-interactive --controller unifi.local \
  --api-key-file /path/to/private-key-file
```

For a self-signed certificate, obtain the SHA-256 fingerprint through an
independent trusted source and have the user approve it. Then add
`--trust-fingerprint <sha256>`. A fingerprint retrieved from the same untrusted
connection is not independent verification. Never fill this argument with an
observed fingerprint just to make setup succeed.

If multiple sites are available, have the user choose and add `--site <uuid>`.
Do not choose the first site arbitrarily. A sole site is selected automatically.
Use `scripts/setup --help` for the full interface.

## What to check

- The controller uses a supported Network version and exposes the Integration API.
- The controller hostname resolves and is covered by the certificate. A matching
  fingerprint alone doesn't fix a hostname mismatch or expired certificate.
- The wizard reports that its connection check and configuration save succeeded.
- Bar placement succeeded, or the user has the explicit command to finish it.
- The panel shows the selected site and a recent refresh. A successful API check
  does not prove the QML panel rendered correctly.

If the certificate cannot be verified or the hostname doesn't resolve, explain
the specific blocker and use the [certificate guide](controller-setup.md).
Do not disable TLS verification, rewrite host resolver settings, or approve
certificate trust on the user's behalf.

Preserve any pre-existing configuration on failure. If setup saves configuration
but cannot place the widget, report that partial result rather than repeating
credential setup. Retain the user's original credential file unless they ask
you to remove it. Never print it during diagnosis.
