"""
app/services/financial_service.py

Business logic for the financials module — the merchant's own sales ledger.
(NOT the SaaS subscription billing; see docs/modules/financials.md.)

Phase 1 surface:
  - create_sale_for_appointment   — auto-create a Sale + line when an appointment
                                     is completed (called by appointment_service).
  - void_sale_for_appointment     — reverse it if the appointment leaves completed.
  - record_payment                — settle a sale; recompute status.
  - get_sales / get_sale          — read the ledger.
  - get_revenue_report            — per-branch revenue, in the branch currency.

Money rules:
  - Every amount is a Decimal quantized to 2 dp.
  - Prices are SNAPSHOTTED on the line at sale time — later edits to Service.price
    never rewrite history.
  - Sale invariant: total = subtotal − discount_total + tax_total + tip_total.
    `balance_due` (what payments settle) = total − tip_total; tips are extra.
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from loguru import logger

from app.models.models import (
    AIAgent,
    Appointment,
    Bundle,
    BundleItem,
    BundleKindEnum,
    Business,
    CommissionBaseEnum,
    CommissionRule,
    CommissionTypeEnum,
    CreditKindEnum,
    Customer,
    Discount,
    DiscountScopeEnum,
    DiscountTypeEnum,
    Employee,
    EmployeeCapability,
    PackageCredit,
    Payment,
    Sale,
    SaleDiscount,
    SaleLineItem,
    SaleLineKindEnum,
    SaleStatusEnum,
    Service,
    ServiceTitle,
    ServiceVariation,
)
from app.schemas.financial import (
    PaymentCreate,
    DiscountCreate,
    DiscountUpdate,
    ApplyDiscountRequest,
    CommissionRuleCreate,
    CommissionRuleUpdate,
    CommissionConfigUpdate,
    BundleCreate,
    BundleUpdate,
    ComboSaleRequest,
    PackageSaleRequest,
    RedeemRequest,
)
from app.core.exceptions import (
    AppException,
    NotFoundException,
    ForbiddenException,
    ConflictException,
)
from app.core.exceptions import (
    AppException,
    NotFoundException,
    ForbiddenException,
    ConflictException,
)


_CENTS = Decimal("0.01")
# Statuses whose money is excluded from sale recomputation + revenue reports.
_DEAD_STATUSES = (SaleStatusEnum.void, SaleStatusEnum.refunded)
# Sales whose discounts may still be changed (before/while settling).
_EDITABLE_STATUSES = (SaleStatusEnum.open, SaleStatusEnum.partially_paid)
# Marker on the line-scoped SaleDiscount that zeroes a redeemed (prepaid) line.
_REDEMPTION_LABEL = "Package redemption"


def _money(value) -> Decimal:
    """Quantize any numeric to 2 decimal places (half-up); None → 0."""
    return Decimal(value or 0).quantize(_CENTS, rounding=ROUND_HALF_UP)


def _z(v) -> Decimal:
    """Coalesce None → 0 as a raw Decimal (a transient, not-yet-flushed line has
    None for columns whose Python-side default only materialises at flush)."""
    return Decimal(v) if v is not None else Decimal("0")


def _enum_val(x):
    """The string value of an enum-or-string (robust to either form)."""
    return getattr(x, "value", x)


def _get_agent_for_business(db: Session, agent_id: int, business_id: int) -> AIAgent:
    """Fetch a branch and verify it belongs to the tenant."""
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Branch not found", code="AGENT_NOT_FOUND")
    if agent.business_id != business_id:
        raise ForbiddenException(detail="You do not have access to this branch")
    return agent


# ─────────────────────────────────────────────────────────────────────────────
# Money helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_line_tax(gross: Decimal, tax_rate, tax_inclusive: bool):
    """
    Given the pre-tax-or-sticker gross (unit_price*qty + surcharge − discount),
    return (subtotal_portion, tax_amount, line_total).

      - exclusive: tax is added on top.       line_total = gross + tax
      - inclusive: tax is backed out of gross. line_total = gross (price unchanged)
      - no tax:    tax_amount = 0.            line_total = gross
    """
    if not tax_rate or tax_rate == 0:
        return _money(gross), Decimal("0.00"), _money(gross)

    rate = Decimal(tax_rate) / Decimal("100")
    if tax_inclusive:
        tax = _money(gross - (gross / (Decimal("1") + rate)))
        subtotal_portion = _money(gross - tax)
        line_total = _money(gross)
    else:
        tax = _money(gross * rate)
        subtotal_portion = _money(gross)
        line_total = _money(gross + tax)
    return subtotal_portion, tax, line_total


def _line_goods(line: SaleLineItem) -> Decimal:
    """The pre-discount, pre-tax value of a line: unit_price·qty + surcharge."""
    return _money(_z(line.unit_price) * _z(line.qty) + _z(line.surcharge))


def _discount_amount(disc_type, value, base: Decimal) -> Decimal:
    """Money a single discount takes off `base`, never exceeding it (floor 0)."""
    base = _money(base)
    if base <= 0:
        return Decimal("0.00")
    kind = getattr(disc_type, "value", disc_type)
    if kind == DiscountTypeEnum.percent.value:
        amt = _money(base * _z(value) / Decimal("100"))
    else:  # fixed
        amt = _money(value)
    return amt if amt < base else base


def _recompute_line(line: SaleLineItem, tax_rate, tax_inclusive: bool) -> None:
    """Recompute a line's tax + total from its goods minus its discount_amount."""
    gross = _line_goods(line) - _z(line.discount_amount)
    if gross < 0:
        gross = Decimal("0.00")
    _subtotal_portion, tax_amount, line_total = _compute_line_tax(gross, tax_rate, tax_inclusive)
    line.tax_amount = tax_amount
    line.line_total = line_total


# ─────────────────────────────────────────────────────────────────────────────
# Commission helpers (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def _commission_base_for_line(line: SaleLineItem, includes_surcharge: bool, base_mode) -> Decimal:
    """
    The amount an employee's commission is computed on for one line.

      - tips are ALWAYS excluded (they're never on the line in v1 anyway);
      - `includes_surcharge` decides whether the capability surcharge counts;
      - `base_mode` net subtracts the line discount (the share attributable to the
        commissioned goods when surcharge is excluded), gross does not. Floor 0.
    """
    qty_price = _money(_z(line.unit_price) * _z(line.qty))
    goods_full = _money(qty_price + _z(line.surcharge))
    goods_comm = goods_full if includes_surcharge else qty_price

    if _enum_val(base_mode) != CommissionBaseEnum.net.value:
        return _money(goods_comm)

    disc = _z(line.discount_amount)
    if includes_surcharge or goods_full <= 0:
        base = goods_comm - disc
    else:
        # Only the portion of the discount attributable to the commissioned goods.
        base = goods_comm - _money(disc * goods_comm / goods_full)
    return _money(base) if base > 0 else Decimal("0.00")


def _resolve_commission_rule(
    db: Session, agent_id: int, employee_id: Optional[int],
    service_id: Optional[int], variation_id: Optional[int], title_id: Optional[int],
):
    """
    Most-specific-first commission rule for this line, within its branch.

    Specificity score = employee weight (4 if the rule names this employee, else 0)
    + target weight (variation 3 / service 2 / title 1 / all-null 0). Highest wins;
    employee-qualified rules therefore beat target-only rules. No match → None.
    Rule targets that don't match the line are skipped.
    """
    rules = (
        db.query(CommissionRule)
        .filter(
            CommissionRule.agent_id == agent_id,
            CommissionRule.active.is_(True),
            (CommissionRule.employee_id == employee_id) | (CommissionRule.employee_id.is_(None)),
        )
        .all()
    )

    best, best_score = None, -1
    for r in rules:
        if r.variation_id is not None:
            if r.variation_id != variation_id:
                continue
            target = 3
        elif r.service_id is not None:
            if r.service_id != service_id:
                continue
            target = 2
        elif r.title_id is not None:
            if r.title_id != title_id:
                continue
            target = 1
        else:
            target = 0
        score = (4 if r.employee_id is not None else 0) + target
        if score > best_score:
            best, best_score = r, score
    return best


def _recompute_commission(db: Session, line: SaleLineItem, agent: Optional[AIAgent],
                          business: Optional[Business]) -> None:
    """Set the line's commission_rate + commission_amount from the resolved rule
    and the company commission policy. No employee/rule → 0 (does not touch totals)."""
    line.commission_rate = None
    line.commission_amount = Decimal("0.00")
    if not line.employee_id or agent is None or business is None:
        return

    service = (
        db.query(Service).filter(Service.id == line.service_id).first()
        if line.service_id else None
    )
    title_id = service.title_id if service else None

    rule = _resolve_commission_rule(
        db, agent.id, line.employee_id, line.service_id, line.variation_id, title_id
    )
    if rule is None:
        return

    base_mode = business.commission_base
    if line.redeemed_credit_id is not None:
        # Prepaid (package redemption): the line is free to the customer but the
        # employee is still commissioned on the full list value — regardless of
        # the company gross/net policy. Force gross so the redemption discount
        # isn't netted out of the base.
        base_mode = CommissionBaseEnum.gross
    base = _commission_base_for_line(
        line, bool(business.commission_includes_surcharge), base_mode
    )
    if _enum_val(rule.type) == CommissionTypeEnum.percent.value:
        line.commission_rate = _money(rule.value)
        line.commission_amount = _money(base * _z(rule.value) / Decimal("100"))
    else:  # flat — a fixed amount per line, independent of the base
        line.commission_rate = None
        line.commission_amount = _money(rule.value)


def _resolve_surcharge(db: Session, employee_id: Optional[int], service: Optional[Service],
                       variation_id: Optional[int]) -> Decimal:
    """
    The performing employee's extra_price for this work, most-specific first:
    variation → service → title. Mirrors EmployeeCapability's grant hierarchy.
    """
    if not employee_id or not service:
        return Decimal("0.00")

    base = db.query(EmployeeCapability).filter(EmployeeCapability.employee_id == employee_id)

    if variation_id:
        cap = base.filter(EmployeeCapability.variation_id == variation_id).first()
        if cap:
            return _money(cap.extra_price)
    cap = base.filter(EmployeeCapability.service_id == service.id).first()
    if cap:
        return _money(cap.extra_price)
    if service.title_id:
        cap = base.filter(EmployeeCapability.title_id == service.title_id).first()
        if cap:
            return _money(cap.extra_price)
    return Decimal("0.00")


def _recompute_sale_totals(sale: Sale) -> None:
    """
    Recompute the header totals + status from the lines and payments.

    Derives the pre-tax subtotal from each line so the result is correct for both
    tax-inclusive and tax-exclusive pricing:
        subtotal_portion = line_total + discount_amount − tax_amount   (tip is 0 on lines in v1)
    """
    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")
    tax_total = Decimal("0.00")
    for li in sale.line_items:
        subtotal += (_z(li.line_total) + _z(li.discount_amount) - _z(li.tax_amount) - _z(li.tip_amount))
        discount_total += _z(li.discount_amount)
        tax_total += _z(li.tax_amount)

    tip_total = sum((_z(p.tip_amount) for p in sale.payments), Decimal("0.00"))
    paid_amount = sum((_z(p.amount) for p in sale.payments), Decimal("0.00"))

    sale.subtotal = _money(subtotal)
    sale.discount_total = _money(discount_total)
    sale.tax_total = _money(tax_total)
    sale.tip_total = _money(tip_total)
    sale.total = _money(sale.subtotal - sale.discount_total + sale.tax_total + sale.tip_total)

    # Payments settle the balance (goods + tax − discount); tips are extra.
    balance_due = _money(sale.subtotal - sale.discount_total + sale.tax_total)
    if sale.status not in _DEAD_STATUSES:
        if balance_due <= 0:
            # Nothing left to collect — fully covered by discounts or a redeemed
            # credit (or a $0 ticket). It's settled even with no payment row, so
            # mark it paid rather than leaving it stuck "open".
            sale.status = SaleStatusEnum.paid
        elif paid_amount <= 0:
            sale.status = SaleStatusEnum.open
        elif _money(paid_amount) >= balance_due:
            sale.status = SaleStatusEnum.paid
        else:
            sale.status = SaleStatusEnum.partially_paid


def _rebuild_sale(db: Session, sale: Sale) -> None:
    """
    Rebuild every derived money figure on a sale from its lines + discounts.

    `line.discount_amount` / `sale.discount_total` are DERIVED from the set of
    `SaleDiscount` rows, so this is idempotent and order-independent:
      1. reset each line's discount to 0;
      2. for each SaleDiscount, compute its amount and lay it onto the line(s):
         line-scope → its target line; order-scope → pro-rata by each line's
         goods share (rounding remainder to the last line);
      3. cap each line's total discount at its own goods (stacked discounts can
         never drive a line — or the sale — negative);
      4. recompute each line's tax + total, then the header totals + status.
    """
    agent = db.query(AIAgent).filter(AIAgent.id == sale.agent_id).first()
    tax_rate = agent.tax_rate if agent else None
    tax_inclusive = bool(agent.tax_inclusive) if agent else False
    # Company commission policy (Phase 3) — recomputed here so a net-based
    # commission tracks discount changes on a still-open sale.
    business = (
        db.query(Business).filter(Business.id == agent.business_id).first()
        if agent else None
    )

    lines = list(sale.line_items)
    for li in lines:
        li.discount_amount = Decimal("0.00")

    goods = {id(li): _line_goods(li) for li in lines}
    total_goods = sum(goods.values(), Decimal("0.00"))

    for sd in sale.discounts:
        target_line = None
        if sd.sale_line_item_id is not None:
            target_line = next((li for li in lines if li.id == sd.sale_line_item_id), None)

        if target_line is not None:
            amt = _discount_amount(sd.type, sd.value, goods[id(target_line)])
            target_line.discount_amount = _money(_z(target_line.discount_amount) + amt)
            sd.amount = amt
        else:
            # Order scope — allocate pro-rata across all lines.
            if total_goods <= 0:
                sd.amount = Decimal("0.00")
                continue
            raw = _discount_amount(sd.type, sd.value, total_goods)
            allocated = Decimal("0.00")
            for i, li in enumerate(lines):
                if i == len(lines) - 1:
                    share = _money(raw - allocated)           # last line absorbs rounding
                else:
                    share = _money(raw * goods[id(li)] / total_goods)
                    allocated += share
                li.discount_amount = _money(_z(li.discount_amount) + share)
            sd.amount = raw

    for li in lines:
        cap = goods[id(li)]
        if _z(li.discount_amount) > cap:
            li.discount_amount = cap
        _recompute_line(li, tax_rate, tax_inclusive)
        _recompute_commission(db, li, agent, business)

    _recompute_sale_totals(sale)


# ─────────────────────────────────────────────────────────────────────────────
# Appointment completion hook (called by appointment_service)
# ─────────────────────────────────────────────────────────────────────────────

def create_sale_for_appointment(
    db: Session,
    appointment: Appointment,
    created_by_user_id: Optional[int] = None,
) -> Optional[Sale]:
    """
    Idempotently create a Sale (one service line) for a completed appointment.

    Returns the existing sale if one already covers this appointment (no-op), the
    new sale otherwise. Snapshots price (variation > service) + the employee's
    capability surcharge, and applies the branch tax. Commits on success.
    """
    # Idempotency — already invoiced?
    existing_line = (
        db.query(SaleLineItem)
        .filter(SaleLineItem.appointment_id == appointment.id)
        .first()
    )
    if existing_line:
        return db.query(Sale).filter(Sale.id == existing_line.sale_id).first()

    agent = db.query(AIAgent).filter(AIAgent.id == appointment.agent_id).first()
    if not agent:
        return None

    service = (
        db.query(Service).filter(Service.id == appointment.service_id).first()
        if appointment.service_id else None
    )

    # Snapshot unit price: variation overrides service flat price.
    unit_price = Decimal("0.00")
    if appointment.variation_id:
        variation = (
            db.query(ServiceVariation)
            .filter(ServiceVariation.id == appointment.variation_id)
            .first()
        )
        if variation:
            unit_price = _money(variation.price)
    elif service and service.price is not None:
        unit_price = _money(service.price)

    surcharge = _resolve_surcharge(db, appointment.employee_id, service, appointment.variation_id)

    description = appointment.service_name or (service.name if service else None)
    if appointment.variation_name:
        description = f"{description} — {appointment.variation_name}" if description else appointment.variation_name

    sale = Sale(
        agent_id=agent.id,
        customer_id=appointment.customer_id,
        created_by_user_id=created_by_user_id,
        status=SaleStatusEnum.open,
        currency=agent.currency,
    )
    line = SaleLineItem(
        kind=SaleLineKindEnum.service,
        service_id=appointment.service_id,
        variation_id=appointment.variation_id,
        appointment_id=appointment.id,
        employee_id=appointment.employee_id,
        description=description,
        employee_name=appointment.employee_name,
        qty=Decimal("1"),
        unit_price=unit_price,
        surcharge=surcharge,
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        tip_amount=Decimal("0.00"),
        line_total=Decimal("0.00"),
    )
    sale.line_items.append(line)

    # Single tax path: rebuild derives tax + totals (no discounts on a new sale).
    _rebuild_sale(db, sale)

    db.add(sale)
    db.commit()
    db.refresh(sale)

    logger.info(
        f"Sale created from appointment {appointment.id}: sale={sale.id} "
        f"total={sale.total} {sale.currency}"
    )
    return sale


def void_sale_for_appointment(db: Session, appointment: Appointment) -> None:
    """
    Reverse the auto-created sale when an appointment leaves `completed`.

    Only safe to reverse an UNPAID sale. If payments exist, the sale is left
    intact (money was taken — a refund is a Phase ≥2 manual action) and a warning
    is logged. Commits on success.
    """
    line = (
        db.query(SaleLineItem)
        .filter(SaleLineItem.appointment_id == appointment.id)
        .first()
    )
    if not line:
        return

    sale = (
        db.query(Sale)
        .options(
            selectinload(Sale.line_items),
            selectinload(Sale.payments),
            selectinload(Sale.discounts),
        )
        .filter(Sale.id == line.sale_id)
        .first()
    )
    if not sale:
        return

    if sale.payments:
        logger.warning(
            f"Appointment {appointment.id} left 'completed' but sale {sale.id} has "
            f"payments — leaving sale intact (manual refund required)."
        )
        return

    # A combo is priced as a unit → it reverses as a unit. Un-completing any one
    # of its services voids the whole combo sale (restoring any redeemed credit),
    # rather than picking lines apart (which would mis-allocate the bundle's
    # pro-rata discounts across the survivors).
    if sale.bundle_id is not None:
        _restore_credits_for_sale(db, sale)
        db.delete(sale)
        db.commit()
        logger.info(f"Reversed combo/package sale {sale.id} as a unit (appointment {appointment.id}).")
        return

    # Single line restored to credit if it was a redemption.
    if line.redeemed_credit_id is not None:
        _restore_one_credit(db, sale, line)

    # Drop just this appointment's line; delete the whole sale if it was the only
    # one (deleting the sale cascades the line — don't also delete it explicitly,
    # or the cascade matches 0 rows and SQLAlchemy warns).
    survivors = [li for li in sale.line_items if li.id != line.id]
    if survivors:
        db.delete(line)
        db.flush()
        db.refresh(sale)
        _rebuild_sale(db, sale)
    else:
        db.delete(sale)
    db.commit()
    logger.info(f"Reversed sale for appointment {appointment.id} (sale={sale.id}).")


# ─────────────────────────────────────────────────────────────────────────────
# Payments
# ─────────────────────────────────────────────────────────────────────────────

def record_payment(
    db: Session,
    sale_id: int,
    data: PaymentCreate,
    business_id: int,
) -> Sale:
    """Record a payment (optionally with a tip) against a sale and re-settle it."""
    # Lock the sale row up-front so concurrent payments serialize instead of racing
    # (read-then-write TOCTOU); without this, N simultaneous "pay the balance" calls
    # all succeed and over-settle the sale.
    locked = db.query(Sale).filter(Sale.id == sale_id).with_for_update().first()
    if not locked:
        raise NotFoundException(detail="Sale not found", code="SALE_NOT_FOUND")

    # Re-load FRESH under the lock. populate_existing() is essential: the endpoint
    # (and other callers) may have already loaded this sale + its payments into the
    # session's identity map BEFORE the lock; without populate_existing the cached
    # (pre-lock) payments collection is reused, so the overpayment check below would
    # read a stale balance and concurrent payments would still over-settle.
    sale = (
        db.query(Sale)
        .options(
            selectinload(Sale.line_items),
            selectinload(Sale.payments),
            selectinload(Sale.discounts),
        )
        .filter(Sale.id == sale_id)
        .populate_existing()
        .first()
    )

    _get_agent_for_business(db, sale.agent_id, business_id)

    if sale.status in _DEAD_STATUSES:
        raise ConflictException(
            detail=f"Cannot record a payment against a {sale.status.value} sale",
            code="SALE_CLOSED",
        )

    # Reject a payment that would settle more than the outstanding balance.
    # Balance to settle = total − tips (tips are extra, not part of the balance);
    # already-paid = sum of prior payment amounts. (The row lock above makes this
    # read-modify-write safe under concurrency.)
    settle_target = _money(sale.total) - _money(sale.tip_total)
    already_paid = sum((_money(p.amount) for p in sale.payments), Decimal("0"))
    outstanding = settle_target - already_paid
    if _money(data.amount) - outstanding > Decimal("0.01"):
        raise ConflictException(
            detail=f"Payment exceeds the outstanding balance ({outstanding})",
            code="PAYMENT_EXCEEDS_BALANCE",
        )

    payment = Payment(
        sale_id=sale.id,
        amount=_money(data.amount),
        tip_amount=_money(data.tip_amount),
        method=data.method,
        note=data.note,
    )
    db.add(payment)
    db.flush()
    db.refresh(sale)

    _recompute_sale_totals(sale)
    # A package purchase grants its prepaid credit(s) once the sale is fully paid.
    _grant_package_credits_if_paid(db, sale)
    db.commit()
    db.refresh(sale)

    logger.info(
        f"Payment recorded: sale={sale.id} amount={payment.amount} tip={payment.tip_amount} "
        f"method={payment.method.value} → status={sale.status.value}"
    )
    return sale


# ─────────────────────────────────────────────────────────────────────────────
# Reads
# ─────────────────────────────────────────────────────────────────────────────

def get_sales(
    db: Session,
    agent_id: int,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
) -> dict:
    """List sales for a branch, newest first, with line items + payments."""
    _get_agent_for_business(db, agent_id, business_id)

    query = (
        db.query(Sale)
        .options(
            selectinload(Sale.line_items),
            selectinload(Sale.payments),
            selectinload(Sale.discounts),
        )
        .filter(Sale.agent_id == agent_id)
    )
    if date_from:
        query = query.filter(Sale.created_at >= date_from)
    if date_to:
        query = query.filter(Sale.created_at <= date_to)
    if status:
        query = query.filter(Sale.status == status)
    if customer_id:
        query = query.filter(Sale.customer_id == customer_id)

    sales = query.order_by(Sale.created_at.desc()).all()
    return {"sales": sales, "total": len(sales)}


def get_sale(db: Session, sale_id: int, business_id: int) -> Sale:
    """Fetch one sale (with lines + payments), tenant-verified."""
    sale = (
        db.query(Sale)
        .options(
            selectinload(Sale.line_items),
            selectinload(Sale.payments),
            selectinload(Sale.discounts),
        )
        .filter(Sale.id == sale_id)
        .first()
    )
    if not sale:
        raise NotFoundException(detail="Sale not found", code="SALE_NOT_FOUND")
    _get_agent_for_business(db, sale.agent_id, business_id)
    return sale


def get_revenue_report(
    db: Session,
    agent_id: int,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """
    Per-branch revenue over a date range (on Sale.created_at), in the branch's
    own currency. Void/refunded sales are excluded from money totals. No
    cross-branch aggregation — that (with FX) is a later phase.
    """
    agent = _get_agent_for_business(db, agent_id, business_id)

    def _range(q):
        q = q.filter(Sale.agent_id == agent_id)
        if date_from:
            q = q.filter(Sale.created_at >= date_from)
        if date_to:
            q = q.filter(Sale.created_at <= date_to)
        return q

    live = (Sale.status.notin_([s.value for s in _DEAD_STATUSES]))

    gross, discounts, tax, tips, net, count = (
        _range(db.query(
            func.coalesce(func.sum(Sale.subtotal), 0),
            func.coalesce(func.sum(Sale.discount_total), 0),
            func.coalesce(func.sum(Sale.tax_total), 0),
            func.coalesce(func.sum(Sale.tip_total), 0),
            func.coalesce(func.sum(Sale.total), 0),
            func.count(Sale.id),
        ).filter(live)).one()
    )

    collected = (
        _range(
            db.query(func.coalesce(func.sum(Payment.amount + Payment.tip_amount), 0))
            .join(Sale, Sale.id == Payment.sale_id)
        ).filter(live)
    ).scalar()

    status_rows = _range(
        db.query(Sale.status, func.count(Sale.id)).group_by(Sale.status)
    ).all()
    by_status = {row[0].value: row[1] for row in status_rows}

    net = _money(net)
    collected = _money(collected)
    return {
        "agent_id": agent_id,
        "currency": agent.currency,
        "date_from": date_from,
        "date_to": date_to,
        "gross": _money(gross),
        "discounts": _money(discounts),
        "tax": _money(tax),
        "tips": _money(tips),
        "net": net,
        "collected": collected,
        "outstanding": _money(net - collected),
        "sales_count": int(count),
        "by_status": by_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Discounts — catalogue (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

def create_discount(db: Session, agent_id: int, business_id: int, data: DiscountCreate) -> Discount:
    """Create a reusable discount for a branch."""
    _get_agent_for_business(db, agent_id, business_id)

    if data.code:
        clash = (
            db.query(Discount)
            .filter(Discount.agent_id == agent_id, Discount.code == data.code)
            .first()
        )
        if clash:
            raise ConflictException(
                detail=f"A discount with code '{data.code}' already exists at this branch",
                code="DISCOUNT_CODE_EXISTS",
            )

    discount = Discount(
        agent_id=agent_id,
        name=data.name,
        type=data.type,
        scope=data.scope,
        value=_money(data.value),
        code=data.code,
        active=data.active,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        max_uses=data.max_uses,
        per_customer_limit=data.per_customer_limit,
    )
    db.add(discount)
    db.commit()
    db.refresh(discount)
    logger.info(f"Discount created: id={discount.id} agent={agent_id} type={discount.type.value} value={discount.value}")
    return discount


def get_discount(db: Session, discount_id: int, business_id: int) -> Discount:
    """Fetch one discount, tenant-verified (used for permission resolution)."""
    discount = db.query(Discount).filter(Discount.id == discount_id).first()
    if not discount:
        raise NotFoundException(detail="Discount not found", code="DISCOUNT_NOT_FOUND")
    _get_agent_for_business(db, discount.agent_id, business_id)
    return discount


def list_discounts(db: Session, agent_id: int, business_id: int, active_only: bool = False) -> dict:
    """List a branch's discount catalogue, newest first."""
    _get_agent_for_business(db, agent_id, business_id)
    q = db.query(Discount).filter(Discount.agent_id == agent_id)
    if active_only:
        q = q.filter(Discount.active.is_(True))
    rows = q.order_by(Discount.created_at.desc()).all()
    return {"discounts": rows, "total": len(rows)}


def update_discount(db: Session, discount_id: int, business_id: int, data: DiscountUpdate) -> Discount:
    """Patch a discount. Only fields present in the request change."""
    discount = db.query(Discount).filter(Discount.id == discount_id).first()
    if not discount:
        raise NotFoundException(detail="Discount not found", code="DISCOUNT_NOT_FOUND")
    _get_agent_for_business(db, discount.agent_id, business_id)

    fields = data.model_dump(exclude_unset=True)

    if "code" in fields and fields["code"]:
        clash = (
            db.query(Discount)
            .filter(
                Discount.agent_id == discount.agent_id,
                Discount.code == fields["code"],
                Discount.id != discount.id,
            )
            .first()
        )
        if clash:
            raise ConflictException(
                detail=f"A discount with code '{fields['code']}' already exists at this branch",
                code="DISCOUNT_CODE_EXISTS",
            )

    # Validate percent ≤ 100 against the *effective* type/value (persisted unless changed).
    eff_type = fields.get("type", discount.type)
    eff_value = fields.get("value", discount.value)
    eff_kind = getattr(eff_type, "value", eff_type)
    if eff_kind == DiscountTypeEnum.percent.value and eff_value is not None and Decimal(eff_value) > 100:
        raise AppException(detail="A percentage discount cannot exceed 100", code="INVALID_DISCOUNT_VALUE", status_code=400)

    if "value" in fields and fields["value"] is not None:
        fields["value"] = _money(fields["value"])

    for k, v in fields.items():
        setattr(discount, k, v)

    if discount.valid_from and discount.valid_to and discount.valid_to < discount.valid_from:
        raise AppException(detail="valid_to cannot be before valid_from", code="INVALID_DISCOUNT_WINDOW", status_code=400)

    db.commit()
    db.refresh(discount)
    return discount


def delete_discount(db: Session, discount_id: int, business_id: int) -> None:
    """
    Delete a discount from the catalogue. Past applications keep their snapshot
    (SaleDiscount.discount_id → NULL via SET NULL), so sales history is intact.
    """
    discount = db.query(Discount).filter(Discount.id == discount_id).first()
    if not discount:
        raise NotFoundException(detail="Discount not found", code="DISCOUNT_NOT_FOUND")
    _get_agent_for_business(db, discount.agent_id, business_id)
    db.delete(discount)
    db.commit()
    logger.info(f"Discount deleted: id={discount_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Discounts — apply / remove on a sale (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

def _load_sale(db: Session, sale_id: int, business_id: int) -> Sale:
    """Load a sale with lines, payments and discounts; tenant-verified."""
    sale = (
        db.query(Sale)
        .options(
            selectinload(Sale.line_items),
            selectinload(Sale.payments),
            selectinload(Sale.discounts),
        )
        .filter(Sale.id == sale_id)
        .first()
    )
    if not sale:
        raise NotFoundException(detail="Sale not found", code="SALE_NOT_FOUND")
    _get_agent_for_business(db, sale.agent_id, business_id)
    return sale


def _assert_sale_editable(sale: Sale) -> None:
    if sale.status not in _EDITABLE_STATUSES:
        raise ConflictException(
            detail=f"Cannot change discounts on a {sale.status.value} sale",
            code="SALE_NOT_EDITABLE",
        )


def _usage_count(db: Session, discount_id: int, customer_id: Optional[int] = None) -> int:
    """How many times a catalogue discount has been applied across non-dead sales."""
    q = (
        db.query(func.count(SaleDiscount.id))
        .join(Sale, Sale.id == SaleDiscount.sale_id)
        .filter(SaleDiscount.discount_id == discount_id)
        .filter(Sale.status.notin_([s.value for s in _DEAD_STATUSES]))
    )
    if customer_id is not None:
        q = q.filter(Sale.customer_id == customer_id)
    return q.scalar() or 0


def _validate_catalogue_discount(db: Session, discount: Discount, sale: Sale) -> None:
    """Active / validity-window / dedup / usage-cap checks for a catalogue discount."""
    if not discount.active:
        raise ConflictException(detail="This discount is inactive", code="DISCOUNT_INACTIVE")

    now = datetime.now(timezone.utc)
    if discount.valid_from and now < discount.valid_from:
        raise ConflictException(detail="This discount is not active yet", code="DISCOUNT_NOT_STARTED")
    if discount.valid_to and now > discount.valid_to:
        raise ConflictException(detail="This discount has expired", code="DISCOUNT_EXPIRED")

    if any(sd.discount_id == discount.id for sd in sale.discounts):
        raise ConflictException(detail="This discount is already applied to the sale", code="DISCOUNT_ALREADY_APPLIED")

    if discount.max_uses is not None and _usage_count(db, discount.id) >= discount.max_uses:
        raise ConflictException(detail="This discount has reached its usage limit", code="DISCOUNT_LIMIT_REACHED")

    if (
        discount.per_customer_limit is not None
        and sale.customer_id is not None
        and _usage_count(db, discount.id, customer_id=sale.customer_id) >= discount.per_customer_limit
    ):
        raise ConflictException(
            detail="This customer has already used this discount the maximum number of times",
            code="DISCOUNT_CUSTOMER_LIMIT",
        )


def apply_discount_to_sale(db: Session, sale_id: int, data: ApplyDiscountRequest, business_id: int) -> Sale:
    """
    Apply a discount to a sale and re-settle it.

    Catalogue path (discount_id/code) snapshots the catalogue terms and enforces
    active/validity/usage caps; ad-hoc path (type+value) is a one-off, no caps.
    A `sale_line_item_id` targets one line (line scope); otherwise the discount is
    order-scope and allocated pro-rata across the lines. A promo "use" is counted
    on application (applying then never paying still consumes a use).
    """
    sale = _load_sale(db, sale_id, business_id)
    _assert_sale_editable(sale)

    discount: Optional[Discount] = None
    if data.discount_id is not None:
        discount = (
            db.query(Discount)
            .filter(Discount.id == data.discount_id, Discount.agent_id == sale.agent_id)
            .first()
        )
        if not discount:
            raise NotFoundException(detail="Discount not found", code="DISCOUNT_NOT_FOUND")
    elif data.code is not None:
        discount = (
            db.query(Discount)
            .filter(Discount.agent_id == sale.agent_id, Discount.code == data.code)
            .first()
        )
        if not discount:
            raise NotFoundException(detail="No discount with that code", code="DISCOUNT_NOT_FOUND")

    if discount is not None:
        _validate_catalogue_discount(db, discount, sale)
        d_type, d_value = discount.type, discount.value
        d_scope, d_name, d_code, d_id = discount.scope, discount.name, discount.code, discount.id
    else:  # ad-hoc
        d_type, d_value = data.type, data.value
        d_scope, d_name, d_code, d_id = (data.scope or DiscountScopeEnum.order), (data.name or "Discount"), None, None

    # Effective scope: a targeted line wins; else order scope (single-line + a
    # line-scope catalogue discount collapses onto that one line).
    line_id = data.sale_line_item_id
    if line_id is not None:
        if not any(li.id == line_id for li in sale.line_items):
            raise NotFoundException(detail="Line item not found on this sale", code="SALE_LINE_NOT_FOUND")
        eff_scope = DiscountScopeEnum.line
    elif d_scope == DiscountScopeEnum.line and len(sale.line_items) == 1:
        line_id = sale.line_items[0].id
        eff_scope = DiscountScopeEnum.line
    else:
        eff_scope = DiscountScopeEnum.order

    sd = SaleDiscount(
        sale_id=sale.id,
        discount_id=d_id,
        sale_line_item_id=line_id,
        name=d_name,
        code=d_code,
        type=d_type,
        scope=eff_scope,
        value=_money(d_value),
        amount=Decimal("0.00"),
    )
    sale.discounts.append(sd)
    db.add(sd)
    db.flush()
    db.refresh(sale)

    _rebuild_sale(db, sale)
    db.commit()
    db.refresh(sale)

    logger.info(
        f"Discount applied: sale={sale.id} discount_id={d_id or 'adhoc'} "
        f"amount={sd.amount} → total={sale.total} {sale.currency}"
    )
    return sale


def remove_discount_from_sale(db: Session, sale_id: int, sale_discount_id: int, business_id: int) -> Sale:
    """Remove an applied discount from a sale and re-settle it."""
    sale = _load_sale(db, sale_id, business_id)
    _assert_sale_editable(sale)

    sd = next((x for x in sale.discounts if x.id == sale_discount_id), None)
    if not sd:
        raise NotFoundException(detail="Discount not found on this sale", code="SALE_DISCOUNT_NOT_FOUND")

    db.delete(sd)
    db.flush()
    db.refresh(sale)

    _rebuild_sale(db, sale)
    db.commit()
    db.refresh(sale)

    logger.info(f"Discount removed: sale={sale.id} sale_discount={sale_discount_id} → total={sale.total}")
    return sale


# ─────────────────────────────────────────────────────────────────────────────
# Commissions — company config (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def get_commission_config(db: Session, business_id: int) -> Business:
    """The company-wide commission policy (read from the Business row)."""
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise NotFoundException(detail="Business not found", code="BUSINESS_NOT_FOUND")
    return business


def update_commission_config(db: Session, business_id: int, data: CommissionConfigUpdate) -> Business:
    """Update the company-wide commission policy. Does NOT retro-rewrite past
    sales — commissions recompute on the next rebuild of each still-open sale."""
    business = get_commission_config(db, business_id)
    fields = data.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(business, k, v)
    db.commit()
    db.refresh(business)
    logger.info(
        f"Commission config updated: business={business_id} "
        f"base={_enum_val(business.commission_base)} incl_surcharge={business.commission_includes_surcharge}"
    )
    return business


# ─────────────────────────────────────────────────────────────────────────────
# Commissions — per-branch rules (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_rule_targets(db: Session, agent_id: int, title_id, service_id, variation_id) -> None:
    """Ensure any service-level target belongs to this branch (tenant isolation)."""
    if service_id is not None:
        ok = db.query(Service.id).filter(Service.id == service_id, Service.agent_id == agent_id).first()
        if not ok:
            raise NotFoundException(detail="Service not found at this branch", code="SERVICE_NOT_FOUND")
    if title_id is not None:
        ok = db.query(ServiceTitle.id).filter(ServiceTitle.id == title_id, ServiceTitle.agent_id == agent_id).first()
        if not ok:
            raise NotFoundException(detail="Service title not found at this branch", code="TITLE_NOT_FOUND")
    if variation_id is not None:
        ok = (
            db.query(ServiceVariation.id)
            .join(Service, Service.id == ServiceVariation.service_id)
            .filter(ServiceVariation.id == variation_id, Service.agent_id == agent_id)
            .first()
        )
        if not ok:
            raise NotFoundException(detail="Variation not found at this branch", code="VARIATION_NOT_FOUND")


def _rule_combo_exists(db: Session, agent_id: int, employee_id, title_id, service_id, variation_id,
                       exclude_id: Optional[int] = None) -> bool:
    """True if a rule with the same (branch, employee, target) combo already exists
    (NULL-aware) — prevents ambiguous duplicates at the same specificity."""
    q = db.query(CommissionRule.id).filter(CommissionRule.agent_id == agent_id)
    for col, val in (
        (CommissionRule.employee_id, employee_id),
        (CommissionRule.title_id, title_id),
        (CommissionRule.service_id, service_id),
        (CommissionRule.variation_id, variation_id),
    ):
        q = q.filter(col.is_(None) if val is None else col == val)
    if exclude_id is not None:
        q = q.filter(CommissionRule.id != exclude_id)
    return db.query(q.exists()).scalar()


def create_commission_rule(db: Session, agent_id: int, business_id: int, data: CommissionRuleCreate) -> CommissionRule:
    """Create a per-branch commission rule."""
    _get_agent_for_business(db, agent_id, business_id)
    _validate_rule_targets(db, agent_id, data.title_id, data.service_id, data.variation_id)

    if _rule_combo_exists(db, agent_id, data.employee_id, data.title_id, data.service_id, data.variation_id):
        raise ConflictException(
            detail="A commission rule for this employee/target already exists at this branch",
            code="COMMISSION_RULE_EXISTS",
        )

    rule = CommissionRule(
        agent_id=agent_id,
        employee_id=data.employee_id,
        title_id=data.title_id,
        service_id=data.service_id,
        variation_id=data.variation_id,
        type=data.type,
        value=_money(data.value),
        active=data.active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    logger.info(f"Commission rule created: id={rule.id} agent={agent_id} type={rule.type.value} value={rule.value}")
    return rule


def list_commission_rules(db: Session, agent_id: int, business_id: int, active_only: bool = False) -> dict:
    """List a branch's commission rules, newest first."""
    _get_agent_for_business(db, agent_id, business_id)
    q = db.query(CommissionRule).filter(CommissionRule.agent_id == agent_id)
    if active_only:
        q = q.filter(CommissionRule.active.is_(True))
    rows = q.order_by(CommissionRule.created_at.desc()).all()
    return {"commission_rules": rows, "total": len(rows)}


def get_commission_rule(db: Session, rule_id: int, business_id: int) -> CommissionRule:
    """Fetch one commission rule, tenant-verified (used for permission resolution)."""
    rule = db.query(CommissionRule).filter(CommissionRule.id == rule_id).first()
    if not rule:
        raise NotFoundException(detail="Commission rule not found", code="COMMISSION_RULE_NOT_FOUND")
    _get_agent_for_business(db, rule.agent_id, business_id)
    return rule


def update_commission_rule(db: Session, rule_id: int, business_id: int, data: CommissionRuleUpdate) -> CommissionRule:
    """Patch a commission rule. Only provided fields change."""
    rule = get_commission_rule(db, rule_id, business_id)
    fields = data.model_dump(exclude_unset=True)

    # Effective targets after the patch.
    eff_title = fields.get("title_id", rule.title_id)
    eff_service = fields.get("service_id", rule.service_id)
    eff_variation = fields.get("variation_id", rule.variation_id)
    if sum(x is not None for x in (eff_title, eff_service, eff_variation)) > 1:
        raise AppException(detail="Set at most one of title_id / service_id / variation_id",
                           code="INVALID_COMMISSION_TARGET", status_code=400)
    _validate_rule_targets(db, rule.agent_id, eff_title, eff_service, eff_variation)

    # Effective percent ≤ 100.
    eff_type = fields.get("type", rule.type)
    eff_value = fields.get("value", rule.value)
    if _enum_val(eff_type) == CommissionTypeEnum.percent.value and eff_value is not None and Decimal(eff_value) > 100:
        raise AppException(detail="A percentage commission cannot exceed 100",
                           code="INVALID_COMMISSION_VALUE", status_code=400)

    eff_employee = fields.get("employee_id", rule.employee_id)
    if _rule_combo_exists(db, rule.agent_id, eff_employee, eff_title, eff_service, eff_variation, exclude_id=rule.id):
        raise ConflictException(
            detail="A commission rule for this employee/target already exists at this branch",
            code="COMMISSION_RULE_EXISTS",
        )

    if "value" in fields and fields["value"] is not None:
        fields["value"] = _money(fields["value"])
    for k, v in fields.items():
        setattr(rule, k, v)

    db.commit()
    db.refresh(rule)
    return rule


def delete_commission_rule(db: Session, rule_id: int, business_id: int) -> None:
    """Delete a commission rule. Past sales keep their stored commission_amount."""
    rule = get_commission_rule(db, rule_id, business_id)
    db.delete(rule)
    db.commit()
    logger.info(f"Commission rule deleted: id={rule_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Commissions — report (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

def get_commission_report(
    db: Session,
    agent_id: int,
    business_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """
    Per-employee commission earnings for a branch over a date range, in the
    branch currency. Sums `SaleLineItem.commission_amount` over non-void/refunded
    sales (a reversed sale must not pay anyone). Lines with no commission are
    excluded. Grouped by employee.
    """
    agent = _get_agent_for_business(db, agent_id, business_id)

    q = (
        db.query(
            SaleLineItem.employee_id,
            func.max(SaleLineItem.employee_name),
            func.count(SaleLineItem.id),
            func.coalesce(func.sum(SaleLineItem.commission_amount), 0),
        )
        .join(Sale, Sale.id == SaleLineItem.sale_id)
        .filter(Sale.agent_id == agent_id)
        .filter(Sale.status.notin_([s.value for s in _DEAD_STATUSES]))
        .filter(SaleLineItem.commission_amount > 0)
    )
    if date_from:
        q = q.filter(Sale.created_at >= date_from)
    if date_to:
        q = q.filter(Sale.created_at <= date_to)
    q = q.group_by(SaleLineItem.employee_id)

    by_employee = []
    total = Decimal("0.00")
    for emp_id, emp_name, line_count, commission_total in q.all():
        amt = _money(commission_total)
        total += amt
        by_employee.append({
            "employee_id": emp_id,
            "employee_name": emp_name,
            "line_count": int(line_count),
            "commission_total": amt,
        })
    by_employee.sort(key=lambda r: r["commission_total"], reverse=True)

    return {
        "agent_id": agent_id,
        "currency": agent.currency,
        "date_from": date_from,
        "date_to": date_to,
        "commission_total": _money(total),
        "by_employee": by_employee,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bundles — combo + package catalogue (Phase 4/5)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_item_pricing(db: Session, service_id: Optional[int], variation_id: Optional[int]):
    """(service, variation, unit_price, description) for a bundle item / combo line."""
    if variation_id:
        variation = db.query(ServiceVariation).filter(ServiceVariation.id == variation_id).first()
        service = db.query(Service).filter(Service.id == variation.service_id).first() if variation else None
        price = _money(variation.price) if variation and variation.price is not None else Decimal("0.00")
        if service and variation:
            desc = f"{service.name} — {variation.name}"
        else:
            desc = variation.name if variation else None
        return service, variation, price, desc
    service = db.query(Service).filter(Service.id == service_id).first() if service_id else None
    price = _money(service.price) if service and service.price is not None else Decimal("0.00")
    return service, None, price, (service.name if service else None)


def _validate_bundle_item_targets(db: Session, agent_id: int, items) -> None:
    """Ensure every bundle item's service/variation belongs to this branch."""
    for it in items:
        if it.variation_id is not None:
            ok = (
                db.query(ServiceVariation.id)
                .join(Service, Service.id == ServiceVariation.service_id)
                .filter(ServiceVariation.id == it.variation_id, Service.agent_id == agent_id)
                .first()
            )
            if not ok:
                raise NotFoundException(detail="Variation not found at this branch", code="VARIATION_NOT_FOUND")
        elif it.service_id is not None:
            ok = db.query(Service.id).filter(Service.id == it.service_id, Service.agent_id == agent_id).first()
            if not ok:
                raise NotFoundException(detail="Service not found at this branch", code="SERVICE_NOT_FOUND")


def create_bundle(db: Session, agent_id: int, business_id: int, data: BundleCreate) -> Bundle:
    """Create a combo or package for a branch."""
    _get_agent_for_business(db, agent_id, business_id)
    _validate_bundle_item_targets(db, agent_id, data.items)

    bundle = Bundle(
        agent_id=agent_id,
        kind=data.kind,
        name=data.name,
        price=_money(data.price),
        active=data.active,
        credit_kind=data.credit_kind,
        credit_value=_money(data.credit_value) if data.credit_value is not None else None,
        expires_days=data.expires_days,
    )
    for it in data.items:
        bundle.items.append(BundleItem(service_id=it.service_id, variation_id=it.variation_id, qty=it.qty))
    db.add(bundle)
    db.commit()
    db.refresh(bundle)
    logger.info(f"Bundle created: id={bundle.id} agent={agent_id} kind={bundle.kind.value} price={bundle.price}")
    return bundle


def list_bundles(db: Session, agent_id: int, business_id: int,
                 kind: Optional[str] = None, active_only: bool = False) -> dict:
    """List a branch's bundles (combos + packages), newest first."""
    _get_agent_for_business(db, agent_id, business_id)
    q = db.query(Bundle).options(selectinload(Bundle.items)).filter(Bundle.agent_id == agent_id)
    if kind:
        q = q.filter(Bundle.kind == kind)
    if active_only:
        q = q.filter(Bundle.active.is_(True))
    rows = q.order_by(Bundle.created_at.desc()).all()
    return {"bundles": rows, "total": len(rows)}


def get_bundle(db: Session, bundle_id: int, business_id: int) -> Bundle:
    """Fetch one bundle (with items), tenant-verified."""
    bundle = (
        db.query(Bundle).options(selectinload(Bundle.items)).filter(Bundle.id == bundle_id).first()
    )
    if not bundle:
        raise NotFoundException(detail="Bundle not found", code="BUNDLE_NOT_FOUND")
    _get_agent_for_business(db, bundle.agent_id, business_id)
    return bundle


def update_bundle(db: Session, bundle_id: int, business_id: int, data: BundleUpdate) -> Bundle:
    """Patch a bundle's scalar fields (items are immutable)."""
    bundle = get_bundle(db, bundle_id, business_id)
    fields = data.model_dump(exclude_unset=True)
    if "price" in fields and fields["price"] is not None:
        fields["price"] = _money(fields["price"])
    if "credit_value" in fields and fields["credit_value"] is not None:
        fields["credit_value"] = _money(fields["credit_value"])
    for k, v in fields.items():
        setattr(bundle, k, v)
    db.commit()
    db.refresh(bundle)
    return bundle


def delete_bundle(db: Session, bundle_id: int, business_id: int) -> None:
    """Delete a bundle. Past sales/credits keep their snapshot (FKs SET NULL)."""
    bundle = get_bundle(db, bundle_id, business_id)
    db.delete(bundle)
    db.commit()
    logger.info(f"Bundle deleted: id={bundle_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Combo sale (Phase 4)
# ─────────────────────────────────────────────────────────────────────────────

def create_combo_sale(db: Session, agent_id: int, business_id: int, data: ComboSaleRequest) -> Sale:
    """
    Ring up a combo as ONE sale: one line per bundle item (list price snapshot),
    with the bundle's savings (Σ list − bundle.price, clamped ≥0) allocated
    pro-rata across the lines as line-scoped fixed discounts. `_rebuild_sale`
    then makes the lines sum to the bundle price and computes per-line commission.
    """
    agent = _get_agent_for_business(db, agent_id, business_id)
    bundle = (
        db.query(Bundle).options(selectinload(Bundle.items))
        .filter(Bundle.id == data.bundle_id, Bundle.agent_id == agent_id).first()
    )
    if not bundle:
        raise NotFoundException(detail="Bundle not found", code="BUNDLE_NOT_FOUND")
    if bundle.kind != BundleKindEnum.combo:
        raise AppException(detail="This bundle is not a combo", code="NOT_A_COMBO", status_code=400)
    if not bundle.active:
        raise ConflictException(detail="This combo is inactive", code="BUNDLE_INACTIVE")

    items = sorted(bundle.items, key=lambda i: i.id)
    if not items:
        raise ConflictException(detail="This combo has no items", code="BUNDLE_EMPTY")

    assignments = data.assignments or []
    customer_id = data.customer_id

    sale = Sale(agent_id=agent_id, customer_id=customer_id, bundle_id=bundle.id,
                status=SaleStatusEnum.open, currency=agent.currency)
    line_objs = []
    for idx, item in enumerate(items):
        asg = assignments[idx] if idx < len(assignments) else None
        employee_id = asg.employee_id if asg else None
        appointment_id = asg.appointment_id if asg else None

        service, variation, unit_price, desc = _resolve_item_pricing(db, item.service_id, item.variation_id)

        if appointment_id is not None:
            appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
            if not appt or appt.agent_id != agent_id:
                raise NotFoundException(detail="Appointment not found at this branch", code="APPOINTMENT_NOT_FOUND")
            already = db.query(SaleLineItem).filter(SaleLineItem.appointment_id == appointment_id).first()
            if already:
                raise ConflictException(
                    detail="That appointment is already on a sale — ring up the combo before completing it",
                    code="APPOINTMENT_ALREADY_INVOICED",
                )
            if customer_id is None:
                customer_id = appt.customer_id

        employee_name = None
        if employee_id is not None:
            emp = db.query(Employee).filter(Employee.id == employee_id).first()
            employee_name = emp.name if emp else None

        line = SaleLineItem(
            kind=SaleLineKindEnum.service,
            service_id=item.service_id, variation_id=item.variation_id,
            appointment_id=appointment_id, employee_id=employee_id, bundle_id=bundle.id,
            description=desc, employee_name=employee_name,
            qty=Decimal(item.qty), unit_price=unit_price, surcharge=Decimal("0.00"),
            discount_amount=Decimal("0.00"), tax_amount=Decimal("0.00"),
            tip_amount=Decimal("0.00"), line_total=Decimal("0.00"),
        )
        sale.line_items.append(line)
        line_objs.append(line)

    sale.customer_id = customer_id
    db.add(sale)
    db.flush()  # assign line ids for the line-scoped discounts

    goods = [_line_goods(li) for li in line_objs]
    total_goods = sum(goods, Decimal("0.00"))
    savings = _money(total_goods - _money(bundle.price))
    if savings > 0 and total_goods > 0:
        allocated = Decimal("0.00")
        for i, li in enumerate(line_objs):
            if i == len(line_objs) - 1:
                share = _money(savings - allocated)
            else:
                share = _money(savings * goods[i] / total_goods)
                allocated += share
            if share > 0:
                sd = SaleDiscount(
                    sale_id=sale.id, sale_line_item_id=li.id, discount_id=None,
                    name=f"Combo: {bundle.name}", type=DiscountTypeEnum.fixed,
                    scope=DiscountScopeEnum.line, value=share, amount=Decimal("0.00"),
                )
                sale.discounts.append(sd)
                db.add(sd)
        db.flush()

    _rebuild_sale(db, sale)
    db.commit()
    db.refresh(sale)
    logger.info(f"Combo sale created: sale={sale.id} bundle={bundle.id} total={sale.total} {sale.currency}")
    return sale


# ─────────────────────────────────────────────────────────────────────────────
# Packages — buy / grant / redeem (Phase 5)
# ─────────────────────────────────────────────────────────────────────────────

def create_package_sale(db: Session, agent_id: int, business_id: int, data: PackageSaleRequest) -> Sale:
    """Sell a package: one `package` line at the bundle price. Credits are granted
    when the sale is PAID (see record_payment), not at creation."""
    agent = _get_agent_for_business(db, agent_id, business_id)
    bundle = (
        db.query(Bundle).options(selectinload(Bundle.items))
        .filter(Bundle.id == data.bundle_id, Bundle.agent_id == agent_id).first()
    )
    if not bundle:
        raise NotFoundException(detail="Bundle not found", code="BUNDLE_NOT_FOUND")
    if bundle.kind != BundleKindEnum.package:
        raise AppException(detail="This bundle is not a package", code="NOT_A_PACKAGE", status_code=400)
    if not bundle.active:
        raise ConflictException(detail="This package is inactive", code="BUNDLE_INACTIVE")

    customer = db.query(Customer).filter(Customer.id == data.customer_id, Customer.business_id == business_id).first()
    if not customer:
        raise NotFoundException(detail="Customer not found", code="CUSTOMER_NOT_FOUND")

    sale = Sale(agent_id=agent_id, customer_id=data.customer_id, bundle_id=bundle.id,
                status=SaleStatusEnum.open, currency=agent.currency)
    sale.line_items.append(SaleLineItem(
        kind=SaleLineKindEnum.package, bundle_id=bundle.id, description=bundle.name,
        qty=Decimal("1"), unit_price=_money(bundle.price), surcharge=Decimal("0.00"),
        discount_amount=Decimal("0.00"), tax_amount=Decimal("0.00"),
        tip_amount=Decimal("0.00"), line_total=Decimal("0.00"),
    ))
    _rebuild_sale(db, sale)
    db.add(sale)
    db.commit()
    db.refresh(sale)
    logger.info(f"Package sale created: sale={sale.id} bundle={bundle.id} total={sale.total} (credit on payment)")
    return sale


def _grant_package_credits_if_paid(db: Session, sale: Sale) -> None:
    """When a package sale becomes `paid`, grant its prepaid credit(s). Idempotent
    (keyed on PackageCredit.sale_id)."""
    if sale.status != SaleStatusEnum.paid or not sale.bundle_id:
        return
    if not any(_enum_val(li.kind) == SaleLineKindEnum.package.value for li in sale.line_items):
        return
    if db.query(PackageCredit.id).filter(PackageCredit.sale_id == sale.id).first():
        return  # already granted

    bundle = db.query(Bundle).options(selectinload(Bundle.items)).filter(Bundle.id == sale.bundle_id).first()
    if not bundle or bundle.kind != BundleKindEnum.package:
        return

    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=bundle.expires_days)
        if bundle.expires_days else None
    )
    if bundle.credit_kind == CreditKindEnum.sessions:
        for item in bundle.items:
            db.add(PackageCredit(
                customer_id=sale.customer_id, bundle_id=bundle.id, agent_id=sale.agent_id, sale_id=sale.id,
                kind=CreditKindEnum.sessions, service_id=item.service_id, variation_id=item.variation_id,
                remaining_qty=item.qty, balance=None, currency=sale.currency, expires_at=expires_at, active=True,
            ))
    else:  # value wallet
        val = _money(bundle.credit_value if bundle.credit_value is not None else bundle.price)
        db.add(PackageCredit(
            customer_id=sale.customer_id, bundle_id=bundle.id, agent_id=sale.agent_id, sale_id=sale.id,
            kind=CreditKindEnum.value, remaining_qty=None, balance=val,
            currency=sale.currency, expires_at=expires_at, active=True,
        ))
    db.flush()
    logger.info(f"Package credits granted for sale {sale.id} (bundle {bundle.id}).")


def redeem_credit(db: Session, sale_id: int, data: RedeemRequest, business_id: int) -> Sale:
    """
    Redeem a prepaid credit against an existing service line on a sale: the line
    becomes free to the customer (a line-scoped discount = the redeemed amount)
    and the credit is decremented. The line stays commissioned on full list value.
    """
    sale = _load_sale(db, sale_id, business_id)
    _assert_sale_editable(sale)

    line = next((li for li in sale.line_items if li.id == data.sale_line_item_id), None)
    if not line:
        raise NotFoundException(detail="Line item not found on this sale", code="SALE_LINE_NOT_FOUND")
    if _enum_val(line.kind) != SaleLineKindEnum.service.value:
        raise AppException(detail="Only a service line can be redeemed", code="LINE_NOT_REDEEMABLE", status_code=400)
    if line.redeemed_credit_id is not None:
        raise ConflictException(detail="This line is already redeemed", code="LINE_ALREADY_REDEEMED")

    credit = db.query(PackageCredit).filter(PackageCredit.id == data.credit_id).first()
    if not credit:
        raise NotFoundException(detail="Credit not found", code="CREDIT_NOT_FOUND")
    _get_agent_for_business(db, credit.agent_id, business_id)
    if credit.agent_id != sale.agent_id:
        raise ConflictException(detail="That credit belongs to a different branch", code="CREDIT_WRONG_BRANCH")
    if not credit.active:
        raise ConflictException(detail="That credit is no longer active", code="CREDIT_INACTIVE")
    if credit.expires_at and datetime.now(timezone.utc) > credit.expires_at:
        raise ConflictException(detail="That credit has expired", code="CREDIT_EXPIRED")
    if sale.customer_id is not None and credit.customer_id != sale.customer_id:
        raise ConflictException(detail="That credit belongs to a different customer", code="CREDIT_WRONG_CUSTOMER")

    goods = _line_goods(line)
    if credit.kind == CreditKindEnum.sessions:
        if (credit.remaining_qty or 0) < 1:
            raise ConflictException(detail="No sessions left on that credit", code="CREDIT_DEPLETED")
        if credit.variation_id is not None and credit.variation_id != line.variation_id:
            raise ConflictException(detail="That credit is for a different service", code="CREDIT_SERVICE_MISMATCH")
        if credit.variation_id is None and credit.service_id is not None and credit.service_id != line.service_id:
            raise ConflictException(detail="That credit is for a different service", code="CREDIT_SERVICE_MISMATCH")
        draw = goods  # a session covers the whole service
    else:  # value wallet
        if _z(credit.balance) <= 0:
            raise ConflictException(detail="No balance left on that credit", code="CREDIT_DEPLETED")
        draw = _money(min(goods, _z(credit.balance)))

    if draw <= 0:
        raise ConflictException(detail="Nothing to redeem on this line", code="NOTHING_TO_REDEEM")

    sd = SaleDiscount(
        sale_id=sale.id, sale_line_item_id=line.id, discount_id=None,
        name=_REDEMPTION_LABEL, type=DiscountTypeEnum.fixed, scope=DiscountScopeEnum.line,
        value=draw, amount=Decimal("0.00"),
    )
    sale.discounts.append(sd)
    db.add(sd)
    line.redeemed_credit_id = credit.id
    if credit.kind == CreditKindEnum.sessions:
        credit.remaining_qty = (credit.remaining_qty or 0) - 1
    else:
        credit.balance = _money(_z(credit.balance) - draw)

    db.flush()
    db.refresh(sale)
    _rebuild_sale(db, sale)
    db.commit()
    db.refresh(sale)
    logger.info(f"Credit {credit.id} redeemed on sale {sale.id} line {line.id} (draw={draw}).")
    return sale


def undo_redeem(db: Session, sale_id: int, sale_line_item_id: int, business_id: int) -> Sale:
    """Reverse a redemption on a line: restore the credit and remove the free-line discount."""
    sale = _load_sale(db, sale_id, business_id)
    _assert_sale_editable(sale)

    line = next((li for li in sale.line_items if li.id == sale_line_item_id), None)
    if not line or line.redeemed_credit_id is None:
        raise NotFoundException(detail="That line is not a redemption", code="LINE_NOT_REDEEMED")

    _restore_one_credit(db, sale, line)
    for sd in list(sale.discounts):
        if sd.sale_line_item_id == line.id and sd.name == _REDEMPTION_LABEL:
            db.delete(sd)
    line.redeemed_credit_id = None

    db.flush()
    db.refresh(sale)
    _rebuild_sale(db, sale)
    db.commit()
    db.refresh(sale)
    logger.info(f"Redemption undone on sale {sale.id} line {line.id}.")
    return sale


def _restore_one_credit(db: Session, sale: Sale, line: SaleLineItem) -> None:
    """Give back to a credit what a line redeemed from it (sessions +1, or the
    value drawn). Idempotent enough for reversal; re-activates a revoked credit."""
    credit = db.query(PackageCredit).filter(PackageCredit.id == line.redeemed_credit_id).first()
    if not credit:
        return
    if credit.kind == CreditKindEnum.sessions:
        credit.remaining_qty = (credit.remaining_qty or 0) + int(_z(line.qty) or Decimal("1"))
    else:
        drawn = sum(
            (_z(sd.value) for sd in sale.discounts
             if sd.sale_line_item_id == line.id and sd.name == _REDEMPTION_LABEL),
            Decimal("0.00"),
        )
        credit.balance = _money(_z(credit.balance) + drawn)
    credit.active = True


def _restore_credits_for_sale(db: Session, sale: Sale) -> None:
    """Restore every credit redeemed by this sale's lines (used on whole-sale void)."""
    for li in sale.line_items:
        if li.redeemed_credit_id is not None:
            _restore_one_credit(db, sale, li)


def list_credits(db: Session, agent_id: int, business_id: int,
                 customer_id: Optional[int] = None, active_only: bool = True) -> dict:
    """List prepaid credits at a branch (optionally for one customer)."""
    _get_agent_for_business(db, agent_id, business_id)
    q = db.query(PackageCredit).filter(PackageCredit.agent_id == agent_id)
    if customer_id is not None:
        q = q.filter(PackageCredit.customer_id == customer_id)
    if active_only:
        q = q.filter(PackageCredit.active.is_(True))
    rows = q.order_by(PackageCredit.created_at.desc()).all()
    return {"credits": rows, "total": len(rows)}
