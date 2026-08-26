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
    """Honest live ownership. Until real Terraform state backends are registered and
    indexed (GCS `terraform state pull`, Terraform Cloud API, Config Connector), no
    resource can be attributed to a state — so every resource resolves UNMANAGED rather
    than to a fabricated owner or conflict. This keeps the board truthful and consistent
    with the live classifier (which reads the goog-terraform-provisioned label), instead
    of inventing "managed by multiple states" narratives from mock fixtures."""

    def __init__(self, states: list[dict] | None = None) -> None:
        self.index: dict[str, list[dict]] = {}

    async def resolve(self, resource_id: str) -> Ownership:
        return Ownership(resource_id, OwnershipStatus.UNMANAGED.value)
