"""
tests/conftest.py

Test harness for the website-builder + public booking work.

Strategy: each test runs inside ONE outer transaction on a real connection and is
**rolled back** at teardown, so tests leave the dev DB untouched. The session uses
`join_transaction_mode="create_savepoint"` so code under test that calls
`db.commit()` (e.g. create_appointment) commits into a savepoint rather than the
real transaction — and the outer rollback still undoes everything.

`client` overrides `get_db` to hand the endpoint the same rolled-back session, and
is built WITHOUT entering the app lifespan (so the APScheduler + RBAC sync never
start during tests).
"""

import uuid
from datetime import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import engine, get_db
from app.main import create_app
from app.api.v1.endpoints import public as public_endpoint
from app.models.models import (
    BusinessType, Business, AIAgent, Employee, EmployeeBranch,
    EmployeeWorkingHour, BusinessHour, Service, EmployeeCapability,
)


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """The booking rate-limiter is a module-global in-memory dict; clear it so one
    test's requests don't trip another's limit."""
    public_endpoint._rate_buckets.clear()
    yield
    public_endpoint._rate_buckets.clear()


@pytest.fixture(autouse=True)
def _no_sms(monkeypatch):
    """Never send a real SMS in tests. create_appointment imports send_confirmation
    from the module at call time, so patching the module attribute is enough."""
    import app.services.notification_service as ns
    monkeypatch.setattr(ns, "send_confirmation", lambda *a, **k: None, raising=False)


@pytest.fixture
def client(db):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    # No `with` → lifespan (scheduler / rbac sync) does not run.
    return TestClient(app)


class Branch:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def bookable_branch(db) -> Branch:
    """Seed the minimal graph for a bookable branch: business → agent (with slug) →
    employee (assigned, working all week) → active service the employee can do, with
    branch hours open all week. Returns the ids + slug."""
    uniq = uuid.uuid4().hex[:10]

    bt = BusinessType(name=f"Salon {uniq}")
    db.add(bt); db.flush()

    biz = Business(
        name=f"Biz {uniq}", business_type_id=bt.id,
        email=f"{uniq}@test.local", password_hash="x", phone=f"+1{uniq[:9]}",
    )
    db.add(biz); db.flush()

    agent = AIAgent(
        business_id=biz.id, agent_name=f"Branch {uniq}", phone_number=f"+1999{uniq[:7]}",
        location_name="Test City", timezone="UTC", model="claude-sonnet-4-6",
        booking_page_slug=f"test-{uniq}", active=True,
    )
    db.add(agent); db.flush()

    emp = Employee(name=f"Stylist {uniq}")
    db.add(emp); db.flush()
    db.add(EmployeeBranch(employee_id=emp.id, agent_id=agent.id))  # active + bookable default True

    svc = Service(agent_id=agent.id, name="Haircut", price=50, duration_minutes=30, active=True)
    db.add(svc); db.flush()
    db.add(EmployeeCapability(employee_id=emp.id, service_id=svc.id))

    # Open + working all 7 days, wide window, so a slot always exists a few days out.
    for dow in range(7):
        db.add(BusinessHour(agent_id=agent.id, day_of_week=dow, open_time=time(0, 0), closed_time=time(23, 59)))
        db.add(EmployeeWorkingHour(employee_id=emp.id, agent_id=agent.id, day_of_week=dow,
                                   start_time=time(0, 0), end_time=time(23, 59)))
    db.flush()

    return Branch(
        business_id=biz.id, agent_id=agent.id, slug=agent.booking_page_slug,
        service_id=svc.id, employee_id=emp.id,
    )
