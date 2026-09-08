#!/usr/bin/env python3
"""The AC-B20 tool's own guards — AUTO, and no controller is contacted.

    python3 -B -E -s tests/test_live_batch.py

`tests/tools/live_batch.py` is the one tool in the tree that sends a packet off
this machine, and the one that handles a real site's device and client lists.
Two properties of it therefore need an executable check rather than a careful
reading, and neither of them needs the controller:

  1. It refuses without G-CONTROLLER approval. Tested by running it as a
     subprocess with a clean environment and asserting exit 2 — the same shape
     `tests/run.sh` uses for `--live-staged`.

  2. Its REQ-B20 leak guard is not vacuous. The guard only ever runs against a
     live envelope, so on every ordinary run it passes trivially; a version of
     it that could never fail would look exactly the same. These cases seed the
     leak.

The summariser is checked against the committed corpus, which is where the
shape it reads is frozen (SEC-011: no real controller data in fixtures).
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import live_batch                          # noqa: E402

TOOL = os.path.join(HERE, "tools", "live_batch.py")
ACCEPT = os.path.join(HERE, "fixtures", "envelopes", "accept")


def load(name):
    with open(os.path.join(ACCEPT, name), encoding="utf-8") as handle:
        return json.load(handle)


class GateTest(unittest.TestCase):
    def test_refuses_without_controller_approval(self):
        """G-CONTROLLER. Exit 2, nothing on stdout, and the gate named."""
        env = dict(os.environ)
        env.pop(live_batch.APPROVAL_ENV, None)
        proc = subprocess.run(
            [sys.executable, "-B", "-E", "-s", TOOL],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=REPO)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"G-CONTROLLER", proc.stderr)
        self.assertIn(b"Nothing was sent", proc.stderr)

    def test_a_value_other_than_1_does_not_approve(self):
        """`approved()` is an equality test, not a truthiness one.

        `OMARCHY_UNIFI_CONTROLLER_APPROVED=0` reading as approval is the
        classic form of this bug, and a gate that opens on "no" is worse than
        no gate at all.
        """
        for value in ("0", "", "false", "no", "yes", "true", "11", " 1"):
            os.environ[live_batch.APPROVAL_ENV] = value
            try:
                self.assertFalse(live_batch.approved(), value)
            finally:
                os.environ.pop(live_batch.APPROVAL_ENV, None)
        os.environ[live_batch.APPROVAL_ENV] = "1"
        try:
            self.assertTrue(live_batch.approved())
        finally:
            os.environ.pop(live_batch.APPROVAL_ENV, None)


class LeakGuardTest(unittest.TestCase):
    """REQ-B20: names, IPs and MACs reach the panel and nowhere else."""

    ENVELOPE = {
        "data": {
            "clients": [{"name": "Kitchen Tablet", "ipAddress": "192.0.2.9",
                         "macAddress": "02:00:00:aa:bb:cc"}],
            "devices": [{"name": "loft-ap", "ipAddress": "192.0.2.4",
                         "macAddress": "02:00:00:dd:ee:ff"}],
            "site": {"name": "Nettleford House"},
        }
    }

    def test_collects_every_personal_field_from_every_list(self):
        found = live_batch.personal_strings(self.ENVELOPE)
        self.assertEqual(found, {
            "Kitchen Tablet", "192.0.2.9", "02:00:00:aa:bb:cc",
            "loft-ap", "192.0.2.4", "02:00:00:dd:ee:ff",
            "Nettleford House",
        })

    def test_a_clean_report_is_printed(self):
        live_batch._assert_no_personal_data(
            "clients listed 44", live_batch.personal_strings(self.ENVELOPE))

    def test_each_personal_value_is_caught_on_its_own(self):
        """One case per value, so a guard that catches only names still fails."""
        for value in live_batch.personal_strings(self.ENVELOPE):
            with self.assertRaises(SystemExit):
                live_batch._assert_no_personal_data(
                    "  warnings: none\n  busiest: " + value,
                    live_batch.personal_strings(self.ENVELOPE))

    def test_the_match_is_case_insensitive(self):
        """A MAC rendered upper-case by some future diagnostic is still a MAC."""
        with self.assertRaises(SystemExit):
            live_batch._assert_no_personal_data(
                "bound to 02:00:00:AA:BB:CC",
                live_batch.personal_strings(self.ENVELOPE))

    def test_the_refusal_does_not_repeat_the_leaked_value(self):
        """An assertion that reports a leak by quoting it has leaked it."""
        with self.assertRaises(SystemExit) as caught:
            live_batch._assert_no_personal_data(
                "client Kitchen Tablet",
                live_batch.personal_strings(self.ENVELOPE))
        message = str(caught.exception)
        self.assertNotIn("Kitchen", message)
        self.assertIn("1 value(s)", message)

    def test_an_empty_name_is_not_treated_as_a_secret(self):
        """`""` appears in every string, so a blank name would fail every run."""
        envelope = {"data": {"clients": [{"name": "", "ipAddress": None,
                                          "macAddress": ""}]}}
        self.assertEqual(live_batch.personal_strings(envelope), set())
        live_batch._assert_no_personal_data(
            "anything at all", live_batch.personal_strings(envelope))


class MainArmsTheGuardFromEveryRunTest(unittest.TestCase):
    """The guard is built from all three runs, not from the last one.

    `run_batch` is replaced, so no controller is contacted and no packet is
    sent — what is under test is the bookkeeping in `main`, not the helper.

    The defect this covers: the first version kept `last_envelope` and handed
    that to the guard. A final run whose stdout did not parse set it to `None`,
    which disarmed the guard while the report still carried the previous run's
    summary. A guard that switches itself off exactly when something has gone
    wrong is the wrong shape for a guard.
    """

    def _run_main(self, batches, report_line):
        calls = iter(batches)
        real_run_batch = live_batch.run_batch
        real_summarise = live_batch.summarise

        def fake_run_batch(index):
            return next(calls)

        def leaky_summarise(envelope):
            summary = real_summarise(envelope)
            # Stand in for a future diagnostic that interpolates a value it
            # should not. The guard is what has to notice.
            summary["warnings"] = [report_line]
            return summary

        os.environ[live_batch.APPROVAL_ENV] = "1"
        live_batch.run_batch = fake_run_batch
        live_batch.summarise = leaky_summarise
        try:
            # The report goes to stdout by design; a passing test should not
            # print one.
            with contextlib.redirect_stdout(io.StringIO()):
                return live_batch.main()
        finally:
            live_batch.run_batch = real_run_batch
            live_batch.summarise = real_summarise
            os.environ.pop(live_batch.APPROVAL_ENV, None)

    NAMED = {"data": {"clients": [{"name": "Kitchen Tablet"}]},
             "ok": True, "warnings": []}

    def test_a_leak_from_an_earlier_run_is_still_caught(self):
        """Run 3 fails to parse; run 1's client name must still arm the guard."""
        batches = [
            (0.4, 0, self.NAMED, ""),
            (0.4, 0, self.NAMED, ""),
            (0.4, 1, None, ""),          # unparseable, so `envelope` is None
        ]
        with self.assertRaises(SystemExit) as caught:
            self._run_main(batches, "Kitchen Tablet")
        self.assertIn("REQ-B20", str(caught.exception))

    def test_a_clean_report_over_the_same_runs_still_prints(self):
        """The control: same batches, nothing leaked, so it must not refuse."""
        batches = [
            (0.4, 0, self.NAMED, ""),
            (0.4, 0, self.NAMED, ""),
            (0.4, 1, None, ""),
        ]
        code = self._run_main(batches, "clients_truncated")
        # A run that did not parse is a finding, so a non-zero exit is correct
        # here; what matters is that it PRINTED rather than refusing.
        self.assertEqual(code, 1)


class SummaryTest(unittest.TestCase):
    """Read against the committed corpus, which is where the shape is frozen."""

    def test_counts_come_from_counts_and_never_from_a_length(self):
        """REQ-010 / AC-063, applied to the tool that reports the numbers.

        `success_browse_empty_lists` carries empty arrays beside real totals,
        so a summariser reading `len()` for a total reports 0 here.
        """
        summary = live_batch.summarise(load("success_browse_empty_lists.json"))
        self.assertEqual(summary["devicesListed"], 0)
        self.assertGreater(summary["devicesTotal"], 0)

    def test_detail_substance_is_measured_separately_from_its_presence(self):
        """Ports and radios asserted apart, because summed they hide each other.

        The first version of this case asserted `ports + radios > 0`, and a
        mutant that never counted a port survived it: the fixture's two radios
        carried the sum on their own.
        """
        full = live_batch.summarise(load("success_browse_full.json"))
        self.assertGreater(full["detailFetched"], 0)
        self.assertGreater(full["ports"], 0)
        self.assertGreater(full["radios"], 0)

    def test_an_empty_detail_object_counts_as_no_ports(self):
        """The check that a 400 ms batch really fetched something."""
        envelope = {"data": {"devices": [
            {"id": "a", "detail": {"ports": [], "radios": []}},
        ]}}
        summary = live_batch.summarise(envelope)
        self.assertEqual(summary["ports"] + summary["radios"], 0)

    def test_a_failure_envelope_summarises_without_raising(self):
        summary = live_batch.summarise(load("failure_credential.json"))
        self.assertIs(summary["ok"], False)
        self.assertEqual(summary["errorKind"], "credential")

    def test_the_budget_matches_the_helper(self):
        """REQ-017 lives in `helper/unifi/deadline.py`; this is a copy of it.

        Two constants that must agree, cross-checked rather than shared —
        the same treatment the dual-use JS modules' duplicated constants get.
        """
        sys.path.insert(0, os.path.join(REPO, "helper"))
        from unifi import deadline
        self.assertEqual(live_batch.BUDGET_SEC, deadline.BUDGET_SEC)


if __name__ == "__main__":
    unittest.main(verbosity=1)
