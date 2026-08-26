"""Onboarding / first-run state — the setup journey.

Tracks whether the hub has been configured and whether the first scan has run, so a
fresh deploy lands the admin in a guided wizard (discover → scope → policy → channels →
first scan) instead of an empty app. Local: eval/onboarding.json; deployed: Firestore.
"""

from __future__ import annotations

from agents.store import load_state, save_state

_STEPS = ["welcome", "discover", "policy", "integrations", "review"]


class OnboardingState:
    def __init__(self, path: str = "eval/onboarding.json") -> None:
        self.path = path
        self.data = {"completed": False, "step": "welcome", "first_scan_done": False}
        self.data.update(load_state(path, {}))

    @property
    def completed(self) -> bool:
        return bool(self.data.get("completed"))

    @property
    def first_scan_done(self) -> bool:
        return bool(self.data.get("first_scan_done"))

    def set(self, **kw) -> None:
        self.data.update(kw)
        self._save()

    def reset(self) -> None:
        self.data = {"completed": False, "step": "welcome", "first_scan_done": False}
        self._save()

    def _save(self) -> None:
        save_state(self.path, self.data)
