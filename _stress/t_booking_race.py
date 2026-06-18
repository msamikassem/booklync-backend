"""Concurrency: double-booking race on the appointment write path (TOCTOU).

Regression for a CRITICAL bug found by stress testing: N concurrent bookings for
the same slot all passed the availability check (each on its own connection,
none committed yet) and all inserted -> multiple appointments for one
employee+time. Fixed by a per-employee Postgres advisory xact lock in
create_appointment (+ batch / update / combo paths). Expect exactly ONE success.
"""
import concurrent.futures as cf
from collections import Counter

from core import call, body, finding

A = "concurrency/booking"


def _first_slot(F):
    r = body(call("GET", "/availability/public/date",
                  params={"agent_id": F["A1"], "service_id": F["S1"], "date": F["future_date"]})) or {}
    slots = r.get("available_slots") or []
    if not slots:
        return None, None
    return slots[0]["employee_id"], slots[0]["start_time"]


def run(F):
    tA = F["A_token"]

    # ---- authed POST /appointments: 12 concurrent for the SAME slot ----
    emp, start = _first_slot(F)
    if not start:
        finding("INFO", A, "no slot available to race-test (skipping)")
        return

    def book_authed(_i):
        return call("POST", "/appointments/", token=tA, json_body={
            "agent_id": F["A1"], "customer_id": F["C1"], "employee_id": emp,
            "service_id": F["S1"], "start_time": start, "source": "manual",
        }).status_code

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        codes = list(ex.map(book_authed, range(12)))
    succ = sum(c in (200, 201) for c in codes)
    if succ != 1:
        finding("CRITICAL", A, f"double-booking race: {succ} concurrent bookings won the SAME slot (expect 1)",
                dict(Counter(codes)), f"authed /appointments emp={emp} {start}")
    else:
        finding("INFO", A, "authed concurrent same-slot -> exactly 1 success (race guarded)", dict(Counter(codes)))

    # ---- public POST /public/appointments: 4 concurrent (under the 5/window rate limit) ----
    slug = (body(call("GET", f"/agents/{F['A1']}", token=tA)) or {}).get("booking_page_slug")
    pemp, pstart = _first_slot(F)  # fresh first slot (authed test took one)
    if not (slug and pstart):
        return

    def book_public(i):
        return call("POST", "/public/appointments", json_body={
            "slug": slug, "service_id": F["S1"], "employee_id": pemp, "start_time": pstart,
            "customer_name": f"Race {i}", "customer_phone": f"+1644{i:07d}",
        }).status_code

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        pcodes = list(ex.map(book_public, range(4)))
    psucc = sum(c in (200, 201) for c in pcodes)
    if psucc > 1:
        finding("CRITICAL", A, f"double-booking race (public): {psucc} concurrent bookings won the SAME slot",
                dict(Counter(pcodes)), f"public /appointments emp={pemp} {pstart}")
    else:
        finding("INFO", A, "public concurrent same-slot -> <=1 success (race guarded)", dict(Counter(pcodes)))
