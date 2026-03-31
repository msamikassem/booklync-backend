"""
app/services/business_hours_service.py

All business logic for business hours and agent-level days off.

Business hours define the regular weekly opening schedule.
Agent days off define branch-wide closures.

Both feed into the availability engine to determine bookable slots.
"""

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import AIAgent, BusinessHour, DayOff
from app.schemas.business_hours import BusinessHoursSet, AgentDayOffCreate
from app.core.exceptions import (
    NotFoundException,
    ForbiddenException,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER — verify agent ownership
# ─────────────────────────────────────────────────────────────────────────────

def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """
    Fetch an agent and verify it belongs to the logged in business.

    Raises:
        NotFoundException:  agent not found
        ForbiddenException: agent belongs to different business
    """
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()

    if not agent:
        raise NotFoundException(detail="Agent not found")

    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS HOURS
# ─────────────────────────────────────────────────────────────────────────────

def set_business_hours(
    db: Session,
    data: BusinessHoursSet,
    business_id: int,
) -> dict:
    """
    Set the weekly opening schedule for an agent.

    This REPLACES the existing schedule entirely.
    Days not included are treated as closed.

    Steps:
    1. Verify agent belongs to logged in business
    2. Delete all existing business hours for this agent
    3. Insert the new schedule

    Args:
        db:          database session
        data:        validated BusinessHoursSet schema
        business_id: id of the logged in business

    Returns:
        dict with new business hours list and total
    """

    # Step 1 — verify ownership
    _get_agent_for_business(db, data.agent_id, business_id)

    # Step 2 — delete all existing hours for this agent
    db.query(BusinessHour).filter(
        BusinessHour.agent_id == data.agent_id
    ).delete()

    # Step 3 — insert the new schedule
    new_hours = []
    for bh in data.business_hours:
        business_hour = BusinessHour(
            agent_id=data.agent_id,
            day_of_week=bh.day_of_week,
            open_time=bh.open_time,
            closed_time=bh.closed_time,
        )
        db.add(business_hour)
        new_hours.append(business_hour)

    db.commit()

    # refresh all to get auto-generated ids
    for bh in new_hours:
        db.refresh(bh)

    logger.info(
        f"Business hours set for agent_id={data.agent_id}: "
        f"{len(new_hours)} days configured"
    )

    return {
        "business_hours": new_hours,
        "total": len(new_hours),
    }


def get_business_hours(
    db: Session,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get the weekly opening schedule for an agent.

    Returns hours ordered by day of week (Monday first).

    Args:
        db:          database session
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with business hours list and total
    """

    _get_agent_for_business(db, agent_id, business_id)

    hours = db.query(BusinessHour).filter(
        BusinessHour.agent_id == agent_id
    ).order_by(BusinessHour.day_of_week).all()

    return {
        "business_hours": hours,
        "total": len(hours),
    }


def delete_business_hour(
    db: Session,
    business_hour_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Delete a specific day's business hours.

    Effectively marks that day as closed.
    For example deleting Sunday's hours means Sunday is now closed.

    Args:
        db:               database session
        business_hour_id: id of the business hour entry to delete
        agent_id:         id of the agent
        business_id:      id of the logged in business

    Returns:
        dict with success message

    Raises:
        NotFoundException: business hour entry not found
    """

    _get_agent_for_business(db, agent_id, business_id)

    hour = db.query(BusinessHour).filter(
        BusinessHour.id == business_hour_id,
        BusinessHour.agent_id == agent_id,
    ).first()

    if not hour:
        raise NotFoundException(detail="Business hour entry not found")

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_name = day_names[hour.day_of_week]

    db.delete(hour)
    db.commit()

    logger.info(
        f"Business hour deleted: id={business_hour_id} "
        f"({day_name}) for agent_id={agent_id}"
    )

    return {"message": f"{day_name} hours removed — day is now closed"}


# ─────────────────────────────────────────────────────────────────────────────
# AGENT DAYS OFF (branch-wide closures)
# ─────────────────────────────────────────────────────────────────────────────

def add_agent_day_off(
    db: Session,
    data: AgentDayOffCreate,
    business_id: int,
) -> DayOff:
    """
    Add a branch-wide closure period.

    This applies to ALL employees under the agent.
    No bookings can be made during this period regardless of
    individual employee availability.

    employee_id is intentionally left as NULL to indicate
    this is a branch-wide closure, not a personal day off.

    Args:
        db:          database session
        data:        validated AgentDayOffCreate schema
        business_id: id of the logged in business

    Returns:
        DayOff SQLAlchemy object
    """

    _get_agent_for_business(db, data.agent_id, business_id)

    day_off = DayOff(
        agent_id=data.agent_id,
        employee_id=None,        # NULL = branch-wide, not personal
        name=data.name,
        start_date=data.start_date,
        end_date=data.end_date,
    )

    db.add(day_off)
    db.commit()
    db.refresh(day_off)

    logger.info(
        f"Agent day off added: agent_id={data.agent_id} "
        f"{data.start_date} to {data.end_date} ({data.name or 'unnamed'})"
    )

    return day_off


def get_agent_days_off(
    db: Session,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Get all branch-wide closure periods for an agent.

    Only returns agent-level days off (employee_id IS NULL).
    Personal employee days off are not included here —
    they are managed through the employee endpoints.

    Args:
        db:          database session
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with days off list and total
    """

    _get_agent_for_business(db, agent_id, business_id)

    days_off = db.query(DayOff).filter(
        DayOff.agent_id == agent_id,
        DayOff.employee_id == None,    # only branch-wide, not personal
    ).order_by(DayOff.start_date).all()

    return {
        "days_off": days_off,
        "total": len(days_off),
    }


def delete_agent_day_off(
    db: Session,
    day_off_id: int,
    agent_id: int,
    business_id: int,
) -> dict:
    """
    Remove a branch-wide closure period.

    Args:
        db:          database session
        day_off_id:  id of the day off to remove
        agent_id:    id of the agent
        business_id: id of the logged in business

    Returns:
        dict with success message

    Raises:
        NotFoundException: day off not found
    """

    _get_agent_for_business(db, agent_id, business_id)

    day_off = db.query(DayOff).filter(
        DayOff.id == day_off_id,
        DayOff.agent_id == agent_id,
        DayOff.employee_id == None,    # only agent-level days off
    ).first()

    if not day_off:
        raise NotFoundException(detail="Day off not found")

    name = day_off.name or "Unnamed closure"

    db.delete(day_off)
    db.commit()

    logger.info(f"Agent day off deleted: id={day_off_id} for agent_id={agent_id}")

    return {"message": f"'{name}' removed successfully"}