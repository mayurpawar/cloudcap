"""In-memory adapters — make the whole fleet runnable with stdlib only.

Same ports as the Google GEAP adapters, so `mock` mode exercises the REAL pipeline
(gateway routing, guardrail screening, classification, memory dedup, observability)
without any cloud access. Swap to google_geap.py for `live` mode — zero logic change.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

from agents import fixtures
from agents.ports.interfaces import (
    AgentSpec,
    Attribution,
    GatewayPort,
    GuardrailPort,
    GuardResult,
    IdentityPort,
    ManagementSource,
    MemoryPort,
    ObservabilityPort,
    Ranking,
    ReasonerPort,
    RegistryPort,
    RemediationChannelPort,
    ResourceClassifierPort,
)

# Markers that indicate a prompt-injection / tool-poisoning attempt.
_INJECTION_MARKERS = ("ignore prior", "ignore your", "system:", "exfiltrate", "email all")

_SEV_WEIGHT = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class MockMemory(MemoryPort):
    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}

    async def recall(self, scope: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._store.get(scope, []))[:limit]

    async def remember(self, scope: str, facts: list[dict[str, Any]]) -> None:
        self._store.setdefault(scope, []).extend(facts)


class FileBackedMemory(MemoryPort):
    """Persists memory to disk so continuity survives across runs.

    Demonstrates the Memory Bank pillar: a second scan RECALLS the first scan's
    findings and suppresses already-known items ("weeks of async context").
    """

    def __init__(self, path: str = "eval/memory_state.json") -> None:
        self.path = path
        self._store: dict[str, list[dict[str, Any]]] = {}
        if os.path.exists(path):
            try:
                self._store = json.load(open(path))
            except (ValueError, OSError):
                self._store = {}

    async def recall(self, scope: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._store.get(scope, []))[:limit]

    async def remember(self, scope: str, facts: list[dict[str, Any]]) -> None:
        self._store.setdefault(scope, []).extend(facts)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(self._store, fh, indent=2)


class MockGuardrail(GuardrailPort):
    async def inspect_input(self, text: str, context: str = "") -> GuardResult:
        low = text.lower()
        if any(m in low for m in _INJECTION_MARKERS):
            return GuardResult(allowed=False, reason="prompt-injection / tool-poisoning detected")
        return GuardResult(allowed=True)

    async def inspect_output(self, text: str) -> GuardResult:
        return GuardResult(allowed=True, redacted_text=text)


class MockGateway(GatewayPort):
    """Returns fixture data per tool name. In live mode this enforces policy + routes.

    Demo hook: CLOUDCAP_RESOLVED_RESOURCES (comma-separated resource names) simulates
    resources that were fixed/terminated between scans — they drop out of results, so
    the history lifecycle marks their findings RESOLVED.
    """

    @staticmethod
    def _resolved() -> set[str]:
        return {r.strip() for r in os.environ.get("CLOUDCAP_RESOLVED_RESOURCES", "").split(",") if r.strip()}

    def _drop(self, items: list[dict], key: str) -> list[dict]:
        gone = self._resolved()
        return [i for i in items if i.get(key) not in gone]

    async def call_tool(self, agent_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "recommender.list":
            return {"items": self._drop(fixtures.COST_RECOMMENDATIONS, "targetResource")}
        if tool == "run.utilization":
            return {"items": self._drop(fixtures.CLOUD_RUN_SERVICES, "resource")}
        if tool == "asset.security_findings":
            return {"items": self._drop(fixtures.SECURITY_FINDINGS, "resource")}
        if tool == "iam.findings":
            return {"items": self._drop(fixtures.IAM_FINDINGS, "resource")}
        if tool == "storage.object_metadata":
            return dict(fixtures.POISON_OBJECT)
        if tool == "monitoring.timeseries":
            return dict(fixtures.UTILIZATION_PROOF)
        return {}

    async def route_to_agent(self, from_agent: str, to_agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"routed": True, "to": to_agent}


class MockRegistry(RegistryPort):
    def __init__(self) -> None:
        self._specs: list[AgentSpec] = []

    async def publish(self, spec: AgentSpec) -> None:
        self._specs.append(spec)

    async def discover(self, department: str | None = None) -> list[AgentSpec]:
        if department is None:
            return list(self._specs)
        return [s for s in self._specs if department in s.departments]


class MockIdentity(IdentityPort):
    async def token_for(self, agent_id: str, scopes: list[str]) -> str:
        return f"mock-token:{agent_id}:{','.join(scopes)}"


class MockClassifier(ResourceClassifierPort):
    """Marks the out-of-band VM UNMANAGED and demonstrates SA assumption-chain resolution."""

    async def classify(self, resource: str) -> Attribution:
        if fixtures.MANUAL_VM_NAME in resource:
            # Created out-of-band by a CI service account (not in IaC). The raw
            # principal is a generic SA; the assumption chain resolves the real actor.
            return Attribution(
                created_by="ci-deployer@demo.iam.gserviceaccount.com",
                created_at="2026-07-03T09:14:00Z",
                last_activity="2026-07-03T09:20:00Z",
                source=ManagementSource.UNMANAGED,
                principal_type="service_account",
                triggering_entity="github-actions: acme/infra (commit a1b2c3, author jane@corp.com)",
                attribution_confidence="high",
            )
        return Attribution(
            created_by="terraform@demo.iam.gserviceaccount.com",
            created_at="2026-08-05T00:00:00Z",
            last_activity="2026-08-17T00:00:00Z",
            source=ManagementSource.TERRAFORM,
            principal_type="service_account",
            triggering_entity=None,
            attribution_confidence="medium",
        )


class MockRemediationChannel(RemediationChannelPort):
    async def can_handle(self, source: ManagementSource) -> bool:
        return True

    async def deliver(self, proposal: dict[str, Any]) -> dict[str, Any]:
        return {"status": "delivered", "channel": "mock-pr", "proposal": proposal.get("finding_id")}


class MockObservability(ObservabilityPort):
    def __init__(self) -> None:
        self.audit_log: list[dict[str, Any]] = []

    @contextmanager
    def span(self, name: str, attrs: dict[str, Any] | None = None):
        yield None

    def audit(self, agent_id: str, action: str, detail: dict[str, Any]) -> None:
        self.audit_log.append({"agent": agent_id, "action": action, "detail": detail})


def _sort_key(f: dict[str, Any]) -> tuple:
    """Deterministic governance urgency: severity, then unmanaged (needs attribution),
    then $ at stake, then compliance impact — highest urgency first."""
    md = f.get("metadata", {}) or {}
    return (
        _SEV_WEIGHT.get(str(f.get("severity", "low")), 3),
        0 if md.get("management_source") == "unmanaged" else 1,
        -float(f.get("est_monthly_savings_usd", 0) or 0),
        0 if md.get("controls") else 1,
        f.get("fingerprint", ""),
    )


def _rationale(f: dict[str, Any]) -> str:
    md = f.get("metadata", {}) or {}
    bits = [f'{f.get("severity", "low")} severity']
    sv = float(f.get("est_monthly_savings_usd", 0) or 0)
    if sv:
        bits.append(f"${sv:,.0f}/mo at stake")
    if md.get("management_source") == "unmanaged":
        bits.append("unmanaged (ClickOps) — no IaC owner, needs attribution + quarantine-first decommission")
    if md.get("ownership_status") == "conflict":
        bits.append("multi-state ownership conflict — human triage before any PR")
    ctrls = md.get("controls")
    if ctrls and ctrls.get("name"):
        bits.append(f'trips control “{ctrls["name"]}”')
    return "Prioritized because " + "; ".join(bits) + "."


class MockReasoner(ReasonerPort):
    """Deterministic stand-in for the Gemini orchestrator — same interface, no network.

    Produces stable ranking + prose from the finding fields so the whole pipeline runs
    (and the eval stays reproducible) with zero cloud access. Swap for GeminiReasoner
    (set CLOUDCAP_GEMINI=1) to have Gemini reason over the exact same findings.
    """

    async def prioritize(self, findings: list[dict[str, Any]], context: str = "") -> list[Ranking]:
        ordered = sorted(findings, key=_sort_key)
        return [Ranking(fingerprint=f.get("fingerprint", ""), rank=i + 1, rationale=_rationale(f))
                for i, f in enumerate(ordered)]

    async def explain(self, finding: dict[str, Any], proof: dict[str, Any] | None = None) -> str:
        md = finding.get("metadata", {}) or {}
        action = finding.get("recommended_action", "review and remediate")
        owner = md.get("owner_repo") or ("no IaC owner (codify-then-PR)"
                                         if md.get("ownership_status") != "conflict" else "conflicting states")
        pv = ""
        if proof and proof.get("summary"):
            pv = f" Evidence: {proof['summary']}."
        return (f'{finding.get("resource", "resource")} — {finding.get("title", "")}. '
                f'{_rationale(finding)}{pv} Recommended: {action}. Delivered as a human-approved PR to {owner}; '
                f'the agent has no cloud write access.')

    async def summarize(self, findings: list[dict[str, Any]], context: str = "") -> str:
        if not findings:
            return "No open findings in scope — posture is clean for this scan."
        crit = sum(1 for f in findings if str(f.get("severity")) == "critical")
        savings = sum(float(f.get("est_monthly_savings_usd", 0) or 0) for f in findings)
        unmanaged = sum(1 for f in findings if (f.get("metadata") or {}).get("management_source") == "unmanaged")
        top = sorted(findings, key=_sort_key)[0]
        parts = [f"{len(findings)} open finding(s)"]
        if crit:
            parts.append(f"{crit} critical")
        line = ", ".join(parts) + "."
        if savings:
            line += f" ${savings:,.0f}/mo recoverable."
        if unmanaged:
            line += f" {unmanaged} unmanaged (ClickOps) resource(s) need owner attribution + quarantine-first handling."
        line += f' Act first on {top.get("resource")} — {top.get("title")}.'
        return line
