"""BIZ-004: one batch, and which of its failures are fatal.

This is where required and optional live, and nowhere else. `pagination.py`
returns a `CollectResult` that describes what happened and takes no view on
whether it matters; `transport.py` raises a typed error and takes no view
either. The policy is one table here:

| Route | Class | On failure |
|---|---|---|
| `/v1/info` | required | the batch fails |
| `/v1/sites` | required | the batch fails |
| `/v1/sites/{id}/devices` | required | the batch fails |
| `/v1/sites/{id}/clients` | optional | `counts.clients = null` + warning |
| `/v1/sites/{id}/devices/{id}/statistics/latest` | optional | metrics null + warning |
| `/v1/sites/{id}/wans` | optional | warning |

The reason it is a table and not a set of `try` blocks with different bodies is
BIZ-004's own sentence: *a device-health indicator must not be disabled by an
unavailable throughput metric*. Every optional route on that list is a metric;
every required one is the inventory the health rules read.

`/v1/wans` is fetched and its result is unused. `api-contract.md` limitation 3
says the route returns `{id, name}` and nothing else, and DATA-006 has no field
for a WAN name — so in v1 the call can only fail, never contribute. It is made
because BIZ-004 lists it and `wans_unavailable` is a documented warning code;
whether to keep paying for it is raised as DEV-5 rather than decided here.
"""

from . import errors
from . import normalize
from . import pagination
from . import routes
from . import transport
from . import version_gate

# api-contract.md: the count comes from the terminal page's totalCount, after
# every DATA-009 invariant has passed. Never from an arithmetic shortcut.
CLIENT_COUNT_FROM_TOTAL = True


class Batch(object):
    """The result of one successful batch: the `data` object and its site."""

    __slots__ = ("data", "site_id")

    def __init__(self, data, site_id):
        self.data = data
        self.site_id = site_id


def run(config, credential, context, deadline, warnings, budget=None,
        get_json=None):
    """Perform one whole batch and return a `Batch`, or raise a `HelperError`.

    `get_json` is injected so the batch can be driven without a socket. Its
    signature is `transport.get_json`'s.
    """
    fetch = get_json if get_json is not None else transport.get_json
    budget = budget if budget is not None else pagination.ByteBudget()

    def request(route, **params):
        deadline.check(route)
        built = routes.build(config.api_root, route, **params)
        body, _size = fetch(built, credential, context, deadline,
                            warnings=warnings)
        return body

    def collection(route, **params):
        def fetch_page(offset, limit):
            deadline.check(route)
            built = routes.build(config.api_root, route, offset=offset,
                                 limit=limit, **params)
            return fetch(built, credential, context, deadline, warnings=warnings)

        return pagination.collect(fetch_page, budget=budget, warnings=warnings)

    # --- required: /v1/info, and the R2 version gate ----------------------
    application_version = version_gate.check(request("info"))

    # --- required: /v1/sites, then DATA-012 site selection ----------------
    sites = _require(collection("sites"), "sites")
    site = _select_site(config.site_id, sites, warnings)

    # --- required: the device inventory -----------------------------------
    devices = _require(collection("devices", siteId=site["id"]), "devices")

    # --- optional: statistics for at most four gateways (REQ-008a) --------
    statistics = _gateway_statistics(devices, site["id"], request, warnings)

    # --- optional: the client count ---------------------------------------
    clients = _optional_count(collection("clients", siteId=site["id"]),
                              warnings, "clients_unavailable")

    # --- optional: /v1/wans. See the module docstring and DEV-5. ----------
    wans = collection("wans", siteId=site["id"])
    if not wans.complete:
        warnings.add("wans_unavailable")

    data = normalize.build(site, devices, clients, statistics,
                           application_version, warnings)
    return Batch(data, site["id"])


def _require(result, name):
    """BIZ-004's required half: the batch fails, keeping the original kind.

    A transport failure is re-raised as itself — a `network` failure on
    `/devices` is a network failure, not a `partial_response`. Only an
    INVARIANT failure becomes `partial_response`, because that is what BIZ-002
    means by a count that could not be proven complete.
    """
    if result.error is not None:
        raise result.error
    if not result.complete:
        raise errors.PartialResponseError(
            "The controller's %s list could not be read completely." % name,
            detail={"collection": name, "invariant": result.invariant})
    return result.records


def _optional_count(result, warnings, code):
    """BIZ-004's optional half: never a smaller-but-plausible number.

    BIZ-002 forbids reporting an incomplete count, and forbids zero standing in
    for unknown. So a failed optional collection yields `None` — which
    `ViewModel` renders as "unknown" — and a warning saying so.
    """
    if result.error is not None or not result.complete:
        warnings.add(code)
        return None
    return result.total_count


def _gateway_statistics(devices, site_id, request, warnings):
    """REQ-008a: statistics for at most four gateways, in primary order.

    The order comes from `normalize.gateway_order`, which is also what picks the
    primary gateway whose metrics become `wan`'s. Sharing it is what guarantees
    the primary is always among the four fetched; two orderings could leave the
    primary as the one gateway nobody asked about.
    """
    gateways = normalize.gateway_order(devices)
    if not gateways:
        return {}

    fetched = gateways[:normalize.GATEWAY_STATISTICS_MAX]
    if len(gateways) > len(fetched):
        warnings.add("gateway_statistics_truncated",
                     {"fetched": len(fetched), "total": len(gateways)},
                     message="Statistics fetched for %d of %d gateways."
                             % (len(fetched), len(gateways)))

    statistics = {}
    for device in fetched:
        device_id = device.get("id")
        try:
            statistics[device_id] = request("device_statistics",
                                            siteId=site_id, deviceId=device_id)
        except errors.HelperError:
            # Optional per BIZ-004, and per DEVICE: one gateway's statistics
            # failing must not cost the other three theirs, and must not fail a
            # batch whose device inventory arrived intact.
            warnings.add("statistics_unavailable", {"deviceId": device_id})
    return statistics


def _select_site(committed_site_id, sites, warnings):
    """DATA-012. Exactly one site is displayed (BIZ-006); discovery only helps choose.

    A committed `siteId` the controller does not have is reported as
    `site_unselected` rather than as a conflict: the user's next action is
    identical — run `scripts/configure --site` — and `site_unselected` is
    already the kind REQ-018a suspends polling for, which is right, because no
    amount of retrying will make the site appear.
    """
    known = [entry for entry in sites if isinstance(entry, dict)
             and isinstance(entry.get("id"), str)]

    if committed_site_id:
        for entry in known:
            if entry["id"] == committed_site_id:
                return {"id": entry["id"], "name": entry.get("name")}
        warnings.add("sites_discovered", {"sites": _pairs(known)})
        raise errors.SiteUnselectedError(
            "The committed site is not on this controller. "
            "Run scripts/configure --site to choose one.")

    if not known:
        raise errors.UnsupportedError(
            "The controller reports no sites, which this plugin cannot display.")

    if len(known) == 1:
        entry = known[0]
        warnings.add("site_auto_selected",
                     {"id": entry["id"], "name": entry.get("name")})
        return {"id": entry["id"], "name": entry.get("name")}

    warnings.add("sites_discovered", {"sites": _pairs(known)})
    raise errors.SiteUnselectedError(
        "This controller has several sites. "
        "Run scripts/configure --site to choose one.")


def _pairs(sites):
    """The `{id, name}` pairs UX-006a lists. Nothing else from the record."""
    return [{"id": entry["id"], "name": entry.get("name")} for entry in sites]
