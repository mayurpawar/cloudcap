"""Compliance mapping — turn findings into control pass/fail across frameworks.

Each governance finding maps to a control across CIS GCP Benchmark, SOC 2,
ISO 27001, and PCI DSS. A control FAILS if any open finding maps to it; the posture
is passing/total per framework. This is what elevates CloudCap from a scanner to an
Enterprise Governance / continuous-audit platform.
"""

from __future__ import annotations

FRAMEWORKS = ["CIS GCP", "SOC 2", "ISO 27001", "PCI DSS"]

# Each framework has its OWN control catalog of a different size — so scores differ.
FRAMEWORK_TOTALS = {"CIS GCP": 58, "SOC 2": 64, "ISO 27001": 93, "PCI DSS": 78}

# Control GROUPS within each framework, mirroring how the real standards scope:
# a mandatory baseline (locked, can't be de-selected) plus opt-in groups. The score
# denominator is the sum of ENABLED groups only — CIS profile levels, SOC 2 Trust
# Services Criteria (Security is mandatory), ISO 27001:2022 Annex A themes, PCI SAQ
# scope. Counts per framework sum to FRAMEWORK_TOTALS. locked=True → always in scope.
GROUPS = {
    "CIS GCP": [
        {"key": "l1",   "name": "Level 1 — Baseline",          "count": 32, "locked": True},
        {"key": "l2",   "name": "Level 2 — Hardening",         "count": 26, "locked": False},
    ],
    "SOC 2": [
        {"key": "cc",   "name": "Security / Common Criteria",  "count": 40, "locked": True},
        {"key": "avail","name": "Availability",                "count": 9,  "locked": False},
        {"key": "conf", "name": "Confidentiality",             "count": 8,  "locked": False},
        {"key": "pi",   "name": "Processing Integrity",        "count": 7,  "locked": False},
    ],
    "ISO 27001": [
        {"key": "tech", "name": "Technological (A.8)",         "count": 34, "locked": True},
        {"key": "org",  "name": "Organizational (A.5)",        "count": 37, "locked": False},
        {"key": "people","name": "People (A.6)",               "count": 8,  "locked": False},
        {"key": "phys", "name": "Physical (A.7)",              "count": 14, "locked": False},
    ],
    "PCI DSS": [
        {"key": "core", "name": "Core CDE requirements",       "count": 46, "locked": True},
        {"key": "saqd", "name": "Extended (SAQ-D) scope",      "count": 32, "locked": False},
    ],
}

# Which group a specific control id belongs to. Unlisted ids fall to the framework's
# locked baseline group — so all evaluated controls show by default, and enabling an
# optional group only widens the denominator (broader claimed scope, same failures).
CONTROL_GROUP = {
    "CIS GCP": {"5.1": "l1", "1.5": "l1", "3.6": "l1", "2.1": "l1", "4.7": "l2"},
    "SOC 2": {},
    "ISO 27001": {},
    "PCI DSS": {},
}


def _locked_keys(fw: str) -> set:
    return {g["key"] for g in GROUPS[fw] if g["locked"]}


def group_for(fw: str, cid: str) -> str:
    """Group key owning a control id; defaults to the locked baseline group."""
    m = CONTROL_GROUP.get(fw, {})
    if cid in m:
        return m[cid]
    return next(g["key"] for g in GROUPS[fw] if g["locked"])


def enabled_keys(fw: str, enabled: dict | None) -> set:
    """Enabled group keys for a framework — locked groups are always included."""
    if enabled is None:
        return _locked_keys(fw)
    return set(enabled.get(fw, set())) | _locked_keys(fw)

# rule -> control ids per framework. A rule maps only to the frameworks where a
# relevant control exists (""=not applicable to that framework). Representative IDs;
# verify against current benchmark revisions before an audit.
CONTROLS = {
    "public-exposure":       {"name": "Public data exposure",        "CIS GCP": "5.1", "SOC 2": "CC6.1", "ISO 27001": "A.9.4.1",  "PCI DSS": "1.3"},
    "excessive-privilege":   {"name": "Excessive IAM privilege",     "CIS GCP": "1.5", "SOC 2": "CC6.3", "ISO 27001": "A.9.2.3",  "PCI DSS": "7.1"},
    "hardcoded-secret":      {"name": "Hardcoded secret in source",  "CIS GCP": "",    "SOC 2": "CC6.1", "ISO 27001": "A.9.4.3",  "PCI DSS": "3.5"},
    "open-firewall":         {"name": "Overly permissive firewall",  "CIS GCP": "3.6", "SOC 2": "",      "ISO 27001": "A.13.1.1", "PCI DSS": "1.2"},
    "unencrypted-data":      {"name": "Unencrypted data at rest",    "CIS GCP": "4.7", "SOC 2": "CC6.7", "ISO 27001": "",         "PCI DSS": "3.4"},
    "audit-logging":         {"name": "Audit logging disabled",      "CIS GCP": "2.1", "SOC 2": "CC7.2", "ISO 27001": "A.12.4.1", "PCI DSS": ""},
    "change-mgmt-unmanaged": {"name": "Out-of-band (unmanaged) change", "CIS GCP": "", "SOC 2": "CC8.1", "ISO 27001": "A.12.1.2", "PCI DSS": ""},
    "threat-detection":      {"name": "Threat / injection detection", "CIS GCP": "",  "SOC 2": "CC7.3", "ISO 27001": "A.12.2.1", "PCI DSS": ""},
}


def rule_for(finding: dict) -> str | None:
    """Derive the governance rule for a finding (specific checks before the generic
    unmanaged/change-management fallback)."""
    r = finding.get("resource", "").lower()
    t = finding.get("title", "").lower()
    d = finding.get("detail", "").lower()
    md = finding.get("metadata", {}) or {}

    # Injection/poisoning first — its object name may coincidentally contain "public".
    if "injection" in t or "poison" in t or "model armor" in t:
        return "threat-detection"
    # Hardcoded secret in IaC/source (plaintext credential committed to the repo).
    if "hardcoded secret" in t or "plaintext credential" in (t + d) or md.get("fix_kind") == "redact-secret":
        return "hardcoded-secret"
    if "world-readable" in t or "allusers" in (t + d) or ("public" in r and "bucket" in r):
        return "public-exposure"
    if "roles/owner" in (t + d) or "over-privileged" in r or " owner" in t:
        return "excessive-privilege"
    if "firewall" in r or "0.0.0.0" in (t + d):
        return "open-firewall"
    if "encrypt" in t or "cmek" in t:
        return "unencrypted-data"
    if "audit log" in t or "logging" in t or "audit-logs" in r:
        return "audit-logging"
    if md.get("management_source") == "unmanaged" or md.get("ownership_status") == "unmanaged":
        return "change-mgmt-unmanaged"
    return None


def controls_for(finding: dict) -> dict | None:
    rule = rule_for(finding)
    if not rule:
        return None
    m = CONTROLS[rule]
    return {"rule": rule, "name": m["name"], **{fw: m[fw] for fw in FRAMEWORKS}}


def posture(findings: list[dict], enabled: dict | None = None) -> dict:
    """Per-framework posture. Each framework scores against the sum of its ENABLED
    control groups (locked baseline always in scope) and only the failing controls
    that fall inside those groups — so scores differ per framework AND per scope."""
    failing_rules = {rule_for(f) for f in findings}
    failing_rules.discard(None)
    result = {}
    for fw in FRAMEWORKS:
        en = enabled_keys(fw, enabled)
        total = sum(g["count"] for g in GROUPS[fw] if g["key"] in en)
        failing_ids = []
        for rule in failing_rules:
            cid = CONTROLS.get(rule, {}).get(fw)
            if cid and group_for(fw, cid) in en:
                failing_ids.append(cid)
        failing = len(failing_ids)
        passing = total - failing
        result[fw] = {"total": total, "passing": passing, "failing": failing,
                      "score": passing / total if total else 1.0, "failing_ids": failing_ids,
                      "groups": [{**g, "enabled": g["key"] in en} for g in GROUPS[fw]]}
    return result


def overall_score(post: dict) -> float:
    tot = sum(p["total"] for p in post.values())
    return sum(p["passing"] for p in post.values()) / tot if tot else 1.0


class ScopeConfig:
    """Admin-selected in-scope control groups per framework (locked groups always on).
    Default = baseline only (the locked group of each framework)."""

    def __init__(self, path: str = "eval/compliance_scope.json") -> None:
        from agents.store import load_state
        self.path = path
        raw = load_state(path, None)
        if isinstance(raw, dict) and raw:
            self.enabled: dict = {fw: set(raw.get(fw, [])) for fw in FRAMEWORKS}
        else:
            self.enabled = {fw: set(_locked_keys(fw)) for fw in FRAMEWORKS}
        # locked groups are non-negotiable
        for fw in FRAMEWORKS:
            self.enabled[fw] |= _locked_keys(fw)

    def as_dict(self) -> dict:
        return {fw: set(self.enabled.get(fw, set())) for fw in FRAMEWORKS}

    def toggle(self, fw: str, key: str, on: bool) -> None:
        if fw not in GROUPS or key in _locked_keys(fw):
            return  # can't disable the mandatory baseline
        if not any(g["key"] == key for g in GROUPS[fw]):
            return
        s = set(self.enabled.get(fw, set()))
        (s.add if on else s.discard)(key)
        self.enabled[fw] = s | _locked_keys(fw)
        self._save()

    def _save(self) -> None:
        from agents.store import save_state
        save_state(self.path, {fw: sorted(self.enabled[fw]) for fw in FRAMEWORKS})


def build_report(findings: list[dict], post: dict) -> str:
    lines = ["# CloudCap — Enterprise Governance Audit Report", "",
             "_Continuous compliance evidence generated from the latest scan._", "",
             "## Compliance posture"]
    for fw in FRAMEWORKS:
        p = post[fw]
        lines.append(f"- **{fw}**: {p['passing']}/{p['total']} controls passing "
                     f"({p['score']:.0%}) · failing: {', '.join(p['failing_ids']) or 'none'}")
    lines += ["", "## Findings mapped to controls (evidence)"]
    for f in findings:
        c = controls_for(f)
        if not c:
            continue
        ctrls = " · ".join(f"{fw} {c[fw]}" for fw in FRAMEWORKS if c.get(fw))
        lines.append(f"- `{f['resource']}` — {f.get('title','')}  \n  _{c['name']}_ → {ctrls}")
    return "\n".join(lines) + "\n"
