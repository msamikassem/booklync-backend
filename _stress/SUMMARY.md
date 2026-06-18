# Booklync API — Professional Stress-Test Summary

**Target:** live server `http://localhost:8000` · local `demo_data` Postgres · tested black-box against `docs/api_reference.md`.
**Method:** an HTTP harness (`_stress/`) that registers two fresh isolated tenants (A + B), DB-seeds a
limited **receptionist** and an employee-linked **staff** user, then exercises **every endpoint** with
valid / invalid / boundary inputs, missing-and-forged auth, RBAC denials, cross-tenant access, the full
booking-conflict matrix, money-invariant checks, idempotency, a tenant-isolation sweep, a UTC+14
timezone day-boundary test, and two concurrency races.

**Final tally:** 2 CRITICAL · 4 HIGH (2 distinct issues) · 6 MEDIUM · 8 LOW · 7 INFO.

**Coverage:** ~139 of 154 endpoints exercised on success + failure paths. The remaining ~15 are the
endpoints with **paid/real-world side effects** (LLM, SMS, email, Vapi, Vercel) whose *happy paths were
skipped per your instruction* — their auth/validation/pre-call guards were still tested where reachable.
See "Skipped" at the bottom.

## What is SOLID (explicitly verified, no findings)

- **Tenant isolation.** A full sweep of B's token against A's agents/employees/services/customers/
  appointments/sales/discounts/commissions/conversations — plus A's token pointed at B's branch —
  **never** leaked data or succeeded. Every cross-tenant attempt was correctly 403/404. **No leaks.**
- **Money arithmetic.** `total = subtotal − discount_total + tax_total + tip_total` held **to the cent**
  after every operation (auto-sale, order discount, payments, tips, commission, combo, package, redeem).
  Tax correctly recomputes on the **post-discount** base; payments settle `total − tip`; combo savings and
  package redemptions land in `discount_total`. **No invariant violations.**
- **Booking-conflict engine.** All documented 409s fire correctly and deliberately on the write path:
  `PAST_APPOINTMENT`, `OUTSIDE_BUSINESS_HOURS`, `OUTSIDE_WORKING_HOURS`, `BRANCH_DAY_OFF`,
  `EMPLOYEE_DAY_OFF`, `BREAK_CONFLICT`, `SLOT_UNAVAILABLE`, `PREP_TIME_CONFLICT`, `VARIATION_REQUIRED`,
  `EMPLOYEE_NOT_BOOKABLE`. Batch booking is **atomic** (a failed item creates none) and shares one
  `booking_group_id`.
- **Double-booking under concurrency holds.** 8 simultaneous identical bookings of one slot → exactly
  **one 201, seven 409** (one row persisted). The create path is race-safe.
- **RBAC.** Receptionist/staff correctly denied owner-gated actions (`manage_branches`, `manage_employees`,
  `manage_users`, `view_financials`, branch-calendar). Owner-protection, staff own-scope, `NO_BRANCH_ACCESS`,
  and `NOT_AN_EMPLOYEE` all enforced.
- **Auth.** Forged `alg=none` tokens, tampered tokens, access-token-as-refresh, expired/used invites all
  rejected. Accept-invite, refresh, login, password rules all correct.

## Findings (by severity)

### 🔴 Unhandled 500s (2)

| # | Endpoint | Trigger | Root cause | Impact |
|---|----------|---------|-----------|--------|
| 1 | `GET /public/business/{slug}/services` | branch has **any variation-based service** | `public.py:155` `price=float(s.price)` — `s.price` is `None` for variation services → `TypeError` | **Public booking page breaks** for any salon that uses service variations (a very common setup). Highest user impact; public-facing. |
| 2 | `POST /auth/register` | `business_type_id` that doesn't exist | `auth_service.register` never validates the FK; the insert hits an IntegrityError that isn't handled | Malformed registration returns 500 instead of 400/422. Lower impact than #1 (only fires on bad input). |

> Fix #1: handle `None` price (e.g. derive from cheapest variation, or return `null`). Fix #2: validate
> `business_type_id` exists → 400/404 (`BUSINESS_TYPE_NOT_FOUND`).

### 🟠 HIGH (2 distinct issues)

**H1 — Timezone duality is not honoured at the API boundary (serialization + filter).** Two confirmations:
- **Serialization:** for a `UTC` agent, a `12:00Z` appointment returns `start_time = …T20:00:00+08:00`
  (the host is `Asia/Kuala_Lumpur`, +08). The instant is correct and `start_time_local` is right, but the
  doc/contract say `start_time` is UTC (`+00:00`). Caused by `TIMESTAMPTZ` rendering in the host session tz.
- **Functional drop (CONFIRMED on a UTC+14 branch):** an in-hours appointment booked at local 09:00 (= the
  previous day 19:00 UTC) is **not returned** when querying its *local* day via `GET /appointments?date_from&date_to`
  in UTC bounds (`total=0`); it only appears when the range is **widened −1 day**. The date filter runs on
  UTC `start_time`, so any raw API consumer querying by local date **silently loses appointments** near the
  day boundary. The frontend already widens ±1 day (per `ai_context.md`); SDK/API consumers must too, and the
  docs imply UTC is sufficient. **Masked when the host runs in UTC, but the contract should not depend on host tz.**
  Fix: normalize all `*_time` outputs to UTC before serialization, and/or document the ±1-day widening requirement.

**H2 — Employee capability is not enforced on the appointment write path.**
`POST /appointments` booked employee *Bob* (capable only of "Haircut") for "Coloring" and returned **201**.
The availability engine excludes non-capable staff, but a direct create/reassign doesn't check capability —
unlike working-hours and bookability, which the docs say are enforced on the write path "so no bypass is
possible via a direct POST." A direct API caller can book an unqualified employee.

### 🟡 MEDIUM (6)

- **M1 Invalid IANA timezone accepted on agent create** (`"Mars/Phobos"` → 201), then **silently treated as
  UTC** downstream — quietly breaks the timezone-duality invariant for that branch. Validate against `pytz`.
- **M2 Agent `phone_number` format not validated** (`"notaphone"` → 201). Bad numbers break Twilio routing.
- **M3 `POST /employees` accepts duplicate `agent_ids`** (`[A1,A1]` → 201). Harmlessly **deduped**
  server-side (no corruption), but the doc says the list must be unique → should be 422.
- **M4 Profile-picture upload accepts non-images** (`.txt` / `text/plain` stored as `…/98.txt`).
  No content-type/extension validation; should reject non-image uploads.
- **M5 Package credit cannot be un-redeemed.** Redeeming the only line of a sale zeroes the balance, which
  **auto-marks the sale `paid`**; the documented reverse `DELETE /sales/{id}/redeem/{line}` then returns
  **409 `SALE_NOT_EDITABLE`**. The credit is decremented (3→2) and **cannot be restored via the API**,
  contradicting the spec's "un-redeem restores the credit."
- **M6 Double-payment race (CONFIRMED).** 6 concurrent full payments on one sale (total **44**) all returned
  201 and were all recorded → **sum(payments) = 264** on a 44 sale, status `paid`. `POST /sales/{id}/payments`
  has no balance/row lock (read-then-write TOCTOU), so retries / double-clicks / multiple cashiers can
  over-record payments and corrupt the ledger. Fix: lock the sale row (`SELECT … FOR UPDATE`) or reject
  amounts exceeding the outstanding balance.

### 🔵 LOW (8) — minor / cosmetic / doc

- L1–L3 `GET`/double-`DELETE` of a non-existent or deleted agent returns **403 `BRANCH_FORBIDDEN`** instead
  of 404 (existence hidden behind the access check — arguably intentional anti-enumeration, but inconsistent).
- L4 Capability with no target returns 409 `EMPLOYEE_NOT_BOOKABLE` rather than a 422 "exactly-one-target"
  validation error (bookability is checked before the target-count rule).
- L5–L7 Out-of-range discount/commission values return generic **422 `VALIDATION_ERROR`** (Pydantic layer)
  rather than the documented domain codes `INVALID_DISCOUNT_VALUE` / `_WINDOW` / `INVALID_COMMISSION_VALUE`.
  Rejected correctly; only the code/status differ from the docs.
- L8 **Doc mismatch:** `api_reference.md` lists `POST /notifications/send/reminder`, but the real route is
  `POST /notifications/send/reminder/{appointment_id}`.

### ℹ️ INFO

- Login is **case-sensitive on email** (upper-cased email → 401). Most systems treat email case-insensitively.
- New branches are created **inactive** by design (must be `PATCH …/activate`d before public pages work) —
  expected; noted so it isn't mistaken for the public 500.

## Skipped per instruction (external side effects) — untested happy-paths, NOT a clean pass
`POST /chat`, `POST /chat/public` (LLM tokens) · `POST /twilio/sms` (AI loop + real SMS) ·
`POST /voice/vapi/config|webhook|status` (Vapi) · `POST /employees/{id}/invite` success (Resend email) ·
`POST /agents/{id}/custom-domain[/verify]` + `DELETE` (Vercel API) ·
`notifications/send/*` + `scheduler/run` (real SMS). Their auth/validation/pre-call guards were tested.

---
_Reproduce: `cd backend && source venv/bin/activate && python _stress/run.py` (raw findings in `_stress/REPORT.md` / `findings.jsonl`)._
