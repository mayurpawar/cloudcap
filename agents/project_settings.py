"""Per-project delivery settings — captured during onboarding / on the Projects page.

Per project: an optional source-code repo (where PRs are proposed) and a list of typed
notification channels (Slack webhook, email, Teams, PagerDuty, generic webhook). Empty
repo → no PR target; empty channels → the finding is still detected (report-only).
Local: eval/project_settings.json; deployed: Firestore.

Stored shape: {"<project>": {"repo": str, "channels": [{"type": str, "value": str}]}}.
Back-compat: an older {"repo","channel"} record is read as a single channel.
"""

from __future__ import annotations

from agents.store import load_state, save_state

# Channel types offered per project. `validate` drives client-side input validation.
CHANNEL_TYPES = [
    {"key": "slack", "label": "Slack", "field": "Incoming webhook URL",
     "placeholder": "https://hooks.slack.com/services/...", "validate": "url"},
    {"key": "email", "label": "Email", "field": "Address",
     "placeholder": "secops@yourco.com", "validate": "email"},
    {"key": "teams", "label": "MS Teams", "field": "Incoming webhook URL",
     "placeholder": "https://outlook.office.com/webhook/...", "validate": "url"},
    {"key": "pagerduty", "label": "PagerDuty", "field": "Integration (routing) key",
     "placeholder": "R0ABCDEF...", "validate": "text"},
    {"key": "webhook", "label": "Webhook", "field": "POST URL",
     "placeholder": "https://example.com/hook", "validate": "url"},
]
CHANNEL_KEYS = [c["key"] for c in CHANNEL_TYPES]


class ProjectSettings:
    def __init__(self, path: str = "eval/project_settings.json") -> None:
        self.path = path
        self.data: dict[str, dict] = load_state(path, {})

    def get(self, project: str) -> dict:
        raw = self.data.get(project) or {}
        channels = raw.get("channels")
        if channels is None:  # migrate legacy single "channel" string
            legacy = raw.get("channel", "")
            channels = [{"type": "slack" if legacy.startswith("http") else "email",
                         "value": legacy}] if legacy else []
        return {"repo": raw.get("repo", ""), "channels": channels}

    def repo(self, project: str) -> str:
        return self.get(project).get("repo", "")

    def channels(self, project: str) -> list[dict]:
        return self.get(project).get("channels", [])

    def channel(self, project: str) -> str:
        """Back-compat: first channel value (empty if none)."""
        ch = self.channels(project)
        return ch[0]["value"] if ch else ""

    def set(self, project: str, repo: str = "", channels: list[dict] | None = None) -> None:
        clean = [{"type": c["type"], "value": c["value"].strip()}
                 for c in (channels or []) if c.get("value", "").strip()]
        self.data[project] = {"repo": (repo or "").strip(), "channels": clean}
        self._save()

    def configured(self, project: str) -> bool:
        s = self.get(project)
        return bool(s["repo"]) and bool(s["channels"])

    def all(self) -> dict[str, dict]:
        return {p: self.get(p) for p in self.data}

    def _save(self) -> None:
        save_state(self.path, self.data)
