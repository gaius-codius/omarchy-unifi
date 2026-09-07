"""SPEC-v1.1-browse.md DATA-B04: the size budget for a browsable envelope.

Until v1.1 the envelope was a fixed handful of kilobytes and the only size rule
that mattered was DATA-005's 256 KiB stdout bound, enforced in `envelope.encode`
as a **cliff**: one byte over and the entire envelope is replaced by an
`oversized_response` failure. The user gets no reading at all — not a shorter
list, nothing — and the panel greys.

`devices[]` and `clients[]` can approach that bound on a large site. Measured
against real hardware on 2026-09-07:

    device list record        ~305 B      client record          ~284 B
    device detail, 0 ports    ~630 B      device detail, 26 ports ~4.4 KB
    device statistics         ~300 B      ≈ 130 B per port

So a 200-device site with 48-port switches would clear 256 KiB on the device
detail alone. This module is what stops the cliff being reachable: assembly
stops at a LOWER threshold, having dropped the least important content first
and said so in a warning.

**The count caps are upper limits, not the operative bound.** Bytes bind first
and are meant to: a site of nine small devices gets detail for all nine, and a
site of two hundred switches gets it for as many as fit, broken ones first
(REQ-B02a). A pure count cap would have to be set for the worst imaginable site
and would then starve every ordinary one.

Nothing here decides WHICH records to drop. That is `collect.py`'s and
`normalize.py`'s job, in REQ-B11's order. This module owns the arithmetic and
one guarantee, stated precisely because a looser version of it was false:

**The browse lists cannot push an otherwise-fitting envelope over the cliff.**

Not "the envelope cannot reach the cliff" — that is not something this module
can promise. The health reading has its own bounds (64 gateways, 10 offline
devices, 32 warnings) and, with every string at `sanitize`'s 512-CHARACTER
limit and `ensure_ascii=True` spending six bytes per non-ASCII character, those
bounds admit content larger than 256 KiB on their own. `envelope.encode`'s
replacement path remains the backstop for that, and DATA-005's
character-versus-byte gap is why it has to.

What this module guarantees is narrower and is the part it controls: whatever
the fixed content costs, `devices[]` and `clients[]` are sized against what is
actually left after it.
"""

import json

# --- the byte budget ------------------------------------------------------
#
# `envelope.STDOUT_MAX_BYTES` is 256 KiB and is DATA-005, which lives in the
# frozen spec. It is deliberately NOT imported here: this module must not be
# able to raise its own ceiling by editing someone else's constant, and
# `test_unifi_status.py` asserts the two agree.
STDOUT_MAX_BYTES = 256 * 1024

# The guard rail, 32 KiB below the cliff. The headroom absorbs `json.dumps` key
# ordering and separator overhead across the whole document.
ENVELOPE_BUDGET_BYTES = 224 * 1024

# Reserved for the two things written AFTER the lists are sized: the warnings
# block, which the truncation itself adds to at the moment it can least afford
# one, and the envelope frame. 32 entries at DATA-005's 256-character message
# bound plus a small detail object is under 20 KiB; the frame — protocolVersion,
# ok, nonce, two timestamps, meta — is about 1 KiB.
WARNINGS_RESERVE_BYTES = 24 * 1024
FRAME_RESERVE_BYTES = 2 * 1024

# The ceiling when no fixed content has been measured. Used by `Budget()` with
# no argument and by tests; real assembly calls `room_for_lists`.
LIST_BUDGET_BYTES = (ENVELOPE_BUDGET_BYTES - WARNINGS_RESERVE_BYTES
                     - FRAME_RESERVE_BYTES)


def room_for_lists(fixed_content):
    """How many bytes `devices[]` and `clients[]` may have, given the rest.

    **Measured, not reserved.** An earlier version subtracted a constant
    32 KiB, described as "measured at ~25 KiB worst case", and was wrong by
    between five and thirty times: the worst case the protocol actually admits
    is 64 gateways, 10 offline devices and 32 warnings with every string at
    `sanitize`'s 512-CHARACTER bound, which is ~175 KiB of ASCII and — because
    `ensure_ascii=True` writes a non-ASCII character as six bytes of escape —
    approaching a megabyte of Cyrillic. Nothing tested the constant: setting it
    to zero left every test green.

    A subtraction cannot be right here, because the quantity varies by a factor
    of thirty with content the helper does not control. So the fixed content is
    encoded and measured, and the lists get what is actually left.

    Returns 0 rather than a negative number. A site whose health reading alone
    fills the envelope gets no browse lists, which is the correct trade and the
    one DATA-B04's assembly order already states.
    """
    room = (ENVELOPE_BUDGET_BYTES - fixed_content
            - WARNINGS_RESERVE_BYTES - FRAME_RESERVE_BYTES)
    return max(0, room)

# --- the count caps -------------------------------------------------------
#
# Upper limits. On any site large enough for these to bind, the byte budget has
# already bound first — which is the point.
DEVICES_LISTED_MAX = 200
CLIENTS_LISTED_MAX = 500
DEVICE_DETAIL_MAX = 40

# Per-device sub-bounds. A 48-port switch is the largest UniFi ships; 64 leaves
# room without leaving the arithmetic open-ended. Radios: 2.4, 5, 6 GHz and a
# scanning radio is four, so 8 is generous.
PORTS_PER_DEVICE_MAX = 64
RADIOS_PER_DEVICE_MAX = 8

# REQ-B02b. Detail collection is optional in BIZ-004's sense: it is attempted
# only while this much of the REQ-017 deadline remains, so it can never consume
# the budget the REQUIRED collections need. A device browser must not be able to
# break the health indicator.
DETAIL_RESERVE_SEC = 8

# The assembly order, most important first. Named here rather than left implicit
# in `collect.py`'s control flow, because "what gets dropped when it does not
# fit" is a product decision and belongs somewhere a reader can find it.
#
#   1. everything else          — the health reading itself, never dropped
#   2. devices[] base records   — identity, state, class; small and bounded
#   3. clients[]                — informational, but the whole point of a page
#   4. per-device detail        — the flexible one, and already ordered by
#                                 importance (REQ-B02a), so what survives is
#                                 the broken devices rather than the first ones
ASSEMBLY_ORDER = ("fixed", "devices", "clients", "detail")


def encoded_size(value):
    """Bytes this value contributes to the envelope, as `envelope.encode` writes it.

    The same `separators` and `ensure_ascii` as `envelope.encode`, because the
    defaults differ by a space after every separator and by three bytes per
    non-ASCII character — across a 500-record array either one is kilobytes.
    Getting this wrong in the optimistic direction would put the projection
    below the truth and let the cliff be reached anyway.
    """
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True).encode("utf-8"))


class Budget(object):
    """A running byte total with a ceiling.

    Deliberately not a context manager and not clever: callers ask `admits`
    before appending and `spend` after, so a record that does not fit is never
    added and then removed. Removing it afterwards would leave the arithmetic
    correct and the ORDER wrong — the dropped record would be the last one
    considered rather than the least important one.
    """

    __slots__ = ("limit", "used", "refused")

    def __init__(self, limit=None):
        self.limit = LIST_BUDGET_BYTES if limit is None else limit
        self.used = 0
        # The name of the first list the BUDGET refused, or None. It is what
        # separates "your site is larger than the cap" from "your reading did
        # not fit", which are different facts to a user with 300 devices: the
        # first is a product limit and the second is a transport one. Without
        # it `envelope_truncated` could only be guessed at from the other
        # warnings, and would fire identically for both.
        self.refused = None

    @property
    def remaining(self):
        return self.limit - self.used

    def admits(self, value, size=None, name=None):
        """Would this value still fit? `size` may be supplied if already known.

        `name` records WHICH list first hit the ceiling, and is recorded on
        refusal rather than on success so that a caller which probes and then
        gives up cannot overwrite an earlier, more informative answer.
        """
        cost = encoded_size(value) if size is None else size
        # +1 for the separating comma the array will need. A per-record byte
        # sounds like pedantry; across 500 clients it is half a kilobyte, and
        # the whole purpose of this class is that the projection is never
        # optimistic.
        fits = self.used + cost + 1 <= self.limit
        if not fits and name is not None and self.refused is None:
            self.refused = name
        return fits

    def spend(self, value, size=None):
        cost = encoded_size(value) if size is None else size
        self.used += cost + 1
        return cost


def bounded_list(records, cap, budget, warnings=None, code=None, total=None):
    """Take records in the order given, until either the cap or the budget stops it.

    Returns `(kept, dropped)`. `total` overrides `len(records)` in the warning's
    detail, because REQ-B01's total is carried independently and may exceed the
    array the caller already had in hand — the same rule REQ-010 applies to the
    offline list, for the same reason: a bound must never be able to understate
    the network.
    """
    kept = []
    for record in records:
        if len(kept) >= cap:
            break
        size = encoded_size(record)
        if not budget.admits(record, size, name=code_list_name(code)):
            break
        budget.spend(record, size)
        kept.append(record)
    true_total = len(records) if total is None else total
    dropped = true_total - len(kept)
    if dropped > 0 and warnings is not None and code is not None:
        warnings.add(code, {"listed": len(kept), "total": true_total})
    return kept, dropped


def code_list_name(code):
    """The array a truncation warning is about, for `Budget.refused`.

    A small mapping rather than string surgery on the code: `devices_truncated`
    happens to begin with the array's name and `device_detail_truncated` does
    not, and a rule derived from the first would silently mis-attribute the
    second.
    """
    return {"devices_truncated": "devices",
            "clients_truncated": "clients"}.get(code)
