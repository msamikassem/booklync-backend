"""
app/api/v1/endpoints/financials.py

Financials routes — the merchant's own sales ledger (sales, payments, revenue).
Distinct from /agents subscription billing (`access_billing`); these are gated by
`view_financials`. See docs/modules/financials.md.

Sales are auto-created when an appointment is marked `completed` (see
appointment_service); this surface reads them and records payments.
"""

from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
from app.schemas.financial import (
    SaleResponse,
    SaleListResponse,
    PaymentCreate,
    RevenueReportResponse,
    DiscountResponse,
    DiscountListResponse,
    DiscountCreate,
    DiscountUpdate,
    ApplyDiscountRequest,
    CommissionConfigResponse,
    CommissionConfigUpdate,
    CommissionRuleResponse,
    CommissionRuleListResponse,
    CommissionRuleCreate,
    CommissionRuleUpdate,
    CommissionReportResponse,
    BundleResponse,
    BundleListResponse,
    BundleCreate,
    BundleUpdate,
    ComboSaleRequest,
    PackageSaleRequest,
    RedeemRequest,
    PackageCreditListResponse,
    ComboAvailabilityResponse,
    ComboBookRequest,
)
from app.services import financial_service, combo_booking_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# SALES — list
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/sales",
    response_model=SaleListResponse,
    status_code=200,
    summary="List sales for a branch",
)
def list_sales(
    agent_id: int = Query(..., description="Branch id"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    status: Optional[str] = Query(None, description="open | partially_paid | paid | refunded | void"),
    customer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Sales for a branch, newest first, each with its line items and payments."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.get_sales(
        db, agent_id, current.business_id,
        date_from=date_from, date_to=date_to, status=status, customer_id=customer_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SALES — one
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/sales/{sale_id}",
    response_model=SaleResponse,
    status_code=200,
    summary="Get one sale",
)
def get_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """One sale with its line items and payments."""
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return sale


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS — record a payment against a sale
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/sales/{sale_id}/payments",
    response_model=SaleResponse,
    status_code=201,
    summary="Record a payment against a sale",
)
def record_payment(
    sale_id: int,
    data: PaymentCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Record a payment (optionally with a tip) against a sale; the sale's status is
    recomputed (open → partially_paid → paid). Returns the updated sale.
    """
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return financial_service.record_payment(db, sale_id, data, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS — per-branch revenue
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/reports/revenue",
    response_model=RevenueReportResponse,
    status_code=200,
    summary="Per-branch revenue report (in the branch currency)",
)
def revenue_report(
    agent_id: int = Query(..., description="Branch id"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Revenue totals over a date range for one branch, in that branch's currency."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.get_revenue_report(
        db, agent_id, current.business_id, date_from=date_from, date_to=date_to,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DISCOUNTS — catalogue (define / edit reusable discounts + promo codes)
#   read/use → view_financials · define → manage_discounts
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/discounts",
    response_model=DiscountListResponse,
    status_code=200,
    summary="List a branch's discount catalogue",
)
def list_discounts(
    agent_id: int = Query(..., description="Branch id"),
    active_only: bool = Query(False, description="Only return active discounts"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """The reusable discounts defined for a branch, newest first."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.list_discounts(db, agent_id, current.business_id, active_only=active_only)


@router.post(
    "/discounts",
    response_model=DiscountResponse,
    status_code=201,
    summary="Create a discount",
)
def create_discount(
    data: DiscountCreate,
    agent_id: int = Query(..., description="Branch id"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Define a reusable discount (optionally a promo code) for a branch."""
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=agent_id)
    return financial_service.create_discount(db, agent_id, current.business_id, data)


@router.patch(
    "/discounts/{discount_id}",
    response_model=DiscountResponse,
    status_code=200,
    summary="Edit a discount",
)
def update_discount(
    discount_id: int,
    data: DiscountUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Patch a discount; only provided fields change."""
    discount = financial_service.get_discount(db, discount_id, current.business_id)
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=discount.agent_id)
    return financial_service.update_discount(db, discount_id, current.business_id, data)


@router.delete(
    "/discounts/{discount_id}",
    status_code=204,
    summary="Delete a discount",
)
def delete_discount(
    discount_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Remove a discount from the catalogue (past applications keep their snapshot)."""
    discount = financial_service.get_discount(db, discount_id, current.business_id)
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=discount.agent_id)
    financial_service.delete_discount(db, discount_id, current.business_id)
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# DISCOUNTS — apply / remove on a sale (operating the till → view_financials)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/sales/{sale_id}/discounts",
    response_model=SaleResponse,
    status_code=201,
    summary="Apply a discount to a sale",
)
def apply_discount(
    sale_id: int,
    data: ApplyDiscountRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Apply a discount to a sale — a catalogue discount (`discount_id`/`code`) or an
    ad-hoc one (`type`+`value`). Returns the recomputed sale.
    """
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return financial_service.apply_discount_to_sale(db, sale_id, data, current.business_id)


@router.delete(
    "/sales/{sale_id}/discounts/{sale_discount_id}",
    response_model=SaleResponse,
    status_code=200,
    summary="Remove an applied discount from a sale",
)
def remove_discount(
    sale_id: int,
    sale_discount_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Remove a previously-applied discount from a sale and re-settle it."""
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return financial_service.remove_discount_from_sale(db, sale_id, sale_discount_id, current.business_id)


# ─────────────────────────────────────────────────────────────────────────────
# COMMISSIONS — company config (per business; tenant-level manage_commissions)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/commission-config",
    response_model=CommissionConfigResponse,
    status_code=200,
    summary="Get the company commission policy",
)
def get_commission_config(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """How commissions are computed company-wide (gross/net + surcharge inclusion)."""
    current.require(rbac.P_MANAGE_COMMISSIONS)  # tenant-level (owner-only by default)
    return financial_service.get_commission_config(db, current.business_id)


@router.patch(
    "/commission-config",
    response_model=CommissionConfigResponse,
    status_code=200,
    summary="Update the company commission policy",
)
def update_commission_config(
    data: CommissionConfigUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Change how commissions are computed. Does not rewrite past sales."""
    current.require(rbac.P_MANAGE_COMMISSIONS)
    return financial_service.update_commission_config(db, current.business_id, data)


# ─────────────────────────────────────────────────────────────────────────────
# COMMISSIONS — per-branch rules (manage_commissions at the branch)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/commission-rules",
    response_model=CommissionRuleListResponse,
    status_code=200,
    summary="List a branch's commission rules",
)
def list_commission_rules(
    agent_id: int = Query(..., description="Branch id"),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Commission rules for a branch, newest first."""
    current.require(rbac.P_MANAGE_COMMISSIONS, agent_id=agent_id)
    return financial_service.list_commission_rules(db, agent_id, current.business_id, active_only=active_only)


@router.post(
    "/commission-rules",
    response_model=CommissionRuleResponse,
    status_code=201,
    summary="Create a commission rule",
)
def create_commission_rule(
    data: CommissionRuleCreate,
    agent_id: int = Query(..., description="Branch id"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Define how an employee is paid for a service/title/variation (or a branch default)."""
    current.require(rbac.P_MANAGE_COMMISSIONS, agent_id=agent_id)
    return financial_service.create_commission_rule(db, agent_id, current.business_id, data)


@router.patch(
    "/commission-rules/{rule_id}",
    response_model=CommissionRuleResponse,
    status_code=200,
    summary="Edit a commission rule",
)
def update_commission_rule(
    rule_id: int,
    data: CommissionRuleUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Patch a commission rule; only provided fields change."""
    rule = financial_service.get_commission_rule(db, rule_id, current.business_id)
    current.require(rbac.P_MANAGE_COMMISSIONS, agent_id=rule.agent_id)
    return financial_service.update_commission_rule(db, rule_id, current.business_id, data)


@router.delete(
    "/commission-rules/{rule_id}",
    status_code=204,
    summary="Delete a commission rule",
)
def delete_commission_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Delete a commission rule (past sales keep their stored commission)."""
    rule = financial_service.get_commission_rule(db, rule_id, current.business_id)
    current.require(rbac.P_MANAGE_COMMISSIONS, agent_id=rule.agent_id)
    financial_service.delete_commission_rule(db, rule_id, current.business_id)
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# COMMISSIONS — per-employee report (view_financials)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/reports/commissions",
    response_model=CommissionReportResponse,
    status_code=200,
    summary="Per-employee commission report (in the branch currency)",
)
def commission_report(
    agent_id: int = Query(..., description="Branch id"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """What each employee earned in commission over a date range, for one branch."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.get_commission_report(
        db, agent_id, current.business_id, date_from=date_from, date_to=date_to,
    )


# ─────────────────────────────────────────────────────────────────────────────
# BUNDLES — combo + package catalogue
#   view/sell → view_financials · define → manage_discounts
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/bundles",
    response_model=BundleListResponse,
    status_code=200,
    summary="List a branch's combos + packages",
)
def list_bundles(
    agent_id: int = Query(..., description="Branch id"),
    kind: Optional[str] = Query(None, description="combo | package"),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """The combos and packages defined for a branch, newest first."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.list_bundles(db, agent_id, current.business_id, kind=kind, active_only=active_only)


@router.post(
    "/bundles",
    response_model=BundleResponse,
    status_code=201,
    summary="Create a combo or package",
)
def create_bundle(
    data: BundleCreate,
    agent_id: int = Query(..., description="Branch id"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Define a combo (services at a bundled price) or a package (prepaid credit)."""
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=agent_id)
    return financial_service.create_bundle(db, agent_id, current.business_id, data)


@router.get(
    "/bundles/{bundle_id}",
    response_model=BundleResponse,
    status_code=200,
    summary="Get one bundle",
)
def get_bundle(
    bundle_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """One combo/package with its items."""
    bundle = financial_service.get_bundle(db, bundle_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=bundle.agent_id)
    return bundle


@router.patch(
    "/bundles/{bundle_id}",
    response_model=BundleResponse,
    status_code=200,
    summary="Edit a bundle",
)
def update_bundle(
    bundle_id: int,
    data: BundleUpdate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Patch a bundle's scalar fields (items are immutable — recreate to change)."""
    bundle = financial_service.get_bundle(db, bundle_id, current.business_id)
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=bundle.agent_id)
    return financial_service.update_bundle(db, bundle_id, current.business_id, data)


@router.delete(
    "/bundles/{bundle_id}",
    status_code=204,
    summary="Delete a bundle",
)
def delete_bundle(
    bundle_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Delete a combo/package (past sales + credits keep their snapshot)."""
    bundle = financial_service.get_bundle(db, bundle_id, current.business_id)
    current.require(rbac.P_MANAGE_DISCOUNTS, agent_id=bundle.agent_id)
    financial_service.delete_bundle(db, bundle_id, current.business_id)
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# COMBO + PACKAGE checkout (operating the till → view_financials)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/sales/combo",
    response_model=SaleResponse,
    status_code=201,
    summary="Ring up a combo as one bundled-price sale",
)
def create_combo_sale(
    data: ComboSaleRequest,
    agent_id: int = Query(..., description="Branch id"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """One sale, one line per combo service, priced to the bundle total with
    per-line commission. Optionally links each line to an already-booked appointment."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.create_combo_sale(db, agent_id, current.business_id, data)


@router.post(
    "/sales/package",
    response_model=SaleResponse,
    status_code=201,
    summary="Sell a prepaid package",
)
def create_package_sale(
    data: PackageSaleRequest,
    agent_id: int = Query(..., description="Branch id"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Sell a package; the customer's prepaid credit is granted once the sale is paid."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.create_package_sale(db, agent_id, current.business_id, data)


@router.post(
    "/sales/{sale_id}/redeem",
    response_model=SaleResponse,
    status_code=201,
    summary="Redeem a prepaid credit against a sale line",
)
def redeem_credit(
    sale_id: int,
    data: RedeemRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Make a service line free by drawing on a customer's prepaid credit."""
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return financial_service.redeem_credit(db, sale_id, data, current.business_id)


@router.delete(
    "/sales/{sale_id}/redeem/{sale_line_item_id}",
    response_model=SaleResponse,
    status_code=200,
    summary="Undo a redemption on a sale line",
)
def undo_redeem(
    sale_id: int,
    sale_line_item_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Reverse a redemption: restore the credit and re-charge the line."""
    sale = financial_service.get_sale(db, sale_id, current.business_id)
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=sale.agent_id)
    return financial_service.undo_redeem(db, sale_id, sale_line_item_id, current.business_id)


@router.get(
    "/credits",
    response_model=PackageCreditListResponse,
    status_code=200,
    summary="List prepaid credits at a branch",
)
def list_credits(
    agent_id: int = Query(..., description="Branch id"),
    customer_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """A branch's prepaid package credits, optionally for one customer."""
    current.require(rbac.P_VIEW_FINANCIALS, agent_id=agent_id)
    return financial_service.list_credits(
        db, agent_id, current.business_id, customer_id=customer_id, active_only=active_only,
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMBO BOOKING — find back-to-back slots + book the chain (single employee)
#   booking action → manage_appointments (the bundled sale is a side effect)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/bundles/{bundle_id}/availability",
    response_model=ComboAvailabilityResponse,
    status_code=200,
    summary="Find bookable slots for a combo on a date",
)
def combo_availability(
    bundle_id: int,
    requested_date: date = Query(..., alias="date", description="Date to search (YYYY-MM-DD)"),
    employee_id: Optional[int] = Query(None, description="Restrict to one stylist"),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Combo slots on a date — one stylist performs the whole combo back-to-back."""
    bundle = financial_service.get_bundle(db, bundle_id, current.business_id)
    current.require(rbac.P_MANAGE_APPOINTMENTS, agent_id=bundle.agent_id)
    return combo_booking_service.get_combo_availability(
        db, bundle.agent_id, current.business_id, bundle_id, requested_date, employee_id=employee_id,
    )


@router.post(
    "/sales/combo/book",
    response_model=SaleResponse,
    status_code=201,
    summary="Book a combo (chained appointments + bundled sale)",
)
def book_combo(
    data: ComboBookRequest,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Book a combo: chained appointments by one stylist + the linked bundled sale,
    created atomically. Returns the sale (its line items carry the appointment ids)."""
    bundle = financial_service.get_bundle(db, data.bundle_id, current.business_id)
    current.require(rbac.P_MANAGE_APPOINTMENTS, agent_id=bundle.agent_id)
    return combo_booking_service.book_combo(
        db, bundle.agent_id, current.business_id, data.bundle_id,
        data.customer_id, data.employee_id, data.start_time, note=data.note,
    )
