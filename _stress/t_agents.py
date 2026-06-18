"""Agents (branches): CRUD, validation, RBAC (manage_branches owner-only), tenant isolation."""
from core import call, body, ok, reject, finding, RUN_TAG

A = "agents"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    P = RUN_TAG[-6:]

    # list — owner sees own branches only
    lst, good = ok(call("GET", "/agents/", token=tA), A, "list agents A")
    if good:
        ids = {a["id"] for a in lst.get("agents", [])}
        for need in (F["A1"], F["A2"], F["A3"]):
            if need not in ids:
                finding("HIGH", A, "owner list missing own agent", f"{need} not in {ids}", "GET /")
        if F["B1"] in ids:
            finding("CRITICAL", A, "TENANT LEAK: A's agent list contains B's branch",
                    f"B1={F['B1']} in {ids}", "GET /")
    lb, _ = ok(call("GET", "/agents/", token=tB), A, "list agents B")
    if lb and F["A1"] in {a["id"] for a in lb.get("agents", [])}:
        finding("CRITICAL", A, "TENANT LEAK: B sees A's branch in list", "", "GET /")

    # get one
    ok(call("GET", f"/agents/{F['A1']}", token=tA), A, "get A1")
    reject(call("GET", "/agents/99999999", token=tA), A, "get unknown agent", {404})
    reject(call("GET", f"/agents/{F['A1']}", token=tB), A, "B reads A1", {403, 404},
           sev2xx="CRITICAL")
    reject(call("GET", f"/agents/{F['A1']}"), A, "no-token get A1", {401, 403})

    # create validation
    base = dict(agent_name="Tmp", phone_number=f"+1{P}7777", location_name="L",
                timezone="UTC", model="claude-3-haiku-20240307")
    reject(call("POST", "/agents/", token=tA, json_body={k: v for k, v in base.items() if k != "agent_name"}),
           A, "create missing agent_name", {422})
    reject(call("POST", "/agents/", token=tA, json_body={**base, "timezone": "Mars/Phobos"}),
           A, "create invalid timezone", {422, 400})
    reject(call("POST", "/agents/", token=tA, json_body={**base, "tax_rate": 500}),
           A, "create tax_rate>100", {422, 400})
    reject(call("POST", "/agents/", token=tA, json_body={**base, "tax_rate": -5}),
           A, "create negative tax_rate", {422, 400})
    reject(call("POST", "/agents/", token=tA, json_body={**base, "currency": "DOLLARS"}),
           A, "create bad currency (not ISO4217)", {422, 400})
    reject(call("POST", "/agents/", token=tA, json_body={**base, "phone_number": f"notaphone{P}"}),
           A, "create bad phone format", {422, 400})

    # RBAC: receptionist lacks manage_branches
    reject(call("POST", "/agents/", token=tR, json_body={**base, "phone_number": f"+1{P}7766"}),
           A, "receptionist create agent", {403}, expect_code="PERMISSION_DENIED")
    reject(call("PUT", f"/agents/{F['A1']}", token=tR, json_body={"location_name": "hax"}),
           A, "receptionist update agent", {403})
    reject(call("DELETE", f"/agents/{F['A1']}", token=tR), A, "receptionist delete agent", {403})

    # cross-tenant writes
    reject(call("PUT", f"/agents/{F['A1']}", token=tB, json_body={"location_name": "hax"}),
           A, "B updates A1", {403, 404}, sev2xx="CRITICAL")
    reject(call("DELETE", f"/agents/{F['A1']}", token=tB), A, "B deletes A1", {403, 404},
           sev2xx="CRITICAL")

    # happy update + activate/deactivate on A2
    ok(call("PUT", f"/agents/{F['A2']}", token=tA, json_body={"location_name": "Uptown Renamed"}),
       A, "owner update A2")
    ok(call("PATCH", f"/agents/{F['A2']}/deactivate", token=tA), A, "deactivate A2")
    ok(call("PATCH", f"/agents/{F['A2']}/activate", token=tA), A, "activate A2")

    # custom-domain: external (Vercel) — only test the guards that return BEFORE any Vercel call
    reject(call("POST", f"/agents/{F['A1']}/custom-domain", token=tR, json_body={"domain": "x.com"}),
           A, "receptionist set custom-domain (perm guard pre-Vercel)", {403})
    reject(call("POST", f"/agents/{F['A1']}/custom-domain"), A, "no-token custom-domain", {401, 403})
    finding("INFO", A, "custom-domain happy path SKIPPED (calls Vercel API) per instruction",
            "POST/verify/DELETE custom-domain not exercised", "custom-domain")

    # disposable agent for destructive delete (don't tear down A1/A2/A3)
    tmp, good = ok(call("POST", "/agents/", token=tA, json_body={**base, "agent_name": "Disposable",
                   "phone_number": f"+1{P}7755"}), A, "create disposable agent")
    if good:
        ok(call("DELETE", f"/agents/{tmp['id']}", token=tA), A, "delete disposable agent")
        reject(call("GET", f"/agents/{tmp['id']}", token=tA), A, "get deleted agent", {404})
        reject(call("DELETE", f"/agents/{tmp['id']}", token=tA), A, "double-delete agent", {404})
