"""Change-freeze windows — a per-project change-management control.

During a freeze (e.g. a live service with frozen code until a release date), CloudCap
must NEVER propose changes: no PRs, no branches. It falls back to detect + report only
(Issue/Slack), and a human can accept findings as exceptions with a TTL. The freeze
auto-lifts on its `until` date.

This is the operational half of the change-management control CloudCap already maps to
(SOC 2 CC8.1, ISO 27001 A.12.1.2): respecting a freeze IS demonstrating that control.

State: eval/freezes.json → { "<project>": {"until": "YYYY-MM-DD", "reason": "..."} }.
An entry with no `until` is an indefinite freeze until cleared.
"""

from __future__ import annotations

from datetime import date

from agents.store import load_state, save_state


class FreezeStore:
    def __init__(self, path: str = "eval/freezes.json") -> None:
        self.path = path
        self.freezes: dict[str, dict] = load_state(path, {})

    def get(self, project: str) -> dict | None:
        return self.freezes.get(project)

    def is_frozen(self, project: str, today: date | None = None) -> bool:
        fz = self.freezes.get(project)
        if not fz:
            return False
        until = fz.get("until")
        if not until:
            return True  # indefinite freeze
        today = today or date.today()
        return today.isoformat() <= until  # frozen through the `until` date (inclusive)

    def active(self, project: str, today: date | None = None) -> dict | None:
        """The freeze record IF currently in effect for this project, else None."""
        return self.get(project) if self.is_frozen(project, today) else None

    def set(self, project: str, until: str | None, reason: str = "") -> None:
        self.freezes[project] = {"until": until, "reason": reason}
        self._save()

    def clear(self, project: str) -> None:
        self.freezes.pop(project, None)
        self._save()

    def _save(self) -> None:
        save_state(self.path, self.freezes)
