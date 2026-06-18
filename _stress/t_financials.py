"""Financials: sales, payments, discounts, commissions, bundles, packages, combos.
Asserts total == subtotal - discount_total + tax_total + tip_total to the cent after each op."""
import datetime, traceback
from core import call, body, ok, reject, finding

A = "financials"

def m(x):
    try: return round(float(x), 2)
    except Exception: return None

def inv(sale, ctx):
    """Check the documented Sale arithmetic invariant."""
    if not isinstance(sale, dict): return
    sub, disc, tax, tip, tot = (m(sale.get(k)) for k in
        ("subtotal", "discount_total", "tax_total", "tip_total", "total"))
    if None in (sub, disc, tax, tip, tot):
        finding("MEDIUM", A, f"sale missing money fields: {ctx}", str(sale)[:300], ctx); return
    expect = round(sub - disc + tax + tip, 2)
    if abs(expect - tot) > 0.01:
        finding("HIGH", A, f"MONEY INVARIANT VIOLATED: {ctx}",
                f"subtotal={sub} discount={disc} tax={tax} tip={tip} => expected total {expect}, got {tot}", ctx)
    # payments settle (total - tip)
    paid = round(sum(m(p.get("amount")) or 0 for p in sale.get("payments", [])), 2)
    if sale.get("status") == "paid" and abs(paid - round(tot - tip, 2)) > 0.01:
        finding("HIGH", A, f"PAID sale not settled: {ctx}",
                f"sum(payments)={paid} but total-tip={round(tot-tip,2)}", ctx)

def _complete_appt_get_sale(F, tA, day_offset, hhmm="09:00"):
    """Create+complete an S1 appointment on a clean far-future day, return its sale dict."""
    A1 = F["A1"]
    d0 = (datetime.date.fromisoformat(F["future_date"]) + datetime.timedelta(days=day_offset)).isoformat()
    apt = body(call("POST", "/appointments/", token=tA, json_body={
        "agent_id": A1, "customer_id": F["C1"], "employee_id": F["E1"], "service_id": F["S1"],
        "start_time": f"{d0}T{hhmm}:00+00:00", "source": "manual"}))
    if not isinstance(apt, dict) or not apt.get("id"):
        return None
    call("PATCH", f"/appointments/{apt['id']}/status", token=tA, json_body={"status": "completed"})
    sales = body(call("GET", "/financials/sales", token=tA, params={"agent_id": A1}))
    if isinstance(sales, dict):
        for s in sales.get("sales", []):
            if any(li.get("appointment_id") == apt["id"] for li in s.get("line_items", [])):
                return s
    return None

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A1, B1 = F["A1"], F["B1"]
    q = {"agent_id": A1}

    # ---- RBAC + isolation on /sales ----
    ok(call("GET", "/financials/sales", token=tA, params=q), A, "list sales (owner)")
    if "view_financials" not in F["recep_perms"]:
        reject(call("GET", "/financials/sales", token=tR, params=q), A,
               "receptionist list sales", {403}, expect_code="PERMISSION_DENIED")
    reject(call("GET", "/financials/sales", token=tB, params={"agent_id": A1}), A,
           "B lists A's sales", {403, 404}, sev2xx="CRITICAL")
    reject(call("GET", "/financials/sales", token=tA), A, "list sales missing agent_id", {422})
    ok(call("GET", "/financials/reports/revenue", token=tA, params=q), A, "revenue report")

    # ---- base sale from a completed appointment (S1=40, tax 10%) ----
    sale = _complete_appt_get_sale(F, tA, 30)
    if not sale:
        finding("HIGH", A, "could not obtain a Sale from a completed appointment", "", "sale hook")
    else:
        SID = sale["id"]
        inv(sale, "auto-created sale")
        if abs((m(sale.get("subtotal")) or 0) - 40) > 0.01:
            finding("MEDIUM", A, "auto sale subtotal != service price",
                    f"subtotal={sale.get('subtotal')} expected 40", "sale")
        ok(call("GET", f"/financials/sales/{SID}", token=tA), A, "get sale by id")
        reject(call("GET", f"/financials/sales/{SID}", token=tB), A, "B reads A's sale", {403, 404}, sev2xx="CRITICAL")
        _discounts(F, tA, tR, SID)
        _payments(F, tA, SID)

    _commissions(F, tA, tR)
    _bundles_combos_packages(F, tA, tR)

def _discounts(F, tA, tR, SID):
    A1 = F["A1"]; q = {"agent_id": A1}
    try:
        # validation
        reject(call("POST", "/financials/discounts", token=tA, params=q,
                json_body={"name": "Bad%", "type": "percent", "value": 150, "scope": "order"}), A,
               "discount percent>100", {400, 422}, expect_code="INVALID_DISCOUNT_VALUE", sev2xx="HIGH")
        reject(call("POST", "/financials/discounts", token=tA, params=q,
                json_body={"name": "BadWin", "type": "fixed", "value": 5, "valid_from": "2026-12-01T00:00:00+00:00",
                           "valid_to": "2026-01-01T00:00:00+00:00"}), A,
               "discount valid_to<valid_from", {400, 422}, expect_code="INVALID_DISCOUNT_WINDOW", sev2xx="HIGH")
        if "manage_discounts" not in F["recep_perms"]:
            reject(call("POST", "/financials/discounts", token=tR, params=q,
                    json_body={"name": "x", "type": "percent", "value": 10}), A,
                   "receptionist create discount", {403})
        # create a 10% order discount
        disc = body(call("POST", "/financials/discounts", token=tA, params=q,
                json_body={"name": "TenOff", "type": "percent", "value": 10, "scope": "order"}))
        ok(call("GET", "/financials/discounts", token=tA, params=q), A, "list discounts")
        if isinstance(disc, dict) and disc.get("id"):
            did = disc["id"]
            # apply
            s2 = body(call("POST", f"/financials/sales/{SID}/discounts", token=tA, json_body={"discount_id": did}))
            inv(s2, "after 10% order discount applied")
            if isinstance(s2, dict):
                # tax should recompute on post-discount base: subtotal 40 -10% =36 -> tax 3.6 -> total 39.6
                if abs((m(s2.get("tax_total")) or 0) - 3.6) > 0.02:
                    finding("HIGH", A, "tax not recomputed on post-discount base",
                            f"tax_total={s2.get('tax_total')} expected ~3.6", "discount+tax")
                sd_id = (s2.get("discounts") or [{}])[0].get("id")
                # idempotency: apply same catalogue discount again
                reject(call("POST", f"/financials/sales/{SID}/discounts", token=tA, json_body={"discount_id": did}),
                       A, "apply same discount twice", {409, 400}, sev2xx="HIGH")
                # remove -> totals restore
                if sd_id:
                    s3 = body(call("DELETE", f"/financials/sales/{SID}/discounts/{sd_id}", token=tA))
                    inv(s3, "after discount removed")
                    if isinstance(s3, dict) and abs((m(s3.get("total")) or 0) - 44) > 0.02:
                        finding("HIGH", A, "totals did not restore after discount removal",
                                f"total={s3.get('total')} expected 44", "discount round-trip")
            # mutually-exclusive apply path: both catalogue + ad-hoc
            reject(call("POST", f"/financials/sales/{SID}/discounts", token=tA,
                    json_body={"discount_id": did, "type": "fixed", "value": 5}), A,
                   "discount apply both catalogue+adhoc", {400, 422}, sev2xx="HIGH")
            # PATCH the discount definition
            ok(call("PATCH", f"/financials/discounts/{did}", token=tA, json_body={"value": 15}), A,
               "patch discount definition")
            # cleanup discount def
            ok(call("DELETE", f"/financials/discounts/{did}", token=tA), A, "delete discount definition",
               codes=(200, 204))
    except Exception as e:
        finding("HIGH", A, f"discounts block crashed: {e}", traceback.format_exc(), "discounts")

def _payments(F, tA, SID):
    try:
        # partial then full (total now 44 after discount removed)
        s = body(call("POST", f"/financials/sales/{SID}/payments", token=tA,
                 json_body={"amount": 20, "method": "cash"}))
        inv(s, "after partial payment 20")
        if isinstance(s, dict) and s.get("status") not in ("partially_paid", "open", "paid"):
            finding("LOW", A, "unexpected status after partial payment", s.get("status"), "payment")
        s = body(call("POST", f"/financials/sales/{SID}/payments", token=tA,
                 json_body={"amount": 24, "method": "card", "tip_amount": 5}))
        inv(s, "after full payment + tip")
        if isinstance(s, dict) and s.get("status") != "paid":
            finding("MEDIUM", A, "sale not 'paid' after settling balance",
                    f"status={s.get('status')} total={s.get('total')}", "payment")
        reject(call("POST", "/financials/sales/99999999/payments", token=tA, json_body={"amount": 5}), A,
               "payment on unknown sale", {404})
        reject(call("POST", f"/financials/sales/{SID}/payments", token=tA, json_body={"amount": -5}), A,
               "negative payment amount", {400, 422}, sev2xx="HIGH")
    except Exception as e:
        finding("HIGH", A, f"payments block crashed: {e}", traceback.format_exc(), "payments")

def _commissions(F, tA, tR):
    A1 = F["A1"]; q = {"agent_id": A1}
    try:
        ok(call("GET", "/financials/commission-config", token=tA), A, "get commission config")
        ok(call("PATCH", "/financials/commission-config", token=tA, json_body={"commission_base": "gross"}), A,
           "set commission base gross")
        if "manage_commissions" not in F["recep_perms"]:
            reject(call("GET", "/financials/commission-config", token=tR), A,
                   "receptionist read commission config", {403})
        # validation
        reject(call("POST", "/financials/commission-rules", token=tA, params=q,
                json_body={"type": "percent", "value": 200, "employee_id": F["E1"]}), A,
               "commission percent>100", {400, 422}, expect_code="INVALID_COMMISSION_VALUE", sev2xx="HIGH")
        # create employee 10% rule
        rule = body(call("POST", "/financials/commission-rules", token=tA, params=q,
                json_body={"type": "percent", "value": 10, "employee_id": F["E1"]}))
        ok(call("GET", "/financials/commission-rules", token=tA, params=q), A, "list commission rules")
        if isinstance(rule, dict) and rule.get("id"):
            # duplicate identical rule -> 409
            reject(call("POST", "/financials/commission-rules", token=tA, params=q,
                    json_body={"type": "percent", "value": 10, "employee_id": F["E1"]}), A,
                   "duplicate commission rule", {409, 400}, sev2xx="HIGH")
            # NEW completed sale should now carry commission on the E1 line
            sale = _complete_appt_get_sale(F, tA, 31)
            if isinstance(sale, dict):
                inv(sale, "sale with commission rule active")
                li = next((l for l in sale.get("line_items", []) if l.get("employee_id") == F["E1"]), None)
                if li:
                    ca = m(li.get("commission_amount"))
                    if ca is None or abs(ca - 4.0) > 0.02:   # 10% of 40
                        finding("MEDIUM", A, "commission_amount not 10% of line base",
                                f"commission_amount={li.get('commission_amount')} expected ~4.0", "commission")
                # commission must NOT change customer-facing total
                if abs((m(sale.get("total")) or 0) - 44) > 0.02:
                    finding("HIGH", A, "commission changed the customer-facing total",
                            f"total={sale.get('total')} expected 44", "commission")
            ok(call("GET", "/financials/reports/commissions", token=tA, params=q), A, "commission report")
            call("DELETE", f"/financials/commission-rules/{rule['id']}", token=tA)
    except Exception as e:
        finding("HIGH", A, f"commissions block crashed: {e}", traceback.format_exc(), "commissions")

def _bundles_combos_packages(F, tA, tR):
    A1 = F["A1"]; q = {"agent_id": A1}
    try:
        # validation: combo with no items
        reject(call("POST", "/financials/bundles", token=tA, params=q,
                json_body={"kind": "combo", "name": "Empty", "price": 10, "items": []}), A,
               "combo bundle empty items", {400, 422}, sev2xx="HIGH")
        if "manage_discounts" not in F["recep_perms"]:
            reject(call("POST", "/financials/bundles", token=tR, params=q,
                    json_body={"kind": "combo", "name": "x", "price": 10,
                               "items": [{"service_id": F["S1"]}]}), A,
                   "receptionist create bundle", {403})
        # combo: S1(40)+S3(25)=65 list, price 60 -> 5 savings as line discounts
        combo = body(call("POST", "/financials/bundles", token=tA, params=q,
                json_body={"kind": "combo", "name": "Cut+Shave", "price": 60,
                           "items": [{"service_id": F["S1"], "qty": 1}, {"service_id": F["S3"], "qty": 1}]}))
        ok(call("GET", "/financials/bundles", token=tA, params=q), A, "list bundles")
        if isinstance(combo, dict) and combo.get("id"):
            ok(call("GET", f"/financials/bundles/{combo['id']}", token=tA), A, "get bundle")
            ok(call("PATCH", f"/financials/bundles/{combo['id']}", token=tA, json_body={"name": "Combo2"}), A,
               "patch bundle")
            # sell combo
            cs = body(call("POST", "/financials/sales/combo", token=tA, params=q,
                    json_body={"bundle_id": combo["id"], "customer_id": F["C1"],
                               "assignments": [{"employee_id": F["E1"]}, {"employee_id": F["E1"]}]}))
            inv(cs, "combo sale")
            if isinstance(cs, dict):
                if abs((m(cs.get("total")) or 0) - 60) > 0.5:   # combo price (+tax handled by inv)
                    # total includes tax; just sanity that discount_total ~5
                    pass
                if abs((m(cs.get("discount_total")) or 0) - 5) > 0.02:
                    finding("MEDIUM", A, "combo savings not allocated as discount_total",
                            f"discount_total={cs.get('discount_total')} expected ~5", "combo")
            # combo availability
            ok(call("GET", f"/financials/bundles/{combo['id']}/availability", token=tA,
               params={"date": F["future_date"]}), A, "combo availability")
        # package: sessions credit, 3x S1, price 100
        pkg = body(call("POST", "/financials/bundles", token=tA, params=q,
                json_body={"kind": "package", "name": "3 Cuts", "price": 100, "credit_kind": "sessions",
                           "items": [{"service_id": F["S1"], "qty": 3}]}))
        if isinstance(pkg, dict) and pkg.get("id"):
            ps = body(call("POST", "/financials/sales/package", token=tA, params=q,
                    json_body={"bundle_id": pkg["id"], "customer_id": F["C1"]}))
            inv(ps, "package purchase sale")
            if isinstance(ps, dict) and ps.get("id"):
                # pay it -> credit granted on payment
                paid = body(call("POST", f"/financials/sales/{ps['id']}/payments", token=tA,
                        json_body={"amount": ps.get("total"), "method": "cash"}))
                inv(paid, "package paid")
                creds = body(call("GET", "/financials/credits", token=tA,
                        params={"agent_id": A1, "customer_id": F["C1"]}))
                if isinstance(creds, dict):
                    has = any(c.get("bundle_id") == pkg["id"] for c in creds.get("credits", []))
                    if not has:
                        finding("MEDIUM", A, "package credit not granted after payment",
                                f"credits={creds.get('total')}", "package credit")
            ok(call("GET", "/financials/credits", token=tA, params={"agent_id": A1}), A, "list credits")
            _redeem(F, tA, pkg["id"])

        # ---- combo booking flow (POST /sales/combo/book) on a clean far date ----
        cb_day = (datetime.date.fromisoformat(F["future_date"]) + datetime.timedelta(days=33)).isoformat()
        if isinstance(combo, dict) and combo.get("id"):
            reject(call("POST", "/financials/sales/combo/book", token=tA,
                   json_body={"bundle_id": 99999999, "customer_id": F["C1"], "employee_id": F["E1"],
                              "start_time": f"{cb_day}T09:00:00+00:00"}), A,
                   "combo/book unknown bundle", {404})
            cb = body(call("POST", "/financials/sales/combo/book", token=tA,
                   json_body={"bundle_id": combo["id"], "customer_id": F["C1"], "employee_id": F["E1"],
                              "start_time": f"{cb_day}T09:00:00+00:00", "note": "combo"}))
            inv(cb, "combo/book sale")
            if isinstance(cb, dict):
                appt_ids = [li.get("appointment_id") for li in cb.get("line_items", []) if li.get("appointment_id")]
                if len(appt_ids) < 2:
                    finding("MEDIUM", A, "combo/book did not chain >=2 appointments",
                            f"line appt ids={appt_ids}", "combo/book")

        # ---- DELETE bundle (use a throwaway, unreferenced bundle) ----
        thr = body(call("POST", "/financials/bundles", token=tA, params=q,
                json_body={"kind": "combo", "name": "Throwaway", "price": 30,
                           "items": [{"service_id": F["S3"], "qty": 1}]}))
        if isinstance(thr, dict) and thr.get("id"):
            ok(call("DELETE", f"/financials/bundles/{thr['id']}", token=tA), A, "delete bundle",
               codes=(200, 204))
    except Exception as e:
        finding("HIGH", A, f"bundles/combos/packages block crashed: {e}", traceback.format_exc(), "bundles")

def _redeem(F, tA, pkg_id):
    """Redeem a package (sessions) credit against a fresh S1 sale line, then un-redeem."""
    A1 = F["A1"]
    try:
        # bad credit id
        sale = _complete_appt_get_sale(F, tA, 32)
        if not isinstance(sale, dict):
            return
        sid = sale["id"]
        line = next((l for l in sale.get("line_items", []) if l.get("service_id") == F["S1"]), None)
        reject(call("POST", f"/financials/sales/{sid}/redeem", token=tA,
               json_body={"credit_id": 99999999, "sale_line_item_id": line and line.get("id")}), A,
               "redeem unknown credit", {404, 400})
        # find a sessions credit for S1
        creds = body(call("GET", "/financials/credits", token=tA,
                params={"agent_id": A1, "customer_id": F["C1"], "active_only": True}))
        cred = None
        if isinstance(creds, dict):
            cred = next((c for c in creds.get("credits", [])
                         if c.get("kind") == "sessions" and (c.get("remaining_qty") or 0) > 0), None)
        if cred and line:
            before = cred.get("remaining_qty")
            r = body(call("POST", f"/financials/sales/{sid}/redeem", token=tA,
                     json_body={"credit_id": cred["id"], "sale_line_item_id": line["id"]}))
            inv(r, "after redeem (line free)")
            # idempotency: redeem same line again
            reject(call("POST", f"/financials/sales/{sid}/redeem", token=tA,
                   json_body={"credit_id": cred["id"], "sale_line_item_id": line["id"]}), A,
                   "redeem same line twice", {409, 400}, sev2xx="HIGH")
            # un-redeem (DELETE) — should restore the credit per the docs
            urr = call("DELETE", f"/financials/sales/{sid}/redeem/{line['id']}", token=tA)
            urb = body(urr)
            if urr.status_code == 409 and isinstance(urb, dict) and urb.get("code") == "SALE_NOT_EDITABLE":
                finding("MEDIUM", A,
                        "un-redeem unreachable: redeeming a single-line sale zeroes the balance and "
                        "auto-marks the sale PAID, so DELETE /sales/{id}/redeem/{line} returns 409 "
                        "SALE_NOT_EDITABLE and the decremented credit canNOT be restored via the API — "
                        "contradicts the documented 'un-redeem restores the credit' behavior.",
                        f"status=409 body={urb}", "redeem un-redeem")
            elif urr.status_code in (200, 201):
                inv(urb, "after un-redeem")
                creds2 = body(call("GET", "/financials/credits", token=tA,
                        params={"agent_id": A1, "customer_id": F["C1"], "active_only": True}))
                if isinstance(creds2, dict):
                    c2 = next((c for c in creds2.get("credits", []) if c.get("id") == cred["id"]), None)
                    if c2 and before is not None and c2.get("remaining_qty") != before:
                        finding("MEDIUM", A, "credit not restored after un-redeem",
                                f"before={before} after={c2.get('remaining_qty')}", "redeem round-trip")
            else:
                finding("LOW", A, f"un-redeem unexpected status {urr.status_code}", urb, "redeem")
        else:
            finding("INFO", A, "no sessions credit/line available to test redeem", "", "redeem")
    except Exception as e:
        finding("HIGH", A, f"redeem block crashed: {e}", traceback.format_exc(), "redeem")
