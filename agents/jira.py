"""JIRA integration — the 'Issue' action files a finding into a configurable JIRA project.

Positioning: point CloudCap at a JIRA project of your choice; each finding becomes an
issue there, labelled for triage. Non-secret config (site URL, email, project key,
optional custom field id for the GCP project) lives in the state store; the API token
comes from JIRA_API_TOKEN (Secret Manager in production) and is never persisted.

Every issue carries standard labels: the GCP project (a custom field if configured, else
a label), the GCP service, the control name, each mapped framework, and the category
(e.g. cost-optimization). Falls back to a local ticket artifact if JIRA isn't configured.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request

from agents.compliance import FRAMEWORKS
from agents.store import load_state, save_state

_JIRA_PATH = "eval/jira_config.json"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


# Best-effort GCP service from the finding text (labels are for filtering, not billing).
_SERVICE_HINTS = [
    ("run", "Cloud Run"), ("bucket", "Cloud Storage"), ("gcs", "Cloud Storage"),
    ("sql", "Cloud SQL"), ("firewall", "VPC Firewall"), ("0.0.0.0", "VPC Firewall"),
    ("disk", "Compute Engine"), ("instance", "Compute Engine"), ("-vm", "Compute Engine"),
    ("ip", "Compute Engine"), ("owner", "IAM"), ("privileg", "IAM"), ("iam", "IAM"),
    ("audit", "Cloud Logging"), ("logging", "Cloud Logging"),
]


def gcp_service(finding: dict) -> str:
    hay = f"{finding.get('resource','')} {finding.get('title','')} {finding.get('detail','')}".lower()
    for k, v in _SERVICE_HINTS:
        if k in hay:
            return v
    return {"cost": "Cost", "security": "Security", "iam": "IAM",
            "compliance": "Compliance"}.get(finding.get("category", ""), "GCP")


class JiraConfig:
    def __init__(self, path: str = _JIRA_PATH) -> None:
        self.path = path
        d = load_state(path, {})
        self.base_url = (d.get("base_url") or os.environ.get("JIRA_BASE_URL", "")).rstrip("/")
        self.email = d.get("email") or os.environ.get("JIRA_EMAIL", "")
        self.project_key = d.get("project_key") or os.environ.get("JIRA_PROJECT_KEY", "")
        self.gcp_field = d.get("gcp_project_field") or os.environ.get("JIRA_GCP_PROJECT_FIELD", "")
        self.token = os.environ.get("JIRA_API_TOKEN", "")  # secret — never stored

    @property
    def configured(self) -> bool:
        """Enough non-secret config to enable the Issue action in the UI."""
        return bool(self.base_url and self.email and self.project_key)

    @property
    def ready(self) -> bool:
        """Can actually POST (token also present)."""
        return self.configured and bool(self.token)

    def save(self, base_url: str, email: str, project_key: str, gcp_field: str = "") -> None:
        save_state(self.path, {
            "base_url": (base_url or "").strip().rstrip("/"),
            "email": (email or "").strip(),
            "project_key": (project_key or "").strip(),
            "gcp_project_field": (gcp_field or "").strip(),
        })


def issue_fields(finding: dict, cfg: JiraConfig) -> dict:
    """Build the JIRA issue `fields` payload with standard labels + optional GCP field."""
    md = finding.get("metadata", {}) or {}
    proj = md.get("project", "")
    ctrls = md.get("controls") or {}
    control = ctrls.get("name", "")
    cat = finding.get("category", "")

    labels = ["cloudcap"]
    if proj and not cfg.gcp_field:
        labels.append("gcp-project-" + _slug(proj))
    labels.append("service-" + _slug(gcp_service(finding)))
    if control:
        labels.append("control-" + _slug(control))
    for fw in FRAMEWORKS:
        if ctrls.get(fw):
            labels.append("framework-" + _slug(fw))
    labels.append("category-" + ("cost-optimization" if cat == "cost" else (_slug(cat) or "governance")))

    sv = finding.get("est_monthly_savings_usd", 0)
    desc = (
        f"Resource: {finding.get('resource','')}\n"
        f"Severity: {finding.get('severity','')}\n"
        f"GCP project: {proj}\n"
        f"Service: {gcp_service(finding)}\n"
        + (f"Control: {control}\n" if control else "")
        + (f"Est. monthly savings: ${sv:,.0f}\n" if sv else "")
        + f"\n{finding.get('detail','')}\n\nRecommended action: {finding.get('recommended_action','')}\n\n"
        "— filed automatically by CloudCap (human-gated; no cloud writes)."
    )
    fields = {
        "project": {"key": cfg.project_key},
        "issuetype": {"name": "Task"},
        "summary": f"[CloudCap] {finding.get('title','')} — {finding.get('resource','')}"[:250],
        "description": desc,
        "labels": labels,
    }
    if cfg.gcp_field and proj:
        fields[cfg.gcp_field] = proj   # custom field for GCP project name
    return fields


def create_issue(finding: dict) -> dict | None:
    """Create a JIRA issue. Returns a result dict, or None if JIRA isn't ready (→ caller
    falls back to a local ticket)."""
    cfg = JiraConfig()
    if not cfg.ready:
        return None
    body = json.dumps({"fields": issue_fields(finding, cfg)}).encode()
    req = urllib.request.Request(cfg.base_url + "/rest/api/2/issue", data=body, method="POST")
    req.add_header("Authorization",
                   "Basic " + base64.b64encode(f"{cfg.email}:{cfg.token}".encode()).decode())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            key = json.loads(r.read()).get("key", "")
            return {"status": "jira_issue", "key": key, "url": f"{cfg.base_url}/browse/{key}"}
    except Exception as exc:  # unreachable / bad creds → caller keeps the local artifact
        return {"status": "jira_error", "error": str(exc)[:200]}
