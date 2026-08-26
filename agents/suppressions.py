"""Accept / suppress findings with a TTL — the compliance-exception workflow.

A user reviews a finding, accepts it as expected (e.g. an idle-looking VM that is
actually a required compliance appliance), and suppresses it so it stops being
re-reported. Suppressions carry an expiry so exceptions are periodically re-reviewed
(good governance) — or `forever` when truly permanent.

Keyed by the STABLE finding fingerprint (CC-xxxxxxxx) so it keeps matching the same
issue across scans. Persisted as governance state (Memory Bank pillar).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agents.store import load_state, save_state
from datetime import date, timedelta


@dataclass
class Suppression:
    fingerprint: str
    resource: str
    reason: str
    until: str | None          # ISO "YYYY-MM-DD", or None = forever
    created_by: str
    created_at: str

    def active(self, today: date) -> bool:
        if self.until is None:
            return True
        try:
            return date.fromisoformat(self.until) >= today
        except ValueError:
            return True


def parse_duration(spec: str, today: date) -> str | None:
    """'forever' -> None; 'week'/'1w', 'month'/'1m', 'Nd', or an explicit YYYY-MM-DD."""
    s = spec.strip().lower()
    if s in ("forever", "permanent", "never"):
        return None
    if s in ("week", "1w"):
        return (today + timedelta(weeks=1)).isoformat()
    if s in ("month", "1m"):
        return (today + timedelta(days=30)).isoformat()
    if s.endswith("w") and s[:-1].isdigit():
        return (today + timedelta(weeks=int(s[:-1]))).isoformat()
    if s.endswith("d") and s[:-1].isdigit():
        return (today + timedelta(days=int(s[:-1]))).isoformat()
    date.fromisoformat(s)  # validate explicit date (raises ValueError if malformed)
    return s


class SuppressionStore:
    def __init__(self, path: str = "eval/suppressions.json") -> None:
        self.path = path
        self._items: dict[str, Suppression] = {}
        try:
            for d in load_state(path, []):
                self._items[d["fingerprint"]] = Suppression(**d)
        except (TypeError, KeyError):
            pass

    def add(self, s: Suppression) -> None:
        self._items[s.fingerprint] = s
        self._save()

    def remove(self, fingerprint: str) -> None:
        self._items.pop(fingerprint, None)
        self._save()

    # Retire a suppression because its finding auto-resolved (resource fixed/terminated).
    # The history record keeps the full audit trail; the live exception is cleared so a
    # future recurrence is re-evaluated fresh rather than silently masked.
    retire = remove

    def all(self) -> list[Suppression]:
        return list(self._items.values())

    def active(self, today: date) -> list[Suppression]:
        return [s for s in self._items.values() if s.active(today)]

    def active_fingerprints(self, today: date) -> frozenset[str]:
        return frozenset(s.fingerprint for s in self._items.values() if s.active(today))

    def _save(self) -> None:
        save_state(self.path, [asdict(s) for s in self._items.values()])
