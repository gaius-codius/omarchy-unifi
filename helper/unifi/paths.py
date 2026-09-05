"""SEC-002 / SEC-003: descriptor-safe validation and bounded reads.

Two rules shape every function here.

**Validate the object, not the name.** Every check is an `fstat` on a
descriptor that is already open, and every read is from that same descriptor.
Checking a path and then opening it is a TOCTOU window: between `os.stat` and
`open`, the name can be replaced with a symlink to something else. `O_NOFOLLOW`
closes the final-component case and `dir_fd` closes the directory-traversal
case, but neither helps if the check and the read look at different objects.

**Return bytes, never a handle.** `read_bounded` opens, validates, reads to a
bound, closes, and hands back `bytes`. A caller that wanted to re-read would
have to call again — which DATA-004c forbids mid-batch, and which is much
easier to notice in review than a second `.read()` on a file object that was
left lying around. Making the wrong thing impossible beats documenting it.
"""

import os
import stat

from . import errors

# SEC-003's three bounds. They are read bounds, not file-size expectations: a
# 64 KiB config.json is already absurd, and the point is that nothing
# unbounded is ever pulled into memory or handed to a parser.
CONFIG_MAX_BYTES = 64 * 1024
API_KEY_MAX_BYTES = 4 * 1024
COMMIT_MAX_BYTES = 8 * 1024

# SEC-006. A PEM bundle can legitimately be large; 1 MiB is far above any real
# CA file and far below anything that would matter to read.
CA_MAX_BYTES = 1024 * 1024

CONFIG_NAME = "config.json"
API_KEY_NAME = "api-key"
COMMIT_NAME = "commit.json"

# SEC-002 forbids group and other bits ENTIRELY on the three files, not merely
# the writable ones. Forbidding only writability would leave a 0644 api-key —
# world-READABLE — passing validation, which is the whole point of AC-060.
FILE_FORBIDDEN_BITS = 0o077

# Directories are held to the weaker rule: a 0755 directory is normal and
# harmless, because the files inside carry their own permissions.
DIR_FORBIDDEN_BITS = 0o022

_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _describe_mode(mode):
    return "0%o" % stat.S_IMODE(mode)


def open_config_dir(path):
    """Open the configuration directory and validate it by descriptor.

    Returns a directory file descriptor the caller must close. Raises
    ConfigError with an actionable message — the user's next step is always
    `scripts/configure`, never a retry.
    """
    flags = _OPEN_FLAGS | os.O_DIRECTORY
    try:
        dir_fd = os.open(path, flags)
    except FileNotFoundError:
        raise errors.ConfigError(
            "No configuration directory at %s; run scripts/configure." % path)
    except NotADirectoryError:
        raise errors.ConfigError("%s is not a directory." % path)
    except OSError as exc:
        # ELOOP here means the final component is a symlink, which O_NOFOLLOW
        # refused. SEC-002 requires a real directory.
        raise errors.ConfigError(
            "Cannot open %s (%s); it must be a real directory you own."
            % (path, exc.strerror))

    try:
        info = os.fstat(dir_fd)
        # Redundant with O_DIRECTORY, deliberately. O_DIRECTORY is what stops a
        # FIFO here — opening one read-only BLOCKS until a writer appears, and
        # we would never reach this line to find out — while SEC-003 asks for
        # the check to be made against the descriptor we actually hold. Each
        # alone rejects a regular file, so neither is individually observable
        # in a test while the other is present; they guard different failures.
        if not stat.S_ISDIR(info.st_mode):
            raise errors.ConfigError("%s is not a directory." % path)
        if info.st_uid != os.getuid():
            raise errors.ConfigError(
                "%s is owned by uid %d, not by you (uid %d)."
                % (path, info.st_uid, os.getuid()))
        if stat.S_IMODE(info.st_mode) & DIR_FORBIDDEN_BITS:
            raise errors.ConfigError(
                "%s is mode %s; it must not be group- or other-writable. "
                "Run: chmod 700 %s" % (path, _describe_mode(info.st_mode), path))
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def read_bounded(dir_fd, name, max_bytes):
    """Open `name` relative to `dir_fd`, validate it, read it once, close it.

    Returns the raw bytes. Nothing else escapes — no descriptor, no file
    object, no path — so DATA-004c's capture-once rule cannot be broken by
    accident later in the batch.
    """
    try:
        fd = os.open(name, _OPEN_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise errors.ConfigError(
            "%s is missing; run scripts/configure." % name)
    except OSError as exc:
        raise errors.ConfigError(
            "Cannot open %s (%s); it must be a regular file you own." % (name, exc.strerror))

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise errors.ConfigError("%s is not a regular file." % name)
        if info.st_uid != os.getuid():
            raise errors.ConfigError(
                "%s is owned by uid %d, not by you (uid %d)."
                % (name, info.st_uid, os.getuid()))
        if stat.S_IMODE(info.st_mode) & FILE_FORBIDDEN_BITS:
            raise errors.ConfigError(
                "%s is mode %s; it must have no group or other permissions at all. "
                "Run: chmod 600 %s" % (name, _describe_mode(info.st_mode), name))
        if info.st_size > max_bytes:
            raise errors.ConfigError(
                "%s is %d bytes; the limit is %d." % (name, info.st_size, max_bytes))

        # Read one byte past the bound rather than trusting st_size. The stat
        # and the read are the same object, but not the same instant, and a
        # file that grew in between would otherwise be silently truncated to
        # something that might still parse.
        data = _read_all(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise errors.ConfigError(
                "%s is larger than %d bytes." % (name, max_bytes))
        return data
    finally:
        os.close(fd)


def _read_all(fd, limit):
    chunks = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _is_untrusted_writable(info):
    """True when a DIRECTORY can be tampered with by someone other than its owner.

    The sticky bit is the exemption, and it is not a loophole. `/tmp` is mode
    1777 on every Linux system: world-writable, so anyone can create entries,
    but sticky, so only an entry's owner can rename or delete it. Without this,
    a CA under any temporary directory — including every directory this
    project's own tests build — would be rejected for a risk that does not
    exist, and the first person to hit it would be tempted to drop the parent
    walk entirely. Refusing something safe is how a check gets removed.
    """
    mode = stat.S_IMODE(info.st_mode)
    if not (mode & DIR_FORBIDDEN_BITS):
        return False
    return not (mode & stat.S_ISVTX)


def _parents(path):
    """Every ancestor directory of an absolute path, root first."""
    parts = os.path.normpath(path).split(os.sep)
    out = []
    for i in range(1, len(parts)):
        out.append(os.sep + os.path.join(*parts[1:i]) if i > 1 else os.sep)
    return out


def read_custom_ca(path):
    """SEC-006: read a configured CA bundle, by descriptor, with its own rules.

    The rules are deliberately NOT the ones in SEC-002. A CA bundle is public
    material, so group and other READ bits are fine and `/etc/ssl/...` owned by
    root is the normal case. What matters is that nobody untrusted can WRITE
    it, or write any directory on the way to it — a writable parent lets an
    attacker replace the file between one batch and the next.

    Returns the decoded PEM text. SEC-006 requires the *captured contents* to
    go to `load_verify_locations(cadata=...)`: a pathname that was validated
    and then handed back to the SSL library to reopen is a TOCTOU window with
    extra steps.
    """
    if not os.path.isabs(path):
        raise errors.TlsError("customCaPath must be an absolute path.")

    for parent in _parents(path):
        try:
            info = os.stat(parent)
        except OSError as exc:
            raise errors.TlsError(
                "Cannot inspect %s on the way to the CA file (%s)." % (parent, exc.strerror))
        if info.st_uid not in (0, os.getuid()):
            raise errors.TlsError(
                "%s is owned by uid %d, which is neither root nor you." % (parent, info.st_uid))
        if _is_untrusted_writable(info):
            raise errors.TlsError(
                "%s is mode %s and is group- or other-writable, so the CA file "
                "it leads to cannot be trusted." % (parent, _describe_mode(info.st_mode)))

    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError as exc:
        raise errors.TlsError("Cannot open the CA file (%s)." % exc.strerror)

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise errors.TlsError("The CA file is not a regular file.")
        if info.st_uid not in (0, os.getuid()):
            raise errors.TlsError(
                "The CA file is owned by uid %d, which is neither root nor you." % info.st_uid)
        if stat.S_IMODE(info.st_mode) & DIR_FORBIDDEN_BITS:
            raise errors.TlsError(
                "The CA file is mode %s; it must not be group- or other-writable."
                % _describe_mode(info.st_mode))
        if info.st_size > CA_MAX_BYTES:
            raise errors.TlsError(
                "The CA file is %d bytes; the limit is %d." % (info.st_size, CA_MAX_BYTES))

        data = _read_all(fd, CA_MAX_BYTES + 1)
        if len(data) > CA_MAX_BYTES:
            raise errors.TlsError("The CA file is larger than %d bytes." % CA_MAX_BYTES)
    finally:
        os.close(fd)

    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        raise errors.TlsError("The CA file is not ASCII PEM text.")
