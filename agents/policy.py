"""Per-project action policy — which delivery channels fire for each scope.

A default policy + per-project overrides decide, for findings in a given project,
whether CloudCap opens a PR, files an issue/ticket, and/or sends a Slack notice.
Example: prod → PR + issue + Slack; sandbox → Slack only; regulated → ticket only.
"""

from __future__ import annotations

from agents.sources import all_projects
from agents.store import load_state, save_state

ACTIONS = ["pr", "issue", "slack"]
DEFAULT = {"pr": True, "issue": False, "slack": True}


class ActionPolicy:
    def __init__(self, path: str = "eval/policy_state.json") -> None:
        self.path = path
        d = load_state(path, {})
        self.default = {**DEFAULT, **d.get("default", {})}
        self.overrides: dict[str, dict] = d.get("overrides", {})

    def channels_for(self, project: str) -> dict:
        return {**self.default, **self.overrides.get(project, {})}

    def save_all(self, default: dict, overrides: dict) -> None:
        self.default = {a: bool(default.get(a)) for a in ACTIONS}
        self.overrides = {p: {a: bool(o.get(a)) for a in ACTIONS} for p, o in overrides.items()}
        save_state(self.path, {"default": self.default, "overrides": self.overrides})
