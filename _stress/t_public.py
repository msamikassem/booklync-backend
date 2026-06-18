"""Public booking-page endpoints (no auth). Requires the agent to be active (fixtures activate)."""
from core import call, body, ok, reject, finding

A = "public"

def run(F):
    tA, A1 = F["A_token"], F["A1"]
    agent = body(call("GET", f"/agents/{A1}", token=tA)) or {}
    slug = agent.get("booking_page_slug")
    if slug:
        pa, good = ok(call("GET", f"/public/business/{slug}"), A, "public business by slug")
        if good:
            for k in ("agent_name", "booking_page_slug", "timezone"):
                if k not in (pa or {}):
                    finding("LOW", A, f"public agent missing '{k}'", list(pa or {}), "business/{slug}")
        ok(call("GET", f"/public/business/{slug}/services"), A, "public services by slug")
        # public availability now that the agent is active
        ok(call("GET", "/availability/public/date",
           params={"agent_id": A1, "service_id": F["S1"], "date": F["future_date"]}), A,
           "public availability (active agent)")
    else:
        finding("MEDIUM", A, "agent has no booking_page_slug", "", "slug")

    reject(call("GET", "/public/business/no-such-slug-xyz"), A, "unknown slug", {404})
    # services for unknown slug -> spec says [] (empty), not 404
    r = call("GET", "/public/business/no-such-slug-xyz/services")
    if r.status_code >= 500:
        finding("CRITICAL", A, "5xx on public services unknown slug", body(r), "services unknown")
    reject(call("GET", "/public/business-by-domain/nonexistent.example.com"), A,
           "unknown custom domain", {404})
