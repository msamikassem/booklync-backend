"""Services / Titles / Variations: CRUD, pricing rule, prep/color validation, assign, RBAC."""
from core import call, body, ok, reject, finding, RUN_TAG

A = "services"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A1, B1 = F["A1"], F["B1"]
    q = {"agent_id": A1}

    ok(call("GET", "/services/catalogue", token=tA, params=q), A, "catalogue")
    ok(call("GET", "/services/", token=tA, params=q), A, "list services")
    ok(call("GET", f"/services/{F['S1']}", token=tA, params=q), A, "get S1")
    reject(call("GET", "/services/", token=tA), A, "list services missing agent_id", {422})
    reject(call("GET", f"/services/{F['S1']}", token=tB, params=q), A, "B reads A service via A1", {403, 404}, sev2xx="CRITICAL")
    reject(call("GET", f"/services/{F['S1']}", token=tA, params={"agent_id": B1}), A, "A reads S1 scoped to B branch", {403, 404}, sev2xx="CRITICAL")

    # ---- titles ----
    t, good = ok(call("POST", "/services/titles/", token=tA, json_body={"agent_id": A1, "name": "Tmp Title"}),
                 A, "create title", codes=(200, 201))
    TID = t["id"] if good else None
    ok(call("GET", "/services/titles/", token=tA, params=q), A, "list titles")
    if TID:
        ok(call("PUT", f"/services/titles/{TID}", token=tA, params=q, json_body={"name": "Renamed"}), A, "update title")
        ok(call("PATCH", f"/services/titles/{TID}/deactivate", token=tA, params=q), A, "deactivate title")
        ok(call("PATCH", f"/services/titles/{TID}/activate", token=tA, params=q), A, "activate title")
    reject(call("POST", "/services/titles/", token=tR, json_body={"agent_id": A1, "name": "X"}), A,
           "receptionist create title", {403}, expect_code="PERMISSION_DENIED")

    # ---- service create: pricing rule ----
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "Both",
            "price": 10, "duration_minutes": 30, "variations": [{"name": "v", "price": 5, "duration_minutes": 10}]}),
           A, "create service price+variations (both)", {422, 400}, sev2xx="HIGH")
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "Neither"}),
           A, "create service no price no variations", {422, 400}, sev2xx="HIGH")
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "NegPrep",
            "price": 10, "duration_minutes": 30, "preparation_before_minutes": -5}),
           A, "create service negative prep", {422, 400})
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "BadColor",
            "price": 10, "duration_minutes": 30, "color": "red"}),
           A, "create service bad color (not hex)", {422, 400})
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "NegPrice",
            "price": -10, "duration_minutes": 30}), A, "create service negative price", {422, 400})
    reject(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "ZeroDur",
            "price": 10, "duration_minutes": 0}), A, "create service zero duration", {422, 400}, sev2xx="HIGH")

    # disposable service for mutation/delete
    s, good = ok(call("POST", "/services/", token=tA, json_body={"agent_id": A1, "name": "Disposable Svc",
            "price": 15, "duration_minutes": 25}), A, "create disposable service", codes=(200, 201))
    SID = s["id"] if good else None
    if SID:
        ok(call("PUT", f"/services/{SID}", token=tA, params=q, json_body={"name": "Disp2", "color": None}), A,
           "update service (clear color via null)")
        ok(call("PATCH", f"/services/{SID}/deactivate", token=tA, params=q), A, "deactivate service")
        ok(call("PATCH", f"/services/{SID}/activate", token=tA, params=q), A, "activate service")
        # variations on it
        v, vgood = ok(call("POST", f"/services/{SID}/variations/", token=tA, params=q,
                json_body={"name": "Var1", "price": 20, "duration_minutes": 30}), A, "add variation", codes=(200, 201))
        ok(call("GET", f"/services/{SID}/variations/", token=tA, params=q), A, "list variations")
        if vgood and v.get("id"):
            VID = v["id"]
            ok(call("PUT", f"/services/{SID}/variations/{VID}", token=tA, params=q, json_body={"price": 22}), A, "update variation")
            ok(call("PATCH", f"/services/{SID}/variations/{VID}/deactivate", token=tA, params=q), A, "deactivate variation")
            ok(call("PATCH", f"/services/{SID}/variations/{VID}/activate", token=tA, params=q), A, "activate variation")
            ok(call("DELETE", f"/services/{SID}/variations/{VID}", token=tA, params=q), A, "delete variation")
        # assign / unassign employee
        ok(call("POST", f"/services/{SID}/assign-employee", token=tA, params=q,
                json_body={"employee_id": F["E1"], "extra_price": 5}), A, "assign employee to service")
        ok(call("GET", f"/services/{SID}/employees", token=tA, params=q), A, "list service employees")
        ok(call("DELETE", f"/services/{SID}/unassign-employee/{F['E1']}", token=tA, params=q), A, "unassign employee")
        # RBAC + cross-tenant on writes
        reject(call("PUT", f"/services/{SID}", token=tR, params=q, json_body={"name": "hax"}), A,
               "receptionist update service", {403})
        reject(call("DELETE", f"/services/{SID}", token=tB, params=q), A, "B deletes A's service", {403, 404}, sev2xx="CRITICAL")
        ok(call("DELETE", f"/services/{SID}", token=tA, params=q), A, "delete disposable service")
        reject(call("GET", f"/services/{SID}", token=tA, params=q), A, "get deleted service", {404, 403})

    # delete disposable title (cascades any children)
    if TID:
        ok(call("DELETE", f"/services/titles/{TID}", token=tA, params=q), A, "delete title")
