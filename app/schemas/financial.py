"""
app/schemas/financial.py

Pydantic schemas for the financials module (merchant's own sales ledger).

  Sale (order header) ─< SaleLineItem ─< Payment

Money is Decimal everywhere; status/method enums are reused from the models so
the API contract and the DB stay in lockstep. See docs/modules/financials.md.
"""

from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.models import (
    SaleStatusEnum,
    SaleLineKindEnum,
    PaymentMethodEnum,
    DiscountTypeEnum,
    DiscountScopeEnum,
    CommissionTypeEnum,
    CommissionBaseEnum,
    BundleKindEnum,
    CreditKindEnum,
)


# ─────────────────────────────────────────────────────────────────────────────
# Responses — data going OUT
# ─────────────────────────────────────────────────────────────────────────────

class SaleLineItemResponse(BaseModel):
    id: int
    kind: SaleLineKindEnum
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    appointment_id: Optional[int] = None
    employee_id: Optional[int] = None
    bundle_id: Optional[int] = None
    redeemed_credit_id: Optional[int] = None
    description: Optional[str] = None
    employee_name: Optional[str] = None
    qty: Decimal
    unit_price: Decimal
    surcharge: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    tip_amount: Decimal
    line_total: Decimal
    commission_rate: Optional[Decimal] = None
    commission_amount: Decimal

    model_config = {"from_attributes": True}


class PaymentResponse(BaseModel):
    id: int
    sale_id: int
    amount: Decimal
    tip_amount: Decimal
    method: PaymentMethodEnum
    paid_at: datetime
    note: Optional[str] = None
    provider: Optional[str] = None
    provider_ref: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SaleDiscountResponse(BaseModel):
    id: int
    sale_id: int
    discount_id: Optional[int] = None        # null = ad-hoc (no catalogue entry)
    sale_line_item_id: Optional[int] = None  # null = order scope
    name: Optional[str] = None
    code: Optional[str] = None
    type: DiscountTypeEnum
    scope: DiscountScopeEnum
    value: Decimal                           # snapshot of percent / fixed value
    amount: Decimal                          # money actually taken off the sale
    created_at: datetime

    model_config = {"from_attributes": True}


class SaleResponse(BaseModel):
    id: int
    agent_id: int
    customer_id: Optional[int] = None
    created_by_user_id: Optional[int] = None
    bundle_id: Optional[int] = None
    status: SaleStatusEnum
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    tip_total: Decimal
    total: Decimal
    note: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    line_items: list[SaleLineItemResponse] = []
    payments: list[PaymentResponse] = []
    discounts: list[SaleDiscountResponse] = []

    model_config = {"from_attributes": True}


class SaleListResponse(BaseModel):
    sales: list[SaleResponse]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# Requests — data coming IN
# ─────────────────────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    """Record a payment against a sale. Amount + tip are in the sale's currency."""
    amount: Decimal
    method: PaymentMethodEnum = PaymentMethodEnum.cash
    tip_amount: Decimal = Decimal("0")
    note: Optional[str] = None

    @field_validator("amount", "tip_amount")
    @classmethod
    def non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Discounts (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

def _norm_code(code: Optional[str]) -> Optional[str]:
    """Promo codes are stored upper-cased + trimmed; blank → None."""
    if code is None:
        return None
    code = code.strip().upper()
    return code or None


def _check_value(type_: Optional[DiscountTypeEnum], value: Optional[Decimal]) -> None:
    """A percent discount must be 0 < value ≤ 100; any discount must be > 0."""
    if value is None:
        return
    if value <= 0:
        raise ValueError("Discount value must be greater than 0")
    if type_ == DiscountTypeEnum.percent and value > 100:
        raise ValueError("A percentage discount cannot exceed 100")


class DiscountCreate(BaseModel):
    """Define a reusable discount for a branch."""
    name: str
    type: DiscountTypeEnum
    value: Decimal
    scope: DiscountScopeEnum = DiscountScopeEnum.order
    code: Optional[str] = None
    active: bool = True
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    max_uses: Optional[int] = None
    per_customer_limit: Optional[int] = None

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        return _norm_code(v)

    @model_validator(mode="after")
    def _validate(self):
        _check_value(self.type, self.value)
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot be before valid_from")
        for f in ("max_uses", "per_customer_limit"):
            n = getattr(self, f)
            if n is not None and n < 1:
                raise ValueError(f"{f} must be at least 1")
        return self


class DiscountUpdate(BaseModel):
    """Patch a discount. Only provided fields change."""
    name: Optional[str] = None
    type: Optional[DiscountTypeEnum] = None
    value: Optional[Decimal] = None
    scope: Optional[DiscountScopeEnum] = None
    code: Optional[str] = None
    active: Optional[bool] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    max_uses: Optional[int] = None
    per_customer_limit: Optional[int] = None

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        return _norm_code(v)

    @model_validator(mode="after")
    def _validate(self):
        # When type isn't being changed we can't fully validate percent≤100 here;
        # the service re-checks against the persisted type.
        _check_value(self.type, self.value)
        return self


class DiscountResponse(BaseModel):
    id: int
    agent_id: int
    name: str
    type: DiscountTypeEnum
    scope: DiscountScopeEnum
    value: Decimal
    code: Optional[str] = None
    active: bool
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    max_uses: Optional[int] = None
    per_customer_limit: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DiscountListResponse(BaseModel):
    discounts: list[DiscountResponse]
    total: int


class ApplyDiscountRequest(BaseModel):
    """
    Apply a discount to a sale. Two mutually-exclusive paths:
      • catalogue:  give `discount_id` OR `code`.
      • ad-hoc:     give `type` + `value` (+ optional `scope`, `name`).
    `sale_line_item_id` targets one line (line scope); omit for order scope.
    """
    discount_id: Optional[int] = None
    code: Optional[str] = None
    type: Optional[DiscountTypeEnum] = None
    value: Optional[Decimal] = None
    scope: Optional[DiscountScopeEnum] = None
    name: Optional[str] = None
    sale_line_item_id: Optional[int] = None

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        return _norm_code(v)

    @model_validator(mode="after")
    def _validate(self):
        is_catalogue = self.discount_id is not None or self.code is not None
        is_adhoc = self.type is not None and self.value is not None
        if is_catalogue and is_adhoc:
            raise ValueError("Provide either a catalogue discount (discount_id/code) or an ad-hoc one (type+value), not both")
        if not is_catalogue and not is_adhoc:
            raise ValueError("Provide a discount_id, a code, or an ad-hoc type+value")
        if is_adhoc:
            _check_value(self.type, self.value)
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Commissions (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def _check_targets(title_id, service_id, variation_id) -> None:
    """At most one service-level target may be set on a commission rule."""
    if sum(x is not None for x in (title_id, service_id, variation_id)) > 1:
        raise ValueError("Set at most one of title_id / service_id / variation_id")


def _check_commission_value(type_: Optional[CommissionTypeEnum], value: Optional[Decimal]) -> None:
    """value must be ≥ 0; a percent commission must also be ≤ 100."""
    if value is None:
        return
    if value < 0:
        raise ValueError("Commission value cannot be negative")
    if type_ == CommissionTypeEnum.percent and value > 100:
        raise ValueError("A percentage commission cannot exceed 100")


class CommissionConfigResponse(BaseModel):
    """Company-wide commission policy (per business)."""
    commission_base: CommissionBaseEnum
    commission_includes_surcharge: bool

    model_config = {"from_attributes": True}


class CommissionConfigUpdate(BaseModel):
    commission_base: Optional[CommissionBaseEnum] = None
    commission_includes_surcharge: Optional[bool] = None


class CommissionRuleCreate(BaseModel):
    """A per-branch commission rule. Targets are optional and resolved
    most-specific-first; all-null = the branch default for everyone."""
    type: CommissionTypeEnum
    value: Decimal
    employee_id: Optional[int] = None
    title_id: Optional[int] = None
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    active: bool = True

    @model_validator(mode="after")
    def _validate(self):
        _check_targets(self.title_id, self.service_id, self.variation_id)
        _check_commission_value(self.type, self.value)
        return self


class CommissionRuleUpdate(BaseModel):
    type: Optional[CommissionTypeEnum] = None
    value: Optional[Decimal] = None
    employee_id: Optional[int] = None
    title_id: Optional[int] = None
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    active: Optional[bool] = None

    @model_validator(mode="after")
    def _validate(self):
        # Targets aren't fully re-validated here (the service re-checks against
        # the persisted row); percent≤100 is checked against the given type.
        _check_commission_value(self.type, self.value)
        return self


class CommissionRuleResponse(BaseModel):
    id: int
    agent_id: int
    employee_id: Optional[int] = None
    title_id: Optional[int] = None
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    type: CommissionTypeEnum
    value: Decimal
    active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CommissionRuleListResponse(BaseModel):
    commission_rules: list[CommissionRuleResponse]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# Bundles — combos + packages (Phase 4/5)
# ─────────────────────────────────────────────────────────────────────────────

class BundleItemCreate(BaseModel):
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    qty: int = 1

    @model_validator(mode="after")
    def _validate(self):
        if (self.service_id is None) == (self.variation_id is None):
            raise ValueError("A bundle item needs exactly one of service_id or variation_id")
        if self.qty < 1:
            raise ValueError("qty must be at least 1")
        return self


class BundleItemResponse(BaseModel):
    id: int
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    qty: int

    model_config = {"from_attributes": True}


class BundleCreate(BaseModel):
    """Define a combo (several services at a bundled price) or a package
    (prepaid sessions of a service, or a value wallet)."""
    kind: BundleKindEnum
    name: str
    price: Decimal
    active: bool = True
    items: list[BundleItemCreate] = []
    credit_kind: Optional[CreditKindEnum] = None
    credit_value: Optional[Decimal] = None
    expires_days: Optional[int] = None

    @model_validator(mode="after")
    def _validate(self):
        if self.price < 0:
            raise ValueError("price cannot be negative")
        if self.kind == BundleKindEnum.combo:
            if self.credit_kind is not None or self.credit_value is not None:
                raise ValueError("A combo has no credit_kind/credit_value")
            if not self.items:
                raise ValueError("A combo needs at least one item")
        else:  # package
            if self.credit_kind is None:
                raise ValueError("A package needs a credit_kind (sessions or value)")
            if self.credit_kind == CreditKindEnum.sessions and not self.items:
                raise ValueError("A sessions package needs at least one item")
            if self.credit_kind == CreditKindEnum.value and (self.credit_value is None or self.credit_value <= 0):
                raise ValueError("A value package needs a positive credit_value")
        if self.expires_days is not None and self.expires_days < 1:
            raise ValueError("expires_days must be at least 1")
        return self


class BundleUpdate(BaseModel):
    """Patch a bundle's scalar fields (items are immutable — recreate to change)."""
    name: Optional[str] = None
    price: Optional[Decimal] = None
    active: Optional[bool] = None
    credit_value: Optional[Decimal] = None
    expires_days: Optional[int] = None


class BundleResponse(BaseModel):
    id: int
    agent_id: int
    kind: BundleKindEnum
    name: str
    price: Decimal
    active: bool
    credit_kind: Optional[CreditKindEnum] = None
    credit_value: Optional[Decimal] = None
    expires_days: Optional[int] = None
    items: list[BundleItemResponse] = []
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BundleListResponse(BaseModel):
    bundles: list[BundleResponse]
    total: int


# ── Combo / package checkout requests ─────────────────────────────────────────

class ComboItemAssignment(BaseModel):
    """Per-line assignment for a combo sale, matched positionally to the bundle's
    items (ordered by id). Both fields optional."""
    employee_id: Optional[int] = None
    appointment_id: Optional[int] = None


class ComboSaleRequest(BaseModel):
    bundle_id: int
    customer_id: Optional[int] = None
    assignments: list[ComboItemAssignment] = []


class PackageSaleRequest(BaseModel):
    bundle_id: int
    customer_id: int


class RedeemRequest(BaseModel):
    """Redeem a prepaid credit against an existing service line on a sale."""
    credit_id: int
    sale_line_item_id: int


# ── Package credit responses ──────────────────────────────────────────────────

class PackageCreditResponse(BaseModel):
    id: int
    customer_id: int
    bundle_id: Optional[int] = None
    agent_id: int
    sale_id: Optional[int] = None
    kind: CreditKindEnum
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    remaining_qty: Optional[int] = None
    balance: Optional[Decimal] = None
    currency: str
    expires_at: Optional[datetime] = None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PackageCreditListResponse(BaseModel):
    credits: list[PackageCreditResponse]
    total: int


# ── Combo booking (Phase 4 booking flow — single employee per combo) ──────────

class ComboOptionItem(BaseModel):
    service_id: Optional[int] = None
    variation_id: Optional[int] = None
    service_name: Optional[str] = None
    variation_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    duration_minutes: int


class ComboOption(BaseModel):
    """One bookable combo slot — a single employee performs the whole combo."""
    employee_id: int
    employee_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    total_duration_minutes: int
    items: list[ComboOptionItem]


class ComboAvailabilityResponse(BaseModel):
    agent_id: int
    bundle_id: int
    bundle_name: str
    date: date
    total_duration_minutes: int
    eligible_employees: int = 0   # staff who can do the WHOLE combo (capable + active + bookable)
    total_options: int
    options: list[ComboOption]


class ComboBookRequest(BaseModel):
    bundle_id: int
    customer_id: int
    employee_id: int
    start_time: datetime
    note: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

class RevenueReportResponse(BaseModel):
    """Per-branch revenue over a date range, in the branch's own currency."""
    agent_id: int
    currency: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    gross: Decimal          # Σ subtotal (before discount/tax/tip)
    discounts: Decimal      # Σ discount_total
    tax: Decimal            # Σ tax_total
    tips: Decimal           # Σ tip_total
    net: Decimal            # Σ total (what the sales are worth)
    collected: Decimal      # Σ payments.amount + tip (actually received)
    outstanding: Decimal    # net − collected
    sales_count: int
    by_status: dict[str, int]


class CommissionReportRow(BaseModel):
    employee_id: Optional[int] = None     # null = unassigned / employee deleted
    employee_name: Optional[str] = None
    line_count: int
    commission_total: Decimal


class CommissionReportResponse(BaseModel):
    """Per-employee commission earnings for a branch over a date range, in the
    branch currency. Excludes void/refunded sales."""
    agent_id: int
    currency: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    commission_total: Decimal
    by_employee: list[CommissionReportRow]
