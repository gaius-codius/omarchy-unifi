"""A local TLS server with certificates minted by the system `openssl`.

Risk R-G in the plan: a test that SKIPS when `openssl` is missing looks green on
a machine where the entire SEC-006/SEC-007a matrix never ran. AC-015b and
AC-016 are the criteria that decide whether an attacker-supplied CA can
intercept a credential-bearing request; a silent skip there is worse than a
failure, because a failure gets fixed. `require_openssl()` therefore raises.

Certificates are minted into a caller-provided temporary directory, never into
the repository — `tests/lint/no_repo_writes.sh` brackets the whole suite and
would catch it, but the reason is the same one that gate exists for: the
repository root IS the plugin folder, and anything written here is written into
a staged plugin.

Nothing in this file is shipped. It is a test tool, and it is deliberately not
under `helper/`, so the SEC-007 grep gate's scope stays exactly the code that
runs on a user's machine.
"""

import os
import shutil
import socket
import ssl
import subprocess
import threading

# One day. These certificates exist for the length of a test run; a long
# validity would only make a stray copy useful to somebody.
DAYS = "1"

CA_CONFIG = """\
[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no

[dn]
CN = %(cn)s

[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
"""

LEAF_CONFIG = """\
[req]
distinguished_name = dn
prompt = no

[dn]
CN = %(cn)s
"""

LEAF_EXTENSIONS = """\
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
"""


class OpensslMissing(Exception):
    """Raised instead of skipping. See the module docstring."""


def require_openssl():
    """Return the path to `openssl`, or raise.

    Deliberately not a `unittest.skipUnless` predicate.
    """
    found = shutil.which("openssl")
    if found is None:
        raise OpensslMissing(
            "openssl is not on PATH. The SEC-006 and SEC-007a tests need it to "
            "mint certificates, and skipping them would report a green suite "
            "for the checks that decide whether an attacker CA can intercept "
            "the API key.")
    return found


def _run(args):
    result = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "openssl failed (%d): %s\n%s"
            % (result.returncode, " ".join(args), result.stdout.decode("utf-8", "replace")))


def _write(path, text):
    with open(path, "w") as handle:
        handle.write(text)


class Authority(object):
    """A minted CA: its certificate, its key, and the PEM text of the cert."""

    def __init__(self, cert_path, key_path):
        self.cert_path = cert_path
        self.key_path = key_path
        with open(cert_path, "r") as handle:
            self.cert_pem = handle.read()


def mint_ca(workdir, name):
    """Mint a self-signed CA into `workdir` under `name`."""
    openssl = require_openssl()
    config = os.path.join(workdir, name + "-ca.cnf")
    cert = os.path.join(workdir, name + "-ca.pem")
    key = os.path.join(workdir, name + "-ca.key")

    _write(config, CA_CONFIG % {"cn": name + " Test CA"})
    _run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
          "-keyout", key, "-out", cert, "-days", DAYS, "-sha256", "-config", config])
    return Authority(cert, key)


def mint_server_cert(workdir, authority, name="server"):
    """Mint a `localhost` server certificate signed by `authority`."""
    openssl = require_openssl()
    config = os.path.join(workdir, name + ".cnf")
    extensions = os.path.join(workdir, name + ".ext")
    csr = os.path.join(workdir, name + ".csr")
    cert = os.path.join(workdir, name + ".pem")
    key = os.path.join(workdir, name + ".key")

    _write(config, LEAF_CONFIG % {"cn": "localhost"})
    # subjectAltName is not optional: every current Python rejects a
    # certificate that carries only a CN, so a leaf without it would fail for a
    # reason that has nothing to do with what the test is checking.
    _write(extensions, LEAF_EXTENSIONS)

    _run([openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes",
          "-keyout", key, "-out", csr, "-config", config])
    _run([openssl, "x509", "-req", "-in", csr, "-CA", authority.cert_path,
          "-CAkey", authority.key_path, "-CAcreateserial", "-out", cert,
          "-days", DAYS, "-sha256", "-extfile", extensions])
    return cert, key


class TlsStub(object):
    """A one-connection-at-a-time HTTPS server on 127.0.0.1, as a context manager.

    Answers every request with the same tiny response. It exists to complete a
    handshake, not to be a controller — the request side is `transport.py`'s
    business in Phase 6.
    """

    RESPONSE = (b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 11\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b'{"ok":true}')

    def __init__(self, certfile, keyfile):
        self.certfile = certfile
        self.keyfile = keyfile
        self.host = "127.0.0.1"
        self.port = None
        self._socket = None
        self._thread = None
        self._stop = threading.Event()

    def __enter__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certfile, self.keyfile)

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, 0))
        self._socket.listen(8)
        self._socket.settimeout(0.25)
        self.port = self._socket.getsockname()[1]

        self._thread = threading.Thread(target=self._serve, args=(context,))
        self._thread.daemon = True
        self._thread.start()
        return self

    def _serve(self, context):
        while not self._stop.is_set():
            try:
                raw, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                raw.settimeout(2.0)
                with context.wrap_socket(raw, server_side=True) as tls:
                    try:
                        tls.recv(4096)
                        tls.sendall(self.RESPONSE)
                    except OSError:
                        pass
            except (ssl.SSLError, OSError):
                # A client that refuses our certificate aborts the handshake.
                # That is the SUCCESS path for AC-015b and half of AC-016, so it
                # must not take the server down.
                try:
                    raw.close()
                except OSError:
                    pass

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        return False

    def connect(self, context, server_hostname="localhost"):
        """Complete a handshake using `context`, returning the peer certificate.

        Raises `ssl.SSLError` (usually `SSLCertVerificationError`) when the
        context refuses the server's chain — which is the assertion in every
        negative case here.
        """
        raw = socket.create_connection((self.host, self.port), timeout=5.0)
        try:
            with context.wrap_socket(raw, server_hostname=server_hostname) as tls:
                tls.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                body = tls.recv(4096)
                return tls.getpeercert(), body
        finally:
            try:
                raw.close()
            except OSError:
                pass
