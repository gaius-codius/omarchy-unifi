"""REQ-017 / REQ-017c: the helper's own 25 s budget across a whole batch.

Two facts define what this can and cannot do.

**It is checked between operations, not during one.** REQ-017c is explicit:
`getaddrinfo` is not interruptible from Python, and no timeout argument bounds
it. So a batch can overrun this budget inside a single blocking resolve, and the
service's 30 s watchdog — a different process, a different clock — is the outer
bound. This class is the inner, best-effort one, and saying so here is more
useful than a comment claiming a guarantee it cannot make.

**A per-operation timeout is not a budget.** A socket timeout resets on every
byte received, so a controller trickling one byte per second holds a connection
open indefinitely while never tripping its read timeout. That is why every
operation takes `timeout_for()` — the remaining budget, capped — and why
`check()` is called between operations rather than trusting the socket.

The clock is monotonic and injectable. Wall-clock time is the wrong axis here
for the same reason it is in `Schedule.js`: an NTP correction mid-batch must not
grant or revoke time.
"""

import time

from . import errors

# REQ-017's number.
BUDGET_SEC = 25.0

# [chosen] No single connect-or-read may claim the entire remaining budget.
# Without this a first request that hangs consumes all 25 s and the batch fails
# with nothing attempted; with it, a wedged first route still leaves time for
# the others to succeed or to fail informatively.
PER_OPERATION_MAX_SEC = 10.0

# [chosen] Below this there is no point starting an operation: a TLS handshake
# will not complete in it, so the only outcome is a socket timeout reported as
# `timeout` a moment later. Failing here instead keeps the reported kind honest
# about which clock ran out.
MIN_OPERATION_SEC = 0.25


class Deadline(object):
    """An absolute budget with a monotonic origin."""

    __slots__ = ("_clock", "_started", "_budget")

    def __init__(self, budget_sec=BUDGET_SEC, clock=None):
        self._clock = clock if clock is not None else time.monotonic
        self._budget = float(budget_sec)
        self._started = self._clock()

    def elapsed(self):
        return self._clock() - self._started

    def remaining(self):
        return self._budget - self.elapsed()

    def expired(self):
        return self.remaining() <= 0.0

    def check(self, what):
        """Raise if the budget is gone. `what` names the step, for the detail."""
        if self.expired():
            raise errors.DeadlineError(
                "The controller did not respond within the time budget.",
                detail={"step": what, "budgetSec": self._budget})

    def timeout_for(self, what):
        """The timeout to hand one socket operation.

        Never larger than what is left (REQ-017's "no greater than the remaining
        budget"), never larger than the per-operation cap, and never so small
        that the operation is doomed before it starts.
        """
        self.check(what)
        remaining = self.remaining()
        if remaining < MIN_OPERATION_SEC:
            raise errors.DeadlineError(
                "Too little time remained in the budget to start a request.",
                detail={"step": what, "remainingSec": round(remaining, 3)})
        return min(remaining, PER_OPERATION_MAX_SEC)
