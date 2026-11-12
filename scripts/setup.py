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
        raise SetupError('Enter a controller IP address, hostname or HTTPS URL without spaces.')
    if '://' not in value:
        value = 'https://' + value
    parts = urlsplit(value)
    if not parts.path or parts.path == '/':
        value = value.rstrip('/') + '/proxy/network/integration'
    routes.build(value, 'info')
    # The hostname is echoed back in error messages, and resolution_help
    # prints it inside commands the reader is told to run, one under sudo.
    # urlsplit keeps shell metacharacters ($ ( ' ; |) and C1 or bidi control
    # characters in a hostname, so vet it here, once, before anything can
    # repeat it. Letters and digits (including non-ASCII ones, for IDN names),
    # dots, hyphens, underscores and the colons of an IPv6 literal are all a
    # controller address needs. The refusal deliberately does not quote the
    # value it refuses.
    host = routes.parse_api_root(value)[1]
    if not all(c.isalnum() or c in '.-_:' for c in host):
        raise SetupError('Enter the controller as a hostname or IP address, using only letters, '
                         'digits, dots and hyphens.')
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
    # By IP address, this context checks the pin rather than the name
    # (SEC-014), which is what lets a stock console be set up by its address.
    host = routes.parse_api_root(root)[1]
    context = tlsctx.build_context(ca_pem=pem, host=host)
    try:
        probe_certificate(root, context)
    except ssl.SSLCertVerificationError:
        if tlsctx.is_ip_literal(host):
            raise SetupError('The approved certificate still fails verification by IP address. It may have expired, '
                             'or it is not self-signed and does not name this address. Repair the certificate on the '
                             'console, or for a private CA follow docs/controller-setup.md and scripts/configure --custom-ca.')
        raise SetupError('The approved certificate still fails verification. Use the console\'s IP address, '
                         'or a resolvable hostname covered by the certificate (shown above), '
                         'or repair an expired certificate/missing issuer chain. '
                         'Rerun with --controller using that address; setup does not change DNS or /etc/hosts. '
                         'For a private CA, follow docs/controller-setup.md and scripts/configure --custom-ca.')
    return context, pem


def default_gateway(route_table='/proc/net/route'):
    """The IPv4 default gateway, or None. Usually the UniFi console itself.

    Read from the kernel's route table rather than by running `ip`, so the
    default costs no subprocess and cannot be steered by PATH. Only offered as
    the prompt's default: the certificate fingerprint is still what decides
    whether the address that answers is the console.
    """
    try:
        with open(route_table, encoding='ascii') as handle:
            lines = handle.read(65536).splitlines()[1:]
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        fields = line.split()
        # Destination 0 with mask 0: the default route, not a 0.0.0.0/8 one.
        if len(fields) < 8 or fields[1] != '00000000' or fields[7] != '00000000':
            continue
        try:
            flags = int(fields[3], 16)
            # The kernel writes the address in host byte order.
            gateway = socket.inet_ntoa(int(fields[2], 16).to_bytes(4, sys.byteorder))
        except (ValueError, OverflowError, OSError):
            continue
        # RTF_UP and RTF_GATEWAY: a live route through a router.
        if flags & 0x3 == 0x3 and gateway != '0.0.0.0':
            return gateway
    return None


def check_resolvable(root):
    """Fail by name when the controller hostname does not resolve.

    Without this the first symptom is a gaierror raised from the certificate
    probe, which main() catches as an OSError and reports as "check
    connectivity, file permissions and the controller certificate" — three
    things that are all typically fine. The cause is name resolution, and it is
    worth naming. An IP address always passes; since SEC-014 that is what the
    message steers a user to, because a `.local` name on Omarchy breaks again
    whenever omarchy-settings rewrites /etc/nsswitch.conf.

    This resolves the name that establish_trust is about to resolve again a
    moment later. The duplicate lookup is deliberate: reporting the failure
    from inside the TLS probe would put the explanation where it has to
    compete with certificate advice, which is the confusion being fixed.
    """
    # routes.parse_api_root is the one place that takes an apiRoot apart, and
    # it has already rejected a host-less address by the time run() gets here.
    # Taking host AND port from it keeps this lookup asking the same question
    # the handshake will ask, instead of a second, subtly different one.
    _, host, port, _ = routes.parse_api_root(root)
    try:
        socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as failure:
        raise SetupError(resolution_failure_message(host, failure))
    except UnicodeError:
        # getaddrinfo IDNA-encodes the name before the lookup and raises
        # UnicodeError — a ValueError, not an OSError — for a label that is
        # empty, over 63 bytes, or holds a character IDNA rejects. Uncaught it
        # would reach main()'s generic clause and print the certificate advice
        # this preflight exists to prevent, so it is named here even though it
        # is a typo, not a resolution failure. The message lists the possible
        # causes rather than guessing one. normalize_controller has already
        # vetted the host's characters, so echoing it here is safe.
        raise SetupError(
            'The controller address ' + host + ' is not a valid hostname: a dot-separated\n'
            'label is empty, longer than 63 bytes, or uses a character DNS names cannot hold.\n'
            'Check the address for a typo; a doubled dot is the usual cause.')


def resolution_failure_message(host, failure):
    """Choose the message for a gaierror: temporary, or a name that is wrong."""
    if failure.errno == socket.EAI_AGAIN:
        return temporary_resolution_help(host)
    return resolution_help(host)


def resolution_help(host):
    """The message for a name that does not resolve, and the fix for it.

    Used by the preflight and, through run(), by the narrow case where
    resolution breaks between the preflight and the handshake, so both paths
    give the reader the same steps. The host is printed inside commands the
    reader is told to run; it is safe to do so only because
    normalize_controller has already refused any character outside a
    hostname's alphabet.
    """
    return (
        'The controller address ' + host + ' does not resolve on this computer.\n'
        'Use the console\'s IP address instead, which needs no name resolution:\n'
        '  scripts/setup --controller <console-ip>\n'
        'The console is usually your gateway; `ip route` shows it after "default via".\n'
        'To keep using the name instead, check it with: getent hosts ' + host + '\n'
        'Setup does not change DNS or /etc/hosts. See docs/controller-setup.md step 1.')


def temporary_resolution_help(host):
    """The message for EAI_AGAIN, which is not a misconfiguration.

    resolution_help would send this reader to make two persistent edits — an
    /etc/hosts entry and an /etc/nsswitch.conf reorder — for a condition that
    clears on its own. That is the same mis-blame this preflight exists to
    remove, so the two cases are told apart by errno rather than merged.
    """
    return (
        'The controller address ' + host + ' could not be resolved right now:\n'
        'the name server did not answer. This is usually temporary, and often\n'
        'means the network is not up yet. Check the connection and run setup again.\n'
        "If it keeps failing once the network is up, check that this computer's DNS\n"
        'server is reachable, for example with: resolvectl status')


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
    parser.add_argument('--controller', help='console IP address, hostname or full HTTPS Integration API root')
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
    if args.controller:
        address = args.controller
    else:
        gateway = default_gateway()
        prompt = 'Controller address' + (' [' + gateway + ']' if gateway else '') + ': '
        address = input(prompt).strip() or gateway
        if not address:
            raise SetupError('Enter the controller address, usually your gateway\'s IP address.')
    root = normalize_controller(address)
    check_resolvable(root)
    try:
        context, pem = establish_trust(root, args.trust_fingerprint, interactive)
    except socket.gaierror as failure:
        # Resolution can break between the preflight and the handshake. The
        # hostname is still in scope here, so the reader gets the same message
        # the preflight would have given rather than main()'s last-resort one,
        # which cannot name it. That includes telling EAI_AGAIN apart: a name
        # that resolved a moment ago and now fails is more likely a resolver
        # blip than a misconfiguration.
        raise SetupError(resolution_failure_message(routes.parse_api_root(root)[1], failure))
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
    except socket.gaierror:
        # Last resort: a gaierror from somewhere that does not know the
        # hostname, so it cannot print resolution_help's steps. It must stay
        # ABOVE the OSError clause below - gaierror subclasses OSError, and
        # below it this would be shadowed and the reader sent to the
        # certificate. SetupResolution.test_main_names_a_late_resolution_failure
        # fails if the two are ever swapped.
        print('Setup could not finish: the controller address stopped resolving.\n'
              'See docs/controller-setup.md step 1.', file=sys.stderr)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Setup could not finish. Check connectivity, file permissions and the controller certificate.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
