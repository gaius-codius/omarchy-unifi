"""The envelope's `warnings` array, as a bounded collector.

Named `warn` rather than `warnings` on purpose: a module named `unifi.warnings`
is one absolute-import mistake away from shadowing the standard library's, and
the failure would be silent and confusing.

The codes are the closed set in docs/protocol-v1.md. A code is a contract with
the panel — UX-006a reads `sites_discovered`'s detail to list sites, UX-009
reads `insecure_tls` — so inventing one at a call site produces a warning
nothing renders. `add()` therefore rejects an unknown code.

The 32-entry bound is the envelope's. It is enforced here rather than at
serialization time so that the count in `truncated` is honest: a collector that
silently dropped the overflow would report a bounded list as a complete one.
"""

from . import sanitize

# docs/protocol-v1.md, the `warnings` code table. Helper-side codes only; the
# service appends its own (DATA-002a's invalid settings, UX-010's plain http)
# and those never appear in an envelope.
CODES = (
    "unknown_device_state",
    "site_auto_selected",
    "sites_discovered",
    "clients_unavailable",
    "statistics_unavailable",
    "wans_unavailable",
    "gateway_statistics_truncated",
    "offline_list_truncated",
    "page_reread_mismatch",
    "insecure_tls",
    "custom_ca_in_use",
    "retry_after_clamped",
    "retry_after_ignored",
    "stderr_bound_exceeded",
)

MAX_ENTRIES = 32

MESSAGES = {
    "unknown_device_state": "A device reported a state this version does not know.",
    "site_auto_selected": "No site was committed; the controller's only site was selected.",
    "sites_discovered": "Several sites exist. Choose one with scripts/configure --site.",
    "clients_unavailable": "Client list unavailable; count shown as unknown.",
    "statistics_unavailable": "Gateway statistics unavailable; metrics shown as unknown.",
    "wans_unavailable": "WAN list unavailable; WAN names omitted.",
    "gateway_statistics_truncated": "Statistics were fetched for the first four gateways only.",
    "offline_list_truncated": "The offline device list is truncated; see the total.",
    "page_reread_mismatch": "The controller's data changed while it was being read; retrying.",
    "insecure_tls": "TLS verification is disabled for this controller.",
    "custom_ca_in_use": "A custom certificate authority is in use for this controller.",
    "retry_after_clamped": "The controller asked for an implausibly long retry delay.",
    "retry_after_ignored": "The controller sent a retry delay that could not be read.",
    "stderr_bound_exceeded": "The helper produced more diagnostic output than it may emit.",
}


class UnknownWarningCode(ValueError):
    """A code outside the closed set. See the module docstring."""


class Warnings(object):
    """An ordered, bounded, de-duplicating warning collector."""

    __slots__ = ("_entries", "_dropped")

    def __init__(self):
        self._entries = []
        self._dropped = 0

    def add(self, code, detail=None, message=None):
        if code not in CODES:
            raise UnknownWarningCode(code)
        if len(self._entries) >= MAX_ENTRIES:
            self._dropped += 1
            return False
        self._entries.append({
            "code": code,
            "message": sanitize.message(message if message is not None
                                        else MESSAGES[code]),
            "detail": detail,
        })
        return True

    def codes(self):
        return [entry["code"] for entry in self._entries]

    def dropped(self):
        return self._dropped

    def to_list(self):
        return list(self._entries)

    def __len__(self):
        return len(self._entries)
