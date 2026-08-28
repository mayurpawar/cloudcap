"""Slack integration — the 'Slack' action posts a finding to a project's Incoming Webhook.

Per-project channels live in ProjectSettings (type='slack', value=the webhook URL, added
on the Projects page / during onboarding). When a project's action policy enables Slack,
each delivered finding POSTs a formatted Block Kit message to every Slack webhook bound to
that project. Best-effort: a missing/bad webhook is skipped, never fatal. The webhook URL
is channel-scoped config (not a broad secret) and lives in the state store, not the repo.
"""

from __future__ import annotations

import json
import urllib.request

from agents.project_settings import ProjectSettings

_SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


def _payload(finding: dict, result: dict | None) -> dict:
    sev = str(finding.get("severity", "")).lower()
    icon = _SEV_ICON.get(sev, "⚪")
    md = finding.get("metadata", {}) or {}
    proj = md.get("project", "")
    res = result or {}
    url = res.get("url", "")
    status = res.get("status", "")
    if url:
        action = f"*Pull Request opened:* <{url}|review &amp; merge>"
    elif status == "change_frozen":
        action = "*Change-freeze active* — detected only; no automated change proposed."
    elif status == "advisory":
        action = "*Proposed fix* shown in the hub for human review."
    else:
        action = "Reported for review in the CloudCap hub."
    return {
        # plain-text fallback used for the notification / unfurl preview
        "text": f"CloudCap {sev.upper()}: {finding.get('title','')}",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"{icon} *CloudCap — {sev.upper()} finding*\n*{finding.get('title','')}*"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Resource:*\n`{finding.get('resource','')}`"},
                {"type": "mrkdwn", "text": f"*Project:*\n`{proj}`"}]},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": (finding.get("detail", "") or "")[:280]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": action}},
            {"type": "context", "elements": [{"type": "mrkdwn",
             "text": "Sent automatically by CloudCap · read-only, human-gated"}]},
        ],
    }


def _post(webhook: str, payload: dict) -> bool:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(webhook, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status in (200, 204)


def notify(finding: dict, project: str, result: dict | None = None) -> dict:
    """POST `finding` to every Slack webhook bound to `project`. Best-effort; returns a
    result dict ({status, sent}). Never raises."""
    hooks = [c["value"] for c in ProjectSettings().channels(project)
             if c.get("type") == "slack" and str(c.get("value", "")).startswith("http")]
    if not hooks:
        return {"status": "no_slack_channel", "sent": 0}
    payload = _payload(finding, result)
    sent = 0
    for h in hooks:
        try:
            if _post(h, payload):
                sent += 1
        except Exception:
            pass
    return {"status": "slack_sent" if sent else "slack_error", "sent": sent}
