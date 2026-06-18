"""Chat: only the DB-backed conversation reads/delete (LLM send paths skipped per instruction)."""
from core import call, body, ok, reject, finding

A = "chat"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    A1 = F["A1"]
    ok(call("GET", "/chat/conversations/", token=tA, params={"agent_id": A1}), A, "list conversations")
    reject(call("GET", "/chat/conversations/", token=tA), A, "list conversations missing agent_id", {422})
    reject(call("GET", "/chat/conversations/", token=tB, params={"agent_id": A1}), A,
           "B lists A's conversations", {403, 404}, sev2xx="CRITICAL")
    reject(call("GET", "/chat/conversations/99999999", token=tA), A, "get unknown conversation", {404, 403})
    reject(call("DELETE", "/chat/conversations/99999999", token=tA), A, "delete unknown conversation", {404, 403})
    finding("INFO", A, "POST /chat and POST /chat/public SKIPPED (live LLM calls) per instruction",
            "send paths not exercised", "chat")
