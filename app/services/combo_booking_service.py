"""
app/services/combo_booking_service.py

Combo booking — find back-to-back slots for a combo bundle and book it as a
chained set of appointments + one bundled-price sale.

v1 scope (chosen with the user): **one stylist per combo** — the whole combo is
performed by a single employee as one continuous block. This collapses the
multi-employee scheduling search and the inter-service prep-buffer question into
a single block:
  - the employee must be capable of EVERY item, assigned + active + bookable at
    the branch, working the whole block, off no day-off;
  - the block `[start, start + Σ durations]` must fit branch ∩ employee hours and
    clear existing appointments (prep-aware) + blocked times;
  - **prep buffers apply only at the OUTER edges** of the combo (the first item's
    prep_before, the last item's prep_after) — services within the combo run
    contiguously for the same person.

Booking is atomic: all appointments + the combo sale commit together, or nothing.
Channels: staff/dashboard only for now (public page + AI tools deferred).
"""

from datetime import datetime, date, time, timedelta
from typing import Optional
import pytz

from sqlalchemy import or_, func
from sqlalchemy.orm import Session, joinedload
from loguru import logger

from app.models.models import (
    AIAgent,
    Appointment,
    AppointmentSourceEnum,
    AppointmentStatusEnum,
    BlockedTime,
    Bundle,
    BundleKindEnum,
    BusinessHour,
    Customer,
    DayOff,
    Employee,
    EmployeeBranch,
    EmployeeCapability,
    EmployeeWorkingHour,
    Service,
    ServiceVariation,
)
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
    ConflictException,
    SlotUnavailableException,
    AppException,
)
from app.services import financial_service
from app.services.availability_service import (
    SLOT_INTERVAL_MINUTES,
    MIN_ADVANCE_MINUTES,
    MAX_ADVANCE_DAYS,
    _resolve_duration,
    _overlaps_appointments,
    _overlaps_blocked_times,
    _ensure_tz_aware,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Branch not found", code="AGENT_NOT_FOUND")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this branch")
    return agent


def _tz_for(agent: AIAgent):
    try:
        return pytz.timezone(agent.timezone)
    except pytz.exceptions.UnknownTimeZoneError:
        return pytz.UTC


def _capable_employee_ids(db: Session, service: Service, variation_id: Optional[int]) -> set:
    """Employee ids capable of a service (granted at variation / service / title)."""
    variation_ids = db.query(ServiceVariation.id).filter(
        ServiceVariation.service_id == service.id
    ).scalar_subquery()
    conditions = [
        EmployeeCapability.service_id == service.id,
        EmployeeCapability.variation_id.in_(variation_ids),
    ]
    if service.title_id:
        conditions.append(EmployeeCapability.title_id == service.title_id)
    rows = db.query(EmployeeCapability.employee_id).filter(or_(*conditions)).all()
    return {r[0] for r in rows}


def _resolve_combo_items(db: Session, bundle: Bundle) -> list:
    """Ordered combo items resolved to (service, variation_id, duration, names)."""
    items = []
    for it in sorted(bundle.items, key=lambda i: i.id):
        if it.variation_id is not None:
            variation = db.query(ServiceVariation).filter(ServiceVariation.id == it.variation_id).first()
            service = db.query(Service).filter(Service.id == variation.service_id).first() if variation else None
            variation_name = variation.name if variation else None
        else:
            service = db.query(Service).filter(Service.id == it.service_id).first()
            variation_name = None
        if not service:
            raise NotFoundException(detail="A combo service no longer exists", code="SERVICE_NOT_FOUND")
        duration = _resolve_duration(db, service, it.variation_id)
        items.append({
            "service": service,
            "service_id": service.id,
            "variation_id": it.variation_id,
            "duration": duration,
            "service_name": service.name,
            "variation_name": variation_name,
        })
    return items


def _eligible_employees(db: Session, agent_id: int, items: list, only_employee_id: Optional[int] = None) -> list:
    """Employees capable of EVERY item and assigned + active + bookable at the branch."""
    capable = None
    for item in items:
        ids = _capable_employee_ids(db, item["service"], item["variation_id"])
        capable = ids if capable is None else (capable & ids)
        if not capable:
            return []

    q = (
        db.query(Employee)
        .join(EmployeeBranch, EmployeeBranch.employee_id == Employee.id)
        .filter(
            EmployeeBranch.agent_id == agent_id,
            EmployeeBranch.active.is_(True),
            EmployeeBranch.can_receive_bookings.is_(True),
            Employee.id.in_(capable),
        )
    )
    if only_employee_id is not None:
        q = q.filter(Employee.id == only_employee_id)
    return q.all()


def _branch_window(db: Session, agent_id: int, requested_date: date, tz):
    """(branch_open, branch_close) tz-aware for the date, or None if closed/day-off."""
    bh = db.query(BusinessHour).filter(
        BusinessHour.agent_id == agent_id,
        BusinessHour.day_of_week == requested_date.weekday(),
    ).first()
    if not bh:
        return None
    day_off = db.query(DayOff).filter(
        DayOff.agent_id == agent_id,
        DayOff.employee_id == None,  # noqa: E711
        DayOff.start_date <= requested_date,
        DayOff.end_date >= requested_date,
    ).first()
    if day_off:
        return None
    return (
        tz.localize(datetime.combine(requested_date, bh.open_time)),
        tz.localize(datetime.combine(requested_date, bh.closed_time)),
    )


def _employee_block_starts(
    db: Session, agent_id: int, employee, requested_date: date,
    total_minutes: int, prep_before: int, prep_after: int,
    branch_open: datetime, branch_close: datetime, tz,
) -> list:
    """All valid combo-block START times (tz-aware) for one employee on a date."""
    working = db.query(EmployeeWorkingHour).filter(
        EmployeeWorkingHour.employee_id == employee.id,
        EmployeeWorkingHour.agent_id == agent_id,
        EmployeeWorkingHour.day_of_week == requested_date.weekday(),
    ).first()
    if not working:
        return []

    personal_off = db.query(DayOff).filter(
        DayOff.employee_id == employee.id,
        DayOff.start_date <= requested_date,
        DayOff.end_date >= requested_date,
    ).first()
    if personal_off:
        return []

    emp_start = tz.localize(datetime.combine(requested_date, working.start_time))
    emp_end = tz.localize(datetime.combine(requested_date, working.end_time))
    effective_start = max(emp_start, branch_open)
    effective_end = min(emp_end, branch_close)
    if effective_start >= effective_end:
        return []

    day_start = tz.localize(datetime.combine(requested_date, time(0, 0, 0)))
    day_end = tz.localize(datetime.combine(requested_date, time(23, 59, 59)))
    existing = (
        db.query(Appointment)
        .options(joinedload(Appointment.service))
        .filter(
            Appointment.employee_id == employee.id,
            Appointment.start_time >= day_start,
            Appointment.start_time <= day_end,
            Appointment.status.notin_(["cancelled", "no_show"]),
        )
        .all()
    )
    blocked = db.query(BlockedTime).filter(
        BlockedTime.employee_id == employee.id,
        BlockedTime.agent_id == agent_id,
        BlockedTime.day_of_week == requested_date.weekday(),
    ).all()

    earliest = datetime.now(tz) + timedelta(minutes=MIN_ADVANCE_MINUTES)
    block_td = timedelta(minutes=total_minutes)
    prep_after_td = timedelta(minutes=prep_after)
    step = timedelta(minutes=SLOT_INTERVAL_MINUTES)

    starts = []
    current = effective_start
    # The whole block + outer post-prep must fit within the working window.
    while current + block_td + prep_after_td <= effective_end:
        block_end = current + block_td
        if current < earliest:
            current += step
            continue
        # outer prep only — the combo runs contiguously for one person
        if _overlaps_appointments(current, block_end, existing, tz,
                                  prep_before_minutes=prep_before, prep_after_minutes=prep_after):
            current += step
            continue
        if _overlaps_blocked_times(current, block_end, blocked, requested_date, tz):
            current += step
            continue
        starts.append(current)
        current += step
    return starts


# ─────────────────────────────────────────────────────────────────────────────
# Read — combo availability
# ─────────────────────────────────────────────────────────────────────────────

def get_combo_availability(
    db: Session, agent_id: int, business_id: int, bundle_id: int,
    requested_date: date, employee_id: Optional[int] = None,
) -> dict:
    """Bookable combo options on a date (single employee per combo), each with
    per-item sub-times. Across all eligible employees, sorted by start."""
    agent = _get_agent_for_business(db, agent_id, business_id)
    tz = _tz_for(agent)
    bundle = (
        db.query(Bundle).filter(Bundle.id == bundle_id, Bundle.agent_id == agent_id).first()
    )
    if not bundle:
        raise NotFoundException(detail="Bundle not found", code="BUNDLE_NOT_FOUND")
    if bundle.kind != BundleKindEnum.combo:
        raise AppException(detail="This bundle is not a combo", code="NOT_A_COMBO", status_code=400)

    items = _resolve_combo_items(db, bundle)
    total_minutes = sum(i["duration"] for i in items)
    prep_before = items[0]["service"].preparation_before_minutes or 0
    prep_after = items[-1]["service"].preparation_after_minutes or 0

    # Employees who could perform the WHOLE combo (capable of every item +
    # active + bookable at the branch). Date-independent — compute once so the
    # caller can tell "nobody can do this combo" apart from "no free times today".
    eligible = _eligible_employees(db, agent_id, items, only_employee_id=employee_id)

    local_today = datetime.now(tz).date()
    options: list = []
    window = None
    if local_today <= requested_date <= local_today + timedelta(days=MAX_ADVANCE_DAYS):
        window = _branch_window(db, agent_id, requested_date, tz)

    if window:
        branch_open, branch_close = window
        for emp in eligible:
            for start in _employee_block_starts(
                db, agent_id, emp, requested_date, total_minutes, prep_before, prep_after,
                branch_open, branch_close, tz,
            ):
                sub = []
                cursor = start
                for item in items:
                    s_end = cursor + timedelta(minutes=item["duration"])
                    sub.append({
                        "service_id": item["service_id"],
                        "variation_id": item["variation_id"],
                        "service_name": item["service_name"],
                        "variation_name": item["variation_name"],
                        "start_time": cursor,
                        "end_time": s_end,
                        "duration_minutes": item["duration"],
                    })
                    cursor = s_end
                options.append({
                    "employee_id": emp.id,
                    "employee_name": emp.name,
                    "start_time": start,
                    "end_time": start + timedelta(minutes=total_minutes),
                    "total_duration_minutes": total_minutes,
                    "items": sub,
                })
        options.sort(key=lambda o: (o["start_time"], o["employee_id"]))

    return {
        "agent_id": agent_id,
        "bundle_id": bundle_id,
        "bundle_name": bundle.name,
        "date": requested_date,
        "total_duration_minutes": total_minutes,
        "eligible_employees": len(eligible),
        "total_options": len(options),
        "options": options,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write — book a combo
# ─────────────────────────────────────────────────────────────────────────────

def book_combo(
    db: Session, agent_id: int, business_id: int, bundle_id: int,
    customer_id: int, employee_id: int, start_time: datetime, note: Optional[str] = None,
) -> "Sale":
    """
    Book a combo as a chained set of appointments performed by one employee, then
    create the linked bundled-price sale — all in ONE transaction (all-or-nothing).
    """
    agent = _get_agent_for_business(db, agent_id, business_id)
    tz = _tz_for(agent)
    bundle = (
        db.query(Bundle).filter(Bundle.id == bundle_id, Bundle.agent_id == agent_id).first()
    )
    if not bundle:
        raise NotFoundException(detail="Bundle not found", code="BUNDLE_NOT_FOUND")
    if bundle.kind != BundleKindEnum.combo:
        raise AppException(detail="This bundle is not a combo", code="NOT_A_COMBO", status_code=400)
    if not bundle.active:
        raise ConflictException(detail="This combo is inactive", code="BUNDLE_INACTIVE")

    customer = db.query(Customer).filter(
        Customer.id == customer_id, Customer.business_id == business_id
    ).first()
    if not customer:
        raise NotFoundException(detail="Customer not found", code="CUSTOMER_NOT_FOUND")

    items = _resolve_combo_items(db, bundle)
    total_minutes = sum(i["duration"] for i in items)
    prep_before = items[0]["service"].preparation_before_minutes or 0
    prep_after = items[-1]["service"].preparation_after_minutes or 0

    # The chosen employee must be eligible for the whole combo.
    eligible = _eligible_employees(db, agent_id, items, only_employee_id=employee_id)
    if not eligible:
        raise ConflictException(
            detail="That employee cannot perform every service in this combo at this branch",
            code="EMPLOYEE_NOT_ELIGIBLE",
        )
    employee = eligible[0]

    start_aware = _ensure_tz_aware(start_time, tz)
    requested_date = start_aware.astimezone(tz).date()

    window = _branch_window(db, agent_id, requested_date, tz)
    if not window:
        raise SlotUnavailableException(detail="Branch is closed on this date", code="BRANCH_DAY_OFF")
    branch_open, branch_close = window

    # Re-validate against the exact same generator the availability search uses —
    # the requested start must be one of the bookable block starts.
    valid_starts = _employee_block_starts(
        db, agent_id, employee, requested_date, total_minutes, prep_before, prep_after,
        branch_open, branch_close, tz,
    )
    target = start_aware.astimezone(tz)
    if not any(abs((s - target).total_seconds()) < 1 for s in valid_starts):
        raise SlotUnavailableException(
            detail="That combo time is no longer available",
            code="SLOT_UNAVAILABLE",
        )

    # Build the chained appointments (confirmed) — no per-item commit; one
    # transaction so a failure leaves nothing behind.
    appointments = []
    cursor = target
    for item in items:
        end = cursor + timedelta(minutes=item["duration"])
        appt = Appointment(
            agent_id=agent_id,
            customer_id=customer_id,
            employee_id=employee.id,
            service_id=item["service_id"],
            variation_id=item["variation_id"],
            customer_name=customer.name,
            customer_phone=customer.phone,
            employee_name=employee.name,
            service_name=item["service_name"],
            variation_name=item["variation_name"],
            start_time=cursor,
            end_time=end,
            status=AppointmentStatusEnum.confirmed,
            source=AppointmentSourceEnum.manual,
            note=note,
        )
        db.add(appt)
        appointments.append(appt)
        cursor = end

    db.flush()  # assign appointment ids (still uncommitted)

    # Create the bundled-price sale linked to these appointments. create_combo_sale
    # commits — which commits the appointments in the same transaction too.
    from app.schemas.financial import ComboSaleRequest, ComboItemAssignment
    sale = financial_service.create_combo_sale(
        db, agent_id, business_id,
        ComboSaleRequest(
            bundle_id=bundle_id,
            customer_id=customer_id,
            assignments=[ComboItemAssignment(employee_id=employee.id, appointment_id=a.id) for a in appointments],
        ),
    )

    # One best-effort confirmation SMS for the whole combo (not one per service).
    try:
        from app.services.notification_service import send_confirmation
        send_confirmation(db, appointments[0].id)
    except Exception as e:
        logger.warning(f"Combo confirmation SMS failed (sale={sale.id}): {e}")

    logger.info(
        f"Combo booked: bundle={bundle_id} employee={employee.id} customer={customer_id} "
        f"appts={[a.id for a in appointments]} sale={sale.id} at={target}"
    )
    return sale
