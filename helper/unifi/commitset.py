"""DATA-004: the three configuration files as one committed set.

`scripts/configure` writes `config.json` and `api-key`, fsyncs them and their
directory, and only then atomically replaces `commit.json`. That last rename is
the commit point: a set is valid exactly when the digests in `commit.json` match
the bytes on disk.

This module is imported by BOTH the helper and `scripts/configure`, which is the
whole design. DATA-004b's failure mode — one side hashing `sk-abc\\n` and the
other hashing `sk-abc` — is not a bug either side can find by testing itself.
It produces a set that never validates, on every run, forever, with a correct
message about a mismatch that is real. The only defence is that one piece of
code computes both, so "agree on the rules" is not something anyone has to
remember.
"""

import hashlib
import json

from . import errors, paths

# The commit marker's own shape. Not part of the wire protocol — it never
# leaves the machine — but `scripts/configure` and the helper must agree, which
# is why the key names live here and not in either caller.
GENERATION_KEY = "commitGeneration"
CONFIG_DIGEST_KEY = "configSha256"
API_KEY_DIGEST_KEY = "apiKeySha256"


def digest(raw):
    """SHA-256 over the EXACT raw file bytes, before any trimming (DATA-004b)."""
    if not isinstance(raw, bytes):
        raise TypeError("digest() takes raw bytes; trimming happens later")
    return hashlib.sha256(raw).hexdigest()


def build_commit(generation, config_bytes, api_key_bytes):
    """The commit marker `scripts/configure` writes, from the same code that reads it."""
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise ValueError("commitGeneration must be a non-negative integer")
    return {
        GENERATION_KEY: generation,
        CONFIG_DIGEST_KEY: digest(config_bytes),
        API_KEY_DIGEST_KEY: digest(api_key_bytes),
    }


class CommittedSet(object):
    """The captured bytes of one verified commit.

    Frozen, and holding BYTES rather than a directory descriptor or three
    paths. DATA-004c requires the batch to use these captured bytes for every
    request: a batch is many HTTP calls across several paginated collections,
    and re-reading between page 1 and page 2 of `/clients` while
    `scripts/configure` is replacing the files would send controller A's URL
    with controller B's key. Descriptor-safe opening does not close that
    window — having nothing left to re-open does.
    """

    __slots__ = ("generation", "config_bytes", "api_key_bytes")

    def __init__(self, generation, config_bytes, api_key_bytes):
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "config_bytes", config_bytes)
        object.__setattr__(self, "api_key_bytes", api_key_bytes)

    def __setattr__(self, name, value):
        raise AttributeError("CommittedSet is frozen; re-read is not a thing it does")

    def __delattr__(self, name):
        raise AttributeError("CommittedSet is frozen")

    def __repr__(self):
        # Never the bytes. config.json holds the controller URL and api-key
        # holds the credential; a repr of either could reach stderr.
        return "<CommittedSet generation=%d config=%dB api-key=%dB>" % (
            self.generation, len(self.config_bytes), len(self.api_key_bytes))


def load(dir_fd):
    """Read all three files once through `dir_fd`, verify both digests, capture.

    Returns a frozen CommittedSet. Raises UncommittedError when the set does not
    hang together — which is a different thing from ConfigError: the files are
    readable and well-formed, but a write was interrupted between the two
    replacements, so what is on disk is a mixture of two configurations.
    """
    commit_raw = paths.read_bounded(dir_fd, paths.COMMIT_NAME, paths.COMMIT_MAX_BYTES)
    config_raw = paths.read_bounded(dir_fd, paths.CONFIG_NAME, paths.CONFIG_MAX_BYTES)
    api_key_raw = paths.read_bounded(dir_fd, paths.API_KEY_NAME, paths.API_KEY_MAX_BYTES)

    try:
        commit = json.loads(commit_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise errors.UncommittedError(
            "commit.json is not readable JSON; re-run scripts/configure.")

    if not isinstance(commit, dict):
        raise errors.UncommittedError("commit.json is not a JSON object.")

    generation = commit.get(GENERATION_KEY)
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise errors.UncommittedError(
            "commit.json has no usable %s; re-run scripts/configure." % GENERATION_KEY)

    for key, raw, label in (
        (CONFIG_DIGEST_KEY, config_raw, paths.CONFIG_NAME),
        (API_KEY_DIGEST_KEY, api_key_raw, paths.API_KEY_NAME),
    ):
        recorded = commit.get(key)
        if not isinstance(recorded, str):
            raise errors.UncommittedError(
                "commit.json records no digest for %s; re-run scripts/configure." % label)
        if not _digests_equal(recorded, digest(raw)):
            raise errors.UncommittedError(
                "%s does not match the committed set. A configuration change was "
                "interrupted; re-run scripts/configure." % label)

    return CommittedSet(generation, config_raw, api_key_raw)


def _digests_equal(recorded, computed):
    """Constant-time comparison.

    Not because a digest is secret — it is not — but because `hmac.compare_digest`
    costs nothing here and the habit is worth more than the reasoning about
    whether this particular comparison is exploitable.
    """
    import hmac
    return hmac.compare_digest(str(recorded).strip().lower(), computed)
