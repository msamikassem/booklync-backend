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


# ── Financials (merchant's own sales — NOT the SaaS subscription billing) ──────
class SaleStatusEnum(str, enum.Enum):
    open           = "open"            # created, nothing paid yet
    partially_paid = "partially_paid"  # some payment recorded, balance remains
    paid           = "paid"            # fully settled
    refunded       = "refunded"        # 🔜 Phase ≥2
    void           = "void"            # cancelled / reversed before payment


class SaleLineKindEnum(str, enum.Enum):
    service    = "service"     # a performed service (Phase 1)
    product    = "product"     # 🔜 retail item, no appointment
    package    = "package"     # 🔜 prepaid package purchase
    adjustment = "adjustment"  # 🔜 refund / fee / manual line


class PaymentMethodEnum(str, enum.Enum):
    cash     = "cash"
    card     = "card"
    transfer = "transfer"
    other    = "other"


class DiscountTypeEnum(str, enum.Enum):
    percent = "percent"   # `value` is a percentage 0–100
    fixed   = "fixed"     # `value` is a money amount in the branch currency


class DiscountScopeEnum(str, enum.Enum):
    order = "order"   # reduces the whole sale (allocated pro-rata across lines)
    line  = "line"    # reduces one specific line


class CommissionTypeEnum(str, enum.Enum):
    percent = "percent"   # `value` is a percentage of the commission base
    flat    = "flat"      # `value` is a fixed money amount per line


class CommissionBaseEnum(str, enum.Enum):
    gross = "gross"   # commission computed before the line discount
    net   = "net"     # commission computed after the line discount


class BundleKindEnum(str, enum.Enum):
    combo   = "combo"     # several services in one visit at a bundled price
    package = "package"   # prepaid credit (sessions or a value wallet)


class CreditKindEnum(str, enum.Enum):
    sessions = "sessions"   # N redemptions of a specific service
    value    = "value"      # a prepaid money wallet, any service


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
    # Commission policy (company-wide; per-branch rates live in CommissionRule).
    # commission_base: compute the cut before (gross) or after (net) the discount.
    # commission_includes_surcharge: count the employee's price surcharge in the base.
    # Tips are always excluded from the commission base.
    commission_base                = Column(
        PgEnum(CommissionBaseEnum, name="commission_base_enum", create_type=True),
        nullable=False, default=CommissionBaseEnum.gross, server_default=CommissionBaseEnum.gross.value,
    )
    commission_includes_surcharge  = Column(Boolean, nullable=False, default=True, server_default="true")
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
    # Financials are per-branch: a branch sets its own currency + tax. Every Sale
    # snapshots this currency; never mix currencies inside one sale or report.
    currency      = Column(String(3), nullable=False, default="USD", server_default="USD")  # ISO 4217
    tax_rate      = Column(Numeric(5, 2), nullable=True)   # percent, e.g. 8.25 (null = no tax)
    tax_inclusive = Column(Boolean, nullable=False, default=False, server_default="false")
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
    sales             = relationship("Sale",                 back_populates="agent",    cascade="all, delete")
    commission_rules  = relationship("CommissionRule",       back_populates="agent",    cascade="all, delete")
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
    # Links the appointments booked together as one multi-service visit (set by
    # create_appointments_batch when >1 service). NULL for single bookings. The
    # dashboard calendar merges contiguous same-employee lines sharing this id.
    booking_group_id = Column(Text, nullable=True)
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


# ═════════════════════════════════════════════════════════════════════════════
# FINANCIALS — the merchant's own sales ledger.
#
# NOT to be confused with AIAgentSubscription / AgentFeature, which are what
# Booklync charges the business (SaaS billing). These tables are what the
# business charges its customers.
#
#   Sale (order header) ─< SaleLineItem (one per service/product/package)
#                       ─< Payment      (settlement ledger)
#
# Prices are SNAPSHOTTED on the line at sale time — later edits to Service.price
# never rewrite history. Currency is per-branch (AIAgent.currency); never mix
# currencies inside one Sale. See docs/modules/financials.md.
# ═════════════════════════════════════════════════════════════════════════════
class Sale(Base):
    __tablename__ = "sales"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    agent_id           = Column(Integer, ForeignKey("ai_agents.id"), nullable=False)
    # Preserve sales history if the customer/user is later deleted.
    customer_id        = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id",     ondelete="SET NULL"), nullable=True)
    # Set when this sale is a combo or package purchase (Phase 4/5). A combo sale
    # is priced as a unit → it reverses as a unit (see void_sale_for_appointment).
    bundle_id          = Column(Integer, ForeignKey("bundles.id", ondelete="SET NULL"), nullable=True)
    status             = Column(
        PgEnum(SaleStatusEnum, name="sale_status_enum", create_type=True),
        nullable=False,
        default=SaleStatusEnum.open,
        server_default=SaleStatusEnum.open.value,
    )
    # Snapshot of the branch currency at sale time (ISO 4217).
    currency           = Column(String(3), nullable=False)
    # Money totals. Invariant: total = subtotal - discount_total + tax_total + tip_total.
    subtotal           = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    discount_total     = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    tax_total          = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    tip_total          = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    total              = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    note               = Column(Text, nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent      = relationship("AIAgent",  back_populates="sales")
    customer   = relationship("Customer")
    created_by = relationship("User")
    line_items = relationship("SaleLineItem", back_populates="sale", cascade="all, delete-orphan")
    payments   = relationship("Payment",      back_populates="sale", cascade="all, delete-orphan")
    discounts  = relationship("SaleDiscount", back_populates="sale", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_sale_agent_created", "agent_id", "created_at"),
        Index("ix_sale_customer", "customer_id"),
    )


class SaleLineItem(Base):
    __tablename__ = "sale_line_items"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    sale_id      = Column(Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    kind         = Column(
        PgEnum(SaleLineKindEnum, name="sale_line_kind_enum", create_type=True),
        nullable=False,
        default=SaleLineKindEnum.service,
        server_default=SaleLineKindEnum.service.value,
    )
    # Snapshot targets — all nullable (a product/adjustment line has no service).
    service_id   = Column(Integer, ForeignKey("services.id",            ondelete="SET NULL"), nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id",  ondelete="SET NULL"), nullable=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id",      ondelete="SET NULL"), nullable=True)
    # Phase 4/5: the bundle this line came from (combo line or package purchase),
    # and the prepaid credit this line was redeemed against (→ free to the
    # customer but still commissioned on full list value).
    bundle_id    = Column(Integer, ForeignKey("bundles.id",         ondelete="SET NULL"), nullable=True)
    redeemed_credit_id = Column(Integer, ForeignKey("package_credits.id", ondelete="SET NULL"), nullable=True)
    # Who performed it → commission attribution.
    employee_id  = Column(Integer, ForeignKey("employees.id",           ondelete="SET NULL"), nullable=True)
    # Snapshot text so the line reads correctly even after the source rows change.
    description   = Column(Text, nullable=True)
    employee_name = Column(Text, nullable=True)
    # Money.
    qty             = Column(Numeric(10, 2), nullable=False, default=1, server_default="1")
    unit_price      = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    surcharge       = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")  # EmployeeCapability.extra_price snapshot
    discount_amount = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")  # 🔜 Phase 2
    tax_amount      = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    tip_amount      = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    line_total      = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    # Commission (🔜 Phase 3 populates these; null/0 until then).
    commission_rate   = Column(Numeric(5, 2),  nullable=True)
    commission_amount = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")

    sale      = relationship("Sale", back_populates="line_items")
    service   = relationship("Service")
    variation = relationship("ServiceVariation")
    employee  = relationship("Employee")
    appointment = relationship("Appointment")

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_sale_line_qty_positive"),
        Index("ix_sale_line_sale", "sale_id"),
        Index("ix_sale_line_employee", "employee_id"),
        Index("ix_sale_line_appointment", "appointment_id"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    sale_id    = Column(Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    amount     = Column(Numeric(10, 2), nullable=False)
    tip_amount = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    method     = Column(
        PgEnum(PaymentMethodEnum, name="payment_method_enum", create_type=True),
        nullable=False,
        default=PaymentMethodEnum.cash,
    )
    paid_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    note       = Column(Text, nullable=True)
    # Gateway-ready: dormant in v1 (internal bookkeeping only). A future Stripe
    # integration fills these without a schema change.
    provider     = Column(Text, nullable=True)   # e.g. "stripe"
    provider_ref = Column(Text, nullable=True)   # charge / transaction id
    intent_id    = Column(Text, nullable=True)   # payment-intent id
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    sale = relationship("Sale", back_populates="payments")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payment_amount_non_negative"),
        Index("ix_payment_sale", "sale_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Discount (Phase 2)
# A reusable discount the business defines, per branch — an ad-hoc "10% off"
# typed at the till, or a named promo code with a validity window and usage caps.
# ─────────────────────────────────────────────────────────────────────────────
class Discount(Base):
    __tablename__ = "discounts"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    agent_id  = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    name      = Column(Text, nullable=False)
    type      = Column(
        PgEnum(DiscountTypeEnum, name="discount_type_enum", create_type=True),
        nullable=False,
    )
    scope     = Column(
        PgEnum(DiscountScopeEnum, name="discount_scope_enum", create_type=True),
        nullable=False,
        default=DiscountScopeEnum.order,
        server_default=DiscountScopeEnum.order.value,
    )
    # percent → 0–100; fixed → money amount in the branch currency.
    value     = Column(Numeric(10, 2), nullable=False)
    # Optional promo code (unique per branch when set — partial unique index).
    code      = Column(Text, nullable=True)
    active    = Column(Boolean, nullable=False, default=True, server_default="true")
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to   = Column(DateTime(timezone=True), nullable=True)
    # Usage caps (null = unlimited). Counted across non-void/refunded sales.
    max_uses           = Column(Integer, nullable=True)
    per_customer_limit = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent        = relationship("AIAgent")
    applications = relationship("SaleDiscount", back_populates="discount")

    __table_args__ = (
        CheckConstraint("value > 0", name="ck_discount_value_positive"),
        Index(
            "uq_discount_agent_code",
            "agent_id", "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL"),
        ),
        Index("ix_discount_agent", "agent_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SaleDiscount (Phase 2)
# One application of a discount to a sale. Snapshots the discount's terms at
# apply time (so editing/deleting the catalogue Discount never rewrites history)
# and stores the money it actually took off. line.discount_amount / sale.
# discount_total are DERIVED from these rows on every recompute.
# ─────────────────────────────────────────────────────────────────────────────
class SaleDiscount(Base):
    __tablename__ = "sale_discounts"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    sale_id     = Column(Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    # null = ad-hoc discount typed at the till (no catalogue entry).
    discount_id = Column(Integer, ForeignKey("discounts.id", ondelete="SET NULL"), nullable=True)
    # Set for a line-scoped discount; null = order scope (allocated across lines).
    sale_line_item_id = Column(Integer, ForeignKey("sale_line_items.id", ondelete="SET NULL"), nullable=True)
    # Snapshot of the discount terms at apply time.
    name   = Column(Text, nullable=True)
    code   = Column(Text, nullable=True)
    type   = Column(PgEnum(DiscountTypeEnum, name="discount_type_enum", create_type=False), nullable=False)
    scope  = Column(PgEnum(DiscountScopeEnum, name="discount_scope_enum", create_type=False), nullable=False)
    value  = Column(Numeric(10, 2), nullable=False)
    # Money actually taken off this sale by this discount (recomputed on rebuild).
    amount     = Column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sale     = relationship("Sale", back_populates="discounts")
    discount = relationship("Discount", back_populates="applications")
    line     = relationship("SaleLineItem")

    __table_args__ = (
        Index("ix_sale_discount_sale", "sale_id"),
        Index("ix_sale_discount_discount", "discount_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CommissionRule (Phase 3)
# A per-branch rule granting an employee a cut of the work they perform. Targets
# are optional and resolved most-specific-first (see financial_service):
#   employee+variation → employee+service → employee+title → employee →
#   variation → service → title → branch default (all targets null).
# FKs CASCADE so a rule never silently broadens its scope when its target is
# deleted. At most one of title/service/variation may be set (DB CHECK).
# ─────────────────────────────────────────────────────────────────────────────
class CommissionRule(Base):
    __tablename__ = "commission_rules"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    agent_id     = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    employee_id  = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True)
    title_id     = Column(Integer, ForeignKey("service_titles.id", ondelete="CASCADE"), nullable=True)
    service_id   = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id", ondelete="CASCADE"), nullable=True)
    type         = Column(
        PgEnum(CommissionTypeEnum, name="commission_type_enum", create_type=True),
        nullable=False,
    )
    # percent → 0–100; flat → money in the branch currency. 0 is a valid explicit
    # override ("earns nothing on X", beating a broader rule).
    value        = Column(Numeric(10, 2), nullable=False)
    active       = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent     = relationship("AIAgent", back_populates="commission_rules")
    employee  = relationship("Employee")
    title     = relationship("ServiceTitle")
    service   = relationship("Service")
    variation = relationship("ServiceVariation")

    __table_args__ = (
        CheckConstraint("value >= 0", name="ck_commission_value_non_negative"),
        CheckConstraint(
            "(CASE WHEN title_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN service_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN variation_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name="ck_commission_one_target",
        ),
        Index("ix_commission_rule_agent", "agent_id"),
        Index("ix_commission_rule_employee", "employee_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bundle / BundleItem (Phase 4/5)
# A per-branch combo (several services in one visit at a bundled price) or a
# prepaid package (sessions of a service, or a value wallet). BundleItems list
# the services involved; combos = the services performed, packages = the
# services the sessions apply to.
# ─────────────────────────────────────────────────────────────────────────────
class Bundle(Base):
    __tablename__ = "bundles"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    agent_id  = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    kind      = Column(
        PgEnum(BundleKindEnum, name="bundle_kind_enum", create_type=True),
        nullable=False,
    )
    name      = Column(Text, nullable=False)
    # What the customer pays for the bundle (combo price, or package purchase price).
    price     = Column(Numeric(10, 2), nullable=False)
    active    = Column(Boolean, nullable=False, default=True, server_default="true")
    # Package-only (null for combos):
    credit_kind  = Column(PgEnum(CreditKindEnum, name="credit_kind_enum", create_type=True), nullable=True)
    credit_value = Column(Numeric(10, 2), nullable=True)   # value-wallet amount granted (≥ price possible)
    expires_days = Column(Integer, nullable=True)          # credit validity in days from purchase (null = never)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent = relationship("AIAgent")
    items = relationship("BundleItem", back_populates="bundle", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_bundle_price_non_negative"),
        Index("ix_bundle_agent", "agent_id"),
    )


class BundleItem(Base):
    __tablename__ = "bundle_items"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    bundle_id    = Column(Integer, ForeignKey("bundles.id", ondelete="CASCADE"), nullable=False)
    service_id   = Column(Integer, ForeignKey("services.id",           ondelete="CASCADE"), nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id", ondelete="CASCADE"), nullable=True)
    qty          = Column(Integer, nullable=False, default=1, server_default="1")

    bundle    = relationship("Bundle", back_populates="items")
    service   = relationship("Service")
    variation = relationship("ServiceVariation")

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_bundle_item_qty_positive"),
        Index("ix_bundle_item_bundle", "bundle_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PackageCredit (Phase 5)
# A customer's prepaid balance from buying a package, branch-scoped. Granted when
# the package sale is PAID; redeemed by a free service line; restored when a
# redemption is reversed; remaining revoked when the purchase is voided.
# ─────────────────────────────────────────────────────────────────────────────
class PackageCredit(Base):
    __tablename__ = "package_credits"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    customer_id  = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    bundle_id    = Column(Integer, ForeignKey("bundles.id",   ondelete="SET NULL"), nullable=True)
    agent_id     = Column(Integer, ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False)
    sale_id      = Column(Integer, ForeignKey("sales.id",     ondelete="SET NULL"), nullable=True)  # the purchase
    kind         = Column(PgEnum(CreditKindEnum, name="credit_kind_enum", create_type=False), nullable=False)
    # sessions: which service the sessions apply to; value: null (any service).
    service_id   = Column(Integer, ForeignKey("services.id",           ondelete="SET NULL"), nullable=True)
    variation_id = Column(Integer, ForeignKey("service_variations.id", ondelete="SET NULL"), nullable=True)
    remaining_qty = Column(Integer, nullable=True)        # sessions remaining
    balance       = Column(Numeric(10, 2), nullable=True) # value wallet remaining
    currency     = Column(String(3), nullable=False)
    expires_at   = Column(DateTime(timezone=True), nullable=True)
    active       = Column(Boolean, nullable=False, default=True, server_default="true")  # false = revoked
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    customer  = relationship("Customer")
    bundle    = relationship("Bundle")
    agent     = relationship("AIAgent")
    sale      = relationship("Sale", foreign_keys=[sale_id])
    service   = relationship("Service")
    variation = relationship("ServiceVariation")

    __table_args__ = (
        Index("ix_package_credit_customer", "customer_id"),
        Index("ix_package_credit_agent", "agent_id"),
        Index("ix_package_credit_sale", "sale_id"),
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

