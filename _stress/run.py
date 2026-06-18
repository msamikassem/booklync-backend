"""Orchestrator: build fixtures, run every section, write the report incrementally."""
import importlib, traceback, json, os, datetime
import core, fixtures

SECTIONS = [
    "t_auth", "t_agents", "t_employees", "t_services", "t_hours",
    "t_availability", "t_customers", "t_appointments", "t_staff",
    "t_users", "t_financials", "t_notifications", "t_chat", "t_public",
    "t_isolation", "t_tz_concurrency", "t_booking_race", "t_misc",
]

def write_report():
    by_sev = {}
    for f in core.FINDINGS:
        by_sev.setdefault(f["sev"], []).append(f)
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    lines = []
    lines.append(f"# Booklync API Stress-Test — Findings\n")
    lines.append(f"_Run {core.RUN_TAG} · live server :8000 · local `demo_data` DB · against `docs/api_reference.md`_\n")
    lines.append("## Tally\n")
    for s in order:
        lines.append(f"- **{s}**: {len(by_sev.get(s, []))}")
    lines.append("")
    for s in order:
        items = by_sev.get(s, [])
        if not items:
            continue
        lines.append(f"\n## {s} ({len(items)})\n")
        for i, f in enumerate(items, 1):
            lines.append(f"### {s}-{i}. [{f['area']}] {f['title']}")
            if f.get("ctx"):
                lines.append(f"- ctx: `{f['ctx']}`")
            if f.get("detail"):
                lines.append(f"- detail: {f['detail']}")
            lines.append("")
    with open(os.path.join(core.HERE, "REPORT.md"), "w") as fh:
        fh.write("\n".join(lines))

def main():
    print("=== Building fixtures ===")
    F = fixtures.build()
    with open(os.path.join(core.HERE, "F.json"), "w") as fh:
        json.dump(F, fh)
    for name in SECTIONS:
        if not os.path.exists(os.path.join(core.HERE, name + ".py")):
            print(f"\n=== Section {name} (not implemented, skipping) ===")
            continue
        print(f"\n=== Section {name} ===")
        try:
            mod = importlib.import_module(name)
            importlib.reload(mod)
            mod.run(F)
        except Exception as e:
            core.finding("CRITICAL", name, f"section harness crashed: {e}",
                         traceback.format_exc())
        write_report()  # durable after every section
    write_report()
    print("\n=== DONE ===")
    counts = {}
    for f in core.FINDINGS:
        counts[f["sev"]] = counts.get(f["sev"], 0) + 1
    print("Tally:", counts)

if __name__ == "__main__":
    main()
