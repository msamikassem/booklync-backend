"""Cross-cutting tenant-isolation sweep: B's token (and cross-tenant agent_id) must never
reach A's resources. Any 2xx here is a CRITICAL data-exposure bug."""
from core import call, body, ok, reject, finding

A = "isolation"

def run(F):
    tA, tB = F["A_token"], F["B_token"]
    A1, B1 = F["A1"], F["B1"]
    E1, S1, C1 = F["E1"], F["S1"], F["C1"]

    # grab an A-owned appointment + sale id to probe
    apts = body(call("GET", "/appointments/", token=tA, params={"agent_id": A1}))
    apt_id = (apts.get("appointments") or [{}])[0].get("id") if isinstance(apts, dict) else None
    sales = body(call("GET", "/financials/sales", token=tA, params={"agent_id": A1}))
    sale_id = (sales.get("sales") or [{}])[0].get("id") if isinstance(sales, dict) else None

    # (1) B's token against A's resources by id — must 403/404, never 2xx
    probes = [
        ("GET", f"/agents/{A1}", None),
        ("GET", f"/employees/{E1}", {"agent_id": A1}),
        ("GET", f"/services/{S1}", {"agent_id": A1}),
        ("GET", f"/customers/{C1}", None),
        ("GET", f"/customers/{C1}/appointments", None),
        ("GET", "/services/catalogue", {"agent_id": A1}),
        ("GET", "/employees/", {"agent_id": A1}),
        ("GET", "/appointments/", {"agent_id": A1}),
        ("GET", "/business-hours/", {"agent_id": A1}),
        ("GET", "/financials/sales", {"agent_id": A1}),
        ("GET", "/financials/discounts", {"agent_id": A1}),
        ("GET", "/financials/commission-rules", {"agent_id": A1}),
        ("GET", "/financials/reports/revenue", {"agent_id": A1}),
        ("GET", "/chat/conversations/", {"agent_id": A1}),
    ]
    for method, path, params in probes:
        reject(call(method, path, token=tB, params=params), A,
               f"B -> A resource {method} {path}", {403, 404}, sev2xx="CRITICAL")
    if apt_id:
        reject(call("GET", f"/appointments/{apt_id}", token=tB), A,
               f"B -> A appointment {apt_id}", {403, 404}, sev2xx="CRITICAL")
    if sale_id:
        reject(call("GET", f"/financials/sales/{sale_id}", token=tB), A,
               f"B -> A sale {sale_id}", {403, 404}, sev2xx="CRITICAL")

    # (2) A's OWN token but pointing agent_id at B's branch — must be rejected (branch access)
    cross = [
        ("GET", "/employees/", {"agent_id": B1}),
        ("GET", "/services/catalogue", {"agent_id": B1}),
        ("GET", "/appointments/", {"agent_id": B1}),
        ("GET", "/business-hours/", {"agent_id": B1}),
        ("GET", "/financials/sales", {"agent_id": B1}),
        ("GET", "/availability/time", {"agent_id": B1}),
    ]
    for method, path, params in cross:
        reject(call(method, path, token=tA, params=params), A,
               f"A token -> B branch {method} {path}", {403, 404}, sev2xx="CRITICAL")

    # (3) write attempts cross-tenant
    reject(call("PUT", f"/customers/{C1}", token=tB, json_body={"name": "hax"}), A,
           "B writes A's customer", {403, 404}, sev2xx="CRITICAL")
    reject(call("POST", "/services/", token=tB, json_body={"agent_id": A1, "name": "x",
            "price": 1, "duration_minutes": 5}), A, "B creates service in A's branch", {403, 404}, sev2xx="CRITICAL")
