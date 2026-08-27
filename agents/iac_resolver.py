"""IaC ownership resolver — which Terraform state/repo manages a GCP resource.

THE MECHANISM (why this is correct):
  Terraform *state* is the source of truth. Each state maps a concrete provider
  resource id -> a `resource` address. We register every known IaC source
  (repo + state backend) and build a reverse index: resource_id -> [owners].

  Lookup outcomes:
    - exactly one owner  -> MANAGED   (open PR against that repo)
    - zero owners        -> UNMANAGED (ClickOps -> codify-then-PR)
    - multiple owners    -> CONFLICT  (drift/overlap -> human triage, never auto-PR)

  State (by real id) beats path/label heuristics: it survives modules, for_each,
  and Terragrunt nesting, and distinguishes a managed `resource` from a `data` read.

Live mode reads real state: GCS backends (`terraform state pull` / object read),
Terraform Cloud/Enterprise workspaces (API), or Config Connector/Infra Manager.
Mock mode uses the fixture states below.
"""

from __future__ import annotations

import asyncio
import json

from agents.ports.interfaces import Ownership, OwnershipStatus, ResourceOwnershipPort

# Fixture Terraform states: several repos, one org — including an intentional
# CONFLICT (cc-public-bucket claimed by two states) and an UNMANAGED resource
# (cc-manual-orphan-vm is in no state → ClickOps).
MOCK_STATES = [
    {"repo": "acme/infra-web", "state": "gs://acme-tfstate/web.tfstate", "resources": [
        {"address": "google_compute_instance.web", "id": "cc-idle-oversized-vm"},
        {"address": "google_storage_bucket.assets", "id": "cc-public-bucket"},
    ]},
    {"repo": "acme/infra-data", "state": "gs://acme-tfstate/data.tfstate", "resources": [
        {"address": "google_sql_database_instance.main", "id": "cc-oversized-sql"},
        {"address": "google_service_account.app", "id": "cc-over-privileged"},
    ]},
    {"repo": "acme/platform", "state": "gs://acme-tfstate/platform.tfstate", "resources": [
        {"address": "google_compute_disk.cache", "id": "cc-orphan-disk"},
    ]},
    # Legacy repo ALSO claims the bucket -> CONFLICT with infra-web.
    {"repo": "acme/legacy-infra", "state": "gs://acme-tfstate/legacy.tfstate", "resources": [
        {"address": "google_storage_bucket.public_legacy", "id": "cc-public-bucket"},
    ]},
]


class MockStateIndexResolver(ResourceOwnershipPort):
    def __init__(self, states: list[dict] | None = None) -> None:
        self.index: dict[str, list[dict]] = {}
        for s in (states if states is not None else MOCK_STATES):
            for r in s["resources"]:
                self.index.setdefault(r["id"], []).append(
                    {"repo": s["repo"], "state": s["state"], "address": r["address"]})

    async def resolve(self, resource_id: str) -> Ownership:
        hits: list[dict] = []
        for rid, owners in self.index.items():
            if rid == resource_id or rid in resource_id or resource_id in rid:
                hits.extend(owners)

        if not hits:
            return Ownership(resource_id, OwnershipStatus.UNMANAGED.value)

        repos = {h["repo"] for h in hits}
        if len(repos) > 1:
            return Ownership(resource_id, OwnershipStatus.CONFLICT.value, candidates=hits)

        h = hits[0]
        return Ownership(resource_id, OwnershipStatus.MANAGED.value,
                         repo=h["repo"], state=h["state"], tf_address=h["address"])


class LiveStateIndexResolver(ResourceOwnershipPort):
    """Live IaC ownership by indexing REAL Terraform state from GCS.

    State backends are registered via CLOUDCAP_TFSTATE_SOURCES:
        gs://bucket/chaos-env.tfstate|owner/repo[,gs://.../other.tfstate|owner/other]
    Each state's *managed* resources are indexed by their real GCP id, name, and the
    basename of the id. A finding's resource then resolves to MANAGED (with the owning
    repo + terraform address) or UNMANAGED — a resource in NO indexed state is genuine
    ClickOps (e.g. a VM created imperatively via gcloud, never a first-class TF resource).

    Read-only (storage.objectViewer). Best-effort: any read/parse error → the resource
    resolves UNMANAGED rather than crashing a scan. State (by real id) beats label/path
    heuristics — it survives modules, for_each, and Terragrunt nesting."""

    def __init__(self, sources: str | None = None) -> None:
        import os
        self._raw = sources if sources is not None else os.environ.get("CLOUDCAP_TFSTATE_SOURCES", "")
        self._index: dict[str, dict] | None = None

    @staticmethod
    def _read_gcs_json(uri: str) -> dict:
        from google.cloud import storage
        bucket, _, obj = uri[len("gs://"):].partition("/")
        blob = storage.Client().bucket(bucket).blob(obj)
        return json.loads(blob.download_as_text())

    def _build_index(self) -> dict[str, dict]:
        idx: dict[str, dict] = {}
        for part in self._raw.split(","):
            part = part.strip()
            if "|" not in part or not part.startswith("gs://"):
                continue
            uri, repo = (x.strip() for x in part.split("|", 1))
            try:
                data = self._read_gcs_json(uri)
            except Exception:
                continue
            for r in data.get("resources", []):
                if r.get("mode") != "managed":
                    continue
                addr = f"{r.get('type')}.{r.get('name')}"
                for inst in r.get("instances", []):
                    a = inst.get("attributes", {}) or {}
                    rid = a.get("id") if isinstance(a.get("id"), str) else None
                    keys = {a.get("name"), rid, (rid.rsplit("/", 1)[-1] if rid else None)}
                    for k in keys:
                        if k and isinstance(k, str):
                            idx.setdefault(k, {"repo": repo, "state": uri, "address": addr})
        return idx

    async def resolve(self, resource_id: str) -> Ownership:
        if self._index is None:
            self._index = await asyncio.to_thread(self._build_index)
        hit = self._index.get(resource_id) or self._index.get(resource_id.rsplit("/", 1)[-1])
        if hit:
            return Ownership(resource_id, OwnershipStatus.MANAGED.value,
                             repo=hit["repo"], state=hit["state"], tf_address=hit["address"])
        return Ownership(resource_id, OwnershipStatus.UNMANAGED.value)
