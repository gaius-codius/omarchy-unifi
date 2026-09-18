#!/usr/bin/env python3
"""Offline setup checks: trust precedes secrets, and failure precedes writes."""
import json
import contextlib
import hashlib
import io
import os
import ssl
import sys
import tempfile
import subprocess
import types
import warnings
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "tests", "tools"))
import setup
import tls_stub


class SetupFlow(unittest.TestCase):
    def test_failed_connection_leaves_existing_settings_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "config.json")
            original = b'{"pollIntervalSeconds":123}\n'
            with open(target, "wb") as handle:
                handle.write(original)
            with mock.patch.object(setup, "establish_trust", side_effect=setup.SetupError("Cannot connect")), \
                    mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                    mock.patch.object(setup, "read_key") as read_key, \
                    mock.patch.object(setup, "configure_plugin") as configure, \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                status = setup.main(["--controller", "https://192.0.2.1", "--config-dir", root,
                                    "--api-key-file", "/does/not/exist", "--non-interactive", "--skip-bar"])
            self.assertNotEqual(status, 0)
            read_key.assert_not_called()
            configure.assert_not_called()
            self.assertEqual(os.listdir(root), ["config.json"])
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), original)


class SetupResolution(unittest.TestCase):
    """The preflight that names a resolution failure instead of mis-blaming TLS.

    A gaierror is an OSError, so before this existed it fell through to main()'s
    generic handler and was reported as a connectivity/permissions/certificate
    problem — sending the reader to audit a certificate that was fine.
    """

    def test_unresolvable_host_is_refused_by_name(self):
        with mock.patch.object(setup.socket, "getaddrinfo", side_effect=setup.socket.gaierror):
            with self.assertRaises(setup.SetupError) as caught:
                setup.check_resolvable("https://unifi.local/proxy/network/integration")
        self.assertIn("unifi.local", str(caught.exception))

    def test_the_refusal_names_every_step_of_the_fix(self):
        message = setup.resolution_help("unifi.local")
        # Each half is a separate assertion: on an mDNS system the /etc/hosts
        # entry alone does nothing, so a message that dropped the nsswitch
        # sentence would send the reader to a fix that cannot work.
        self.assertIn("getent hosts", message)
        self.assertIn("/etc/hosts", message)
        self.assertIn("/etc/nsswitch.conf", message)
        self.assertIn("[NOTFOUND=return]", message)
        self.assertIn("docs/controller-setup.md", message)
        # Substring checks alone pass for a bag of keywords with no sentences,
        # so pin the shape too: the host named, and one step per line.
        self.assertIn("unifi.local", message.splitlines()[0])
        self.assertGreaterEqual(len(message.splitlines()), 8)
        self.assertIn("move files ahead of it", message)

    def test_a_resolvable_host_is_accepted(self):
        with mock.patch.object(setup.socket, "getaddrinfo",
                               return_value=[(2, 1, 6, "", ("192.0.2.1", 8443))]) as resolve:
            setup.check_resolvable("https://unifi.example:8443/proxy/network/integration")
        # Without these the test passes with check_resolvable's body deleted.
        # The host must be the hostname rather than the whole URL, and the port
        # must be the one the handshake will use, not a hardcoded 443.
        resolve.assert_called_once()
        self.assertEqual(resolve.call_args[0][0], "unifi.example")
        self.assertEqual(resolve.call_args[0][1], 8443)

    def test_a_temporary_resolver_failure_is_not_called_a_misconfiguration(self):
        """EAI_AGAIN is the resolver not answering, not a name that is wrong."""
        failure = setup.socket.gaierror(setup.socket.EAI_AGAIN, "Temporary failure in name resolution")
        with mock.patch.object(setup.socket, "getaddrinfo", side_effect=failure):
            with self.assertRaises(setup.SetupError) as caught:
                setup.check_resolvable("https://unifi.local/proxy/network/integration")
        message = str(caught.exception)
        self.assertIn("unifi.local", message)
        self.assertIn("temporary", message.lower())
        # Telling someone to edit /etc/hosts and reorder /etc/nsswitch.conf for
        # a condition that clears on its own is the mis-blame this preflight
        # exists to remove, relocated. Neither persistent edit may be suggested.
        self.assertNotIn("/etc/hosts", message)
        self.assertNotIn("/etc/nsswitch.conf", message)
        # Nor may it send the reader to the doc section that prescribes them.
        self.assertNotIn("controller-setup.md", message)

    def test_an_unencodable_hostname_is_refused_by_name(self):
        """getaddrinfo raises UnicodeError, a ValueError, for a bad IDNA label.

        It is not an OSError, so `except socket.gaierror` does not catch it and
        it would otherwise reach main()'s generic clause and print the
        certificate advice this preflight exists to prevent.
        """
        def offline_getaddrinfo(host, *args, **kwargs):
            # CPython IDNA-encodes a str host before any lookup, which is
            # where these names fail. Do the same, then answer NXDOMAIN rather
            # than asking a real resolver: if the encoding ever stops raising,
            # this test fails on the wrong message instead of sending mDNS and
            # DNS queries whose answer depends on the machine running it.
            host.encode("idna")
            raise setup.socket.gaierror(setup.socket.EAI_NONAME, "stubbed: tests do not resolve")
        for host in ("a" * 64 + ".local", "unifi..local"):
            with self.subTest(host=host):
                with mock.patch.object(setup.socket, "getaddrinfo", side_effect=offline_getaddrinfo), \
                        self.assertRaises(setup.SetupError) as caught:
                    setup.check_resolvable("https://" + host + "/proxy/network/integration")
                message = str(caught.exception)
                self.assertIn(host, message)
                self.assertIn("label", message)
                self.assertNotIn("certificate", message)

    def test_a_hostname_outside_its_alphabet_is_refused_without_echoing_it(self):
        """The host is printed inside commands the reader is told to run, one
        under sudo, so a hostile address must never reach that message."""
        hostile = (
            "unifi.local$(id)",          # command substitution in the sudo line
            "x';id;'.local",             # breaks out of the echo's quotes
            "uni\x9bfi.local",          # C1 CSI: a terminal control sequence
            "uni\u202efi.local",        # right-to-left override: spoofs the display
            "unifi.local\u2028x",       # line separator: forges a new output line
        )
        for host in hostile:
            with self.subTest(host=ascii(host)):
                with self.assertRaises(setup.SetupError) as caught:
                    setup.normalize_controller(host)
                self.assertNotIn(host, str(caught.exception))
        stderr = io.StringIO()
        with mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                mock.patch.object(setup, "establish_trust") as trust, \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            status = setup.main(["--controller", "unifi.local$(id)", "--non-interactive",
                                 "--api-key-file", "/does/not/exist", "--skip-bar"])
        self.assertEqual(status, 1)
        trust.assert_not_called()
        self.assertNotIn("$(id)", stderr.getvalue())
        self.assertNotIn("sudo", stderr.getvalue())

    def test_ordinary_controller_addresses_are_still_accepted(self):
        for value in ("unifi.local", "192.168.1.1", "https://unifi.local:8443",
                      "my_unifi.local", "ünifi.local"):
            with self.subTest(value=value):
                setup.normalize_controller(value)

    def test_resolution_is_checked_before_the_key_is_read(self):
        """No secret is collected, and nothing is written, for a doomed run."""
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(setup.socket, "getaddrinfo", side_effect=setup.socket.gaierror), \
                    mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                    mock.patch.object(setup, "establish_trust") as trust, \
                    mock.patch.object(setup, "read_key") as read_key, \
                    mock.patch.object(setup, "configure_plugin") as configure, \
                    contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                status = setup.main(["--controller", "unifi.local", "--config-dir", root,
                                     "--api-key-file", "/does/not/exist",
                                     "--non-interactive", "--skip-bar"])
            self.assertNotEqual(status, 0)
            read_key.assert_not_called()
            trust.assert_not_called()
            configure.assert_not_called()
            self.assertEqual(os.listdir(root), [])
        # Asserting on resolution_help() alone never proves the operator sees
        # it; this is the only test that reads what actually reached stderr.
        self.assertIn("unifi.local", stderr.getvalue())
        self.assertIn("getent hosts", stderr.getvalue())
        self.assertIn("/etc/nsswitch.conf", stderr.getvalue())

    def test_resolution_lost_after_the_preflight_still_names_the_host(self):
        """The preflight can pass and the handshake still hit a gaierror.

        run() catches it while the hostname is in scope, so this path gives the
        same steps instead of main()'s last-resort message.
        """
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(setup.socket, "getaddrinfo",
                                   return_value=[(2, 1, 6, "", ("192.0.2.1", 443))]), \
                    mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                    mock.patch.object(setup, "establish_trust", side_effect=setup.socket.gaierror), \
                    mock.patch.object(setup, "read_key") as read_key, \
                    contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                status = setup.main(["--controller", "unifi.local", "--config-dir", root,
                                     "--api-key-file", "/does/not/exist",
                                     "--non-interactive", "--skip-bar"])
        self.assertEqual(status, 1)
        read_key.assert_not_called()
        self.assertIn("unifi.local", stderr.getvalue())
        self.assertIn("getent hosts", stderr.getvalue())

    def test_a_temporary_failure_after_the_preflight_is_not_called_a_misconfiguration(self):
        """A name that resolved a moment ago and now fails with EAI_AGAIN is a
        resolver blip; it must get the same message the preflight would give."""
        stderr = io.StringIO()
        blip = setup.socket.gaierror(setup.socket.EAI_AGAIN, "Temporary failure in name resolution")
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(setup.socket, "getaddrinfo",
                                   return_value=[(2, 1, 6, "", ("192.0.2.1", 443))]), \
                    mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                    mock.patch.object(setup, "establish_trust", side_effect=blip), \
                    contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                status = setup.main(["--controller", "unifi.local", "--config-dir", root,
                                     "--api-key-file", "/does/not/exist",
                                     "--non-interactive", "--skip-bar"])
        self.assertEqual(status, 1)
        self.assertIn("temporary", stderr.getvalue().lower())
        self.assertNotIn("/etc/hosts", stderr.getvalue())
        self.assertNotIn("/etc/nsswitch.conf", stderr.getvalue())

    def test_main_names_a_late_resolution_failure(self):
        """main()'s backstop must stay above its `except (OSError, ...)` clause.

        socket.gaierror subclasses OSError, so swapping the two clauses shadows
        this one silently and the reader is sent back to the certificate. This
        test is what holds that order in place.
        """
        stderr = io.StringIO()
        with mock.patch.object(setup, "run", side_effect=setup.socket.gaierror), \
                contextlib.redirect_stderr(stderr):
            status = setup.main(["--controller", "unifi.local", "--non-interactive",
                                 "--api-key-file", "/does/not/exist", "--skip-bar"])
        self.assertEqual(status, 1)
        self.assertIn("stopped resolving", stderr.getvalue())
        self.assertIn("docs/controller-setup.md", stderr.getvalue())
        self.assertNotIn("certificate", stderr.getvalue())


class SetupCertificates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.authority = tls_stub.mint_ca(cls.work.name, "setup")
        cls.cert = os.path.join(cls.work.name, "selfsigned.pem")
        cls.key = os.path.join(cls.work.name, "selfsigned.key")
        subprocess.run([tls_stub.require_openssl(), "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", cls.key, "-out", cls.cert, "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(cls.cert) as handle:
            cls.der = ssl.PEM_cert_to_DER_cert(handle.read())
        cls.fingerprint = hashlib.sha256(cls.der).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()


class SetupTLS(SetupCertificates):
    def test_rejected_fingerprint_sends_no_http_or_key(self):
        with tls_stub.TlsStub(self.cert, self.key) as server:
            root = "https://127.0.0.1:%d" % server.port
            with self.assertRaises(setup.SetupError), contextlib.redirect_stdout(io.StringIO()):
                setup.establish_trust(root, "00" * 32, False)
            self.assertFalse(any(request.get("headers") for request in server.received()))

    def test_matching_fingerprint_allows_verified_handshake(self):
        with tls_stub.TlsStub(self.cert, self.key) as server:
            root = "https://127.0.0.1:%d" % server.port
            with contextlib.redirect_stdout(io.StringIO()):
                context, pem = setup.establish_trust(root, self.fingerprint, False)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertTrue(pem)
            self.assertEqual(setup.probe_certificate(root, context), self.der)
            self.assertFalse(any(request.get("headers") for request in server.received()))

    def test_redirect_does_not_forward_credential(self):
        def redirect(request, index):
            return b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1:1/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        context = ssl.create_default_context(cafile=self.cert)
        with tls_stub.TlsStub(self.cert, self.key, handler=redirect) as server:
            with self.assertRaises(setup.errors.HelperError):
                setup.discover_sites("https://127.0.0.1:%d" % server.port, setup.credential.parse(b"test-secret"), context)
            self.assertEqual(len(server.received()), 1)


class SetupInputs(unittest.TestCase):
    sites = [{"id": "11111111-1111-1111-1111-111111111111", "name": "Home"},
             {"id": "22222222-2222-2222-2222-222222222222", "name": "Office"}]

    def test_sole_site_automatically_selected(self):
        with mock.patch("builtins.input") as prompt:
            self.assertEqual(setup.select_site(self.sites[:1], None, True), self.sites[0]["id"])
        prompt.assert_not_called()

    def test_multiple_sites_need_explicit_selection(self):
        with self.assertRaises(setup.SetupError):
            setup.select_site(self.sites, None, False)
        with mock.patch("builtins.input", return_value="2"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.select_site(self.sites, None, True), self.sites[1]["id"])
        with self.assertRaises(setup.SetupError):
            setup.select_site(self.sites, "33333333-3333-3333-3333-333333333333", False)

    def test_hidden_input_must_never_fall_back_to_echo(self):
        def unavailable(prompt):
            warnings.warn("Cannot control echo", setup.getpass.GetPassWarning)
            self.fail("Echo fallback reached")
        with mock.patch.object(setup.getpass, "getpass", side_effect=unavailable):
            with self.assertRaises(setup.SetupError):
                setup.read_key(types.SimpleNamespace(api_key_file=None), True)

    def test_noninteractive_missing_key_does_not_prompt(self):
        with mock.patch.object(setup.getpass, "getpass") as prompt:
            with self.assertRaises(setup.SetupError):
                setup.read_key(types.SimpleNamespace(api_key_file=None), False)
        prompt.assert_not_called()

    def test_credential_passed_only_on_subprocess_stdin(self):
        secret = b"fixture-key-not-a-real-key"
        with mock.patch.object(setup.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as child, \
                contextlib.redirect_stdout(io.StringIO()):
            setup.configure_plugin(types.SimpleNamespace(config_dir="/tmp/test-only"),
                                   "https://192.0.2.1", secret, self.sites[0]["id"], None)
        positional, kwargs = child.call_args
        self.assertNotIn(secret.decode(), repr(positional))
        self.assertNotIn(secret.decode(), repr(kwargs.get("env", {})))
        self.assertEqual(kwargs["input"], secret)

    def test_bar_command_failure_stops_and_returns_failure(self):
        with mock.patch.object(setup.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)) as child:
            self.assertFalse(setup.configure_bar())
        self.assertEqual(child.call_count, 1)

    def test_bar_failure_reports_saved_configuration(self):
        output = io.StringIO()
        with mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                mock.patch.object(setup, "establish_trust", return_value=(object(), None)), \
                mock.patch.object(setup, "read_key", return_value=b"fixture-key"), \
                mock.patch.object(setup, "discover_sites", return_value=self.sites[:1]), \
                mock.patch.object(setup.transport, "get_json", return_value=({"offset": 0, "limit": 200, "count": 0, "totalCount": 0, "data": []}, 10)), \
                mock.patch.object(setup, "configure_plugin") as save, \
                mock.patch.object(setup, "configure_bar", return_value=False), \
                contextlib.redirect_stdout(output):
            result = setup.run(["--controller", "192.0.2.1", "--api-key-file", "/not/read", "--non-interactive"])
        save.assert_called_once()
        self.assertNotEqual(result, 0)
        self.assertIn("configuration saved", output.getvalue())
        self.assertIn("could not be added", output.getvalue())


class SetupAcceptance(SetupCertificates):
    def response(self, body, status=200):
        encoded = json.dumps(body).encode()
        return (('HTTP/1.1 %d OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n' %
                 (status, len(encoded))).encode() + encoded)

    def test_full_verified_setup_saves_through_real_configure(self):
        site = SetupInputs.sites[0]
        def handler(request, index):
            if '/info' in request['target']:
                return self.response({'applicationVersion': '10.6.101'})
            records = [site] if '/sites?' in request['target'] else []
            return self.response({'offset': 0, 'limit': 200, 'count': len(records),
                                  'totalCount': len(records), 'data': records})
        with tempfile.TemporaryDirectory() as work, tls_stub.TlsStub(self.cert, self.key, handler=handler) as server:
            config_dir = os.path.join(work, 'config')
            key_file = os.path.join(work, 'input-key')
            secret = b'fixture-key-only'
            with open(key_file, 'wb') as f:
                f.write(secret)
            output = io.StringIO()
            def configure_child(command, **kwargs):
                # Run the actual configuration transaction, replacing only shell IPC.
                with mock.patch.object(setup.configure, 'read_credential', return_value=kwargs['input']), \
                        mock.patch.object(setup.configure, 'reload_shell', return_value=('applied', 'Applied.', 0)):
                    code, _ = setup.configure.run(command[1:], out=io.StringIO())
                return types.SimpleNamespace(returncode=code)
            # Certificate metadata openssl is unnecessary to the trust decision.
            with mock.patch.object(setup.shutil, 'which', return_value='/mock/omarchy'), \
                    mock.patch.object(setup, 'certificate_names', return_value=['localhost']), \
                    mock.patch.object(setup.subprocess, 'run', side_effect=configure_child), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = setup.main(['--controller', 'https://127.0.0.1:%d' % server.port,
                                     '--api-key-file', key_file, '--trust-fingerprint', self.fingerprint,
                                     '--config-dir', config_dir, '--skip-bar', '--non-interactive'])
            self.assertEqual(result, 0, output.getvalue())
            with open(os.path.join(config_dir, 'config.json')) as f:
                config = json.load(f)
            self.assertEqual(config['siteId'], site['id'])
            self.assertFalse(config.get('allowInsecureTls', False))
            self.assertTrue(os.path.isfile(config['customCaPath']))
            with open(os.path.join(config_dir, 'api-key'), 'rb') as f:
                self.assertEqual(f.read().strip(), secret)
            self.assertNotIn(secret.decode(), output.getvalue())
            self.assertEqual(os.stat(os.path.join(config_dir, 'api-key')).st_mode & 0o777, 0o600)
            self.assertTrue(os.path.exists(os.path.join(config_dir, 'commit.json')))

    def test_unsupported_version_never_saves(self):
        def handler(request, index):
            return self.response({'applicationVersion': '99.0.0'})
        context = setup.tlsctx.build_context(ca_pem=ssl.DER_cert_to_PEM_cert(self.der))
        with tls_stub.TlsStub(self.cert, self.key, handler=handler) as server, \
                mock.patch.object(setup.shutil, 'which', return_value='/mock/omarchy'), \
                mock.patch.object(setup, 'establish_trust', return_value=(context, None)), \
                mock.patch.object(setup, 'read_key', return_value=b'fixture-key'), \
                mock.patch.object(setup, 'configure_plugin') as save, \
                contextlib.redirect_stderr(io.StringIO()):
            result = setup.main(['--controller', 'https://127.0.0.1:%d' % server.port,
                                 '--api-key-file', '/not/read', '--non-interactive', '--skip-bar'])
        self.assertEqual(result, 1)
        save.assert_not_called()

    def test_hostname_mismatch_never_requests_key(self):
        # Use the trusted self-signed certificate with a hostname it does not cover.
        def wrong_host(root, context):
            with setup.socket.create_connection(('127.0.0.1', server.port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname='wrong.invalid') as secure:
                    return secure.getpeercert(binary_form=True)
        stderr = io.StringIO()
        with tls_stub.TlsStub(self.cert, self.key) as server, \
                mock.patch.object(setup.shutil, 'which', return_value='/mock/omarchy'), \
                mock.patch.object(setup, 'probe_certificate', side_effect=wrong_host) as probe, \
                mock.patch.object(setup, 'read_key') as key, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            result = setup.main(['--controller', 'https://127.0.0.1:%d' % server.port,
                                 '--api-key-file', '/not/read',
                                 '--trust-fingerprint', self.fingerprint, '--non-interactive', '--skip-bar'])
            self.assertEqual(result, 1)
            # The controller address must RESOLVE for this test to mean
            # anything: with an unresolvable name the preflight refuses first,
            # every assertion below is satisfied by that refusal alone, and the
            # mismatch handling this test exists for is never reached. Pinning
            # the count is what detects that: establish_trust probes three
            # times — the system-trust attempt that raises
            # SSLCertVerificationError, the insecure capture of the leaf, and
            # the verifying re-probe that raises it again and refuses.
            self.assertEqual(probe.call_count, 3)
            # Exit 1 alone is not enough: ssl.SSLCertVerificationError is an
            # OSError, so if establish_trust stopped handling the mismatch it
            # would escape to main()'s generic clause and still return 1 after
            # three probes. Only the message proves the mismatch was handled.
            self.assertIn("still fails verification", stderr.getvalue())
            key.assert_not_called()
            self.assertFalse(any(r.get("method") or r.get("headers") for r in server.received()))

    def test_unapproved_certificate_noninteractive_never_prompts(self):
        with tls_stub.TlsStub(self.cert, self.key) as server, \
                mock.patch('builtins.input') as prompt, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(setup.SetupError):
                setup.establish_trust('https://127.0.0.1:%d' % server.port, None, False)
            prompt.assert_not_called()
            self.assertFalse(any(r.get("method") or r.get("headers") for r in server.received()))



class CertificatePersistence(unittest.TestCase):
    def test_failed_write_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            pem = 'public-certificate-fixture'
            with mock.patch.object(setup.os, 'fsync', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    setup.persist_certificate(directory, pem)
            self.assertEqual(os.listdir(directory), [])
            path = setup.persist_certificate(directory, pem)
            with open(path) as handle:
                self.assertEqual(handle.read(), pem)
            self.assertEqual(setup.persist_certificate(directory, pem), path)
            self.assertEqual(len(os.listdir(directory)), 1)

    def test_existing_certificate_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            pem = 'new-public-certificate'
            name = 'controller-' + hashlib.sha256(pem.encode()).hexdigest() + '.pem'
            target = os.path.join(directory, name)
            with open(target, 'w') as f:
                f.write('different existing contents')
            with self.assertRaises(setup.SetupError):
                setup.persist_certificate(directory, pem)
            with open(target) as f:
                self.assertEqual(f.read(), 'different existing contents')


if __name__ == "__main__":
    unittest.main()
