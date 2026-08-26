"""Hexagonal ports — the portability seams.

Agent logic depends ONLY on these interfaces, never on a concrete platform.
For the hackathon we ship a single set of implementations (Google GEAP, see
adapters/google_geap.py). Each port has an off-Google analogue, so the fleet is
swappable without touching agent logic — this is what makes the "not locked to
Google" architecture story true, and it scores on Architectural Discipline (30%).

NOTE: keep these interfaces thin and stable. Do NOT leak Google types across them.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Finding:
    """A single governance finding produced by a scanner agent."""
    id: str
    category: str            # "cost" | "security" | "iam" | "compliance"
    severity: Severity
    resource: str
    title: str
    detail: str
    est_monthly_savings_usd: float = 0.0
    recommended_action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""  # STABLE id across scans; suppression/acceptance keys off this


def compute_fingerprint(category: str, resource: str, rule: str = "") -> str:
    """Deterministic, scan-stable id for a finding.

    Independent of scan-time-varying fields (savings, timestamps) so a user's
    accept/suppress decision keeps matching the same issue on later scans.
    """
    digest = hashlib.sha1(f"{category}|{resource}|{rule}".encode()).hexdigest()[:8]
    return f"CC-{digest}"


# --- MEMORY (GEAP Memory Bank | generic vector/kv store) --------------------
class MemoryPort(ABC):
    """Persistent, cross-session memory scoped to an identity (e.g. project/dept)."""

    @abstractmethod
    async def recall(self, scope: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return prior memories relevant to `query` within `scope`."""

    @abstractmethod
    async def remember(self, scope: str, facts: list[dict[str, Any]]) -> None:
        """Persist new facts (findings, dismissals, remediation state)."""


# --- GUARDRAIL (Model Armor | NeMo Guardrails / Llama Guard) ----------------
@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    redacted_text: str | None = None


class GuardrailPort(ABC):
    """Inline guardrails: prompt injection, tool poisoning, PII/data leakage."""

    @abstractmethod
    async def inspect_input(self, text: str, context: str = "") -> GuardResult:
        """Screen untrusted input (e.g. resource metadata) before the model sees it."""

    @abstractmethod
    async def inspect_output(self, text: str) -> GuardResult:
        """Screen model output for PII/secret leakage before it leaves the boundary."""


# --- GATEWAY (GEAP Agent Gateway | Apigee / Kong) ---------------------------
class GatewayPort(ABC):
    """Unified routing + runtime policy enforcement for agent<->data / agent<->agent."""

    @abstractmethod
    async def call_tool(self, agent_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Route a tool call through policy checks (read-only, region/residency, quotas)."""

    @abstractmethod
    async def route_to_agent(self, from_agent: str, to_agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Route sub-agent coordination through the gateway."""


# --- REGISTRY (GEAP Agent Registry | generic catalog) -----------------------
@dataclass
class AgentSpec:
    name: str
    version: str
    description: str
    departments: list[str]          # cross-department discovery
    capabilities: list[str]
    identity_sa: str                # zero-trust identity bound to this agent


class RegistryPort(ABC):
    """Publish / version / discover enterprise-approved agents."""

    @abstractmethod
    async def publish(self, spec: AgentSpec) -> None: ...

    @abstractmethod
    async def discover(self, department: str | None = None) -> list[AgentSpec]: ...


# --- IDENTITY (GEAP Agent Identity | Workload Identity / SPIFFE) ------------
class IdentityPort(ABC):
    """Zero-trust identity: each agent acts as a least-privilege principal."""

    @abstractmethod
    async def token_for(self, agent_id: str, scopes: list[str]) -> str:
        """Mint a scoped, short-lived credential for the agent."""


# --- OBSERVABILITY (OpenTelemetry + Cloud Trace) ----------------------------
class ObservabilityPort(ABC):
    """OTel-compliant audit logs + end-to-end reasoning-chain traces."""

    @abstractmethod
    def span(self, name: str, attrs: dict[str, Any] | None = None):
        """Context manager returning a trace span for a reasoning step."""

    @abstractmethod
    def audit(self, agent_id: str, action: str, detail: dict[str, Any]) -> None:
        """Emit an immutable audit-log record."""


# --- REMEDIATION CHANNEL (graceful degradation over brownfield infra) -------
class ManagementSource(str, Enum):
    TERRAFORM = "terraform"
    PULUMI = "pulumi"
    CLOUDFORMATION = "cloudformation"
    CONFIG_CONNECTOR = "config_connector"
    K8S_MANIFEST = "k8s_manifest"
    UNMANAGED = "unmanaged"   # ClickOps / manually created in console
    UNKNOWN = "unknown"


@dataclass
class Attribution:
    """Who created a resource and when — resolved from Cloud Audit Logs.

    Critical for MANUALLY-created resources: even with no IaC owner, audit logs
    reveal the creating principal + timestamp, turning an orphan into an
    actionable, owner-attributed finding.

    Service-account masking: if `principal_type == "service_account"`, the raw
    creator is a generic SA (CI/CD or Workload Identity). `triggering_entity` then
    holds the REAL actor found by traversing the assumption chain (correlating the
    creation timestamp with CI logs / WIF federation logs). Never stop at the SA.
    """
    created_by: str | None
    created_at: str | None
    last_activity: str | None
    source: ManagementSource
    principal_type: str = "unknown"          # "user" | "service_account" | "unknown"
    triggering_entity: str | None = None     # real human/pipeline behind a SA
    attribution_confidence: str = "low"      # low | medium | high


class ResourceClassifierPort(ABC):
    """Classify how a resource is managed + who made it (IaC vs ClickOps)."""

    @abstractmethod
    async def classify(self, resource: str) -> Attribution: ...


# --- IaC OWNERSHIP (state-index resolution) --------------------------------
class OwnershipStatus(str, Enum):
    MANAGED = "managed"      # exactly one Terraform state manages it → PR to that repo
    UNMANAGED = "unmanaged"  # in no known state → ClickOps → codify-then-PR
    CONFLICT = "conflict"    # multiple states claim it → drift → human triage, no auto-PR


@dataclass
class Ownership:
    """Which IaC repo/state manages a GCP resource, resolved from Terraform state.

    The binding is at the STATE level (by real resource id), not repo↔project — many
    repos may target one project; only the state that manages a resource owns it.
    """
    resource: str
    status: str                              # OwnershipStatus value
    repo: str | None = None
    state: str | None = None
    tf_address: str | None = None
    candidates: list[dict] = field(default_factory=list)  # populated on CONFLICT


class ResourceOwnershipPort(ABC):
    """Resolve a GCP resource id to its owning IaC state/repo (one / none / conflict)."""

    @abstractmethod
    async def resolve(self, resource_id: str) -> Ownership: ...


class RemediationChannelPort(ABC):
    """Deliver a proof-backed fix through the best available channel.

    Never mutates cloud directly. Implementations: PR (Terraform/K8s/other IaC),
    Ticket/artifact (no repo access), Codify-then-PR (unmanaged/ClickOps).
    """

    @abstractmethod
    async def can_handle(self, source: ManagementSource) -> bool: ...

    @abstractmethod
    async def deliver(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Route a {finding, proof, proposed_change} package to human review."""


class SecretResolverPort(ABC):
    """Resolve a secret reference to its value at runtime.

    Nothing else in the code sees a raw token. Live = Secret Manager;
    mock = env vars. Secret *values* never live in config or Terraform state.
    """

    @abstractmethod
    async def resolve(self, secret_ref: str) -> str: ...


# --- REASONER (Gemini via Vertex AI | any LLM) ------------------------------
@dataclass
class Ranking:
    """LLM-assigned priority for a single finding (rank 1 = act first)."""
    fingerprint: str
    rank: int
    rationale: str


class ReasonerPort(ABC):
    """LLM reasoning seam. Live = Gemini (Vertex AI); mock = deterministic templates.

    The reasoner NEVER invents or mutates findings — the scanner tools stay the
    deterministic ground truth. It only (a) RANKS findings by governance urgency,
    (b) EXPLAINS them for a human reviewer, and (c) SUMMARIZES the scan. Keeping the
    LLM off the security-critical write path (no autonomous action, no fabricated
    findings) is the governed-agent posture; it's still genuinely agentic — a Gemini
    orchestrator reasoning over tool output, exactly the GEAP pattern.
    """

    @abstractmethod
    async def prioritize(self, findings: list[dict[str, Any]], context: str = "") -> list[Ranking]:
        """Return one Ranking per finding, ordered by urgency (rank 1 = act first)."""

    @abstractmethod
    async def explain(self, finding: dict[str, Any], proof: dict[str, Any] | None = None) -> str:
        """One-paragraph rationale shown to the human reviewer on the finding / PR."""

    @abstractmethod
    async def summarize(self, findings: list[dict[str, Any]], context: str = "") -> str:
        """Executive summary of the scan for a FinOps / Security lead."""


@dataclass
class FleetContext:
    """Bundle of ports injected into every agent — the only platform surface."""
    memory: MemoryPort
    guardrail: GuardrailPort
    gateway: GatewayPort
    registry: RegistryPort
    identity: IdentityPort
    observability: ObservabilityPort
    classifier: ResourceClassifierPort
    remediation_channel: RemediationChannelPort
    ownership: ResourceOwnershipPort
    reasoner: ReasonerPort | None = None
