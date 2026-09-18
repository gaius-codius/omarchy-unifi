# Agent instructions

How an AI agent should install, set up and update this plugin for a user.

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
the specific blocker and use the
[name resolution and certificate guide](controller-setup.md).
Do not disable TLS verification, rewrite host resolver settings, or approve
certificate trust on the user's behalf.

Preserve any pre-existing configuration on failure. If setup saves configuration
but cannot place the widget, report that partial result rather than repeating
credential setup. Retain the user's original credential file unless they ask
you to remove it. Never print it during diagnosis.

## Update the plugin

```bash
omarchy plugin update gaius-codius.unifi
```

It shows the incoming changes and asks the user to confirm them. That review is
theirs to make: the new code runs in their shell with access to their API key.
Do not pass `--yes` to skip it unless the user asks you to.

An agent's shell usually has no terminal, so the command prints the diff and
then stops with `refusing to continue without confirmation; pass --yes`. That is
expected. Do not follow the suggestion. Give the user the command above and ask
them to run it in their own terminal, where they can review the changes and
confirm. `omarchy plugin remove` behaves the same way.

When it reports `Updated gaius-codius.unifi.`, tell the user the shell is about
to restart, then run:

```bash
omarchy restart shell
```

New code loads only after the restart. Configuration and bar-layout changes
apply without one.

An update replaces only the plugin folder. The controller address, API key,
trusted certificate and site selection live in `~/.config/omarchy-unifi/` and
carry over, so do not rerun setup afterwards.

If the update doesn't complete:

- **`is up to date`** — there is nothing to install and no restart is needed.
- **`cannot fast-forward … you have local changes`** — despite the wording, this
  can have more than one cause. Start with
  `git -C ~/.config/omarchy/plugins/gaius-codius.unifi status`. If it lists
  changed files, the plugin folder was edited: show the user
  and let them decide what to keep, and do not discard their changes with a
  reset or checkout. If it is clean, either the user committed changes inside
  the plugin folder or the upstream history was rewritten, and a clean status
  cannot tell those apart. Ask the user. Only if they have never committed there,
  reinstall as described for `is not a git checkout` below: reinstalling deletes
  the folder, including any commits in it. Configuration lives outside the
  plugin folder and is kept either way.
- **`failed validation; rolled back`** — the previous version is still installed
  and working. Report the failure. Do not retry with `--yes` or try to bypass
  validation.
- **`is not a git checkout`** — the plugin was copied into place rather than
  added with `omarchy plugin add`, so it can't update itself. With the user's
  agreement, run `omarchy plugin remove gaius-codius.unifi` and then the two
  commands under [Install the plugin](#install-the-plugin). Configuration in
  `~/.config/omarchy-unifi/` is kept, so setup doesn't need to run again. If the
  widget doesn't reappear in the bar, run
  `omarchy bar put gaius-codius.unifi --section right`.
