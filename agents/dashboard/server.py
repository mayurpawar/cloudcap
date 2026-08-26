"""CloudCap — Governance Control Plane (dashboard + onboarding wizard).

The ONLY prerequisite is running the Terraform in a hub project. On first boot the
app shows a SETUP screen (configure git / ticketing / notifications — tokens are
written to Secret Manager by the app), then runs an on-screen PREFLIGHT with live
progress, and only when every check passes does the FIRST SCAN unlock.

    python -m agents.dashboard.server            # http://localhost:8080

Served by the Python stdlib http.server — no build step, no external CDN.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
import urllib.parse
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

from agents.context import build_context
from agents.fleet_runner import run_fleet
from agents.history import FindingHistory
from agents.remediation.pr_channel import remediate
from agents.runcost import estimate as estimate_runcost
from agents.compliance import FRAMEWORKS, build_report as build_audit_report, posture as compliance_posture, rule_for as compliance_rule_for
from agents.governance import GOV_OPTIONS, GovernanceConfig
from agents.integrations import IntegrationsStore, run_check as run_integration_check
from agents.policy import ACTIONS, ActionPolicy
from agents.sources import ORG_TREE, SourcesConfig, all_projects
from agents.run import finding_to_dict
from agents.suppressions import Suppression, SuppressionStore, parse_duration
from eval.score import score as score_fleet

GROUND_TRUTH = "eval/ground_truth.json"
STATE_PATH = "eval/setup_state.json"

SEVERITY = {"critical": "#F43F5E", "high": "#FB923C", "medium": "#FACC15", "low": "#38BDF8"}
KIND = {"rightsize": "#22C55E", "iam-tighten": "#38BDF8", "delete": "#F43F5E", "codify": "#A855F7"}

LOCK = threading.Lock()
STATE = {"phase": "setup", "config": {}, "checks": []}

# Mock auth/session (single-session demo). Real: Google OAuth/IAP + local email/password.
AUTH = {"logged_in": False, "user": "", "role": "operator", "roles": []}
ADMIN_ROUTES = {"/setup/save", "/sources/save", "/integrations/update",
                "/integrations/testall", "/policy/save", "/governance/save"}


def _is_admin() -> bool:
    return AUTH["role"] == "admin"


def _admin_gate():
    """Return (banner_html, disabled_attr) for config controls based on role."""
    if _is_admin():
        return "", ""
    return ('<div class="ro-banner">View-only — switch to the <b>Admin</b> role '
            '(top-right) to edit.</div>', "disabled")


# --- setup state persistence ------------------------------------------------
def _load_state():
    global STATE
    if os.path.exists(STATE_PATH):
        try:
            STATE = json.load(open(STATE_PATH))
        except (ValueError, OSError):
            pass


def _save_state():
    with open(STATE_PATH, "w") as fh:
        json.dump(STATE, fh, indent=2)


def _build_checks(cfg: dict) -> list[dict]:
    checks = [
        ("iam", "Hub IAM & read-only scan scope granted"),
        ("apis", "Required Google Cloud APIs enabled"),
        ("secrets", "Secret Manager reachable (store integration tokens)"),
    ]
    if cfg.get("git_token"):
        checks.append(("git", f"{cfg.get('git_provider','git')} auth + repo access (draft-PR dry run)"))
    if cfg.get("enable_jira"):
        checks.append(("jira", "Jira reachable + project writable"))
    if cfg.get("enable_slack"):
        checks.append(("notify", "Notifications channel reachable"))
    checks.append(("scan", "Sample read-only scan dry run (1 recommendation)"))
    return [{"id": i, "label": l, "status": "pending", "detail": ""} for i, l in checks]


def _run_preflight():
    """Advance each check (mock probes); live probes wire in during D8-D11."""
    with LOCK:
        checks = STATE["checks"]
    for c in checks:
        with LOCK:
            c["status"] = "running"
        time.sleep(0.6)
        with LOCK:
            c["status"] = "pass"
            c["detail"] = "ok"
    with LOCK:
        STATE["phase"] = "passed"
        _save_state()


# --- data (only used once ready) --------------------------------------------
async def _gather(project: str):
    ctx = build_context("mock", project)
    ctx._suppressed_fingerprints = SuppressionStore().active_fingerprints(date.today())
    findings, meta = await run_fleet(ctx, project)
    # Per-project governance scope: drop findings whose category is not governed here.
    gov = GovernanceConfig()
    findings = [f for f in findings if gov.category_enabled(project, f.category)]
    fdicts = [finding_to_dict(f) for f in findings]
    prs = await remediate(ctx, fdicts, project)
    audit = list(getattr(ctx.observability, "audit_log", []))
    return fdicts, prs, audit, meta


def _load_ground_truth() -> list[dict]:
    if os.path.exists(GROUND_TRUTH):
        return json.load(open(GROUND_TRUTH)).get("issues", [])
    return []


# --- render helpers ---------------------------------------------------------
def _badge(text, color, *, solid=False):
    text = escape(str(text))
    if solid:
        return f'<span class="badge" style="background:{color};color:#020617">{text}</span>'
    return f'<span class="badge" style="color:{color};border-color:{color}">{text}</span>'


def _kpis(sc):
    ok = sc["unmanaged_detection_ok"]
    cards = [
        ("RECALL", f"{sc['found']}/{sc['total']}", f"{sc['recall']:.0%} of planted issues", "#22C55E"),
        ("PRECISION", f"{sc['precision']:.0%}", "findings that are real", "#38BDF8"),
        ("MONTHLY WASTE FOUND", f"${sc['savings_identified_usd']:,.0f}", "identified this scan", "#FACC15"),
        ("CLICKOPS DETECTION", "PASS" if ok else "FAIL", "untracked resource + attribution",
         "#22C55E" if ok else "#F43F5E"),
    ]
    out = "".join(
        f'<div class="kpi"><div class="kpi-label">{l}</div>'
        f'<div class="kpi-value" style="color:{c}">{escape(v)}</div>'
        f'<div class="kpi-sub">{escape(s)}</div></div>'
        for l, v, s, c in cards
    )
    return f'<section class="kpi-row">{out}</section>'


def _lifecycle_badge(rec):
    if rec is None:
        return ""
    if rec.reopen_count > 0 and rec.state in ("open", "reopened"):
        return " " + _badge("REOPENED", "#FB923C")
    if rec.occurrences <= 1:
        return " " + _badge("NEW", "#38BDF8")
    return ""


def _runcost_panel(open_count, pr_count, monthly_waste):
    est = estimate_runcost(open_count, pr_count)
    monthly = est["monthly"]
    roi = (monthly_waste / monthly) if monthly else 0
    bars = "".join(
        f'<div class="cost-row"><span class="cost-k">{escape(k)}</span>'
        f'<span class="cost-v">${v * est["scans_per_day"] * 30:,.2f}/mo</span></div>'
        for k, v in est["breakdown"].items()
    )
    return (
        '<section class="panel"><h2>CloudCap run cost <span class="muted">— '
        'self-metered estimate (excl. optional SCC Premium)</span></h2>'
        '<div class="roi">'
        f'<div class="roi-cell"><div class="roi-num" style="color:#F87171">${monthly:,.0f}<span>/mo</span></div>'
        '<div class="roi-lab">CloudCap run cost</div></div>'
        f'<div class="roi-cell"><div class="roi-num" style="color:#22C55E">${monthly_waste:,.0f}<span>/mo</span></div>'
        '<div class="roi-lab">waste it finds</div></div>'
        f'<div class="roi-cell"><div class="roi-num" style="color:#FACC15">{roi:,.0f}&times;</div>'
        '<div class="roi-lab">return on run cost</div></div>'
        f'<div class="roi-cell"><div class="roi-num" style="color:#38BDF8">${est["per_scan"]:.2f}</div>'
        '<div class="roi-lab">per scan · {sd}×/day</div></div>'.replace("{sd}", str(est["scans_per_day"])) +
        '</div>'
        f'<div class="cost-break">{bars}</div>'
        '<div class="muted" style="font-size:11px;margin-top:8px">Estimate over 2026 list prices; '
        'a single agent turn bills across several SKUs. CloudCap pays for itself on the first idle VM it retires.</div>'
        '</section>'
    )


def _compliance_panel(findings, enabled_frameworks):
    post = compliance_posture(findings)
    cells = ""
    for fw in enabled_frameworks:
        p = post[fw]
        color = "#22C55E" if p["failing"] == 0 else "#FB923C" if p["score"] >= 0.5 else "#F43F5E"
        cells += (f'<div class="roi-cell"><div class="roi-num" style="color:{color}">{p["score"] * 100:.0f}'
                  f'<span>%</span></div><div class="roi-lab">{escape(fw)} · {p["passing"]}/{p["total"]} controls</div></div>')
    if not cells:
        return ""
    return ('<section class="panel"><h2>Compliance posture <span class="muted">— controls passing by framework · '
            '<a class="navlink inline" href="/compliance">details</a> · '
            '<a class="navlink inline" href="/compliance/report">audit report</a></span></h2>'
            f'<div class="roi">{cells}</div></section>')


def render_compliance(project):
    src = SourcesConfig()
    if project not in src.selected():
        return _page("CloudCap — Compliance", _header("compliance") + '<main>' + _scope_panel(src) + '</main>')
    findings, prs, audit, meta = asyncio.run(_gather(project))
    enabled = GovernanceConfig().enabled_frameworks(project)
    post = compliance_posture(findings)
    from agents.compliance import CONTROLS
    fails = {}
    for f in findings:
        rule = compliance_rule_for(f)
        if rule:
            fails.setdefault(rule, []).append(f["resource"])
    rows = ""
    for rule, m in CONTROLS.items():
        status = "FAIL" if rule in fails else "PASS"
        color = "#F43F5E" if status == "FAIL" else "#22C55E"
        ids = "".join(f'<td class="mono">{escape(m[fw])}</td>' for fw in FRAMEWORKS)
        evidence = ", ".join(fails.get(rule, [])) or "—"
        rows += (f'<tr><td>{escape(m["name"])}</td>{ids}'
                 f'<td>{_badge(status, color)}</td><td class="mono res">{escape(evidence)}</td></tr>')
    head = "".join(f"<th>{escape(fw)}</th>" for fw in FRAMEWORKS)
    body = (_header("compliance") + '<main>' + _compliance_panel(findings, enabled)
            + '<section class="panel"><h2>Control matrix <span class="muted">— '
            '<a class="navlink inline" href="/compliance/report">download audit report</a></span></h2>'
            '<table><thead><tr><th>Control</th>' + head +
            '<th>Status</th><th>Evidence</th></tr></thead><tbody>' + rows +
            '</tbody></table></section></main>')
    return _page("CloudCap — Compliance", body)


def _findings_table(findings, hist):
    rows = ""
    for f in sorted(findings, key=lambda x: -x.get("est_monthly_savings_usd", 0)):
        md = f.get("metadata", {})
        sev = f["severity"]
        src = md.get("management_source", "unknown")
        src_cell = _badge("UNMANAGED · ClickOps", "#A855F7", solid=True) if src == "unmanaged" else escape(src)
        own_status = md.get("ownership_status", "")
        if own_status == "managed":
            owner_cell = _badge(md.get("owner_repo", "repo"), "#22C55E")
        elif own_status == "conflict":
            owner_cell = _badge("CONFLICT · multi-state", "#F43F5E", solid=True)
        elif own_status == "unmanaged":
            owner_cell = _badge("no IaC", "#A855F7")
        else:
            owner_cell = '<span class="muted">—</span>'
        sv = f.get("est_monthly_savings_usd", 0)
        fp = escape(f.get("fingerprint", ""))
        life = _lifecycle_badge(hist.get(f.get("fingerprint", "")))
        ctrls = md.get("controls")
        ctrl_line = ""
        if ctrls:
            ids = " · ".join(f"{fw} {ctrls[fw]}" for fw in FRAMEWORKS)
            ctrl_line = f'<div class="ctrl-line">{escape(ctrls["name"])}: {escape(ids)}</div>'
        res = escape(f["resource"])
        accept = (
            '<form method="POST" action="/suppress" class="acc">'
            f'<input type="hidden" name="fingerprint" value="{fp}">'
            f'<input type="hidden" name="resource" value="{res}">'
            '<select name="duration"><option value="forever">forever</option>'
            '<option value="month">1 month</option><option value="week">1 week</option></select>'
            '<input name="reason" placeholder="reason" class="rsn">'
            '<button class="btn xs">Accept</button></form>'
        )
        rows += (
            "<tr>"
            f'<td class="mono id">{fp}</td>'
            f'<td>{_badge(sev.upper(), SEVERITY.get(sev, "#94A3B8"))}</td>'
            f'<td class="mono">{escape(f["category"])}</td>'
            f'<td class="mono res">{res}</td>'
            f'<td>{escape(f["title"])}{life}{ctrl_line}</td>'
            f'<td class="num">{("$" + format(sv, ",.0f")) if sv else "—"}</td>'
            f"<td>{src_cell}</td>"
            f"<td>{owner_cell}</td>"
            f'<td class="act">{accept}</td></tr>'
        )
    return (
        '<section class="panel"><h2>Findings <span class="muted">— '
        'Accept = suppress as a compliance exception (TTL)</span></h2><table><thead><tr>'
        "<th>Finding ID</th><th>Severity</th><th>Category</th><th>Resource</th>"
        "<th>Title</th><th>Est. savings</th><th>Management source</th>"
        "<th>IaC owner</th><th>Accept</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></section>"
    )


def _resolved_panel(hist):
    rows = sorted(hist.by_state("resolved"), key=lambda r: r.resolved_at or "", reverse=True)
    if not rows:
        return ""
    items = ""
    for r in rows[:12]:
        items += (
            f'<li class="pr"><span class="mono id">{escape(r.fingerprint)}</span>'
            f'<span class="pr-res mono">{escape(r.resource)}</span>'
            f'<span class="pr-reason" style="margin-left:0">{escape(r.title)}</span>'
            f'<span class="pr-status open" style="margin-left:auto">RESOLVED · '
            f'${r.max_savings_usd:,.0f}/mo recovered</span></li>'
        )
    return ('<section class="panel"><h2>Recently resolved <span class="muted">— '
            'auto-closed when no longer detected (fixed / rightsized / terminated)</span></h2>'
            f'<ul class="pr-list">{items}</ul></section>')


def _exceptions():
    store = SuppressionStore()
    rows = store.active(date.today())
    if not rows:
        return ""
    items = ""
    for s in rows:
        when = "forever" if s.until is None else f"until {escape(s.until)}"
        restore = (
            '<form method="POST" action="/unsuppress" style="margin-left:auto">'
            f'<input type="hidden" name="fingerprint" value="{escape(s.fingerprint)}">'
            '<button class="btn xs">Restore</button></form>'
        )
        items += (
            f'<li class="pr"><span class="mono id">{escape(s.fingerprint)}</span>'
            f'<span class="pr-res mono">{escape(s.resource or "—")}</span>'
            f'<span class="pr-reason" style="margin-left:0">{escape(s.reason)} · {when} · '
            f'by {escape(s.created_by)}</span>{restore}</li>'
        )
    return (
        '<section class="panel"><h2>Compliance exceptions <span class="muted">— '
        'accepted findings, suppressed with TTL</span></h2>'
        f'<ul class="pr-list">{items}</ul></section>'
    )


def _pr_list(findings, prs):
    items = ""
    for f, r in zip(findings, prs):
        if r.get("status") == "pr_opened":
            kind = r.get("kind", "")
            items += (
                f'<li class="pr">{_badge(kind, KIND.get(kind, "#94A3B8"), solid=True)}'
                f'<span class="mono branch">{escape(r["branch"])}</span>'
                f'<span class="pr-res mono">{escape(f["resource"])}</span>'
                '<span class="pr-status open">PR OPENED</span></li>'
            )
        else:
            items += (
                f'<li class="pr">{_badge(r.get("status","n/a"), "#64748B")}'
                f'<span class="pr-res mono">{escape(r.get("resource", f["resource"]))}</span>'
                f'<span class="pr-reason">{escape(r.get("reason",""))}</span></li>'
            )
    opened = sum(1 for r in prs if r.get("status") == "pr_opened")
    return (
        f'<section class="panel"><h2>GitOps Pull Requests <span class="muted">— '
        f'{opened} opened · agent has no cloud write access</span></h2>'
        f'<ul class="pr-list">{items}</ul></section>'
    )


def _audit_log(audit):
    lines = ""
    for e in audit:
        action = e.get("action", "")
        color = "#F43F5E" if "block" in action else "#22C55E" if "complete" in action else "#94A3B8"
        detail = escape(json.dumps(e.get("detail", {}), separators=(",", ":")))
        lines += (
            f'<div class="log-line"><span class="log-agent">{escape(e.get("agent",""))}</span>'
            f'<span class="log-action" style="color:{color}">{escape(action)}</span>'
            f'<span class="log-detail">{detail}</span></div>'
        )
    return ('<section class="panel"><h2>Observability — OTel audit / reasoning-chain log</h2>'
            f'<div class="log">{lines or "<div class=\'muted\'>no events</div>"}</div></section>')


# --- pages ------------------------------------------------------------------
def _page(title, body):
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>')


def _header(right="", nav=True):
    navhtml = ('<a class="navlink" href="/">Board</a><a class="navlink" href="/sources">Sources</a>'
               '<a class="navlink" href="/integrations">Integrations</a>'
               '<a class="navlink" href="/compliance">Compliance</a>'
               '<a class="navlink" href="/policy">Policy</a>' if nav else "")
    ident = ""
    if AUTH["logged_in"]:
        btns = "".join(
            f'<form method="POST" action="/assume-role" style="display:inline"><input type="hidden" '
            f'name="role" value="{r}"><button class="rolebtn{" active" if r == AUTH["role"] else ""}">'
            f'{r}</button></form>' for r in AUTH["roles"])
        ident = (f'<span class="who">{escape(AUTH["user"])}</span>'
                 f'<span class="switcher" title="assume role">{btns}</span>'
                 '<a class="navlink" href="/logout">Logout</a>')
    return (
        '<header><span class="logo"><b>Cloud</b>Cap</span>'
        '<span class="sub">Governance Control Plane · Fortified Enterprise Fleet</span>'
        f'{navhtml}<span class="proj">{right}</span>{ident}</header>'
    )


def render_login():
    body = ('<div class="login-wrap"><div class="login-card">'
            '<div class="login-logo"><b>Cloud</b>Cap</div>'
            '<div class="login-sub">Governance Control Plane</div>'
            '<form method="POST" action="/login"><input type="hidden" name="method" value="google">'
            '<button class="btn google" type="submit">Sign in with Google</button></form>'
            '<div class="divider">or</div>'
            '<form method="POST" action="/login">'
            '<label>Email</label><input name="email" placeholder="you@acme.com">'
            '<label>Password</label><input name="password" type="password" placeholder="••••••••">'
            '<button class="btn primary" type="submit" style="width:100%;margin-top:14px">Sign in</button>'
            '</form>'
            '<div class="muted login-hint">Demo: any email + password works. An address containing '
            '"admin" signs in as <b>Admin</b>; otherwise <b>Operator</b>. You can switch roles after login.</div>'
            '</div></div>')
    return _page("CloudCap — Sign in", body)


def _scope_panel(src):
    sel = sorted(src.selected())
    total = len(all_projects())
    chips = "".join(_badge(p, "#38BDF8") for p in sel) or '<span class="muted">none selected</span>'
    return (
        f'<section class="panel"><h2>Scan scope <span class="muted">— {len(sel)}/{total} projects · '
        '<a class="navlink inline" href="/sources">manage</a></span>'
        '<span class="clouds">Clouds: <span class="cloud-on">GCP</span> · '
        '<span class="cloud-soon">AWS — coming soon</span> · '
        '<span class="cloud-soon">Azure — coming soon</span></span></h2>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px">{chips}</div></section>'
    )


def render_policy():
    pol = ActionPolicy()
    banner, dis = _admin_gate()
    labels = {"pr": "Open PR", "issue": "File issue", "slack": "Slack notify"}

    def row(scope, eff, is_default=False):
        cells = ""
        for a in ACTIONS:
            checked = "checked" if eff.get(a) else ""
            cells += (f'<td class="pcell"><input type="checkbox" name="{a}__{escape(scope)}" {checked}></td>')
        name = "default (all projects)" if is_default else scope
        cls = ' class="prow-default"' if is_default else ""
        return f'<tr{cls}><td class="mono">{escape(name)}</td>{cells}</tr>'

    rows = row("default", pol.default, is_default=True)
    for p in all_projects():
        rows += row(p, pol.channels_for(p))

    head = "".join(f"<th>{labels[a]}</th>" for a in ACTIONS)
    body = (_header("action policy")
            + '<main><section class="panel"><h2>Per-project action policy '
            '<span class="muted">— which channels fire for findings in each project</span></h2>'
            f'{banner}<form method="POST" action="/policy/save"><table class="policy"><thead><tr>'
            f'<th>Scope</th>{head}</tr></thead><tbody>{rows}</tbody></table>'
            f'<button class="btn primary" type="submit" {dis}>Save policy</button></form>'
            '<div class="muted" style="font-size:11px;margin-top:10px">Per-project rows override the '
            'default. Remediation delivers each finding only through its project\'s enabled channels.</div>'
            '</section>' + _governance_matrix(banner, dis) + '</main>')
    return _page("CloudCap — Policy", body)


def _governance_matrix(banner, dis):
    gov = GovernanceConfig()

    def grow(scope, eff, is_default=False):
        cells = "".join(
            f'<td class="pcell"><input type="checkbox" name="{escape(o)}__{escape(scope)}" '
            f'{"checked" if eff.get(o) else ""}></td>' for o in GOV_OPTIONS)
        name = "default (all projects)" if is_default else scope
        cls = ' class="prow-default"' if is_default else ""
        return f'<tr{cls}><td class="mono">{escape(name)}</td>{cells}</tr>'

    rows = grow("default", gov.default, is_default=True)
    for p in all_projects():
        rows += grow(p, gov.profile_for(p))
    head = "".join(f"<th>{escape(o)}</th>" for o in GOV_OPTIONS)
    return ('<section class="panel"><h2>Governance scope <span class="muted">— which checks &amp; '
            'compliance frameworks apply per project</span></h2>'
            f'{banner}<form method="POST" action="/governance/save"><table class="policy"><thead><tr>'
            f'<th>Scope</th>{head}</tr></thead><tbody>{rows}</tbody></table>'
            f'<button class="btn primary" type="submit" {dis}>Save governance scope</button></form>'
            '<div class="muted" style="font-size:11px;margin-top:10px">A PCI project can be audited '
            'to PCI DSS; a sandbox can run cost-only. Disabled categories are not scanned; posture uses '
            'only the enabled frameworks.</div></section>')


def _status_pill(status):
    colors = {"pass": "#22C55E", "fail": "#F43F5E", "disabled": "#64748B", "untested": "#FACC15"}
    labels = {"pass": "✓ HEALTHY", "fail": "✗ FAILING", "disabled": "— DISABLED", "untested": "• UNTESTED"}
    c = colors.get(status, "#64748B")
    return f'<span class="pill" style="color:{c};border-color:{c}">{labels.get(status, status)}</span>'


def render_integrations():
    store = IntegrationsStore()
    banner, dis = _admin_gate()
    healthy = sum(1 for i in store.items if i["status"] == "pass")
    total_enabled = sum(1 for i in store.items if i["enabled"])
    cards = ""
    for i in store.items:
        fields = ""
        for fld in i["fields"]:
            val = escape(i["config"].get(fld["key"], ""))
            typ = "password" if fld.get("secret") else "text"
            fields += (f'<label>{escape(fld["label"])}</label>'
                       f'<input type="{typ}" name="{fld["key"]}" value="{val}">')
        checked = "checked" if i["enabled"] else ""
        lc = f' · last checked {escape(i["last_checked"])}' if i["last_checked"] else ""
        detail = f'<div class="int-detail muted">{escape(i["detail"])}{lc}</div>' if i["detail"] else ""
        cards += (
            f'<form method="POST" action="/integrations/update" class="int-card">'
            f'<input type="hidden" name="id" value="{i["id"]}">'
            f'<div class="int-head"><span class="int-name">{escape(i["name"])}</span>'
            f'{_status_pill(i["status"])}</div>'
            f'<div class="chk-row"><input type="checkbox" name="enabled" {checked}>'
            f'<span class="muted">enabled</span></div>'
            f'{fields}{detail}'
            '<div class="int-btns">'
            f'<button class="btn xs" name="action" value="save" {dis}>Save</button>'
            f'<button class="btn xs go" name="action" value="test" {dis}>Test</button></div>'
            '</form>'
        )
    body = (_header(f"{healthy}/{total_enabled} healthy")
            + '<main><section class="panel"><h2>Integrations &amp; health '
            '<span class="muted">— configure endpoints; tokens go to Secret Manager; Test probes live access</span></h2>'
            f'{banner}'
            '<form method="POST" action="/integrations/testall" style="display:inline">'
            f'<button class="btn primary" style="margin:0 0 14px" {dis}>Test all</button></form>'
            f'<div class="int-grid">{cards}</div></section></main>')
    return _page("CloudCap — Integrations", body)


def render_sources():
    src = SourcesConfig()
    banner, dis = _admin_gate()
    sel = src.selected()
    folders = ""
    for f in ORG_TREE["folders"]:
        projs = ""
        for p in f["projects"]:
            checked = "checked" if p in sel else ""
            projs += (f'<li><label><input type="checkbox" name="proj" value="{escape(p)}" {checked}> '
                      f'<span class="mono">{escape(p)}</span></label></li>')
        folders += (
            '<fieldset class="folder"><legend><label>'
            '<input type="checkbox" class="folder-all"> '
            f'{escape(f["name"])} <span class="muted mono">{escape(f["id"])}</span></label></legend>'
            f'<ul class="proj-list">{projs}</ul></fieldset>'
        )
    body = (_header("scan sources") + '<main class="narrow"><section class="panel">'
            '<h2>Scan sources — choose projects &amp; folders to scan</h2>'
            '<p class="muted" style="margin-top:0">Two layers: IAM grants read access to the org/folder '
            '(via Terraform); here you pick which of those projects to actively scan. '
            'Deselect to take a project out of scope without changing IAM.</p>'
            f'{banner}<form method="POST" action="/sources/save">{folders}'
            f'<button class="btn primary" type="submit" {dis}>Save scan scope</button></form>'
            f'</section></main><script>{SOURCES_JS}</script>')
    return _page("CloudCap — Sources", body)


def render_board(project):
    src = SourcesConfig()
    if project not in src.selected():
        body = (_header(f"project: {escape(project)} · out of scope")
                + '<main>' + _scope_panel(src)
                + '<section class="panel"><h2>Out of scan scope</h2>'
                + f'<p class="muted">Project <span class="mono">{escape(project)}</span> is not selected '
                + 'for scanning. Add it under <a class="navlink inline" href="/sources">Sources</a> to scan it.</p>'
                + '</section></main>')
        return _page("CloudCap — Control Plane", body)

    findings, prs, audit, meta = asyncio.run(_gather(project))
    # Score DETECTION (unaffected by user acceptance): open findings + accepted ones.
    detected = findings + [
        {"category": s.get("category", ""), "resource": s["resource"],
         "est_monthly_savings_usd": 0, "metadata": {}}
        for s in meta.get("suppressed_by_policy", [])
    ]
    sc = score_fleet(detected, _load_ground_truth())
    hist = FindingHistory()
    body = (_header(f"project: {escape(project)} · mode: mock · reload to re-scan")
            + '<main>' + _scope_panel(src) + _kpis(sc)
            + _compliance_panel(findings, GovernanceConfig().enabled_frameworks(project))
            + _runcost_panel(len(findings), sum(1 for r in prs if r.get("status") == "pr_opened"),
                             sc["savings_identified_usd"])
            + _findings_table(findings, hist) + _resolved_panel(hist)
            + _exceptions() + _pr_list(findings, prs) + _audit_log(audit) + '</main>'
            + '<footer>CloudCap runs read-only. Remediation is delivered as human-approved '
              'GitOps PRs — the agents hold zero cloud write access.</footer>')
    return _page("CloudCap — Control Plane", body)


def render_setup():
    body = _header("first-run setup") + f'<main class="narrow">{SETUP_FORM}</main>'
    return _page("CloudCap — Setup", body)


def render_testing():
    rows = "".join(
        f'<li class="chk" data-id="{c["id"]}"><span class="dot"></span>'
        f'<span class="clabel">{escape(c["label"])}</span>'
        f'<span class="cstatus">{c["status"].upper()}</span></li>'
        for c in STATE["checks"]
    )
    body = (_header("running preflight")
            + f'<main class="narrow"><section class="panel"><h2>Preflight — verifying access before first scan</h2>'
            + f'<ul class="chk-list" id="checks">{rows}</ul>'
            + '<form method="POST" action="/scan"><button id="go" class="btn go" style="display:none">'
              'Start first scan &rarr;</button></form>'
            + '<form method="POST" action="/reset"><button class="btn ghost">Back to setup</button></form>'
            + f'</section></main><script>{POLL_JS}</script>')
    return _page("CloudCap — Preflight", body)


# --- HTTP -------------------------------------------------------------------
def make_handler(project):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to="/"):
            self.send_response(303)
            self.send_header("Location", to)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/favicon"):
                self.send_response(204); self.end_headers(); return
            if not AUTH["logged_in"]:
                return self._send(render_login())
            if self.path == "/logout":
                AUTH.update(logged_in=False, user="", role="operator", roles=[])
                return self._redirect("/")
            if self.path == "/status":
                with LOCK:
                    payload = {"phase": STATE["phase"], "checks": STATE["checks"]}
                return self._send(json.dumps(payload), "application/json")
            if self.path == "/sources" and STATE["phase"] == "ready":
                return self._send(render_sources())
            if self.path == "/integrations" and STATE["phase"] == "ready":
                return self._send(render_integrations())
            if self.path == "/policy" and STATE["phase"] == "ready":
                return self._send(render_policy())
            if self.path == "/compliance" and STATE["phase"] == "ready":
                return self._send(render_compliance(project))
            if self.path == "/compliance/report" and STATE["phase"] == "ready":
                findings, _p, _a, _m = asyncio.run(_gather(project))
                report = build_audit_report(findings, compliance_posture(findings))
                return self._send(report, "text/markdown; charset=utf-8")
            phase = STATE["phase"]
            if phase == "ready":
                return self._send(render_board(project))
            if phase in ("testing", "passed"):
                return self._send(render_testing())
            return self._send(render_setup())

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode()) if length else {}
            g = lambda k, d="": form.get(k, [d])[0]

            # --- auth (works while logged out) ---
            if self.path == "/login":
                if g("method") == "google":
                    AUTH.update(logged_in=True, user="admin@acme.com (Google)",
                                role="admin", roles=["admin", "operator"])
                else:
                    email = g("email") or "user@acme.com"
                    role = "admin" if "admin" in email.lower() else "operator"
                    AUTH.update(logged_in=True, user=email, role=role, roles=["admin", "operator"])
                return self._redirect("/")
            if not AUTH["logged_in"]:
                return self._redirect("/")
            if self.path == "/assume-role":
                if g("role") in AUTH["roles"]:
                    AUTH["role"] = g("role")
                return self._redirect(self.headers.get("Referer", "/"))
            # --- RBAC: config mutations are Admin-only ---
            if self.path in ADMIN_ROUTES and not _is_admin():
                return self._redirect(self.headers.get("Referer", "/"))

            if self.path == "/setup/save":
                cfg = {
                    "git_provider": g("git_provider", "github"),
                    "git_host": g("git_host", "github.com"),
                    "git_auth_method": g("git_auth_method", "github_app"),
                    "git_token": g("git_token"),
                    "repo_scope": g("repo_scope", f"project:{project}"),
                    "repo": g("repo", "acme/infra-demo"),
                    "enable_jira": bool(g("enable_jira")),
                    "jira_url": g("jira_url"),
                    "enable_slack": bool(g("enable_slack")),
                    "slack_channel": g("slack_channel"),
                }
                with LOCK:
                    STATE["config"] = cfg
                    STATE["checks"] = _build_checks(cfg)
                    STATE["phase"] = "testing"
                    _save_state()
                threading.Thread(target=_run_preflight, daemon=True).start()
                return self._redirect("/")
            if self.path == "/suppress":
                fp = g("fingerprint")
                if fp:
                    until = parse_duration(g("duration", "forever"), date.today())
                    SuppressionStore().add(Suppression(
                        fingerprint=fp, resource=g("resource"),
                        reason=g("reason") or "accepted via dashboard", until=until,
                        created_by="dashboard", created_at=date.today().isoformat()))
                return self._redirect("/")
            if self.path == "/unsuppress":
                fp = g("fingerprint")
                if fp:
                    SuppressionStore().remove(fp)
                return self._redirect("/")
            if self.path == "/sources/save":
                SourcesConfig().set_selected(form.get("proj", []))
                return self._redirect("/sources")
            if self.path == "/integrations/update":
                from datetime import datetime
                store = IntegrationsStore()
                iid = g("id")
                integ = store.get(iid)
                if integ:
                    cfg = {fld["key"]: g(fld["key"]) for fld in integ["fields"]}
                    store.update(iid, cfg, bool(g("enabled")))
                    if g("action") == "test":
                        status, detail = run_integration_check(store.get(iid))
                        store.set_result(iid, status, detail, datetime.now().strftime("%H:%M:%S"))
                return self._redirect("/integrations")
            if self.path == "/policy/save":
                default = {a: bool(g(f"{a}__default")) for a in ACTIONS}
                overrides = {p: {a: bool(g(f"{a}__{p}")) for a in ACTIONS} for p in all_projects()}
                ActionPolicy().save_all(default, overrides)
                return self._redirect("/policy")
            if self.path == "/governance/save":
                default = {o: bool(g(f"{o}__default")) for o in GOV_OPTIONS}
                overrides = {p: {o: bool(g(f"{o}__{p}")) for o in GOV_OPTIONS} for p in all_projects()}
                GovernanceConfig().save_all(default, overrides)
                return self._redirect("/policy")
            if self.path == "/integrations/testall":
                from datetime import datetime
                store = IntegrationsStore()
                ts = datetime.now().strftime("%H:%M:%S")
                for integ in store.items:
                    status, detail = run_integration_check(integ)
                    store.set_result(integ["id"], status, detail, ts)
                return self._redirect("/integrations")
            if self.path == "/scan":
                with LOCK:
                    STATE["phase"] = "ready"; _save_state()
                return self._redirect("/")
            if self.path == "/reset":
                with LOCK:
                    STATE["phase"] = "setup"; STATE["checks"] = []; _save_state()
                return self._redirect("/")
            self._send("not found", code=404)

        def log_message(self, *a):
            pass

    return Handler


def main():
    p = argparse.ArgumentParser(description="CloudCap dashboard + setup wizard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--project", default="demo-proj")
    p.add_argument("--fresh-setup", action="store_true", help="force the setup wizard on start")
    a = p.parse_args()
    _load_state()
    if a.fresh_setup and os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        STATE.update({"phase": "setup", "config": {}, "checks": []})
    srv = HTTPServer(("127.0.0.1", a.port), make_handler(a.project))
    print(f"CloudCap → http://localhost:{a.port}  (phase: {STATE['phase']}; Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


CSS = """
:root{--bg:#020617;--panel:#0F172A;--panel2:#1E293B;--border:#1E293B;--border2:#334155;
--text:#F8FAFC;--muted:#94A3B8;--pos:#22C55E;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.5}
header{display:flex;align-items:baseline;gap:14px;padding:18px 24px;border-bottom:1px solid var(--border);
background:linear-gradient(180deg,#0b1220,#020617)}
header .logo{font-family:var(--mono);font-weight:700;font-size:18px;letter-spacing:.5px}
header .logo b{color:var(--pos)}
header .sub{color:var(--muted);font-size:13px}
header .proj{margin-left:auto;font-family:var(--mono);color:var(--muted);font-size:12px}
main{padding:20px 24px;max-width:1200px;margin:0 auto}
main.narrow{max-width:720px}
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.kpi{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px}
.kpi-label{font-family:var(--mono);font-size:11px;letter-spacing:.6px;color:var(--muted)}
.kpi-value{font-family:var(--mono);font-size:30px;font-weight:700;margin:6px 0 2px;text-shadow:0 0 12px currentColor}
.kpi-sub{font-size:12px;color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:18px}
.panel h2{font-size:13px;font-family:var(--mono);letter-spacing:.5px;text-transform:uppercase;margin:0 0 12px}
.muted{color:var(--muted);text-transform:none;letter-spacing:0;font-weight:400}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-family:var(--mono);font-size:11px;letter-spacing:.5px;color:var(--muted);
border-bottom:1px solid var(--border2);padding:8px 10px}
td{padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--panel2)}
.mono{font-family:var(--mono);font-size:12.5px}
.id{color:#7DD3FC}
.res{color:#CBD5E1}
.num{font-family:var(--mono);text-align:right;color:#FACC15;white-space:nowrap}
.badge{display:inline-block;font-family:var(--mono);font-size:10.5px;font-weight:600;letter-spacing:.4px;
padding:2px 8px;border-radius:999px;border:1px solid transparent}
.pr-list{list-style:none;margin:0;padding:0}
.pr{display:flex;align-items:center;gap:12px;padding:9px 4px;border-bottom:1px solid var(--border)}
.pr:last-child{border-bottom:none}
.branch{color:#E2E8F0}
.pr-res{color:var(--muted);font-size:12px}
.pr-status{margin-left:auto;font-family:var(--mono);font-size:11px}
.pr-status.open{color:var(--pos)}
.pr-reason{margin-left:auto;color:var(--muted);font-size:12px;font-style:italic}
.log{font-family:var(--mono);font-size:12px;background:#060B18;border:1px solid var(--border);border-radius:8px;
padding:12px;max-height:260px;overflow:auto}
.log-line{display:flex;gap:12px;padding:2px 0}
.log-agent{color:#7DD3FC;min-width:130px}
.log-action{min-width:150px}
.log-detail{color:var(--muted)}
label{display:block;font-family:var(--mono);font-size:11px;letter-spacing:.4px;color:var(--muted);margin:12px 0 4px}
input,select{width:100%;background:#060B18;border:1px solid var(--border2);color:var(--text);border-radius:8px;
padding:9px 10px;font-family:var(--sans);font-size:14px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.chk-row{display:flex;align-items:center;gap:8px;margin-top:12px}
.chk-row input{width:auto}
.btn{cursor:pointer;font-family:var(--mono);font-size:13px;font-weight:600;border-radius:8px;padding:11px 18px;
border:1px solid transparent;margin-top:16px;transition:opacity .2s}
.btn:hover{opacity:.85}
.btn.primary{background:var(--pos);color:#020617}
.btn.go{background:var(--pos);color:#020617}
.btn.ghost{background:transparent;border-color:var(--border2);color:var(--muted);margin-left:10px}
.chk-list{list-style:none;margin:0;padding:0}
.chk{display:flex;align-items:center;gap:12px;padding:10px 4px;border-bottom:1px solid var(--border)}
.chk .dot{width:10px;height:10px;border-radius:50%;background:#334155;flex:0 0 auto}
.chk.running .dot{background:#FACC15;box-shadow:0 0 8px #FACC15}
.chk.pass .dot{background:var(--pos);box-shadow:0 0 8px var(--pos)}
.chk.fail .dot{background:#F43F5E;box-shadow:0 0 8px #F43F5E}
.chk .clabel{flex:1}
.chk .cstatus{font-family:var(--mono);font-size:11px;color:var(--muted)}
.act{white-space:nowrap}
.acc{display:flex;gap:6px;align-items:center;margin:0}
.acc select,.acc input{width:auto;padding:5px 8px;font-size:12px}
.acc .rsn{width:110px}
.btn.xs{padding:5px 10px;margin:0;font-size:11px;background:var(--panel2);color:var(--text);border:1px solid var(--border2)}
.roi{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:10px}
.roi-cell{background:#060B18;border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}
.roi-num{font-family:var(--mono);font-size:26px;font-weight:700;text-shadow:0 0 12px currentColor}
.roi-num span{font-size:13px;font-weight:400;color:var(--muted);text-shadow:none}
.roi-lab{font-family:var(--mono);font-size:10.5px;letter-spacing:.4px;color:var(--muted);margin-top:4px}
.cost-break{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:8px}
.cost-row{display:flex;gap:8px;font-family:var(--mono);font-size:12px}
.cost-k{color:var(--muted)}
.cost-v{color:#CBD5E1}
.ctrl-line{font-family:var(--mono);font-size:10.5px;color:#7DD3FC;margin-top:3px}
.clouds{float:right;font-family:var(--mono);font-size:10.5px;font-weight:400;letter-spacing:0}
.cloud-on{color:var(--pos)}
.cloud-soon{color:#475569}
@media (max-width:900px){.roi{grid-template-columns:repeat(2,1fr)}}
.navlink{font-family:var(--mono);font-size:12px;color:var(--muted);text-decoration:none;padding:2px 8px;border-radius:6px}
.navlink:hover{color:var(--text);background:var(--panel2)}
.navlink.inline{padding:0;color:#38BDF8}
header .navlink{margin-left:4px}
.folder{border:1px solid var(--border2);border-radius:8px;padding:8px 14px 12px;margin:0 0 12px}
.folder legend{font-family:var(--mono);font-size:12px;padding:0 6px}
.proj-list{list-style:none;margin:6px 0 0;padding:0}
.proj-list li{padding:4px 0}
.proj-list label,.folder legend label{display:flex;align-items:center;gap:8px;cursor:pointer;font-family:var(--sans)}
.proj-list input,.folder legend input{width:auto}
.int-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.int-card{background:#060B18;border:1px solid var(--border);border-radius:10px;padding:14px}
.int-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.int-name{font-family:var(--mono);font-size:13px;font-weight:600}
.pill{font-family:var(--mono);font-size:10.5px;font-weight:600;letter-spacing:.4px;padding:3px 9px;border-radius:999px;border:1px solid currentColor}
.int-detail{font-size:11px;margin-top:8px}
.int-btns{display:flex;gap:8px;margin-top:12px}
@media (max-width:900px){.int-grid{grid-template-columns:1fr}}
.policy td,.policy th{text-align:left}
.policy .pcell{text-align:center;width:110px}
.policy .pcell input{width:auto}
.prow-default td{background:#0b1220;border-bottom:1px solid var(--border2);font-weight:600}
header .who{margin-left:auto;font-family:var(--mono);font-size:12px;color:#CBD5E1}
.switcher{display:inline-flex;gap:2px;margin:0 6px;border:1px solid var(--border2);border-radius:999px;padding:2px}
.rolebtn{cursor:pointer;font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;
padding:3px 10px;border:none;border-radius:999px;background:transparent;color:var(--muted)}
.rolebtn.active{background:var(--pos);color:#020617;font-weight:700}
.ro-banner{background:#231a06;border:1px solid #a16207;color:#FACC15;border-radius:8px;padding:9px 12px;
font-size:12px;font-family:var(--mono);margin-bottom:12px}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login-card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:30px;width:360px}
.login-logo{font-family:var(--mono);font-size:26px;font-weight:700;text-align:center}
.login-logo b{color:var(--pos)}
.login-sub{text-align:center;color:var(--muted);font-size:13px;margin:2px 0 22px}
.btn.google{width:100%;background:#fff;color:#111;border:none}
.divider{text-align:center;color:var(--muted);font-size:11px;margin:16px 0;position:relative}
.login-hint{font-size:11px;margin-top:18px;line-height:1.5}
footer{color:var(--muted);font-size:12px;padding:8px 24px 28px;max-width:1200px;margin:0 auto;font-family:var(--mono)}
@media (max-width:900px){.kpi-row{grid-template-columns:repeat(2,1fr)}.row{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

SETUP_FORM = """
<section class="panel">
<h2>Setup — connect CloudCap to your tools</h2>
<p class="muted" style="margin-top:0">The only prerequisite was running the Terraform in your hub project.
Configure integrations below — tokens are written to Secret Manager by the app, never stored in code.
A preflight verifies everything before the first scan.</p>
<form method="POST" action="/setup/save">
  <div class="row">
    <div><label>Git provider</label>
      <select name="git_provider"><option>github</option><option>gitlab</option><option>bitbucket</option></select></div>
    <div><label>Host</label><input name="git_host" value="github.com"></div>
  </div>
  <div class="row">
    <div><label>Auth method</label>
      <select name="git_auth_method"><option value="github_app">GitHub App (recommended)</option>
      <option value="pat">Personal / access token</option></select></div>
    <div><label>Token / App key (&rarr; Secret Manager)</label><input name="git_token" type="password" placeholder="ghp_… or app private key"></div>
  </div>
  <div class="row">
    <div><label>IaC scope</label><input name="repo_scope" value="project:demo-proj"></div>
    <div><label>IaC repo</label><input name="repo" value="acme/infra-demo"></div>
  </div>
  <div class="chk-row"><input type="checkbox" name="enable_jira" id="jira"><label for="jira" style="margin:0">Enable Jira fallback (tickets when no repo access)</label></div>
  <div><label>Jira base URL</label><input name="jira_url" placeholder="https://acme.atlassian.net"></div>
  <div class="chk-row"><input type="checkbox" name="enable_slack" id="slack"><label for="slack" style="margin:0">Enable Slack notifications</label></div>
  <div><label>Slack channel</label><input name="slack_channel" placeholder="#cloudcap-alerts"></div>
  <button class="btn primary" type="submit">Save &amp; run preflight &rarr;</button>
</form>
</section>
"""

SOURCES_JS = """
document.querySelectorAll('.folder-all').forEach(fa => {
  const box = fa.closest('fieldset');
  const kids = box.querySelectorAll('input[name=proj]');
  const sync = () => { fa.checked = [...kids].every(k => k.checked); };
  sync();
  fa.addEventListener('change', e => { kids.forEach(k => k.checked = e.target.checked); });
  kids.forEach(k => k.addEventListener('change', sync));
});
"""

POLL_JS = """
async function poll(){
  const s = await (await fetch('/status')).json();
  const el = document.getElementById('checks');
  el.innerHTML = s.checks.map(c =>
    `<li class="chk ${c.status}"><span class="dot"></span><span class="clabel">${c.label}</span>`+
    `<span class="cstatus">${c.status.toUpperCase()}${c.detail? ' · '+c.detail : ''}</span></li>`).join('');
  if(s.phase==='passed'){ document.getElementById('go').style.display='inline-block'; }
  else if(s.phase==='ready'){ location.href='/'; }
  else { setTimeout(poll, 700); }
}
poll();
"""


if __name__ == "__main__":
    main()
