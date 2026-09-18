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

    def test_a_resolvable_host_is_accepted(self):
        with mock.patch.object(setup.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.1", 443))]):
            setup.check_resolvable("https://unifi.example/proxy/network/integration")

    def test_resolution_is_checked_before_the_key_is_read(self):
        """SEC-001's ordering, extended: no secret is collected for a doomed run."""
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(setup.socket, "getaddrinfo", side_effect=setup.socket.gaierror), \
                    mock.patch.object(setup.shutil, "which", return_value="/mock/omarchy"), \
                    mock.patch.object(setup, "establish_trust") as trust, \
                    mock.patch.object(setup, "read_key") as read_key, \
                    mock.patch.object(setup, "configure_plugin") as configure, \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                status = setup.main(["--controller", "unifi.local", "--config-dir", root,
                                     "--api-key-file", "/does/not/exist",
                                     "--non-interactive", "--skip-bar"])
            self.assertNotEqual(status, 0)
            read_key.assert_not_called()
            trust.assert_not_called()
            configure.assert_not_called()
            self.assertEqual(os.listdir(root), [])


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
        with tls_stub.TlsStub(self.cert, self.key) as server, \
                mock.patch.object(setup.shutil, 'which', return_value='/mock/omarchy'), \
                mock.patch.object(setup, 'probe_certificate', side_effect=wrong_host), \
                mock.patch.object(setup, 'read_key') as key, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = setup.main(['--controller', 'wrong.invalid', '--api-key-file', '/not/read',
                                 '--trust-fingerprint', self.fingerprint, '--non-interactive', '--skip-bar'])
            self.assertEqual(result, 1)
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
