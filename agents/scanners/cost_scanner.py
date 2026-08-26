"""Cost scanner sub-agent — the first vertical slice, end to end.

Reads REAL data via the Gateway (Cloud Billing + Recommender/Active Assist),
turns Google's own recommendations into ranked Findings. All cloud access is
read-only and routed through the Gateway with the scanner's scoped identity.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent

from agents.normalize import recommendation_to_finding as to_finding
from agents.ports.interfaces import Finding, FleetContext, Severity

MODEL = "gemini-3.5-pro"
AGENT_ID = "cost_scanner"

INSTRUCTION = """
You are the Cost Scanner. Identify wasted spend in the target GCP project:
idle/oversized VMs, unattached disks, unused reserved IPs, oversized Cloud SQL.
Use ONLY the provided tools (routed via Gateway, read-only). For each issue emit a
Finding with an estimated monthly saving and a concrete rightsizing/cleanup action.
Do not invent resources; ground every finding in tool output.
"""


def build_cost_scanner(ctx: FleetContext) -> LlmAgent:

    async def list_cost_recommendations(project_id: str) -> list[dict[str, Any]]:
        """Fetch Google's Recommender cost insights (idle VM, rightsizing, idle disk...)."""
        with ctx.observability.span("cost.list_recommendations", {"project": project_id}):
            # Read-only call, policy-enforced by the Gateway.
            raw = await ctx.gateway.call_tool(
                agent_id=AGENT_ID,
                tool="recommender.list",  # TODO(D4): wire google-cloud-recommender
                args={
                    "project": project_id,
                    "recommenders": [
                        "google.compute.instance.IdleResourceRecommender",
                        "google.compute.instance.MachineTypeRecommender",
                        "google.compute.disk.IdleResourceRecommender",
                        "google.compute.address.IdleResourceRecommender",
                        "google.cloudsql.instance.IdleRecommender",
                    ],
                },
            )
            ctx.observability.audit(AGENT_ID, "read_recommendations", {"count": len(raw or [])})
            return raw or []

    async def screen_metadata(text: str) -> dict[str, Any]:
        """Screen untrusted resource metadata through Model Armor before reasoning."""
        result = await ctx.guardrail.inspect_input(text, context="resource-metadata")
        if not result.allowed:
            ctx.observability.audit(AGENT_ID, "guardrail_block", {"reason": result.reason})
        return {"allowed": result.allowed, "reason": result.reason}

    return LlmAgent(
        name=AGENT_ID,
        model=MODEL,
        instruction=INSTRUCTION,
        tools=[list_cost_recommendations, screen_metadata],
    )


# Normalization is shared with the deterministic runner + eval harness.
# See agents/normalize.py: `to_finding` is an alias for `recommendation_to_finding`.
