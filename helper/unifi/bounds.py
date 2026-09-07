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
one guarantee: **the assembled envelope cannot reach `envelope.encode`'s
replacement path.**
"""

import json

# --- the byte budget ------------------------------------------------------
#
# `envelope.STDOUT_MAX_BYTES` is 256 KiB and is DATA-005, which lives in the
# frozen spec. It is deliberately NOT imported here: this module must not be
# able to raise its own ceiling by editing someone else's constant, and
# `test_unifi_status.py` asserts the two agree.
STDOUT_MAX_BYTES = 256 * 1024

# The guard rail, 32 KiB below the cliff. The headroom absorbs three things the
# projection cannot see exactly: `json.dumps` key ordering and separator
# overhead across the whole document, the `\uXXXX` expansion of non-ASCII names
# under `ensure_ascii=True` (a Cyrillic device name is 6 bytes per character in
# the output and 2 in the input), and warnings appended AFTER assembly by the
# truncation itself — a truncating envelope grows a warning at the moment it can
# least afford one.
ENVELOPE_BUDGET_BYTES = 224 * 1024

# Reserved for everything that is not `devices[]` or `clients[]`: site, wan,
# gateways (64 max), offlineDevices (10 max), counts, warnings (32 max), meta
# and the envelope frame. Measured at ~25 KiB worst case; rounded up.
FIXED_CONTENT_RESERVE_BYTES = 32 * 1024

LIST_BUDGET_BYTES = ENVELOPE_BUDGET_BYTES - FIXED_CONTENT_RESERVE_BYTES

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

    __slots__ = ("limit", "used")

    def __init__(self, limit=None):
        self.limit = LIST_BUDGET_BYTES if limit is None else limit
        self.used = 0

    @property
    def remaining(self):
        return self.limit - self.used

    def admits(self, value, size=None):
        """Would this value still fit? `size` may be supplied if already known."""
        cost = encoded_size(value) if size is None else size
        # +1 for the separating comma the array will need. A per-record byte
        # sounds like pedantry; across 500 clients it is half a kilobyte, and
        # the whole purpose of this class is that the projection is never
        # optimistic.
        return self.used + cost + 1 <= self.limit

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
        if not budget.admits(record, size):
            break
        budget.spend(record, size)
        kept.append(record)
    true_total = len(records) if total is None else total
    dropped = true_total - len(kept)
    if dropped > 0 and warnings is not None and code is not None:
        warnings.add(code, {"listed": len(kept), "total": true_total})
    return kept, dropped
