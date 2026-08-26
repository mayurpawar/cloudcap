"""Scan scope (Sources) — which projects/folders CloudCap actively scans.

Two layers:
  - HARD scope (IAM, via Terraform): what the scanner SAs *can* read (org/folder).
  - SOFT scope (this file): of those, which projects you *choose* to scan.

The org tree here is a mock fixture; in live mode it comes from Cloud Resource
Manager (folders + projects the hub is granted to see).
"""

from __future__ import annotations

from agents.store import load_state, save_state

ORG_TREE = {
    "org": "acme.com",
    "org_id": "123456789012",
    "folders": [
        {"name": "Production", "id": "folders/1001",
         "projects": ["prod-web", "prod-data", "prod-payments", "prod-api"]},
        {"name": "Non-Production", "id": "folders/1002",
         "projects": ["staging", "sandbox", "qa-automation"]},
        {"name": "Data Science", "id": "folders/1003",
         "projects": ["ml-training", "ml-serving", "analytics-lake"]},
        {"name": "Sandbox / Demo", "id": "folders/1004", "projects": ["demo-proj"]},
    ],
}


def folder_of(project: str) -> str:
    from agents.discovery import discover_projects
    for p in discover_projects():
        if p["id"] == project:
            return p["folder"]
    return "—"


def all_projects() -> list[str]:
    # Discovered projects (live via Cloud Resource Manager, or the mock org tree).
    from agents.discovery import discovered_ids
    return discovered_ids()


class SourcesConfig:
    def __init__(self, path: str = "eval/sources_state.json") -> None:
        self.path = path
        raw = load_state(path, None)
        # A saved doc (even {"selected": []}) is honored; NO doc → default all in scope.
        self._selected = set(raw.get("selected", [])) if isinstance(raw, dict) else set(all_projects())

    def selected(self) -> set[str]:
        return set(self._selected)

    def is_selected(self, project: str) -> bool:
        return project in self._selected

    def set_selected(self, projects) -> None:
        self._selected = {p for p in projects if p in all_projects()}
        self._save()

    def _save(self) -> None:
        save_state(self.path, {"selected": sorted(self._selected)})
