"""CloudCap entrypoint.

    python -m agents.run --mode mock --project demo-proj --out eval/last_findings.json

Runs the fleet over a project and writes findings JSON for the eval harness.
`mock` runs anywhere with stdlib; `live` uses the Google GEAP adapters.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from agents.context import build_context
from agents.fleet_runner import run_fleet
from agents.ports.interfaces import Finding

MEMORY_PATH = "eval/memory_state.json"


def finding_to_dict(f: Finding) -> dict:
    return {
        "id": f.id,
        "fingerprint": f.fingerprint,
        "category": f.category,
        "severity": f.severity.value,
        "resource": f.resource,
        "title": f.title,
        "detail": f.detail,
        "est_monthly_savings_usd": f.est_monthly_savings_usd,
        "recommended_action": f.recommended_action,
        "metadata": f.metadata,
    }


AUDIT_PATH = "eval/audit_log.jsonl"


async def _main(args: argparse.Namespace) -> int:
    if args.reset_memory and os.path.exists(MEMORY_PATH):
        os.remove(MEMORY_PATH)
        print(f"(reset memory: {MEMORY_PATH})")
    if args.reset_audit and os.path.exists(AUDIT_PATH):
        os.remove(AUDIT_PATH)
        print(f"(reset audit trail: {AUDIT_PATH})")

    ctx = build_context(args.mode, args.project, persist_memory=args.persist_memory,
                        memory_path=MEMORY_PATH, audit_path=AUDIT_PATH)

    # Apply active suppressions (accepted / compliance exceptions).
    from datetime import date, datetime
    from agents.history import FindingHistory
    from agents.suppressions import SuppressionStore
    store = SuppressionStore()
    ctx._suppressed_fingerprints = store.active_fingerprints(date.today())

    findings, meta = await run_fleet(ctx, args.project)

    # Lifecycle: keep ALL findings in history, detect resolutions, no duplicates.
    scan_ts = datetime.now().isoformat(timespec="seconds")
    hist = FindingHistory()
    delta = hist.reconcile([finding_to_dict(f) for f in findings],
                           meta.get("suppressed_by_policy", []), scan_ts)
    # A finding that auto-resolved retires its (now-moot) suppression; history keeps it.
    for r in delta.resolved:
        store.retire(r["fingerprint"])

    total_savings = sum(f.est_monthly_savings_usd for f in findings)
    print(f"\nCloudCap [{args.mode}] scanned {args.project!r} — {len(findings)} open findings"
          f"  (new {len(delta.new)} · recurring {len(delta.recurring)} · reopened {len(delta.reopened)})")
    if meta.get("suppressed_by_policy"):
        print(f"Accepted (compliance exceptions): {len(meta['suppressed_by_policy'])} "
              f"— {', '.join(s['fingerprint'] for s in meta['suppressed_by_policy'])}")
    if delta.resolved:
        print("Auto-resolved since last scan (no longer detected → closed):")
        for r in delta.resolved:
            print(f"  ✓ {r['fingerprint']}  {r['resource']}  — closed (${r['saved']:,.0f}/mo recovered)")
    print(f"Estimated monthly savings identified: ${total_savings:,.0f}")
    if meta.get("executive_summary"):
        print(f"\nOrchestrator [{meta.get('reasoner', 'reasoner')}] summary:\n  {meta['executive_summary']}")
    print("\nFindings (ranked by the reasoner):")
    # Order by the LLM-assigned priority rank when present; savings as tiebreak.
    ranked = sorted(findings, key=lambda x: (x.metadata.get("priority_rank", 10**6),
                                             -x.est_monthly_savings_usd))
    for f in ranked:
        src = f.metadata.get("management_source", "?")
        tag = "  ⚠ UNMANAGED (ClickOps)" if src == "unmanaged" else ""
        flag = " [NEW]" if f.fingerprint in delta.new else (
            " [REOPENED]" if f.fingerprint in delta.reopened else "")
        rank = f.metadata.get("priority_rank")
        rankstr = f"#{rank:<2}" if rank is not None else "  ·"
        print(f"  {rankstr} {f.fingerprint}  [{f.severity.value:8}] {f.category:8} {f.resource:24} — {f.title}{tag}{flag}")
        if src == "unmanaged":
            print(f"             created_by={f.metadata.get('created_by')} "
                  f"→ real actor: {f.metadata.get('triggering_entity')}")

    with open(args.out, "w") as fh:
        json.dump([finding_to_dict(f) for f in findings], fh, indent=2)
    print(f"\nWrote {args.out}")

    # Governance audit trail — durable + tamper-evident (hash chain).
    from agents.audit import verify_audit_log
    ok, msg = verify_audit_log(AUDIT_PATH)
    print(f"Audit trail → {AUDIT_PATH}  ({'✓' if ok else '✗'} {msg}; verify: python -m agents.audit)")

    if args.remediate:
        from agents.remediation.pr_channel import remediate
        results = await remediate(ctx, [finding_to_dict(f) for f in findings], args.project)
        opened = [r for r in results if r.get("status") == "pr_opened"]
        print(f"\nRemediation — {len(opened)} PR(s) opened "
              f"(no cloud writes; humans review + merge):")
        for r in results:
            if r.get("status") == "pr_opened":
                print(f"  ✓ [{r['kind']:11}] {r['branch']}  →  {r['path']}")
            else:
                print(f"  · [{r['status']:11}] {r.get('resource','')}  ({r.get('reason','')})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run the CloudCap governance fleet.")
    p.add_argument("--mode", choices=["mock", "live"], default="mock")
    p.add_argument("--project", default="demo-proj")
    p.add_argument("--out", default="eval/last_findings.json")
    p.add_argument("--remediate", action="store_true", help="open GitOps PRs for findings")
    p.add_argument("--persist-memory", action="store_true",
                   help="persist Memory Bank across runs (demonstrates cross-scan continuity)")
    p.add_argument("--reset-memory", action="store_true", help="clear persisted memory before running")
    p.add_argument("--reset-audit", action="store_true", help="clear the audit trail before running")
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
