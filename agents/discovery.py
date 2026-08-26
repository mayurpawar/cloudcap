"""Project discovery — what the hub can actually see.

Live (deployed): Cloud Resource Manager lists the projects the runtime SA has access to
(grant it Browser at the org/folder to cover the whole estate). Local/mock: the demo org
tree. Cached per process; the "Discover" action forces a refresh. Selected by
CLOUDCAP_DISCOVERY = mock (default) | live, set to live by the installer.
"""

from __future__ import annotations

import os

_cache: list[dict] | None = None


def _mock() -> list[dict]:
    from agents.sources import ORG_TREE
    return [{"id": p, "name": p, "folder": f["name"]}
            for f in ORG_TREE["folders"] for p in f["projects"]]


def _live() -> list[dict]:
    from google.cloud import resourcemanager_v3
    client = resourcemanager_v3.ProjectsClient()
    out: list[dict] = []
    # search_projects() returns the ACTIVE projects the caller can access.
    for p in client.search_projects():
        state = getattr(getattr(p, "state", None), "name", "ACTIVE")
        if state and state != "ACTIVE":
            continue
        out.append({
            "id": p.project_id,
            "name": p.display_name or p.project_id,
            "folder": p.parent or "—",  # e.g. "folders/123" / "organizations/456"
        })
    return out


def discover_projects(refresh: bool = False) -> list[dict]:
    """[{id, name, folder}] the hub can govern. Cached; refresh re-queries the source."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if os.environ.get("CLOUDCAP_DISCOVERY", "mock").lower() == "live":
        try:
            _cache = _live()
            return _cache
        except Exception:
            pass  # no perms / lib missing → fall back so the UI still works
    _cache = _mock()
    return _cache


def discovered_ids(refresh: bool = False) -> list[str]:
    return [p["id"] for p in discover_projects(refresh)]
