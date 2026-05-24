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
    employees         = relationship("Employee",             back_populates="agent",    cascade="all, delete")
    business_hours    = relationship("BusinessHour",         back_populates="agent",    cascade="all, delete")
    service_titles    = relationship("ServiceTitle",          back_populates="agent",    cascade="all, delete")
    services          = relationship("Service",              back_populates="agent",    cascade="all, delete")
    conversation_logs = relationship("ConversationLog",      back_populates="agent",    cascade="all, delete")
    appointments      = relationship("Appointment",          back_populates="agent",    cascade="all, delete")
    subscriptions     = relationship("AIAgentSubscription",  back_populates="agent",    cascade="all, delete")
    features          = relationship("AgentFeature",         back_populates="agent",    cascade="all, delete")
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
    agent_id            = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    name                = Column(Text, nullable=False)
    role                = Column(Text)
    phone               = Column(Text)
    email               = Column(Text)
    active              = Column(Boolean, nullable=False, default=True)
    profile_picture_url = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    # track when the record was last modified.
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent         = relationship("AIAgent",   back_populates="employees")
    working_hours = relationship("EmployeeWorkingHour", back_populates="employee", cascade="all, delete")
    blocked_times = relationship("BlockedTime",         back_populates="employee", cascade="all, delete")
    capabilities  = relationship("EmployeeCapability",  back_populates="employee", cascade="all, delete")
    appointments  = relationship("Appointment",         back_populates="employee", cascade="all, delete")
    # personal days off for this employee.
    days_off      = relationship(
        "DayOff",
        back_populates="employee",
        cascade="all, delete",
        primaryjoin="DayOff.employee_id==Employee.id",
        foreign_keys="DayOff.employee_id",
    )


# ─────────────────────────────────────────────────────────────────────────────
# EmployeeWorkingHour
# Regular weekly schedule for an employee (Mon–Sun, 0=Mon … 6=Sun).
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeWorkingHour(Base):
    __tablename__ = "employee_working_hours"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    # 0 = Monday … 6 = Sunday (ISO weekday convention).
    day_of_week = Column(Integer, nullable=False)
    start_time  = Column(Time, nullable=False)
    end_time    = Column(Time, nullable=False)

    employee = relationship("Employee", back_populates="working_hours")

    __table_args__ = (
        # enforce one row per employee per weekday.
        UniqueConstraint("employee_id", "day_of_week", name="uq_employee_working_hour"),
        # Validate weekday range at DB level.
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
    day_of_week = Column(Integer, nullable=False)
    start_time  = Column(Time, nullable=False)
    end_time    = Column(Time, nullable=False)
    reason      = Column(Text)

    employee = relationship("Employee", back_populates="blocked_times")

    __table_args__ = (
        # Sanity check: end must be after start.
        CheckConstraint("end_time > start_time", name="ck_blocked_time_order"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_day_of_week_range"),
        # Index for fast "is this employee blocked on date X?" queries.
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
    appointments      = relationship("Appointment",        back_populates="service", cascade="all, delete")

    __table_args__ = (
        CheckConstraint("duration_minutes IS NULL OR duration_minutes > 0", name="ck_service_duration_positive"),
        CheckConstraint("price IS NULL OR price >= 0",                      name="ck_service_price_non_negative"),
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
    appointments = relationship("Appointment", back_populates="variation", cascade="all, delete")

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
    appointments      = relationship("Appointment",    back_populates="customer", cascade="all, delete")

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
    customer_id = Column(Integer, ForeignKey("customers.id"),  nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"),  nullable=False)
    service_id   = Column(Integer, ForeignKey("services.id"),            nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id"),  nullable=True)
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

