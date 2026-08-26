"""Per-project governance scope — which checks & frameworks apply to each project.

Lets an operator say: prod-payments → cost + security + PCI DSS + SOC 2;
sandbox → cost only. A default profile + per-project overrides. Findings whose
category is disabled for a project are dropped; compliance posture is computed only
over the frameworks enabled for that project.
"""

from __future__ import annotations

from agents.store import load_state, save_state

from agents.compliance import FRAMEWORKS

DIMENSIONS = ["cost", "security"]          # finding categories (iam grouped under security)
GOV_OPTIONS = DIMENSIONS + FRAMEWORKS      # matrix columns
DEFAULT = {"cost": True, "security": True,
           "CIS GCP": True, "SOC 2": True, "ISO 27001": False, "PCI DSS": False}


class GovernanceConfig:
    def __init__(self, path: str = "eval/governance_state.json") -> None:
        self.path = path
        d = load_state(path, {})
        self.default = {**DEFAULT, **d.get("default", {})}
        self.overrides: dict[str, dict] = d.get("overrides", {})

    def profile_for(self, project: str) -> dict:
        return {**self.default, **self.overrides.get(project, {})}

    def enabled_frameworks(self, project: str) -> list[str]:
        p = self.profile_for(project)
        return [fw for fw in FRAMEWORKS if p.get(fw)]

    def category_enabled(self, project: str, category: str) -> bool:
        p = self.profile_for(project)
        return bool(p.get("cost" if category == "cost" else "security", True))

    def save_all(self, default: dict, overrides: dict) -> None:
        self.default = {o: bool(default.get(o)) for o in GOV_OPTIONS}
        self.overrides = {p: {o: bool(v.get(o)) for o in GOV_OPTIONS} for p, v in overrides.items()}
        save_state(self.path, {"default": self.default, "overrides": self.overrides})
