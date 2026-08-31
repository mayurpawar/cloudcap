"""Cross-request 'a scan is running' flag, so every page/tab can show progress and
disable the scan buttons while a scan is in flight.

Stored via the normal state store (Firestore when deployed, JSON locally). A TTL
auto-expires a stale flag so a crashed scan can never leave the UI stuck-disabled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.store import load_state, save_state

_KEY = "scan_status"
_TTL_SECONDS = 180  # a scan takes ~60s; auto-clear a stuck flag after 3 min (crash safety)


def begin() -> None:
    save_state(_KEY, {"active": True,
                      "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def end() -> None:
    save_state(_KEY, {"active": False})


def status() -> dict:
    """{'active': bool, 'started'?: iso}. A flag older than the TTL reads inactive."""
    s = load_state(_KEY, {}) or {}
    if not s.get("active"):
        return {"active": False}
    try:
        started = datetime.fromisoformat(s["started"])
        if (datetime.now(timezone.utc) - started).total_seconds() > _TTL_SECONDS:
            return {"active": False}  # stale — treat a crashed/abandoned scan as done
    except (KeyError, ValueError, TypeError):
        return {"active": False}
    return {"active": True, "started": s.get("started")}
