# Controller address and certificates

Start with `scripts/setup` in the installed plugin directory. It retrieves
self-signed certificates, asks you to verify the fingerprint and saves approved
trust automatically. This guide covers manual setup and verification problems.

The examples use `192.168.1.1`. Replace it with your console's address.

## 1. Choose the controller address

Use your console's **IP address**. It is usually your gateway:

```bash
ip route show default
```

The address after `default via` is the gateway. Setup offers it as the default.

A stock UniFi console's self-signed certificate names `unifi.local` and
loopback addresses, never the LAN address you reach it on. When you connect by
IP address with that certificate pinned, the plugin checks that the console
presents **exactly the pinned certificate** instead of checking the name. The
TLS handshake proves the console holds that certificate's private key, so this
is as strict as a name check, and nothing depends on name resolution.

A hostname still works if it resolves on your machine and the certificate covers
it. On Omarchy, a `.local` name is fragile: every `omarchy-settings` update
rewrites `/etc/nsswitch.conf`, which stops `/etc/hosts` from answering for
`.local` names. Use the IP address unless you have a reason not to.

If your console has a certificate from a private CA, connecting by IP works when
the certificate has an IP-address entry for that address, as before.

## 2. Save and verify the certificate

```bash
mkdir -p -m 700 ~/.config/omarchy-unifi
openssl s_client -connect 192.168.1.1:443 </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ~/.config/omarchy-unifi/console.pem

openssl x509 -in ~/.config/omarchy-unifi/console.pem \
  -noout -subject -ext subjectAltName -fingerprint -sha256
```

**Verify the SHA-256 fingerprint through a trusted, independent connection to
your console before using this file.** Saving a certificate with
`openssl s_client` doesn't establish that it belongs to your controller. Don't
trust it solely because it came from the address you entered.

## 3. Configure the plugin

Run the following in Bash, replacing the address as needed:

```bash
cd ~/.config/omarchy/plugins/gaius-codius.unifi
IFS= read -r -s -p 'UniFi API key: ' unifi_api_key
printf '\n'
printf '%s' "$unifi_api_key" | ./scripts/configure \
  --api-root https://192.168.1.1/proxy/network/integration \
  --custom-ca ~/.config/omarchy-unifi/console.pem --api-key-stdin --verify-tls
unset unifi_api_key
```

The custom file replaces system trust for this connection. Certificate
verification remains enabled. For a private CA, use its independently verified
PEM bundle with `--custom-ca` instead of the downloaded leaf certificate.

## If verification still fails

- **The console's certificate changed.** A firmware update or reset can issue a
  new one. Run `scripts/setup` again and verify the new fingerprint.
- **The certificate expired.** Repair it on the console; the plugin won't accept
  it.
- **You used a hostname.** Check `getent hosts <name>` and that the certificate
  covers the name, or switch to the IP address (step 1).
- **The certificate file moved or changed permissions.** It must be a regular
  file you own, not group- or world-writable.

`./scripts/configure --allow-insecure-tls` disables certificate verification and
adds a permanent panel warning. Use it only if you accept that someone on the
network path could intercept your API key and alter the reported data. Restore
verification with `./scripts/configure --verify-tls` once trust is configured.
