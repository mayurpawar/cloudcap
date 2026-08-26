"""FleetContext factory — the only place adapters are chosen.

`mock` wires in-memory adapters (runs anywhere, no cloud). `live` wires the Google
GEAP adapters. Agent logic is identical across both — that's the hexagonal payoff.
"""

from __future__ import annotations

import os

from agents.ports.interfaces import FleetContext, ReasonerPort


def _build_observability(durable_audit: bool, audit_path: str):
    """The audit sink, decoupled from data mode. Non-durable (dashboard page loads) =
    ephemeral in-memory. Durable = a hash-chained trail: local file by default, or
    **Cloud Logging** (immutable) when CLOUDCAP_AUDIT=cloud (set by the installer on
    deploy). Cloud Logging self-heals to the file if unreachable — never loses a record."""
    if not durable_audit:
        from agents.adapters.local_mock import MockObservability
        return MockObservability()
    if os.environ.get("CLOUDCAP_AUDIT", "file").lower() == "cloud":
        try:
            from agents.adapters.google_geap import OtelObservabilityAdapter
            return OtelObservabilityAdapter()
        except Exception:
            pass  # SDK missing → local file trail
    from agents.audit import FileAuditObservability
    return FileAuditObservability(audit_path)


def _build_reasoner(project: str, location: str) -> ReasonerPort:
    """The LLM layer. Deterministic MockReasoner by default (runs anywhere); set
    CLOUDCAP_GEMINI=1 to reason over the SAME findings with real Gemini on Vertex AI.
    Reasoning mode is decoupled from data mode on purpose — you can demo live Gemini
    orchestration on top of the deterministic mock fleet."""
    if os.environ.get("CLOUDCAP_GEMINI", "").lower() in ("1", "true", "yes"):
        try:
            from agents.adapters.google_geap import GeminiReasoner
            return GeminiReasoner(project=project, location=location)
        except Exception:
            pass  # SDK/creds missing → fall through to the deterministic reasoner
    from agents.adapters.local_mock import MockReasoner
    return MockReasoner()


def build_context(
    mode: str,
    project: str,
    location: str = "us-central1",
    persist_memory: bool = False,
    memory_path: str = "eval/memory_state.json",
    durable_audit: bool = True,
    audit_path: str = "eval/audit_log.jsonl",
) -> FleetContext:
    if mode == "mock":
        from agents.adapters import local_mock as m
        from agents.iac_resolver import MockStateIndexResolver
        from agents.remediation.pr_channel import MockGitBackend, PullRequestChannel
        # Persistent memory demonstrates cross-run continuity (Memory Bank pillar);
        # ephemeral memory (default) always shows a full scan (dashboard, one-off runs).
        memory = m.FileBackedMemory(memory_path) if persist_memory else m.MockMemory()
        # Durable + tamper-evident audit for real scans; ephemeral for dashboard loads.
        # Sink is file locally, Cloud Logging on deploy (CLOUDCAP_AUDIT=cloud).
        observability = _build_observability(durable_audit, audit_path)
        return FleetContext(
            memory=memory,
            guardrail=m.MockGuardrail(),
            gateway=m.MockGateway(),
            registry=m.MockRegistry(),
            identity=m.MockIdentity(),
            observability=observability,
            classifier=m.MockClassifier(),
            # Real PR-channel logic over a disk-writing backend (inspectable, no GitHub).
            remediation_channel=PullRequestChannel(MockGitBackend("eval/prs"), repo="acme/infra"),
            ownership=MockStateIndexResolver(),
            # LLM layer: MockReasoner, or real Gemini when CLOUDCAP_GEMINI=1.
            reasoner=_build_reasoner(project, location),
        )

    if mode == "live":
        # Runnable live slice: REAL read-only cost data (Recommender) + durable
        # Cloud Logging audit + Gemini reasoning. Heavier adapters (Model Armor,
        # audit-log attribution, Terraform-state ownership) stay mock until validated
        # against a real project — swapped in one line each, no agent-logic change.
        from agents.adapters import google_geap as g
        from agents.adapters import local_mock as m
        from agents.iac_resolver import LiveStateIndexResolver
        from agents.remediation.pr_channel import GitHubBackend, MockGitBackend, PullRequestChannel
        # --project = the project to AUDIT; GOOGLE_CLOUD_PROJECT = the hub where our
        # app / Vertex / Cloud Logging live (they can differ).
        hub = os.environ.get("GOOGLE_CLOUD_PROJECT") or project
        memory = m.FileBackedMemory(memory_path) if persist_memory else m.MockMemory()
        # Live audit defaults to Cloud Logging (unless CLOUDCAP_AUDIT overrides).
        os.environ.setdefault("CLOUDCAP_AUDIT", "cloud")
        observability = _build_observability(durable_audit, audit_path)
        try:
            reasoner: ReasonerPort = g.GeminiReasoner(project=hub, location=location)
        except Exception:
            reasoner = m.MockReasoner()
        # Real GitHub PR remediation when a token is present (Secret Manager → GITHUB_TOKEN);
        # otherwise the disk-writing mock so the flow still runs with no GitHub access.
        pr_backend = (GitHubBackend() if os.environ.get("GITHUB_TOKEN")
                      else MockGitBackend("eval/prs"))
        # Model Armor guardrail (real REST calls, deterministic backstop) — the hub project
        # holds the template; falls back to markers if the service/creds are unavailable.
        guardrail = g.ModelArmorAdapter(
            project=hub,
            location=os.environ.get("CLOUDCAP_MODELARMOR_LOCATION", "us-central1"),
            template_id=os.environ.get("CLOUDCAP_MODELARMOR_TEMPLATE", "cloudcap-guard"))
        return FleetContext(
            memory=memory,
            guardrail=guardrail,                               # REAL Model Armor (+ backstop)
            gateway=g.AgentGatewayAdapter(project=project),    # REAL cost + security + IAM
            registry=m.MockRegistry(),
            identity=m.MockIdentity(),
            observability=observability,                       # Cloud Logging (+ file fallback)
            classifier=g.LiveResourceClassifier(project=project),  # REAL IaC-vs-ClickOps (Asset labels)
            remediation_channel=PullRequestChannel(pr_backend, repo="acme/infra"),
            ownership=LiveStateIndexResolver(),                # honest: UNMANAGED until real TF state indexed
            reasoner=reasoner,
        )

    raise ValueError(f"unknown mode: {mode!r} (expected 'mock' or 'live')")
