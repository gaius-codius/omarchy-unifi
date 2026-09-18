# Controller name resolution and certificates

Start with `scripts/setup` in the installed plugin directory. It retrieves
self-signed certificates, asks you to verify the fingerprint and saves approved
trust automatically. This guide covers manual setup and name-resolution problems.

Use a hostname covered by your controller's certificate. Trusting a certificate
and checking its hostname are separate requirements: adding a trusted
certificate won't fix an IP-address mismatch.

The examples use `unifi.local` and `192.168.1.1`. Replace both with your console's
hostname and address. If it already has a certificate trusted by your system,
you don't need a custom certificate file.

## 1. Check name resolution

```bash
getent hosts unifi.local
```

If this returns your console's address, continue to step 2. Otherwise, configure
local DNS or add an entry to `/etc/hosts`:

```bash
echo '192.168.1.1  unifi.local' | sudo tee -a /etc/hosts
```

Check again with `getent hosts unifi.local`. If `.local` lookups still fail,
inspect the `hosts:` line in `/etc/nsswitch.conf`. An mDNS entry followed by
`[NOTFOUND=return]` before `files` prevents `/etc/hosts` from being consulted at
all, so the entry you just added cannot take effect. Back up the file, then move
`files` ahead of that entry while preserving the other lookup sources. Check
resolution again before continuing.

Expect this on Omarchy: it ships `mdns_minimal [NOTFOUND=return]` ahead of
`files`, and UniFi consoles present a certificate issued for `unifi.local`, so a
`.local` name is the one you are most likely to need and the one `/etc/hosts`
alone cannot fix. Note that `resolvectl query` reads `/etc/hosts` directly and
will report the name as resolving even while `getent hosts` — the path the
plugin actually uses — fails. Trust `getent`.

## 2. Save and verify the certificate

```bash
mkdir -p -m 700 ~/.config/omarchy-unifi
openssl s_client -connect 192.168.1.1:443 -servername unifi.local </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ~/.config/omarchy-unifi/console.pem

openssl x509 -in ~/.config/omarchy-unifi/console.pem \
  -noout -subject -ext subjectAltName -fingerprint -sha256
```

Check that the hostname is covered by the certificate. **Verify the SHA-256
fingerprint through a trusted, independent connection to your console before
using this file.** Saving a certificate with `openssl s_client` doesn't establish
that it belongs to your controller. Don't trust it solely because it came from
the address you entered.

## 3. Configure the plugin

Run the following in Bash, replacing the hostname as needed:

```bash
cd ~/.config/omarchy/plugins/gaius-codius.unifi
IFS= read -r -s -p 'UniFi API key: ' unifi_api_key
printf '\n'
printf '%s' "$unifi_api_key" | ./scripts/configure \
  --api-root https://unifi.local/proxy/network/integration \
  --custom-ca ~/.config/omarchy-unifi/console.pem --api-key-stdin --verify-tls
unset unifi_api_key
```

The custom file replaces system trust for this connection. Hostname verification
remains enabled. For a private CA, use its independently verified PEM bundle
with `--custom-ca` instead of the downloaded leaf certificate.

## If verification still fails

Check name resolution, the certificate's hostname and validity dates, and the
certificate file path. A certificate for a hostname cannot verify a connection
made to an IP address unless that IP is also covered by the certificate.

`./scripts/configure --allow-insecure-tls` disables certificate verification and
adds a permanent panel warning. Use it only if you accept that someone on the
network path could intercept your API key and alter the reported data. Restore
verification with `./scripts/configure --verify-tls` once trust is configured.
