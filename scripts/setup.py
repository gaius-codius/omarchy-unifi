"""Interactive, verified first-run setup for the UniFi widget."""
import argparse
import getpass
import hashlib
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import warnings
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'helper'))
from unifi import credential, deadline, errors, pagination, paths, routes, tlsctx, transport, version_gate
import configure

PLUGIN = 'gaius-codius.unifi'


class SetupError(Exception):
    pass


def normalize_controller(value):
    value = value.strip()
    if any(ord(c) < 33 for c in value):
        raise SetupError('Enter a controller hostname or HTTPS URL without spaces.')
    if '://' not in value:
        value = 'https://' + value
    parts = urlsplit(value)
    if not parts.path or parts.path == '/':
        value = value.rstrip('/') + '/proxy/network/integration'
    routes.build(value, 'info')
    return value.rstrip('/')


def normalize_fingerprint(value):
    value = value.replace(':', '').strip().lower()
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise SetupError('Use the complete SHA-256 fingerprint (64 hexadecimal digits).')
    return value


def probe_certificate(root, context):
    """TLS handshake only: this function never receives or sends credentials."""
    _, host, port, _ = routes.parse_api_root(root)
    with socket.create_connection((host, port or 443), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=host) as secured:
            return secured.getpeercert(binary_form=True)


def certificate_names(pem):
    # Decode only public certificate metadata. Never pass credentials to openssl.
    try:
        result = subprocess.run(['openssl', 'x509', '-noout', '-ext', 'subjectAltName'],
                                input=pem.encode('ascii'), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10, check=False)
        text = result.stdout.decode('ascii', 'replace')[:8192]
        return re.findall(r'DNS:([A-Za-z0-9.*_-]+)', text)
    except (OSError, subprocess.TimeoutExpired):
        return []


def establish_trust(root, fingerprint, interactive):
    expected = normalize_fingerprint(fingerprint) if fingerprint else None
    context = tlsctx.build_context()
    try:
        der = probe_certificate(root, context)
        if expected and hashlib.sha256(der).hexdigest() != expected:
            raise SetupError('The controller certificate does not match the approved fingerprint.')
        return context, None
    except ssl.SSLCertVerificationError:
        pass
    # Capture the presented leaf without authentication. Trust is granted only
    # after independent fingerprint approval AND a verifying second handshake.
    der = probe_certificate(root, tlsctx.build_context(allow_insecure=True))
    actual = hashlib.sha256(der).hexdigest()
    pem = ssl.DER_cert_to_PEM_cert(der)
    print('The controller certificate is not trusted by this computer.')
    print('SHA-256: ' + ':'.join(actual[i:i + 2].upper() for i in range(0, 64, 2)))
    names = certificate_names(pem)
    if names:
        print('Certificate hostnames: ' + ', '.join(names))
    if not expected:
        if not interactive:
            raise SetupError('Verify this fingerprint through a trusted controller connection, then rerun with --trust-fingerprint.')
        print('Compare this with the certificate on the controller through a trusted connection.\n'
              'Do not approve it just because it is displayed here.')
        expected = normalize_fingerprint(input('Paste the independently verified SHA-256 fingerprint (Ctrl-C to cancel): '))
    if actual != expected:
        raise SetupError('The controller certificate does not match the approved fingerprint.')
    context = tlsctx.build_context(ca_pem=pem)
    try:
        probe_certificate(root, context)
    except ssl.SSLCertVerificationError:
        raise SetupError('The approved certificate still fails verification. Use a resolvable hostname covered by the certificate '
                         '(shown above), or repair an expired certificate/missing issuer chain. '
                         'Rerun with --controller using that hostname; setup does not change DNS or /etc/hosts. '
                         'For a private CA, follow docs/controller-setup.md and scripts/configure --custom-ca.')
    return context, pem


def read_key(args, interactive):
    if args.api_key_file:
        try:
            with open(args.api_key_file, 'rb') as handle:
                raw = handle.read(paths.API_KEY_MAX_BYTES + 1)
        except OSError:
            raise SetupError('Could not read the API key file.')
    else:
        if not interactive:
            raise SetupError('Non-interactive setup requires --api-key-file.')
        # getpass normally falls back to echoed stdin; never allow that fallback.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                raw = getpass.getpass('UniFi API key (hidden): ').encode('utf-8')
            except getpass.GetPassWarning:
                raise SetupError('A hidden terminal prompt is unavailable. Use --api-key-file instead.')
    if len(raw) > paths.API_KEY_MAX_BYTES:
        raise SetupError('The API key file is too large.')
    credential.parse(raw)
    return raw


def discover_sites(root, key, context):
    budget = deadline.Deadline()
    info, _ = transport.get_json(routes.build(root, 'info'), key, context, budget)
    version_gate.check(info)
    def fetch(offset, limit):
        return transport.get_json(routes.build(root, 'sites', offset=offset, limit=limit), key, context, budget)
    result = pagination.collect(fetch)
    if result.error:
        raise result.error
    if not result.complete:
        raise SetupError('The controller returned an incomplete site list. Try again.')
    if any(not routes.UUID_RE.fullmatch(site.get('id', '')) for site in result.records):
        raise SetupError('The controller returned an invalid site identifier.')
    return result.records


def select_site(sites, requested, interactive):
    if requested:
        if requested not in [site['id'] for site in sites]:
            raise SetupError('The requested site is not available to this API key.')
        return requested
    if not sites:
        raise SetupError('No sites are available to this API key.')
    if len(sites) == 1:
        return sites[0]['id']
    for index, site in enumerate(sites, 1):
        name = ''.join(c for c in str(site.get('name', 'Unnamed site')) if c.isprintable())[:120]
        print('%d. %s (%s)' % (index, name, site['id']))
    if not interactive:
        raise SetupError('More than one site is available; rerun with --site and its UUID.')
    choice = input('Choose a site number: ').strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(sites):
        raise SetupError('Choose one of the listed site numbers.')
    return sites[int(choice) - 1]['id']


def persist_certificate(directory, pem):
    configure.ensure_directory(directory)
    name = 'controller-' + hashlib.sha256(pem.encode('ascii')).hexdigest() + '.pem'
    path = os.path.join(directory, name)
    # Publish only fully written files, without overwriting live trust anchors.
    fd, temporary = tempfile.mkstemp(prefix='.controller-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if paths.read_custom_ca(path) != pem:
                raise SetupError('The saved certificate file does not match its content hash.')
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        os.unlink(temporary)
    return path


def configure_plugin(args, root, raw, site, ca_path):
    command = [os.path.join(_HERE, 'configure'), '--config-dir', os.path.expanduser(args.config_dir),
               '--api-root', root, '--site', site, '--api-key-stdin', '--verify-tls']
    command += ['--custom-ca', ca_path] if ca_path else ['--no-custom-ca']
    result = subprocess.run(command, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise SetupError('Configuration was not saved: the configure step failed. Check that the Omarchy shell is ready, then rerun setup.')
    print('Configuration saved with TLS verification enabled.')


def configure_bar():
    for command in (['omarchy', 'plugin', 'enable', PLUGIN],
                    ['omarchy', 'bar', 'put', PLUGIN, '--section', 'right']):
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode:
            return False
    return True


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Set up the UniFi bar widget using verified TLS.', allow_abbrev=False)
    parser.add_argument('--controller', help='hostname or full HTTPS Integration API root')
    parser.add_argument('--api-key-file', help='read the secret from a file, never the command line')
    parser.add_argument('--trust-fingerprint', help='independently verified SHA-256 certificate fingerprint')
    parser.add_argument('--site', help='site UUID (required when multiple sites exist without a terminal)')
    parser.add_argument('--config-dir', default='~/.config/omarchy-unifi')
    parser.add_argument('--skip-bar', action='store_true', help='save configuration without changing the bar')
    parser.add_argument('--non-interactive', action='store_true', help='never prompt')
    return parser.parse_args(argv)


def run(argv):
    args = parse_args(argv)
    interactive = not args.non_interactive and sys.stdin.isatty() and sys.stderr.isatty()
    if not interactive and (not args.controller or not args.api_key_file):
        raise SetupError('Non-interactive setup requires --controller and --api-key-file.')
    if args.site and not routes.UUID_RE.fullmatch(args.site):
        raise SetupError('--site must be a canonical UUID.')
    args.config_dir = os.path.abspath(os.path.expanduser(args.config_dir))
    default_dir = os.path.abspath(os.path.expanduser('~/.config/omarchy-unifi'))
    if args.config_dir != default_dir and not args.skip_bar:
        raise SetupError('A nondefault --config-dir requires --skip-bar; the widget uses ~/.config/omarchy-unifi.')
    if shutil.which('omarchy') is None:
        raise SetupError('The omarchy command is required. Run setup on your Omarchy desktop.')
    root = normalize_controller(args.controller or input('Controller address [unifi.local]: ').strip() or 'unifi.local')
    context, pem = establish_trust(root, args.trust_fingerprint, interactive)
    raw = read_key(args, interactive)
    key = credential.parse(raw)
    sites = discover_sites(root, key, context)
    site = select_site(sites, args.site, interactive)
    # Verify required device access too, before committing a new configuration.
    device_deadline = deadline.Deadline()
    def fetch_devices(offset, limit):
        return transport.get_json(routes.build(root, 'devices', siteId=site, offset=offset, limit=limit),
                                  key, context, device_deadline)
    devices = pagination.collect(fetch_devices)
    if devices.error:
        raise devices.error
    if not devices.complete:
        raise SetupError('The controller returned an incomplete device list. Configuration was not changed.')
    ca_path = persist_certificate(os.path.expanduser(args.config_dir), pem) if pem else None
    configure_plugin(args, root, raw, site, ca_path)
    if not args.skip_bar and not configure_bar():
        print('Controller connection verified and configuration saved, but the widget could not be added.\n'
              'Once the shell is ready, run:\n  omarchy plugin enable ' + PLUGIN + '\n'
              '  omarchy bar put ' + PLUGIN + ' --section right')
        return 1
    print('Controller version, API key, site and device access verified.\n'
          'Open the UniFi panel and check for a fresh update; setup has not verified the displayed data.')
    return 0


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (SetupError, configure.ConfigureError) as failure:
        print(str(failure), file=sys.stderr)
    except errors.HelperError as failure:
        print(failure.message, file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print('Setup cancelled.', file=sys.stderr)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Setup could not finish. Check connectivity, file permissions and the controller certificate.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
