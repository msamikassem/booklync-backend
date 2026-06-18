"""Notifications: guards only (sending real SMS is skipped per instruction)."""
from core import call, body, ok, reject, finding

A = "notifications"

def run(F):
    tA, tB, tR = F["A_token"], F["B_token"], F["recep_token"]
    # read-only template/rule inspection (need message_customers; owner has)
    ok(call("GET", "/notifications/templates", token=tA), A, "GET templates")
    ok(call("GET", "/notifications/rules", token=tA), A, "GET rules")

    # send/* on an unknown appointment returns 404 BEFORE any SMS is sent — safe to test
    reject(call("POST", "/notifications/send/confirmation/99999999", token=tA), A,
           "confirmation unknown appt (pre-SMS guard)", {404})
    reject(call("POST", "/notifications/send/cancellation/99999999", token=tA), A,
           "cancellation unknown appt (pre-SMS guard)", {404})
    reject(call("POST", "/notifications/send/custom", token=tA,
           json_body={"appointment_id": 99999999, "message": "hi"}), A,
           "custom unknown appt (pre-SMS guard)", {404})
    reject(call("POST", "/notifications/send/reminder/99999999", token=tA,
           json_body={"appointment_id": 99999999, "minutes_before": 0}), A,
           "reminder unknown appt (pre-SMS guard)", {404})

    # scheduler/run sweeps ALL due reminders across the DB and would send real SMS — NOT run.
    # Only the permission guard is checked (returns before any send for an unauthorized caller).
    if "manage_automations" not in F["recep_perms"]:
        reject(call("POST", "/notifications/scheduler/run", token=tR), A,
               "non-owner scheduler/run", {403})
    finding("INFO", A, "send/* happy paths + scheduler/run SKIPPED (send real SMS) per instruction",
            "only pre-send guards exercised", "notifications")
