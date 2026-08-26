"""First-run setup journey — the guided onboarding wizard.

A focused, sidebar-less flow: Welcome → Discover projects → Governance & actions →
Sources & channels (per project) → Review → first scan. Writes straight into the same
config stores the app uses (SourcesConfig, GovernanceConfig, ActionPolicy,
ProjectSettings, FreezeStore), so finishing onboarding = a fully configured hub.
"""

from __future__ import annotations

import os
from html import escape as esc

from agents.governance import GovernanceConfig
from agents.policy import ActionPolicy
from agents.project_settings import ProjectSettings
from agents.sources import ORG_TREE, SourcesConfig, all_projects

_HERE = os.path.dirname(__file__)
_BOARD_HTML = os.path.join(_HERE, "screens", "board.html")

STEPS = [("discover", "Discover"), ("policy", "Govern"),
         ("integrations", "Deliver"), ("review", "Review")]

GOV_DOMAINS = [("cost", "Cost waste"), ("security", "Security & IAM")]
FRAMEWORKS = ["CIS GCP", "SOC 2", "ISO 27001", "PCI DSS"]
ACTION_LABELS = [("pr", "Open remediation PRs"), ("issue", "File issues / tickets"),
                 ("slack", "Send Slack alerts")]


def _page(inner: str) -> str:
    head = open(_BOARD_HTML).read()
    head = head[:head.index("<body")]
    return (head + '<body class="bg-background text-on-background min-h-screen antialiased">'
            + inner + "</body></html>")


def _stepper(active_key: str) -> str:
    cells = ""
    active_idx = next((i for i, (k, _) in enumerate(STEPS) if k == active_key), -1)
    for i, (key, label) in enumerate(STEPS):
        done = i < active_idx
        on = i == active_idx
        dot = ("bg-primary text-on-primary" if on else
               "bg-primary-container text-on-primary-container" if done else
               "bg-surface-container-highest text-on-surface-variant")
        txt = "text-on-surface font-semibold" if on else "text-on-surface-variant"
        cells += (f'<div class="flex items-center gap-2">'
                  f'<span class="h-6 w-6 rounded-full flex items-center justify-center text-[11px] font-bold {dot}">'
                  f'{"✓" if done else i + 1}</span>'
                  f'<span class="text-xs {txt}">{esc(label)}</span></div>')
        if i < len(STEPS) - 1:
            cells += '<div class="flex-1 h-px bg-outline-variant/60 mx-2"></div>'
    return f'<div class="flex items-center w-full mb-8">{cells}</div>'


def _shell(active_key: str, title: str, subtitle: str, body: str) -> str:
    stepper = _stepper(active_key) if active_key else ""
    inner = (
        '<div class="min-h-screen flex flex-col items-center px-4 py-10">'
        '<div class="w-full max-w-3xl">'
        '<div class="flex items-center gap-2 mb-8">'
        '<span class="material-symbols-outlined text-primary">shield</span>'
        '<span class="font-headline font-bold text-xl text-on-surface">CloudCap</span>'
        '<span class="text-xs text-on-surface-variant ml-1">setup</span>'
        '<a href="/logout" class="ml-auto text-xs text-on-surface-variant hover:text-error">sign out</a></div>'
        + stepper +
        '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-8">'
        f'<h1 class="text-2xl font-bold text-on-surface">{esc(title)}</h1>'
        f'<p class="text-sm text-on-surface-variant mt-1 mb-6">{esc(subtitle)}</p>'
        + body +
        '</section></div></div>')
    return _page(inner)


def _btn(label, primary=True):
    cls = ("bg-primary text-on-primary hover:bg-primary/90" if primary
           else "bg-surface border border-outline-variant/50 text-on-surface hover:bg-surface-container")
    return f'<button type="submit" class="px-5 py-2.5 rounded-lg text-sm font-bold {cls}">{esc(label)}</button>'


def _toggle(name, on, label, value="1"):
    chk = "checked" if on else ""
    return (f'<label class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-outline-variant/40 '
            'bg-surface hover:border-primary/40 cursor-pointer">'
            f'<input type="checkbox" name="{esc(name)}" value="{esc(value)}" {chk} class="w-4 h-4 accent-primary"/>'
            f'<span class="text-sm text-on-surface">{esc(label)}</span></label>')


# --- steps ------------------------------------------------------------------
def render_welcome(auth):
    name = (auth.get("name") or "there").split(" ")[0]
    body = (
        f'<p class="text-sm text-on-surface leading-relaxed">Hi {esc(name)} — CloudCap is a '
        'governed multi-agent service that continuously audits your GCP fleet for cost waste, '
        'security and IAM risk, and compliance drift, then proposes <b>human-approved</b> fixes. '
        'It never writes to your cloud.</p>'
        '<div class="mt-6 grid grid-cols-1 sm:grid-cols-2 gap-3">'
        + "".join(f'<div class="flex items-start gap-2 text-sm text-on-surface-variant">'
                  f'<span class="material-symbols-outlined text-primary text-base mt-0.5">{ic}</span>{t}</div>'
                  for ic, t in [("travel_explore", "Discover the projects we can see"),
                                ("checklist", "Choose what to govern & how"),
                                ("hub", "Connect repos & alert channels"),
                                ("radar", "Run your first scan")])
        + '</div>'
        '<form method="POST" action="/onboarding/start" class="mt-8 flex justify-end">'
        + _btn("Discover my projects  →") + '</form>')
    return _shell("", "Welcome to CloudCap", "Let's set up your governance hub — about 2 minutes.", body)


def render_discover():
    from collections import OrderedDict

    from agents.discovery import discover_projects
    selected = SourcesConfig().selected()
    projects = discover_projects()
    # group by folder/parent for display
    groups: "OrderedDict[str, list]" = OrderedDict()
    for p in projects:
        groups.setdefault(p["folder"], []).append(p)
    rows = ""
    for folder, items in groups.items():
        rows += (f'<div class="mt-4 first:mt-0"><div class="text-[11px] font-bold uppercase tracking-wider '
                 f'text-on-surface-variant mb-2">{esc(folder)}</div>'
                 '<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">')
        for p in items:
            proj = p["id"]
            chk = "checked" if proj in selected else ""
            rows += (f'<label class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-outline-variant/40 '
                     'bg-surface hover:border-primary/40 cursor-pointer">'
                     f'<input type="checkbox" name="proj" value="{esc(proj)}" {chk} class="w-4 h-4 accent-primary"/>'
                     f'<span class="font-mono text-sm text-on-surface">{esc(proj)}</span></label>')
        rows += "</div></div>"
    empty = ('<p class="text-sm text-on-surface-variant">No projects discovered yet. Grant the CloudCap '
             'service account <b>Browser</b> at your org/folder, then re-discover.</p>') if not projects else ""
    body = (
        '<div class="flex items-center justify-between mb-1">'
        f'<p class="text-sm text-on-surface-variant">Discovered <b class="text-on-surface">{len(projects)}</b> '
        'project(s) via Cloud Resource Manager. Select the ones CloudCap should govern.</p>'
        '<form method="POST" action="/onboarding/rediscover">'
        '<button class="text-xs text-primary hover:underline">↻ Re-discover</button></form></div>'
        '<form method="POST" action="/onboarding/sources">'
        f'{empty}{rows}'
        '<div class="mt-8 flex justify-between">'
        '<a href="/onboarding" class="px-5 py-2.5 rounded-lg text-sm font-bold bg-surface border '
        'border-outline-variant/50 text-on-surface hover:bg-surface-container">←  Back</a>'
        + _btn("Continue  →") + '</div></form>')
    return _shell("discover", "Discover & scope projects", "Pick the projects in governance scope.", body)


def render_policy():
    gov = GovernanceConfig().default
    pol = ActionPolicy().default
    gov_body = "".join(_toggle(f"gov__{k}", gov.get(k, True), label) for k, label in GOV_DOMAINS)
    gov_body += "".join(_toggle(f"gov__{fw}", gov.get(fw, True), fw) for fw in FRAMEWORKS)
    act_body = "".join(_toggle(f"act__{k}", pol.get(k, k != "issue"), label) for k, label in ACTION_LABELS)
    body = (
        '<form method="POST" action="/onboarding/policy">'
        '<div class="text-[11px] font-bold uppercase tracking-wider text-on-surface-variant mb-2">Governance domains</div>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{gov_body}</div>'
        '<div class="text-[11px] font-bold uppercase tracking-wider text-on-surface-variant mt-6 mb-2">'
        'Actions (how we deliver findings)</div>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{act_body}</div>'
        '<p class="text-xs text-on-surface-variant mt-3">These are org-wide defaults — you can override per project '
        'anytime on the Policy screen.</p>'
        '<div class="mt-8 flex justify-between">'
        '<a href="/onboarding/discover" class="px-5 py-2.5 rounded-lg text-sm font-bold bg-surface border '
        'border-outline-variant/50 text-on-surface hover:bg-surface-container">←  Back</a>'
        + _btn("Continue  →") + '</div></form>')
    return _shell("policy", "Governance & actions", "What should we look for, and how should we act on it?", body)


def render_integrations():
    from agents.freeze import FreezeStore
    from webui.render import SETUP_JS, project_setup_form
    ps = ProjectSettings()
    fz = FreezeStore()
    selected = sorted(SourcesConfig().selected()) or all_projects()

    cards = ""
    for proj in selected:
        s = ps.get(proj)
        repo, chans, frozen = bool(s["repo"]), len(s["channels"]), fz.get(proj)
        if repo and chans:
            badge = ('<span class="inline-flex items-center gap-1 text-xs font-bold text-primary">'
                     '<span class="material-symbols-outlined text-base">check_circle</span>Configured</span>')
        elif repo or chans or frozen:
            badge = '<span class="text-xs font-semibold text-tertiary">Partial</span>'
        else:
            badge = '<span class="text-xs text-on-surface-variant">Not set</span>'
        # Each project = a collapsible card with its own validated setup form.
        cards += (
            '<details class="rounded-lg border border-outline-variant/40 bg-surface overflow-hidden">'
            '<summary class="flex items-center justify-between px-4 py-3 cursor-pointer select-none '
            'hover:bg-surface-container-low">'
            f'<span class="font-mono text-sm text-on-surface">{esc(proj)}</span>{badge}</summary>'
            '<div class="border-t border-outline-variant/30 p-4">'
            + project_setup_form(proj, action="/onboarding/project/save", submit="Save", cancel_href=None, include_js=False)
            + '</div></details>')

    body = (
        '<p class="text-sm text-on-surface-variant mb-3">Set up each project — expand it, add a repo (for fix PRs), '
        'a freeze window, and notification channels. All optional: a project still scans without them. '
        'Saving one returns you here to do the next.</p>'
        f'<div class="space-y-2">{cards}</div>'
        + SETUP_JS +
        '<div class="mt-8 flex items-center justify-between">'
        '<a href="/onboarding/policy" class="px-5 py-2.5 rounded-lg text-sm font-bold bg-surface border '
        'border-outline-variant/50 text-on-surface hover:bg-surface-container">←  Back</a>'
        '<div class="flex items-center gap-4">'
        '<a href="/onboarding/review" class="text-sm text-on-surface-variant hover:text-primary">Skip — finish later</a>'
        '<a href="/onboarding/review" class="px-5 py-2.5 rounded-lg text-sm font-bold bg-primary text-on-primary '
        'hover:bg-primary/90">Review  →</a></div></div>')
    return _shell("integrations", "Sources & channels (optional)",
                  "Set up projects one by one — repo, freeze, and channels, each validated. "
                  "Optional; you can finish any project later from the Projects page.", body)


def render_review():
    selected = sorted(SourcesConfig().selected())
    gov = {k: v for k, v in GovernanceConfig().default.items() if v}
    pol = {k: v for k, v in ActionPolicy().default.items() if v}
    ps = ProjectSettings()
    from agents.freeze import FreezeStore
    fz = FreezeStore()
    configured = 0
    proj_rows = ""
    for p in selected:
        s = ps.get(p)
        frozen = fz.get(p)
        chans = s.get("channels", [])
        repo, chan = bool(s.get("repo")), bool(chans)
        if repo and chan:
            configured += 1
            badge = ('<span class="inline-flex items-center gap-1 text-xs font-bold text-primary whitespace-nowrap">'
                     '<span class="material-symbols-outlined text-base">check_circle</span>Configured</span>')
        elif repo or chan or frozen:
            badge = '<span class="text-xs font-semibold text-tertiary whitespace-nowrap">Partial</span>'
        else:
            badge = '<span class="text-xs text-on-surface-variant whitespace-nowrap">Report-only</span>'
        details = []
        if s.get("repo"):
            details.append(f'<span class="font-mono">{esc(s["repo"])}</span>')
        for c in chans:
            details.append(f'{esc(c["type"])}: {esc(c["value"])}')
        if frozen:
            details.append(f'<span class="text-tertiary">❄ frozen until {esc(frozen.get("until", "?"))}</span>')
        det = (f'<div class="text-xs text-on-surface-variant mt-0.5 break-all">{" · ".join(details)}</div>'
               if details else "")
        proj_rows += (
            '<div class="flex items-start justify-between gap-4 px-4 py-2.5 border-b border-outline-variant/30 last:border-0 '
            'hover:bg-surface-container-low">'
            f'<div class="min-w-0"><div class="font-mono text-sm text-on-surface">{esc(p)}</div>{det}</div>'
            f'<div class="shrink-0 pt-0.5">{badge}</div></div>')

    def tile(big, sub):
        return (f'<div class="rounded-lg border border-[#d4d4d8] bg-surface p-3 flex flex-col">'
                f'<div class="text-sm font-semibold text-on-surface leading-snug">{big}</div>'
                f'<div class="text-xs text-on-surface-variant mt-auto pt-2">{esc(sub)}</div></div>')

    body = (
        '<div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5 items-stretch">'
        + tile(f'<span class="text-2xl font-bold text-primary">{len(selected)}</span> '
               f'<span class="text-xs text-on-surface-variant">· {configured} configured</span>', "projects in scope")
        + tile(esc(", ".join(gov) or "none"), "governance")
        + tile(esc(", ".join(pol) or "none"), "actions")
        + '</div>'
        '<div class="text-[11px] font-bold uppercase tracking-wider text-on-surface-variant mb-2">Projects</div>'
        f'<section class="bg-surface border border-[#d4d4d8] rounded-lg overflow-hidden">{proj_rows}</section>'
        '<p class="text-xs text-on-surface-variant mt-2">Report-only projects still scan — they just have no repo/channel '
        'yet. You can finish their setup anytime from the Projects page.</p>'
        '<form method="POST" action="/onboarding/finish" class="mt-8 flex justify-between">'
        '<a href="/onboarding/integrations" class="px-5 py-2.5 rounded-lg text-sm font-bold bg-surface border '
        'border-outline-variant/50 text-on-surface hover:bg-surface-container">←  Back</a>'
        + _btn("Finish & go to dashboard  →") + '</form>')
    return _shell("review", "Review & finish", "Confirm your setup — then run the first scan.", body)
