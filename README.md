# omarchy-unifi

An Omarchy bar plugin that shows UniFi Network site health: a status glyph on
the bar and a popup panel with WAN state, device counts, and which devices are
offline. It is **read-only** — it issues six GET requests and never changes
anything on your controller.

> **Status: pre-release.** Phase 0 of `docs/feature-specs/omarchy-unifi-plugin/PLAN.md`.
> The manifest, entry-point stubs, and the full gate and test harness are in
> place. The service, panel, and helper are not written yet, so installing this
> today gets you a widget that does nothing.

## Requirements

- Omarchy 4.0.2-1 with Quickshell 0.3.1-1
- **Python 3.9 or newer**, standard library only

There are no pip packages and no other runtime dependencies. Omarchy's plugin
installer does not install runtime packages, so the interpreter must already be
present; `scripts/configure` checks for it and reports a clear message rather
than failing obscurely if it is missing or too old.

A UniFi controller exposing the **Network Integration API**, and an API key for
it, created in the UniFi console.

## Install

```bash
omarchy plugin add https://github.com/<you>/omarchy-unifi
omarchy plugin enable gaius-codius.unifi
```

The plugin does nothing until the widget is placed on the bar — that is what
creates the service. Add it through the Omarchy bar settings, or by adding
`{"id": "gaius-codius.unifi"}` to a section of `bar.layout` in
`~/.config/omarchy/shell.json`.

## Configure

Controller details and the credential live outside the plugin folder, in
`~/.config/omarchy-unifi/` (mode `0700`):

| File | Contents |
|---|---|
| `config.json` | `apiRoot`, `siteId`, optionally `customCaPath` and `allowInsecureTls` |
| `api-key` | the API key and nothing else |
| `commit.json` | written by `scripts/configure`; do not edit by hand |

Run `scripts/configure` to create them. The API key is read by the Python helper
directly from that file — it is never placed in `shell.json`, in an environment
variable, on a command line, or anywhere in this repository.

### Widget settings

Omarchy 4.0.2 ships no settings-form renderer, so these are set by editing your
`shell.json` layout entry or with `omarchy shell setBarWidget`:

| Key | Default | Range |
|---|---|---|
| `refreshIntervalSec` | `30` | 15–3600 |
| `compactMetric` | `"none"` | `"none"`, `"clients"` |
| `dashboardUrl` | derived from `apiRoot` | `http` or `https` only |

```json
{ "id": "gaius-codius.unifi", "refreshIntervalSec": 60, "compactMetric": "clients" }
```

An out-of-range or wrongly typed value falls back to the default and raises a
warning in the panel rather than being used.

## Removing it

```bash
omarchy plugin disable gaius-codius.unifi
omarchy plugin remove gaius-codius.unifi
```

**This does not remove your credential.** `~/.config/omarchy-unifi/` survives
the uninstall and still contains the API key, so:

```bash
rm -rf ~/.config/omarchy-unifi
```

**Then revoke the API key in the UniFi console.** Deleting the local copy does
not invalidate the credential — anyone who obtained it before you deleted it can
still use it.

## Development

```bash
mise install          # pins gitleaks, node, and both supported Python versions
tests/run.sh          # everything that runs without a graphical session
tests/run.sh --gates  # additionally prove each lint gate fails on a seeded violation
```

`tests/run.sh --live-harness` adds the Quickshell harness, which needs a live
Wayland session. `--live-staged` is refused unless explicitly authorised,
because it installs into `~/.config/omarchy/plugins/`.

`docs/feature-specs/omarchy-unifi-plugin/` holds the spec, the phased plan, the
verified host and API contracts, and the deviation log. `CLAUDE.md` says which
to read first.

## Licence

MIT. See `LICENSE`.
