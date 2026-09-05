"""SEC-004: the API key, validated and then kept from leaking.

Two separate jobs, and the order between them is DATA-004b's, not a preference.

**Verify the raw bytes first, trim second.** The commit digest is computed over
the exact file bytes, so an `api-key` written as `sk-abc\\n` must hash as
`sk-abc\\n`. Trimming before hashing on either side produces a committed set
that can never validate — permanently, silently, and identically on every
retry. `commitset.verify` does the hashing; this module is called afterwards,
on bytes that have already been proven to belong to the committed set.

**Then make the value hard to emit by accident.** SEC-001 lists ten channels
the key must never reach, and most of them are reached by interpolation rather
than by intent: an f-string in a log line, a `repr()` in a traceback, a `%s` in
an exception a library raises. `Credential` redacts `__repr__`, `__str__` and
`__format__`, so every one of those produces a placeholder. The value leaves
through exactly one named method, which is greppable.
"""

from . import errors

# SEC-004. Visible ASCII only: 0x21 (!) through 0x7e (~). This excludes space
# (0x20) and DEL (0x7f) as well as every control byte, so the check is one
# range rather than a list of exclusions someone can forget to extend.
MIN_BYTE = 0x21
MAX_BYTE = 0x7E

MIN_LENGTH = 1
MAX_LENGTH = 4096

REDACTED = "<api-key redacted>"


class Credential(object):
    """A validated API key that does not print itself.

    Deliberately not a `str` subclass. A `str` subclass inherits `join`,
    `format`, `%`, concatenation and every other operation that would emit the
    value, and overriding three dunders would give a false sense of safety
    while `"key=" + cred` still worked.
    """

    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def header_value(self):
        """The single deliberate exit point, for the `X-API-Key` header.

        Named so that `grep -rn header_value` finds every place the credential
        is materialised. There should be exactly one, in the transport layer.
        """
        return self._value

    def __repr__(self):
        return REDACTED

    def __str__(self):
        return REDACTED

    def __format__(self, spec):
        # Without this, `"{:>20}".format(cred)` formats str(self) — which is
        # already redacted — but an explicit override keeps that true even if
        # __str__ is ever changed.
        return REDACTED

    def __len__(self):
        # Length is not secret and is occasionally worth asserting in a test.
        # It is not included in any error message: "your key is 39 characters"
        # narrows a brute-force search and tells the user nothing they can act
        # on.
        return len(self._value)

    def __eq__(self, other):
        if not isinstance(other, Credential):
            return NotImplemented
        return self._value == other._value

    def __hash__(self):
        return hash(self._value)


def parse(raw):
    """Trim ASCII whitespace from `raw` bytes, validate, and wrap.

    `raw` must already have been digest-verified (DATA-004b). Raises
    CredentialError, whose message describes the SHAPE of the problem and never
    quotes any part of the value — not even the offending byte's position,
    which for a short key would leak more than it helps.
    """
    if not isinstance(raw, bytes):
        raise errors.CredentialError("The API key was not read as bytes.")

    trimmed = raw.strip()

    if len(trimmed) < MIN_LENGTH:
        raise errors.CredentialError(
            "The api-key file is empty. Paste the key from the UniFi console "
            "into it, or re-run scripts/configure.")
    if len(trimmed) > MAX_LENGTH:
        raise errors.CredentialError(
            "The api-key file holds %d bytes after trimming; the limit is %d. "
            "It probably contains something other than a key."
            % (len(trimmed), MAX_LENGTH))

    for byte in bytearray(trimmed):
        if byte < MIN_BYTE or byte > MAX_BYTE:
            # No position, no byte value, no excerpt. SEC-004's purpose is that
            # nothing downstream — including a library exception raised while
            # building a header — can echo secret material, and an error
            # message that helpfully points at "byte 17" is a start on that.
            raise errors.CredentialError(
                "The api-key file contains a character that cannot appear in an "
                "API key: it must be a single line of visible ASCII with no "
                "spaces. Check for a stray newline in the middle, a smart quote, "
                "or a pasted line break.")

    return Credential(trimmed.decode("ascii"))
