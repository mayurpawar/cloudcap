"""Read the immutable Cloud Logging audit trail (`cloudcap-audit`) for the UI.

The write side is OtelObservabilityAdapter (agents/adapters/google_geap.py), which appends
hash-chained records {seq, ts, agent, action, detail, prev, hash} to Cloud Logging. This
reads them back so the dashboard can show REAL GCP audit logs (History scan logs, Hub
per-agent logs). Best-effort: any error (no creds / SDK / permission) returns [] so the UI
degrades gracefully. Needs roles/logging.viewer on the caller (owner locally; cc-runtime on
deploy).
"""

from __future__ import annotations

import os


def read_audit(project: str | None = None, agent: str | None = None,
               limit: int = 100) -> list[dict]:
    """Recent audit entries (newest first), optionally filtered to one agent."""
    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return []
    try:
        from google.cloud import logging as gcl
    except Exception:
        return []
    flt = f'logName="projects/{project}/logs/cloudcap-audit"'
    if agent:
        flt += f' AND jsonPayload.agent="{agent}"'
    try:
        client = gcl.Client(project=project)
        order = getattr(gcl, "DESCENDING", "timestamp desc")
        out: list[dict] = []
        for e in client.list_entries(filter_=flt, order_by=order, max_results=limit):
            p = e.payload if isinstance(e.payload, dict) else {}
            ts = p.get("ts") or (e.timestamp.isoformat() if getattr(e, "timestamp", None) else "")
            out.append({
                "ts": ts,
                "agent": p.get("agent", ""),
                "action": p.get("action", ""),
                "detail": p.get("detail", {}),
                "seq": p.get("seq", ""),
                "hash": (p.get("hash", "") or "")[:12],
            })
        return out
    except Exception:
        return []
