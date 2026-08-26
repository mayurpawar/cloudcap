"""Remediation agent — closes the Trust Gap via GitOps, not cloud writes.

CRITICAL DESIGN RULE: this agent has NO cloud write permissions. It can only:
  1. read a Finding + its deterministic proof,
  2. edit the customer's IaC (Terraform/K8s) in a branch,
  3. open a Pull Request for a human to review and merge.

The human reviews a code diff backed by hard evidence — not a black-box AI action.
Merging the PR + `terraform apply` is what actually changes infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent

from agents.ports.interfaces import Finding, FleetContext
from agents.remediation.quarantine import QUARANTINE_PRIMITIVES, quarantine_for  # noqa: F401

MODEL = "gemini-3.5-pro"
AGENT_ID = "remediation"

INSTRUCTION = """
You convert a confirmed Finding into a reviewable Pull Request against the IaC repo.
You may NOT execute cloud changes. For each finding:
1. Locate the resource in the Terraform/manifests.
2. Produce a minimal, correct diff (e.g., machine_type e2-standard-4 -> e2-small;
   remove allUsers IAM binding; replace roles/owner with least-privilege role).
3. Write a PR description that embeds the DETERMINISTIC PROOF (utilization graph link,
   Recommender evidence) so the reviewer trusts the change without trusting you.
4. Never widen scope beyond the finding. If unsure, open a draft PR and say why.
5. UNMANAGED (ClickOps) resources: do NOT reverse-engineer their module/Terragrunt
   layout. Generate a FLAT `cloudcap_clickops_recovery.tf` at the repo ROOT, run the
   state import, and open the PR. Human engineers refactor into modules later.
6. Decommission is QUARANTINE-FIRST and resource-specific (see QUARANTINE_PRIMITIVES).
   Never propose a raw delete; every quarantine step must be reversible where possible.
"""

# QUARANTINE_PRIMITIVES / quarantine_for now live in agents/remediation/quarantine.py
# (dependency-free so the PR channel can share them). Imported at the top.


@dataclass
class ProofArtifact:
    """Deterministic evidence attached to a remediation, not LLM assertion."""
    kind: str                 # "utilization_timeseries" | "recommender_evidence"
    summary: str              # e.g. "peak CPU 12% over available window"
    chart_url: str | None     # rendered from Cloud Monitoring
    raw_evidence: dict[str, Any]


def build_remediation_agent(ctx: FleetContext) -> LlmAgent:

    async def gather_proof(finding_id: str, resource: str) -> dict[str, Any]:
        """Deterministically pull metrics/Recommender evidence for the finding."""
        with ctx.observability.span("remediation.gather_proof", {"finding": finding_id}):
            # Read-only, via Gateway. TODO(D9): Cloud Monitoring time-series query +
            # render chart; fall back to Recommender evidence when history is short.
            evidence = await ctx.gateway.call_tool(
                agent_id=AGENT_ID,
                tool="monitoring.timeseries",
                args={"resource": resource, "metric": "utilization", "window_days": 90},
            )
            ctx.observability.audit(AGENT_ID, "proof_generated", {"finding": finding_id})
            return evidence or {}

    async def open_pull_request(
        repo: str, branch: str, diff: str, title: str, body_with_proof: str
    ) -> dict[str, Any]:
        """Open a PR against the IaC repo. This is the ONLY 'action' this agent takes."""
        with ctx.observability.span("remediation.open_pr", {"repo": repo}):
            # Semantic policy guardrail BEFORE a human sees it.
            check = await ctx.guardrail.inspect_input(diff, context="iac-policy-check")
            if not check.allowed:
                ctx.observability.audit(AGENT_ID, "policy_block", {"reason": check.reason})
                return {"status": "blocked", "reason": check.reason}
            # TODO(D9): GitHub/GitLab API — create branch, commit diff, open PR.
            ctx.observability.audit(AGENT_ID, "pr_opened", {"repo": repo, "title": title})
            return {"status": "pr_opened", "repo": repo, "branch": branch}

    async def codify_clickops_resource(
        resource: str, resource_type: str, hcl: str
    ) -> dict[str, Any]:
        """Bring an UNMANAGED (ClickOps) resource under IaC — FLAT, at repo root.

        Deliberately does NOT touch the customer's module/Terragrunt structure. It
        writes a single `cloudcap_clickops_recovery.tf`, runs `terraform import` to
        bind live state, and opens a PR. Humans refactor into modules afterward.
        """
        with ctx.observability.span("remediation.codify", {"resource": resource}):
            recovery_file = "cloudcap_clickops_recovery.tf"
            import_cmd = f"terraform import {_tf_address(resource_type, resource)} {resource}"
            ctx.observability.audit(
                AGENT_ID, "codify_clickops",
                {"resource": resource, "file": recovery_file},
            )
            return {
                "file": recovery_file,      # flat, root-level
                "hcl": hcl,                 # generated resource block
                "import_cmd": import_cmd,   # binds existing infra to the new block
                "note": "flat recovery file; refactor into modules post-merge",
            }

    async def plan_quarantine(resource: str, resource_type: str) -> dict[str, Any]:
        """Produce the reversible-first quarantine plan for a decommission finding."""
        prim = quarantine_for(resource_type)
        ctx.observability.audit(AGENT_ID, "quarantine_planned", {"resource": resource})
        return {
            "resource": resource,
            "step_1_reversible": prim["reversible"],
            "step_2_soak": "wait N days; abort if any activity observed",
            "step_3_terminal": prim["terminal"],
        }

    return LlmAgent(
        name=AGENT_ID,
        model=MODEL,
        instruction=INSTRUCTION,
        tools=[gather_proof, open_pull_request, codify_clickops_resource, plan_quarantine],
    )


def _tf_address(resource_type: str, resource: str) -> str:
    """Map a GCP resource type to its flat Terraform resource address."""
    mapping = {
        "compute.instance": "google_compute_instance.recovered",
        "compute.disk": "google_compute_disk.recovered",
        "sql.instance": "google_sql_database_instance.recovered",
        "storage.bucket": "google_storage_bucket.recovered",
    }
    return mapping.get(resource_type, "google_resource.recovered")


def render_pr_body(finding: Finding, proof: ProofArtifact) -> str:
    """Compose a PR description that leads with the proof, not the AI's opinion."""
    return (
        f"### CloudCap remediation: {finding.title}\n\n"
        f"**Resource:** `{finding.resource}`  \n"
        f"**Severity:** {finding.severity.value}  \n"
        f"**Est. monthly savings:** ${finding.est_monthly_savings_usd:.0f}\n\n"
        f"#### Proof ({proof.kind})\n{proof.summary}\n"
        f"{f'![utilization]({proof.chart_url})' if proof.chart_url else ''}\n\n"
        f"#### Proposed change\n{finding.recommended_action}\n\n"
        f"_Review the diff below. Merge + `terraform apply` applies the change. "
        f"This agent has no cloud write access._"
    )
