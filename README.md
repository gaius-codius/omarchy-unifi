# omarchy-unifi

Check your UniFi network from the Omarchy bar. Open the panel to find offline
or impaired devices, see connected clients, and browse device details. The
plugin only reads from your controller; it never changes its configuration.

**Pre-release.** Tested with a UDM Pro running UniFi Network 10.6.101. Manual
checks across light and dark themes and release packaging are still pending.

## Before you install

You'll need:

- Omarchy **4.0.3-1** with Quickshell **0.3.1-1**.
- Python **3.9 or newer**. No pip packages are needed.
- UniFi Network **10.6.x** for hardware-tested support, with the Network
  Integration API available.
- An API key from your UniFi console: **Settings → Control Plane → Integrations**
  (older firmware: **Settings → System → Advanced**). Copy the key when you
  create it; the console won't show it again.

The helper also accepts **9.1.x** for test fixtures, but it has not been validated
against hardware running that version. Other versions are rejected. If yours is unsupported,
[open an issue](https://github.com/gaius-codius/omarchy-unifi/issues) with its
`applicationVersion`.

**WAN monitoring has a limitation:** the tested UDM Pro doesn't identify itself
as a gateway through the API. On that controller, WAN status is unknown, gateway
uptime and throughput are unavailable, and the bar cannot show “all gateways
down”. Device and client monitoring still work.

## Install

```bash
omarchy plugin add https://github.com/gaius-codius/omarchy-unifi
omarchy plugin enable gaius-codius.unifi
```

## Set up your controller

Run the wizard from the installed plugin:

```bash
~/.config/omarchy/plugins/gaius-codius.unifi/scripts/setup
```

It asks for your controller address (default `unifi.local`) and API key, checks
the connection, selects a site, saves the configuration and adds the widget to
the bar. Key input is hidden. With multiple sites, it asks which one to use.

For a self-signed certificate, the wizard retrieves it for you and displays its
SHA-256 fingerprint. Compare it through an independent trusted connection to
your controller, then paste the verified fingerprint to approve it. The wizard
saves the certificate and keeps TLS verification enabled.

Use a hostname that resolves on your machine and matches the certificate.
The wizard won't change your DNS settings or accept a mismatched or expired
certificate. See [certificate troubleshooting](docs/controller-setup.md) if it
cannot verify the connection.

After setup, open the panel and check the site name and last-update time.
The wizard verifies API access; the panel confirms that the widget is refreshing.

Using an AI agent? Follow the [agent installation guide](docs/agent-install.md).
For manual configuration, see the [configuration reference](docs/usage.md#controller-configuration).

## Use the widget

The bar icon follows your Omarchy theme. Its tooltip states the network
condition in words. Open the panel to:

- Check site health, offline devices and available WAN information in **Overview**.
- Search and filter **Devices**, then expand a row for firmware, resource usage,
  connections, ports and radios.
- Browse **Clients** to see addresses, connection types and connection times.

Click an address, MAC address or site ID to copy it. Missing metrics read
“unknown”. If a list is incomplete, the panel shows that limit rather than
presenting a partial count as a total.

See the [usage and settings reference](docs/usage.md) for status indicators,
keyboard shortcuts and configuration options.

To show the connected client count beside the icon:

```bash
omarchy bar set gaius-codius.unifi compactMetric clients
```

## Troubleshooting

| Problem | What to do |
|---|---|
| No widget or updates | Add the widget to the bar; enabling the plugin alone doesn't start its service. |
| Unsupported controller version | Use a supported version; only 10.6.x has been tested on hardware. |
| TLS error | Check the hostname and certificate using the [setup guide](docs/controller-setup.md). |
| WAN status is unknown | See the gateway limitation above. |
| Configuration changed but not committed | Run `~/.config/omarchy/plugins/gaius-codius.unifi/scripts/configure --commit`. |
| Duplicate widgets disagree | Give them the same `refreshIntervalSec`, or remove the duplicate. |

**After updating the plugin, restart the shell** so it loads the new code:

```bash
omarchy restart shell
```

Configuration and bar-layout changes apply live.

## Remove

```bash
omarchy plugin disable gaius-codius.unifi
omarchy plugin remove gaius-codius.unifi
```

Your saved configuration and API key remain in `~/.config/omarchy-unifi/`.
To delete them too:

```bash
rm -rf ~/.config/omarchy-unifi
```

Revoke the API key in the UniFi console as well. Deleting the local copy doesn't
invalidate it.

## Development

See [development and testing](docs/development.md) for the test commands,
local QML checks and project documentation.

## Licence

MIT. See [LICENSE](LICENSE).
