"""Deterministic fleet runner — the pipeline backbone.

Findings are DETERMINISTIC (from tools/recommenders); in live mode the LLM
orchestrator narrates and prioritizes on top. This function is what proves the
end-to-end flow: gateway -> guardrail -> classify -> memory dedup -> findings.
"""

from __future__ import annotations

from typing import Any

from agents.compliance import controls_for as compliance_controls_for
from agents.normalize import recommendation_to_finding, run_service_to_finding
from agents.ports.interfaces import Finding, FleetContext, Severity, compute_fingerprint


async def run_fleet(ctx: FleetContext, project: str) -> tuple[list[Finding], dict[str, Any]]:
    """Returns (fresh_findings, meta). meta carries classifications + continuity stats."""
    findings: list[Finding] = []

    with ctx.observability.span("fleet.run", {"project": project}):
        # 1. COST — Recommender / Active Assist (read-only, via Gateway)
        cost = await ctx.gateway.call_tool("cost_scanner", "recommender.list", {"project": project})
        findings += [recommendation_to_finding(r) for r in cost.get("items", [])]

        # 1b. COST — usage-based analyzers BEYOND Recommender. Cloud Run over-provisioned
        #     always-on min-instances (Recommender's blind spot); flagged from real usage.
        run_over = await ctx.gateway.call_tool("cost_scanner", "run.utilization", {"project": project})
        findings += [run_service_to_finding(s) for s in run_over.get("items", [])]

        # 2. SECURITY — misconfiguration posture
        sec = await ctx.gateway.call_tool("security_scanner", "asset.security_findings", {"project": project})
        for s in sec.get("items", []):
            findings.append(Finding(
                id=f"sec/{s['resource']}", category="security", severity=Severity(s["severity"]),
                resource=s["resource"], title=s["title"], detail=s["detail"],
                recommended_action=s["recommendedAction"],
            ))

        # 3. IAM — over-privilege
        iam = await ctx.gateway.call_tool("iam_scanner", "iam.findings", {"project": project})
        for i in iam.get("items", []):
            findings.append(Finding(
                id=f"iam/{i['resource']}", category="iam", severity=Severity(i["severity"]),
                resource=i["resource"], title=i["title"], detail=i["detail"],
                recommended_action=i["recommendedAction"],
            ))

        # 4. GUARDRAIL — screen untrusted object metadata (tool-poisoning defense)
        poison = await ctx.gateway.call_tool("security_scanner", "storage.object_metadata", {"project": project})
        guard = await ctx.guardrail.inspect_input(
            f"{poison.get('name','')} {poison.get('content','')}", context="resource-metadata")
        if not guard.allowed:
            ctx.observability.audit("security_scanner", "guardrail_block", {"reason": guard.reason})
            findings.append(Finding(
                id="sec/tool-poisoning", category="security", severity=Severity.CRITICAL,
                resource=poison.get("name", "unknown-object"),
                title="Tool-poisoning attempt blocked by Model Armor",
                detail=guard.reason,
                recommended_action="Agent refused injected instruction; object flagged for review",
            ))

        # 4.5 STABLE FINGERPRINT — assign a scan-stable id to every finding
        for f in findings:
            f.fingerprint = compute_fingerprint(f.category, f.resource, f.metadata.get("recommender", ""))

        # 4.6 SUPPRESSIONS — drop user-accepted / compliance-exception findings
        active = getattr(ctx, "_suppressed_fingerprints", frozenset())
        suppressed_by_policy = [f for f in findings if f.fingerprint in active]
        if suppressed_by_policy:
            findings = [f for f in findings if f.fingerprint not in active]
            ctx.observability.audit("orchestrator", "suppressions_applied",
                                    {"count": len(suppressed_by_policy)})

        # 5. CLASSIFY — management source + attribution (incl. SA assumption-chain)
        #    + IaC OWNERSHIP (which Terraform state/repo manages it: one/none/conflict)
        classifications: dict[str, Any] = {}
        for f in findings:
            attr = await ctx.classifier.classify(f.resource)
            f.metadata["management_source"] = attr.source.value
            f.metadata["created_by"] = attr.created_by
            f.metadata["triggering_entity"] = attr.triggering_entity
            f.metadata["attribution_confidence"] = attr.attribution_confidence
            classifications[f.resource] = attr

            from agents.fixtures import RESOURCE_PROJECT
            f.metadata["project"] = RESOURCE_PROJECT.get(f.resource, project)

            own = await ctx.ownership.resolve(f.resource)
            f.metadata["ownership_status"] = own.status
            f.metadata["owner_repo"] = own.repo
            f.metadata["tf_address"] = own.tf_address
            f.metadata["owner_candidates"] = own.candidates

            # Compliance: map to control IDs across CIS / SOC 2 / ISO 27001 / PCI DSS.
            ctrls = compliance_controls_for({
                "resource": f.resource, "title": f.title, "detail": f.detail, "metadata": f.metadata})
            if ctrls:
                f.metadata["controls"] = ctrls
                f.metadata["compliance_rule"] = ctrls["rule"]

        # 5.5 REASON — the LLM orchestrator (Gemini in live, deterministic in mock)
        #     ranks + summarizes on top of the deterministic findings. It never
        #     invents/mutates findings; a reasoner error degrades to no-ranking.
        executive_summary = ""
        reasoner_name = ""
        if getattr(ctx, "reasoner", None) is not None:
            reasoner_name = type(ctx.reasoner).__name__
            fd_view = [{
                "fingerprint": f.fingerprint, "category": f.category, "severity": f.severity.value,
                "resource": f.resource, "title": f.title,
                "est_monthly_savings_usd": f.est_monthly_savings_usd, "metadata": f.metadata,
            } for f in findings]
            with ctx.observability.span("orchestrator.reason", {"reasoner": reasoner_name}):
                try:
                    rankings = await ctx.reasoner.prioritize(fd_view, context=f"project={project}")
                    by_fp = {r.fingerprint: r for r in rankings}
                    for f in findings:
                        r = by_fp.get(f.fingerprint)
                        if r:
                            f.metadata["priority_rank"] = r.rank
                            f.metadata["priority_rationale"] = r.rationale
                    executive_summary = await ctx.reasoner.summarize(fd_view, context=f"project={project}")
                except Exception as exc:  # reasoning is additive — never break the scan
                    ctx.observability.audit("orchestrator", "reasoner_error", {"error": str(exc)})

        # 6. OBSERVE — a scan always reports ALL currently-open issues. Continuity
        #    (new vs recurring vs resolved) is tracked by the history lifecycle, NOT by
        #    hiding recurring findings — an open issue stays open until it's resolved.
        ctx.observability.audit(
            "orchestrator", "scan_complete",
            {"open": len(findings), "accepted": len(suppressed_by_policy)},
        )

    meta = {
        "classifications": classifications,
        "total_scanned": len(findings) + len(suppressed_by_policy),
        "executive_summary": executive_summary,
        "reasoner": reasoner_name,
        "suppressed_by_policy": [
            {"fingerprint": f.fingerprint, "resource": f.resource, "title": f.title,
             "category": f.category}
            for f in suppressed_by_policy
        ],
    }
    return findings, meta
