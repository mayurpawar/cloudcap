"""Integrations & health checks — endpoints + live ✓/✗ status.

Each integration has an endpoint config (tokens → Secret Manager in live) and a
`check()` that probes it read-only. In mock mode the probe passes when required
fields are present and fails (with a reason) when they are missing — so the health
tick is meaningful and config-driven. In live mode these become real probes:
GitHub GET repo + draft-PR dry run, Jira GET /myself, Slack auth.test, etc.
"""

from __future__ import annotations

from agents.store import load_state, save_state
import re


def _defaults() -> list[dict]:
    return [
        {"id": "gcp", "name": "GCP scan scope", "kind": "cloud", "enabled": True,
         "fields": [{"key": "org", "label": "Org / folder ID"}], "required": ["org"],
         "config": {"org": "folders/1001"}, "ok_detail": "listed 5 projects in scope",
         "status": "untested", "detail": "", "last_checked": ""},
        {"id": "iac", "name": "IaC state backends", "kind": "iac", "enabled": True,
         "fields": [{"key": "backend", "label": "State backend (GCS / TF Cloud)"}], "required": ["backend"],
         "config": {"backend": "gs://acme-tfstate"}, "ok_detail": "read 4 states, indexed 6 resources",
         "status": "untested", "detail": "", "last_checked": ""},
        {"id": "github", "name": "GitHub (Pull Requests)", "kind": "git", "enabled": True,
         "fields": [{"key": "host", "label": "Host"}, {"key": "org", "label": "Org"},
                    {"key": "token", "label": "Token / App key (→ Secret Manager)", "secret": True}],
         "required": ["host", "org", "token"],
         "config": {"host": "github.com", "org": "acme", "token": ""},
         "ok_detail": "auth ok · draft-PR dry run ok",
         "status": "untested", "detail": "", "last_checked": ""},
        {"id": "jira", "name": "Jira (fallback tickets)", "kind": "ticket", "enabled": False,
         "fields": [{"key": "base_url", "label": "Base URL"}, {"key": "project", "label": "Project key"},
                    {"key": "token", "label": "API token (→ Secret Manager)", "secret": True}],
         "required": ["base_url", "project", "token"],
         "config": {"base_url": "", "project": "", "token": ""}, "ok_detail": "GET /myself ok",
         "status": "untested", "detail": "", "last_checked": ""},
        {"id": "slack", "name": "Slack (notifications)", "kind": "notify", "enabled": False,
         "fields": [{"key": "channel", "label": "Channel"},
                    {"key": "token", "label": "Bot token (→ Secret Manager)", "secret": True}],
         "required": ["channel", "token"],
         "config": {"channel": "#cloudcap-alerts", "token": ""}, "ok_detail": "auth.test ok",
         "status": "untested", "detail": "", "last_checked": ""},
    ]


def run_check(integ: dict) -> tuple[str, str]:
    """Probe an integration. Mock: pass if required fields present, else fail with reason."""
    if not integ.get("enabled"):
        return "disabled", "integration not enabled"
    missing = [k for k in integ.get("required", []) if not integ["config"].get(k)]
    if missing:
        return "fail", "missing: " + ", ".join(missing)
    return "pass", integ.get("ok_detail", "connected")


class IntegrationsStore:
    def __init__(self, path: str = "eval/integrations_state.json") -> None:
        self.path = path
        loaded = load_state(path, None)
        self.items: list[dict] = loaded if isinstance(loaded, list) else _defaults()

    def get(self, iid: str) -> dict | None:
        return next((i for i in self.items if i["id"] == iid), None)

    def add(self, name: str, kind: str, endpoint: str, token: str) -> None:
        iid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "integration"
        base, n = iid, 1
        while self.get(iid):
            iid, n = f"{base}-{n}", n + 1
        self.items.append({
            "id": iid, "name": name, "kind": kind or "custom", "enabled": True,
            "fields": [{"key": "endpoint", "label": "Endpoint / host"},
                       {"key": "token", "label": "Token (→ Secret Manager)", "secret": True}],
            "required": ["endpoint"], "config": {"endpoint": endpoint, "token": token},
            "ok_detail": "reachable", "status": "untested", "detail": "", "last_checked": ""})
        self._save()

    def remove(self, iid: str) -> None:
        self.items = [i for i in self.items if i["id"] != iid]
        self._save()

    def update(self, iid: str, config: dict, enabled: bool) -> None:
        i = self.get(iid)
        if i:
            i["config"].update({k: v for k, v in config.items() if k in i["config"]})
            i["enabled"] = enabled
            self._save()

    def set_result(self, iid: str, status: str, detail: str, ts: str) -> None:
        i = self.get(iid)
        if i:
            i["status"], i["detail"], i["last_checked"] = status, detail, ts
            self._save()

    def _save(self) -> None:
        save_state(self.path, self.items)
