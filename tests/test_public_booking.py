"""Regression tests for the public booking endpoint (POST /public/appointments).

Locks in the behaviour verified manually: pending default, per-branch auto-confirm,
honeypot, slot-taken conflict, and rate limiting. Reuses the validated create path
so these also guard against bypasses there.
"""

from datetime import date, timedelta

from app.models.models import AIAgent
from app.services import availability_service

URL = "/api/v1/public/appointments"


def _first_slot(db, branch):
    """A real bookable (employee_id, start_time ISO) a few days out."""
    for dd in range(2, 10):
        d = date.today() + timedelta(days=dd)
        resp = availability_service.get_public_availability(db, branch.agent_id, branch.service_id, d, None)
        if resp.available_slots:
            s = resp.available_slots[0]
            return s.employee_id, s.start_time.isoformat()
    raise AssertionError("no bookable slot found for seeded branch")


def _body(branch, emp, st, **over):
    body = {
        "slug": branch.slug, "service_id": branch.service_id, "employee_id": emp,
        "start_time": st, "customer_name": "Test Customer", "customer_phone": "+15550100000",
    }
    body.update(over)
    return body


def test_booking_lands_pending_by_default(client, db, bookable_branch):
    emp, st = _first_slot(db, bookable_branch)
    r = client.post(URL, json=_body(bookable_branch, emp, st, customer_phone="+15550100001"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["pending"] is True


def test_auto_confirm_when_enabled(client, db, bookable_branch):
    db.query(AIAgent).filter(AIAgent.id == bookable_branch.agent_id).update(
        {"public_booking_auto_confirm": True}
    )
    db.flush()
    emp, st = _first_slot(db, bookable_branch)
    r = client.post(URL, json=_body(bookable_branch, emp, st, customer_phone="+15550100002"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"


def test_honeypot_rejected(client, db, bookable_branch):
    emp, st = _first_slot(db, bookable_branch)
    r = client.post(URL, json=_body(bookable_branch, emp, st, hp="i-am-a-bot", customer_phone="+15550100003"))
    assert r.status_code == 400
    assert r.json()["code"] == "BOOKING_REJECTED"


def test_unknown_slug_404(client, db, bookable_branch):
    emp, st = _first_slot(db, bookable_branch)
    r = client.post(URL, json=_body(bookable_branch, emp, st, slug="no-such-branch", customer_phone="+15550100007"))
    assert r.status_code == 404


def test_slot_taken_conflict(client, db, bookable_branch):
    emp, st = _first_slot(db, bookable_branch)
    r1 = client.post(URL, json=_body(bookable_branch, emp, st, customer_phone="+15550100004"))
    assert r1.status_code == 200, r1.text
    # Same slot, different customer → must be rejected.
    r2 = client.post(URL, json=_body(bookable_branch, emp, st, customer_phone="+15550100005"))
    assert r2.status_code == 409


def test_rate_limited_after_burst(client, db, bookable_branch):
    emp, st = _first_slot(db, bookable_branch)
    codes = [
        client.post(URL, json=_body(bookable_branch, emp, st, customer_phone="+15550100006")).status_code
        for _ in range(6)
    ]
    assert 429 in codes  # limit is 5 per window
