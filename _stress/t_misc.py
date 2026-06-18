"""App-level + webhook health, and the consolidated list of endpoints SKIPPED per instruction."""
from core import call, body, ok, reject, finding

A = "misc"

def run(F):
    A1 = F["A1"]
    # app-level health (no /api/v1 prefix)
    ok(call("GET", "/health", root=True), A, "GET /health")
    # twilio health is safe (no SMS)
    th = call("GET", "/twilio/health")
    if th.status_code >= 500:
        finding("CRITICAL", A, "5xx on /twilio/health", body(th), "/twilio/health")
    elif th.status_code != 200:
        finding("LOW", A, f"/twilio/health -> {th.status_code}", body(th), "/twilio/health")
    # /b/{slug} app-level redirect (don't follow)
    agent = body(call("GET", f"/agents/{A1}", token=F["A_token"])) or {}
    slug = agent.get("booking_page_slug")
    if slug:
        import requests as _rq
        r = _rq.get(f"http://localhost:8000/b/{slug}", allow_redirects=False, timeout=20)
        if r.status_code not in (301, 302, 307, 308):
            finding("LOW", A, f"/b/{{slug}} not a redirect", f"status={r.status_code}", "/b/{slug}")

    # network-ip (dev-only, hidden) — just ensure no 5xx
    ni = call("GET", "/public/network-ip")
    if ni.status_code >= 500:
        finding("MEDIUM", A, "5xx on /public/network-ip", body(ni), "network-ip")

    finding("INFO", A, "ENDPOINTS SKIPPED PER INSTRUCTION (external side-effects)",
            "Not exercised on their success paths: "
            "POST /chat, POST /chat/public (LLM); "
            "POST /twilio/sms (AI loop + SMS); "
            "POST /voice/vapi/config, /voice/vapi/webhook, /voice/vapi/status (Vapi); "
            "POST /employees/{id}/invite success (Resend email); "
            "POST /agents/{id}/custom-domain[/verify] + DELETE (Vercel API); "
            "notifications send/* + scheduler/run (SMS). "
            "All of these had their auth/validation/pre-call guards tested where reachable without the external call.",
            "coverage")
