"""Customers: CRUD, by-phone, history, uniqueness, RBAC (view/manage_customers), tenant isolation."""
from core import call, body, ok, reject, finding, RUN_TAG

A = "customers"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    P = RUN_TAG[-6:]

    ok(call("GET", "/customers/", token=tA), A, "list customers")
    ok(call("GET", "/customers/", token=tA, params={"search": "Carol"}), A, "search customers")
    ok(call("GET", f"/customers/{F['C1']}", token=tA), A, "get C1")
    ok(call("GET", "/customers/by-phone", token=tA, params={"phone": f"+1{P}0041"}), A, "by-phone")
    ok(call("GET", f"/customers/{F['C1']}/appointments", token=tA), A, "customer history")

    # create + uniqueness (business_id + phone)
    cust, good = ok(call("POST", "/customers/", token=tA, json_body={
        "name": "New Cust", "phone": f"+1{P}9001", "email": f"nc_{RUN_TAG}@qa-booklync.com"}),
        A, "create customer", codes=(200, 201))
    reject(call("POST", "/customers/", token=tA, json_body={"name": "Dup", "phone": f"+1{P}9001"}), A,
           "create customer duplicate phone", {409, 400, 422}, sev2xx="HIGH")
    reject(call("POST", "/customers/", token=tA, json_body={"name": "NoPhone"}), A,
           "create customer missing phone", {422})

    # cross-tenant
    reject(call("GET", f"/customers/{F['C1']}", token=tB), A, "B reads A's customer", {403, 404}, sev2xx="CRITICAL")
    reject(call("PUT", f"/customers/{F['C1']}", token=tB, json_body={"name": "hax"}), A,
           "B updates A's customer", {403, 404}, sev2xx="CRITICAL")
    reject(call("GET", "/customers/99999999", token=tA), A, "get unknown customer", {404, 403})

    # RBAC: receptionist usually CAN manage customers; assert from captured perms
    if "manage_customers" not in F["recep_perms"]:
        reject(call("POST", "/customers/", token=tR, json_body={"name": "x", "phone": f"+1{P}9099"}), A,
               "no-manage_customers create", {403})
    if "view_customers" not in F["recep_perms"]:
        reject(call("GET", "/customers/", token=tR), A, "no-view_customers list", {403})

    # update + delete (disposable)
    if good and cust.get("id"):
        cid = cust["id"]
        ok(call("PUT", f"/customers/{cid}", token=tA, json_body={"notes": "VIP"}), A, "update customer")
        ok(call("DELETE", f"/customers/{cid}", token=tA), A, "delete customer")
        reject(call("GET", f"/customers/{cid}", token=tA), A, "get deleted customer", {404, 403})
