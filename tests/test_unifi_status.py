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

import io
import os
import shutil
import ssl
import stat
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

# The explicit bootstrap. `-E -s` discards PYTHONPATH and the user site
# directory, which is deliberate — the helper must not be steerable through the
# environment — so the path to the package is stated here rather than inherited.
sys.path.insert(0, os.path.join(_REPO, "helper"))
sys.path.insert(0, os.path.join(_HERE, "tools"))

import tls_stub  # noqa: E402
from unifi import commitset, credential, errors, paths, tlsctx  # noqa: E402


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

    def commit(self, config=b'{"apiRoot":"https://192.168.1.1"}', key=b"sk-abc\n",
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
        config = b'{"apiRoot":"https://192.168.1.1"}'
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
        config = b'{"apiRoot":"https://192.168.1.1"}'
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
                   "collections", "datetime", "unicodedata", "subprocess"}
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
