# Usage and settings

## Status indicators

The icon uses Omarchy theme colours; the tooltip and panel also describe the
condition in words.

| Indicator | Meaning |
|---|---|
| Normal foreground | Healthy |
| Foreground with an urgent dot | Degraded |
| Urgent colour | All reported gateways are down |
| Dimmed | Unknown or stale reading |
| Short bar in the opposite corner | The last refresh failed, but the displayed reading is still current |

A refresh failure and a reported network problem are separate states. The
[known gateway limitation](../README.md#before-you-install) can prevent the
“all gateways down” state from being available.

## Browse devices and clients

Overview shows the site's last update, device and client counts, available WAN
metrics, offline or impaired devices, and warnings with searchable codes. When
nothing is offline it says so, rather than leaving the section out. Every row
under Inventory opens a page: **Connected clients** opens Clients and
**Adopted devices** opens Devices, both unfiltered, and a device-role count
opens Devices filtered to that role. These rows carry a `›` and highlight
under the pointer.

The Gateways section appears only on sites with more than one gateway. With a
single gateway its figures are the same ones Uplink already shows, so only its
name is kept, beside the Uplink heading — with the model after it when the name
doesn't already say it.

Device rows expand to show firmware and update availability, addresses, CPU,
memory, throughput, upstream and downstream connections, client counts, and
port or radio information. Ports are drawn as one mark each — filled when the
link is up, outlined when it is down, underlined when the port carries PoE —
with the same counts written out beside them. Client rows show addresses, MAC
addresses, access type and connection time. Availability depends on what the
controller reports.

A device's status word is printed only when it is not "Online", so a healthy
list leaves that column empty and anything wrong is the only thing in it.
Clients always show their connection type, because neither wired nor wireless
is the expected value.

Search matches text without regard to case; it doesn't use fuzzy matching.
Devices filter by role, and Clients by connection type. Filters stay active
until cleared. Refreshes preserve the list's scroll position; changing the
search or filter returns it to the top.

If a list is bounded, the panel shows both the visible count and the reported
total. Derived counts may say “14 or more”. An empty search on an incomplete
list doesn't establish that there are no matches on the controller.

Click an address, MAC address or site ID to copy it. MAC addresses appear only
in expanded rows, not in logs, warnings or `status` output.

## Keyboard shortcuts

| Key | Action |
|---|---|
| Tab | Move between controls |
| Left / Right | Switch pages |
| `/` | Focus search |
| `f` | Cycle the current page's filter |
| Up / Down | Move through the list; elsewhere, move between controls |
| Enter | Expand a row, or open an Inventory row on Overview |
| `r` | Refresh |
| `d` | Show or hide Details |
| `?` | Show or hide the key legend |
| Escape | Clear a search before closing the panel |

The panel scrolls to the focused control. Controls that aren't on screen are
skipped: before the first reading Tab moves between Refresh and Open UniFi
only, the panel opens on Refresh, and the page and search keys do nothing. On
Overview, `/` opens Devices with the caret in its search field. Leaving the search field returns
keyboard navigation to the panel. The full list above is behind **? keys** at
the foot of the panel.

**Refresh** is the ↻ button beside the timestamp in the panel's header.
**Open UniFi** is the last thing in the panel, because it is the one action that
leaves it. **Details** — site, controller, site id, helper version and
configuration generation — is collapsed by default and opens itself whenever
the panel is reporting a problem; you can still close it.

## Widget settings

Use `omarchy bar set`, or edit the widget's entry in `bar.layout` in
`~/.config/omarchy/shell.json`. Omarchy 4.0.3 has no settings-form renderer.

```bash
omarchy bar set gaius-codius.unifi refreshIntervalSec 60 --json
omarchy bar set gaius-codius.unifi compactMetric clients
```

| Key | Default | Accepted values |
|---|---|---|
| `refreshIntervalSec` | `30` | 15–3600 seconds |
| `compactMetric` | `"none"` | `"none"` or `"clients"` |
| `dashboardUrl` | Derived from `apiRoot` | An `http` or `https` URL, opened by **Open UniFi** |

Use `--json` for numeric values so they are stored as numbers rather than strings.
Invalid values fall back to the default and raise a panel warning.

## Controller configuration

For guided setup, run `~/.config/omarchy/plugins/gaius-codius.unifi/scripts/setup`.
The options below are for manual changes and existing configurations.

Run `scripts/configure` from `~/.config/omarchy/plugins/gaius-codius.unifi/`.
Existing values are retained when you omit their options.

| Option | Effect |
|---|---|
| `--api-root <url>` | Set the Network Integration API root |
| `--api-key-stdin` | Read a key from standard input through end-of-file |
| `--api-key-file <file>` | Read a key from an existing file |
| `--site <uuid>` | Choose the site |
| `--custom-ca <file>` | Trust a PEM certificate file instead of the system store |
| `--no-custom-ca` | Restore the system trust store |
| `--allow-insecure-tls` | Disable TLS verification; the panel shows a warning |
| `--verify-tls` | Re-enable TLS verification |
| `--commit` | Revalidate manually edited files and write a new commit marker |

Configuration lives in `~/.config/omarchy-unifi/`, with directory mode `0700`.
`config.json` holds the API root, site and TLS settings; `api-key` holds the
credential. The script manages `commit.json`; don't edit it by hand.

After saving, the script asks the shell to reload. Exit code 0 also covers a
stopped shell or a widget not yet on the bar: the settings are saved for later.
If a reachable shell rejects the change, the script returns a non-zero code
and restores the previous configuration. It never queries the controller;
connection errors and site selection appear in the panel on polling.
