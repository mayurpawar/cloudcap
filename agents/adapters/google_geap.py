"""Google Gemini Enterprise Agent Platform (GEAP) implementations of the ports.

This is the ONE implementation we ship for the hackathon. Every class maps a
required category pillar to a real Google product.

IMPORTANT: exact SDK symbols/signatures move fast — pin these against the current
GEAP / Vertex AI docs during D6-D9. The structure (one adapter per port) is what
matters and is stable. TODO markers show where live SDK calls go.

Pillar mapping:
  MemoryPort        -> Vertex AI Memory Bank
  GuardrailPort     -> Model Armor
  GatewayPort       -> GEAP Agent Gateway
  RegistryPort      -> GEAP Agent Registry
  IdentityPort      -> IAM / Workload Identity
  ObservabilityPort -> OpenTelemetry -> Cloud Trace
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from typing import Any

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
    ResourceClassifierPort,
)


class MemoryBankAdapter(MemoryPort):
    """Vertex AI Memory Bank — persistent cross-session context."""

    def __init__(self, project: str, location: str, agent_engine_id: str):
        self.project = project
        self.location = location
        self.agent_engine_id = agent_engine_id
        # TODO(D6): init vertexai + Memory Bank client for this Agent Engine.
        # from vertexai import agent_engines / preview memory bank client.

    async def recall(self, scope: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        # TODO(D6): memory_bank.retrieve_memories(scope=scope, query=query, top_k=limit)
        return []

    async def remember(self, scope: str, facts: list[dict[str, Any]]) -> None:
        # TODO(D6): memory_bank.generate_memories / create_memory(scope, facts)
        return None


# Deterministic backstop markers — used when Model Armor is unreachable so the
# security screen NEVER silently fails open. Mirrors the mock's markers.
_INJECTION_BACKSTOP = ("ignore prior", "ignore your", "ignore all previous",
                       "system:", "exfiltrate", "email all", "disregard", "override your")


class ModelArmorAdapter(GuardrailPort):
    """Model Armor — inline guardrails against prompt injection / tool poisoning / PII.

    Calls the Model Armor REST API (sanitizeUserPrompt / sanitizeModelResponse) against a
    template with PI-&-jailbreak + malicious-URI filters. If the service is unreachable it
    degrades to a deterministic marker check — the security screen fails CLOSED on a real
    injection, never silently open. Read-only: it inspects text, it never acts on it."""

    def __init__(self, project: str, location: str = "us-central1",
                 template_id: str = "cloudcap-guard"):
        self.project = project
        self.location = location
        self.template_id = template_id
        self._base = f"https://modelarmor.{location}.rep.googleapis.com/v1"
        self._creds = None

    def _token(self) -> str:
        import google.auth
        import google.auth.transport.requests
        if self._creds is None:
            self._creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not self._creds.valid:
            self._creds.refresh(google.auth.transport.requests.Request())
        return self._creds.token

    def _sanitize(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST to :sanitizeUserPrompt / :sanitizeModelResponse. Returns the parsed
        sanitizationResult, or None on any transport/auth error (→ caller backstops)."""
        import json as _json
        import urllib.request
        url = (f"{self._base}/projects/{self.project}/locations/{self.location}"
               f"/templates/{self.template_id}:{method}")
        req = urllib.request.Request(url, data=_json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._token()}",
                                              "Content-Type": "application/json",
                                              "x-goog-user-project": self.project})
        with urllib.request.urlopen(req, timeout=15) as r:
            return _json.load(r).get("sanitizationResult")

    async def inspect_input(self, text: str, context: str = "") -> GuardResult:
        if not text or not text.strip():
            return GuardResult(allowed=True)
        try:
            res = await asyncio.to_thread(
                self._sanitize, "sanitizeUserPrompt", {"userPromptData": {"text": text}})
            if res is not None:
                if res.get("filterMatchState") == "MATCH_FOUND":
                    hits = ", ".join(sorted(res.get("filterResults", {}).keys())) or "prompt-injection"
                    return GuardResult(allowed=False,
                                       reason=f"Model Armor blocked untrusted input ({hits})")
                return GuardResult(allowed=True)
        except Exception:
            pass  # fall through to the deterministic backstop
        low = text.lower()
        if any(m in low for m in _INJECTION_BACKSTOP):
            return GuardResult(allowed=False,
                               reason="prompt-injection / tool-poisoning detected (deterministic backstop)")
        return GuardResult(allowed=True)

    async def inspect_output(self, text: str) -> GuardResult:
        if not text or not text.strip():
            return GuardResult(allowed=True, redacted_text=text)
        try:
            res = await asyncio.to_thread(
                self._sanitize, "sanitizeModelResponse", {"modelResponseData": {"text": text}})
            if res is not None and res.get("filterMatchState") == "MATCH_FOUND":
                return GuardResult(allowed=False, redacted_text=None,
                                   reason="Model Armor flagged model output (PII/secret/unsafe)")
        except Exception:
            pass
        return GuardResult(allowed=True, redacted_text=text)


# Read-only data-plane tools the scanners are allowed to call. The gateway REFUSES
# anything not on this allowlist — the enforcement point that makes "the agent has no
# cloud write access" a runtime guarantee, not just a claim.
_READ_ONLY_TOOLS = frozenset({
    "recommender.list", "run.utilization", "iam.findings", "asset.security_findings",
    "storage.object_metadata", "monitoring.timeseries",
})

# A Cloud Run service is "over-provisioned" if it holds instances warm (min-instances>=1)
# yet serves almost nothing. Thresholds are conservative + configurable.
_RUN_MAX_REQS_PER_DAY = int(os.environ.get("CLOUDCAP_RUN_MAX_REQS_PER_DAY", "50"))
# Median CPU utilization below this on a min-instances>=1 service = over-provisioned.
_RUN_LOW_CPU = float(os.environ.get("CLOUDCAP_RUN_LOW_CPU", "0.05"))

# Cost recommenders (Active Assist). Cost signal is the cheapest, most reliable
# real data — free API, per-project, read-only.
_COST_RECOMMENDERS = (
    "google.compute.instance.IdleResourceRecommender",
    "google.compute.instance.MachineTypeRecommender",
    "google.compute.disk.IdleResourceRecommender",
    "google.compute.address.IdleResourceRecommender",
    "google.cloudsql.instance.IdleRecommender",
    "google.cloudsql.instance.OverprovisionedRecommender",
)


def _short_resource(full: str) -> str:
    """.../instances/foo -> foo. Best-effort short name for display/fingerprint."""
    return full.rsplit("/", 1)[-1] if full else full


def _rec_target(rec: Any) -> str:
    """Best-effort target resource from a recommendation's operation groups."""
    try:
        for grp in rec.content.operation_groups:
            for op in grp.operations:
                if getattr(op, "resource", ""):
                    return _short_resource(op.resource)
    except Exception:
        pass
    return _short_resource(getattr(rec, "name", ""))


class AgentGatewayAdapter(GatewayPort):
    """GEAP Agent Gateway — routing + runtime policy enforcement (read-only).

    Live data plane. Enforces the read-only allowlist, then routes each tool to its
    Google API. Cost (Recommender + Cloud Run usage), Security (Asset Inventory
    public-exposure) and IAM (primitive-role sprawl + IAM Recommender) are wired
    live; the remaining probes fail safe (empty) until validated on a real project.
    """

    def __init__(self, project: str, locations: list[str] | None = None):
        self.project = project
        # Recommender is zonal/regional — scan a configurable set (env override).
        env_locs = os.environ.get("CLOUDCAP_LOCATIONS", "")
        self.locations = locations or [x.strip() for x in env_locs.split(",") if x.strip()] or \
            ["us-central1", "us-central1-a", "us-central1-b"]

    async def call_tool(self, agent_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool not in _READ_ONLY_TOOLS:
            # Governance guarantee: reject anything outside the read-only allowlist.
            raise PermissionError(f"gateway denied non-read tool {tool!r} for {agent_id}")
        project = args.get("project", self.project)
        if tool == "recommender.list":
            return {"items": self._cost_recommendations(project)}
        if tool == "run.utilization":
            return {"items": self._cloud_run_overprovisioned(project)}
        if tool == "asset.security_findings":
            return {"items": self._security_findings(project)}
        if tool == "iam.findings":
            return {"items": self._iam_findings(project)}
        if tool == "storage.object_metadata":
            # Untrusted object metadata the security agent would ingest — screened by
            # Model Armor before the model ever sees it (tool-poisoning defense).
            return self._object_metadata(project)
        # monitoring.timeseries — wired next; fail safe so live mode always runs.
        return {"items": []}

    def _object_metadata(self, project: str) -> dict[str, Any]:
        """Return one object's name + (small) content from the project's buckets — the
        untrusted metadata a security agent would ingest. Read-only (objectViewer). An
        attacker can plant an object whose NAME or CONTENT is a prompt injection; the
        guardrail (Model Armor) screens this return value before the model sees it."""
        try:
            from google.cloud import storage
        except Exception:
            return {}
        try:
            client = storage.Client(project=project)
            buckets = list(client.list_buckets())
        except Exception:
            return {}
        for b in buckets:
            try:
                for obj in client.list_blobs(b.name, max_results=25):
                    content = ""
                    try:
                        if (obj.size or 0) <= 8192 and (obj.content_type or "").startswith(("text", "application/json", "")):
                            content = obj.download_as_text()[:2000]
                    except Exception:
                        content = ""
                    # First object is enough — the agent ingests whatever metadata it finds.
                    return {"name": obj.name, "content": content, "bucket": b.name}
            except Exception:
                continue
        return {}

    def _cost_recommendations(self, project: str) -> list[dict[str, Any]]:
        try:
            from google.cloud import recommender_v1
        except Exception:
            return []  # SDK not installed → no cost data (mock mode is the fallback)
        client = recommender_v1.RecommenderClient()
        items: list[dict[str, Any]] = []
        for loc in self.locations:
            for rid in _COST_RECOMMENDERS:
                parent = f"projects/{project}/locations/{loc}/recommenders/{rid}"
                try:
                    for rec in client.list_recommendations(parent=parent):
                        cost = rec.primary_impact.cost_projection.cost
                        units = abs(int(getattr(cost, "units", 0) or 0))  # savings shown as negative cost
                        items.append({
                            "targetResource": _rec_target(rec),
                            "description": rec.description,
                            "recommendedAction": rec.description,
                            "recommenderSubtype": rec.recommender_subtype,
                            "primaryImpact": {"costProjection": {"cost": {"units": units}}},
                        })
                except Exception:
                    continue  # recommender not enabled in this location → skip
        return items

    def _cloud_run_overprovisioned(self, project: str) -> list[dict[str, Any]]:
        """Flag Cloud Run services kept warm (min-instances>=1) but barely used.

        Cloud Run Admin API v2 for config (min-instances, CPU/mem) + Cloud Monitoring for
        actual request volume and billable instance-time. This catches waste Recommender
        misses. Cost is an ESTIMATE from billable instance-time; validate against billing.
        """
        try:
            from google.cloud import run_v2
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        for loc in self.locations:
            parent = f"projects/{project}/locations/{loc}"
            try:
                services = run_v2.ServicesClient().list_services(parent=parent)
            except Exception:
                continue
            for svc in services:
                try:
                    scaling = svc.template.scaling
                    min_inst = int(getattr(scaling, "min_instance_count", 0) or 0)
                    if min_inst < 1:
                        continue  # already scales to zero — nothing to flag
                    name = svc.name.rsplit("/", 1)[-1]
                    cpu_util = self._run_cpu_utilization(project, name, loc)
                    reqs_day = self._run_requests_per_day(project, name, loc)
                    # Over-provisioned if kept warm (min>=1) AND barely working: very low
                    # CPU utilization is the primary signal (a busy request count can still
                    # be near-idle CPU); low request volume is a secondary signal.
                    low_cpu = cpu_util is not None and cpu_util < _RUN_LOW_CPU
                    low_reqs = reqs_day is not None and reqs_day <= _RUN_MAX_REQS_PER_DAY
                    if not (low_cpu or low_reqs):
                        continue  # genuinely busy
                    cpu, mem = self._run_resources(svc)
                    cost = self._run_idle_cost_estimate(min_inst, cpu, mem)
                    items.append({
                        "resource": name, "region": loc, "minInstances": min_inst,
                        "cpu": cpu, "memoryMiB": mem,
                        "requestsPerDay": reqs_day if reqs_day is not None else "unknown",
                        "cpuUtilization": round(cpu_util, 4) if cpu_util is not None else 0.0,
                        "billableInstanceHoursMonthly": 730 * min_inst,
                        "estMonthlyCostUsd": round(cost),
                        "recommendedAction": ("Set min-instances=0 + enable startup-CPU-boost, or "
                                              "right-size CPU/memory, or keep warm only in business "
                                              "hours via Cloud Scheduler. Trade-off: cold-start vs. cost."),
                    })
                except Exception:
                    continue
        return items

    def _run_cpu_utilization(self, project: str, service: str, loc: str) -> float | None:
        """Median CPU utilization (0..1) over 14 days — the real over-provisioning signal.
        A min-instances>=1 service running at <5% CPU is paying for near-idle capacity."""
        try:
            from datetime import datetime, timedelta, timezone

            from google.cloud import monitoring_v3
            client = monitoring_v3.MetricServiceClient()
            end = datetime.now(timezone.utc)
            interval = monitoring_v3.TimeInterval(
                {"end_time": end, "start_time": end - timedelta(days=14)})
            agg = monitoring_v3.Aggregation(
                alignment_period={"seconds": 86400},
                per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_PERCENTILE_50,
                cross_series_reducer=monitoring_v3.Aggregation.Reducer.REDUCE_MEAN,
            )
            flt = ('metric.type="run.googleapis.com/container/cpu/utilizations" '
                   f'AND resource.labels.service_name="{service}"')
            series = client.list_time_series(request={
                "name": f"projects/{project}", "filter": flt, "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL, "aggregation": agg})
            vals = [p.value.double_value for ts in series for p in ts.points]
            return sum(vals) / len(vals) if vals else None
        except Exception:
            return None

    @staticmethod
    def _run_resources(svc) -> tuple[str, int]:
        try:
            limits = svc.template.containers[0].resources.limits  # {"cpu": "1", "memory": "512Mi"}
            cpu = str(limits.get("cpu", "1"))
            mem_raw = str(limits.get("memory", "512Mi"))
            mem = int(mem_raw.rstrip("MiGB") or 512) * (1024 if mem_raw.endswith(("Gi", "G")) else 1)
            return cpu, mem
        except Exception:
            return "1", 512

    def _run_requests_per_day(self, project: str, service: str, loc: str) -> int | None:
        """30-day avg requests/day from run.googleapis.com/request_count. None if unknown."""
        try:
            from datetime import datetime, timedelta, timezone

            from google.cloud import monitoring_v3
            client = monitoring_v3.MetricServiceClient()
            end = datetime.now(timezone.utc)
            interval = monitoring_v3.TimeInterval(
                {"end_time": end, "start_time": end - timedelta(days=30)})
            # request_count is a DELTA int metric: sum per day, then sum across series.
            aggregation = monitoring_v3.Aggregation(
                alignment_period={"seconds": 86400},
                per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_DELTA,
                cross_series_reducer=monitoring_v3.Aggregation.Reducer.REDUCE_SUM,
            )
            flt = ('metric.type="run.googleapis.com/request_count" '
                   f'AND resource.labels.service_name="{service}"')
            series = client.list_time_series(request={
                "name": f"projects/{project}",
                "filter": flt,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation,
            })
            total = sum(int(p.value.int64_value or 0) for ts in series for p in ts.points)
            return int(total / 30)
        except Exception:
            return None

    @staticmethod
    def _run_idle_cost_estimate(min_inst: int, cpu: str, mem_mib: int) -> float:
        """Rough always-on cost: allocated vCPU/mem × ~730h/mo at Cloud Run rates.
        Deliberately conservative; the exact figure comes from billing export."""
        try:
            vcpu = float(cpu)
        except ValueError:
            vcpu = 1.0
        secs = 730 * 3600 * min_inst
        cpu_cost = vcpu * secs * 0.0000240
        mem_cost = (mem_mib / 1024.0) * secs * 0.0000025
        return cpu_cost + mem_cost

    # --- SECURITY posture (live, read-only via Cloud Asset Inventory) ----------
    def _security_findings(self, project: str) -> list[dict[str, Any]]:
        """Public-exposure posture from Cloud Asset Inventory (cloudasset.viewer).

        `searchAllIamPolicies` surfaces every resource granting `allUsers` /
        `allAuthenticatedUsers` — the canonical "publicly exposed" signal. Data
        resources (buckets, Pub/Sub, BigQuery) exposed to the world are CRITICAL;
        a public web frontend (Cloud Run / App Engine) is HIGH ("confirm intended;
        restrict ingress if internal"). Read-only — we never mutate a policy."""
        client = self._asset_client()
        if client is None:
            return []
        scope = f"projects/{project}"
        items: list[dict[str, Any]] = []
        try:
            results = client.search_all_iam_policies(request={
                "scope": scope,
                "query": "policy:allUsers OR policy:allAuthenticatedUsers",
                "page_size": 100,
            })
            for r in results:
                public = sorted({m for b in r.policy.bindings for m in b.members
                                 if m in ("allUsers", "allAuthenticatedUsers")})
                if not public:
                    continue
                atype = r.asset_type or ""
                short = r.resource.rsplit("/", 1)[-1]
                is_frontend = atype.startswith(("run.googleapis.com", "appengine.googleapis.com"))
                sev = "high" if is_frontend else "critical"
                kind = atype.split("/")[-1] or "resource"
                items.append({
                    "resource": short,
                    "severity": sev,
                    "title": f"Publicly accessible {kind} ({', '.join(public)})",
                    "detail": (f"{atype} '{short}' grants {', '.join(public)} in its IAM "
                               f"policy — reachable by anyone on the internet."),
                    "recommendedAction": (
                        "Confirm public access is intended; if this is an internal service, "
                        "remove the allUsers/allAuthenticatedUsers binding and restrict "
                        "ingress (internal + load-balancer) or require IAP/authenticated access."
                        if is_frontend else
                        "Remove the allUsers/allAuthenticatedUsers IAM binding immediately and "
                        "grant access to specific principals only (least privilege)."),
                    "assetType": atype,
                })
        except Exception:
            return items  # partial results are still useful; never crash the scan
        return items

    # --- IAM over-privilege (live, read-only) ----------------------------------
    @staticmethod
    def _is_google_managed_sa(email: str) -> bool:
        """Google-managed default/agent SAs (compute default, App Engine, service agents,
        cloud services). We don't flag these for editor — they ship with it by design and
        would be pure noise. A *user-created* SA (…@PROJECT.iam.gserviceaccount.com, not a
        service agent) holding a primitive role is the real least-privilege violation."""
        return (email.endswith("-compute@developer.gserviceaccount.com")
                or email.endswith("@appspot.gserviceaccount.com")
                or email.endswith("@cloudservices.gserviceaccount.com")
                or email.startswith("service-"))

    def _iam_findings(self, project: str) -> list[dict[str, Any]]:
        """Excessive-privilege posture: (a) primitive roles (owner/editor) granted to
        human users OR user-created service accounts at the project level — a least-privilege
        violation Asset Inventory exposes directly; (b) IAM Recommender's unused-permission
        recommendations (recommender.iamViewer) where the project has enough history."""
        items: list[dict[str, Any]] = []
        client = self._asset_client()
        if client is not None:
            try:
                results = client.search_all_iam_policies(request={
                    "scope": f"projects/{project}",
                    "query": "roles:(roles/owner OR roles/editor)",
                    "page_size": 100,
                })
                for r in results:
                    for b in r.policy.bindings:
                        if b.role not in ("roles/owner", "roles/editor"):
                            continue
                        primitive = b.role.split("/")[-1]
                        # Humans with any primitive role → always flag.
                        users = sorted(m for m in b.members if m.startswith(("user:", "allUsers")))
                        if users:
                            who = ", ".join(u.split(":", 1)[-1] for u in users)
                            items.append({
                                "resource": f"iam-policy/{project}",
                                "severity": "high",
                                "title": f"Primitive {b.role} granted to human user(s): {who}",
                                "detail": (f"{who} hold the primitive role {b.role} on project "
                                           f"'{project}'. Primitive roles are broad and violate "
                                           f"least privilege; owner also allows IAM self-escalation."),
                                "recommendedAction": (
                                    f"Replace {b.role} with least-privilege predefined roles scoped to "
                                    f"what each principal actually needs; reserve {primitive} for "
                                    f"break-glass service accounts, not standing human access."),
                            })
                        # Service accounts: owner is always alarming; editor only when the SA
                        # is user-created (Google-managed defaults ship with editor by design).
                        sas = sorted(m.split(":", 1)[-1] for m in b.members
                                     if m.startswith("serviceAccount:"))
                        flagged = [s for s in sas if b.role == "roles/owner"
                                   or not self._is_google_managed_sa(s)]
                        if flagged:
                            who = ", ".join(flagged)
                            sev = "critical" if b.role == "roles/owner" else "high"
                            items.append({
                                "resource": f"iam-policy/{project}",
                                "severity": sev,
                                "title": f"Primitive {b.role} granted to service account(s): {who}",
                                "detail": (f"Service account(s) {who} hold the primitive role {b.role} "
                                           f"on project '{project}'. An application identity with "
                                           f"{primitive} far exceeds least privilege; if the SA key or "
                                           f"workload is compromised, the blast radius is the whole "
                                           f"project (owner additionally permits IAM self-escalation)."),
                                "recommendedAction": (
                                    f"Grant the service account only the predefined roles its workload "
                                    f"needs (e.g. specific viewer/writer roles) and remove {b.role}. "
                                    f"Never assign owner/editor to an application service account."),
                            })
            except Exception:
                pass
        items += self._iam_recommender_findings(project)
        return items

    def _iam_recommender_findings(self, project: str) -> list[dict[str, Any]]:
        """IAM Recommender (google.iam.policy.Recommender): unused-permission findings.
        Empty for young/low-traffic projects (needs ~90d of usage) — that's expected."""
        try:
            from google.cloud import recommender_v1
        except Exception:
            return []
        items: list[dict[str, Any]] = []
        try:
            client = recommender_v1.RecommenderClient()
            parent = (f"projects/{project}/locations/global/recommenders/"
                      "google.iam.policy.Recommender")
            for rec in client.list_recommendations(parent=parent):
                items.append({
                    "resource": f"iam-policy/{project}",
                    "severity": "high",
                    "title": "IAM Recommender: excess permissions detected",
                    "detail": rec.description,
                    "recommendedAction": (rec.description or
                                          "Apply the IAM Recommender suggestion to remove unused permissions."),
                })
        except Exception:
            return items
        return items

    def _asset_client(self):
        """Lazy Cloud Asset Inventory client; None if the SDK/creds are unavailable
        (live mode then degrades to the cost slice rather than crashing)."""
        try:
            from google.cloud import asset_v1
            return asset_v1.AssetServiceClient()
        except Exception:
            return None

    async def route_to_agent(self, from_agent: str, to_agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"routed": True, "to": to_agent}


class LiveResourceClassifier(ResourceClassifierPort):
    """Real IaC-vs-ClickOps attribution from Cloud Asset Inventory (read-only).

    GCP's Terraform provider stamps `goog-terraform-provisioned=true` on resources it
    creates. We snapshot the set of Terraform-provisioned resources in the project once,
    then classify each finding's resource: labelled → TERRAFORM (managed by IaC);
    otherwise → UNMANAGED (ClickOps / console / CLI, no IaC provisioning label found).

    We deliberately do NOT fabricate a creating principal — precise creator attribution
    needs Cloud Audit Log correlation (not wired), so `created_by`/`triggering_entity`
    stay None and confidence is 'medium' (the label signal is strong but not absolute)."""

    def __init__(self, project: str):
        self.project = project
        self._tf_resources: set[str] | None = None  # lazy snapshot of TF-provisioned names

    def _terraform_set(self) -> set[str]:
        if self._tf_resources is not None:
            return self._tf_resources
        names: set[str] = set()
        try:
            from google.cloud import asset_v1
            client = asset_v1.AssetServiceClient()
            for r in client.search_all_resources(request={
                    "scope": f"projects/{self.project}", "page_size": 500}):
                labels = dict(r.labels) if r.labels else {}
                if any("terraform" in k.lower() for k in labels):
                    names.add(r.display_name)
                    names.add(r.name.rsplit("/", 1)[-1])
        except Exception:
            pass  # SDK/creds/permission → empty set → everything reads as unmanaged
        self._tf_resources = names
        return names

    async def classify(self, resource: str) -> Attribution:
        short = resource.rsplit("/", 1)[-1]
        tf = self._terraform_set()
        managed = short in tf or any(short in n or n in short for n in tf)
        if managed:
            return Attribution(
                created_by=None, created_at=None, last_activity=None,
                source=ManagementSource.TERRAFORM, principal_type="unknown",
                triggering_entity=None, attribution_confidence="medium")
        # No IaC provisioning label found in Asset Inventory → ClickOps / unmanaged.
        return Attribution(
            created_by=None, created_at=None, last_activity=None,
            source=ManagementSource.UNMANAGED, principal_type="unknown",
            triggering_entity=None, attribution_confidence="medium")


_FLEET_VERSION = os.environ.get("CLOUDCAP_FLEET_VERSION", "1.0.0")


def fleet_roster(project: str) -> list[AgentSpec]:
    """The enterprise-approved fleet — mirrors terraform/fleet `var.agents` (the source of
    truth for which agents exist + their least-privilege roles). Capabilities reflect the
    tools each scanner actually calls in this codebase. `identity_sa` is the real GCP
    service account the agent runs as (empty for remediation: writes are brokered behind a
    human-approved PR, so it holds NO standing cloud identity)."""
    p = project
    return [
        AgentSpec(name="orchestrator", version=_FLEET_VERSION,
                  description="Ranks + narrates findings with Gemini; never mutates them.",
                  departments=["finops", "secops", "platform"],
                  capabilities=["reasoning", "prioritization", "executive-summary"],
                  identity_sa=f"cc-orchestrator@{p}.iam.gserviceaccount.com"),
        AgentSpec(name="cost_scanner", version=_FLEET_VERSION,
                  description="Recommender + Cloud Run utilization → cost findings.",
                  departments=["finops"],
                  capabilities=["recommender.list", "run.utilization"],
                  identity_sa=f"cc-cost-scanner@{p}.iam.gserviceaccount.com"),
        AgentSpec(name="security_scanner", version=_FLEET_VERSION,
                  description="Asset Inventory public-exposure + object-metadata screen (Model Armor).",
                  departments=["secops"],
                  capabilities=["asset.security_findings", "storage.object_metadata"],
                  identity_sa=f"cc-security-scanner@{p}.iam.gserviceaccount.com"),
        AgentSpec(name="iam_scanner", version=_FLEET_VERSION,
                  description="Primitive-role sprawl + IAM Recommender.",
                  departments=["secops"],
                  capabilities=["iam.findings", "recommender.iam"],
                  identity_sa=f"cc-iam-scanner@{p}.iam.gserviceaccount.com"),
        AgentSpec(name="compliance_scanner", version=_FLEET_VERSION,
                  description="Maps findings to SOC 2 / CIS / ISO 27001 / PCI DSS controls.",
                  departments=["grc"],
                  capabilities=["control-mapping"],
                  identity_sa=f"cc-compliance-scanner@{p}.iam.gserviceaccount.com"),
        AgentSpec(name="remediation", version=_FLEET_VERSION,
                  description="GitOps PR remediation; no standing cloud identity (human-approved PR).",
                  departments=["platform"],
                  capabilities=["pr-remediation", "secret-redaction"],
                  identity_sa=""),
    ]


class AgentRegistryAdapter(RegistryPort):
    """GEAP Agent Registry — publish / version / discover approved agents, persisted in
    Firestore and VERIFIED against real deployed GCP service accounts.

    publish() records the agent (Firestore doc `agent_registry`) and checks — live, via the
    IAM API — that its bound service account actually exists (registry-vs-reality drift is a
    real enterprise concern). discover() returns the persisted roster, optionally filtered by
    department. Read-only against IAM; the only write is to our own Firestore registry doc."""

    _DOC = "agent_registry"

    def __init__(self, project: str, location: str = "us-central1"):
        self.project = project  # hub project where the cc-* service accounts live
        self.location = location
        self._sa_emails: set[str] | None = None

    def _token(self) -> str:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    def _live_sa_emails(self) -> set[str]:
        """Set of service-account emails that ACTUALLY exist in the hub project (one IAM
        list call, cached). Empty set on any error → agents read as unverified, never crash."""
        if self._sa_emails is not None:
            return self._sa_emails
        emails: set[str] = set()
        try:
            import json as _json
            import urllib.request
            url = f"https://iam.googleapis.com/v1/projects/{self.project}/serviceAccounts?pageSize=100"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token()}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                for a in _json.load(r).get("accounts", []):
                    emails.add(a.get("email", ""))
        except Exception:
            pass
        self._sa_emails = emails
        return emails

    async def publish(self, spec: AgentSpec) -> None:
        import dataclasses

        from agents.store import load_state, save_state
        reg = load_state(self._DOC, {}) or {}
        verified = (not spec.identity_sa) or (
            await asyncio.to_thread(lambda: spec.identity_sa in self._live_sa_emails()))
        entry = dataclasses.asdict(spec)
        entry["identity_verified"] = bool(verified)
        reg[spec.name] = entry
        save_state(self._DOC, reg)

    async def discover(self, department: str | None = None) -> list[AgentSpec]:
        from agents.store import load_state
        reg = load_state(self._DOC, {}) or {}
        out: list[AgentSpec] = []
        for e in reg.values():
            if department and department not in e.get("departments", []):
                continue
            out.append(AgentSpec(
                name=e["name"], version=e.get("version", ""), description=e.get("description", ""),
                departments=e.get("departments", []), capabilities=e.get("capabilities", []),
                identity_sa=e.get("identity_sa", "")))
        return out


class WorkloadIdentityAdapter(IdentityPort):
    """IAM / Workload Identity — zero-trust, least-privilege per agent."""

    def __init__(self, project: str):
        self.project = project

    async def token_for(self, agent_id: str, scopes: list[str]) -> str:
        # TODO(D8): impersonate the agent's dedicated SA; mint short-lived token.
        raise NotImplementedError


class AuditLogClassifierAdapter(ResourceClassifierPort):
    """Classify management source + attribution using Asset Inventory + Audit Logs.

    This is what makes the ClickOps / manually-created case actionable: even with
    no IaC owner, Cloud Audit Logs reveal WHO created a resource and WHEN.

    Classification signals (best-effort, in priority order):
      1. Infrastructure Manager / Config Connector annotations -> IaC-managed
      2. `managed_by` label (terraform | pulumi | manual-clickops | ...)
      3. Terraform state read (if access granted) -> confirms TF ownership
      4. none of the above -> UNMANAGED
    """

    _LABEL_TO_SOURCE = {
        "terraform": ManagementSource.TERRAFORM,
        "pulumi": ManagementSource.PULUMI,
        "cloudformation": ManagementSource.CLOUDFORMATION,
        "config-connector": ManagementSource.CONFIG_CONNECTOR,
        "manual-clickops": ManagementSource.UNMANAGED,
    }

    def __init__(self, project: str):
        self.project = project
        # TODO(D5): init cloud-asset + cloud-logging clients (read-only).

    async def classify(self, resource: str) -> Attribution:
        # TODO(D5): Asset Inventory getResource -> labels/annotations for source.
        labels: dict[str, str] = {}
        source = self._LABEL_TO_SOURCE.get(
            labels.get("managed_by", ""), ManagementSource.UNKNOWN
        )

        # TODO(D5): query Cloud Audit Logs for the *.insert / create event on this
        # resource -> principalEmail + timestamp; and latest usage event.
        created_by: str | None = None
        created_at: str | None = None
        last_activity: str | None = None

        # No IaC signal at all => treat as manually created (ClickOps).
        if source == ManagementSource.UNKNOWN and not labels:
            source = ManagementSource.UNMANAGED

        principal_type = self._principal_type(created_by)
        triggering_entity = created_by
        confidence = "high" if principal_type == "user" else "low"

        # SERVICE-ACCOUNT MASKING FIX: a generic SA is not a real owner. Traverse
        # the assumption chain to find the human/pipeline that actually triggered it.
        if principal_type == "service_account":
            triggering_entity, confidence = await self._resolve_assumption_chain(
                created_by, created_at
            )

        return Attribution(
            created_by=created_by,
            created_at=created_at,
            last_activity=last_activity,
            source=source,
            principal_type=principal_type,
            triggering_entity=triggering_entity,
            attribution_confidence=confidence,
        )

    @staticmethod
    def _principal_type(principal: str | None) -> str:
        if not principal:
            return "unknown"
        return "service_account" if ".gserviceaccount.com" in principal else "user"

    async def _resolve_assumption_chain(
        self, sa_email: str | None, at: str | None
    ) -> tuple[str | None, str]:
        """Find the real actor behind a service account.

        Correlate the creation timestamp with:
          - Workload Identity Federation logs (external IdP subject: GitHub repo, etc.)
          - CI/CD trigger logs (GitHub Actions / Cloud Build build id -> commit -> author)
          - serviceAccounts.generateAccessToken / impersonation audit events
        Returns (best-effort real actor, confidence). Falls back to the SA itself.
        """
        # TODO(D5): implement correlation; requires read access to WIF + CI logs.
        return sa_email, "low"


class OtelObservabilityAdapter(ObservabilityPort):
    """OpenTelemetry -> Cloud Trace + Cloud Logging (immutable audit).

    Audit records carry the SAME tamper-evident hash chain as the local file trail, so
    integrity is verifiable regardless of sink. Cloud Logging is append-only/immutable
    server-side; the chain adds cross-entry integrity a viewer can independently check.
    Falls back to the local file trail if the Cloud Logging client is unavailable.
    """

    def __init__(self, service_name: str = "cloudcap", project: str | None = None,
                 log_name: str = "cloudcap-audit"):
        self.service_name = service_name
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.log_name = log_name
        self._logger = None
        self._seq = 0
        self._prev = "0" * 64
        from agents.audit import FileAuditObservability
        self._fallback = FileAuditObservability()  # local hash-chained trail

    def _logger_lazy(self):
        if self._logger is None:
            from google.cloud import logging as gcl  # google-cloud-logging
            client = gcl.Client(project=self.project)
            self._logger = client.logger(self.log_name)
        return self._logger

    @contextmanager
    def span(self, name: str, attrs: dict[str, Any] | None = None):
        # TODO(D10): start_as_current_span(name) via OTel -> Cloud Trace exporter.
        yield None

    def audit(self, agent_id: str, action: str, detail: dict[str, Any]) -> None:
        from agents.audit import _chain_hash, _now
        self._seq += 1
        payload = {"seq": self._seq, "ts": _now(), "agent": agent_id,
                   "action": action, "detail": detail, "prev": self._prev}
        digest = _chain_hash(self._prev, payload)
        record = {**payload, "hash": digest}
        self._prev = digest
        try:
            self._logger_lazy().log_struct(record, severity="NOTICE",
                                           labels={"service": self.service_name, "agent": agent_id})
        except Exception:
            self._fallback.audit(agent_id, action, detail)  # never lose an audit record


# --- REASONER: Gemini on Vertex AI -----------------------------------------
def _slim(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the fields the model needs to rank/explain — keeps prompts small and
    avoids leaking full internal state to the LLM."""
    out = []
    for f in findings:
        md = f.get("metadata", {}) or {}
        ctrls = md.get("controls") or {}
        out.append({
            "fingerprint": f.get("fingerprint", ""),
            "category": f.get("category"),
            "severity": str(f.get("severity")),
            "resource": f.get("resource"),
            "title": f.get("title"),
            "est_monthly_savings_usd": f.get("est_monthly_savings_usd", 0),
            "management_source": md.get("management_source"),
            "ownership_status": md.get("ownership_status"),
            "control": ctrls.get("name"),
        })
    return out


_PRIORITIZE_SYS = (
    "You are CloudCap's governance orchestrator on Google GEAP (Gemini on Vertex AI). "
    "You are given DETERMINISTIC findings from read-only scanner tools. You must NOT "
    "invent, merge, or drop findings, and you must NOT propose autonomous action — "
    "remediation is human-gated. Rank every finding by governance urgency: severity "
    "first, then unmanaged/ClickOps resources (no IaC owner), then dollars at stake, "
    "then compliance impact. Return STRICT JSON: a list of "
    '{"fingerprint": str, "rank": int, "rationale": str}, rank 1 = act first, one '
    "entry per input finding, no prose outside the JSON."
)


class GeminiReasoner(ReasonerPort):
    """Real Gemini (Vertex AI) reasoning over the deterministic findings.

    Selected when CLOUDCAP_GEMINI is truthy AND the google-genai SDK + ADC creds are
    available. On any SDK/parse error it degrades to the deterministic MockReasoner —
    the security path never depends on the model being reachable.
    """

    def __init__(self, project: str, location: str = "us-central1", model: str | None = None):
        # The Vertex AI project comes from the GCP env, NOT the scan-target project
        # (that's a governance scope, not necessarily where Vertex runs).
        self.project = (os.environ.get("GOOGLE_CLOUD_PROJECT")
                        or os.environ.get("CLOUDCAP_GCP_PROJECT") or project)
        # Gemini's endpoint location is independent of the app/scan region. The 3.5+
        # models are served from the `global` endpoint (not us-central1), so default there;
        # override with CLOUDCAP_GEMINI_LOCATION if a regional model is ever used.
        self.location = os.environ.get("CLOUDCAP_GEMINI_LOCATION") or "global"
        self.model = model or os.environ.get("CLOUDCAP_GEMINI_MODEL", "gemini-3.7-flash")
        self._client = None
        from agents.adapters.local_mock import MockReasoner
        self._fallback = MockReasoner()

    def _client_lazy(self):
        if self._client is None:
            from google import genai  # google-genai SDK (Vertex AI mode)
            self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    async def _generate(self, system: str, prompt: str, json_out: bool) -> str:
        from google.genai import types
        client = self._client_lazy()
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            response_mime_type="application/json" if json_out else "text/plain",
        )
        resp = await client.aio.models.generate_content(model=self.model, contents=prompt, config=cfg)
        return resp.text or ""

    async def prioritize(self, findings: list[dict[str, Any]], context: str = "") -> list[Ranking]:
        if not findings:
            return []
        try:
            prompt = f"context: {context}\nfindings:\n{json.dumps(_slim(findings), indent=2)}"
            data = json.loads(await self._generate(_PRIORITIZE_SYS, prompt, json_out=True))
            valid = {f.get("fingerprint", "") for f in findings}
            out = [Ranking(fingerprint=r["fingerprint"], rank=int(r["rank"]),
                           rationale=str(r.get("rationale", "")))
                   for r in data if r.get("fingerprint") in valid]
            if len(out) == len(findings):   # model must cover every finding, else fall back
                return out
        except Exception:
            pass
        return await self._fallback.prioritize(findings, context)

    async def explain(self, finding: dict[str, Any], proof: dict[str, Any] | None = None) -> str:
        try:
            sys = ("You explain a single cloud governance finding to a human reviewer in ONE "
                   "paragraph: why it matters, the evidence, and the recommended human-approved "
                   "fix. No autonomous action. Plain prose, no JSON.")
            prompt = json.dumps({"finding": _slim([finding])[0], "proof": proof or {}}, indent=2)
            text = (await self._generate(sys, prompt, json_out=False)).strip()
            if text:
                return text
        except Exception:
            pass
        return await self._fallback.explain(finding, proof)

    async def summarize(self, findings: list[dict[str, Any]], context: str = "") -> str:
        try:
            sys = ("You are a governance orchestrator. In 2-3 sentences summarize this scan for "
                   "a FinOps/Security lead: totals, dollars recoverable, unmanaged/ClickOps risk, "
                   "and what to act on first. Plain prose, no JSON.")
            prompt = f"context: {context}\nfindings:\n{json.dumps(_slim(findings), indent=2)}"
            text = (await self._generate(sys, prompt, json_out=False)).strip()
            if text:
                return text
        except Exception:
            pass
        return await self._fallback.summarize(findings, context)
