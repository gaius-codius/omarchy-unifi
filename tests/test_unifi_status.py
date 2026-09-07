#!/usr/bin/env python3
"""AC-015a (grep half), AC-015b, AC-016, AC-053, AC-060, and the SEC-001..007a core.

Created in Phase 5 and extended in Phases 6, 7 and 8 — this file is the cited
test for twelve traceability rows and for all of section 10 except SEC-001/011/012,
so it gets an explicit owner here rather than appearing by accident later.

Run under BOTH pinned interpreters (3.9.25 and 3.14.7) by `tests/run.sh`, with
`-B -E -s`: HC-14 says a `__pycache__` write inside the plugin tree hot-reloads a
staged plugin, and HC-15 says `-I` would strip the sys.path entry the helper
needs. The same flags are used to LAUNCH the helper, so the tests run it the way
the service will.
"""

import atexit
import errno
import http.client
import io
import inspect
import json
import os
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import time
import unittest
import urllib.parse as urllib_parse

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

# The explicit bootstrap. `-E -s` discards PYTHONPATH and the user site
# directory, which is deliberate — the helper must not be steerable through the
# environment — so the path to the package is stated here rather than inherited.
sys.path.insert(0, os.path.join(_REPO, "helper"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
sys.path.insert(0, os.path.join(_HERE, "tools"))

import configure  # noqa: E402  (scripts/configure.py)
import fixture_pages  # noqa: E402
import normalize_inputs  # noqa: E402
import tls_stub  # noqa: E402
import unifi  # noqa: E402
import unifi_status  # noqa: E402
from unifi import bounds, collect, commitset, credential, deadline, envelope  # noqa: E402
from unifi import config as config_module  # noqa: E402
from unifi import errors, normalize, pagination, paths, routes  # noqa: E402
from unifi import sanitize, tlsctx, transport, version_gate, warn  # noqa: E402


_AUTHORITY = []


def _shared_authority():
    """Mint one CA and one server certificate for the whole module.

    Minting is two `openssl` invocations, and the transport suite starts dozens
    of stubs. The certificate is not what any of those tests vary — the SEC-006
    negative matrix mints its own — so one is enough, and the difference is
    seconds per run rather than minutes.
    """
    if not _AUTHORITY:
        workdir = tempfile.mkdtemp(prefix="omarchy-unifi-ca-")
        atexit.register(shutil.rmtree, workdir, True)
        authority = tls_stub.mint_ca(workdir, "shared")
        certfile, keyfile = tls_stub.mint_server_cert(workdir, authority)
        _AUTHORITY.append((authority, certfile, keyfile))
    return _AUTHORITY[0]


class TempTree(unittest.TestCase):
    """Builds a throwaway configuration tree per test.

    Everything is written under `tempfile.mkdtemp()`. `tests/lint/no_repo_writes.sh`
    brackets the whole suite and would catch a stray write, but the reason it
    matters is the same reason that gate exists: the repository root IS the
    plugin folder, so anything written here lands in a staged plugin.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="omarchy-unifi-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.config_dir = os.path.join(self.root, "omarchy-unifi")
        os.mkdir(self.config_dir, 0o700)

    def write(self, name, data, mode=0o600, directory=None):
        target = os.path.join(directory or self.config_dir, name)
        with open(target, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.chmod(target, mode)
        return target

    def open_dir(self):
        fd = paths.open_config_dir(self.config_dir)
        self.addCleanup(_close_quietly, fd)
        return fd

    # TEST-NET-1 (RFC 5737), which is guaranteed not to be routed. Every
    # apiRoot in this suite that could conceivably reach a socket uses it, so a
    # test that grew a real request by accident cannot reach a device on the
    # machine's own network. The route-building tests below are the exception
    # and say why.
    def commit(self, config=b'{"apiRoot":"https://192.0.2.9"}', key=b"sk-abc\n",
               generation=1, marker=None):
        """Write a consistent, committed set. Returns the raw bytes written."""
        self.write(paths.CONFIG_NAME, config)
        self.write(paths.API_KEY_NAME, key)
        payload = marker if marker is not None else commitset.build_commit(
            generation, config, key)
        import json
        self.write(paths.COMMIT_NAME, json.dumps(payload).encode("utf-8"))
        return config, key


def _close_quietly(fd):
    try:
        os.close(fd)
    except OSError:
        pass


# --- SEC-002 / SEC-003: the directory ---------------------------------------

class ConfigDirectory(TempTree):

    def test_a_well_formed_directory_opens(self):
        fd = self.open_dir()
        self.assertIsInstance(fd, int)

    def test_a_missing_directory_is_unconfigured_not_internal(self):
        # The user's next step is to run scripts/configure, so the kind must be
        # the one whose panel state says that. `internal` would tell them to
        # file a bug.
        with self.assertRaises(errors.ConfigError) as caught:
            paths.open_config_dir(os.path.join(self.root, "absent"))
        self.assertEqual(caught.exception.kind, "unconfigured")
        self.assertIn("scripts/configure", caught.exception.message)

    def test_a_symlinked_directory_is_refused(self):
        # SEC-002 says a REAL directory. O_NOFOLLOW refuses the final component,
        # so validation and reading cannot end up on different objects.
        link = os.path.join(self.root, "link-to-config")
        os.symlink(self.config_dir, link)
        with self.assertRaises(errors.ConfigError):
            paths.open_config_dir(link)

    def test_a_group_or_other_writable_directory_is_refused(self):
        for mode in (0o770, 0o707, 0o777, 0o702, 0o720):
            os.chmod(self.config_dir, mode)
            with self.assertRaises(errors.ConfigError) as caught:
                paths.open_config_dir(self.config_dir)
            self.assertIn("chmod 700", caught.exception.message)

    def test_a_readable_but_not_writable_group_bit_is_allowed_on_the_directory(self):
        # Directories are held to the weaker rule on purpose: a 0750 directory
        # is harmless because the files inside carry their own permissions, and
        # tightening this would reject setups that are not actually unsafe.
        os.chmod(self.config_dir, 0o750)
        _close_quietly(paths.open_config_dir(self.config_dir))

    def test_a_file_where_the_directory_should_be_is_refused(self):
        target = os.path.join(self.root, "not-a-dir")
        with open(target, "w"):
            pass
        with self.assertRaises(errors.ConfigError):
            paths.open_config_dir(target)

    def test_a_fifo_in_place_of_the_directory_does_not_hang(self):
        # What O_DIRECTORY is actually for. The S_ISDIR check after fstat is
        # what REJECTS a non-directory, so removing O_DIRECTORY changes no
        # verdict — but `os.open(fifo, O_RDONLY)` BLOCKS until a writer
        # appears, and we would never reach the fstat to find out. A helper
        # hung on open() produces no envelope at all: the watchdog fires at
        # 30 s, every batch, forever, with nothing in any log to explain it.
        #
        # The alarm is what makes this a failure rather than a hung suite.
        import signal
        fifo = os.path.join(self.root, "fifo-not-a-dir")
        os.mkfifo(fifo, 0o600)

        def _timeout(signum, frame):
            raise AssertionError(
                "open_config_dir blocked on a FIFO; O_DIRECTORY is missing")

        previous = signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(5)
        try:
            with self.assertRaises(errors.ConfigError):
                paths.open_config_dir(fifo)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    def test_a_directory_owned_by_someone_else_is_refused(self):
        # The check compares st_uid against os.getuid(). Creating a file owned
        # by another user needs privileges no test should want, so the OBSERVER
        # is moved instead of the file — the same comparison, the same branch.
        # The real uid is captured BEFORE patching: a lambda that calls
        # os.getuid() would be calling the replacement, not the original.
        fake_uid = os.getuid() + 1000
        with _patched(os, "getuid", lambda: fake_uid):
            with self.assertRaises(errors.ConfigError) as caught:
                paths.open_config_dir(self.config_dir)
        self.assertIn("not by you", caught.exception.message)


# --- SEC-002 / SEC-003 / AC-060: the files ----------------------------------

class BoundedReads(TempTree):

    def test_a_well_formed_file_reads_back_its_exact_bytes(self):
        self.write(paths.API_KEY_NAME, b"sk-abc\n")
        self.assertEqual(
            paths.read_bounded(self.open_dir(), paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES),
            b"sk-abc\n")

    def test_AC_060_a_0644_api_key_is_rejected(self):
        # The criterion by name. SEC-002 forbids ALL group and other bits, not
        # merely the writable ones: 0644 is world-READABLE, and a check that
        # only looked at write bits would pass it while any user on the machine
        # could read the credential.
        self.write(paths.API_KEY_NAME, b"sk-abc\n", mode=0o644)
        with self.assertRaises(errors.ConfigError) as caught:
            paths.read_bounded(self.open_dir(), paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)
        self.assertIn("no group or other permissions", caught.exception.message)

    def test_AC_060_every_group_and_other_bit_is_rejected(self):
        for mode in (0o604, 0o640, 0o660, 0o606, 0o666, 0o601, 0o610, 0o700 | 0o004):
            self.write(paths.API_KEY_NAME, b"sk-abc\n", mode=mode)
            with self.assertRaises(errors.ConfigError):
                paths.read_bounded(self.open_dir(), paths.API_KEY_NAME,
                                   paths.API_KEY_MAX_BYTES)

    def test_AC_060_only_0600_and_tighter_are_accepted(self):
        for mode in (0o600, 0o400):
            self.write(paths.API_KEY_NAME, b"sk-abc\n", mode=mode)
            paths.read_bounded(self.open_dir(), paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)

    def test_AC_060_a_symlinked_api_key_is_rejected(self):
        elsewhere = self.write("decoy", b"sk-decoy\n", directory=self.root)
        os.symlink(elsewhere, os.path.join(self.config_dir, paths.API_KEY_NAME))
        with self.assertRaises(errors.ConfigError):
            paths.read_bounded(self.open_dir(), paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)

    def test_AC_060_an_oversized_api_key_is_rejected(self):
        self.write(paths.API_KEY_NAME, b"x" * (paths.API_KEY_MAX_BYTES + 1))
        with self.assertRaises(errors.ConfigError) as caught:
            paths.read_bounded(self.open_dir(), paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)
        self.assertIn("limit", caught.exception.message)

    def test_a_file_at_exactly_the_bound_is_accepted(self):
        self.write(paths.API_KEY_NAME, b"x" * paths.API_KEY_MAX_BYTES)
        data = paths.read_bounded(self.open_dir(), paths.API_KEY_NAME,
                                  paths.API_KEY_MAX_BYTES)
        self.assertEqual(len(data), paths.API_KEY_MAX_BYTES)

    def test_a_missing_file_names_itself_and_the_fix(self):
        with self.assertRaises(errors.ConfigError) as caught:
            paths.read_bounded(self.open_dir(), paths.CONFIG_NAME, paths.CONFIG_MAX_BYTES)
        self.assertIn(paths.CONFIG_NAME, caught.exception.message)
        self.assertIn("scripts/configure", caught.exception.message)

    def test_a_directory_in_place_of_a_file_is_rejected(self):
        os.mkdir(os.path.join(self.config_dir, paths.CONFIG_NAME), 0o700)
        with self.assertRaises(errors.ConfigError):
            paths.read_bounded(self.open_dir(), paths.CONFIG_NAME, paths.CONFIG_MAX_BYTES)

    def test_a_file_owned_by_someone_else_is_rejected(self):
        self.write(paths.API_KEY_NAME, b"sk-abc\n")
        fd = self.open_dir()
        fake_uid = os.getuid() + 1000
        with _patched(os, "getuid", lambda: fake_uid):
            with self.assertRaises(errors.ConfigError):
                paths.read_bounded(fd, paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)

    def test_the_read_returns_bytes_and_no_handle(self):
        # SEC-003's shape, and DATA-004c's precondition: nothing a caller could
        # use to read again escapes this function. A second read mid-batch
        # would be a config.json from controller A paired with an api-key from
        # controller B.
        self.write(paths.API_KEY_NAME, b"sk-abc\n")
        result = paths.read_bounded(self.open_dir(), paths.API_KEY_NAME,
                                    paths.API_KEY_MAX_BYTES)
        self.assertIsInstance(result, bytes)
        self.assertNotIsInstance(result, io.IOBase)
        self.assertFalse(hasattr(result, "read"))
        self.assertFalse(hasattr(result, "fileno"))

    def test_reads_do_not_leak_descriptors(self):
        # Every read opens and closes. A leak would be invisible until a long
        # session ran out of descriptors, at which point the failure would look
        # like anything but this.
        self.write(paths.API_KEY_NAME, b"sk-abc\n")
        fd = self.open_dir()
        before = _open_fd_count()
        for _ in range(64):
            paths.read_bounded(fd, paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)
        self.assertEqual(_open_fd_count(), before)


# --- SEC-004 / AC-060 / AC-053: the credential ------------------------------

class CredentialShape(unittest.TestCase):

    def test_AC_053_an_api_key_written_with_a_trailing_newline_validates(self):
        # The criterion by name, and DATA-004b's whole point: the digest is
        # over `sk-abc\n`, and the trim happens afterwards, in memory.
        parsed = credential.parse(b"sk-abc\n")
        self.assertEqual(parsed.header_value(), "sk-abc")

    def test_AC_053_the_digest_is_over_raw_bytes_not_trimmed_ones(self):
        raw = b"sk-abc\n"
        self.assertEqual(commitset.digest(raw), commitset.digest(b"sk-abc\n"))
        self.assertNotEqual(commitset.digest(raw), commitset.digest(b"sk-abc"))
        # If either side ever trimmed first, this is the shape of the failure:
        # a set that never validates, on every run, with a correct message
        # about a mismatch that is real.
        self.assertNotEqual(
            commitset.digest(raw), commitset.digest(credential.parse(raw)
                                                    .header_value().encode("ascii")))

    def test_surrounding_whitespace_of_every_kind_is_trimmed(self):
        for wrapper in (b"\n%s\n", b"  %s  ", b"\r\n%s\r\n", b"\t%s\t", b"%s\n\n\n"):
            self.assertEqual(credential.parse(wrapper % b"sk-abc").header_value(), "sk-abc")

    def test_AC_060_an_empty_credential_is_rejected(self):
        for raw in (b"", b"\n", b"   ", b"\r\n\t "):
            with self.assertRaises(errors.CredentialError) as caught:
                credential.parse(raw)
            self.assertEqual(caught.exception.kind, "credential")

    def test_AC_060_embedded_whitespace_and_control_bytes_are_rejected(self):
        # SEC-004 names these individually, so they are asserted individually.
        cases = {
            "space": b"sk abc",
            "tab": b"sk\tabc",
            "CR": b"sk\rabc",
            "LF": b"sk\nabc",
            "NUL": b"sk\x00abc",
            "vertical tab": b"sk\x0babc",
            "form feed": b"sk\x0cabc",
            "escape": b"sk\x1babc",
            "DEL": b"sk\x7fabc",
            "high byte": b"sk\x80abc",
            "UTF-8 smart quote": "sk’abc".encode("utf-8"),
        }
        for label, raw in cases.items():
            with self.assertRaises(errors.CredentialError, msg=label):
                credential.parse(raw)

    def test_the_full_visible_ascii_range_is_accepted(self):
        every = bytes(bytearray(range(credential.MIN_BYTE, credential.MAX_BYTE + 1)))
        self.assertEqual(len(credential.parse(every)), credential.MAX_BYTE
                         - credential.MIN_BYTE + 1)

    def test_length_bounds(self):
        credential.parse(b"x")
        credential.parse(b"x" * credential.MAX_LENGTH)
        with self.assertRaises(errors.CredentialError):
            credential.parse(b"x" * (credential.MAX_LENGTH + 1))

    def test_SEC_001_the_credential_never_appears_in_a_rejection_message(self):
        # An error message reaches stderr, and DATA-005b caps stderr precisely
        # because it is the channel most likely to carry material nobody meant
        # to emit. "byte 17 of your key is bad" is a start on a brute force.
        secret = b"sk-supersecrethorse"
        for raw in (secret + b" x", secret + b"\x00", secret + "’".encode("utf-8")):
            with self.assertRaises(errors.CredentialError) as caught:
                credential.parse(raw)
            text = "%s %r %s" % (caught.exception.message, caught.exception,
                                 caught.exception.detail)
            self.assertNotIn("supersecret", text)
            self.assertNotIn("sk-", text)

    def test_SEC_001_the_credential_does_not_print_itself(self):
        parsed = credential.parse(b"sk-supersecrethorse")
        renders = [
            repr(parsed), str(parsed), "{}".format(parsed), "{!s}".format(parsed),
            "{!r}".format(parsed), "{:>40}".format(parsed), "%s" % (parsed,),
            "%r" % (parsed,), format(parsed, ""), format(parsed, ">40"),
        ]
        for rendered in renders:
            self.assertNotIn("supersecret", rendered)
            self.assertEqual(rendered.strip(), credential.REDACTED)

    def test_SEC_001_a_traceback_carrying_the_credential_does_not_show_it(self):
        # The realistic leak is not `print(cred)` — nobody writes that. It is an
        # exception raised somewhere else whose repr walks the arguments.
        import traceback
        parsed = credential.parse(b"sk-supersecrethorse")
        try:
            raise ValueError("boom", parsed)
        except ValueError:
            text = traceback.format_exc()
        self.assertNotIn("supersecret", text)

    def test_it_is_not_a_str_subclass(self):
        # A str subclass inherits join, %, + and every other operation that
        # would emit the value, so overriding three dunders would give a false
        # sense of safety while `"key=" + cred` still worked.
        parsed = credential.parse(b"sk-abc")
        self.assertNotIsInstance(parsed, str)
        with self.assertRaises(TypeError):
            "key=" + parsed

    def test_the_single_exit_point_is_named_so_it_can_be_grepped(self):
        parsed = credential.parse(b"sk-abc")
        self.assertEqual(parsed.header_value(), "sk-abc")
        source = _read_source("helper/unifi/credential.py")
        self.assertIn("def header_value", source)


# --- DATA-004 / DATA-004b / DATA-004c: the committed set --------------------

class CommittedSetVerification(TempTree):

    def test_a_consistent_set_loads_and_captures_the_raw_bytes(self):
        config, key = self.commit()
        loaded = commitset.load(self.open_dir())
        self.assertEqual(loaded.config_bytes, config)
        self.assertEqual(loaded.api_key_bytes, key)
        self.assertEqual(loaded.generation, 1)

    def test_an_interrupted_update_is_uncommitted_not_unconfigured(self):
        # The distinction matters to the user: the files are readable and
        # well-formed, but they are a mixture of two configurations. Sending a
        # request would use one controller's URL with another's key.
        self.commit()
        self.write(paths.API_KEY_NAME, b"sk-a-different-key\n")
        with self.assertRaises(errors.UncommittedError) as caught:
            commitset.load(self.open_dir())
        self.assertEqual(caught.exception.kind, "uncommitted")
        self.assertIn(paths.API_KEY_NAME, caught.exception.message)

    def test_a_changed_config_is_caught_too(self):
        self.commit()
        self.write(paths.CONFIG_NAME, b'{"apiRoot":"https://10.0.0.1"}')
        with self.assertRaises(errors.UncommittedError) as caught:
            commitset.load(self.open_dir())
        self.assertIn(paths.CONFIG_NAME, caught.exception.message)

    def test_DATA_004b_a_marker_built_from_trimmed_bytes_never_validates(self):
        # The failure this rule prevents, reproduced. It is permanent, silent,
        # and identical on every retry, which is why it has to be impossible by
        # construction rather than caught by a test in the field.
        key = b"sk-abc\n"
        config = b'{"apiRoot":"https://192.0.2.9"}'
        wrong = {
            commitset.GENERATION_KEY: 1,
            commitset.CONFIG_DIGEST_KEY: commitset.digest(config),
            commitset.API_KEY_DIGEST_KEY: commitset.digest(key.strip()),
        }
        self.commit(config=config, key=key, marker=wrong)
        with self.assertRaises(errors.UncommittedError):
            commitset.load(self.open_dir())

    def test_build_commit_and_load_agree_by_construction(self):
        # The point of sharing this module with scripts/configure: the writer
        # and the reader are the same code, so "agree on the rules" is not
        # something either side has to remember.
        for key in (b"sk-abc", b"sk-abc\n", b"  sk-abc  \n", b"sk-abc\r\n"):
            config, written = self.commit(key=key)
            loaded = commitset.load(self.open_dir())
            self.assertEqual(loaded.api_key_bytes, key)
            self.assertEqual(credential.parse(loaded.api_key_bytes).header_value(), "sk-abc")

    def test_a_malformed_marker_is_uncommitted(self):
        config = b'{"apiRoot":"https://192.0.2.9"}'
        key = b"sk-abc\n"
        for bad in (b"not json", b"[]", b"{}", b'{"commitGeneration":"one"}',
                    b'{"commitGeneration":-1}', b'{"commitGeneration":true}',
                    b'{"commitGeneration":1}'):
            self.write(paths.CONFIG_NAME, config)
            self.write(paths.API_KEY_NAME, key)
            self.write(paths.COMMIT_NAME, bad)
            with self.assertRaises(errors.UncommittedError):
                commitset.load(self.open_dir())

    def test_DATA_004c_the_captured_set_is_frozen(self):
        # Capture-once is what closes the window descriptor-safety cannot:
        # a batch is many HTTP calls, and re-reading between page 1 and page 2
        # of /clients while scripts/configure is running would mix the two.
        self.commit()
        loaded = commitset.load(self.open_dir())
        for attribute in ("generation", "config_bytes", "api_key_bytes"):
            with self.assertRaises(AttributeError):
                setattr(loaded, attribute, b"replaced")
        with self.assertRaises(AttributeError):
            del loaded.config_bytes

    def test_DATA_004c_replacing_the_files_does_not_change_captured_bytes(self):
        config, key = self.commit()
        loaded = commitset.load(self.open_dir())
        self.write(paths.CONFIG_NAME, b'{"apiRoot":"https://evil.example"}')
        self.write(paths.API_KEY_NAME, b"sk-attacker\n")
        self.assertEqual(loaded.config_bytes, config)
        self.assertEqual(loaded.api_key_bytes, key)

    def test_SEC_001_the_repr_of_a_committed_set_shows_no_bytes(self):
        self.commit(key=b"sk-supersecrethorse\n")
        text = repr(commitset.load(self.open_dir()))
        self.assertNotIn("supersecret", text)
        self.assertNotIn("192.168", text)

    def test_digest_refuses_text(self):
        with self.assertRaises(TypeError):
            commitset.digest("sk-abc")


# --- SEC-007 / SEC-007a: AC-015a's grep half and the scrub ------------------

class TlsConstruction(unittest.TestCase):

    def test_AC_015a_no_shipped_source_calls_the_convenience_constructor(self):
        # Asserted here as well as by tests/lint/no_default_ssl_context.sh. The
        # duplication is deliberate: the gate runs in the suite, but this test
        # runs under both interpreters and fails with a message that says what
        # the rule is for.
        banned = ("create_default_context", "_create_unverified_context",
                  "_create_stdlib_context")
        for path in _shipped_python_sources():
            source = _read_source(path)
            for line_number, line in enumerate(source.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for token in banned:
                    self.assertNotIn(
                        token, line,
                        "%s:%d calls %s, which assigns keylog_filename during "
                        "construction (SEC-007)" % (path, line_number, token))

    def test_AC_015a_keylog_filename_is_never_assigned(self):
        for path in _shipped_python_sources():
            for line_number, line in enumerate(_read_source(path).splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                self.assertNotRegex(
                    line, r"keylog_filename\s*=",
                    "%s:%d assigns keylog_filename" % (path, line_number))

    def test_AC_015a_a_built_context_has_no_keylog_file(self):
        with _patched_env(SSLKEYLOGFILE=os.path.join(tempfile.gettempdir(),
                                                     "omarchy-unifi-keylog-must-not-exist")):
            target = os.environ["SSLKEYLOGFILE"]
            if os.path.exists(target):
                os.unlink(target)
            for context in (tlsctx.build_context(), tlsctx.build_context(allow_insecure=True)):
                self.assertIsNone(context.keylog_filename)
            self.assertFalse(os.path.exists(target),
                             "a key-log file was created; session keys for a "
                             "credential-bearing request would be written to it")

    def test_SEC_007a_all_three_variables_are_removed_before_any_context(self):
        with _patched_env(SSLKEYLOGFILE="/tmp/k", SSL_CERT_FILE="/tmp/c",
                          SSL_CERT_DIR="/tmp/d"):
            tlsctx.build_context()
            for name in tlsctx.SCRUBBED_ENV_VARS:
                self.assertNotIn(name, os.environ, name + " survived the scrub")

    def test_SEC_007a_the_scrub_happens_inside_the_builder(self):
        # Not "the caller scrubs first". A scrub the caller has to remember is
        # a scrub a future code path will skip.
        for kwargs in ({}, {"allow_insecure": True}):
            with _patched_env(SSL_CERT_FILE="/tmp/attacker.pem"):
                tlsctx.build_context(**kwargs)
                self.assertNotIn("SSL_CERT_FILE", os.environ)

    def test_the_verifying_context_is_explicit_about_every_setting(self):
        context = tlsctx.build_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_SEC_005_the_insecure_context_is_opt_in_and_built_the_same_way(self):
        context = tlsctx.build_context(allow_insecure=True)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        # Same constructor, so it cannot write session keys either. The path
        # taken during troubleshooting is the last place to relax this.
        self.assertIsNone(context.keylog_filename)

    def test_the_scrub_reports_what_it_removed(self):
        environ = {"SSLKEYLOGFILE": "a", "SSL_CERT_DIR": "b", "UNRELATED": "c"}
        removed = tlsctx.scrub_environment(environ)
        self.assertEqual(sorted(removed), ["SSLKEYLOGFILE", "SSL_CERT_DIR"])
        self.assertEqual(environ, {"UNRELATED": "c"})


# --- AC-015b / AC-016: against a real handshake -----------------------------

class TlsVerification(TempTree):

    def setUp(self):
        super(TlsVerification, self).setUp()
        # Not a skip. See tests/tools/tls_stub.py: a machine without openssl
        # would otherwise report a green suite for the checks that decide
        # whether an attacker CA can intercept the API key.
        tls_stub.require_openssl()
        self.ca = tls_stub.mint_ca(self.root, "good")
        self.cert, self.key = tls_stub.mint_server_cert(self.root, self.ca)

    def test_AC_016_the_accepted_case_verifies_against_the_configured_ca(self):
        pem = paths.read_custom_ca(self.ca.cert_path)
        with tls_stub.TlsStub(self.cert, self.key) as stub:
            peer, body = stub.connect(tlsctx.build_context(ca_pem=pem))
        self.assertTrue(peer, "a verified handshake must expose the peer certificate")
        self.assertIn(b'{"ok":true}', body)

    def test_a_configured_ca_REPLACES_default_trust_rather_than_adding_to_it(self):
        # The gap a mutation pass found: every other test here signs with a
        # PRIVATE CA, which the system store does not trust either way — so a
        # build_context that loaded the system store IN ADDITION to the
        # configured CA passed all of them. It would also accept a controller
        # certificate signed by any of the ~120 public roots, which is exactly
        # what pinning to a private CA is meant to prevent.
        pem = paths.read_custom_ca(self.ca.cert_path)
        pinned = tlsctx.build_context(ca_pem=pem).get_ca_certs()
        self.assertEqual(len(pinned), 1,
                         "a pinned context must trust the configured CA and nothing else")

        default = tlsctx.build_context().get_ca_certs()
        self.assertGreater(len(default), 1,
                           "this assertion is only meaningful while the system "
                           "store is non-empty; it is what `pinned` is compared against")

    def test_the_configured_ca_does_not_widen_trust_to_anything_else(self):
        other = tls_stub.mint_ca(self.root, "other")
        other_cert, other_key = tls_stub.mint_server_cert(self.root, other)
        pem = paths.read_custom_ca(self.ca.cert_path)
        with tls_stub.TlsStub(other_cert, other_key) as stub:
            with self.assertRaises(ssl.SSLError):
                stub.connect(tlsctx.build_context(ca_pem=pem))

    def test_AC_015b_an_attacker_ca_in_SSL_CERT_FILE_is_not_trusted(self):
        # The criterion by name, and the reason SEC-007a calls the variables
        # load-bearing rather than hygiene. OpenSSL honours SSL_CERT_FILE when
        # default trust is loaded, so without the scrub this handshake would
        # SUCCEED and the X-API-Key would go to the attacker while
        # allowInsecureTls was still false.
        attacker = tls_stub.mint_ca(self.root, "attacker")
        attacker_cert, attacker_key = tls_stub.mint_server_cert(self.root, attacker)

        with _patched_env(SSL_CERT_FILE=attacker.cert_path):
            with tls_stub.TlsStub(attacker_cert, attacker_key) as stub:
                context = tlsctx.build_context()      # allowInsecureTls == false
                self.assertNotIn("SSL_CERT_FILE", os.environ)
                with self.assertRaises(ssl.SSLError):
                    stub.connect(context)

    def test_AC_015b_an_attacker_ca_in_SSL_CERT_DIR_is_not_trusted_either(self):
        attacker = tls_stub.mint_ca(self.root, "attacker-dir")
        attacker_cert, attacker_key = tls_stub.mint_server_cert(self.root, attacker)
        with _patched_env(SSL_CERT_DIR=self.root):
            with tls_stub.TlsStub(attacker_cert, attacker_key) as stub:
                with self.assertRaises(ssl.SSLError):
                    stub.connect(tlsctx.build_context())

    def test_the_insecure_opt_in_really_does_accept_an_untrusted_chain(self):
        # UX-009 raises a persistent warning for exactly this. The test exists
        # so the warning is never mistaken for the whole protection.
        attacker = tls_stub.mint_ca(self.root, "any")
        cert, key = tls_stub.mint_server_cert(self.root, attacker)
        with tls_stub.TlsStub(cert, key) as stub:
            peer, body = stub.connect(tlsctx.build_context(allow_insecure=True))
        self.assertEqual(peer, {}, "CERT_NONE means there is nothing verified to show")
        self.assertIn(b'{"ok":true}', body)


# --- SEC-006 / AC-016: the CA file's negative matrix -------------------------

class CustomCaFile(TempTree):

    def setUp(self):
        super(CustomCaFile, self).setUp()
        self.holder = os.path.join(self.root, "ca-holder")
        os.mkdir(self.holder, 0o755)
        self.ca_path = os.path.join(self.holder, "ca.pem")
        with open(self.ca_path, "w") as handle:
            handle.write("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
        os.chmod(self.ca_path, 0o644)

    def test_the_accepted_case_reads_and_returns_the_captured_text(self):
        # SEC-006 requires the CONTENTS to be captured: a pathname validated and
        # then handed back to the SSL library to reopen is a TOCTOU window with
        # extra steps.
        text = paths.read_custom_ca(self.ca_path)
        self.assertIn("BEGIN CERTIFICATE", text)
        self.assertIsInstance(text, str)

    def test_a_world_readable_ca_is_fine(self):
        # Unlike the credential. A CA bundle is public material, so the rule is
        # about who can WRITE it, and applying SEC-002's mask here would reject
        # /etc/ssl/certs/ca-certificates.crt.
        os.chmod(self.ca_path, 0o644)
        paths.read_custom_ca(self.ca_path)

    def test_AC_016_a_symlinked_ca_is_rejected(self):
        link = os.path.join(self.holder, "link.pem")
        os.symlink(self.ca_path, link)
        with self.assertRaises(errors.TlsError):
            paths.read_custom_ca(link)

    def test_AC_016_an_oversized_ca_is_rejected(self):
        with open(self.ca_path, "wb") as handle:
            handle.write(b"-" * (paths.CA_MAX_BYTES + 1))
        with self.assertRaises(errors.TlsError) as caught:
            paths.read_custom_ca(self.ca_path)
        self.assertIn("limit", caught.exception.message)

    def test_a_ca_at_exactly_the_bound_is_accepted(self):
        with open(self.ca_path, "wb") as handle:
            handle.write(b"-" * paths.CA_MAX_BYTES)
        self.assertEqual(len(paths.read_custom_ca(self.ca_path)), paths.CA_MAX_BYTES)

    def test_AC_016_a_group_writable_ca_is_rejected(self):
        for mode in (0o664, 0o646, 0o666, 0o622):
            os.chmod(self.ca_path, mode)
            with self.assertRaises(errors.TlsError):
                paths.read_custom_ca(self.ca_path)

    def test_AC_016_a_group_writable_parent_is_rejected(self):
        os.chmod(self.holder, 0o775)
        with self.assertRaises(errors.TlsError) as caught:
            paths.read_custom_ca(self.ca_path)
        self.assertIn(self.holder, caught.exception.message)

    def test_AC_016_an_other_writable_parent_is_rejected(self):
        os.chmod(self.holder, 0o757)
        with self.assertRaises(errors.TlsError):
            paths.read_custom_ca(self.ca_path)

    def test_a_sticky_world_writable_parent_is_accepted(self):
        # /tmp is 1777 on every Linux system. Sticky means only an entry's
        # owner can rename or delete it, so the risk the parent walk guards
        # against does not exist. Rejecting this would make the check refuse
        # something safe, which is how a check gets deleted.
        os.chmod(self.holder, 0o1777)
        paths.read_custom_ca(self.ca_path)

    def test_AC_016_a_ca_owned_by_neither_root_nor_the_user_is_rejected(self):
        fake_uid = os.getuid() + 1000
        with _patched(os, "getuid", lambda: fake_uid):
            with self.assertRaises(errors.TlsError) as caught:
                paths.read_custom_ca(self.ca_path)
        self.assertIn("neither root nor you", caught.exception.message)

    def test_a_relative_path_is_rejected(self):
        with self.assertRaises(errors.TlsError):
            paths.read_custom_ca("ca.pem")

    def test_a_missing_ca_is_a_tls_error_not_a_crash(self):
        with self.assertRaises(errors.TlsError):
            paths.read_custom_ca(os.path.join(self.holder, "absent.pem"))

    def test_a_non_ascii_ca_is_rejected(self):
        with open(self.ca_path, "wb") as handle:
            handle.write(b"\xff\xfe not pem")
        with self.assertRaises(errors.TlsError):
            paths.read_custom_ca(self.ca_path)

    def test_a_ca_that_is_not_a_trust_anchor_fails_in_the_builder(self):
        with self.assertRaises(errors.TlsError):
            tlsctx.build_context(ca_pem="-----BEGIN CERTIFICATE-----\nnope\n"
                                        "-----END CERTIFICATE-----\n")


# --- section 11: the interpreter floor --------------------------------------

class InterpreterFloor(unittest.TestCase):

    def test_the_helper_runs_on_the_declared_floor(self):
        # The suite runs under 3.9.25 and 3.14.7. This asserts the floor itself
        # rather than trusting the runner to have used both.
        self.assertGreaterEqual(sys.version_info[:2], (3, 9),
                                "section 11 declares Python 3.9 as the floor")

    def test_no_shipped_source_needs_anything_newer_than_the_floor(self):
        # A syntax-level check. `match`, `X | Y` annotations and dataclass
        # slots= would all import fine on 3.14 and fail on 3.9, which is the
        # interpreter a user is most likely to have.
        import ast
        for path in _shipped_python_sources():
            ast.parse(_read_source(path), filename=path)

    def test_the_package_is_sealed_against_a_namespace_merge(self):
        # `unifi` is a real name on PyPI. As a PEP 420 namespace package this
        # one would MERGE with any other `unifi` directory later on sys.path —
        # and `-s` removes the user site directory but not the system one. The
        # failure is silent: `from unifi import tlsctx` resolves to somebody
        # else's file, cleanly, in the process holding the API key.
        import unifi
        self.assertTrue(hasattr(unifi, "__file__") and unifi.__file__,
                        "unifi is a namespace package; add helper/unifi/__init__.py")
        self.assertEqual(
            list(unifi.__path__), [os.path.join(_REPO, "helper", "unifi")],
            "unifi.__path__ spans more than this repository's own directory")

    def test_the_package_imports_with_no_third_party_dependency(self):
        # Section 11: standard library only, no pip packages, because Omarchy's
        # plugin installer does not install runtime packages.
        import ast
        allowed = {"os", "ssl", "stat", "json", "hashlib", "hmac", "io", "sys",
                   "socket", "errno", "time", "re", "base64", "urllib", "typing",
                   "collections", "datetime", "unicodedata", "subprocess",
                   "email", "http", "argparse", "fcntl", "tempfile",
                   # DEV-6: `ipaddress.is_global` decides whether a device is
                   # reporting a WAN address. Hand-rolling the RFC 1918 test
                   # would also have to hand-roll loopback, link-local, CGNAT
                   # and the IPv6 equivalents.
                   "ipaddress",
                   # The helper's own package. `unifi_status.py` is a SCRIPT,
                   # not a module inside the package, so it cannot use a
                   # relative import; the explicit sys.path bootstrap above it
                   # is what makes this absolute one resolve.
                   "unifi"}
        for path in _shipped_python_sources():
            tree = ast.parse(_read_source(path), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertIn(root, allowed, "%s imports %s" % (path, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level:            # a relative import within the package
                        continue
                    root = (node.module or "").split(".")[0]
                    self.assertIn(root, allowed, "%s imports %s" % (path, node.module))


# --- section 8/9/10: routes, transport, pagination, taxonomy (Phase 6) ------

class ErrorTaxonomy(unittest.TestCase):
    """DATA-007 / DATA-007a, producer side.

    Protocol.js rejects an inconsistent error object; this is the other half —
    the helper must be unable to BUILD one. A test that only checked the
    consumer would leave the producer free to emit `credential` with a 429 and
    call the resulting rejection somebody else's problem.
    """

    def test_all_nineteen_data_007_kinds_are_present_exactly_once(self):
        self.assertEqual(len(errors.KINDS), 19)
        self.assertEqual(len(set(errors.KINDS)), 19)

    def test_every_kind_names_its_retry_class(self):
        # The DATA-007a matrix, longhand. Written out rather than looped over
        # errors.RETRY_CLASS, because a loop derives its expectation from the
        # table it is checking and would pass on any self-consistent table.
        expected = {
            "unconfigured": "fatal",
            "site_unselected": "fatal",
            "uncommitted": "fatal",
            "credential": "fatal",
            "unauthorized": "fatal",
            "forbidden": "fatal",
            "tls": "fatal",
            "network": "transient",
            "timeout": "transient",
            "rate_limited": "transient",
            "unsupported": "fatal",
            "redirect": "integrity",
            "configuration_conflict": "fatal",
            "partial_response": "integrity",
            "oversized_response": "integrity",
            "malformed_response": "integrity",
            "helper_unavailable": "fatal",
            "internal": "integrity",
        }
        for kind, retry_class in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(errors.retry_class_for(kind), retry_class)
                self.assertEqual(errors.retryable_for(kind), retry_class != "fatal")
        # `http` is deliberately absent above: its class depends on its status.
        self.assertNotIn("http", expected)
        self.assertNotIn("http", errors.RETRY_CLASS)

    def test_http_is_classified_by_its_status(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(errors.retry_class_for("http", status), "transient")
                self.assertTrue(errors.retryable_for("http", status))
        for status in (400, 404, 418, 501, 505):
            with self.subTest(status=status):
                self.assertEqual(errors.retry_class_for("http", status), "fatal")
                self.assertFalse(errors.retryable_for("http", status))

    def test_an_unknown_kind_takes_internals_class(self):
        # DATA-007's forward-compatibility mapping. A newer helper's kind must
        # not brick an older consumer, and must not be treated as fatal either.
        self.assertEqual(errors.retry_class_for("something_new_in_v2"), "integrity")

    def test_http_status_is_forbidden_where_the_matrix_forbids_it(self):
        for kind in errors.KINDS:
            if kind in ("unauthorized", "forbidden", "rate_limited", "http"):
                continue
            with self.subTest(kind=kind):
                with self.assertRaises(errors.InconsistentError):
                    errors.error_object(kind, "m", http_status=429)
                built = errors.error_object(kind, "m")
                self.assertNotIn("httpStatus", built)

    def test_typed_statuses_are_pinned_to_their_kinds(self):
        self.assertEqual(errors.error_object("unauthorized", "m")["httpStatus"], 401)
        self.assertEqual(errors.error_object("forbidden", "m")["httpStatus"], 403)
        self.assertEqual(errors.error_object("rate_limited", "m")["httpStatus"], 429)
        with self.assertRaises(errors.InconsistentError):
            errors.error_object("unauthorized", "m", http_status=403)

    def test_http_may_not_carry_a_status_that_has_its_own_kind(self):
        # An `http` error carrying 401 means the producer skipped the typed
        # mapping DATA-007 requires, so the service would reject the envelope.
        for status in (401, 403, 429):
            with self.subTest(status=status):
                with self.assertRaises(errors.InconsistentError):
                    errors.error_object("http", "m", http_status=status)
        self.assertEqual(errors.error_object("http", "m", http_status=502)["httpStatus"], 502)

    def test_http_requires_an_integer_status_in_range(self):
        for bad in (None, "502", True, 99, 600):
            with self.subTest(status=bad):
                with self.assertRaises(errors.InconsistentError):
                    errors.error_object("http", "m", http_status=bad)

    def test_retry_after_belongs_only_to_rate_limited(self):
        for kind in errors.KINDS:
            if kind == "rate_limited":
                continue
            with self.subTest(kind=kind):
                with self.assertRaises(errors.InconsistentError):
                    if kind == "http":
                        errors.error_object(kind, "m", http_status=502,
                                            retry_after_sec=30)
                    else:
                        errors.error_object(kind, "m", retry_after_sec=30)
        self.assertEqual(
            errors.error_object("rate_limited", "m", retry_after_sec=30)["retryAfterSec"], 30)

    def test_retryable_is_derived_and_cannot_be_supplied(self):
        # DATA-007a requires `retryable` to equal the kind's class. There is no
        # argument for it, so no call site can contradict the matrix.
        import inspect
        signature = inspect.signature(errors.error_object)
        self.assertNotIn("retryable", signature.parameters)

    def test_every_exception_class_reports_a_kind_in_the_taxonomy(self):
        for name in dir(errors):
            value = getattr(errors, name)
            if isinstance(value, type) and issubclass(value, errors.HelperError):
                with self.subTest(exception=name):
                    self.assertIn(value.kind, errors.KINDS)

    def test_to_error_object_round_trips_a_raised_exception(self):
        built = errors.to_error_object(
            errors.RateLimitedError("slow down", retry_after_sec=45))
        self.assertEqual(built, {"kind": "rate_limited", "message": "slow down",
                                 "retryable": True, "httpStatus": 429,
                                 "retryAfterSec": 45})
        built = errors.to_error_object(errors.HttpError("bad gateway", 502))
        self.assertEqual(built["kind"], "http")
        self.assertEqual(built["httpStatus"], 502)
        self.assertTrue(built["retryable"])


class Sanitization(unittest.TestCase):
    """SEC-010. Nothing the helper says carries a credential, a header or a URL."""

    # Assembled at runtime from halves, so the key-shaped literal exists only
    # while this test runs and never in a tracked file. tests/lint/secrets.sh
    # does the same with its canary, and for the same reason: a test about
    # credential material must not be the thing that puts credential material
    # into the repository.
    FAKE_TOKEN = "sk-" + "live-" + "abcdefghijklmnop"

    def test_a_message_never_carries_a_credential_shaped_fragment(self):
        for hostile in ("X-API-" + "Key: " + self.FAKE_TOKEN,
                        "api_" + "key=" + self.FAKE_TOKEN,
                        "Authorization: " + "Bearer " + self.FAKE_TOKEN):
            with self.subTest(text=hostile):
                cleaned = sanitize.message(hostile)
                self.assertNotIn(self.FAKE_TOKEN, cleaned)
                self.assertIn(sanitize.REDACTED, cleaned)

    def test_a_message_never_carries_a_url(self):
        cleaned = sanitize.message("failed at https://192.168.1.1/proxy/network/x")
        self.assertNotIn("192.168.1.1", cleaned)
        self.assertIn(sanitize.ELIDED_URL, cleaned)

    def test_control_characters_cannot_forge_a_second_log_line(self):
        cleaned = sanitize.message("device\r\nWARNING: everything is fine")
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\r", cleaned)

    def test_non_whitespace_control_bytes_are_stripped_too(self):
        # CR and LF are also whitespace, so the whitespace collapse alone
        # satisfies the test above and the control-character strip could be
        # deleted without failing it. These are the bytes only the strip
        # catches: a terminal escape in a device name, and NUL.
        cleaned = sanitize.clean("device\x1b[31mRED\x1b[0m\x00\x07 name")
        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\x07", cleaned)
        self.assertIn("name", cleaned)

    def test_strings_are_bounded_on_both_limits(self):
        self.assertEqual(len(sanitize.clean("a" * 5000)), sanitize.STRING_MAX_CHARS)
        self.assertEqual(len(sanitize.message("a" * 5000)), sanitize.MESSAGE_MAX_CHARS)
        self.assertTrue(sanitize.message("a" * 5000).endswith(sanitize.TRUNCATION_MARKER))

    def test_an_exception_is_described_by_type_never_by_its_text(self):
        # The rule that makes SEC-010 hold without a scrubber: str(exc) is never
        # interpolated, so a host, a path or a certificate subject inside an
        # exception cannot reach an envelope at all.
        marker = "10.9.8.7 " + self.FAKE_TOKEN
        # Every branch, including the fallback. PEP 3151 makes OSError(111) a
        # ConnectionRefusedError, so a first version of this test only ever
        # reached the phrase book and the fallback could return str(exc)
        # unnoticed. The last two entries are types the book does not name.
        bearers = [
            OSError(errno.ECONNREFUSED, "connect refused for " + marker),
            ssl.SSLCertVerificationError("hostname " + marker + " does not match"),
            socket.gaierror(socket.EAI_NONAME, "no address for " + marker),
            OSError(errno.EACCES, "denied for " + marker),
            ValueError("unexpected " + marker),
        ]
        for exc in bearers:
            with self.subTest(exception=type(exc).__name__):
                described = sanitize.describe_exception(exc)
                self.assertNotIn("10.9.8.7", described)
                self.assertNotIn(self.FAKE_TOKEN, described)

    def test_the_host_is_extracted_without_userinfo_and_with_its_port(self):
        self.assertEqual(
            sanitize.host_of("https://user:pass@192.168.1.1:8443/proxy"),
            "192.168.1.1:8443")
        self.assertEqual(sanitize.host_of("https://192.168.1.1/proxy"), "192.168.1.1")
        self.assertIsNone(sanitize.host_of("not a url"))


class Bounds(unittest.TestCase):
    """SPEC-v1.1-browse.md DATA-B04. The guard rail, and where it sits."""

    def test_the_budget_is_strictly_below_the_stdout_cliff(self):
        """AC-B12a.

        `envelope.encode` enforces DATA-005 as a CLIFF: one byte over and the
        whole envelope is replaced by an `oversized_response` failure, so the
        panel greys and the user gets no reading rather than a shorter list.
        The budget exists to make that path unreachable, which it can only do
        from strictly below it.
        """
        self.assertLess(bounds.ENVELOPE_BUDGET_BYTES, envelope.STDOUT_MAX_BYTES)
        self.assertGreaterEqual(
            envelope.STDOUT_MAX_BYTES - bounds.ENVELOPE_BUDGET_BYTES, 16 * 1024,
            "the headroom absorbs separator overhead, \\uXXXX expansion of "
            "non-ASCII names, and the warning that truncation itself appends")

    def test_the_two_ceilings_are_declared_independently(self):
        """AC-B12a's second half, and the reason this test exists at all.

        If `bounds.py` imported the stdout bound and derived its own from it,
        the assertion above would hold by construction and prove nothing — and
        raising DATA-005 would silently raise the guard rail with it. They are
        two literals in two modules, and this is what notices when only one of
        them moves.
        """
        with io.open(os.path.join(_REPO, "helper", "unifi", "bounds.py"),
                     encoding="utf-8") as handle:
            source = handle.read()
        # Comments stripped first. The module explains in prose exactly which
        # constant it is deliberately not importing, and a naive substring
        # search would flag the explanation as the violation it warns about.
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        self.assertNotIn("import envelope", code)
        self.assertNotIn("envelope.STDOUT_MAX_BYTES", code)
        self.assertEqual(bounds.STDOUT_MAX_BYTES, envelope.STDOUT_MAX_BYTES,
                         "bounds.py records a different DATA-005 than envelope.py")

    def test_the_room_for_lists_is_measured_and_not_reserved(self):
        """The replacement for a test that could not fail.

        Its predecessor asserted `LIST_BUDGET > 0` and that a subtraction had
        been performed — a tautology that survived setting the reserve to
        ZERO, which is how a constant wrong by up to thirty times went
        unnoticed. These assertions are about the RELATIONSHIP between fixed
        content and the room left for lists, which is the property that
        matters.
        """
        # Bigger fixed content, strictly less room. Monotone, and the direction
        # a reserve-based version got wrong by not looking at all.
        small = bounds.room_for_lists(1024)
        large = bounds.room_for_lists(64 * 1024)
        self.assertGreater(small, large)
        self.assertEqual(small - large, 63 * 1024)

        # The room plus everything it must coexist with never exceeds the
        # budget, which is the arithmetic the guarantee rests on.
        for fixed in (0, 1024, 64 * 1024, 150 * 1024):
            with self.subTest(fixed=fixed):
                room = bounds.room_for_lists(fixed)
                self.assertLessEqual(
                    room + fixed + bounds.WARNINGS_RESERVE_BYTES
                    + bounds.FRAME_RESERVE_BYTES,
                    bounds.ENVELOPE_BUDGET_BYTES)

        # Fixed content that fills the envelope leaves no room at all, rather
        # than a negative number a Budget would treat as unlimited.
        self.assertEqual(bounds.room_for_lists(bounds.ENVELOPE_BUDGET_BYTES), 0)
        self.assertEqual(bounds.room_for_lists(10 * 1024 * 1024), 0)

    def test_a_record_that_does_not_fit_is_never_added(self):
        # `admits` before `spend`, never spend-then-remove: removing afterwards
        # leaves the arithmetic right and the ORDER wrong, because the dropped
        # record would be the last one considered rather than the least
        # important one.
        budget = bounds.Budget(limit=200)
        record = {"id": "x" * 50}
        size = bounds.encoded_size(record)
        self.assertTrue(budget.admits(record))
        budget.spend(record)
        self.assertEqual(budget.used, size + 1)
        big = {"id": "y" * 500}
        self.assertFalse(budget.admits(big))
        self.assertEqual(budget.used, size + 1, "a refused record still cost bytes")

    def test_admits_and_spend_agree_on_what_a_record_costs(self):
        # Dropping the `+ 1` from `admits` while leaving it in `spend` survived
        # mutation. The comment defends that byte at length — half a kilobyte
        # across 500 clients — and nothing checked the two functions agreed, so
        # `admits` could say yes to a record `spend` then over-charged for,
        # walking `used` past `limit`.
        budget = bounds.Budget(limit=10 ** 6)
        record = {"id": "x" * 40, "name": "y" * 40}
        before = budget.used
        budget.spend(record)
        cost = budget.used - before

        probe = bounds.Budget(limit=before + cost)
        probe.used = before
        self.assertTrue(probe.admits(record),
                        "admits refused a record that exactly fits")
        tight = bounds.Budget(limit=before + cost - 1)
        tight.used = before
        self.assertFalse(tight.admits(record),
                         "admits accepted a record one byte too large")

    def test_a_budget_never_walks_past_its_own_limit(self):
        budget = bounds.Budget(limit=500)
        records = [{"id": "device-%03d" % i, "pad": "x" * 20} for i in range(100)]
        for record in records:
            if budget.admits(record):
                budget.spend(record)
        self.assertLessEqual(budget.used, budget.limit)

    def test_the_projection_matches_how_the_envelope_is_actually_written(self):
        # An optimistic projection is worse than none: it would let the cliff be
        # reached anyway. `encoded_size` must use the same separators and
        # ensure_ascii as `envelope.encode`, and a non-ASCII name is where the
        # defaults differ most — six bytes out for two bytes in.
        record = {"name": "Café Ätelier \u00fc"}
        self.assertEqual(
            bounds.encoded_size(record),
            len(json.dumps(record, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True).encode("utf-8")))
        self.assertGreater(bounds.encoded_size(record),
                           len(json.dumps(record).encode("utf-8")) - 20)

    def test_bounded_list_reports_the_true_total_not_the_kept_length(self):
        # REQ-010's rule, generalised. The warning's `total` comes from the
        # count carried independently, so a caller holding a list that was
        # already shortened upstream cannot understate the site.
        collector = warn.Warnings()
        records = [{"id": "device-%03d" % i} for i in range(10)]
        kept, dropped = bounds.bounded_list(records, cap=3,
                                            budget=bounds.Budget(),
                                            warnings=collector,
                                            code="devices_truncated",
                                            total=412)
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, 409)
        entry = [w for w in collector.to_list() if w["code"] == "devices_truncated"]
        self.assertEqual(entry[0]["detail"], {"listed": 3, "total": 412})

    def test_bounded_list_stops_on_the_budget_before_the_cap(self):
        collector = warn.Warnings()
        records = [{"id": "device-%03d" % i} for i in range(50)]
        kept, dropped = bounds.bounded_list(records, cap=50,
                                            budget=bounds.Budget(limit=100),
                                            warnings=collector,
                                            code="devices_truncated")
        self.assertGreater(len(kept), 0, "the budget admitted nothing at all")
        self.assertLess(len(kept), 50, "the budget did not bind")
        self.assertEqual(dropped, 50 - len(kept))


class BrowseRecords(unittest.TestCase):
    """DATA-B01's per-device mapping, at the edges the corpus does not reach."""

    def _built(self, devices, **kwargs):
        return normalize.build({"id": normalize_inputs.uid(1), "name": "Home"},
                               devices, None, kwargs.pop("statistics", {}),
                               "9.1.0", warn.Warnings(),
                               listed_devices=devices, **kwargs)

    def test_a_port_table_past_the_bound_is_truncated_not_rejected(self):
        # The corpus has no device with more than 64 ports, so removing this
        # truncation changed no fixture and the mutation survived. A 96-port
        # chassis nobody has shipped yet should cost the user the ports past
        # the bound, not the whole reading — so the producer truncates and the
        # consumer's `bound_exceeded` is the backstop for a producer that did
        # not.
        device = normalize_inputs.browse_device(60, "ONLINE",
                                                normalize_inputs.SWITCH)
        detail = normalize_inputs.detail(ports=96)
        data = self._built([device],
                           details={normalize_inputs.uid(60): detail})
        entry = data["devices"][0]
        self.assertEqual(len(entry["detail"]["ports"]),
                         normalize.PORTS_PER_DEVICE_MAX)
        # The ones kept are the FIRST, so port 1 is present and 96 is not.
        self.assertEqual(entry["detail"]["ports"][0]["idx"], 1)

    def test_a_radio_table_past_the_bound_is_truncated(self):
        device = normalize_inputs.browse_device(61, "ONLINE",
                                                normalize_inputs.ACCESS_POINT)
        detail = normalize_inputs.detail(radios=20)
        data = self._built([device],
                           details={normalize_inputs.uid(61): detail})
        self.assertEqual(len(data["devices"][0]["detail"]["radios"]),
                         normalize.RADIOS_PER_DEVICE_MAX)

    def test_a_fractional_metric_is_not_coerced_to_an_integer(self):
        # `_number`, not `_count`. Utilisation percentages and radio
        # frequencies are fractional, and truncating 4.5 to 4 would be a silent
        # lie about a number the panel prints.
        device = normalize_inputs.browse_device(62, "ONLINE",
                                                normalize_inputs.SWITCH)
        data = self._built(
            [device],
            statistics={normalize_inputs.uid(62): normalize_inputs.stats(
                100, 1, 2, cpu=4.5, memory=38.25)})
        metrics = data["devices"][0]["metrics"]
        self.assertEqual(metrics["cpuUtilizationPct"], 4.5)
        self.assertEqual(metrics["memoryUtilizationPct"], 38.25)

    def test_a_non_boolean_updatable_flag_is_null_not_passed_through(self):
        # `_as_bool` reduced to `return value` survived: a controller sending
        # `firmwareUpdatable: "yes"` would reach the panel as the string, and
        # `Protocol.js` does not type-check the field either. The panel renders
        # a truthy string as "update available", so a controller quirk becomes a
        # claim about the user's firmware.
        for bogus in ("yes", 1, 0, "", None, [], {}):
            with self.subTest(value=bogus):
                device = dict(normalize_inputs.browse_device(
                    66, "ONLINE", normalize_inputs.SWITCH))
                device["firmwareUpdatable"] = bogus
                entry = self._built([device])["devices"][0]
                self.assertIsNone(entry["firmwareUpdatable"])
        for real in (True, False):
            device = dict(normalize_inputs.browse_device(
                67, "ONLINE", normalize_inputs.SWITCH))
            device["firmwareUpdatable"] = real
            self.assertIs(self._built([device])["devices"][0]["firmwareUpdatable"],
                          real)

    def test_a_detail_body_that_is_not_an_object_is_null_not_a_crash(self):
        # The `isinstance(detail, dict)` guard weakened to `detail is None`
        # survived: a list-valued detail would then reach `.get` and raise.
        for bogus in ([], "string", 42, True):
            with self.subTest(value=bogus):
                device = normalize_inputs.browse_device(
                    68, "ONLINE", normalize_inputs.SWITCH)
                data = self._built([device],
                                   details={normalize_inputs.uid(68): bogus})
                self.assertIsNone(data["devices"][0]["detail"])
                self.assertIsNone(data["devices"][0]["uplinkDeviceId"])

    def test_client_and_device_identifiers_go_through_the_sanitizer(self):
        # Dropping `sanitize.clean` from the client id survived. Every string
        # that reaches the wire is bounded and control-character-stripped by
        # SEC-008; an id is not exempt just because it is usually a uuid.
        long_id = "x" * (sanitize.STRING_MAX_CHARS + 50)
        record = normalize_inputs.client(0, "n", "WIRED")
        record["id"] = long_id + "\r\n"
        data = normalize.build({"id": normalize_inputs.uid(1), "name": "Home"},
                               [], 1, {}, "9.1.0", warn.Warnings(),
                               client_records=[record])
        emitted = data["clients"][0]["id"]
        self.assertLessEqual(len(emitted), sanitize.STRING_MAX_CHARS)
        self.assertNotIn("\r", emitted)
        self.assertNotIn("\n", emitted)

    def test_a_boolean_is_not_read_as_a_number(self):
        # `True` is an int in Python, so a `firmwareUpdatable` that leaked into
        # a numeric field would arrive as 1. `_number` rejects bools first.
        self.assertIsNone(normalize._number(True))
        self.assertIsNone(normalize._number("4.5"))
        self.assertEqual(normalize._number(0), 0)

    def test_detail_and_metrics_are_null_when_not_fetched(self):
        device = normalize_inputs.browse_device(63, "ONLINE",
                                                normalize_inputs.SWITCH)
        entry = self._built([device])["devices"][0]
        self.assertIsNone(entry["detail"])
        self.assertIsNone(entry["metrics"])
        self.assertIsNone(entry["uplinkDeviceId"])
        # The KEYS are present. Absent and null are different statements and
        # the consumer rejects the first (Protocol.js checkDeviceRecord).
        for key in ("detail", "metrics", "uplinkDeviceId"):
            self.assertIn(key, entry)

    def test_an_empty_port_table_is_not_the_same_as_no_detail(self):
        device = normalize_inputs.browse_device(64, "ONLINE",
                                                normalize_inputs.ACCESS_POINT)
        entry = self._built(
            [device],
            details={normalize_inputs.uid(64): normalize_inputs.detail()},
        )["devices"][0]
        self.assertIsNotNone(entry["detail"])
        self.assertEqual(entry["detail"]["ports"], [])

    def test_the_order_is_case_insensitive_on_name(self):
        # REQ-B11 says "case-insensitively". Removing `.lower()` from the key
        # changed no test: every corpus name happens to be capitalised the same
        # way, so ASCII order and case-insensitive order agreed.
        # The pair has to be one where ASCII order and case-insensitive order
        # DISAGREE. A first attempt used "Apple" and "zebra", which sort the
        # same way under both — so the test passed and the mutation removing
        # `.lower()` survived it. Lowercase 'a' is 0x61 and uppercase 'Z' is
        # 0x5a, so this pair inverts between the two rules.
        devices = [normalize_inputs.browse_device(70, "ONLINE",
                                                  normalize_inputs.SWITCH, "Zebra"),
                   normalize_inputs.browse_device(71, "ONLINE",
                                                  normalize_inputs.SWITCH, "apple")]
        order = [d["name"] for d in normalize.browse_order(devices)]
        self.assertEqual(order, ["apple", "Zebra"],
                         "byte order would put Zebra first")

    def test_the_id_is_the_final_tiebreaker(self):
        # Two devices alike in class, role and name. Without the id key the
        # order follows the controller's page order, and the list is
        # byte-truncated — so a tie would decide which of them the user sees.
        devices = [normalize_inputs.browse_device(81, "ONLINE",
                                                  normalize_inputs.SWITCH, "twin"),
                   normalize_inputs.browse_device(80, "ONLINE",
                                                  normalize_inputs.SWITCH, "twin")]
        self.assertEqual([d["id"] for d in normalize.browse_order(devices)],
                         [normalize_inputs.uid(80), normalize_inputs.uid(81)])
        # And it is stable under a reversed input, which page order can be.
        self.assertEqual(
            [d["id"] for d in normalize.browse_order(list(reversed(devices)))],
            [normalize_inputs.uid(80), normalize_inputs.uid(81)])

    def test_unknown_sorts_above_transitional(self):
        """The judgement REQ-B11 spends a paragraph on, and nothing pinned.

        A device in a state this build does not recognise is a thing to look at;
        an UPDATING one is not. It is the same judgement REQ-002 rule 4 makes,
        which reads `unknown` and does not read `transitional` — so swapping the
        two ranks would put the panel's list and the bar's colour at odds.
        """
        devices = [normalize_inputs.browse_device(90, "UPDATING",
                                                  normalize_inputs.SWITCH, "a"),
                   normalize_inputs.browse_device(91, "NO_SUCH_STATE",
                                                  normalize_inputs.SWITCH, "b"),
                   normalize_inputs.browse_device(92, "ISOLATED",
                                                  normalize_inputs.SWITCH, "c"),
                   normalize_inputs.browse_device(93, "OFFLINE",
                                                  normalize_inputs.SWITCH, "d"),
                   normalize_inputs.browse_device(94, "ONLINE",
                                                  normalize_inputs.SWITCH, "e")]
        self.assertEqual([d["name"] for d in normalize.browse_order(devices)],
                         ["d", "c", "b", "a", "e"])

    def test_a_non_string_name_does_not_crash_the_helper(self):
        # A malformed controller response is a bad reading, not a 30-second
        # watchdog timeout with nothing in any log.
        devices = [{"id": normalize_inputs.uid(95), "state": "ONLINE",
                    "name": 5, "features": []},
                   {"id": normalize_inputs.uid(96), "state": "ONLINE",
                    "name": {"nested": "object"}, "features": []},
                   normalize_inputs.browse_device(97, "ONLINE",
                                                  normalize_inputs.SWITCH, "ok")]
        self.assertEqual(len(normalize.browse_order(devices)), 3)
        data = self._built(devices)
        self.assertEqual(len(data["devices"]), 3)

    def test_a_device_the_consumer_would_reject_is_dropped_not_shipped(self):
        # `Protocol.js` refuses a device with no string id, and refusal is
        # WHOLE-ENVELOPE: the user loses the reading rather than the row.
        devices = [{"state": "ONLINE", "name": "no id here", "features": []},
                   normalize_inputs.browse_device(98, "ONLINE",
                                                  normalize_inputs.SWITCH, "fine")]
        data = self._built(devices)
        self.assertEqual([d["name"] for d in data["devices"]], ["fine"])
        # Still COUNTED, though: the counts come from `devices`, not this list.
        self.assertEqual(data["counts"]["devicesTotal"], 2)

    def test_a_client_the_consumer_would_reject_is_dropped_not_shipped(self):
        records = [{"name": "no id", "type": "WIRED"},
                   {"id": normalize_inputs.uid(99), "name": "no type"},
                   normalize_inputs.client(100, "fine", "WIRED")]
        data = normalize.build({"id": normalize_inputs.uid(1), "name": "Home"},
                               [], 3, {}, "9.1.0", warn.Warnings(),
                               client_records=records)
        self.assertEqual([c["name"] for c in data["clients"]], ["fine"])
        self.assertEqual(data["counts"]["clients"], 3)

    def test_the_client_list_is_clamped_to_its_own_count(self):
        """`list_exceeds_total` is a rejection class, and it is reachable.

        `counts.clients` is `totalCount` off the terminal page while the records
        accumulate across pages, so a client connecting mid-pagination produces
        one more record than the count. Clamping costs that client its row; not
        clamping costs the user the whole reading.
        """
        records = [normalize_inputs.client(200 + i, "c%d" % i, "WIRED")
                   for i in range(5)]
        data = normalize.build({"id": normalize_inputs.uid(1), "name": "Home"},
                               [], 3, {}, "9.1.0", warn.Warnings(),
                               client_records=records)
        self.assertEqual(len(data["clients"]), 3)
        self.assertEqual(data["counts"]["clients"], 3)

    def test_the_gateway_list_is_bounded_by_the_producer(self):
        """DATA-006's 64 was enforced only by the consumer, which rejects.

        Since SPEC-AMD-1 a device is a gateway if it reports an off-LAN address,
        so a site behind carrier-grade NAT can present dozens of them — and the
        failure mode was a grey panel rather than a shortened list.
        """
        devices = [normalize_inputs.device(300 + i, "ONLINE", ["gateway"],
                                           "GW %03d" % i)
                   for i in range(70)]
        collector = warn.Warnings()
        data = normalize.build({"id": normalize_inputs.uid(1), "name": "Home"},
                               devices, None, {}, "9.1.0", collector)
        self.assertEqual(len(data["gateways"]), 64)
        self.assertEqual(data["counts"]["gateways"]["online"], 70,
                         "the COUNT is the site's, not the list's")
        self.assertIn("gateway_list_truncated", collector.codes())

    def test_the_browse_class_matches_the_class_the_counters_used(self):
        # The two are rendered a few hundred pixels apart. Asserted over every
        # state the API defines, not over a sample.
        for state in ("ONLINE", "OFFLINE", "ISOLATED", "UPDATING", "NONSENSE"):
            with self.subTest(state=state):
                device = normalize_inputs.browse_device(
                    65, state, normalize_inputs.SWITCH)
                data = self._built([device])
                klass = data["devices"][0]["class"]
                self.assertEqual(data["counts"]["byClass"][klass], 1)


class WarningCodes(unittest.TestCase):
    """The closed set from docs/protocol-v1.md."""

    def test_the_code_set_is_exactly_the_documented_one(self):
        # Longhand, for the same reason as the retry-class table: a loop over
        # warn.CODES would agree with any set warn.CODES happened to hold.
        self.assertEqual(sorted(warn.CODES), sorted([
            "unknown_device_state", "site_auto_selected", "sites_discovered",
            "clients_unavailable", "statistics_unavailable",
            "gateway_statistics_truncated", "offline_list_truncated",
            "page_reread_mismatch", "insecure_tls", "custom_ca_in_use",
            "retry_after_clamped", "retry_after_ignored", "stderr_bound_exceeded",
            # SPEC-v1.1-browse.md §5.
            "device_detail_truncated", "device_detail_unavailable",
            "devices_truncated", "clients_truncated", "envelope_truncated",
            "gateway_list_truncated",
        ]))
        # DEV-5 retired this one. Asserted by name, because a code deleted from
        # the tuple and left in `MESSAGES` would pass the equality above while
        # `warn.MESSAGES` still documented a condition nothing can raise.
        self.assertNotIn("wans_unavailable", warn.CODES)
        self.assertNotIn("wans_unavailable", warn.MESSAGES)

    def test_every_code_has_a_message(self):
        for code in warn.CODES:
            with self.subTest(code=code):
                self.assertTrue(warn.MESSAGES.get(code))

    def test_an_unknown_code_is_refused(self):
        collector = warn.Warnings()
        with self.assertRaises(warn.UnknownWarningCode):
            collector.add("looks_plausible")

    def test_the_list_is_bounded_and_says_how_much_it_dropped(self):
        collector = warn.Warnings()
        for _ in range(warn.MAX_ENTRIES + 7):
            collector.add("unknown_device_state", {"state": "X", "deviceId": "d"})
        self.assertEqual(len(collector), warn.MAX_ENTRIES)
        self.assertEqual(collector.dropped(), 7)


class TimeBudget(unittest.TestCase):
    """REQ-017 / REQ-017c."""

    def setUp(self):
        self.now = [0.0]

    def budget(self, seconds=deadline.BUDGET_SEC):
        return deadline.Deadline(seconds, clock=lambda: self.now[0])

    def test_the_budget_is_the_spec_value(self):
        self.assertEqual(deadline.BUDGET_SEC, 25.0)

    def test_a_per_operation_timeout_never_exceeds_what_is_left(self):
        budget = self.budget()
        self.now[0] = 21.0
        self.assertEqual(budget.timeout_for("devices"), 4.0)

    def test_a_per_operation_timeout_is_capped_below_the_whole_budget(self):
        # Without the cap, one wedged request consumes all 25 s and the batch
        # fails having attempted exactly one route.
        self.assertEqual(self.budget().timeout_for("info"),
                         deadline.PER_OPERATION_MAX_SEC)
        self.assertLess(deadline.PER_OPERATION_MAX_SEC, deadline.BUDGET_SEC)

    def test_an_expired_budget_raises_timeout_not_network(self):
        budget = self.budget()
        self.now[0] = 25.5
        with self.assertRaises(errors.DeadlineError) as caught:
            budget.check("clients")
        self.assertEqual(caught.exception.kind, "timeout")

    def test_a_sliver_of_budget_fails_instead_of_starting_a_doomed_request(self):
        budget = self.budget()
        self.now[0] = deadline.BUDGET_SEC - deadline.MIN_OPERATION_SEC / 2
        with self.assertRaises(errors.DeadlineError):
            budget.timeout_for("device")

    def test_the_clock_is_monotonic_not_wall(self):
        # An NTP correction mid-batch must neither grant nor revoke time.
        source = deadline.Deadline()
        self.assertIs(source._clock, time.monotonic)


class RouteAllowlist(unittest.TestCase):
    """AC-017, AC-018, AC-061. BIZ-001, SEC-009, SEC-013."""

    # A LAN-shaped address on purpose: these tests assert the exact string a
    # local console produces, and nothing here opens a socket — `routes.build`
    # returns a Request and never connects.
    ROOT = "https://192.168.1.1/proxy/network/integration"
    SITE = "140d6676-08f6-5cbd-806a-bff7222ccc5d"
    DEVICE = "2f4dcb4c-0d20-5e6b-9a0e-0a03a6f8b111"

    def build(self, name, **kwargs):
        params = {}
        if "siteId" in routes.ROUTES[name]["params"]:
            params["siteId"] = self.SITE
        if "deviceId" in routes.ROUTES[name]["params"]:
            params["deviceId"] = self.DEVICE
        params.update(kwargs)
        return routes.build(self.ROOT, name, **params)

    def test_the_allowlist_holds_exactly_the_six_contract_routes(self):
        # Six before DEV-5 and six after: `wans` left and `device` arrived.
        # Written out rather than counted, because "there are six" is satisfied
        # by any six and BIZ-001's claim is about WHICH.
        self.assertEqual(routes.ROUTE_NAMES,
                         ("clients", "device", "device_statistics", "devices",
                          "info", "sites"))
        self.assertNotIn("wans", routes.ROUTES)

    def test_every_route_produces_the_local_console_url(self):
        # AC-018, longhand. The expected strings are written out rather than
        # rebuilt from the templates: a test that formats the same template the
        # code formats would pass on a wrong template.
        expected = {
            "info": self.ROOT + "/v1/info",
            "sites": self.ROOT + "/v1/sites",
            "devices": self.ROOT + "/v1/sites/" + self.SITE + "/devices",
            "clients": self.ROOT + "/v1/sites/" + self.SITE + "/clients",
            "device": (self.ROOT + "/v1/sites/" + self.SITE
                       + "/devices/" + self.DEVICE),
            "device_statistics": (self.ROOT + "/v1/sites/" + self.SITE
                                  + "/devices/" + self.DEVICE + "/statistics/latest"),
        }
        self.assertEqual(sorted(expected), sorted(routes.ROUTE_NAMES))
        for name, url in expected.items():
            with self.subTest(route=name):
                self.assertEqual(self.build(name).url, url)

    def test_a_trailing_slash_on_the_api_root_changes_nothing(self):
        for name in routes.ROUTE_NAMES:
            with self.subTest(route=name):
                params = {}
                if "siteId" in routes.ROUTES[name]["params"]:
                    params["siteId"] = self.SITE
                if "deviceId" in routes.ROUTES[name]["params"]:
                    params["deviceId"] = self.DEVICE
                with_slash = routes.build(self.ROOT + "/", name, **params)
                self.assertEqual(with_slash.url, self.build(name).url)

    def test_no_route_outside_the_allowlist_can_be_built(self):
        for name in ("hotspots", "vouchers", "v1/info", "", "INFO"):
            with self.subTest(route=name):
                with self.assertRaises(errors.InternalError):
                    routes.build(self.ROOT, name, siteId=self.SITE)

    def test_every_request_is_a_get(self):
        for name in routes.ROUTE_NAMES:
            with self.subTest(route=name):
                self.assertEqual(self.build(name).method, "GET")
        self.assertEqual(routes.METHOD, "GET")

    def test_a_non_get_method_is_refused_by_the_only_thing_that_opens_a_socket(self):
        # AC-017's method half. routes.build cannot produce a non-GET, so the
        # guarantee is asserted where a socket is actually opened: an edit that
        # started constructing requests elsewhere would still be caught.
        request = self.build("info")
        request.method = "POST"
        opened = []

        def recording_connect(host, port, timeout, context):
            opened.append(host)
            raise AssertionError("unreachable")

        with self.assertRaises(errors.InternalError):
            transport.get_json(request, _StubCredential(), None,
                               deadline.Deadline(), connect=recording_connect)
        # The assertion is that no socket was opened, not merely that something
        # was raised: get_json maps ANY unexpected exception to `internal`, so
        # a version with no guard at all would also raise InternalError here —
        # after connecting.
        self.assertEqual(opened, [], "a socket was opened for a non-GET request")

    def test_site_id_path_traversal_is_rejected_before_a_request_exists(self):
        # AC-061, the exact inputs the criterion names.
        for hostile in ("x/../../v1/hotspots", "../", "",
                        "not-a-uuid",
                        self.SITE + "/../../hotspots",
                        self.SITE + "%2f..%2f",
                        "140d6676-08f6-5cbd-806a-bff7222ccc5",
                        "140d6676_08f6_5cbd_806a_bff7222ccc5d",
                        self.SITE + "\n"):
            with self.subTest(siteId=hostile):
                with self.assertRaises(errors.HelperError) as caught:
                    routes.build(self.ROOT, "devices", siteId=hostile)
                self.assertNotIn(caught.exception.kind,
                                 ("network", "timeout", "http"))

    def test_a_rejected_site_id_is_never_quoted_back(self):
        with self.assertRaises(errors.HelperError) as caught:
            routes.build(self.ROOT, "devices", siteId="x/../../v1/hotspots")
        self.assertNotIn("hotspots", caught.exception.message)

    def test_path_segments_are_percent_encoded(self):
        # Barrier 2. It cannot fire while barrier 1 (the UUID pattern) stands —
        # a canonical UUID has nothing to encode — so it is tested directly
        # rather than through build(). A barrier whose test never runs is one
        # that gets deleted during a refactor.
        self.assertEqual(routes.encode_segment("a/b"), "a%2Fb")
        self.assertEqual(routes.encode_segment("../x"), "..%2Fx")
        self.assertEqual(routes.encode_segment("a?b#c"), "a%3Fb%23c")
        self.assertEqual(routes.encode_segment(self.SITE), self.SITE)

    def test_the_assembled_url_is_re_checked_against_the_allowlist(self):
        # Barrier 3, driven by bypassing barriers 1 and 2 the way a future edit
        # would: a template that no longer matches its own pattern.
        with _patched(routes, "ROUTES", dict(routes.ROUTES)):
            routes.ROUTES["devices"] = dict(routes.ROUTES["devices"],
                                            template="sites/{siteId}/hotspots")
            with self.assertRaises(errors.InternalError) as caught:
                routes.build(self.ROOT, "devices", siteId=self.SITE)
            self.assertIn("allowlisted route", caught.exception.message)

    def test_a_non_https_api_root_is_refused(self):
        for hostile in ("http://192.168.1.1/proxy", "ftp://h/x", "file:///etc/passwd",
                        "javascript:1", "//192.168.1.1/proxy", ""):
            with self.subTest(apiRoot=hostile):
                with self.assertRaises(errors.HelperError):
                    routes.build(hostile, "info")

    def test_an_api_root_with_embedded_credentials_is_refused(self):
        with self.assertRaises(errors.HelperError):
            routes.build("https://user:pass@192.168.1.1/proxy", "info")

    def test_an_api_root_carrying_traversal_or_a_query_is_refused(self):
        for hostile in ("https://h/proxy/../../admin", "https://h/proxy?x=1",
                        "https://h/proxy#frag", "https://h/pro xy"):
            with self.subTest(apiRoot=hostile):
                with self.assertRaises(errors.HelperError):
                    routes.build(hostile, "info")

    def test_pagination_parameters_only_reach_paginated_routes(self):
        self.assertIn("offset=0&limit=200", self.build("sites", offset=0, limit=200).url)
        for name in ("info", "device_statistics"):
            with self.subTest(route=name):
                with self.assertRaises(errors.InternalError):
                    self.build(name, offset=0, limit=200)

    def test_pagination_parameters_are_range_checked(self):
        for offset, limit in ((-1, 200), (0, 0), (0, 201), ("0", 200), (0, None)):
            if limit is None:
                continue
            with self.subTest(offset=offset, limit=limit):
                with self.assertRaises(errors.InternalError):
                    self.build("sites", offset=offset, limit=limit)

    def test_a_request_repr_cannot_leak_the_controller_address(self):
        self.assertNotIn("192.168.1.1", repr(self.build("info")))


class _StubCredential(object):
    """A credential-shaped double. Never a real key, and never a plain string.

    `transport` reads the header value through `header_value()` and nowhere
    else; passing a str here would let a future edit that used the object
    directly still pass this suite.
    """

    VALUE = "sk-test-not-a-real-key"

    def header_value(self):
        return self.VALUE


class _RaisingConnection(object):
    """A connection whose request() raises, for the AC-012a matrix."""

    def __init__(self, exception):
        self.exception = exception

    def request(self, method, target, headers=None):
        raise self.exception

    def getresponse(self):
        raise AssertionError("unreachable")

    def close(self):
        pass


def _connect_raising(exception):
    def factory(host, port, timeout, context):
        return _RaisingConnection(exception)
    return factory


class StubServed(TempTree):
    """A live HTTPS stub with a context that trusts exactly its CA."""

    def serve(self, handler=None):
        authority, certfile, keyfile = _shared_authority()
        stub = tls_stub.TlsStub(certfile, keyfile, handler=handler)
        stub.__enter__()
        self.addCleanup(stub.__exit__, None, None, None)
        context = tlsctx.build_context(ca_pem=authority.cert_pem)
        # 127.0.0.1 rather than localhost: the stub binds IPv4 only, and
        # "localhost" resolves to ::1 first on plenty of systems. The minted
        # certificate carries an IP SAN for exactly this.
        api_root = "https://127.0.0.1:%d/proxy/network/integration" % stub.port
        return stub, context, api_root, authority


class TransportRequests(StubServed):
    """BIZ-001, SEC-010: what one GET actually puts on the wire."""

    def get(self, handler, route="info", **kwargs):
        stub, context, api_root, _authority = self.serve(handler)
        request = routes.build(api_root, route)
        return stub, transport.get_json(request, _StubCredential(), context,
                                        deadline.Deadline(), **kwargs)

    def test_a_successful_get_returns_the_decoded_body_and_its_size(self):
        payload = b'{"applicationVersion":"9.1.0"}'
        stub, (body, size) = self.get(lambda req, i: tls_stub.response(200, body=payload))
        self.assertEqual(body, {"applicationVersion": "9.1.0"})
        self.assertEqual(size, len(payload))
        self.assertEqual(len(stub.received()), 1)

    def test_the_request_carries_the_credential_header_and_asks_for_no_compression(self):
        stub, _result = self.get(lambda req, i: tls_stub.response(200, body=b"{}"))
        sent = stub.received()[0]
        self.assertEqual(sent["method"], "GET")
        self.assertEqual(sent["headers"]["x-api-key"], _StubCredential.VALUE)
        self.assertEqual(sent["headers"]["accept"], "application/json")
        # Never compressed: the helper will not be the thing that expands a
        # decompression bomb, so it does not ask for one.
        self.assertEqual(sent["headers"]["accept-encoding"], "identity")
        self.assertEqual(sent["target"], "/proxy/network/integration/v1/info")

    def test_the_credential_is_read_through_one_named_accessor(self):
        headers = transport.request_headers(_StubCredential())
        self.assertEqual(headers["X-API-Key"], _StubCredential.VALUE)
        # Counted over the parsed tree, not the text: the module docstring
        # names the accessor when it explains the rule, and a substring count
        # would make documenting the rule break it.
        import ast
        tree = ast.parse(_read_source(
            os.path.join(_REPO, "helper", "unifi", "transport.py")))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "header_value"]
        self.assertEqual(len(calls), 1,
                         "the credential must have exactly one exit point")

    def test_a_compressed_response_is_refused_rather_than_expanded(self):
        with self.assertRaises(errors.MalformedResponseError):
            self.get(lambda req, i: tls_stub.response(
                200, body=b"{}", extra_headers={"Content-Encoding": "gzip"}))

    def test_a_login_page_answering_200_is_not_treated_as_data(self):
        with self.assertRaises(errors.MalformedResponseError):
            self.get(lambda req, i: tls_stub.response(
                200, body=b"<html>sign in</html>", content_type="text/html"))

    def test_valid_json_under_the_wrong_content_type_is_still_refused(self):
        # The case above is caught by the JSON parser whether or not the
        # content type is checked, so it cannot tell the two rules apart. This
        # body parses; only the content-type check rejects it.
        with self.assertRaises(errors.MalformedResponseError):
            self.get(lambda req, i: tls_stub.response(
                200, body=b'{"applicationVersion":"9.1.0"}',
                content_type="text/html"))

    def test_a_body_that_is_not_json_is_malformed_not_internal(self):
        with self.assertRaises(errors.MalformedResponseError):
            self.get(lambda req, i: tls_stub.response(200, body=b"{not json"))

    def test_a_json_array_is_refused_because_every_route_returns_an_object(self):
        with self.assertRaises(errors.MalformedResponseError):
            self.get(lambda req, i: tls_stub.response(200, body=b"[1,2,3]"))

    def test_a_body_over_the_bound_is_oversized(self):
        big = b'{"x":"' + b"a" * 4096 + b'"}'
        with self.assertRaises(errors.OversizedResponseError):
            self.get(lambda req, i: tls_stub.response(200, body=big), max_bytes=1024)

    HOLD_SEC = 5.0

    def test_a_declared_length_over_the_bound_fails_before_the_body_is_read(self):
        # The measured check catches this too, which is why the declared-length
        # check survived a first mutation pass. What it adds is that nothing is
        # read at all: the server sends a head promising 100 MB and then holds
        # the connection, so reading up to the bound would block for the whole
        # socket timeout before failing with the same kind.
        def handler(req, index):
            head = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    "Content-Length: 100000000\r\nConnection: close\r\n\r\n")
            return head.encode("latin-1"), self.HOLD_SEC

        stub, context, api_root, _authority = self.serve(handler)
        request = routes.build(api_root, "info")
        started = time.monotonic()
        with self.assertRaises(errors.OversizedResponseError):
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline(self.HOLD_SEC), max_bytes=1024)
        self.assertLess(time.monotonic() - started, self.HOLD_SEC / 3,
                        "the body was read before the declared length was checked")

    def test_a_response_with_no_declared_length_is_still_bounded(self):
        # Content-Length is a claim, and the pre-read check against it is only
        # half the rule. A response framed by connection close declares no
        # length at all, so nothing but the measurement stops it — this is the
        # case where reading "until the server is done" reads whatever it sends.
        big = b'{"x":"' + b"a" * 8192 + b'"}'

        def handler(req, index):
            head = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    "Connection: close\r\n\r\n")
            return head.encode("latin-1") + big

        with self.assertRaises(errors.OversizedResponseError):
            self.get(handler, max_bytes=1024)

    def test_a_response_exactly_at_the_bound_is_accepted(self):
        filler = b"a" * (1024 - len(b'{"x":""}'))
        payload = b'{"x":"' + filler + b'"}'
        self.assertEqual(len(payload), 1024)
        stub, (body, size) = self.get(lambda req, i: tls_stub.response(200, body=payload),
                                      max_bytes=1024)
        self.assertEqual(size, 1024)


class RedirectRefusal(StubServed):
    """AC-014 / SEC-008. Nothing is followed and nothing is copied."""

    STATUSES = (301, 302, 303, 307, 308)

    def attempt(self, status, location, body=b"", content_length=None):
        def handler(req, index):
            if content_length is not None:
                head = ("HTTP/1.1 %d Moved\r\nLocation: %s\r\n"
                        "Content-Length: %d\r\nConnection: close\r\n\r\n"
                        % (status, location, content_length))
                return head.encode("latin-1") + body
            return tls_stub.response(status, "Moved", body=body,
                                     extra_headers={"Location": location})

        stub, context, api_root, _authority = self.serve(handler)
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.RedirectError) as caught:
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline())
        return stub, caught.exception

    def test_every_redirect_status_is_refused(self):
        for status in self.STATUSES:
            with self.subTest(status=status):
                stub, failure = self.attempt(status, "https://192.0.2.9/v1/info")
                self.assertEqual(failure.kind, "redirect")
                self.assertTrue(errors.retryable_for("redirect"))

    def test_a_redirect_produces_exactly_one_request_and_one_credential_send(self):
        for status in self.STATUSES:
            with self.subTest(status=status):
                stub, _failure = self.attempt(status, "https://192.0.2.9/v1/info")
                sent = stub.received()
                self.assertEqual(len(sent), 1, "a second request was constructed")
                self.assertEqual(sent[0]["target"],
                                 "/proxy/network/integration/v1/info")

    def test_a_same_origin_redirect_is_refused_too(self):
        stub, failure = self.attempt(302, "/proxy/network/integration/v1/sites")
        self.assertEqual(failure.kind, "redirect")
        self.assertEqual(len(stub.received()), 1)

    def test_a_downgrade_to_plain_http_is_refused(self):
        stub, failure = self.attempt(302, "http://192.0.2.9/v1/info")
        self.assertEqual(failure.kind, "redirect")
        self.assertEqual(len(stub.received()), 1)

    def test_a_redirect_loop_cannot_loop(self):
        # Location points back at the request's own target. An implementation
        # that followed redirects at all would spin here rather than fail once.
        stub, context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(
                302, "Found", extra_headers={"Location": req["target"]}))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.RedirectError):
            transport.get_json(request, _StubCredential(), context, deadline.Deadline())
        self.assertEqual(len(stub.received()), 1)

    HOLD_SEC = 5.0

    def test_an_oversized_redirect_body_is_never_read(self):
        # The body is announced as 100 MB, 16 bytes are sent, and the server
        # then HOLDS the connection open. That last part is the whole test: a
        # server that hangs up makes an unbounded read return at once, so an
        # implementation that drained the body would look identical to one that
        # discarded it. Holding it open means draining blocks for the whole
        # socket timeout, and the elapsed time separates them.
        def handler(req, index):
            head = ("HTTP/1.1 302 Found\r\nLocation: https://192.0.2.9/v1/info\r\n"
                    "Content-Length: 100000000\r\nConnection: close\r\n\r\n")
            return head.encode("latin-1") + b"x" * 16, self.HOLD_SEC

        stub, context, api_root, _authority = self.serve(handler)
        request = routes.build(api_root, "info")
        started = time.monotonic()
        with self.assertRaises(errors.RedirectError):
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline(self.HOLD_SEC))
        self.assertLess(time.monotonic() - started, self.HOLD_SEC / 3,
                        "the redirect body was read rather than discarded")

    def test_the_location_header_is_never_consulted(self):
        source = _read_source(os.path.join(_REPO, "helper", "unifi", "transport.py"))
        self.assertNotIn('"Location"', source)
        self.assertNotIn("'Location'", source)


class TransportErrorTaxonomy(StubServed):
    """DATA-007's exception precedence, and AC-012a."""

    def attempt(self, exception):
        request = routes.build("https://192.0.2.9/proxy/network/integration", "info")
        with self.assertRaises(errors.HelperError) as caught:
            transport.get_json(request, _StubCredential(), None, deadline.Deadline(),
                               connect=_connect_raising(exception))
        return caught.exception

    def test_the_ac_012a_matrix_is_network(self):
        cases = [
            socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
            ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
            ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"),
            OSError(errno.ENETUNREACH, "Network is unreachable"),
            OSError(errno.EHOSTUNREACH, "No route to host"),
        ]
        for exception in cases:
            with self.subTest(exception=type(exception).__name__,
                              errno=getattr(exception, "errno", None)):
                failure = self.attempt(exception)
                self.assertEqual(failure.kind, "network")
                self.assertTrue(errors.retryable_for(failure.kind))
                built = errors.to_error_object(failure)
                self.assertNotIn("httpStatus", built)
                self.assertNotIn("x-api-key", built["message"].lower())
                self.assertNotIn(_StubCredential.VALUE, built["message"])
                self.assertNotIn("192.0.2.9", built["message"])

    def test_a_tls_failure_outranks_the_oserror_it_is_a_subclass_of(self):
        # ssl.SSLError IS an OSError. Classifying in the wrong order turns "your
        # controller is being intercepted" into "the network is flaky".
        failure = self.attempt(ssl.SSLCertVerificationError("bad cert"))
        self.assertEqual(failure.kind, "tls")
        self.assertTrue(issubclass(ssl.SSLError, OSError))

    def test_a_socket_timeout_is_timeout_not_network(self):
        self.assertEqual(self.attempt(socket.timeout("timed out")).kind, "timeout")

    def test_a_kernel_etimedout_reports_the_same_kind_on_every_interpreter(self):
        # This started as "ETIMEDOUT is network, not timeout". It cannot be:
        # PEP 3151 makes OSError(ETIMEDOUT) construct a TimeoutError, and since
        # 3.10 socket.timeout IS TimeoutError — so on 3.14 the kernel timeout
        # and this helper's own socket timeout are one class, while on 3.9 they
        # are two. A taxonomy that split them would report a different kind for
        # the same failure depending on the user's Python. Both are `timeout`.
        self.assertEqual(self.attempt(OSError(errno.ETIMEDOUT, "timed out")).kind,
                         "timeout")
        self.assertEqual(self.attempt(socket.timeout("timed out")).kind, "timeout")
        self.assertNotIn(errno.ETIMEDOUT, transport._NETWORK_ERRNOS)

    def test_a_framing_error_is_network_because_no_response_arrived(self):
        self.assertEqual(self.attempt(http.client.BadStatusLine("garbage")).kind,
                         "network")

    def test_an_unrelated_oserror_is_internal_not_network(self):
        self.assertEqual(self.attempt(OSError(errno.EACCES, "permission denied")).kind,
                         "internal")

    def test_an_untrusted_certificate_really_does_fail_the_handshake(self):
        # The synthetic mapping above proves the precedence; this proves the
        # condition happens at all. Both are needed: a mapping that is never
        # reached is not a control.
        stub, _context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(200, body=b"{}"))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.TlsError):
            transport.get_json(request, _StubCredential(),
                               tlsctx.build_context(), deadline.Deadline())
        self.assertEqual(stub.received(), [])

    def test_http_statuses_map_to_their_typed_kinds(self):
        expected = {401: "unauthorized", 403: "forbidden", 429: "rate_limited",
                    400: "http", 404: "http", 500: "http", 502: "http", 503: "http"}
        for status, kind in expected.items():
            with self.subTest(status=status):
                stub, context, api_root, _authority = self.serve(
                    lambda req, i, s=status: tls_stub.response(s, "Status", body=b"{}"))
                request = routes.build(api_root, "info")
                with self.assertRaises(errors.HelperError) as caught:
                    transport.get_json(request, _StubCredential(), context,
                                       deadline.Deadline())
                self.assertEqual(caught.exception.kind, kind)
                built = errors.to_error_object(caught.exception)
                if kind == "http":
                    self.assertEqual(built["httpStatus"], status)
                self.assertEqual(built["retryable"],
                                 errors.retryable_for(kind, status))

    def test_a_204_is_not_silently_accepted_as_success(self):
        stub, context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(204, "No Content", body=b""))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.HttpError) as caught:
            transport.get_json(request, _StubCredential(), context, deadline.Deadline())
        self.assertEqual(caught.exception.http_status, 204)

    def test_a_429_carries_a_normalized_retry_after(self):
        collector = warn.Warnings()
        stub, context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(429, "Too Many", body=b"{}",
                                             extra_headers={"Retry-After": "45"}))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.RateLimitedError) as caught:
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline(), warnings=collector)
        self.assertEqual(caught.exception.retry_after_sec, 45)
        self.assertEqual(collector.codes(), [])

    def test_an_implausible_retry_after_is_clamped_with_a_warning(self):
        collector = warn.Warnings()
        stub, context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(429, "Too Many", body=b"{}",
                                             extra_headers={"Retry-After": "604800"}))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.RateLimitedError) as caught:
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline(), warnings=collector)
        self.assertEqual(caught.exception.retry_after_sec, transport.RETRY_AFTER_MAX_SEC)
        self.assertEqual(collector.codes(), ["retry_after_clamped"])

    def test_an_unreadable_retry_after_is_ignored_with_a_warning(self):
        collector = warn.Warnings()
        stub, context, api_root, _authority = self.serve(
            lambda req, i: tls_stub.response(429, "Too Many", body=b"{}",
                                             extra_headers={"Retry-After": "soon"}))
        request = routes.build(api_root, "info")
        with self.assertRaises(errors.RateLimitedError) as caught:
            transport.get_json(request, _StubCredential(), context,
                               deadline.Deadline(), warnings=collector)
        self.assertIsNone(caught.exception.retry_after_sec)
        self.assertEqual(collector.codes(), ["retry_after_ignored"])

    def test_retry_after_accepts_both_documented_forms(self):
        # R3: the API documents no Retry-After at all, so both branches are
        # written against something that may never arrive.
        self.assertEqual(transport.parse_retry_after("30"), 30)
        self.assertEqual(
            transport.parse_retry_after("Thu, 01 Jan 2099 00:00:00 GMT",
                                        now=4070908800.0 - 120), 120)
        self.assertIsNone(transport.parse_retry_after("Thu, 01 Jan 1999 00:00:00 GMT"))
        self.assertIsNone(transport.parse_retry_after("Thu, 01 Jan 2099 00:00:00"))
        self.assertIsNone(transport.parse_retry_after(""))
        self.assertIsNone(transport.parse_retry_after("-30"))
        self.assertIsNone(transport.parse_retry_after(None))


class Pagination(unittest.TestCase):
    """DATA-009 / 009a / 009b, driven by the Phase 1 corpus.

    Every case is fed page by page from its fixture. When the response list is
    exhausted it wraps around, which is what lets one committed list serve both
    the collection read and the DATA-009a re-read of page 0 — and, for the drift
    case, the retry that follows a first mismatch.
    """

    # The thirteen invariants, longhand, each with the fixture that must be
    # rejected for exactly it. Written out rather than derived from
    # pagination.INVARIANTS or from the corpus, because both are the things
    # under test: a loop over either would agree with a table that had lost an
    # entry, and AC-072 counts a rule as covered only when a test names it.
    REJECTED = {
        "offset_matches_request": "offset_matches_request",
        "count_equals_data_length": "count_equals_data_length",
        "count_within_limit": "count_within_limit",
        "non_terminal_page_progresses": "non_terminal_page_progresses",
        "record_ids_unique": "record_ids_unique",
        "total_count_non_negative": "total_count_non_negative",
        "total_count_stable": "total_count_stable",
        "advance_by_validated_count": "advance_by_validated_count",
        "max_pages_enforced": "max_pages_enforced",
        "max_decoded_bytes_enforced": "max_decoded_bytes_enforced",
        "empty_is_valid_not_premature": "empty_is_valid_not_premature",
        "reread_page_zero_matches": "reread_page_zero_matches",
        "terminal_completeness": "terminal_completeness",
    }

    ACCEPTED = ("accept_single_page_25", "accept_boundary_200",
                "accept_boundary_201", "accept_three_pages_413",
                "accept_empty_collection")

    def setUp(self):
        self.cases = fixture_pages.load_all(
            os.path.join(_REPO, "tests", "fixtures", "api", "pagination"))

    def feed(self, pages, record=None):
        state = {"index": 0}

        def fetch_page(offset, limit):
            page = pages[state["index"] % len(pages)]
            state["index"] += 1
            if record is not None:
                record.append((offset, limit))
            return page, len(json.dumps(page))

        return fetch_page

    def collect_case(self, case_id, **kwargs):
        case = self.cases[case_id]
        pages = fixture_pages.responses_for(case)
        return pagination.collect(self.feed(pages), limit=case["limit"], **kwargs)

    def test_the_invariant_list_is_the_documented_thirteen(self):
        self.assertEqual(sorted(pagination.INVARIANTS), sorted(self.REJECTED))
        self.assertEqual(len(pagination.INVARIANTS), 13)

    def test_every_invariant_rejects_its_own_fixture_and_names_itself(self):
        for case_id, invariant in sorted(self.REJECTED.items()):
            with self.subTest(invariant=invariant):
                result = self.collect_case(case_id)
                self.assertFalse(result.complete)
                self.assertEqual(result.invariant, invariant)

    def test_every_accept_fixture_collects_completely(self):
        for case_id in self.ACCEPTED:
            with self.subTest(case=case_id):
                result = self.collect_case(case_id)
                self.assertTrue(result.complete, result.invariant)
                self.assertEqual(len(result.records), result.total_count)

    def test_the_413_record_fixture_yields_all_413(self):
        # AC-007's first half at this layer. The count itself is Phase 7's
        # business; what is asserted here is that pagination handed it 413
        # unique records over three pages.
        result = self.collect_case("accept_three_pages_413")
        self.assertEqual(len(result.records), 413)
        self.assertEqual(result.total_count, 413)
        self.assertEqual(result.pages_read, 3)
        self.assertEqual(len(set(r["id"] for r in result.records)), 413)

    def test_a_full_terminal_page_still_terminates(self):
        # accept_boundary_200: the terminal page is FULL, so a reader that stops
        # only on a short page never stops at all.
        result = self.collect_case("accept_boundary_200")
        self.assertTrue(result.complete)
        self.assertEqual(result.pages_read, 1)

    def test_an_empty_collection_is_complete_and_a_premature_empty_is_not(self):
        # AC-043. The pair is the whole of DATA-009b; either case alone can be
        # satisfied by a wrong rule.
        empty = self.collect_case("accept_empty_collection")
        self.assertTrue(empty.complete)
        self.assertEqual(empty.records, [])
        self.assertEqual(empty.total_count, 0)
        premature = self.collect_case("empty_is_valid_not_premature")
        self.assertFalse(premature.complete)
        self.assertEqual(premature.invariant, "empty_is_valid_not_premature")

    def test_a_short_page_with_a_missing_record_is_caught_by_completeness(self):
        result = self.collect_case("terminal_completeness")
        self.assertEqual(result.invariant, "terminal_completeness")

    def test_offset_drift_is_retried_once_then_reported(self):
        # AC-042. The first mismatch is ordinary on a busy controller and warns;
        # the second is the collection genuinely unreadable.
        collector = warn.Warnings()
        result = self.collect_case("reread_page_zero_matches", warnings=collector)
        self.assertFalse(result.complete)
        self.assertEqual(result.invariant, "reread_page_zero_matches")
        self.assertEqual(collector.codes(), ["page_reread_mismatch"])
        self.assertTrue(result.reread_retried)

    def test_the_reread_is_what_catches_drift_and_nothing_else_does(self):
        # Every other invariant passes on this fixture. Removing the re-read
        # must therefore make it collect "successfully" with a record missing —
        # this asserts the fixture really is the trap it claims to be.
        case = self.cases["reread_page_zero_matches"]
        pages = fixture_pages.responses_for(case)
        with _patched(pagination, "_reread_matches",
                      lambda *args, **kwargs: True):
            result = pagination.collect(self.feed(pages), limit=case["limit"])
        self.assertTrue(result.complete)
        self.assertEqual(len(result.records), 5)
        self.assertNotIn("2f4dcb4c-0d20-5e6b-9a0e-0a03a6f8b111",
                         [r["id"] for r in result.records])

    def test_the_reader_advances_by_the_validated_count(self):
        # The positive half of `advance_by_validated_count`: the offsets
        # actually requested come from validated counts, and the last request is
        # the DATA-009a re-read of page zero.
        case = self.cases["accept_three_pages_413"]
        pages = fixture_pages.responses_for(case)
        asked = []
        pagination.collect(self.feed(pages, record=asked), limit=case["limit"])
        self.assertEqual(asked, [(0, 200), (200, 200), (400, 200), (0, 200)])

    def test_a_page_echoing_a_different_limit_is_refused(self):
        # The negative half. A reader that trusted the echoed limit for its
        # arithmetic would skip records, so the page is refused before any
        # arithmetic happens.
        result = self.collect_case("advance_by_validated_count")
        self.assertEqual(result.invariant, "advance_by_validated_count")

    def test_the_page_bound_stops_a_collection_that_never_terminates(self):
        result = self.collect_case("max_pages_enforced")
        self.assertEqual(result.invariant, "max_pages_enforced")
        self.assertEqual(pagination.MAX_PAGES, 64)

    def test_the_byte_bound_is_reached_before_the_page_bound(self):
        # The fixture is padded precisely so this ordering holds. Unpadded, 64
        # pages of device records total under 4 MiB and this case would stop at
        # the page bound while claiming to test the byte bound.
        case = self.cases["max_decoded_bytes_enforced"]
        pages = fixture_pages.responses_for(case)
        self.assertLessEqual(len(pages), pagination.MAX_PAGES)
        self.assertGreater(sum(len(json.dumps(p)) for p in pages),
                           pagination.MAX_DECODED_BYTES)
        result = self.collect_case("max_decoded_bytes_enforced")
        self.assertEqual(result.invariant, "max_decoded_bytes_enforced")

    def test_the_batch_budget_bounds_the_sum_of_collections(self):
        # Per-collection bounds alone leave six collections able to read 48 MiB.
        budget = pagination.ByteBudget(limit=4096)
        case = self.cases["accept_three_pages_413"]
        pages = fixture_pages.responses_for(case)
        result = pagination.collect(self.feed(pages), limit=case["limit"],
                                    budget=budget)
        self.assertFalse(result.complete)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.kind, "oversized_response")

    def test_a_transport_failure_is_captured_not_raised(self):
        def fetch_page(offset, limit):
            raise errors.NetworkError("The controller could not be reached.")

        result = pagination.collect(fetch_page)
        self.assertFalse(result.complete)
        self.assertIsNone(result.invariant)
        self.assertEqual(result.error.kind, "network")

    def test_pagination_does_not_know_whether_a_collection_is_required(self):
        # A CP4b exit criterion, asserted structurally rather than by reading
        # the code: there is no parameter and no result field through which
        # BIZ-004's policy could be expressed here.
        import inspect
        signature = inspect.signature(pagination.collect)
        for forbidden in ("required", "optional", "policy", "kind"):
            self.assertNotIn(forbidden, signature.parameters)
        for field in pagination.CollectResult.__slots__:
            self.assertNotIn("required", field)
            self.assertNotIn("optional", field)

    def test_one_result_drives_both_biz_004_policies(self):
        # The other half of the same criterion: the SAME failure, consumed by a
        # required caller and an optional one, produces the two BIZ-004
        # outcomes. If pagination decided, one of these could not be written.
        result = self.collect_case("count_equals_data_length")

        def required(collect_result):
            if collect_result.error is not None:
                raise collect_result.error
            if not collect_result.complete:
                raise errors.PartialResponseError("A required collection was incomplete.")
            return collect_result.records

        def optional(collect_result, collector):
            if collect_result.complete:
                return collect_result.records
            collector.add("clients_unavailable")
            return None

        with self.assertRaises(errors.PartialResponseError) as caught:
            required(result)
        self.assertEqual(caught.exception.kind, "partial_response")

        collector = warn.Warnings()
        self.assertIsNone(optional(result, collector))
        self.assertEqual(collector.codes(), ["clients_unavailable"])

    def test_a_200_scale_short_page_is_partial_for_a_required_collection(self):
        # AC-007's second half, at the scale the criterion states: page 2
        # reports count = 200 with data.length = 199.
        case = self.cases["accept_three_pages_413"]
        pages = json.loads(json.dumps(fixture_pages.responses_for(case)))
        pages[1]["data"] = pages[1]["data"][:199]
        result = pagination.collect(self.feed(pages), limit=case["limit"])
        self.assertFalse(result.complete)
        self.assertEqual(result.invariant, "count_equals_data_length")
        self.assertEqual(result.records, [])
        self.assertIsNone(result.total_count)

    def test_a_record_without_a_stable_id_cannot_be_counted(self):
        page = {"offset": 0, "limit": 200, "count": 1, "totalCount": 1,
                "data": [{"name": "no id"}]}
        result = pagination.collect(self.feed([page]))
        self.assertEqual(result.invariant, "record_ids_unique")

    def test_a_malformed_page_is_a_protocol_failure_not_an_invariant(self):
        for page in ({"offset": 0, "limit": 200, "count": 0},
                     {"offset": 0, "limit": 200, "count": "0", "totalCount": 0,
                      "data": []},
                     {"offset": 0, "limit": 200, "count": 0, "totalCount": 0,
                      "data": {}},
                     "not a page"):
            with self.subTest(page=page):
                result = pagination.collect(self.feed([page]))
                self.assertIsNone(result.invariant)
                self.assertEqual(result.error.kind, "malformed_response")

    def test_an_absurd_total_count_is_oversized_not_a_page_bound(self):
        page = {"offset": 0, "limit": 200, "count": 0, "totalCount": 10 ** 9,
                "data": []}
        result = pagination.collect(self.feed([page]))
        self.assertEqual(result.error.kind, "oversized_response")

    def test_the_bounds_match_the_protocol_document(self):
        self.assertEqual(pagination.PAGE_LIMIT, 200)
        self.assertEqual(pagination.MAX_PAGES, 64)
        self.assertEqual(pagination.MAX_DECODED_BYTES, 8 * 1024 * 1024)
        self.assertEqual(pagination.MAX_BATCH_DECODED_BYTES, 16 * 1024 * 1024)
        self.assertEqual(pagination.TOTAL_COUNT_MAX, 1000000)


class VersionGate(unittest.TestCase):
    """R2. The mechanism is complete; the matrix's contents are not, on purpose."""

    def test_every_matrix_entry_is_documented_where_it_came_from(self):
        # G-CONTROLLER blocked the VALUE, not the mechanism, and Phase 12a
        # supplied the observed entry: applicationVersion "10.6.101" from a real
        # controller, against which a full batch succeeded on 2026-09-06.
        #
        # The guard that used to freeze this tuple is replaced rather than
        # deleted, because what it was really protecting against is a version
        # being added because someone hoped it worked. Each entry must be
        # accounted for in the module's own comment, so adding one without
        # saying where it came from fails the build.
        self.assertEqual(version_gate.TESTED_VERSIONS, ((9, 1), (10, 6)))
        source = inspect.getsource(version_gate)
        for major, minor in version_gate.TESTED_VERSIONS:
            marker = "#   (%d, %d)" % (major, minor)
            self.assertIn(marker, source,
                          "version_gate.py must record where (%d, %d) came from"
                          % (major, minor))
        self.assertIn("SYNTHETIC", source)
        self.assertIn("OBSERVED", source)

    def test_the_observed_entry_is_the_version_phase_12a_recorded(self):
        # The exact string the controller reported, not a rounded one.
        self.assertTrue(version_gate.is_supported("10.6.101"))
        self.assertEqual(version_gate.parse("10.6.101"), (10, 6))
        # And the gate is still a gate: a neighbouring minor is not implied.
        self.assertFalse(version_gate.is_supported("10.5.0"))
        self.assertFalse(version_gate.is_supported("10.7.0"))

    def test_the_synthetic_entry_matches_the_fixture_corpus(self):
        with open(os.path.join(_REPO, "tests", "fixtures", "api", "scenarios",
                               "healthy", "info.json"), encoding="utf-8") as handle:
            reported = json.load(handle)["applicationVersion"]
        self.assertTrue(version_gate.is_supported(reported))

    def test_a_tested_version_passes_the_gate(self):
        self.assertEqual(version_gate.check({"applicationVersion": "9.1.0"}), "9.1.0")
        self.assertEqual(version_gate.check({"applicationVersion": "9.1"}), "9.1")
        self.assertEqual(version_gate.check({"applicationVersion": "9.1.12-beta"}),
                         "9.1.12-beta")

    def test_an_untested_version_is_unsupported_not_a_transport_failure(self):
        # The whole point of R2: a shape change must not surface as an
        # authentication or connectivity problem the user then chases.
        for reported in ("10.4.57", "8.9.0", "9.2.0", "1.0.0"):
            with self.subTest(version=reported):
                with self.assertRaises(errors.UnsupportedError) as caught:
                    version_gate.check({"applicationVersion": reported})
                self.assertEqual(caught.exception.kind, "unsupported")
                self.assertFalse(errors.retryable_for("unsupported"))

    def test_a_missing_or_unreadable_version_is_unsupported(self):
        for info in ({}, {"applicationVersion": None}, {"applicationVersion": ""},
                     {"applicationVersion": "banana"},
                     {"applicationVersion": "9"},
                     {"applicationVersion": "9." + "9" * 80}):
            with self.subTest(info=info):
                with self.assertRaises(errors.UnsupportedError):
                    version_gate.check(info)

    def test_the_unsupported_path_is_exercised_against_a_synthetic_entry(self):
        # CP4b's criterion, stated as a test rather than as a claim: an injected
        # matrix proves the gate follows the constant, so P12a changing the
        # constant changes the behaviour.
        self.assertEqual(
            version_gate.check({"applicationVersion": "10.4.57"},
                               tested=((10, 4),)), "10.4.57")
        with self.assertRaises(errors.UnsupportedError):
            version_gate.check({"applicationVersion": "9.1.0"}, tested=((10, 4),))

    def test_a_non_object_info_body_is_malformed(self):
        with self.assertRaises(errors.MalformedResponseError):
            version_gate.check(["9.1.0"])


class HelperIdentity(unittest.TestCase):

    def test_the_helper_version_matches_the_manifest(self):
        # meta.helperVersion is what a bug report will quote. If it disagreed
        # with the version the user sees in the plugin list, the report would be
        # unanswerable.
        with open(os.path.join(_REPO, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(unifi.HELPER_VERSION, manifest["version"])

    def test_the_user_agent_carries_no_credential_and_no_host(self):
        self.assertIn(unifi.HELPER_VERSION, transport.USER_AGENT)
        self.assertNotIn("key", transport.USER_AGENT.lower())


# --- Phase 7: normalize, collect, envelope, entry point ---------------------

class NormalizedModel(unittest.TestCase):
    """DATA-006, and the producer half of risk R-F.

    The accept corpus is one artifact used in two directions: the model tests
    feed it to `Health.js` as input, and these assert it as `normalize.py`'s
    output. The inputs come from `tests/tools/normalize_inputs.py`, which is
    authored from device states and `features` arrays — so every count here is
    DERIVED and compared against a literal, not restated.
    """

    def normalized(self, case):
        inputs = normalize_inputs.cases()[case]
        collector = warn.Warnings()
        data = normalize.build(inputs["site"], inputs["devices"],
                               inputs["clients"], inputs["statistics"],
                               inputs["applicationVersion"], collector,
                               details=inputs.get("details"),
                               listed_devices=inputs.get("listedDevices"),
                               client_records=inputs.get("clientRecords"))
        return data, collector

    def expected(self, case):
        path = os.path.join(_REPO, "tests", "fixtures", "envelopes", "accept",
                            "%s.json" % case)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_every_accept_envelope_is_reproduced_byte_for_byte(self):
        cases = normalize_inputs.cases()
        self.assertEqual(len(cases), 19)
        for case in sorted(cases):
            with self.subTest(case=case):
                data, _warnings = self.normalized(case)
                self.assertEqual(
                    json.dumps(data, sort_keys=True),
                    json.dumps(self.expected(case)["data"], sort_keys=True))

    def test_the_input_corpus_covers_every_success_envelope(self):
        # A gap here is invisible otherwise: the loop above would still pass
        # while silently skipping whichever envelope had no input authored.
        directory = os.path.join(_REPO, "tests", "fixtures", "envelopes", "accept")
        envelopes = sorted(name[:-5] for name in os.listdir(directory)
                           if name.startswith("success_"))
        self.assertEqual(sorted(normalize_inputs.cases()), envelopes)

    # --- DEV-6: the widened gateway rule ----------------------------------

    def test_an_off_lan_address_is_the_gateway_signal(self):
        # The console reports its WAN address; every other device reports an
        # RFC 1918 one. That difference is the whole signal.
        offlan = ("203.0.113.9", "192.0.2.1", "2001:db8::1",
                  # RFC 6598 carrier-grade NAT. A console behind CGNAT reports a
                  # real WAN address, and `ipaddress.is_global` calls it private
                  # — which is why that function is not what this uses.
                  "100.64.0.1")
        for address in offlan:
            self.assertTrue(
                normalize.reports_an_offlan_address({"ipAddress": address}), address)

        on_lan = ("10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.1",
                  "fc00::1", "fd12:3456::1")
        for address in on_lan:
            self.assertFalse(
                normalize.reports_an_offlan_address({"ipAddress": address}), address)

    def test_nothing_that_is_not_an_address_is_evidence_of_a_gateway(self):
        # The heuristic must fail CLOSED. Every one of these would otherwise
        # promote an arbitrary device to gateway, which would put its metrics in
        # `wan` and could make a healthy site red.
        for value in ("", "   ", "nonsense", "999.1.1.1", "192.168.1", None,
                      123, [], {}, "127.0.0.1", "169.254.1.1", "0.0.0.0",
                      "224.0.0.1", "::1", "fe80::1"):
            self.assertFalse(
                normalize.reports_an_offlan_address({"ipAddress": value}), repr(value))
        self.assertFalse(normalize.reports_an_offlan_address({}))

    def test_the_inferred_gateway_holds_both_roles(self):
        # REQ-009: a device is counted in every role it reports. The console
        # advertises `switching` and is INFERRED to be a gateway, so it must
        # appear in both rows — and `is_gateway` and the counter must agree,
        # which is why they share `roles_of`.
        console = {"id": "c", "features": ["switching"], "ipAddress": "203.0.113.9"}
        self.assertEqual(normalize.roles_of(console), {"switching", "gateway"})
        self.assertTrue(normalize.is_gateway(console))

        lan_switch = {"id": "s", "features": ["switching"], "ipAddress": "192.168.1.5"}
        self.assertEqual(normalize.roles_of(lan_switch), {"switching"})
        self.assertFalse(normalize.is_gateway(lan_switch))

        # A device with no features at all is still a gateway if it is off-LAN,
        # and still nothing if it is not. DATA-006b counts it in byClass either
        # way, which is what makes the sum invariant hold.
        self.assertEqual(normalize.roles_of({"ipAddress": "203.0.113.9"}), {"gateway"})
        self.assertEqual(normalize.roles_of({"ipAddress": "192.168.1.9"}), set())

    def test_the_declared_feature_still_wins_on_its_own(self):
        # Widening must not have replaced the documented rule. A device that
        # advertises `gateway` is one whatever its address is — which is every
        # fixture in the corpus authored before DEV-6.
        declared = {"id": "g", "features": ["gateway", "switching"],
                    "ipAddress": "192.168.1.1"}
        self.assertTrue(normalize.is_gateway(declared))
        self.assertEqual(normalize.roles_of(declared), {"gateway", "switching"})

    def test_dev_6_end_to_end_through_normalize(self):
        data, _warnings = self.normalized("success_console_without_gateway_feature")
        # Before DEV-6 this site had no gateway: unknown status, no metrics.
        self.assertEqual(data["wan"]["status"], "up")
        self.assertEqual(data["wan"]["uptimeSec"], 1103341)
        self.assertEqual(data["wan"]["downloadBps"], 29584)
        self.assertEqual(len(data["gateways"]), 1)
        self.assertEqual(data["counts"]["gateways"]["online"], 1)
        # And the console is still a switch, so the rows over-count: 1 + 2 + 2
        # against four unique devices.
        roles = sum(sum(data["counts"][role].values())
                    for role in ("gateways", "switches", "accessPoints"))
        self.assertEqual(roles, 5)
        self.assertEqual(data["counts"]["devicesTotal"], 4)

    def test_ac_025_role_counts_are_not_a_partition(self):
        # AC-025 against normalize.py: multi-role counting derived from a
        # `features` array on the wire, not from a hand-built Health.js input.
        data, _warnings = self.normalized("success_multi_feature_roles")
        counts = data["counts"]
        self.assertEqual(counts["devicesTotal"], 3)
        self.assertEqual(counts["gateways"]["online"], 1)
        self.assertEqual(counts["switches"]["online"], 1)
        self.assertEqual(counts["accessPoints"]["online"], 3)
        # Five role placements over three unique devices. The rows are not
        # expected to sum to the total, and that contrast is the point.
        self.assertEqual(sum(counts[role]["online"] for role in
                             ("gateways", "switches", "accessPoints")), 5)
        self.assertEqual(sum(counts["byClass"].values()), 3)

    def test_a_featureless_device_is_still_counted_once(self):
        data, _warnings = self.normalized("success_featureless_device")
        counts = data["counts"]
        self.assertEqual(counts["devicesTotal"], 3)
        self.assertEqual(sum(counts["byClass"].values()), 3)
        # It appears in no role object at all, so byClass is the only place it
        # is visible — and a partition that dropped it would satisfy every
        # role-based check while under-reporting the site.
        self.assertEqual(sum(counts[role]["online"] for role in
                             ("gateways", "switches", "accessPoints")), 2)

    def test_ac_063_the_offline_list_is_bounded_and_the_total_is_not(self):
        # AC-063 against normalize.py: the ten-row bound with an independent
        # offlineTotal, so "and 490 more" is right on the wire.
        data, collector = self.normalized("success_500_down_bounded_list")
        self.assertEqual(len(data["offlineDevices"]), normalize.OFFLINE_LIST_MAX)
        self.assertEqual(data["counts"]["offlineTotal"], 500)
        self.assertIn("offline_list_truncated", collector.codes())
        detail = collector.to_list()[-1]["detail"]
        self.assertEqual(detail, {"listed": 10, "total": 500})

    def test_the_offline_list_is_ordered_before_it_is_bounded(self):
        # The corpus happens to author devices in ascending id order, so the
        # sort was untested until a mutation removed it and nothing failed.
        # These arrive scrambled, which is what a controller is free to do.
        scrambled = [normalize_inputs.device(60 - i, "OFFLINE", ["switching"],
                                             "Switch %02d" % (60 - i))
                     for i in range(20)]
        data = normalize.build({"id": "s", "name": "S"}, scrambled, 0, {},
                               "9.1.0", warn.Warnings())
        listed = [entry["id"] for entry in data["offlineDevices"]]
        self.assertEqual(listed, sorted(listed))
        # The lowest ten ids, not the first ten the controller happened to send:
        # an unstable list would show a different set of devices on every poll.
        self.assertEqual(listed,
                         [normalize_inputs.uid(41 + i) for i in range(10)])
        self.assertEqual(data["counts"]["offlineTotal"], 20)

    def test_the_down_gateway_is_not_the_device_the_bound_drops(self):
        # It sorts first, so it heads the list. A bound applied before ordering
        # would drop the one device REQ-002 rule 3 turns on.
        data, _warnings = self.normalized("success_500_down_bounded_list")
        self.assertEqual(data["offlineDevices"][0]["name"], "UDM Pro")

    def test_a_transitional_device_never_reaches_the_offline_list(self):
        data, _warnings = self.normalized("success_transitional_only")
        self.assertEqual(data["offlineDevices"], [])
        self.assertEqual(data["counts"]["offlineTotal"], 0)
        self.assertEqual(data["counts"]["byClass"]["transitional"], 2)

    def test_an_unrecognised_state_is_unknown_and_warns_with_the_value(self):
        data, collector = self.normalized("success_unknown_state")
        self.assertEqual(data["counts"]["byClass"]["unknown"], 1)
        self.assertEqual(data["counts"]["offlineTotal"], 0)
        warning = [entry for entry in collector.to_list()
                   if entry["code"] == "unknown_device_state"]
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0]["detail"]["state"], "REBOOTING")

    def test_every_documented_state_maps_to_its_class(self):
        # The ten API states, longhand against protocol-v1.md's table.
        expected = {
            "ONLINE": "online",
            "PENDING_ADOPTION": "transitional",
            "UPDATING": "transitional",
            "GETTING_READY": "transitional",
            "ADOPTING": "transitional",
            "DELETING": "transitional",
            "OFFLINE": "down",
            "CONNECTION_INTERRUPTED": "down",
            "ISOLATED": "impaired",
            "U5G_INCORRECT_TOPOLOGY": "impaired",
        }
        for state, klass in expected.items():
            with self.subTest(state=state):
                self.assertEqual(normalize.classify_state(state), klass)
        for state in ("REBOOTING", "", "online", None, 7):
            with self.subTest(state=state):
                self.assertEqual(normalize.classify_state(state), "unknown")

    def test_a_missing_metric_is_null_and_never_zero(self):
        # BIZ-003. Zero is a value a controller can actually report.
        data, _warnings = self.normalized("success_optional_gaps")
        self.assertIsNone(data["wan"]["uptimeSec"])
        self.assertIsNone(data["wan"]["downloadBps"])
        self.assertIsNone(data["gateways"][0]["uptimeSec"])
        self.assertIsNone(data["counts"]["clients"])

    def test_a_zero_metric_the_controller_reported_survives_as_zero(self):
        inputs = normalize_inputs.cases()["success_healthy"]
        statistics = {normalize_inputs.uid(10):
                      normalize_inputs.stats(0, 0, 0)}
        data = normalize.build(inputs["site"], inputs["devices"], 0, statistics,
                               "9.1.0", warn.Warnings())
        self.assertEqual(data["wan"]["uptimeSec"], 0)
        self.assertEqual(data["wan"]["downloadBps"], 0)
        self.assertEqual(data["counts"]["clients"], 0)

    def test_a_non_integer_metric_is_refused_rather_than_coerced(self):
        inputs = normalize_inputs.cases()["success_healthy"]
        for value in (True, "864000", 1.5, -1):
            with self.subTest(value=value):
                statistics = {normalize_inputs.uid(10): {"uptimeSec": value}}
                data = normalize.build(inputs["site"], inputs["devices"], 0,
                                       statistics, "9.1.0", warn.Warnings())
                self.assertIsNone(data["wan"]["uptimeSec"])

    def test_the_wan_model_omits_the_metrics_the_api_cannot_supply(self):
        data, _warnings = self.normalized("success_healthy")
        self.assertEqual(sorted(data["wan"]),
                         ["downloadBps", "status", "uploadBps", "uptimeSec"])

    def test_wan_status_follows_req_008a_for_every_gateway_combination(self):
        def status(states):
            devices = [normalize_inputs.device(10 + i, state, ["gateway"])
                       for i, state in enumerate(states)]
            return normalize.build({"id": "s", "name": "S"}, devices, 0, {},
                                   "9.1.0", warn.Warnings())["wan"]["status"]

        self.assertEqual(status([]), "unknown")
        self.assertEqual(status(["OFFLINE"]), "down")
        self.assertEqual(status(["OFFLINE", "CONNECTION_INTERRUPTED"]), "down")
        self.assertEqual(status(["ONLINE"]), "up")
        self.assertEqual(status(["ONLINE", "ONLINE"]), "up")
        self.assertEqual(status(["ONLINE", "OFFLINE"]), "degraded")
        self.assertEqual(status(["ONLINE", "ISOLATED"]), "degraded")
        self.assertEqual(status(["ONLINE", "REBOOTING"]), "degraded")
        # A transitional gateway is not a degraded WAN: REQ-008a's third bullet
        # names down, impaired and unknown, and nothing else.
        self.assertEqual(status(["ONLINE", "UPDATING"]), "up")

    def test_the_primary_gateway_is_always_among_the_four_fetched(self):
        # One ordering serves both choices. With two, the primary could be the
        # one gateway whose statistics nobody asked for — and `wan`'s metrics
        # would be null on a site where four calls succeeded.
        devices = [normalize_inputs.device(10 + i, "OFFLINE", ["gateway"])
                   for i in range(5)]
        devices.append(normalize_inputs.device(99, "ONLINE", ["gateway"]))
        ordered = normalize.gateway_order(devices)
        fetched = ordered[:normalize.GATEWAY_STATISTICS_MAX]
        self.assertIn(ordered[0], fetched)
        # The single ONLINE gateway sorts first despite the highest id.
        self.assertEqual(ordered[0]["id"], normalize_inputs.uid(99))

    def test_the_producer_refuses_to_emit_a_broken_partition(self):
        # DATA-006b is checked here as well as by the service. Failing with
        # `internal` names the process that has the bug; shipping the envelope
        # would get it rejected as `malformed_response`, which points at the
        # wire instead.
        data, _warnings = self.normalized("success_degraded")
        good = json.loads(json.dumps(data))
        normalize._check_invariants(good)          # the control

        broken = json.loads(json.dumps(data))
        broken["counts"]["devicesTotal"] += 1
        with self.assertRaises(errors.InternalError):
            normalize._check_invariants(broken)

        broken = json.loads(json.dumps(data))
        broken["counts"]["offlineTotal"] += 1
        with self.assertRaises(errors.InternalError):
            normalize._check_invariants(broken)

    def test_every_build_runs_the_invariant_check(self):
        # The check above is only worth anything if `build` calls it. Patched to
        # record rather than asserted by reading the source, so a refactor that
        # moved the call still has to keep it.
        seen = []
        inputs = normalize_inputs.cases()["success_healthy"]
        with _patched(normalize, "_check_invariants", lambda data: seen.append(data)):
            normalize.build(inputs["site"], inputs["devices"], inputs["clients"],
                            inputs["statistics"], "9.1.0", warn.Warnings())
        self.assertEqual(len(seen), 1)


class Configuration(unittest.TestCase):
    """`config.json`: four keys, none of them trusted.

    This class exists because a mutation pass found the module had no test at
    all — removing the apiRoot check, the unknown-key check and the boolean
    check for `allowInsecureTls` all survived. The last one is the reason it
    matters: a coerced `"false"` is truthy, and silently disabling TLS
    verification is not a thing to guess at.
    """

    def parse(self, body):
        return config_module.parse(json.dumps(body).encode("utf-8"))

    def test_a_minimal_configuration_parses_with_its_defaults(self):
        parsed = self.parse({"apiRoot": "https://192.168.1.1/proxy"})
        self.assertEqual(parsed.api_root, "https://192.168.1.1/proxy")
        self.assertIsNone(parsed.site_id)
        self.assertIsNone(parsed.custom_ca_path)
        self.assertIs(parsed.allow_insecure_tls, False)

    def test_the_parsed_configuration_is_immutable(self):
        parsed = self.parse({"apiRoot": "https://h/proxy"})
        with self.assertRaises(AttributeError):
            parsed.api_root = "https://elsewhere/proxy"
        with self.assertRaises(AttributeError):
            del parsed.site_id

    def test_a_repr_never_carries_the_controller_address(self):
        parsed = self.parse({"apiRoot": "https://192.168.1.1/proxy"})
        self.assertNotIn("192.168.1.1", repr(parsed))

    def test_a_missing_api_root_is_unconfigured_not_internal(self):
        # Without this check the value reaches `.strip()` as None, and the
        # entry point's backstop reports `internal` — which tells the user
        # their helper is broken rather than that their configuration is.
        for body in ({}, {"apiRoot": None}, {"apiRoot": ""}, {"apiRoot": "   "},
                     {"apiRoot": 7}, {"apiRoot": ["https://h"]}):
            with self.subTest(body=body):
                with self.assertRaises(errors.ConfigError) as caught:
                    self.parse(body)
                self.assertEqual(caught.exception.kind, "unconfigured")

    def test_allow_insecure_tls_is_never_coerced(self):
        for value in ("false", "true", 0, 1, "", []):
            with self.subTest(value=value):
                with self.assertRaises(errors.ConfigError):
                    self.parse({"apiRoot": "https://h/p", "allowInsecureTls": value})
        self.assertIs(
            self.parse({"apiRoot": "https://h/p", "allowInsecureTls": True}).allow_insecure_tls,
            True)
        # An explicit null is the absent case, which SEC-005 makes `false`.
        self.assertIs(
            self.parse({"apiRoot": "https://h/p", "allowInsecureTls": None}).allow_insecure_tls,
            False)

    def test_an_unknown_key_is_named_rather_than_ignored(self):
        # A typo means the setting the user believes they set is not in force.
        with self.assertRaises(errors.ConfigError) as caught:
            self.parse({"apiRoot": "https://h/p", "allowInsecureTLS": True})
        self.assertIn("allowInsecureTLS", caught.exception.message)

    def test_an_unusable_site_id_or_ca_path_is_refused(self):
        for body in ({"apiRoot": "https://h/p", "siteId": ""},
                     {"apiRoot": "https://h/p", "siteId": 7},
                     {"apiRoot": "https://h/p", "customCaPath": ""},
                     {"apiRoot": "https://h/p", "customCaPath": 7},
                     {"apiRoot": "https://h/p",
                      "customCaPath": "/" + "a" * config_module.CA_PATH_MAX_CHARS}):
            with self.subTest(body=body):
                with self.assertRaises(errors.ConfigError):
                    self.parse(body)

    def test_unparseable_bytes_are_unconfigured_and_name_the_file(self):
        for raw in (b"", b"not json", b"[]", b'"string"', b"\xff\xfe"):
            with self.subTest(raw=raw):
                with self.assertRaises(errors.ConfigError) as caught:
                    config_module.parse(raw)
                self.assertIn("config.json", caught.exception.message)

    def test_no_message_quotes_a_value_back(self):
        # config.json sits beside the api-key, and a file somebody pasted into
        # the wrong place could hold anything. No rejection message repeats a
        # VALUE — the unknown-key message names a key, which is the one thing
        # the user needs in order to fix the typo.
        token = Sanitization.FAKE_TOKEN
        rejections = [
            {"apiRoot": "https://h/p", "allowInsecureTls": token},
            {"apiRoot": "https://h/p", "siteId": 7, "customCaPath": token},
            {"apiRoot": token.encode("ascii").decode("ascii") * 0 or None},
        ]
        for body in rejections:
            with self.subTest(body=body):
                with self.assertRaises(errors.ConfigError) as caught:
                    self.parse(body)
                self.assertNotIn(token, caught.exception.message)
        with self.assertRaises(errors.ConfigError) as caught:
            config_module.parse(token.encode("utf-8"))
        self.assertNotIn(token, caught.exception.message)

    def test_userinfo_in_the_api_root_is_left_to_the_route_builder(self):
        # Deliberately NOT rejected here: `paths.py` answers "may this be read",
        # this module answers "is it usable", and `routes.parse_api_root`
        # answers "is this a legal URL" — at every assembly, not once at load.
        # Duplicating the check here would make the weaker copy the one people
        # maintain.
        parsed = self.parse({"apiRoot": "https://user:secret@h/p"})
        with self.assertRaises(errors.ConfigError):
            routes.build(parsed.api_root, "info")


class CollectionPolicy(unittest.TestCase):
    """BIZ-004, BIZ-006 and DATA-012, driven with no socket anywhere."""

    SITE = normalize_inputs.uid(1)

    def setUp(self):
        self.warnings = warn.Warnings()
        self.config = config_module.Config(
            "https://192.0.2.9/proxy/network/integration", None, None, False)

    def responder(self, overrides=None, sites=1):
        """A `get_json` double that answers by route name."""
        overrides = overrides or {}
        site_records = [{"id": normalize_inputs.uid(1 + i), "name": "Site %d" % i}
                        for i in range(sites)]
        devices = normalize_inputs.cases()["success_healthy"]["devices"]

        def page(records):
            return {"offset": 0, "limit": 200, "count": len(records),
                    "totalCount": len(records), "data": records}

        bodies = {
            "info": {"applicationVersion": "9.1.0"},
            "sites": page(site_records),
            "devices": page(devices),
            "clients": page([{"id": normalize_inputs.uid(500 + i)}
                             for i in range(42)]),
            "device_statistics": normalize_inputs.stats(864000, 12000000, 3000000),
            "device": normalize_inputs.detail(ports=4),
        }
        self.calls = []

        def get_json(request, credential, context, deadline, warnings=None):
            self.calls.append(request.route)
            if request.route in overrides:
                raise overrides[request.route]
            body = bodies[request.route]
            return body, len(json.dumps(body))

        return get_json

    def run_batch(self, **kwargs):
        return collect.run(self.config, _StubCredential(), None,
                           deadline.Deadline(), self.warnings,
                           get_json=self.responder(**kwargs))

    def test_a_complete_batch_produces_the_data_object(self):
        batch = self.run_batch()
        self.assertEqual(batch.data["counts"]["clients"], 42)
        self.assertEqual(batch.data["counts"]["devicesTotal"], 5)
        self.assertEqual(batch.site_id, self.SITE)

    # The batch's route set and the ALLOWLIST are two different claims, and
    # conflating them cost a test its meaning once already: asserting
    # `set(calls) == set(ROUTE_NAMES)` reads as "no others are requested" but
    # actually also asserts "all of them are", so adding an allowlisted route
    # the batch does not yet use fails a test about something else entirely.
    BATCH_ROUTES = {"info", "sites", "devices", "clients", "device_statistics",
                    "device"}

    def test_no_route_outside_the_allowlist_is_requested(self):
        self.run_batch()
        self.assertTrue(set(self.calls) <= set(routes.ROUTE_NAMES),
                        sorted(set(self.calls) - set(routes.ROUTE_NAMES)))

    def test_the_batch_requests_exactly_the_routes_it_needs(self):
        self.run_batch()
        self.assertEqual(set(self.calls), self.BATCH_ROUTES)

    def test_a_required_collection_failure_keeps_its_own_kind(self):
        # BIZ-004 says the batch fails; it does not say it becomes
        # `partial_response`. A network failure on /devices is a network
        # failure, and reporting it as a completeness problem would send the
        # user looking at their controller's data instead of their link.
        for route in ("info", "sites", "devices"):
            with self.subTest(route=route):
                with self.assertRaises(errors.NetworkError):
                    self.run_batch(overrides={route: errors.NetworkError("x")})

    def test_an_optional_collection_failure_yields_a_successful_batch(self):
        batch = self.run_batch(overrides={"clients": errors.NetworkError("x")})
        self.assertIsNone(batch.data["counts"]["clients"])
        self.assertIn("clients_unavailable", self.warnings.codes())
        # The device health the panel actually needs is unaffected.
        self.assertEqual(batch.data["counts"]["devicesTotal"], 5)

    def test_an_optional_failure_never_reports_zero_for_unknown(self):
        # BIZ-002/BIZ-003. Zero is what an empty site reports, and confusing
        # the two turns "we could not read it" into "there is nobody here".
        batch = self.run_batch(overrides={"clients": errors.NetworkError("x")})
        self.assertIsNone(batch.data["counts"]["clients"])
        self.assertNotEqual(batch.data["counts"]["clients"], 0)

    def test_a_statistics_failure_costs_only_that_gateway(self):
        batch = self.run_batch(
            overrides={"device_statistics": errors.NetworkError("x")})
        self.assertIsNone(batch.data["gateways"][0]["uptimeSec"])
        self.assertIsNone(batch.data["wan"]["uptimeSec"])
        self.assertIn("statistics_unavailable", self.warnings.codes())
        self.assertEqual(batch.data["counts"]["devicesTotal"], 5)

    def test_the_wans_route_is_not_requested_at_all(self):
        # DEV-5 Option B. The removal is asserted as an OBSERVATION of the
        # batch's traffic, not merely as an absence from the route table:
        # deleting the entry and leaving a hand-built URL in `collect.py` would
        # satisfy the allowlist test and still send the request.
        self.run_batch()
        self.assertNotIn("wans", self.calls)
        self.assertNotIn("wans", routes.ROUTES)

    def test_an_incomplete_required_collection_is_partial_response(self):
        # The other half: an INVARIANT failure, where the transport worked and
        # the data cannot be proven complete, is what BIZ-002 calls partial.
        def get_json(request, credential, context, deadline, warnings=None):
            if request.route == "devices":
                body = {"offset": 0, "limit": 200, "count": 2, "totalCount": 5,
                        "data": [{"id": normalize_inputs.uid(1)},
                                 {"id": normalize_inputs.uid(2)}]}
                return body, 10
            return self.responder()(request, credential, context, deadline)

        with self.assertRaises(errors.PartialResponseError):
            collect.run(self.config, _StubCredential(), None,
                        deadline.Deadline(), self.warnings, get_json=get_json)

    def test_data_012_one_site_auto_selects_with_a_warning(self):
        self.run_batch(sites=1)
        self.assertIn("site_auto_selected", self.warnings.codes())

    def test_data_012_several_sites_suspend_and_carry_the_pairs(self):
        with self.assertRaises(errors.SiteUnselectedError) as caught:
            self.run_batch(sites=3)
        self.assertEqual(caught.exception.kind, "site_unselected")
        self.assertFalse(errors.retryable_for("site_unselected"))
        detail = [entry for entry in self.warnings.to_list()
                  if entry["code"] == "sites_discovered"][0]["detail"]
        self.assertEqual(len(detail["sites"]), 3)
        self.assertEqual(sorted(detail["sites"][0]), ["id", "name"])

    def test_data_012_zero_sites_is_unsupported(self):
        with self.assertRaises(errors.UnsupportedError):
            self.run_batch(sites=0)

    def test_a_committed_site_that_does_not_exist_suspends_rather_than_retries(self):
        self.config = config_module.Config(
            "https://192.0.2.9/proxy/network/integration",
            normalize_inputs.uid(777), None, False)
        with self.assertRaises(errors.SiteUnselectedError):
            self.run_batch(sites=3)
        self.assertIn("sites_discovered", self.warnings.codes())

    def test_a_committed_site_is_selected_out_of_several(self):
        self.config = config_module.Config(
            "https://192.0.2.9/proxy/network/integration",
            normalize_inputs.uid(2), None, False)
        batch = self.run_batch(sites=3)
        self.assertEqual(batch.site_id, normalize_inputs.uid(2))
        self.assertNotIn("site_auto_selected", self.warnings.codes())

    def test_the_version_gate_runs_before_anything_else_is_requested(self):
        def get_json(request, credential, context, deadline, warnings=None):
            self.calls.append(request.route)
            body = {"applicationVersion": "10.4.57"}
            return body, len(json.dumps(body))

        self.calls = []
        with self.assertRaises(errors.UnsupportedError):
            collect.run(self.config, _StubCredential(), None,
                        deadline.Deadline(), self.warnings, get_json=get_json)
        self.assertEqual(self.calls, ["info"])

    def test_statistics_cover_every_gateway_across_both_tiers(self):
        devices = [normalize_inputs.device(10 + i, "ONLINE", ["gateway"])
                   for i in range(6)]

        def get_json(request, credential, context, deadline, warnings=None):
            self.calls.append(request.route)
            if request.route == "devices":
                body = {"offset": 0, "limit": 200, "count": 6, "totalCount": 6,
                        "data": devices}
            elif request.route == "sites":
                body = {"offset": 0, "limit": 200, "count": 1, "totalCount": 1,
                        "data": [{"id": self.SITE, "name": "S"}]}
            elif request.route == "info":
                body = {"applicationVersion": "9.1.0"}
            elif request.route == "device_statistics":
                body = normalize_inputs.stats(1, 2, 3)
            else:
                body = {"offset": 0, "limit": 200, "count": 0, "totalCount": 0,
                        "data": []}
            return body, len(json.dumps(body))

        self.calls = []
        batch = collect.run(self.config, _StubCredential(), None,
                            deadline.Deadline(), self.warnings,
                            get_json=get_json)
        # REQ-008a's guarantee is about `wan`'s SOURCE, not about the total
        # number of statistics requests. Since REQ-B02 the browse tier fetches
        # statistics too, for the head of REQ-B11's order, so six gateways
        # produce six calls: four for REQ-008a and two more from the browse
        # tier that REQ-008a had already covered are skipped, with the other
        # two picked up.
        #
        # Restated as the three things REQ-008a actually promises. The old form
        # — `calls.count("device_statistics") == 4` — would now fail for a
        # correct implementation and pass for one that fetched four ARBITRARY
        # gateways, which is the part that matters.
        self.assertEqual(len(batch.data["gateways"]), 6)
        # No truncation warning: REQ-008a's cap fetched four and the browse tier
        # picked up the other two, so every gateway has metrics and there is
        # nothing missing to report. The warning now counts what is ABSENT
        # rather than what the cap declined, which is a smaller and truer claim.
        self.assertNotIn("gateway_statistics_truncated", self.warnings.codes())
        self.assertTrue(all(entry["uptimeSec"] is not None
                            for entry in batch.data["gateways"]))

        # 1. `wan` is populated, so the primary gateway was among those fetched.
        self.assertIsNotNone(batch.data["wan"]["uptimeSec"])
        # 2. The first four in PRIMARY order carry metrics.
        primary_order = [d["id"] for d
                         in normalize.gateway_order(devices)[:4]]
        with_metrics = {entry["id"] for entry in batch.data["gateways"]
                        if entry["uptimeSec"] is not None}
        self.assertTrue(set(primary_order) <= with_metrics)
        # 3. No device is asked twice. The browse tier skips what REQ-008a
        #    already fetched, so the union is what is requested and not the sum.
        self.assertEqual(self.calls.count("device_statistics"), 6)
        self.assertLessEqual(self.calls.count("device_statistics"), len(devices))

    def test_the_browse_tier_never_costs_req_008a_its_gateway(self):
        """REQ-B02's bound must not be able to take `wan`'s metrics away.

        Browse order puts `down` devices first, so on a site with many failed
        switches and one healthy gateway the gateway sorts past the detail
        bound. Deriving REQ-008a's four from the browse head — which reads as a
        tidy simplification — would silently cost the panel its WAN reading
        exactly when the site is at its worst.
        """
        gateway = normalize_inputs.device(10, "ONLINE", ["gateway"], "GW")
        broken = [normalize_inputs.device(100 + i, "OFFLINE", ["switching"],
                                          "Broken %03d" % i)
                  for i in range(bounds.DEVICE_DETAIL_MAX + 10)]
        devices = broken + [gateway]

        def get_json(request, credential, context, deadline, warnings=None):
            self.calls.append((request.route, getattr(request, "params", None)))
            if request.route == "devices":
                body = {"offset": 0, "limit": 200, "count": len(devices),
                        "totalCount": len(devices), "data": devices}
            elif request.route == "sites":
                body = {"offset": 0, "limit": 200, "count": 1, "totalCount": 1,
                        "data": [{"id": self.SITE, "name": "S"}]}
            elif request.route == "info":
                body = {"applicationVersion": "9.1.0"}
            elif request.route == "device_statistics":
                body = normalize_inputs.stats(864000, 12000000, 3000000)
            elif request.route == "device":
                body = normalize_inputs.detail(ports=2)
            else:
                body = {"offset": 0, "limit": 200, "count": 0, "totalCount": 0,
                        "data": []}
            return body, len(json.dumps(body))

        self.calls = []
        batch = collect.run(self.config, _StubCredential(), None,
                            deadline.Deadline(), self.warnings,
                            get_json=get_json)
        # The gateway is the LAST device in browse order — every other device
        # is `down` and sorts ahead of it — so it is past the detail bound.
        ordered = normalize.browse_order(devices)
        self.assertEqual(ordered[-1]["id"], gateway["id"])
        self.assertGreater(len(ordered), bounds.DEVICE_DETAIL_MAX)
        # And `wan` still has its metrics.
        self.assertEqual(batch.data["wan"]["uptimeSec"], 864000)
        self.assertEqual(batch.data["gateways"][0]["uptimeSec"], 864000)


def _page_of(records, url):
    """Serve one well-formed page of `records` for the offset in `url`.

    Real pagination rather than one oversized page. DATA-009's invariants
    include `count <= limit`, so a stub that returned 250 records in a page
    declaring `limit: 200` is rejected as `partial_response` — which is the
    collector working correctly and the stub being wrong. The bounds this class
    exercises only bind above 200, so every one of its sites needs more than
    one page.
    """
    query = urllib_parse.parse_qs(urllib_parse.urlsplit(url).query)
    offset = int(query.get("offset", ["0"])[0])
    limit = int(query.get("limit", ["200"])[0])
    window = list(records)[offset:offset + limit]
    return {"offset": offset, "limit": limit, "count": len(window),
            "totalCount": len(records), "data": window}


class BrowseCollection(unittest.TestCase):
    """SPEC-v1.1-browse.md Phase B1: AC-B02 … AC-B06, AC-B12, AC-B13."""

    SITE = normalize_inputs.uid(1)

    def setUp(self):
        self.warnings = warn.Warnings()
        self.config = config_module.Config(
            "https://192.0.2.9/proxy/network/integration", self.SITE, None, False)
        self.calls = []

    def responder(self, devices, clients=(), fail_detail=None,
                  detail_ports=2):
        def get_json(request, credential, context, deadline_, warnings=None):
            route = request.route
            self.calls.append((route, request.url))
            if route == "info":
                body = {"applicationVersion": "9.1.0"}
            elif route == "sites":
                body = {"offset": 0, "limit": 200, "count": 1, "totalCount": 1,
                        "data": [{"id": self.SITE, "name": "Home"}]}
            elif route == "devices":
                body = _page_of(devices, request.url)
            elif route == "clients":
                body = _page_of(clients, request.url)
            elif route == "device_statistics":
                body = normalize_inputs.stats(864000, 12000000, 3000000,
                                              cpu=4.0, memory=30.0)
            elif route == "device":
                if fail_detail is not None and fail_detail in request.url:
                    raise errors.NetworkError("detail unreachable")
                body = normalize_inputs.detail(ports=detail_ports)
            else:
                raise AssertionError("unexpected route %r" % route)
            return body, len(json.dumps(body))
        return get_json

    def run_batch(self, devices, clients=(), budget_sec=None, **kwargs):
        limit = deadline.Deadline() if budget_sec is None else deadline.Deadline(budget_sec)
        return collect.run(self.config, _StubCredential(), None, limit,
                           self.warnings,
                           get_json=self.responder(devices, clients, **kwargs))

    def detail_of(self, code):
        for entry in self.warnings.to_list():
            if entry["code"] == code:
                return entry["detail"]
        return None

    @staticmethod
    def site_of(size, broken_at=()):
        """`size` devices, with `broken_at` indices given a not-well state.

        The broken ones are placed LATE on purpose — indices near the end — so
        that a selection which simply took the first N would miss them. That is
        the whole of REQ-B02a and it cannot be shown by a corpus whose broken
        devices happen to sort first anyway.
        """
        states = {}
        for index, state in broken_at:
            states[index] = state
        return [normalize_inputs.browse_device(
                    1000 + i, states.get(i, "ONLINE"),
                    normalize_inputs.SWITCH, "Device %04d" % i)
                for i in range(size)]

    # --- AC-B02 -----------------------------------------------------------

    def test_detail_stops_at_the_bound_and_takes_the_broken_ones_first(self):
        size = 200
        broken = [(190, "OFFLINE"), (191, "ISOLATED"), (192, "NO_SUCH_STATE")]
        devices = self.site_of(size, broken)
        data = self.run_batch(devices).data

        asked = [url for route, url in self.calls if route == "device"]
        self.assertEqual(len(asked), bounds.DEVICE_DETAIL_MAX)

        with_detail = {entry["id"] for entry in data["devices"]
                       if entry["detail"] is not None}
        self.assertEqual(len(with_detail), bounds.DEVICE_DETAIL_MAX)

        # REQ-B02a: every device that is not well got detail, despite sitting
        # at indices 190-192 of a 200-device list.
        for index, _state in broken:
            self.assertIn(normalize_inputs.uid(1000 + index), with_detail,
                          "a broken device was passed over for a healthy one")

        self.assertEqual(self.detail_of("device_detail_truncated"),
                         {"fetched": bounds.DEVICE_DETAIL_MAX, "total": size})

    def test_a_small_site_is_fetched_whole_and_warns_about_nothing(self):
        # The bound must not fire on an ordinary site. A truncation warning
        # nobody can act on is noise, and a test suite that only ever exercises
        # the truncated path would not notice it had become permanent.
        data = self.run_batch(self.site_of(9)).data
        self.assertEqual(len([e for e in data["devices"]
                              if e["detail"] is not None]), 9)
        self.assertNotIn("device_detail_truncated", self.warnings.codes())
        self.assertNotIn("devices_truncated", self.warnings.codes())

    # --- AC-B03 -----------------------------------------------------------

    def test_a_deadline_below_the_reserve_costs_detail_and_nothing_else(self):
        # REQ-B02b. The batch SUCCEEDS, the inventory is complete, and the
        # health reading is intact — only the browser loses. A device browser
        # must never be able to break the health indicator.
        devices = self.site_of(12, [(11, "OFFLINE")])
        data = self.run_batch(devices,
                              budget_sec=bounds.DETAIL_RESERVE_SEC).data
        self.assertEqual(len(data["devices"]), 12)
        self.assertEqual(data["counts"]["devicesTotal"], 12)
        self.assertEqual(data["counts"]["offlineTotal"], 1)
        self.assertTrue(all(entry["detail"] is None for entry in data["devices"]))
        self.assertEqual([route for route, _ in self.calls].count("device"), 0)
        self.assertEqual(self.detail_of("device_detail_truncated"),
                         {"fetched": 0, "total": 12})

    # --- AC-B04 -----------------------------------------------------------

    def test_one_failing_detail_request_costs_only_that_device(self):
        devices = self.site_of(6)
        target = normalize_inputs.uid(1000 + 3)
        data = self.run_batch(devices, fail_detail=target).data

        by_id = {entry["id"]: entry for entry in data["devices"]}
        self.assertIsNone(by_id[target]["detail"])
        others = [entry for identifier, entry in by_id.items()
                  if identifier != target]
        self.assertEqual(len(others), 5)
        self.assertTrue(all(entry["detail"] is not None for entry in others),
                        "one device's failure cost another device its detail")
        self.assertEqual(self.detail_of("device_detail_unavailable"),
                         {"deviceId": target})
        # And the batch is a success with a complete inventory.
        self.assertEqual(data["counts"]["devicesTotal"], 6)

    # --- AC-B05 -----------------------------------------------------------

    def test_the_browse_list_agrees_with_the_lists_it_duplicates(self):
        """`devices[]` alongside `gateways[]`/`offlineDevices[]` is duplication.

        Duplication drifts, and these two are rendered a few hundred pixels
        apart. Checked over every accept fixture rather than over one, because
        a disagreement will appear first on the fixture nobody looked at.
        """
        directory = os.path.join(_REPO, "tests", "fixtures", "envelopes",
                                 "accept")
        checked = 0
        for name in sorted(os.listdir(directory)):
            if not name.startswith("success_"):
                continue
            with io.open(os.path.join(directory, name), encoding="utf-8") as fh:
                data = json.load(fh)["data"]
            listed = {entry["id"]: entry for entry in data["devices"]}
            for other in ("gateways", "offlineDevices"):
                for entry in data[other]:
                    if entry["id"] not in listed:
                        continue          # bounded out of the browse list
                    twin = listed[entry["id"]]
                    with self.subTest(fixture=name, id=entry["id"], list=other):
                        self.assertEqual(entry["class"], twin["class"])
                        self.assertEqual(entry["state"], twin["state"])
                        self.assertEqual(entry["name"], twin["name"])
                        self.assertEqual(entry["model"], twin["model"])
                        checked += 1
        self.assertGreater(checked, 0, "no fixture had a device in both lists")

    # --- AC-B06 -----------------------------------------------------------

    def test_a_bounded_list_never_understates_the_site(self):
        devices = self.site_of(bounds.DEVICES_LISTED_MAX + 50)
        clients = [normalize_inputs.client(2000 + i, "c%d" % i, "WIRED")
                   for i in range(bounds.CLIENTS_LISTED_MAX + 20)]
        data = self.run_batch(devices, clients).data

        self.assertLessEqual(len(data["devices"]), bounds.DEVICES_LISTED_MAX)
        self.assertLessEqual(len(data["clients"]), bounds.CLIENTS_LISTED_MAX)
        # The totals are the site's, not the arrays'.
        self.assertEqual(data["counts"]["devicesTotal"],
                         bounds.DEVICES_LISTED_MAX + 50)
        self.assertEqual(data["counts"]["clients"],
                         bounds.CLIENTS_LISTED_MAX + 20)
        self.assertLess(len(data["devices"]), data["counts"]["devicesTotal"])
        self.assertLess(len(data["clients"]), data["counts"]["clients"])
        self.assertEqual(self.detail_of("devices_truncated")["total"],
                         bounds.DEVICES_LISTED_MAX + 50)
        self.assertEqual(self.detail_of("clients_truncated")["total"],
                         bounds.CLIENTS_LISTED_MAX + 20)

    # --- AC-B12 -----------------------------------------------------------

    def test_the_assembled_envelope_never_reaches_the_replacement_path(self):
        """The whole point of DATA-B04.

        `envelope.encode` replaces an over-sized envelope with an
        `oversized_response` FAILURE — the panel greys and the user gets no
        reading at all. This drives the largest site the bounds permit through
        the real encoder and asserts the replacement did not happen.
        """
        devices = self.site_of(400)
        clients = [normalize_inputs.client(
                       3000 + i, "client-with-a-fairly-long-name-%04d" % i,
                       "WIRELESS", ip="192.168.20.%d" % (i % 254),
                       mac="02:00:00:00:%02x:%02x" % (i // 256, i % 256))
                   for i in range(700)]
        # 52 ports each, which is the largest switch UniFi ships.
        data = self.run_batch(devices, clients, detail_ports=52).data

        body = envelope.success("b7c1d4e9f2a68035", "2026-01-15T12:00:00Z",
                                "2026-01-15T12:00:02Z", envelope.meta(),
                                data, self.warnings.to_list())
        text, exit_status = envelope.encode(body)
        self.assertEqual(exit_status, envelope.EXIT_SUCCESS,
                         "the envelope was replaced by an oversized failure")
        encoded = json.loads(text)
        self.assertTrue(encoded["ok"])
        self.assertIsNotNone(encoded["data"])
        self.assertLess(len(text.encode("utf-8")), envelope.STDOUT_MAX_BYTES)
        # And it says what it dropped, in ASSEMBLY_ORDER.
        self.assertIn("envelope_truncated", self.warnings.codes())
        self.assertIn(self.detail_of("envelope_truncated")["dropped"],
                      ("devices", "clients", "detail"))

    def test_the_budget_binds_on_detail_rather_than_on_the_base_lists(self):
        """A measurement, recorded as a test because the design turns on it.

        `devices[]` and `clients[]` at their full caps come to roughly 150 KiB
        against a ~192 KiB list budget, so the base records always fit and it is
        detail the budget actually constrains. An earlier version of
        `envelope_truncated` watched only the two base lists and was therefore
        unreachable. If a future field makes a base record much larger this
        fails, which is the point — the caps and the budget would then need
        rebalancing rather than silently starting to drop devices.
        """
        devices = self.site_of(bounds.DEVICES_LISTED_MAX + 20)
        clients = [normalize_inputs.client(3000 + i, "client-%04d" % i,
                                           "WIRELESS",
                                           ip="192.168.20.%d" % (i % 254),
                                           mac="02:00:00:00:%02x:%02x"
                                               % (i // 256, i % 256))
                   for i in range(bounds.CLIENTS_LISTED_MAX + 20)]
        data = self.run_batch(devices, clients, detail_ports=52).data
        self.assertEqual(len(data["devices"]), bounds.DEVICES_LISTED_MAX)
        self.assertEqual(len(data["clients"]), bounds.CLIENTS_LISTED_MAX)
        self.assertEqual(self.detail_of("envelope_truncated")["dropped"], "detail")

    def test_the_health_reading_survives_a_site_that_does_not_fit(self):
        # DATA-B04's assembly order, stated as its consequence: what a squeeze
        # costs is browsing, never the answer to "is my network fine?".
        devices = self.site_of(400, [(399, "OFFLINE")])
        clients = [normalize_inputs.client(3000 + i, "c" * 60, "WIRED")
                   for i in range(700)]
        data = self.run_batch(devices, clients, detail_ports=52).data
        self.assertEqual(data["counts"]["devicesTotal"], 400)
        self.assertEqual(data["counts"]["offlineTotal"], 1)
        self.assertEqual(data["wan"]["status"], "unknown")   # no gateway here
        self.assertEqual(len(data["offlineDevices"]), 1)
        self.assertIsNotNone(data["site"]["name"])

    # --- AC-B13 -----------------------------------------------------------

    def test_no_client_identifier_reaches_any_diagnostic_output(self):
        """REQ-B20. Client names, addresses and MACs render, and go nowhere else.

        The realistic failure is not a deliberate leak — it is a future warning
        message interpolating a client name and nobody noticing. So this
        asserts over the diagnostic output AS A WHOLE rather than over the
        specific messages that exist today.
        """
        secrets = ["kitchen-tablet-of-alice", "192.168.20.77",
                   "02:00:00:00:07:07"]
        clients = [normalize_inputs.client(4000, secrets[0], "WIRELESS",
                                           ip=secrets[1], mac=secrets[2])]
        clients += [normalize_inputs.client(4001 + i, "c%d" % i, "WIRED")
                    for i in range(bounds.CLIENTS_LISTED_MAX + 5)]
        devices = self.site_of(60, [(59, "OFFLINE")])
        data = self.run_batch(devices, clients, detail_ports=52).data

        # The client IS in the model — otherwise this test would pass against a
        # helper that simply dropped it.
        rendered = json.dumps(data["clients"])
        for secret in secrets:
            self.assertIn(secret, rendered, "the fixture client never arrived")

        diagnostics = json.dumps(self.warnings.to_list())
        for secret in secrets:
            self.assertNotIn(secret, diagnostics,
                             "a client identifier reached a warning")

        # Warnings did fire — otherwise the assertion above is vacuous.
        self.assertGreater(len(self.warnings.to_list()), 0)

    def test_a_client_identifier_appears_nowhere_but_the_client_list(self):
        """REQ-B20, over the WHOLE envelope rather than over the warnings.

        The first version of this asserted that a failing batch's error object
        carried no client data — and it could not fail, because there is no
        batch that fails after the client collection: every REQUIRED collection
        precedes it, and every later failure is optional and warns instead. A
        test that cannot fail is worse than none.

        So the check is the reachable one: encode the whole envelope, remove
        `data.clients` — the one place these values are allowed — and assert
        they appear nowhere in what is left. That covers `meta`, every warning,
        every device record and any field a future change adds, which is where
        the leak would actually come from.
        """
        secrets = ["kitchen-tablet-of-alice", "192.168.20.77",
                   "02:00:00:00:07:07"]
        clients = [normalize_inputs.client(4000, secrets[0], "WIRELESS",
                                           ip=secrets[1], mac=secrets[2])]
        devices = self.site_of(6, [(5, "OFFLINE")])
        data = self.run_batch(devices, clients).data

        self.assertIn(secrets[0], json.dumps(data["clients"]),
                      "the fixture client never arrived")

        body = envelope.success("b7c1d4e9f2a68035", "2026-01-15T12:00:00Z",
                                "2026-01-15T12:00:02Z", envelope.meta(),
                                data, self.warnings.to_list())
        text, _status = envelope.encode(body)
        whole = json.loads(text)
        del whole["data"]["clients"]
        rest = json.dumps(whole)
        for secret in secrets:
            self.assertNotIn(secret, rest,
                             "a client identifier escaped the client list")


class EnvelopeShape(unittest.TestCase):
    """DATA-005 / DATA-006a, producer side."""

    NONCE = "b7c1d4e9f2a68035"
    ATTEMPTED = "2026-01-15T12:00:00Z"

    def test_both_shapes_carry_the_same_nine_keys(self):
        success = envelope.success(self.NONCE, self.ATTEMPTED,
                                   "2026-01-15T12:00:02Z", envelope.meta(),
                                   {}, None)
        failure = envelope.failure(self.NONCE, self.ATTEMPTED, envelope.meta(),
                                   errors.to_error_object(errors.NetworkError("x")),
                                   None)
        self.assertEqual(sorted(success), sorted(envelope.KEYS))
        self.assertEqual(sorted(failure), sorted(envelope.KEYS))

    def test_exit_status_is_derived_from_the_shape(self):
        # Exit status is part of the protocol: the service rejects `ok: true`
        # with a non-zero exit, so it is returned from here rather than chosen
        # at a call site that could get it wrong.
        _text, status = envelope.encode(
            envelope.success(self.NONCE, self.ATTEMPTED, self.ATTEMPTED,
                             envelope.meta(), {}, None))
        self.assertEqual(status, 0)
        _text, status = envelope.encode(
            envelope.failure(self.NONCE, self.ATTEMPTED, envelope.meta(),
                             errors.to_error_object(errors.NetworkError("x")), None))
        self.assertNotEqual(status, 0)

    def test_meta_is_always_an_object_with_a_helper_version(self):
        blank = envelope.meta()
        self.assertEqual(blank["helperVersion"], unifi.HELPER_VERSION)
        for field in ("commitGeneration", "apiRootHost", "siteId",
                      "allowInsecureTls", "customCaInUse"):
            self.assertIsNone(blank[field], field)

    def test_meta_carries_the_host_only_and_never_the_url(self):
        parsed = config_module.parse(
            b'{"apiRoot":"https://192.168.1.1:8443/proxy/network/integration"}')
        built = envelope.meta(parsed)
        self.assertEqual(built["apiRootHost"], "192.168.1.1:8443")
        self.assertNotIn("proxy", json.dumps(built))
        self.assertFalse(built["allowInsecureTls"])
        self.assertFalse(built["customCaInUse"])

    def test_an_oversized_envelope_is_replaced_and_never_truncated(self):
        # Truncating produces half a JSON object, which the service reports as
        # `malformed_response` — pointing at the wire for what is a size
        # problem here.
        huge = envelope.success(self.NONCE, self.ATTEMPTED, self.ATTEMPTED,
                                envelope.meta(),
                                {"padding": "a" * (envelope.STDOUT_MAX_BYTES + 1)},
                                None)
        text, status = envelope.encode(huge)
        self.assertLess(len(text.encode("utf-8")), envelope.STDOUT_MAX_BYTES)
        self.assertNotEqual(status, 0)
        replacement = json.loads(text)
        self.assertEqual(replacement["error"]["kind"], "oversized_response")
        self.assertEqual(replacement["nonce"], self.NONCE)

    def test_the_stdout_bound_is_the_protocol_number(self):
        self.assertEqual(envelope.STDOUT_MAX_BYTES, 256 * 1024)
        self.assertEqual(envelope.STDERR_MAX_BYTES, 4 * 1024)

    def test_the_stderr_bound_caps_and_reports_that_it_capped(self):
        # AC-049's producer half. DATA-005b bounds BOTH sides: the helper at
        # 4 KiB (here) and the service at 8 KiB (tests/model/protocol.test.js).
        # The service WAITS for this stream (REQ-017b), so an unbounded one is a
        # hang as well as a disclosure.
        text, exceeded = envelope.bounded_stderr("x" * 64 * 1024)
        self.assertLessEqual(len(text.encode("utf-8")), envelope.STDERR_MAX_BYTES)
        self.assertTrue(exceeded)
        text, exceeded = envelope.bounded_stderr("short")
        self.assertEqual(text, "short")
        self.assertFalse(exceeded)


class ConfigureProtocol(unittest.TestCase):
    """DATA-004's commit protocol, at the level `tests/test_configure.sh` cannot reach.

    That suite drives the real script and asserts what a user would see. Two
    properties are invisible from there: the ORDER the three files are written
    in, and whether the directory is fsynced after each rename. Both survived a
    mutation pass against the shell suite, which is what this class is for.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="omarchy-unifi-commit-")
        self.addCleanup(shutil.rmtree, self.directory, True)

    def test_commit_json_is_written_last(self):
        # DATA-004 calls `commit.json` the commit point. The ordering is what
        # makes the meaning of an interrupted write unambiguous: at every
        # instant the marker on disk is one that was fully written and
        # certified a set that existed.
        #
        # Worth being precise about what this does NOT buy: the safety property
        # comes from the marker carrying digests of BOTH files, so an
        # interruption in either order leaves a set that fails verification and
        # is reported `uncommitted`. The order is the protocol and the clarity;
        # the two digests are the guarantee.
        written = []
        with _patched(configure, "atomic_write",
                      lambda directory, name, data: written.append(name)):
            configure.commit(self.directory, b'{"apiRoot":"https://192.0.2.9"}',
                             b"sk-key\n", 3)
        self.assertEqual(written, [paths.CONFIG_NAME, paths.API_KEY_NAME,
                                   paths.COMMIT_NAME])

    def test_a_rollback_also_writes_the_marker_last(self):
        # And on the way OUT, for the same reason: at no instant may a reader
        # see a marker certifying bytes that are no longer there.
        written = []
        with _patched(configure, "atomic_write",
                      lambda directory, name, data: written.append(name)):
            configure._restore(self.directory, {
                paths.CONFIG_NAME: b"c", paths.API_KEY_NAME: b"k",
                paths.COMMIT_NAME: b"m"})
        self.assertEqual(written[-1], paths.COMMIT_NAME)

    def test_each_rename_is_followed_by_a_directory_fsync(self):
        # The step people leave out. Without it the RENAME itself can be lost on
        # a crash, and `commit.json` can become durable before the files it
        # certifies — which is the one ordering the protocol exists to prevent.
        synced = []
        real_fsync = os.fsync

        def recording_fsync(fd):
            try:
                synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            except OSError:
                synced.append(None)
            return real_fsync(fd)

        with _patched(os, "fsync", recording_fsync):
            configure.atomic_write(self.directory, "probe", b"contents")
        self.assertIn(True, synced, "the directory was never fsynced")
        self.assertIn(False, synced, "the file was never fsynced")
        # File first, then the directory: fsyncing the directory before the file
        # makes the rename durable ahead of the bytes it points at.
        self.assertLess(synced.index(False), synced.index(True))

    def test_the_written_file_carries_no_group_or_other_bits(self):
        configure.atomic_write(self.directory, "probe", b"contents")
        mode = stat.S_IMODE(os.stat(os.path.join(self.directory, "probe")).st_mode)
        self.assertEqual(mode & 0o077, 0, "SEC-002 forbids every group and other bit")

    def test_a_written_set_verifies_against_the_helper_s_own_loader(self):
        # The agreement is by SHARED CODE — configure imports commitset.py — so
        # this asserts the consequence rather than restating the algorithm.
        config_bytes = b'{"apiRoot":"https://192.0.2.9/proxy"}\n'
        api_key_bytes = b"sk-round-trip-key\n"
        configure.commit(self.directory, config_bytes, api_key_bytes, 5)
        dir_fd = paths.open_config_dir(self.directory)
        try:
            loaded = commitset.load(dir_fd)
        finally:
            os.close(dir_fd)
        self.assertEqual(loaded.generation, 5)
        self.assertEqual(loaded.config_bytes, config_bytes)
        self.assertEqual(loaded.api_key_bytes, api_key_bytes)

    def test_the_credential_is_stored_exactly_as_it_is_hashed(self):
        # DATA-004b. A version that hashed the trimmed value while writing the
        # raw one — or the reverse — produces a set that can NEVER validate,
        # and the failure is permanent rather than intermittent.
        #
        # Normalizing to a single trailing newline is a separate, cosmetic
        # choice; it is safe either way because both sides work from the file's
        # bytes. What is asserted here is the property that is not cosmetic.
        for supplied in (b"sk-key", b"sk-key\n", b"  sk-key  \n"):
            with self.subTest(supplied=supplied):
                stored = configure.read_credential(_KeyFromBytes(self, supplied))
                configure.commit(self.directory, b'{"apiRoot":"https://192.0.2.9"}',
                                 stored, 1)
                with open(os.path.join(self.directory, paths.API_KEY_NAME), "rb") as handle:
                    on_disk = handle.read()
                self.assertEqual(on_disk, stored)
                with open(os.path.join(self.directory, paths.COMMIT_NAME), "rb") as handle:
                    marker = json.loads(handle.read().decode("utf-8"))
                self.assertEqual(marker[commitset.API_KEY_DIGEST_KEY],
                                 commitset.digest(on_disk))

    def test_the_reload_outcome_table_is_the_seven_data_010b_rows(self):
        expected = [
            ("", 0),
            ("omarchy-shell is not running", 0),
            ("Target not found.", 0),
            ("omarchy-shell is not responding", 1),
            ("omarchy-shell is not ready", 1),
            ("Function not found.", 1),
        ]
        self.assertEqual([(row[0], row[3]) for row in configure.RELOAD_OUTCOMES],
                         expected)
        # The seventh row is "any other", which is the fallthrough.
        self.assertEqual(configure.UNKNOWN_OUTCOME[2], configure.EXIT_FAILURE)
        exits_zero = [row for row in configure.RELOAD_OUTCOMES if row[3] == 0]
        self.assertEqual(len(exits_zero), 3)


class _KeyFromBytes(object):
    """An args-shaped double that hands `read_credential` a file of bytes."""

    def __init__(self, case, data):
        path = os.path.join(case.directory, "supplied-key")
        with open(path, "wb") as handle:
            handle.write(data)
        self.api_key_file = path
        self.api_key_stdin = False


class EntryPoint(unittest.TestCase):
    """The one rule this layer owns: every exit path writes exactly one envelope."""

    def test_the_timestamp_is_rfc_3339_utc_with_a_z_suffix(self):
        # `datetime.now(timezone.utc).isoformat()` emits +00:00, which DATA-008
        # rejects — the service would then discard a batch that worked.
        stamp = unifi_status.utc_now()
        self.assertRegex(stamp, r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
        self.assertLessEqual(len(stamp), 64)

    def test_argv_parsing_accepts_only_what_the_service_sends(self):
        self.assertEqual(unifi_status.parse_args(["--nonce", "abc"])[0], "abc")
        self.assertEqual(unifi_status.parse_args(["--nonce=abc"])[0], "abc")
        self.assertEqual(
            unifi_status.parse_args(["--nonce", "abc", "--config-dir", "/tmp/x"])[1],
            "/tmp/x")
        for argv in ([], ["--nonce"], ["-n", "abc"], ["--nonce", "abc", "extra"],
                     ["--nonce", ""], ["--nonce", "x" * 129]):
            with self.subTest(argv=argv):
                with self.assertRaises(errors.InternalError):
                    unifi_status.parse_args(argv)

    def test_the_config_directory_is_an_argument_not_an_environment_variable(self):
        # An env var is ambient: every child of a shell somebody has touched
        # inherits it silently, which is the property SEC-007a spends -E, -s
        # and the TLS scrub removing from a process that reads an API key.
        source = _read_source(os.path.join(_REPO, "helper", "unifi_status.py"))
        self.assertNotIn("os.environ.get", source)
        self.assertIn("--config-dir", source)

    def test_run_never_raises_and_always_returns_an_envelope(self):
        for directory in ("/nonexistent-config-dir", _REPO, "/dev/null"):
            with self.subTest(directory=directory):
                built = unifi_status.run("abc", directory, "2026-01-15T12:00:00Z")
                self.assertEqual(sorted(built), sorted(envelope.KEYS))
                self.assertIs(built["ok"], False)
                self.assertIsNotNone(built["error"])
                self.assertIsInstance(built["meta"], dict)

    def test_an_unexpected_exception_becomes_internal_and_not_a_traceback(self):
        with _patched(unifi_status.paths, "open_config_dir",
                      lambda path: 1 / 0):
            built = unifi_status.run("abc", "/tmp", "2026-01-15T12:00:00Z")
        self.assertEqual(built["error"]["kind"], "internal")
        self.assertNotIn("ZeroDivision", json.dumps(built))

    def test_a_missing_nonce_still_produces_a_well_formed_envelope(self):
        # It will be discarded on the nonce check (DATA-005a), which is correct.
        # Emitting nothing would leave the service waiting on a stream that
        # never carries a value.
        stream = io.StringIO()
        with _patched(sys, "stdout", stream):
            status = unifi_status.main([])
        self.assertNotEqual(status, 0)
        built = json.loads(stream.getvalue())
        self.assertEqual(sorted(built), sorted(envelope.KEYS))
        self.assertEqual(built["error"]["kind"], "internal")

    def test_the_helper_writes_one_json_value_and_nothing_else(self):
        stream = io.StringIO()
        with _patched(sys, "stdout", stream):
            unifi_status.main(["--nonce", "abc", "--config-dir", "/nonexistent"])
        text = stream.getvalue()
        self.assertEqual(text.strip(), text)
        json.loads(text)

    def test_the_launch_flags_in_the_docstring_are_the_documented_ones(self):
        # HC-14 and HC-15 in one line each. `-I` would strip the script's own
        # directory from sys.path on 3.11+ and the package would not import.
        source = _read_source(os.path.join(_REPO, "helper", "unifi_status.py"))
        self.assertIn("python3 -B -E -s", source)
        self.assertIn("sys.path.insert", source)


# --- helpers ----------------------------------------------------------------

class _patched(object):
    """Minimal attribute patcher. unittest.mock would do, but this is three lines
    and keeps the test's dependency surface identical on 3.9 and 3.14."""

    def __init__(self, target, name, value):
        self.target, self.name, self.value = target, name, value

    def __enter__(self):
        self.original = getattr(self.target, self.name)
        setattr(self.target, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.target, self.name, self.original)
        return False


class _patched_env(object):
    """Set environment variables for a block and restore them afterwards.

    Restoring matters more than usual here: the code under test DELETES these
    variables by design, so a test that set one and did not clean up would
    leave the next test running in an environment it did not choose.
    """

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.previous = {k: os.environ.get(k) for k in self.values}
        os.environ.update(self.values)
        return self

    def __exit__(self, *exc):
        for key, was in self.previous.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was
        return False


def _read_source(relative_or_absolute):
    path = relative_or_absolute
    if not os.path.isabs(path):
        path = os.path.join(_REPO, path)
    with open(path, "r") as handle:
        return handle.read()


def _shipped_python_sources():
    """Every .py file that ships to a user's machine: helper/ and scripts/.

    Stated positively, like the lint gates: tests/ and docs/ are outside it, so
    this file can name the things it forbids without exempting itself.
    """
    found = []
    for directory in ("helper", "scripts"):
        root = os.path.join(_REPO, directory)
        if not os.path.isdir(root):
            continue
        for base, _dirs, files in os.walk(root):
            for name in sorted(files):
                if name.endswith(".py"):
                    found.append(os.path.join(base, name))
    assert found, "no shipped Python sources found; the scan is broken"
    return found


def _open_fd_count():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


if __name__ == "__main__":
    unittest.main(verbosity=2)
