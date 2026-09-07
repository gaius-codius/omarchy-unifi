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
| `/v1/sites/{id}/devices/{id}` | optional | that device's detail null + warning |

The reason it is a table and not a set of `try` blocks with different bodies is
BIZ-004's own sentence: *a device-health indicator must not be disabled by an
unavailable throughput metric*. Every optional route on that list is a metric;
every required one is the inventory the health rules read.

`/v1/wans` is gone. DEV-5, resolved 2026-09-07 as Option B: the route returns
`{id, name}` and nothing else — inferred from the specification at Phase 5,
confirmed against hardware at Phase 12a — so it could never contribute to
DATA-006. It cost two HTTPS requests per batch, because DATA-009a re-reads page
0 at the end of every collection and every request sends `Connection: close`.
"""

from . import bounds
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

    # --- optional: the client list and its count ---------------------------
    #
    # One collection, two products. The COUNT is `totalCount` off the terminal
    # page and survives a bounded list; the RECORDS are what the client page
    # draws. BIZ-002 forbids ever reporting the count as the length of
    # something that was bounded, which is why they stay separate all the way
    # into `normalize.build`.
    client_result = collection("clients", siteId=site["id"])
    clients = _optional_count(client_result, warnings, "clients_unavailable")
    client_records = (client_result.records
                      if client_result.error is None and client_result.complete
                      else [])

    # --- optional: per-device detail (REQ-B02) -----------------------------
    ordered = normalize.browse_order(devices)
    details, statistics = _device_detail(ordered, site["id"], request, deadline,
                                         statistics, warnings)

    _warn_missing_gateway_metrics(devices, statistics, warnings)

    data = normalize.build(site, devices, clients, statistics,
                           application_version, warnings,
                           details=details, listed_devices=ordered,
                           client_records=client_records)
    _apply_budget(data, warnings, clients_total=clients,
                  client_records=len(client_records))
    return Batch(data, site["id"])


def _warn_missing_gateway_metrics(devices, statistics, warnings):
    """REQ-008a's truncation notice, raised once BOTH statistics tiers are done.

    The warning means "not every gateway's metrics are in this envelope". Until
    REQ-B02 that was the same thing as "there were more than four gateways",
    and the cap was the only thing that could cause it — so it was raised at
    the cap. It no longer is: the browse tier fetches statistics too, and on a
    five-gateway site all five now arrive.

    Raising it from the cap would have reported "fetched 4 of 5" beside an
    envelope carrying five sets of metrics. Counting what is actually present
    is both more accurate and a smaller claim: it stays true whatever the two
    tiers between them manage to collect.
    """
    gateways = normalize.gateway_order(devices)
    if not gateways:
        return
    have = [device for device in gateways if device.get("id") in statistics]
    if len(have) < len(gateways):
        warnings.add("gateway_statistics_truncated",
                     {"fetched": len(have), "total": len(gateways)},
                     message="Statistics fetched for %d of %d gateways."
                             % (len(have), len(gateways)))


def _device_detail(ordered, site_id, request, deadline, statistics, warnings):
    """REQ-B02/B02a/B02b: route 6 and route 4 for the head of the browse order.

    Three properties, and each is a decision rather than a detail:

    **The gateway statistics fetched above are kept.** REQ-008a's four are what
    `wan`'s metrics come from, and they are health-critical. Browse order sorts
    `down` devices first, so on a site with a hundred failed switches and one
    healthy gateway the gateway lands past this bound — and deriving REQ-008a's
    set from this one would silently cost the panel its WAN reading exactly
    when the site is at its worst. Two separate selections, and this one only
    adds.

    **The order is REQ-B11's**, so the devices that survive the bound are the
    broken ones rather than the alphabetically early ones (REQ-B02a).

    **It yields to the deadline** (REQ-B02b). Detail is optional in BIZ-004's
    sense, and a device browser must never be able to break the health
    indicator — so collection stops while there is still budget left for
    nothing, rather than consuming what the required collections have already
    spent. Stopping raises the truncation warning, never a failure.
    """
    details = {}
    fetched = 0
    stopped_early = False
    for device in ordered:
        if fetched >= bounds.DEVICE_DETAIL_MAX:
            stopped_early = True
            break
        if deadline.remaining() <= bounds.DETAIL_RESERVE_SEC:
            stopped_early = True
            break
        device_id = device.get("id")
        if not device_id:
            continue
        try:
            details[device_id] = request("device", siteId=site_id,
                                         deviceId=device_id)
            if device_id not in statistics:
                statistics[device_id] = request("device_statistics",
                                                siteId=site_id,
                                                deviceId=device_id)
        except errors.HelperError:
            # Optional per BIZ-004 and per DEVICE. One device's detail failing
            # costs that device its detail and nothing else — not the other
            # thirty-nine, and not a batch whose inventory arrived intact.
            details.pop(device_id, None)
            warnings.add("device_detail_unavailable", {"deviceId": device_id})
        fetched += 1

    if stopped_early:
        warnings.add("device_detail_truncated",
                     {"fetched": len(details), "total": len(ordered)},
                     message="Details were fetched for %d of %d devices."
                             % (len(details), len(ordered)))
    return details, statistics


def _apply_budget(data, warnings, clients_total, client_records):
    """DATA-B04. Assemble within a byte budget, dropping in `ASSEMBLY_ORDER`.

    The model is built whole and then bounded, rather than bounded as it is
    built, because the budget is measured in ENCODED bytes and the encoding is
    what `normalize` produces. Building 300 entries to send 40 costs a few
    milliseconds in-process; guessing at sizes beforehand would cost
    correctness.

    Detail is re-attached last and device by device. It is the flexible tier —
    the one thing here nobody needs to answer "is my network fine?" — and it is
    already ordered by importance, so what survives a squeeze is the broken
    devices' detail rather than the first devices' detail.
    """
    devices = data["devices"]
    clients = data["clients"]

    # Measure the fixed content, do not assume it. `gateways` alone can be 64
    # records of 512-character strings, which is more than the whole envelope
    # budget — so a constant reserve here was wrong by up to thirty times and
    # nothing noticed, because setting it to zero changed no test.
    fixed = dict(data)
    fixed["devices"] = []
    fixed["clients"] = []
    budget = bounds.Budget(bounds.room_for_lists(bounds.encoded_size(fixed)))
    devices_total = data["counts"]["devicesTotal"]
    clients_total = clients_total if clients_total is not None else client_records

    # Held aside so the base records can be measured without it, then given
    # back to as many devices as the remaining budget allows.
    held = [entry.pop("detail") for entry in devices]

    kept_devices, _ = bounds.bounded_list(
        devices, bounds.DEVICES_LISTED_MAX, budget, warnings,
        "devices_truncated", total=devices_total)
    kept_clients, _ = bounds.bounded_list(
        clients, bounds.CLIENTS_LISTED_MAX, budget, warnings,
        "clients_truncated", total=clients_total)

    attached = 0
    withheld = 0
    for index, entry in enumerate(kept_devices):
        detail = held[index]
        if detail is None:
            entry["detail"] = None
            continue
        if budget.admits(detail, name="detail"):
            budget.spend(detail)
            entry["detail"] = detail
            attached += 1
        else:
            # Fetched and then not sent. The request was already paid for, so
            # this is waste — but the alternative is an envelope the service
            # rejects wholesale, and a device whose detail is dropped is still
            # listed, named and classified.
            entry["detail"] = None
            withheld += 1

    if withheld:
        warnings.add("device_detail_truncated",
                     {"fetched": attached, "total": devices_total},
                     message="Details were fetched for %d of %d devices."
                             % (attached, devices_total))

    data["devices"] = kept_devices
    data["clients"] = kept_clients

    # Raised only when the BYTE budget bound, not when a count cap did. The two
    # are different facts: a cap is a product limit and the budget is a
    # transport one, and a user with 300 devices is owed the distinction.
    #
    # `detail` counts as a place the budget can bind, and has to. Measured, the
    # base records at their caps come to ~150 KiB against a ~192 KiB list
    # budget — so `devices[]` and `clients[]` at full stretch ALWAYS fit, and a
    # version of this that only watched those two produced a warning nothing
    # could reach. The budget's real job is bounding detail; saying so is more
    # honest than keeping a code that never fires.
    if budget.refused is not None:
        warnings.add("envelope_truncated", {"dropped": budget.refused})


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

    # No truncation warning HERE. Since REQ-B02 the browse tier also fetches
    # statistics, so a gateway past this cap may still end up with metrics —
    # and a warning raised at this point would report four of five while the
    # envelope carried five. `_warn_missing_gateway_metrics` says it afterwards,
    # once what is actually missing is known.
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
