"""Finding history & lifecycle — keeps ALL findings, detects resolutions, no duplicates.

Keyed by the STABLE fingerprint, so the same issue on the same resource is ONE record
across time. Lifecycle:

    (new) OPEN ──accept──> SUPPRESSED ──ttl expiry (still seen)──> OPEN
      │                        │
      │ not seen next scan     │ not seen next scan
      ▼                        ▼
    RESOLVED  ◄────────────────┘        RESOLVED ──seen again──> REOPENED (reopen_count++)

Resolution = a previously-known fingerprint that a later scan no longer reports (the
resource was rightsized / relabelled / terminated). A recurrence reuses the SAME record
(REOPENED) — never a duplicate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from agents.store import load_state, save_state


@dataclass
class FindingRecord:
    fingerprint: str
    category: str
    resource: str
    title: str
    first_seen: str
    last_seen: str
    state: str                       # open | suppressed | resolved | reopened
    occurrences: int = 1
    reopen_count: int = 0
    max_savings_usd: float = 0.0
    accepted: bool = False
    resolved_at: str | None = None
    resolution: str | None = None


@dataclass
class ReconcileResult:
    new: list[str] = field(default_factory=list)
    recurring: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)  # {fingerprint,resource,title,saved}


class FindingHistory:
    def __init__(self, path: str = "eval/findings_history.json") -> None:
        self.path = path
        self.records: dict[str, FindingRecord] = {}
        try:
            for d in load_state(path, []):
                self.records[d["fingerprint"]] = FindingRecord(**d)
        except (TypeError, KeyError):
            pass

    def get(self, fingerprint: str) -> FindingRecord | None:
        return self.records.get(fingerprint)

    def all(self) -> list[FindingRecord]:
        return list(self.records.values())

    def by_state(self, state: str) -> list[FindingRecord]:
        return [r for r in self.records.values() if r.state == state]

    def reconcile(self, open_findings: list[dict], accepted: list[dict], scan_ts: str) -> ReconcileResult:
        """Advance the lifecycle for one scan. `open_findings` + `accepted` = all detected."""
        res = ReconcileResult()
        detected: dict[str, tuple[dict, bool]] = {}
        for f in open_findings:
            detected[f["fingerprint"]] = (f, False)
        for s in accepted:  # accepted findings are still DETECTED (not resolved), just suppressed
            detected[s["fingerprint"]] = (s, True)

        for fp, (f, is_accepted) in detected.items():
            savings = float(f.get("est_monthly_savings_usd", 0) or 0)
            rec = self.records.get(fp)
            if rec is None:
                self.records[fp] = FindingRecord(
                    fingerprint=fp, category=f.get("category", ""), resource=f["resource"],
                    title=f.get("title", ""), first_seen=scan_ts, last_seen=scan_ts,
                    state="suppressed" if is_accepted else "open",
                    max_savings_usd=savings, accepted=is_accepted)
                res.new.append(fp)
                continue
            was_resolved = rec.state == "resolved"
            rec.last_seen = scan_ts
            rec.occurrences += 1
            rec.max_savings_usd = max(rec.max_savings_usd, savings)
            rec.accepted = is_accepted
            rec.resolved_at = None
            rec.resolution = None
            if was_resolved:
                rec.reopen_count += 1
                rec.state = "suppressed" if is_accepted else "reopened"
                res.reopened.append(fp)
            else:
                rec.state = "suppressed" if is_accepted else "open"
                res.recurring.append(fp)

        # Resolutions: known & previously-active fingerprints not detected this scan.
        for fp, rec in self.records.items():
            if fp not in detected and rec.state in ("open", "suppressed", "reopened"):
                rec.state = "resolved"
                rec.resolved_at = scan_ts
                rec.resolution = "auto: no longer detected"
                res.resolved.append({"fingerprint": fp, "resource": rec.resource,
                                     "title": rec.title, "saved": rec.max_savings_usd})

        self._save()
        return res

    def _save(self) -> None:
        save_state(self.path, [asdict(r) for r in self.records.values()])
