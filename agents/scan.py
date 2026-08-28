"""The authoritative scan — one governance scan, persisted and audited.

This is the single primitive behind BOTH the onboarding "first scan" and the scheduled
daily scan. Unlike a dashboard page-load (which recomputes findings for display only), a
real scan is a governance EVENT: it establishes/advances the finding lifecycle
(new/recurring/resolved) in history and writes to the tamper-evident audit trail.

`project` may be a single id or a comma-separated list — one "Run scan" then covers the
whole in-scope fleet (e.g. chaos-env for planted issues + kitearc-prod for cost savings),
aggregating findings into one board.
"""

from __future__ import annotations

from datetime import date, datetime

from agents.context import build_context
from agents.fleet_runner import run_fleet
from agents.history import FindingHistory
from agents.run import finding_to_dict
from agents.suppressions import SuppressionStore


async def run_scan(project: str, mode: str = "mock", durable_audit: bool = True) -> dict:
    """Run one full governance scan across one or more projects: detect → rank → reconcile
    lifecycle → deliver ONCE (per project policy) → PERSIST the combined result → audit."""
    import os as _os

    from agents.governance import GovernanceConfig
    from agents.remediation.pr_channel import remediate
    from agents.sources import SourcesConfig
    from agents.store import load_state, save_state

    targets = [p.strip() for p in str(project).split(",") if p.strip()] or [str(project)]
    gov = GovernanceConfig()
    store = SuppressionStore()
    suppressed = store.active_fingerprints(date.today())

    per_project: list[tuple[str, object, list[dict]]] = []   # (project, ctx, fd)
    all_fd: list[dict] = []
    suppressed_by_policy: list = []
    reasoner_name = ""
    primary_ctx = None

    for tproj in targets:
        ctx = build_context(mode, tproj, durable_audit=durable_audit)
        ctx._suppressed_fingerprints = suppressed
        if primary_ctx is None:
            primary_ctx = ctx
            # Agent Registry pillar: register the approved fleet once (persist + verify SAs).
            try:
                from agents.adapters.google_geap import fleet_roster
                for _spec in fleet_roster(_os.environ.get("GOOGLE_CLOUD_PROJECT") or tproj):
                    await ctx.registry.publish(_spec)
            except Exception:
                pass

        findings, meta = await run_fleet(ctx, tproj)
        findings = [f for f in findings
                    if gov.category_enabled(f.metadata.get("project", tproj), f.category)]
        fd = [finding_to_dict(f) for f in findings]

        # IaC code scan: hardcoded secrets in the repo bound to this project (SOC 2 CC6.1).
        try:
            from agents.iac_secret_scan import scan_repo_secrets
            fd += scan_repo_secrets(tproj)
        except Exception:
            pass

        reasoner_name = meta.get("reasoner", "") or reasoner_name
        suppressed_by_policy += meta.get("suppressed_by_policy", []) or []
        per_project.append((tproj, ctx, fd))
        all_fd += fd

    scan_ts = datetime.now().isoformat(timespec="seconds")
    hist = FindingHistory()
    delta = hist.reconcile(all_fd, suppressed_by_policy, scan_ts)
    for r in delta.resolved:
        store.retire(r["fingerprint"])

    # Deliver per project — the action policy (PR/Issue/Slack/Email) is per-project.
    prs: list = []
    for tproj, ctx, fd in per_project:
        prs += await remediate(ctx, fd, tproj)

    # One combined executive summary across every scanned project.
    summary = ""
    try:
        if primary_ctx is not None and getattr(primary_ctx, "reasoner", None) is not None:
            summary = await primary_ctx.reasoner.summarize(all_fd, context=f"projects={','.join(targets)}")
    except Exception:
        pass

    label = ", ".join(targets)
    save_state("eval/last_scan.json", {
        "findings": all_fd, "prs": prs, "scan_ts": scan_ts, "project": label, "mode": mode,
        "summary": summary, "reasoner": reasoner_name,
    })

    savings = sum(x.get("est_monthly_savings_usd", 0) for x in all_fd)
    projects = sorted(SourcesConfig().selected())
    frameworks = sorted({fw for p in projects for fw in gov.enabled_frameworks(p)}) if projects else []
    entry = {
        "ts": scan_ts, "mode": mode, "target": label, "findings": len(all_fd),
        "critical": sum(1 for x in all_fd if str(x.get("severity")) == "critical"),
        "high": sum(1 for x in all_fd if str(x.get("severity")) == "high"),
        "savings": savings, "new": len(delta.new), "resolved": len(delta.resolved),
        "projects": projects, "frameworks": frameworks, "reasoner": reasoner_name,
    }
    hlist = load_state("eval/scan_history.json", [])
    hlist = ([entry] + hlist)[:50] if isinstance(hlist, list) else [entry]
    save_state("eval/scan_history.json", hlist)

    if primary_ctx is not None:
        primary_ctx.observability.audit("orchestrator", "scan_run",
                                        {"projects": targets, "mode": mode, "open": len(all_fd),
                                         "new": len(delta.new), "resolved": len(delta.resolved)})

    return {"findings": len(all_fd), "new": len(delta.new), "resolved": len(delta.resolved),
            "savings": savings, "scan_ts": scan_ts}
