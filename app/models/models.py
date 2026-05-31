import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum as PgEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Float,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass



class AppointmentStatusEnum(str, enum.Enum):
    pending    = "pending"
    confirmed  = "confirmed"
    cancelled  = "cancelled"
    completed  = "completed"
    no_show    = "no_show"


class InviteStatusEnum(str, enum.Enum):
    pending  = "pending"
    accepted = "accepted"
    expired  = "expired"


class AppointmentSourceEnum(str, enum.Enum):
    booking_page = "booking_page"
    manual       = "manual"
    sms          = "sms"
    phone_call   = "call"
    whats_app    = "whatsapp"


class SubscriptionStatusEnum(str, enum.Enum):
    active    = "active"
    paused    = "paused"
    cancelled = "cancelled"
    expired   = "expired"


# ─────────────────────────────────────────────────────────────────────────────
# BusinessType
# Controls which AI tools and which system prompt a business vertical gets.
# ─────────────────────────────────────────────────────────────────────────────
class BusinessType(Base):
    __tablename__ = "business_types"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(255), nullable=False, unique=True)
    description = Column(Text)
    # The base system prompt injected into the AI agent for this vertical.
    system_prompt = Column(Text)

    businesses  = relationship("Business",  back_populates="business_type", cascade="all, delete")
    agent_tools = relationship("AgentTool", back_populates="business_type", cascade="all, delete")


# ─────────────────────────────────────────────────────────────────────────────
# Business
# A single paying client (e.g. "Luxe Hair Salon").
# One business can have multiple AIAgent instances (e.g. per branch).
# ─────────────────────────────────────────────────────────────────────────────
class Business(Base):
    __tablename__ = "businesses"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    name               = Column(Text, nullable=False)
    business_type_id   = Column(Integer, ForeignKey("business_types.id"), nullable=False)
    email              = Column(Text, nullable=False, unique=True, index=True)
    password_hash      = Column(Text, nullable=False)
    # refresh_token enables JWT-based session revocation.
    #           Store the hashed token, not the raw token.
    refresh_token      = Column(Text, nullable=True)
    # last_login helps detect inactive accounts and aids support.
    last_login         = Column(DateTime(timezone=True), nullable=True)
    phone              = Column(Text, nullable=False, unique=True, index=True)
    subscription_plan  = Column(Text, default="free")
    active             = Column(Boolean, default=False)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    # updated_at auto-refreshes on every UPDATE.
    updated_at         = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    business_type = relationship("BusinessType", back_populates="businesses")
    agents        = relationship("AIAgent",   back_populates="business",  cascade="all, delete")
    customers     = relationship("Customer",  back_populates="business",  cascade="all, delete")
    users         = relationship("User",      back_populates="business",  cascade="all, delete")
    roles         = relationship("Role",      back_populates="business",  cascade="all, delete")


# ─────────────────────────────────────────────────────────────────────────────
# AIAgent
# Represents one deployed AI receptionist (phone number, timezone, location).
# A business may run multiple agents (e.g. one per branch).
# ─────────────────────────────────────────────────────────────────────────────
class AIAgent(Base):
    __tablename__ = "ai_agents"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    business_id   = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    agent_name    = Column(String(255), nullable=False)
    # The Twilio (or equivalent) number this agent answers on.
    phone_number  = Column(Text, nullable=False, unique=True, index=True)
    location_name = Column(Text, nullable=False)
    # Store IANA timezone strings (e.g. "America/New_York").
    timezone      = Column(Text, nullable=False)
    model         = Column(Text, nullable=False)
    sms_welcome_message       = Column(Text, nullable=True)
    sms_post_welcome_behavior = Column(Text, nullable=True, default="ai")
    minutes_used = Column(Float, default=0.0, nullable=False, server_default="0")
    # booking_page_slug is the public URL slug for this branch (e.g. "luxe-salon-main").
    booking_page_slug = Column(Text, nullable=True, unique=True, index=True)
    custom_domain     = Column(Text, nullable=True, unique=True, index=True)
    domain_verified   = Column(Boolean, default=False, nullable=False, server_default="false")
    active        = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    business          = relationship("Business",             back_populates="agents")
    employee_branches = relationship("EmployeeBranch",       back_populates="agent",    cascade="all, delete-orphan")
    business_hours    = relationship("BusinessHour",         back_populates="agent",    cascade="all, delete")
    service_titles    = relationship("ServiceTitle",          back_populates="agent",    cascade="all, delete")
    services          = relationship("Service",              back_populates="agent",    cascade="all, delete")
    conversation_logs = relationship("ConversationLog",      back_populates="agent",    cascade="all, delete")
    appointments      = relationship("Appointment",          back_populates="agent",    cascade="all, delete")
    subscriptions     = relationship("AIAgentSubscription",  back_populates="agent",    cascade="all, delete")
    features          = relationship("AgentFeature",         back_populates="agent",    cascade="all, delete")
    user_branch_roles = relationship("UserBranchRole",       back_populates="agent",    cascade="all, delete")
    # agent-level days off (public holidays, full-branch closures).
    days_off          = relationship("DayOff",               back_populates="agent",    cascade="all, delete",
                                     primaryjoin="and_(DayOff.agent_id==AIAgent.id, DayOff.employee_id==None)",
                                     foreign_keys="DayOff.agent_id")


# ─────────────────────────────────────────────────────────────────────────────
# Employee
# A staff member belonging to an AIAgent (branch).
# ─────────────────────────────────────────────────────────────────────────────
class Employee(Base):
    __tablename__ = "employees"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    name                = Column(Text, nullable=False)
    role                = Column(Text)
    phone               = Column(Text)
    email               = Column(Text)
    profile_picture_url = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Branch assignments live in the join table; each row carries the per-branch
    # active + can_receive_bookings flags so the same person can be paused at
    # one branch without affecting the others.
    branches      = relationship("EmployeeBranch",      back_populates="employee", cascade="all, delete-orphan")
    working_hours = relationship("EmployeeWorkingHour", back_populates="employee", cascade="all, delete")
    blocked_times = relationship("BlockedTime",         back_populates="employee", cascade="all, delete")
    capabilities  = relationship("EmployeeCapability",  back_populates="employee", cascade="all, delete")
    appointments  = relationship("Appointment",         back_populates="employee", passive_deletes=True)
    # Optional dashboard login linked to this employee (one-to-one; created via invite).
    user          = relationship("User",                back_populates="employee", uselist=False)
    invites       = relationship("UserInvite",          back_populates="employee", cascade="all, delete")
    days_off      = relationship(
        "DayOff",
        back_populates="employee",
        cascade="all, delete",
        primaryjoin="DayOff.employee_id==Employee.id",
        foreign_keys="DayOff.employee_id",
    )


# ─────────────────────────────────────────────────────────────────────────────
# EmployeeBranch
# Join table: assigns an Employee to one of the business's AIAgents (branches)
# with per-branch active and can_receive_bookings flags. A row here is the
# source of truth for "does this employee belong to this branch?" — services
# and the availability engine filter through it.
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeBranch(Base):
    __tablename__ = "employee_branches"

    employee_id          = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    agent_id             = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), primary_key=True)
    active               = Column(Boolean, nullable=False, default=True, server_default="true")
    can_receive_bookings = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at           = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at           = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    employee = relationship("Employee", back_populates="branches")
    agent    = relationship("AIAgent",  back_populates="employee_branches")


# ─────────────────────────────────────────────────────────────────────────────
# EmployeeWorkingHour
# Regular weekly schedule for an employee (Mon–Sun, 0=Mon … 6=Sun).
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeWorkingHour(Base):
    __tablename__ = "employee_working_hours"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    # Per-branch weekly schedule: an employee can work different hours at
    # different branches; UNIQUE is (employee, branch, weekday).
    agent_id    = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time  = Column(Time, nullable=False)
    end_time    = Column(Time, nullable=False)

    employee = relationship("Employee", back_populates="working_hours")
    agent    = relationship("AIAgent")

    __table_args__ = (
        UniqueConstraint("employee_id", "agent_id", "day_of_week", name="uq_employee_working_hour"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_day_of_week_range"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BlockedTime
# Regular weekly schedule timeoff for an employee (Mon–Sun, 0=Mon … 6=Sun).
# ─────────────────────────────────────────────────────────────────────────────
class BlockedTime(Base):
    __tablename__ = "blocked_times"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    # Recurring breaks are per-branch: an employee's lunch at branch A doesn't
    # apply at branch B unless explicitly added there.
    agent_id    = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time  = Column(Time, nullable=False)
    end_time    = Column(Time, nullable=False)
    reason      = Column(Text)

    employee = relationship("Employee", back_populates="blocked_times")
    agent    = relationship("AIAgent")

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_blocked_time_order"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_day_of_week_range"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AITool / AgentTool
# Catalogue of tools the AI can call, assigned per business vertical.
# ─────────────────────────────────────────────────────────────────────────────
class AITool(Base):
    __tablename__ = "ai_tools"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=False)

    agent_tools = relationship("AgentTool", back_populates="tool", cascade="all, delete")


class AgentTool(Base):
    __tablename__ = "agent_tools"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    business_type_id = Column(Integer, ForeignKey("business_types.id"), nullable=False)
    tool_id          = Column(Integer, ForeignKey("ai_tools.id"),        nullable=False)

    business_type = relationship("BusinessType", back_populates="agent_tools")
    tool          = relationship("AITool",        back_populates="agent_tools")

    __table_args__ = (
        # Each tool should appear at most once per business type.
        UniqueConstraint("business_type_id", "tool_id", name="uq_agent_tool"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# BusinessHour
# Opening / closing hours per weekday for an AIAgent (branch).
# ─────────────────────────────────────────────────────────────────────────────
class BusinessHour(Base):
    __tablename__ = "business_hours"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    agent_id    = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    open_time   = Column(Time, nullable=False)
    closed_time = Column(Time, nullable=False)

    agent = relationship("AIAgent", back_populates="business_hours")

    __table_args__ = (
        UniqueConstraint("agent_id", "day_of_week", name="uq_business_hour"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_bh_day_range"),
        CheckConstraint("closed_time > open_time", name="ck_bh_time_order"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service
# A bookable service offered by an AIAgent (branch).
#
# CHANGE 11: Added updated_at.
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# ServiceTitle
# A grouping label for services — e.g. "Haircut", "Coloring".
# Services optionally belong to a title; titles have no bookable logic of their own.
# ─────────────────────────────────────────────────────────────────────────────
class ServiceTitle(Base):
    __tablename__ = "service_titles"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    agent_id   = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    name       = Column(Text, nullable=False)
    active     = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    agent    = relationship("AIAgent",   back_populates="service_titles")
    services = relationship("Service",   back_populates="title", cascade="all, delete")


class Service(Base):
    __tablename__ = "services"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    agent_id         = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    # Optional grouping — null means the service is uncategorised
    title_id         = Column(Integer, ForeignKey("service_titles.id"), nullable=True)
    name             = Column(Text, nullable=False)
    description      = Column(Text, nullable=True)
    # Null when the service has variations (price/duration live on each variation instead)
    duration_minutes = Column(Integer, nullable=True)
    price            = Column(Numeric(10, 2), nullable=True)
    # Preparation buffers: employee time reserved before/after the appointment window.
    # Used by the availability engine to prevent back-to-back bookings with no setup time.
    preparation_before_minutes = Column(Integer, nullable=True)
    preparation_after_minutes  = Column(Integer, nullable=True)
    # Optional display colour for calendar/UI (6-digit hex, e.g. #FF5733). Null = use default.
    color            = Column(Text, nullable=True)
    active           = Column(Boolean, nullable=False, default=True)
    sort_order       = Column(Integer, nullable=False, default=0)
    updated_at       = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent             = relationship("AIAgent",           back_populates="services")
    title             = relationship("ServiceTitle",       back_populates="services")
    variations        = relationship("ServiceVariation",   back_populates="service", cascade="all, delete")
    capabilities      = relationship("EmployeeCapability", back_populates="service",  cascade="all, delete",
                                    primaryjoin="EmployeeCapability.service_id==Service.id",
                                    foreign_keys="EmployeeCapability.service_id")
    appointments      = relationship("Appointment",        back_populates="service", passive_deletes=True)

    __table_args__ = (
        CheckConstraint("duration_minutes IS NULL OR duration_minutes > 0",          name="ck_service_duration_positive"),
        CheckConstraint("price IS NULL OR price >= 0",                               name="ck_service_price_non_negative"),
        CheckConstraint("preparation_before_minutes IS NULL OR preparation_before_minutes >= 0", name="ck_service_prep_before_non_negative"),
        CheckConstraint("preparation_after_minutes  IS NULL OR preparation_after_minutes  >= 0", name="ck_service_prep_after_non_negative"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ServiceVariation
# An optional sub-option of a Service, each with its own price and duration.
# e.g. Service "Buzz Cut" → Variation "Fade" ($30, 30 min), "Skin Fade" ($40, 45 min).
# If a service has no variations it carries price/duration directly.
# ─────────────────────────────────────────────────────────────────────────────
class ServiceVariation(Base):
    __tablename__ = "service_variations"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    service_id       = Column(Integer, ForeignKey("services.id"), nullable=False)
    name             = Column(Text, nullable=False)
    price            = Column(Numeric(10, 2), nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    active           = Column(Boolean, nullable=False, default=True)
    sort_order       = Column(Integer, nullable=False, default=0)

    service = relationship("Service", back_populates="variations")
    appointments = relationship("Appointment", back_populates="variation", passive_deletes=True)

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_variation_duration_positive"),
        CheckConstraint("price >= 0",           name="ck_variation_price_non_negative"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# EmployeeCapability
# Grants an employee the ability to perform work at one of three levels:
#   - title_id set    → employee can do every service + variation under that title
#   - service_id set  → employee can do that service and all its variations
#   - variation_id set → employee can do only that specific variation
# Exactly one FK must be non-null (enforced by CHECK constraint).
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeCapability(Base):
    __tablename__ = "employee_capabilities"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    employee_id  = Column(Integer, ForeignKey("employees.id"),          nullable=False)
    title_id     = Column(Integer, ForeignKey("service_titles.id"),     nullable=True)
    service_id   = Column(Integer, ForeignKey("services.id"),           nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id"), nullable=True)
    extra_price  = Column(Numeric(10, 2), nullable=False, default=0)

    employee  = relationship("Employee",         back_populates="capabilities")
    title     = relationship("ServiceTitle")
    service   = relationship("Service",          back_populates="capabilities",
                             primaryjoin="EmployeeCapability.service_id==Service.id",
                             foreign_keys="[EmployeeCapability.service_id]")
    variation = relationship("ServiceVariation")

    __table_args__ = (
        # Exactly one of the three FKs must be set
        CheckConstraint(
            "(title_id IS NOT NULL)::int + (service_id IS NOT NULL)::int + (variation_id IS NOT NULL)::int = 1",
            name="ck_employee_capability_exactly_one",
        ),
        # Prevent duplicate grants at each level
        Index("uix_emp_cap_title",     "employee_id", "title_id",     unique=True,
              postgresql_where=text("title_id IS NOT NULL")),
        Index("uix_emp_cap_service",   "employee_id", "service_id",   unique=True,
              postgresql_where=text("service_id IS NOT NULL")),
        Index("uix_emp_cap_variation", "employee_id", "variation_id", unique=True,
              postgresql_where=text("variation_id IS NOT NULL")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Customer
# An end-user who books appointments.
# ─────────────────────────────────────────────────────────────────────────────
class Customer(Base):
    __tablename__ = "customers"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    name        = Column(Text)
    phone       = Column(Text, nullable=False)
    email       = Column(Text)
    notes       = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    business          = relationship("Business",       back_populates="customers")
    conversation_logs = relationship("ConversationLog", back_populates="customer", cascade="all, delete")
    appointments      = relationship("Appointment",    back_populates="customer", passive_deletes=True)

    __table_args__ = (
        #a phone number is unique per business (not globally).
        UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),
        Index("ix_customer_business_phone", "business_id", "phone"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ConversationLog
# Header record for one conversation session.
# ─────────────────────────────────────────────────────────────────────────────
class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    agent_id       = Column(Integer, ForeignKey("ai_agents.id"),  nullable=False)
    customer_id    = Column(Integer, ForeignKey("customers.id"),  nullable=False)
    customer_phone = Column(Text, nullable=False)
    # channel replaces `place` — explicit values: sms, call, web, dashboard.
    channel        = Column(Text, nullable=False)
    # full conversation stored as JSONB for flexible querying.
    #           Example value: [{"role": "user", "content": "I'd like to book...", "ts": "2024-01-01T09:00:00Z"}]
    messages       = Column(JSONB, nullable=False, default=list)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    agent    = relationship("AIAgent",   back_populates="conversation_logs")
    customer = relationship("Customer",  back_populates="conversation_logs")

    __table_args__ = (
        Index("ix_conversation_logs_agent_customer", "agent_id", "customer_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Appointment
# The core transactional record.
# ─────────────────────────────────────────────────────────────────────────────
class Appointment(Base):
    __tablename__ = "appointments"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    agent_id    = Column(Integer, ForeignKey("ai_agents.id"),  nullable=False)
    customer_id  = Column(Integer, ForeignKey("customers.id",             ondelete="SET NULL"), nullable=True)
    employee_id  = Column(Integer, ForeignKey("employees.id",            ondelete="SET NULL"), nullable=True)
    service_id   = Column(Integer, ForeignKey("services.id",             ondelete="SET NULL"), nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id",   ondelete="SET NULL"), nullable=True)
    # Snapshots at booking time so history survives deletion of the linked rows.
    customer_name  = Column(Text, nullable=True)
    customer_phone = Column(Text, nullable=True)
    employee_name  = Column(Text, nullable=True)
    service_name   = Column(Text, nullable=True)
    variation_name = Column(Text, nullable=True)
    start_time   = Column(DateTime(timezone=True), nullable=False)
    end_time    = Column(DateTime(timezone=True), nullable=False)
    status      = Column(
        PgEnum(AppointmentStatusEnum, name="appointment_status_enum", create_type=True),
        nullable=False,
        default=AppointmentStatusEnum.pending,
    )
    source      = Column(
        PgEnum(AppointmentSourceEnum, name="appointment_source_enum", create_type=True),
        nullable=False,
    )
    note        = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    agent     = relationship("AIAgent",          back_populates="appointments")
    customer  = relationship("Customer",         back_populates="appointments")
    employee  = relationship("Employee",         back_populates="appointments")
    service   = relationship("Service",          back_populates="appointments")
    variation = relationship("ServiceVariation", back_populates="appointments")

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_appointment_time_order"),
        Index("ix_appointment_agent_start", "agent_id", "start_time"),
        Index("ix_appointment_employee_start", "employee_id", "start_time"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AIAgentSubscription
# Billing record for each agent's subscription period.
# ─────────────────────────────────────────────────────────────────────────────
class AIAgentSubscription(Base):
    __tablename__ = "ai_agent_subscriptions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    agent_id   = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    plan_name  = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date   = Column(Date, nullable=False)
    status     = Column(
        PgEnum(SubscriptionStatusEnum, name="subscription_status_enum", create_type=True),
        nullable=False,
        default=SubscriptionStatusEnum.active,
    )
    price      = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    agent = relationship("AIAgent", back_populates="subscriptions")

    __table_args__ = (
        CheckConstraint("end_date > start_date",    name="ck_subscription_date_order"),
        CheckConstraint("price >= 0",               name="ck_subscription_price_non_negative"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AIFeature / AgentFeature
# À-la-carte features that can be enabled per agent with optional price overrides.
# ─────────────────────────────────────────────────────────────────────────────
class AIFeature(Base):
    __tablename__ = "ai_features"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=False)
    base_price  = Column(Numeric(10, 2), nullable=False)

    agent_features = relationship("AgentFeature", back_populates="feature", cascade="all, delete")

    __table_args__ = (
        CheckConstraint("base_price >= 0", name="ck_feature_base_price_non_negative"),
    )


class AgentFeature(Base):
    __tablename__ = "agent_features"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    agent_id       = Column(Integer, ForeignKey("ai_agents.id"),   nullable=False)
    feature_id     = Column(Integer, ForeignKey("ai_features.id"), nullable=False)
    # NULL means use the feature's base_price.
    price_override = Column(Numeric(10, 2), nullable=True)

    agent   = relationship("AIAgent",    back_populates="features")
    feature = relationship("AIFeature",  back_populates="agent_features")

    __table_args__ = (
        UniqueConstraint("agent_id", "feature_id", name="uq_agent_feature"),
        CheckConstraint("price_override >= 0", name="ck_agent_feature_override_non_negative"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DayOff
# Represents a closed period — either for the whole branch (agent_id only)
# or for a specific employee (employee_id set).
# ─────────────────────────────────────────────────────────────────────────────
class DayOff(Base):
    __tablename__ = "days_off"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(255))                                    # e.g. "Christmas Day"
    agent_id    = Column(Integer, ForeignKey("ai_agents.id"),  nullable=False)
    #nullable — NULL means the day off applies to the whole branch.
    employee_id = Column(Integer, ForeignKey("employees.id"),  nullable=True)
    start_date  = Column(Date, nullable=False)
    end_date    = Column(Date, nullable=False)

    agent    = relationship(
        "AIAgent",
        back_populates="days_off",
        foreign_keys=[agent_id],
    )
    #back-populate to the employee's personal days_off list.
    employee = relationship(
        "Employee",
        back_populates="days_off",
        foreign_keys=[employee_id],
    )

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_day_off_date_order"),
    )


# ═════════════════════════════════════════════════════════════════════════════
# RBAC — Role-Based Access Control
#
# Identity & access for everyone who logs into the dashboard (owner, branch
# manager, receptionist, staff). The design separates three concerns so that
# granular custom permissions can be added later without a refactor:
#
#   Permission  — an atomic capability (e.g. "manage_services")
#   Role        — a named bundle of permissions (4 system presets seeded)
#   User        — a login identity belonging to one tenant (Business)
#   UserBranchRole — assigns a User a Role AT a specific branch (AIAgent)
#
# "Can user X do Y at branch Z?" resolves as:
#   owner short-circuit (User.is_owner) → yes on every branch in the tenant
#   else: UserBranchRole(user=X, agent=Z) → Role → role_permissions → Y?
# ═════════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# Permission
# Atomic capability key. Seeded from app/core/rbac.py at startup. Adding a new
# granular permission later = add a row here + map it to roles; no schema change.
# ─────────────────────────────────────────────────────────────────────────────
class Permission(Base):
    __tablename__ = "permissions"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    key         = Column(Text, nullable=False, unique=True, index=True)   # e.g. "manage_services"
    description = Column(Text, nullable=False)
    category    = Column(Text, nullable=True)                              # UI grouping (e.g. "scheduling")

    role_links  = relationship("RolePermission", back_populates="permission", cascade="all, delete")


# ─────────────────────────────────────────────────────────────────────────────
# Role
# A named bundle of permissions. System presets (owner / branch_manager /
# receptionist / staff) have business_id NULL and is_system=True; future custom
# roles carry a real business_id.
# ─────────────────────────────────────────────────────────────────────────────
class Role(Base):
    __tablename__ = "roles"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    # NULL = shared system preset; set = a tenant's own custom role.
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)
    key         = Column(Text, nullable=False)                # e.g. "branch_manager"
    name        = Column(Text, nullable=False)                # display name
    description = Column(Text, nullable=True)
    is_system   = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    business     = relationship("Business", back_populates="roles")
    permissions  = relationship("RolePermission", back_populates="role", cascade="all, delete")
    branch_roles = relationship("UserBranchRole", back_populates="role")

    __table_args__ = (
        # A role key is unique per tenant; system presets (business_id NULL) are
        # globally unique by key via a partial index below.
        UniqueConstraint("business_id", "key", name="uq_role_business_key"),
        Index("uix_role_system_key", "key", unique=True, postgresql_where=text("business_id IS NULL")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RolePermission
# Many-to-many: which permissions a role grants.
# ─────────────────────────────────────────────────────────────────────────────
class RolePermission(Base):
    __tablename__ = "role_permissions"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    role_id       = Column(Integer, ForeignKey("roles.id"),       nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id"), nullable=False)

    role       = relationship("Role",       back_populates="permissions")
    permission = relationship("Permission", back_populates="role_links")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# User
# Single login identity for all dashboard users. Belongs to exactly one tenant
# (Business). May optionally link to one Employee (for the own-calendar view).
# Owners carry is_owner=True → full access to every branch in the tenant.
# ─────────────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    business_id   = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)
    # Optional 1:1 link to an Employee record (nullable for owners/managers).
    # ON DELETE SET NULL: deleting the employee unlinks the login rather than
    # cascading into (and destroying) the user account.
    employee_id   = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, unique=True)
    email         = Column(Text, nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    # Tenant-wide authority flag — explicit rather than a NULL-branch wildcard.
    is_owner      = Column(Boolean, nullable=False, default=False, server_default="false")
    refresh_token = Column(Text, nullable=True)
    last_login    = Column(DateTime(timezone=True), nullable=True)
    active        = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    business     = relationship("Business", back_populates="users")
    employee     = relationship("Employee", back_populates="user")
    branch_roles = relationship("UserBranchRole", back_populates="user", cascade="all, delete")


# ─────────────────────────────────────────────────────────────────────────────
# UserBranchRole
# The core scoping assignment: a User holds a Role AT a specific branch.
# agent_id is ALWAYS set (never a wildcard) — tenant-wide authority is the
# is_owner flag on User, so an accidental NULL can never escalate privileges.
# Multiple rows per user = membership in multiple branches.
# ─────────────────────────────────────────────────────────────────────────────
class UserBranchRole(Base):
    __tablename__ = "user_branch_roles"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"),     nullable=False, index=True)
    agent_id   = Column(Integer, ForeignKey("ai_agents.id"), nullable=False, index=True)
    role_id    = Column(Integer, ForeignKey("roles.id"),     nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user  = relationship("User",          back_populates="branch_roles")
    agent = relationship("AIAgent",       back_populates="user_branch_roles")
    role  = relationship("Role",          back_populates="branch_roles")

    __table_args__ = (
        # One role per user per branch.
        UniqueConstraint("user_id", "agent_id", name="uq_user_branch"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# UserInvite
# A pending dashboard-access invitation. Originates from an existing Employee
# (email + role + branch). Token stored as a bcrypt hash (raw token only emailed).
# On accept → creates a User + a UserBranchRole.
# ─────────────────────────────────────────────────────────────────────────────
class UserInvite(Base):
    __tablename__ = "user_invites"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    # List of branch ids this invite grants access to. Same role is applied at
    # every branch on accept (one UserBranchRole row per agent_id).
    agent_ids   = Column(JSONB, nullable=False)
    role_id     = Column(Integer, ForeignKey("roles.id"),     nullable=False)
    # The employee being granted login access (must exist first).
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    email       = Column(Text, nullable=False)
    token_hash  = Column(Text, nullable=False, unique=True, index=True)
    status      = Column(
        PgEnum(InviteStatusEnum, name="invite_status_enum", create_type=True),
        nullable=False,
        default=InviteStatusEnum.pending,
    )
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    role     = relationship("Role")
    employee = relationship("Employee", back_populates="invites")


# ─────────────────────────────────────────────────────────────────────────────
# Create all tables
# In production prefer Alembic migrations over create_all so schema changes
# are versioned, reversible, and safe to run against a live database.
#
# pip install alembic
# alembic init alembic
# alembic revision --autogenerate -m "initial schema"
# alembic upgrade head
# ─────────────────────────────────────────────────────────────────────────────

