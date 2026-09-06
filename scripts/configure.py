"""Write the committed configuration set, then ask the shell to reload.

DATA-004's commit protocol, in one place:

    write config.json    -> fsync -> rename -> fsync the directory
    write api-key        -> fsync -> rename -> fsync the directory
    write commit.json    -> fsync -> rename -> fsync the directory   <- the commit point

`commit.json` is last because it is what makes the other two *a set*. Until it
lands, the helper sees digests that do not match and reports `uncommitted`,
which is exactly right: a write interrupted between the first two renames leaves
one controller's URL beside another's key, and the helper must refuse to use
them rather than send one to the other.

`commitset.py` is IMPORTED, not reimplemented. The digests here and the digests
the helper verifies have to be over the same bytes in the same way (DATA-004b) —
and "the same way" is a property two copies of an algorithm do not have. Sharing
the module makes agreement structural.

The whole read-modify-commit holds an exclusive `flock` on the configuration
directory (DATA-004a). Two concurrent runs cannot interleave their renames and
strand a permanently mismatched set.

SEC-001 governs the credential's route through this program: it is read from a
file or from stdin, never from argv, because argv is world-readable in /proc.
"""

import argparse
import errno
import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# The same explicit bootstrap the helper uses, and for the same reason: `-E -s`
# discard PYTHONPATH and the user site directory on purpose.
sys.path.insert(0, os.path.join(_ROOT, "helper"))

from unifi import commitset            # noqa: E402
from unifi import config as config_module  # noqa: E402
from unifi import credential as credential_module  # noqa: E402
from unifi import errors               # noqa: E402
from unifi import paths                # noqa: E402
from unifi import routes               # noqa: E402

DEFAULT_CONFIG_DIR = "~/.config/omarchy-unifi"
LOCK_NAME = ".configure.lock"

DIR_MODE = 0o700
FILE_MODE = 0o600

IPC_TARGET = "gaius-codius.unifi"
IPC_METHOD = "reload"

EXIT_OK = 0
EXIT_FAILURE = 1

# DATA-010b, verbatim. Three of the seven outcomes exit 0, and `Target not
# found.` is one of them: per HC-3 the service does not exist until the widget
# is on the bar, so it is the EXPECTED outcome when a first-time user
# configures before adding the widget. Treating it as a failure would make
# correct setup report an error.
RELOAD_OUTCOMES = (
    ("", "applied",
     "Configuration applied; the running shell has reloaded it.", EXIT_OK),
    ("omarchy-shell is not running", "deferred-no-shell",
     "Configuration saved. No shell is running; it will be picked up at the "
     "next shell start.", EXIT_OK),
    ("Target not found.", "deferred-no-widget",
     "Configuration saved. The UniFi widget is not on the bar yet, so there is "
     "no service to reload; add it and the configuration will be used.", EXIT_OK),
    ("omarchy-shell is not responding", "ipc-timeout",
     "The shell did not answer within its IPC timeout. Try again once it is "
     "responsive.", EXIT_FAILURE),
    ("omarchy-shell is not ready", "shell-starting",
     "The shell is still starting. Re-run this command once it has finished.",
     EXIT_FAILURE),
    ("Function not found.", "method-missing",
     "The running service has no reload method. The installed plugin and the "
     "running shell may be different versions.", EXIT_FAILURE),
)

UNKNOWN_OUTCOME = ("unknown",
                   "The reload result could not be understood.", EXIT_FAILURE)


class ConfigureError(Exception):
    """A message for the user. Never carries credential material (SEC-001)."""


# ---------------------------------------------------------------------------
# The committed set
# ---------------------------------------------------------------------------

def ensure_directory(path):
    """Create the configuration directory 0700, or validate an existing one."""
    try:
        os.mkdir(path, DIR_MODE)
    except FileExistsError:
        pass
    except OSError as failure:
        raise ConfigureError("Could not create %s (%s)."
                             % (path, errno.errorcode.get(failure.errno, "error")))
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise ConfigureError("%s is a symlink; refusing to use it." % path)
    if not stat.S_ISDIR(info.st_mode):
        raise ConfigureError("%s exists and is not a directory." % path)
    if info.st_uid != os.getuid():
        raise ConfigureError("%s is owned by another user." % path)
    if stat.S_IMODE(info.st_mode) & paths.DIR_FORBIDDEN_BITS:
        # Tightened rather than refused: this script owns the directory, and a
        # refusal here would strand a user whose umask was wrong once.
        os.chmod(path, DIR_MODE)
    return path


def atomic_write(directory, name, data):
    """Replace one file atomically, and durably.

    fsync the file, rename it, then fsync the DIRECTORY. The last step is the
    one people leave out: without it the rename itself can be lost on a crash,
    and `commit.json` can land before the files it certifies.
    """
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".%s." % name)
    try:
        os.write(handle, data)
        os.fchmod(handle, FILE_MODE)
        os.fsync(handle)
    finally:
        os.close(handle)
    os.rename(temporary, os.path.join(directory, name))
    _fsync_directory(directory)


def _fsync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def commit(directory, config_bytes, api_key_bytes, generation):
    """Write all three files in DATA-004's order. `commit.json` is the commit."""
    atomic_write(directory, paths.CONFIG_NAME, config_bytes)
    atomic_write(directory, paths.API_KEY_NAME, api_key_bytes)
    marker = commitset.build_commit(generation, config_bytes, api_key_bytes)
    atomic_write(directory, paths.COMMIT_NAME,
                 (json.dumps(marker, sort_keys=True) + "\n").encode("utf-8"))


def read_existing(directory):
    """Return `(config_bytes, api_key_bytes, generation)` or `(None, None, 0)`.

    Read through the same descriptor-safe path the helper uses, so a directory
    this script would refuse to write is also one it refuses to read.
    """
    try:
        dir_fd = paths.open_config_dir(directory)
    except errors.HelperError:
        return None, None, 0
    try:
        config_raw = _maybe(dir_fd, paths.CONFIG_NAME, paths.CONFIG_MAX_BYTES)
        api_key_raw = _maybe(dir_fd, paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)
        commit_raw = _maybe(dir_fd, paths.COMMIT_NAME, paths.COMMIT_MAX_BYTES)
    finally:
        os.close(dir_fd)

    generation = 0
    if commit_raw is not None:
        try:
            marker = json.loads(commit_raw.decode("utf-8"))
            candidate = marker.get(commitset.GENERATION_KEY)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                generation = max(candidate, 0)
        except (UnicodeDecodeError, ValueError, AttributeError):
            generation = 0
    return config_raw, api_key_raw, generation


def _maybe(dir_fd, name, bound):
    try:
        return paths.read_bounded(dir_fd, name, bound)
    except errors.HelperError:
        return None


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def read_credential(args):
    """The API key, from a file or stdin. Never from argv (SEC-001).

    argv is world-readable through /proc on every running process, so a
    `--api-key <value>` option would publish the credential to every user on
    the machine for as long as this script ran.
    """
    if args.api_key_file:
        try:
            with open(args.api_key_file, "rb") as handle:
                raw = handle.read(paths.API_KEY_MAX_BYTES + 1)
        except OSError:
            raise ConfigureError("Could not read the API key file.")
    elif args.api_key_stdin:
        raw = sys.stdin.buffer.read(paths.API_KEY_MAX_BYTES + 1)
    else:
        return None

    if len(raw) > paths.API_KEY_MAX_BYTES:
        raise ConfigureError("The API key is larger than %d bytes."
                             % paths.API_KEY_MAX_BYTES)
    # Validated before it is written, so a key that could never be used is
    # rejected now rather than surfacing as `credential` on every poll. The
    # message describes the SHAPE and never quotes any part of the value.
    credential_module.parse(raw)
    # Stored with a single trailing newline: DATA-004b hashes the RAW bytes, so
    # the exact form written here is the exact form the helper verifies.
    return raw if raw.endswith(b"\n") else raw + b"\n"


def build_config(existing_bytes, args):
    """Merge the requested changes into the existing configuration."""
    body = {}
    if existing_bytes is not None:
        try:
            parsed = json.loads(existing_bytes.decode("utf-8"))
            if isinstance(parsed, dict):
                body = parsed
        except (UnicodeDecodeError, ValueError):
            body = {}

    if args.api_root is not None:
        body[config_module.API_ROOT] = args.api_root
    if args.site is not None:
        body[config_module.SITE_ID] = args.site
    if args.custom_ca is not None:
        body[config_module.CUSTOM_CA_PATH] = os.path.abspath(args.custom_ca)
    if args.no_custom_ca:
        body.pop(config_module.CUSTOM_CA_PATH, None)
    if args.allow_insecure_tls is not None:
        body[config_module.ALLOW_INSECURE_TLS] = args.allow_insecure_tls

    encoded = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")
    # Validated with the helper's own parser, so this script cannot write a file
    # the helper will refuse. `routes.build` is called too: `apiRoot` is checked
    # by the thing that will actually assemble the URL, not by a second opinion.
    parsed = config_module.parse(encoded)
    routes.build(parsed.api_root, "info")
    if parsed.site_id is not None and not routes.UUID_RE.match(parsed.site_id):
        raise ConfigureError("siteId must be a canonical UUID.")
    if parsed.custom_ca_path is not None:
        paths.read_custom_ca(parsed.custom_ca_path)
    return encoded


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

def reload_shell(runner=None):
    """`omarchy shell gaius-codius.unifi reload`, WITHOUT -q, classified on stderr.

    HC-5: the wrapper exits 1 for every failure mode and prints which one on
    stderr, so the exit status alone cannot tell "no shell running" from
    "target missing" — and DATA-010b treats those two very differently.
    """
    run = runner if runner is not None else _run_omarchy
    try:
        status, stderr = run()
    except FileNotFoundError:
        return ("no-omarchy",
                "The `omarchy` command was not found, so nothing could be "
                "reloaded.", EXIT_FAILURE)

    message = (stderr or "").strip()
    if status == 0 and not message:
        return RELOAD_OUTCOMES[0][1:]
    for expected, name, sentence, code in RELOAD_OUTCOMES:
        if expected and message == expected:
            return name, sentence, code
    return UNKNOWN_OUTCOME


def _run_omarchy():
    completed = subprocess.run(
        ["omarchy", "shell", IPC_TARGET, IPC_METHOD],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return completed.returncode, completed.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="scripts/configure",
        description="Configure the Omarchy UniFi plugin.",
        # No prefix matching. `--api-key` is not an option here, and with
        # abbreviation on it resolves to whichever `--api-key-*` option happens
        # to be unambiguous at the time — so what a typo means would depend on
        # which other options exist. For a flag one character away from a
        # credential, that is not a thing to leave to the option table's shape.
        allow_abbrev=False)
    parser.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--api-root",
                        help="https://<console>/proxy/network/integration")
    parser.add_argument("--site", help="the site UUID to display")
    parser.add_argument("--api-key-file", help="read the API key from this file")
    parser.add_argument("--api-key-stdin", action="store_true",
                        help="read the API key from standard input")
    parser.add_argument("--custom-ca", help="a PEM file to pin instead of system trust")
    parser.add_argument("--no-custom-ca", action="store_true",
                        help="stop pinning a custom certificate authority")
    parser.add_argument("--allow-insecure-tls", dest="allow_insecure_tls",
                        action="store_const", const=True, default=None,
                        help="DISABLE TLS verification (SEC-005; raises a "
                             "permanent warning in the panel)")
    parser.add_argument("--verify-tls", dest="allow_insecure_tls",
                        action="store_const", const=False,
                        help="re-enable TLS verification")
    parser.add_argument("--commit", action="store_true",
                        help="revalidate the files as they are on disk and "
                             "write a new commit marker")
    return parser.parse_args(argv)


def run(argv, runner=None, out=None):
    args = parse_args(argv)
    out = out if out is not None else sys.stdout
    directory = os.path.expanduser(args.config_dir)

    if args.commit and (args.api_root or args.site or args.api_key_file
                        or args.api_key_stdin or args.custom_ca
                        or args.no_custom_ca or args.allow_insecure_tls is not None):
        raise ConfigureError(
            "--commit revalidates the files as they are; it takes no other options.")

    ensure_directory(directory)
    lock_fd = _acquire_lock(directory)
    try:
        existing_config, existing_key, generation = read_existing(directory)

        if args.commit:
            if existing_config is None or existing_key is None:
                raise ConfigureError(
                    "There is nothing to commit: %s has no configuration yet."
                    % directory)
            # Revalidate exactly what is on disk, byte for byte. Not a rewrite:
            # DATA-004b hashes raw bytes, so reformatting the JSON here would
            # change the digest of a file the user hand-edited and hid whatever
            # they actually wrote.
            config_bytes = existing_config
            api_key_bytes = existing_key
            config_module.parse(config_bytes)
            credential_module.parse(api_key_bytes)
            parsed = config_module.parse(config_bytes)
            routes.build(parsed.api_root, "info")
        else:
            api_key_bytes = read_credential(args)
            if api_key_bytes is None:
                api_key_bytes = existing_key
            if api_key_bytes is None:
                raise ConfigureError(
                    "No API key. Pass --api-key-stdin or --api-key-file.")
            config_bytes = build_config(existing_config, args)

        previous = _snapshot(directory)
        commit(directory, config_bytes, api_key_bytes, generation + 1)

        name, sentence, code = reload_shell(runner)
        if code != EXIT_OK:
            # AC-010 / DATA-010b: every non-zero path leaves the three files
            # byte-identical to their pre-run state. The run is atomic across
            # its own exit status — a caller that treats non-zero as "this did
            # not happen" is then right, rather than left with a configuration
            # on disk that it believes was rejected.
            #
            # The argument the other way is real: DATA-011a means the next poll
            # would re-read the files and pick the change up anyway, so keeping
            # them would also converge. Atomicity wins because the exit status
            # is the only thing a script can act on.
            _restore(directory, previous)
            sentence = _NOT_SAVED + " " + sentence
    finally:
        os.close(lock_fd)

    out.write(sentence + "\n")
    if code != EXIT_OK:
        sys.stderr.write(sentence + "\n")
    return code, name


# Prefixed to the reload sentence on a rollback, so the message says what
# actually happened rather than what was attempted.
_NOT_SAVED = "The configuration was NOT changed."

_COMMITTED_NAMES = (paths.CONFIG_NAME, paths.API_KEY_NAME, paths.COMMIT_NAME)


def _snapshot(directory):
    """The three files' exact bytes, or None each where absent."""
    captured = {}
    for name in _COMMITTED_NAMES:
        path = os.path.join(directory, name)
        try:
            with open(path, "rb") as handle:
                captured[name] = handle.read(paths.CONFIG_MAX_BYTES + 1)
        except OSError:
            captured[name] = None
    return captured


def _restore(directory, captured):
    """Put the previous set back, in DATA-004's order.

    `commit.json` last on the way in AND on the way out: at no instant may a
    reader see a marker that certifies bytes which are no longer there.
    """
    for name in _COMMITTED_NAMES:
        data = captured.get(name)
        path = os.path.join(directory, name)
        if data is None:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        atomic_write(directory, name, data)


def _acquire_lock(directory):
    """DATA-004a: one exclusive lock, held across the whole read-modify-commit.

    A separate lock FILE rather than the directory itself, because a directory
    descriptor opened O_RDONLY cannot take an exclusive flock on every
    filesystem. The lock file is never read and never part of the committed set.
    """
    path = os.path.join(directory, LOCK_NAME)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                 FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        os.close(fd)
        raise ConfigureError("Could not lock %s." % directory)
    return fd


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        code, _name = run(argv)
        return code
    except ConfigureError as failure:
        sys.stderr.write(str(failure) + "\n")
        return EXIT_FAILURE
    except errors.HelperError as failure:
        # The helper's own validators, reused here so this script cannot write
        # something the helper will refuse. Their messages are already written
        # for a user and already governed by SEC-001.
        sys.stderr.write(failure.message + "\n")
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
