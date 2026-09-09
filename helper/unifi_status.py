#!/usr/bin/env python3
"""The helper's entry point. One batch, one JSON object on stdout, one exit.

Launched as an argv vector, never composed shell text (SEC-001):

    python3 -B -E -s <this file> --nonce <nonce>

`-B` because a `__pycache__` write inside a staged plugin folder hot-reloads the
plugin mid-poll (HC-14). `-E` and `-s` because neither the environment nor the
user site directory may steer a process that reads an API key. **Not `-I`**
(HC-15): on 3.11+ isolated mode also drops the script's own directory from
`sys.path`, so the `unifi` package would not import. The bootstrap below inserts
that directory explicitly instead — stated rather than inherited, which is the
same reason `-E` is there in the first place.

Nothing in this file decides anything. It parses argv, arranges for a
well-formed envelope to exist whatever happens, and hands off. The one rule it
owns is the last-resort one: **every exit path writes exactly one envelope.** A
traceback on stdout would be a protocol violation, and a silent exit would leave
the service waiting for a stream that never carries a value.
"""

import os
import sys
import time

# The explicit bootstrap. See the module docstring: `-E -s` discard PYTHONPATH
# and the user site directory on purpose, so the path to this helper's own
# package is stated here rather than inherited from an environment that a
# credential-reading process must not trust.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unifi import collect                      # noqa: E402
from unifi import commitset                    # noqa: E402
from unifi import config as config_module      # noqa: E402
from unifi import credential as credential_module  # noqa: E402
from unifi import deadline as deadline_module  # noqa: E402
from unifi import envelope                     # noqa: E402
from unifi import errors                       # noqa: E402
from unifi import paths                        # noqa: E402
from unifi import tlsctx                       # noqa: E402
from unifi import warn                         # noqa: E402

NONCE_FLAG = "--nonce"
NONCE_MIN_CHARS = 1
NONCE_MAX_CHARS = 128

# Used only by the test suite, and deliberately an ARGUMENT rather than an
# environment variable. An env var is ambient: every child of a shell whose
# environment somebody has touched inherits it silently, which is the exact
# property SEC-007a spends `-E`, `-s` and the TLS scrub to remove from a process
# that reads an API key. An argument is explicit at the call site, and the
# service never passes it.
CONFIG_DIR_FLAG = "--config-dir"
DEFAULT_CONFIG_DIR = "~/.config/omarchy-unifi"


def default_config_dir():
    return os.path.expanduser(DEFAULT_CONFIG_DIR)


def utc_now():
    """RFC 3339 UTC with a `Z` suffix and no fractional part (DATA-008).

    `time.gmtime` rather than `datetime.now(timezone.utc).isoformat()`, which
    emits `+00:00` — a form DATA-008 rejects — and which the service would
    then reject as a malformed timestamp for a batch that worked.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_args(argv):
    """`--nonce <value>`, optionally `--config-dir <path>`, and nothing else.

    Returns `(nonce, config_dir)`. Unknown arguments are refused rather than
    ignored: the service builds this vector, so anything unexpected in it means
    the two sides disagree about the invocation, and guessing would hide that.
    """
    values = {NONCE_FLAG: None, CONFIG_DIR_FLAG: None}
    index = 0
    while index < len(argv):
        token = argv[index]
        flag, separator, inline = token.partition("=")
        if flag not in values:
            raise errors.InternalError(
                "The helper was given an argument it does not accept.")
        if separator:
            values[flag] = inline
            index += 1
            continue
        if index + 1 >= len(argv):
            raise errors.InternalError("An argument was given no value.")
        values[flag] = argv[index + 1]
        index += 2

    nonce = values[NONCE_FLAG]
    if nonce is None:
        raise errors.InternalError("The helper was launched without a nonce.")
    if not (NONCE_MIN_CHARS <= len(nonce) <= NONCE_MAX_CHARS):
        raise errors.InternalError("The nonce is not within its length bounds.")
    return nonce, values[CONFIG_DIR_FLAG] or default_config_dir()


def run(nonce, config_dir, attempted_at, connect=None):
    """One batch. Returns the envelope dict; never raises a HelperError.

    Ordered so that the two things that must happen before any traffic really do:
    the environment is scrubbed inside `tlsctx.build_context` (SEC-007a), and the
    committed set is verified before a request exists (DATA-004). A `uncommitted`
    result therefore cannot have sent anything — there is no code path between
    the digest check and the first `routes.build` that could.
    """
    warnings = warn.Warnings()
    meta = envelope.meta()
    config = None
    committed = None

    try:
        dir_fd = paths.open_config_dir(config_dir)
        try:
            # DATA-004c: read once, verify, and use the captured bytes for the
            # whole batch. `commitset.load` returns bytes and no descriptor, so
            # a mid-batch re-read is not expressible rather than merely
            # discouraged.
            committed = commitset.load(dir_fd)
        finally:
            os.close(dir_fd)

        config = config_module.parse(committed.config_bytes)
        credential = credential_module.parse(committed.api_key_bytes)

        ca_pem = None
        if config.custom_ca_path is not None:
            ca_pem = paths.read_custom_ca(config.custom_ca_path)
            warnings.add("custom_ca_in_use")
        if config.allow_insecure_tls:
            warnings.add("insecure_tls")

        meta = envelope.meta(config, committed,
                             custom_ca_in_use=ca_pem is not None)
        context = tlsctx.build_context(
            allow_insecure=config.allow_insecure_tls, ca_pem=ca_pem)

        budget = deadline_module.Deadline()
        kwargs = {}
        if connect is not None:
            kwargs["get_json"] = connect
        batch = collect.run(config, credential, context, budget, warnings,
                            **kwargs)
        return envelope.success(nonce, attempted_at, utc_now(), meta,
                                batch.data, warnings)

    except errors.HelperError as failure:
        if config is not None:
            meta = envelope.meta(config, committed)
        return envelope.failure(nonce, attempted_at, meta,
                                errors.to_error_object(failure), warnings)
    except Exception:  # noqa: BLE001
        # The last resort. An unexpected exception here is a bug in this helper,
        # and `internal` is what that means — but a traceback on stdout would be
        # a protocol violation, and an unhandled exit would leave the service
        # waiting for a stream that never carries a value. The exception object
        # is deliberately not formatted into the message (SEC-010, N-34).
        return envelope.failure(
            nonce, attempted_at, meta,
            errors.to_error_object(errors.InternalError(
                "The helper failed unexpectedly.")), warnings)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    attempted_at = utc_now()
    try:
        nonce, config_dir = parse_args(argv)
    except errors.HelperError as failure:
        # There is no nonce to echo, so the service will discard this envelope
        # on the nonce check (DATA-005a). It is still emitted as a well-formed
        # envelope rather than as a bare exit, because the alternative is a
        # stream the service waits on that never carries a value.
        text, status = envelope.encode(envelope.failure(
            "", attempted_at, envelope.meta(),
            errors.to_error_object(failure), None))
        sys.stdout.write(text)
        return status

    # `run` has a catch-all of its own, but it is not the last word: `run`
    # builds its failure envelope INSIDE an `except errors.HelperError` handler,
    # and an exception raised there — `errors.InconsistentError` was the live
    # one — propagates past the sibling `except Exception` entirely. Nothing
    # then stood between it and the interpreter, so the helper exited with a
    # traceback and an empty stdout, leaving the service waiting on a stream
    # that never carried a value.
    #
    # This is the backstop that makes "the helper always emits an envelope"
    # true rather than intended. It stays deliberately tiny: no config, no
    # meta beyond the default, and the exception object is not formatted into
    # the message (SEC-010).
    try:
        result = run(nonce, config_dir, attempted_at)
    except Exception:  # noqa: BLE001
        result = envelope.failure(
            nonce, attempted_at, envelope.meta(),
            errors.to_error_object(errors.InternalError(
                "The helper failed unexpectedly.")), None)
    text, status = envelope.encode(result)
    sys.stdout.write(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
