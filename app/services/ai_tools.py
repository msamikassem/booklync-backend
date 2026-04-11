"""
app/services/ai_tools.py

AI tool definitions and executor.

Two things live here:
    1. TOOLS — the list of tool definitions sent to the AI
    2. execute_tool() — routes tool calls to the right service function

Keeping tools separate from the AI runner means:
    - Easy to add or remove tools without touching the AI logic
    - Easy to read what tools are available at a glance
    - Each tool is self-contained with its own description and schema
"""

import json
from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS
# Sent to the AI so it knows what functions it can call.
# Format follows Anthropic's tool spec (identical to OpenAI function calling).
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_current_time",
        "description": (
            "Get the current local time for this branch. "
            "Always call this first before checking availability or making bookings "
            "so you know the current date and whether the branch is open right now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_services",
        "description": (
            "Get the list of all services this business offers — "
            "names, durations, and prices. "
            "Call this when the customer asks what services are available "
            "or when you need a service_id to check availability or book."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Get all available time slots for a specific service on a given date. "
            "Returns slots in local time with a UTC field for booking. "
            "Use this to show the customer what times are open."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "integer",
                    "description": "The ID of the service to check availability for.",
                },
                "date": {
                    "type": "string",
                    "description": "The date to check in YYYY-MM-DD format e.g. '2025-07-01'.",
                },
                "employee_id": {
                    "type": "integer",
                    "description": "Optional. Filter to a specific employee if the customer has a preference.",
                },
            },
            "required": ["service_id", "date"],
        },
    },
    {
        "name": "check_specific_slot",
        "description": (
            "Check if a specific date and time is available. "
            "If not available, returns the closest alternatives before and after. "
            "Use this when the customer requests a specific time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "integer",
                    "description": "The ID of the service.",
                },
                "start_time_utc": {
                    "type": "string",
                    "description": (
                        "The requested start time in UTC ISO 8601 format "
                        "e.g. '2025-07-01T14:00:00+00:00'. "
                        "Convert the customer's local time to UTC using the branch timezone."
                    ),
                },
                "employee_id": {
                    "type": "integer",
                    "description": "Optional. Specific employee preference.",
                },
            },
            "required": ["service_id", "start_time_utc"],
        },
    },
    {
        "name": "create_appointment",
        "description": (
            "Book an appointment. "
            "Only call this after the slot is confirmed available AND "
            "the customer has explicitly said yes to booking. "
            "Never book without clear customer confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "integer",
                    "description": "The ID of the service to book.",
                },
                "employee_id": {
                    "type": "integer",
                    "description": "The ID of the employee to book with.",
                },
                "start_time_utc": {
                    "type": "string",
                    "description": (
                        "Appointment start time in UTC ISO 8601. "
                        "Use start_time_utc from the availability result."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional note e.g. customer preferences or special requests.",
                },
            },
            "required": ["service_id", "employee_id", "start_time_utc"],
        },
    },
    {
        "name": "get_customer_appointments",
        "description": (
            "Get the current customer's appointment history. "
            "Use this to check upcoming bookings, confirm details, "
            "or find an appointment ID before cancelling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "cancel_appointment",
        "description": (
            "Cancel an existing appointment. "
            "Always call get_customer_appointments first to find the appointment ID. "
            "Confirm with the customer which appointment to cancel before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "integer",
                    "description": "The ID of the appointment to cancel.",
                },
            },
            "required": ["appointment_id"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# TOOL EXECUTOR
# Routes each tool call to the correct service function.
# Returns a dict that the AI runner serializes to JSON and sends back to the AI.
# ─────────────────────────────────────────────────────────────────────────────

def execute_tool(
    db: Session,
    agent,
    customer,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """
    Execute a tool call by routing to the appropriate service function.

    Tools call service functions directly — no HTTP overhead.
    Returns a plain dict that gets JSON-serialized and sent back to the AI.

    Args:
        db:         database session
        agent:      AIAgent object (provides agent_id and business context)
        customer:   Customer object (provides customer_id)
        tool_name:  which tool the AI wants to call
        tool_input: arguments the AI passed

    Returns:
        dict — the tool result
    """

    
    logger.info(f"Tool call: {tool_name}")
    logger.debug(f"Tool input:\n{json.dumps(tool_input, indent=2)}")

    try:
        result = _route_tool(db, agent, customer, tool_name, tool_input)
        logger.debug(f"Tool result:\n{json.dumps(result, indent=2, default=str)}")
        return result

    except Exception as e:
        logger.exception(f"Tool '{tool_name}' failed: {e}")
        error_result = {
            "error": str(e),
            "tool": tool_name,
        }
        logger.debug(f"Tool error result:\n{json.dumps(error_result, indent=2)}")
        return error_result


def _route_tool(
    db: Session,
    agent,
    customer,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """Internal router — maps tool names to service calls."""

    # ── get_current_time ──────────────────────────────────────────────────
    if tool_name == "get_current_time":
        return _tool_get_current_time(db, agent)

    # ── get_services ──────────────────────────────────────────────────────
    elif tool_name == "get_services":
        return _tool_get_services(db, agent)

    # ── check_availability ────────────────────────────────────────────────
    elif tool_name == "check_availability":
        return _tool_check_availability(db, agent, tool_input)

    # ── check_specific_slot ───────────────────────────────────────────────
    elif tool_name == "check_specific_slot":
        return _tool_check_specific_slot(db, agent, tool_input)

    # ── create_appointment ────────────────────────────────────────────────
    elif tool_name == "create_appointment":
        return _tool_create_appointment(db, agent, customer, tool_input)

    # ── get_customer_appointments ─────────────────────────────────────────
    elif tool_name == "get_customer_appointments":
        return _tool_get_customer_appointments(db, agent, customer)

    # ── cancel_appointment ────────────────────────────────────────────────
    elif tool_name == "cancel_appointment":
        return _tool_cancel_appointment(db, agent, tool_input)

    else:
        logger.warning(f"Unknown tool requested: {tool_name}")
        return {"error": f"Unknown tool: {tool_name}"}


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL TOOL IMPLEMENTATIONS
# Each function handles one tool — clean and easy to modify independently.
# ─────────────────────────────────────────────────────────────────────────────

def _tool_get_current_time(db: Session, agent) -> dict:
    from app.services.availability_service import get_agent_time

    result = get_agent_time(
        db=db,
        agent_id=agent.id,
        business_id=agent.business_id,
    )
    return {
        "timezone": result.timezone,
        "current_time": result.current_time.isoformat(),
        "current_date": result.current_date.isoformat(),
        "is_open": result.is_open,
        "open_time": result.open_time,
        "close_time": result.close_time,
    }


def _tool_get_services(db: Session, agent) -> dict:
    from app.services.service_service import get_services

    result = get_services(
        db=db,
        agent_id=agent.id,
        business_id=agent.business_id,
    )
    return {
        "services": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "duration_minutes": s.duration_minutes,
                "price": str(s.price),
            }
            for s in result["services"]
            if s.active
        ]
    }


def _tool_check_availability(db: Session, agent, tool_input: dict) -> dict:
    from app.services.availability_service import get_availability

    requested_date = date.fromisoformat(tool_input["date"])
    result = get_availability(
        db=db,
        agent_id=agent.id,
        service_id=tool_input["service_id"],
        requested_date=requested_date,
        employee_id=tool_input.get("employee_id"),
        business_id=agent.business_id,
    )
    return {
        "service_name": result.service_name,
        "duration_minutes": result.duration_minutes,
        "timezone": result.timezone,
        "date": result.date.isoformat(),
        "total_slots": result.total_slots,
        "available_slots": [
            {
                "employee_id": s.employee_id,
                "employee_name": s.employee_name,
                "start_time": s.start_time.isoformat(),         # local
                "end_time": s.end_time.isoformat(),             # local
                "start_time_utc": s.start_time_utc.isoformat(), # use for booking
            }
            for s in result.available_slots
        ],
    }


def _tool_check_specific_slot(db: Session, agent, tool_input: dict) -> dict:
    from app.services.availability_service import check_slot

    requested_start = datetime.fromisoformat(tool_input["start_time_utc"])
    result = check_slot(
        db=db,
        agent_id=agent.id,
        service_id=tool_input["service_id"],
        requested_start=requested_start,
        employee_id=tool_input.get("employee_id"),
        business_id=agent.business_id,
    )
    return {
        "available": result.available,
        "timezone": result.timezone,
        "requested_start": result.requested_start.isoformat(),
        "slots": [
            {
                "employee_id": s.employee_id,
                "employee_name": s.employee_name,
                "start_time": s.start_time.isoformat(),
                "start_time_utc": s.start_time_utc.isoformat(),
            }
            for s in result.slots
        ],
        "closest_before": [
            {
                "employee_id": s.employee_id,
                "employee_name": s.employee_name,
                "start_time": s.start_time.isoformat(),
                "start_time_utc": s.start_time_utc.isoformat(),
            }
            for s in result.closest_before
        ],
        "closest_after": [
            {
                "employee_id": s.employee_id,
                "employee_name": s.employee_name,
                "start_time": s.start_time.isoformat(),
                "start_time_utc": s.start_time_utc.isoformat(),
            }
            for s in result.closest_after
        ],
    }


def _tool_create_appointment(db: Session, agent, customer, tool_input: dict) -> dict:
    from app.services import appointment_service
    from app.schemas.appointment import AppointmentCreate, AppointmentSourceEnum

    start_time = datetime.fromisoformat(tool_input["start_time_utc"])

    data = AppointmentCreate(
        agent_id=agent.id,
        customer_id=customer.id,
        employee_id=tool_input["employee_id"],
        service_id=tool_input["service_id"],
        start_time=start_time,
        source=AppointmentSourceEnum.ai,
        note=tool_input.get("note"),
    )

    result = appointment_service.create_appointment(
        db=db,
        data=data,
        business_id=agent.business_id,
    )

    return {
        "success": True,
        "appointment_id": result["id"],
        "service_name": result["service_name"],
        "employee_name": result["employee_name"],
        "start_time_local": result["start_time_local"].isoformat() if result["start_time_local"] else None,
        "end_time_local": result["end_time_local"].isoformat() if result["end_time_local"] else None,
        "timezone": result["timezone"],
        "status": result["status"],
    }


def _tool_get_customer_appointments(db: Session, agent, customer) -> dict:
    from app.services import appointment_service

    result = appointment_service.get_customer_appointments(
        db=db,
        customer_id=customer.id,
        business_id=agent.business_id,
    )
    return {
        "customer_name": customer.name,
        "total": result["total"],
        "appointments": [
            {
                "id": appt["id"],
                "service_name": appt["service_name"],
                "employee_name": appt["employee_name"],
                "start_time_local": appt["start_time_local"].isoformat() if appt["start_time_local"] else None,
                "status": appt["status"],
            }
            for appt in result["appointments"]
        ],
    }


def _tool_cancel_appointment(db: Session, agent, tool_input: dict) -> dict:
    from app.services import appointment_service

    result = appointment_service.cancel_appointment(
        db=db,
        appointment_id=tool_input["appointment_id"],
        business_id=agent.business_id,
    )
    return {
        "success": True,
        "appointment_id": result["id"],
        "service_name": result["service_name"],
        "status": result["status"],
        "message": "Appointment cancelled successfully",
    }