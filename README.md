# omarchy-unifi

An Omarchy bar plugin that shows UniFi Network site health: a status glyph on
the bar and a popup panel with WAN state, device counts, and which devices are
offline. It is **read-only** — it uses six kinds of GET request and never changes
anything on your controller.

> **Status: pre-release.** Through Phase 12b and Phase B0 of
> `docs/feature-specs/omarchy-unifi-plugin/PLAN.md`. The helper, the service and
> the panel are written, tested, and have been run installed in a real Omarchy
> shell against a real UniFi controller (Network 10.6.101) — so the API key
> header, the route set, the record shapes and the supported-version matrix are
> confirmed against hardware rather than only against a stub.
>
> Still to come: the v1.1 **Devices and Clients** views
> (`SPEC-v1.1-browse.md`, phases B1-B4), and Phase 13 — manual QA, packaging
> and release.

## What it shows

**On the bar:** one glyph, coloured by overall site health using Omarchy theme
tokens only — there are no hard-coded colours, so it follows your theme,
including light ones. The theme exposes no green/amber/red, so the four health
levels are four theme-native *visual* levels rather than four hues: the plain
foreground when healthy, the foreground plus a small urgent dot when degraded,
the urgent colour when every gateway is down, and dimmed when the reading is
unknown or too old to trust. Colour is never the only signal — the tooltip and
the panel always say the condition in words. Optionally, the connected client
count sits beside the glyph.

A small hollow ring in the opposite corner means the *last refresh* failed while
the reading you are looking at is still current. That is deliberately a
different shape in a different place from the degraded dot, because "I could not
reach the controller" and "the controller says something is wrong" are different
problems.

**In the panel:** the site name and how long ago it last updated; WAN state with
the gateway's uptime and current throughput; every gateway listed individually;
the connected client count and the number of adopted devices; per-role counts in
each of the five device states; the devices that are down or impaired, with an
honest "and N more" when there are more than fit; and any warnings the last
fetch raised, each with its code so it is searchable.

When something is wrong, the panel says which of twenty-four conditions it is
and names the single action that fixes it. A metric the controller did not
report reads "unknown" — never `0`.

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

Run `scripts/configure` to create them:

```bash
printf '%s' "$YOUR_API_KEY" | scripts/configure \
  --api-root https://<console-ip>/proxy/network/integration --api-key-stdin
```

The key is read from standard input or from `--api-key-file`, never from a
command-line value: argv is world-readable through `/proc`, so an option that
took the key as a value would publish it to every user on the machine for as
long as the command ran. It is read by the Python helper directly from
`api-key` — never placed in `shell.json`, in an environment variable, on a
command line, or anywhere in this repository.

If your controller has more than one site, the first run reports which ones it
found; pick one with `scripts/configure --site <uuid>`. A controller with
exactly one site is selected automatically.

Other options:

| Option | Effect |
|---|---|
| `--site <uuid>` | choose which site to display |
| `--custom-ca <file>` | trust this PEM certificate authority instead of the system store |
| `--no-custom-ca` | go back to the system trust store |
| `--allow-insecure-tls` | **disable** TLS verification; the panel then shows a permanent warning |
| `--verify-tls` | re-enable TLS verification |
| `--commit` | re-validate the files as they are on disk and write a fresh commit marker, after editing `config.json` by hand |

After writing the files, `configure` asks the running shell to reload. It exits
0 when the change was applied, and also when there is no shell running or the
widget is not on the bar yet — both mean the configuration is saved and will be
picked up. It exits non-zero if the shell was reachable but could not accept
the change, and in that case the previous configuration is left exactly as it
was, so a failed run never leaves you half-configured.

#### Pointing it at your controller

A UniFi console's certificate is self-signed and issued for the name
**`unifi.local`** — not for its IP address. So configuring it by IP cannot use
TLS verification, however you pin the certificate: the chain is trusted and the
hostname check still fails with `IP address mismatch`.

Use the name, and pin the certificate:

```bash
# 1. make the name resolve, if it does not already
echo '192.168.1.1  unifi.local' | sudo tee -a /etc/hosts

# 1b. on Arch, /etc/hosts is NOT consulted for .local names by default:
#     nss-mdns claims them first and [NOTFOUND=return] ends the search.
#     Put `files` ahead of it. Check with: getent hosts unifi.local
sudo cp /etc/nsswitch.conf /etc/nsswitch.conf.bak
sudo sed -i 's|^hosts:.*|hosts: mymachines files mdns_minimal [NOTFOUND=return] resolve myhostname dns|' \
  /etc/nsswitch.conf

# 2. save the console's certificate
openssl s_client -connect 192.168.1.1:443 </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ~/.config/omarchy-unifi/console.pem

# 3. configure
scripts/configure \
  --api-root https://unifi.local/proxy/network/integration \
  --custom-ca ~/.config/omarchy-unifi/console.pem \
  --api-key-stdin
```

That gives verified TLS with the console's own certificate pinned, which is
stronger than the system trust store would be — nothing but that certificate is
accepted.

Your console may advertise `unifi.local` over mDNS already, in which case steps
1 and 1b are unnecessary — check with `getent hosts unifi.local` first.

If the name cannot be made to resolve on your network, `--allow-insecure-tls`
turns verification off. It works, and the panel then shows a permanent warning
row for the whole session, because anything on the network path can read and
alter what you are looking at.

## Widget settings

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

### What you will and will not see

**Your controller may not report a gateway.** The plugin identifies a gateway by
the `gateway` entry in a device's `features` array, which is what the UniFi
Network API documents. On the one real console this has been tested against —
a UDM Pro on Network 10.6.101 — no device reports it: the console itself comes
back as `["switching"]`. Where that happens, the WAN section reads "unknown",
gateway uptime and throughput are blank, and the bar item can never reach the
"down" level, because that level is defined as *every gateway down* and there
are no gateways to be down.

Everything else works normally: device counts, per-role counts, the offline
list, the client count, and the degraded level when something is down or
impaired. This is tracked as DEV-6 in
`docs/feature-specs/omarchy-unifi-plugin/DEVIATION_LOG.md`, and more data from
more controllers is exactly what it needs.

## Troubleshooting

**After updating the plugin, restart the shell.**

```bash
omarchy restart shell
```

Omarchy watches `~/.config/omarchy/plugins/` and reloads a plugin when its files
change, and the reload genuinely happens — the old service is torn down and a
new one built. But the new one is built from the *previously compiled* source,
so a code change does not take effect until the shell restarts. Everything else
— your configuration, the bar layout, your credential — is picked up live.

**The panel says the configuration has changed but not been committed.** Run
`scripts/configure --commit`. That happens when `config.json` or `api-key` is
edited by hand rather than through the script.

**Two UniFi widgets disagree.** If you have the widget on the bar twice with
different `refreshIntervalSec` values, polling stops and the panel says so.
Make them match or remove one, in `~/.config/omarchy/shell.json`.

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
