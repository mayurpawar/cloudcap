"""The authoritative scan — one governance scan, persisted and audited.

This is the single primitive behind BOTH the onboarding "first scan" and the scheduled
daily scan. Unlike a dashboard page-load (which recomputes findings for display only), a
real scan is a governance EVENT: it establishes/advances the finding lifecycle
(new/recurring/resolved) in history and writes to the tamper-evident audit trail.
"""

from __future__ import annotations

from datetime import date, datetime

from agents.context import build_context
from agents.fleet_runner import run_fleet
from agents.history import FindingHistory
from agents.run import finding_to_dict
from agents.suppressions import SuppressionStore


async def run_scan(project: str, mode: str = "mock", durable_audit: bool = True) -> dict:
    """Run one full governance scan: detect → rank → reconcile lifecycle → deliver ONCE
    (per policy; PRs only where a repo is set and no freeze) → PERSIST the result for the
    dashboard to display → audit. This is the single source of truth for what the board
    shows (so the board never re-runs the fleet, and delivery never duplicates)."""
    from agents.governance import GovernanceConfig
    from agents.remediation.pr_channel import remediate
    from agents.store import save_state

    ctx = build_context(mode, project, durable_audit=durable_audit)
    store = SuppressionStore()
    ctx._suppressed_fingerprints = store.active_fingerprints(date.today())

    findings, meta = await run_fleet(ctx, project)

    # honour per-project governance (which domains to keep)
    gov = GovernanceConfig()
    findings = [f for f in findings
                if gov.category_enabled(f.metadata.get("project", project), f.category)]
    fd = [finding_to_dict(f) for f in findings]

    # IaC code scan: flag hardcoded secrets in the Terraform of the repo bound to this
    # project (SOC 2 CC6.1). Read-only fetch of the repo's *.tf; a no-op if no repo is
    # configured or the fetch fails. These flow through lifecycle + PR remediation too.
    try:
        from agents.iac_secret_scan import scan_repo_secrets
        fd += scan_repo_secrets(project)
    except Exception:
        pass

    scan_ts = datetime.now().isoformat(timespec="seconds")
    hist = FindingHistory()
    delta = hist.reconcile(fd, meta.get("suppressed_by_policy", []), scan_ts)
    for r in delta.resolved:
        store.retire(r["fingerprint"])

    # Deliver ONCE per scan (report/PR/Issue per policy). Read-only cloud; PRs require a
    # repo + no freeze, so a project with no repo (or frozen) is report-only.
    prs = await remediate(ctx, fd, project)

    # Persist the scan result — the dashboard reads THIS, never a live recompute.
    save_state("eval/last_scan.json", {
        "findings": fd, "prs": prs, "scan_ts": scan_ts, "project": project, "mode": mode,
        "summary": meta.get("executive_summary", ""), "reasoner": meta.get("reasoner", ""),
    })

    # Append to scan history (timestamped, with the scan profile — projects + frameworks).
    from agents.sources import SourcesConfig
    from agents.store import load_state
    savings = sum(x.get("est_monthly_savings_usd", 0) for x in fd)
    projects = sorted(SourcesConfig().selected())
    frameworks = sorted({fw for p in projects for fw in gov.enabled_frameworks(p)}) if projects else []
    entry = {
        "ts": scan_ts, "mode": mode, "target": project,
        "findings": len(fd),
        "critical": sum(1 for x in fd if str(x.get("severity")) == "critical"),
        "high": sum(1 for x in fd if str(x.get("severity")) == "high"),
        "savings": savings, "new": len(delta.new), "resolved": len(delta.resolved),
        "projects": projects, "frameworks": frameworks, "reasoner": meta.get("reasoner", ""),
    }
    hlist = load_state("eval/scan_history.json", [])
    hlist = ([entry] + hlist)[:50] if isinstance(hlist, list) else [entry]
    save_state("eval/scan_history.json", hlist)

    ctx.observability.audit("orchestrator", "scan_run",
                            {"project": project, "mode": mode, "open": len(fd),
                             "new": len(delta.new), "resolved": len(delta.resolved)})

    return {"findings": len(fd), "new": len(delta.new), "resolved": len(delta.resolved),
            "savings": savings, "scan_ts": scan_ts}
