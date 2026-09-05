#!/usr/bin/env python3
"""AC-059 and the corpus integrity checks.

Run directly, under every pinned interpreter:

    python3 -B -E -s tests/test_fixtures.py

Not through `unittest discover`: discover needs the start directory to be an
importable package, which means PYTHONPATH, and -E is exactly what discards
PYTHONPATH. -E is not negotiable — it is what stops the suite being steered by
the environment. So the repo root is derived from __file__ instead.
"""

import io
import json
import os
import re
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import gen_envelopes                      # noqa: E402
import gen_fixtures                       # noqa: E402
import gen_index                          # noqa: E402

FIXTURES = os.path.join(REPO, "tests", "fixtures")
PROTOCOL_MD = os.path.join(REPO, "docs", "protocol-v1.md")

# The two literal synthetic UUID prefixes, one per hand-authored corpus. Both
# are version-5-shaped but are written out directly rather than hashed, so they
# cannot collide with the derived set and are obvious on sight.
LITERAL_UUID = re.compile(r"^00000000-0000-5000-[89]000-[0-9a-f]{12}$")

UUID_ANY = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
MAC_ANY = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
IPV4_ANY = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# SEC-011's synthetic MAC range. 0x02 sets the locally-administered bit, which
# by IEEE 802 definition means the address is drawn from no assigned OUI, so
# there is no vendor it could be traced to.
SYNTHETIC_MAC_PREFIX = "02:00:00:"


def corpus_files():
    for root, _dirs, names in os.walk(FIXTURES):
        for name in sorted(names):
            if name.endswith(".json"):
                yield os.path.join(root, name)


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def rfc1918(address):
    try:
        octets = [int(part) for part in address.split(".")]
    except ValueError:
        return False
    if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
        return False          # not a dotted quad at all; e.g. a version string
    a, b = octets[0], octets[1]
    return (a == 10) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def is_dotted_quad(candidate):
    """Reject version-string false positives such as 9.1.0 or 6.6.55."""
    return len(candidate.split(".")) == 4


class Privacy(unittest.TestCase):
    """AC-059 / SEC-011. No fixture may carry anything traceable to a real site."""

    def test_every_uuid_is_derivable_from_the_documented_namespace(self):
        # This is a proof, not a pattern match. Every uuid in the corpus is
        # either recomputed here from the recorded label list, or is one of the
        # two literal synthetic prefixes. Anything captured from a real
        # controller can be neither.
        manifest = json.loads(read(os.path.join(FIXTURES, "api", "manifest.json")))
        namespace = uuid.UUID(manifest["identity"]["namespace"])
        self.assertEqual(
            namespace,
            uuid.uuid5(uuid.NAMESPACE_DNS, "fixtures.omarchy-unifi.invalid"),
            "the recorded namespace is not the documented one",
        )
        derived = {str(uuid.uuid5(namespace, label))
                   for label in manifest["identity"]["labels"]}
        # The namespace itself appears in the manifest. It is allowed only
        # because the assertion above has just proved it is the documented
        # value, recomputed from the .invalid DNS name.
        derived.add(str(namespace))
        self.assertGreater(len(derived), 1)

        strays = {}
        for path in corpus_files():
            for found in set(UUID_ANY.findall(read(path))):
                lowered = found.lower()
                if lowered in derived or LITERAL_UUID.match(lowered):
                    continue
                strays.setdefault(os.path.relpath(path, REPO), set()).add(found)
        self.assertEqual(strays, {}, "uuid(s) not derivable from the fixture namespace")

    def test_every_mac_is_in_the_synthetic_range(self):
        strays = {}
        for path in corpus_files():
            for found in set(MAC_ANY.findall(read(path))):
                if not found.lower().startswith(SYNTHETIC_MAC_PREFIX):
                    strays.setdefault(os.path.relpath(path, REPO), set()).add(found)
        self.assertEqual(strays, {},
                         "MAC(s) outside the documented 02:00:00: synthetic range")

    def test_every_ipv4_address_is_rfc1918(self):
        strays = {}
        for path in corpus_files():
            text = read(path)
            for found in set(IPV4_ANY.findall(text)):
                if not is_dotted_quad(found):
                    continue
                if not rfc1918(found):
                    strays.setdefault(os.path.relpath(path, REPO), set()).add(found)
        self.assertEqual(strays, {}, "non-RFC1918 address(es) in the corpus")

    def test_no_credential_shaped_string(self):
        # SEC-001's belt to secrets.sh's braces. tests/fixtures/ is exempt from
        # the repo-wide pattern grep by AC-013, precisely so synthetic key-shaped
        # strings are possible — which means this is the only check standing
        # between the corpus and a real key pasted into it.
        banned = re.compile(
            r"X-API-Key|api[_-]?key|BEGIN [A-Z ]*PRIVATE KEY|Bearer\s+\S{20,}",
            re.IGNORECASE)
        hits = {}
        for path in corpus_files():
            for line_no, line in enumerate(read(path).splitlines(), 1):
                if banned.search(line):
                    hits.setdefault(os.path.relpath(path, REPO), []).append(line_no)
        self.assertEqual(hits, {}, "credential-shaped string(s) in the corpus")


class Determinism(unittest.TestCase):
    """The corpus is tamper-evident: it must regenerate byte-identically."""

    def test_api_corpus_regenerates_identically(self):
        self.assertEqual(
            gen_fixtures.main(["--check", "--out", os.path.join(FIXTURES, "api")]), 0)

    def test_envelope_corpus_regenerates_identically(self):
        self.assertEqual(
            gen_envelopes.main(["--check", "--out", os.path.join(FIXTURES, "envelopes")]), 0)

    def test_index_matches_both_corpora(self):
        self.assertEqual(gen_index.main(["--check"]), 0)


class Coverage(unittest.TestCase):
    """The corpus covers every enumeration protocol-v1.md declares."""

    def setUp(self):
        self.index = json.loads(read(os.path.join(FIXTURES, "index.json")))
        self.envelopes = json.loads(
            read(os.path.join(FIXTURES, "envelopes", "index.json")))

    def test_every_data_008_rejection_class_has_a_fixture(self):
        declared = set(gen_envelopes.load_rejection_classes())
        covered = {entry["rejectionClass"] for entry in self.envelopes["reject"].values()}
        self.assertEqual(declared - covered, set(),
                         "DATA-008 class(es) declared in protocol-v1.md with no fixture")
        self.assertEqual(covered - declared, set(),
                         "fixture(s) naming a class absent from protocol-v1.md")

    def test_every_data_007_error_kind_has_an_accept_fixture(self):
        matrix = gen_envelopes.load_error_matrix()
        covered = {entry["errorKind"] for entry in self.envelopes["accept"].values()
                   if entry.get("errorKind")}
        self.assertEqual(set(matrix) - covered, set(),
                         "DATA-007 kind(s) with no protocol-valid failure envelope")

    def test_every_data_009_invariant_has_a_pagination_case(self):
        text = read(PROTOCOL_MD)
        section = re.search(r"## DATA-009 invariants(.*?)(\n## |$)", text, re.S)
        self.assertIsNotNone(section, "protocol-v1.md: DATA-009 section not found")
        declared = {line.strip().strip("|").split("|")[0].strip().strip("`")
                    for line in section.group(1).splitlines() if line.startswith("| `")}
        covered = {entry["invariant"] for key, entry in self.index["cases"].items()
                   if key.startswith("api/pagination/") and entry.get("invariant")}
        self.assertEqual(declared - covered, set(),
                         "DATA-009 invariant(s) with no pagination fixture")

    def test_every_reject_case_names_the_side_that_enforces_it(self):
        # The distinction is load-bearing: DATA-009 is helper-side because the
        # pages do not survive into the envelope, DATA-008 is service-side
        # because the service is the consumer. A test written against the wrong
        # side passes while leaving the rule untested where it is implemented.
        for name, entry in self.index["cases"].items():
            if entry["verdict"] != "reject":
                continue
            self.assertIn(entry["enforcedBy"], ("helper", "service"), name)
            self.assertTrue(entry["rule"], "%s: no rule cited" % name)
            if name.startswith("api/pagination/"):
                self.assertEqual(entry["enforcedBy"], "helper", name)
            elif name.startswith("envelopes/reject/"):
                self.assertEqual(entry["enforcedBy"], "service", name)


class AcceptCorpusShape(unittest.TestCase):
    """The accept corpus is the frozen shape both languages implement."""

    def accepts(self):
        directory = os.path.join(FIXTURES, "envelopes", "accept")
        for name in sorted(os.listdir(directory)):
            yield name, json.loads(read(os.path.join(directory, name)))

    def test_every_accept_envelope_has_the_nine_top_level_keys(self):
        expected = {"protocolVersion", "ok", "nonce", "attemptedAt", "observedAt",
                    "meta", "data", "warnings", "error"}
        for name, envelope in self.accepts():
            self.assertEqual(set(envelope), expected, name)

    def test_meta_is_always_an_object_with_a_helper_version(self):
        # DEV-2. The container is unconditional and helperVersion is the one
        # non-nullable field, so the validator has one rule rather than two.
        for name, envelope in self.accepts():
            self.assertIsInstance(envelope["meta"], dict, name)
            self.assertIsNotNone(envelope["meta"]["helperVersion"], name)

    def test_success_envelopes_satisfy_the_byclass_invariants(self):
        # DEV-1. Checked here as well as in the generator, because the generator
        # could be edited to compute rather than assert and nothing else would
        # notice.
        for name, envelope in self.accepts():
            if not envelope["ok"]:
                continue
            counts = envelope["data"]["counts"]
            by_class = counts["byClass"]
            self.assertEqual(set(by_class), set(gen_envelopes.CLASSES), name)
            self.assertEqual(sum(by_class.values()), counts["devicesTotal"],
                             "%s: sum(byClass) != devicesTotal" % name)
            self.assertEqual(by_class["down"] + by_class["impaired"],
                             counts["offlineTotal"],
                             "%s: byClass.down+impaired != offlineTotal" % name)

    def test_offline_list_never_exceeds_its_bound_or_its_total(self):
        for name, envelope in self.accepts():
            if not envelope["ok"]:
                continue
            offline = envelope["data"]["offlineDevices"]
            self.assertLessEqual(len(offline), 10, "%s: REQ-010 bounds this to 10" % name)
            self.assertLessEqual(len(offline), envelope["data"]["counts"]["offlineTotal"],
                                 "%s: more devices listed than counted" % name)
            for device in offline:
                self.assertIn(device["class"], ("down", "impaired"),
                              "%s: REQ-003 keeps transitional devices out of this list" % name)

    def test_wan_status_stays_in_its_domain_and_omits_the_impossible_metrics(self):
        for name, envelope in self.accepts():
            if not envelope["ok"]:
                continue
            wan = envelope["data"]["wan"]
            self.assertIn(wan["status"], ("up", "down", "degraded", "unknown"), name)
            # Absent from the model, not present-and-null: no supported API
            # version can populate them, and a present-and-null field invites a
            # future contributor to fill it in.
            self.assertNotIn("latencyMs", wan, name)
            self.assertNotIn("packetLossPct", wan, name)

    def test_every_failure_envelope_matches_the_data_007a_matrix(self):
        matrix = gen_envelopes.load_error_matrix()
        for name, envelope in self.accepts():
            if envelope["ok"]:
                continue
            gen_envelopes.assert_matrix_consistent(envelope["error"], matrix)


if __name__ == "__main__":
    unittest.main(verbosity=2)
