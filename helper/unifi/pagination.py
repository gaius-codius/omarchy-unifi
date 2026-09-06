"""DATA-009 / DATA-009a / DATA-009b: collect a paginated collection, or say why not.

**This module does not know whether the collection is required.** BIZ-004 makes
`/devices` fail the batch and `/clients` degrade to a null count with a warning;
if that policy lived here, every invariant would have to be written twice or
carry a flag, and the flag would eventually be wrong for one of them. So
`collect` returns a `CollectResult` describing what happened, and `collect.py`
(Phase 7) applies the policy. The plan states this as a checkpoint criterion
because it is the kind of separation that erodes the first time a caller finds
it inconvenient.

`fetch_page(offset, limit)` is injected for the same reason: the invariants are
about page *shape* and page *sequence*, not about HTTP, so they are testable
against a list of fixture pages with no socket anywhere.

The two termination signals both matter, and each one alone is a bug:

* a SHORT page (`count < limit`) is the controller saying "that is all";
* accumulating `totalCount` unique records is the reader knowing it has them.

A reader that stops only on a short page never terminates on a collection whose
last page is exactly full — the `accept_boundary_200` fixture, which is a real
shape, 200 records at the API's maximum limit. A reader that stops only on the
count can never notice that the controller ended the collection early, which is
what `terminal_completeness` is.
"""

from . import errors

# docs/protocol-v1.md, "Pagination (helper-side, during collection)". Every one
# of these is asserted against that table by tests/model/consistency.test.js, so
# the document and this module cannot drift apart silently.
PAGE_LIMIT = 200
MAX_PAGES = 64
MAX_DECODED_BYTES = 8 * 1024 * 1024
MAX_BATCH_DECODED_BYTES = 16 * 1024 * 1024
TOTAL_COUNT_MIN = 0
TOTAL_COUNT_MAX = 1000000

PAGE_KEYS = ("offset", "limit", "count", "totalCount", "data")

# The thirteen invariant ids, in docs/protocol-v1.md's order. Named here so a
# violation reports the id the fixture corpus and the acceptance criteria use,
# rather than prose that has to be matched by eye.
OFFSET_MATCHES_REQUEST = "offset_matches_request"
COUNT_EQUALS_DATA_LENGTH = "count_equals_data_length"
COUNT_WITHIN_LIMIT = "count_within_limit"
NON_TERMINAL_PAGE_PROGRESSES = "non_terminal_page_progresses"
RECORD_IDS_UNIQUE = "record_ids_unique"
TOTAL_COUNT_NON_NEGATIVE = "total_count_non_negative"
TOTAL_COUNT_STABLE = "total_count_stable"
ADVANCE_BY_VALIDATED_COUNT = "advance_by_validated_count"
MAX_PAGES_ENFORCED = "max_pages_enforced"
MAX_DECODED_BYTES_ENFORCED = "max_decoded_bytes_enforced"
EMPTY_IS_VALID_NOT_PREMATURE = "empty_is_valid_not_premature"
REREAD_PAGE_ZERO_MATCHES = "reread_page_zero_matches"
TERMINAL_COMPLETENESS = "terminal_completeness"

INVARIANTS = (
    OFFSET_MATCHES_REQUEST,
    COUNT_EQUALS_DATA_LENGTH,
    COUNT_WITHIN_LIMIT,
    NON_TERMINAL_PAGE_PROGRESSES,
    RECORD_IDS_UNIQUE,
    TOTAL_COUNT_NON_NEGATIVE,
    TOTAL_COUNT_STABLE,
    ADVANCE_BY_VALIDATED_COUNT,
    MAX_PAGES_ENFORCED,
    MAX_DECODED_BYTES_ENFORCED,
    EMPTY_IS_VALID_NOT_PREMATURE,
    REREAD_PAGE_ZERO_MATCHES,
    TERMINAL_COMPLETENESS,
)

# The invariants that are shape violations rather than bound violations. Both
# fail a collection; only the second group is a "the controller is too big to
# read" condition, which BIZ-004's required side reports as oversized rather
# than partial.
BOUND_INVARIANTS = (MAX_PAGES_ENFORCED, MAX_DECODED_BYTES_ENFORCED)


class ByteBudget(object):
    """The 16 MiB per-batch decoded bound, shared across every collection.

    Per-collection bounds alone leave a batch of six collections able to read
    48 MiB. This is passed between them so the total is what is bounded.
    """

    __slots__ = ("limit", "used")

    def __init__(self, limit=MAX_BATCH_DECODED_BYTES):
        self.limit = limit
        self.used = 0

    def spend(self, count):
        self.used += count
        return self.used <= self.limit


class CollectResult(object):
    """What a collection attempt produced. Deliberately not a verdict."""

    __slots__ = ("records", "total_count", "complete", "invariant", "error",
                 "pages_read", "decoded_bytes", "reread_retried")

    def __init__(self, records, total_count, complete, invariant=None,
                 error=None, pages_read=0, decoded_bytes=0,
                 reread_retried=False):
        self.records = records
        self.total_count = total_count
        self.complete = complete
        self.invariant = invariant
        self.error = error
        self.pages_read = pages_read
        self.decoded_bytes = decoded_bytes
        self.reread_retried = reread_retried

    def __repr__(self):
        return ("<CollectResult complete=%r records=%d invariant=%r>"
                % (self.complete, len(self.records), self.invariant))


class _Rejected(Exception):
    """Internal: an invariant failed. Carries the id, never escapes this module."""

    def __init__(self, invariant):
        super(_Rejected, self).__init__(invariant)
        self.invariant = invariant


def collect(fetch_page, limit=PAGE_LIMIT, budget=None, warnings=None,
            max_pages=MAX_PAGES, max_bytes=MAX_DECODED_BYTES):
    """Read one paginated collection to completion, validating every page.

    Returns a `CollectResult`. A transport failure from `fetch_page` is captured
    on the result rather than raised, so that the caller — which is the only
    thing that knows whether this collection is required — decides between
    failing the batch with the transport's own kind and degrading to a warning.
    """
    attempt = 0
    while True:
        try:
            return _one_pass(fetch_page, limit, budget, warnings, max_pages,
                             max_bytes, retried=attempt > 0)
        except _Rejected as rejected:
            if (rejected.invariant == REREAD_PAGE_ZERO_MATCHES and attempt == 0):
                # DATA-009a: one retry from offset zero. The mismatch means the
                # collection changed underneath the read, which is ordinary on a
                # busy controller; a second mismatch is not.
                if warnings is not None:
                    warnings.add("page_reread_mismatch")
                attempt += 1
                continue
            return CollectResult([], None, False, invariant=rejected.invariant,
                                 reread_retried=attempt > 0)
        except errors.HelperError as failure:
            return CollectResult([], None, False, error=failure,
                                 reread_retried=attempt > 0)


def _one_pass(fetch_page, limit, budget, warnings, max_pages, max_bytes, retried):
    offset = 0
    pages_read = 0
    decoded = 0
    total_count = None
    first_page_ids = None
    seen = set()
    records = []

    while True:
        if pages_read >= max_pages:
            raise _Rejected(MAX_PAGES_ENFORCED)

        page, page_bytes = _fetch(fetch_page, offset, limit)
        pages_read += 1
        decoded += page_bytes
        if decoded > max_bytes:
            raise _Rejected(MAX_DECODED_BYTES_ENFORCED)
        if budget is not None and not budget.spend(page_bytes):
            raise errors.OversizedResponseError(
                "The controller returned more data than one batch may read.")

        count, page_total, data = _validate_page(page, offset, limit)

        if total_count is None:
            total_count = page_total
        elif page_total != total_count:
            raise _Rejected(TOTAL_COUNT_STABLE)

        for record in data:
            identifier = _record_id(record)
            if identifier in seen:
                raise _Rejected(RECORD_IDS_UNIQUE)
            seen.add(identifier)
            records.append(record)

        if count == 0:
            if offset == 0 and total_count == 0:
                # DATA-009b's complete empty collection: the empty site.
                break
            # Two ids for one condition, split by position. DATA-009b states the
            # rule about page zero specifically, and `non_terminal_page_progresses`
            # states it about a page in the middle; each has its own fixture, and
            # a single id would leave one of them unable to name what caught it.
            raise _Rejected(EMPTY_IS_VALID_NOT_PREMATURE if offset == 0
                            else NON_TERMINAL_PAGE_PROGRESSES)

        if first_page_ids is None:
            first_page_ids = set(_record_id(record) for record in data)

        # DATA-009: advance by the VALIDATED count. Never by the echoed limit,
        # never by len(data) before it was checked against `count`.
        offset += count

        if count < limit or len(seen) >= total_count:
            break

    if first_page_ids is None:
        first_page_ids = set()

    if not _reread_matches(fetch_page, limit, first_page_ids, total_count):
        raise _Rejected(REREAD_PAGE_ZERO_MATCHES)

    if len(seen) != total_count:
        raise _Rejected(TERMINAL_COMPLETENESS)

    return CollectResult(records, total_count, True, pages_read=pages_read,
                         decoded_bytes=decoded, reread_retried=retried)


def _reread_matches(fetch_page, limit, first_page_ids, total_count):
    """DATA-009a: re-request page 0 and compare its id set and totalCount.

    The only check in the contract that detects offset drift at constant
    cardinality — one record deleted and another appended mid-read, which leaves
    every other invariant satisfied while one record is silently skipped.
    """
    page, _bytes = _fetch(fetch_page, 0, limit)
    count, page_total, data = _validate_page(page, 0, limit)
    if page_total != total_count:
        return False
    return set(_record_id(record) for record in data) == first_page_ids


def _fetch(fetch_page, offset, limit):
    result = fetch_page(offset, limit)
    if isinstance(result, tuple):
        page, page_bytes = result
    else:
        page, page_bytes = result, 0
    return page, page_bytes


def _validate_page(page, requested_offset, requested_limit):
    """Every per-page invariant, in a fixed order, returning the validated parts."""
    if not isinstance(page, dict):
        raise errors.MalformedResponseError("A page was not a JSON object.")
    for key in PAGE_KEYS:
        if key not in page:
            raise errors.MalformedResponseError("A page was missing a required field.")

    offset = _integer(page["offset"])
    limit = _integer(page["limit"])
    count = _integer(page["count"])
    total = _integer(page["totalCount"])
    data = page["data"]
    if offset is None or limit is None or count is None or total is None:
        raise errors.MalformedResponseError("A page field was not an integer.")
    if not isinstance(data, list):
        raise errors.MalformedResponseError("A page's data was not an array.")

    if offset != requested_offset:
        raise _Rejected(OFFSET_MATCHES_REQUEST)
    if count != len(data):
        raise _Rejected(COUNT_EQUALS_DATA_LENGTH)
    # The echoed limit must be the limit that was asked for. This is the page
    # half of `advance_by_validated_count`: a reader that trusts the echoed
    # limit for its arithmetic skips records, so a page that contradicts the
    # request is refused before any arithmetic happens at all.
    if limit != requested_limit:
        raise _Rejected(ADVANCE_BY_VALIDATED_COUNT)
    if not (0 <= count <= requested_limit):
        raise _Rejected(COUNT_WITHIN_LIMIT)
    if total < TOTAL_COUNT_MIN:
        raise _Rejected(TOTAL_COUNT_NON_NEGATIVE)
    if total > TOTAL_COUNT_MAX:
        raise errors.OversizedResponseError(
            "The controller reported more records than the helper will read.")
    return count, total, data


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _record_id(record):
    if not isinstance(record, dict):
        raise errors.MalformedResponseError("A record was not a JSON object.")
    identifier = record.get("id")
    if not isinstance(identifier, str) or not identifier:
        # DATA-009 requires a stable id per record; without one, uniqueness and
        # therefore completeness are unknowable.
        raise _Rejected(RECORD_IDS_UNIQUE)
    return identifier
