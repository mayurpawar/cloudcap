"""Render the polished Stitch Board with LIVE CloudCap data.

Strategy: keep screens/board.html exactly as Stitch produced it, and inject live
values at serve time (scalars via targeted replace; compliance cards + findings rows
via regex block-replace). This is the reusable pattern for every other screen.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date
from html import escape as esc

from agents.compliance import CONTROLS, FRAMEWORKS, posture as compliance_posture, rule_for as compliance_rule_for
from agents.context import build_context
from agents.fleet_runner import run_fleet
from agents.governance import GOV_OPTIONS, GovernanceConfig
from agents.integrations import IntegrationsStore
from agents.history import FindingHistory
from agents.policy import ACTIONS, ActionPolicy
from agents.remediation.pr_channel import remediate
from agents.run import finding_to_dict
from agents.runcost import estimate as estimate_runcost
from agents.sources import SourcesConfig, all_projects
from agents.suppressions import SuppressionStore
from eval.score import score as score_fleet

FW_DISPLAY = {"CIS GCP": "CIS GCP 2.0", "SOC 2": "SOC 2 Type II",
              "ISO 27001": "ISO 27001", "PCI DSS": "PCI DSS v4.0"}
NAV = {"Board": "/board", "Sources": "/sources", "Integrations": "/integrations",
       "Compliance": "/compliance", "Policy": "/policy"}
# The Stitch export's checkbox ids don't all follow proj-<name>.
SOURCE_ID = {"demo-proj": "proj-demo"}


def _wire_nav(html):
    """Point the sidebar nav at our routes — matched by LABEL (icon-agnostic)."""
    for label, path in NAV.items():
        html = re.sub(
            r'(<a\b[^>]*?)href="#"((?:(?!</a>).)*?>\s*' + re.escape(label) + r'\s*<)',
            r'\1href="' + path + r'"\2', html, count=1, flags=re.S)
    return html


def _screen(name):
    return open(os.path.join(HERE, "screens", name)).read()


# Collects checkboxes within the button's <section> and POSTs them, then reloads.
CC_JS = """<script>
function ccSave(action, btn){
  const sec = btn.closest('section') || document;
  const p = new URLSearchParams();
  sec.querySelectorAll('input[type=checkbox]').forEach(c=>{ if(c.name && c.checked) p.append(c.name, c.value||'on'); });
  btn.disabled = true;
  fetch(action,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:p.toString()})
    .then(()=>location.reload());
}
</script>"""


def _inject_js(html):
    return html.replace("</body>", CC_JS + "</body>", 1)


PAGE_SIZE_FINDINGS = 10
PAGE_SIZE_CONTROLS = 10

# Secondary (non-primary) button — soft taupe with a forest-green outline (reference
# design). Primary CTAs stay solid green; everything else uses this.
BTN_2ND = ("bg-[#ece6dc] text-primary border border-primary/25 rounded-lg font-semibold "
           "hover:bg-primary hover:text-on-primary transition-colors")


def _pager(base, page, total, per):
    if total <= per:            # nothing to paginate — hide the pager entirely
        return ""
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))
    lo = 0 if total == 0 else (page - 1) * per + 1
    hi = min(total, page * per)
    dis = lambda c: "opacity-40 pointer-events-none" if c else ""  # noqa: E731
    return (
        '<div class="px-4 py-3 border-t border-outline-variant/30 flex justify-between items-center '
        'text-xs text-on-surface-variant bg-surface-container-lowest">'
        f'<span>Showing {lo}–{hi} of {total}</span><div class="flex gap-2">'
        f'<a href="{base}?page={page - 1}" class="px-3 py-1 rounded-md bg-surface-container '
        f'hover:bg-surface-container-high transition-colors {dis(page <= 1)}">Previous</a>'
        f'<a href="{base}?page={page + 1}" class="px-3 py-1 rounded-md bg-surface-container '
        f'hover:bg-surface-container-high transition-colors {dis(page >= pages)}">Next</a>'
        '</div></div>')


# Neutral (Langfuse-like) palette: warm-cream surfaces → white/zinc, warm text → near-black,
# borders → light gray. Green stays as the single accent; error/amber kept for status.
# Warm "paper" palette (matches the reference design). We keep the template's warm
# Material tokens and only nudge a couple of accents for consistent finding severities.
CONFIG_REMAP = {
    # Warm cream surfaces + taupe containers (card / header / sidebar backgrounds).
    "background": "#fdf9f3", "surface": "#fdf9f3", "surface-bright": "#fdf9f3",
    "surface-container-lowest": "#ffffff", "surface-container-low": "#f7f3ed",
    "surface-container": "#f1ede7", "surface-variant": "#e6e2dc",
    "surface-container-high": "#ebe8e2", "surface-container-highest": "#e6e2dc",
    "surface-dim": "#dddad4",
    "on-surface": "#1c1c18", "on-background": "#1c1c18", "on-surface-variant": "#414942",
    "outline": "#717971", "outline-variant": "#c1c9bf",
    "primary": "#316342", "primary-container": "#dcecdf", "on-primary-container": "#1e5031",
}


def neutralize(html):
    for key, hexv in CONFIG_REMAP.items():
        html = re.sub(r'"' + re.escape(key) + r'": "#[0-9a-fA-F]{6}"', f'"{key}": "{hexv}"', html)
    return html


def restyle(html):
    """Warm paper theme (reference design): Literata headings + Nunito Sans body,
    subtle table row lines, and a uniform type scale."""
    # Keep the template's typography — Literata (headline/display) + Nunito Sans (body/
    # label). Normal text + the menu render in Nunito; headings/component names in Literata.
    # UNIFORM TYPE SCALE — collapse ad-hoc pixel sizes onto the Tailwind scale so no page
    # mixes several near-identical tiny fonts. Smallest allowed is text-xs; the scale is
    # then text-xs · text-sm · text-base · text-lg · text-xl · text-2xl.
    for tiny in ("text-[8px]", "text-[9px]", "text-[10px]", "text-[11px]"):
        html = html.replace(tiny, "text-xs")
    html = html.replace("text-[12px]", "text-xs").replace("text-[13px]", "text-sm")
    # subtle, visible row separators
    html = html.replace("divide-outline-variant/10", "divide-outline-variant/60")
    html = html.replace("divide-outline-variant/20", "divide-outline-variant/60")
    # crisper cards (less round) + tighter rhythm (aa2 look)
    html = html.replace("rounded-2xl", "rounded-lg").replace("rounded-xl", "rounded-lg")
    html = html.replace("space-y-8", "space-y-6").replace("gap-6", "gap-5")
    # cards must read against the dotted canvas: warm border + a soft shadow to lift
    html = html.replace("border border-outline-variant/40 rounded-lg",
                        "border border-[#e0dbd1] rounded-lg shadow-sm")
    html = html.replace("rounded-lg border border-outline-variant/20",
                        "rounded-lg border border-[#e0dbd1] shadow-sm")
    html = html.replace("#d4d4d8", "#e0dbd1")  # warm any hardcoded cool-grey card borders
    # darken card/section headers (thead + _card strips) — but not row-hover or the
    # pager's -lowest fill (protect those two, then restore).
    html = html.replace("hover:bg-surface-container-low", "\x00H\x00")
    html = html.replace("bg-surface-container-lowest", "\x00L\x00")
    html = html.replace("bg-surface-container-low", "bg-surface-container")
    html = html.replace("\x00H\x00", "hover:bg-surface-container-low")
    html = html.replace("\x00L\x00", "bg-surface-container-lowest")
    # global density + a faint dot-grid canvas behind the cards
    style = ("<style>html{font-size:14px}"
             # checkboxes / radios in the forest-green theme. The @tailwindcss/forms plugin
             # makes them appearance:none and colours the CHECKED state via background-color
             # (default blue-600), so override that (not accent-color) to the primary green.
             "input[type=checkbox]:checked,input[type=radio]:checked{"
             "background-color:#316342 !important;border-color:#316342 !important}"
             "input[type=checkbox]:focus,input[type=radio]:focus{"
             "--tw-ring-color:#316342 !important}"
             "input[type=checkbox],input[type=radio]{accent-color:#316342 !important}"
             "main{background-color:#fdf9f3;background-image:radial-gradient(circle at 1px 1px,"
             "rgba(28,28,24,0.045) 1px,transparent 0);background-size:20px 20px}"
             "main>header{background-image:none}"
             # vertical column dividers on every table (match the horizontal row-line tone)
             "table td,table th{border-right:1px solid rgba(228,228,231,0.6)}"
             "table td:last-child,table th:last-child{border-right:0}</style>")
    icons = ('<link rel="icon" type="image/x-icon" href="/favicon.ico"/>'
             '<link rel="icon" type="image/png" sizes="128x128" href="/logo-small.png"/>'
             '<link rel="apple-touch-icon" href="/logo-small@2x.png"/>')
    html = html.replace("</head>", icons + style + "</head>", 1)
    return html


def normalize_layout(html):
    """Left-align content uniformly (like Compliance) — strip the max-width centering
    that leaves dead whitespace on Sources/Integrations/Policy/Board."""
    reps = {
        "max-w-4xl mx-auto": "w-full",                       # sources
        "max-w-7xl mx-auto": "w-full",                       # integrations, board
        "max-w-6xl w-full": "w-full",                        # policy content
        "flex justify-center w-full ml-64": "w-full ml-64",  # policy main: drop centering
        # give Sources' main the same comfortable padding as the other screens
        '<main class="flex-1 flex flex-col min-w-0 bg-background relative">':
            '<main class="flex-1 flex flex-col min-w-0 bg-background relative p-8 lg:p-12">',
    }
    for a, b in reps.items():
        html = html.replace(a, b)
    return html


TITLES = {"/board": "Board Overview", "/sources": "Sources",
          "/integrations": "Integrations & Health", "/compliance": "Compliance Posture",
          "/policy": "Policy Configuration", "/hub": "Hub · Components"}
NAV_ITEMS = [("Board", "/board", "dashboard"), ("Hub", "/hub", "hub"),
             ("History", "/history", "history"), ("Sources", "/sources", "database"),
             ("Compliance", "/compliance", "verified_user"), ("Policy", "/policy", "gavel")]


def _sidebar(active, project):
    sel, total = len(SourcesConfig().selected()), len(all_projects())
    nav = ""
    for label, path, icon in NAV_ITEMS:
        on = path == active
        cls = ("bg-primary-container text-on-primary-container font-bold" if on
               else "text-on-surface-variant hover:text-primary hover:bg-surface-variant")
        # The Sources item carries the scope count (selected / discovered) as a badge.
        badge = (f'<span class="ml-auto text-xs font-semibold px-1.5 py-0.5 rounded '
                 f'bg-surface-variant text-on-surface-variant">{sel}/{total}</span>'
                 if label == "Sources" else "")
        nav += (f'<a href="{path}" class="flex items-center gap-3 px-4 py-3 rounded-lg {cls} transition-colors">'
                f'<span class="material-symbols-outlined">{icon}</span><span>{label}</span>{badge}</a>')
    # Primary action: run a governance scan. Placed at the BOTTOM (not the top-left,
    # where a stray click would trigger an unintended scan).
    scan_js = ("var b=this.querySelector('button');b.disabled=true;"
               "b.innerHTML='&lt;span class=&quot;material-symbols-outlined animate-spin&quot;&gt;"
               "progress_activity&lt;/span&gt; Scanning…';")
    scan_cta = (
        f'<div class="px-4 pt-3"><form method="POST" action="/scan/run" onsubmit="{scan_js}">'
        '<button type="submit" class="w-full bg-primary text-on-primary py-3 rounded-lg '
        'font-bold flex items-center justify-center gap-2 hover:bg-primary/90 transition-colors '
        'disabled:opacity-80 disabled:cursor-wait">'
        '<span class="material-symbols-outlined">radar</span>Run scan</button></form></div>')
    return (
        '<aside class="w-64 shrink-0 bg-surface-container-highest flex flex-col border-r border-outline-variant/50 h-screen sticky top-0">'
        '<div class="p-6"><div class="flex items-center gap-2.5">'
        '<img src="/logo-small.png" alt="CloudCap" class="w-8 h-8 shrink-0"/>'
        '<div class="font-headline font-bold text-2xl text-primary">CloudCap</div></div>'
        '<div class="text-on-surface-variant text-xs mt-1">Fortified Enterprise Fleet</div></div>'
        f'<nav class="flex-1 px-4 py-3 space-y-1 overflow-y-auto">{nav}</nav>'
        + scan_cta +
        '<div class="px-4 py-4 mt-1 border-t border-outline-variant/20 space-y-1">'
        '<a href="/docs#support" class="flex items-center gap-3 px-4 py-2 text-sm text-on-surface-variant hover:text-primary">'
        '<span class="material-symbols-outlined text-lg">help</span>Support</a>'
        '<a href="/docs" class="flex items-center gap-3 px-4 py-2 text-sm text-on-surface-variant hover:text-primary">'
        '<span class="material-symbols-outlined text-lg">description</span>Docs</a></div></aside>')


def _initials(name, email):
    """FL from a full name; else two letters from the email local-part."""
    src = (name or "").strip() or (email or "").split("@")[0]
    parts = [p for p in re.split(r"[.\-_ ]+", src) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (src[:2] or "U").upper()


def _header(active, auth, title=None):
    """Role is a READ-ONLY badge (set at login from identity) — no self-service toggle."""
    role = auth.get("role", "operator")
    name = auth.get("name") or auth.get("user") or "user"
    email = auth.get("email", "")
    picture = auth.get("picture")
    initials = _initials(name, email)
    avatar = (
        f'<img src="{esc(picture)}" alt="{esc(initials)}" referrerpolicy="no-referrer" '
        'class="h-9 w-9 rounded-full border border-primary/20 object-cover"/>'
        if picture else
        '<div class="h-9 w-9 rounded-full bg-primary-container/30 border border-primary/20 flex items-center '
        f'justify-center"><span class="text-primary font-bold text-xs">{esc(initials)}</span></div>')
    return (
        '<header class="flex items-center justify-between px-8 py-3.5 border-b border-outline-variant/70 '
        'bg-surface-container-highest sticky top-0 z-10">'
        f'<h1 class="font-headline text-xl font-bold text-on-surface">{esc(title or TITLES.get(active, "CloudCap"))}</h1>'
        '<div class="flex items-center gap-3">'
        '<div class="text-right leading-tight">'
        f'<div class="text-sm font-bold text-on-surface">{esc(name)}</div>'
        f'<div class="text-[10px] font-bold text-primary uppercase tracking-wider">{esc(role.title())}</div></div>'
        f'{avatar}'
        '<a href="/logout" class="material-symbols-outlined text-on-surface-variant hover:text-error transition-colors p-2">logout</a>'
        '</div></header>')


def _footer():
    return (
        '<footer class="sticky bottom-0 z-10 flex items-center justify-between gap-4 px-8 py-3 '
        'border-t border-outline-variant/70 bg-surface-container-highest text-sm text-on-surface-variant">'
        '<div>© 2026 CloudCap · Fortified Enterprise Fleet</div>'
        '<div class="flex items-center gap-4">'
        '<span class="inline-flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-primary"></span>'
        'All systems operational</span>'
        '<a href="#" class="hover:text-primary transition-colors">Privacy</a>'
        '<a href="#" class="hover:text-primary transition-colors">Terms</a>'
        '<span class="font-mono">v0.1.0</span></div></footer>')


# Actions an Operator (read-only) may still perform. Everything else is Admin-only:
# the UI disables those controls client-side (the server enforces it too).
_RO_ALLOWED_ACTIONS = ("/scan/run", "/suppress", "/login")


def _read_only_banner():
    return ('<div class="flex items-center gap-2 rounded-lg border border-outline-variant/30 '
            'bg-surface-container-low px-4 py-2.5 text-xs text-on-surface-variant">'
            '<span class="material-symbols-outlined text-base select-none">visibility</span>'
            '<span><b class="text-on-surface">Read-only access.</b> You can view everything, '
            'run a scan, and accept findings. Configuration is admin-only.</span></div>')


def _read_only_script():
    """Disable every mutating control except the operator-allowed ones, and mark them
    'Only admins can change this'. Server-side gating is the real guard; this makes it
    obvious (no dead clicks). Runs after all server-side HTML transforms."""
    allow = "{" + ",".join(f"'{a}':1" for a in _RO_ALLOWED_ACTIONS) + "}"
    return (
        "<script>(function(){var A=" + allow + ";var T='Only admins can change this';"
        "function note(f){var n=document.createElement('div');"
        "n.className='text-[10px] font-semibold text-on-surface-variant/70 mt-1';"
        "n.textContent=T;f.appendChild(n);}"
        "document.querySelectorAll('form').forEach(function(f){"
        "var a=f.getAttribute('action')||'';if(A[a])return;"
        "f.querySelectorAll('input,select,textarea').forEach(function(el){el.disabled=true;el.title=T;});"
        "var btns=f.querySelectorAll('button,[type=submit]');"
        "btns.forEach(function(el){el.style.display='none';});"
        "if(btns.length)note(f);});"
        "document.querySelectorAll('[onchange]').forEach(function(el){"
        "if(/ccToggle|ccPol|ccScope|ccChan/.test(el.getAttribute('onchange')||'')){"
        "el.disabled=true;el.removeAttribute('onchange');el.title=T;}});"
        "document.querySelectorAll('[onclick]').forEach(function(el){"
        "var m=(el.getAttribute('onclick')||'').match(/ccSave\\('([^']+)'/);"
        "if(m&&!A[m[1]]){el.style.display='none';}});"
        "})();</script>"
    )


def _scanning_banner(started):
    when = f" (started {esc(started)} UTC)" if started else ""
    return ('<div class="flex items-center gap-2 rounded-lg border border-primary/30 '
            'bg-primary-container/40 text-on-surface px-4 py-2.5 text-xs">'
            '<span class="material-symbols-outlined text-base animate-spin select-none">progress_activity</span>'
            f'<span><b class="text-on-surface">Scan in progress…</b>{when} — this page refreshes '
            'automatically when it completes.</span></div>')


def _scanning_script():
    """Disable the scan triggers everywhere and poll until the scan finishes, then reload."""
    return (
        "<script>(function(){var T='Scan in progress…';"
        "document.querySelectorAll('form[action=\"/scan/run\"] button').forEach("
        "function(b){b.disabled=true;b.title=T;});"
        "var p=setInterval(function(){fetch('/scan/status',{credentials:'same-origin'})"
        ".then(function(r){return r.json();}).then(function(s){"
        "if(!s||!s.active){clearInterval(p);location.reload();}}).catch(function(){});},4000);"
        "})();</script>"
    )


def _scan_submit_lock():
    """Always on, NO polling (a one-time click listener — never keeps the instance warm):
    the moment ANY scan form is submitted, disable EVERY scan button on the page so a
    second scan can't be fired from the same page."""
    return (
        "<script>(function(){function lock(){"
        "document.querySelectorAll('form[action=\"/scan/run\"] button').forEach("
        "function(b){b.disabled=true;b.title='Scan in progress…';});}"
        "document.querySelectorAll('form[action=\"/scan/run\"]').forEach("
        "function(f){f.addEventListener('submit',lock);});})();</script>"
    )


def shell(html, active, project, auth, title=None):
    """Replace each screen's sidebar + header with ONE canonical shell; content stays."""
    from agents import scan_status
    html = re.sub(r"<header[^>]*>.*?</header>", "", html, count=1, flags=re.S)  # drop screen's header
    read_only = (auth or {}).get("role") != "admin"
    scan = scan_status.status()
    scanning = bool(scan.get("active"))
    canon = ('<body class="bg-background text-on-background min-h-screen flex antialiased">'
             + _sidebar(active, project)
             + '<main class="flex-1 flex flex-col min-w-0 overflow-y-auto">'
             + _header(active, auth, title)
             + '<div class="p-8 lg:p-12 flex-1 space-y-8">'
             + (_scanning_banner(scan.get("started", "")) if scanning else "")
             + (_read_only_banner() if read_only else ""))
    html = re.sub(r"<body[^>]*>.*?<main[^>]*>", lambda m: canon, html, count=1, flags=re.S)
    html = html.replace("</main>", "</div>" + _footer() + "</main>", 1)
    html = html.replace(" ml-64", "")
    if read_only:
        html += _read_only_script()
    html += _scan_submit_lock()      # always — cheap, no polling, blocks same-page double-fire
    if scanning:
        html += _scanning_script()   # poll only while a scan runs (instance already up)
    return html


def wire_scan_scope(html, project):
    """Turn the sidebar 'Scan Scope' button into a live status chip → links to Sources,
    showing the real selected/total project count."""
    sel, total = len(SourcesConfig().selected()), len(all_projects())
    count = f"{sel}/{total}"
    return re.sub(
        r'<button ([^>]*?)>((?:(?!</button>).)*?Scan Scope)\s*</button>',
        lambda m: f'<a href="/sources" {m.group(1)}>{m.group(2)} · {count}</a>',
        html, count=1, flags=re.S)


def _wire_save(html, label, action):
    """Turn a static Save button (matched by its label) into a JS save trigger."""
    return re.sub(
        r'<button ([^>]*?)>((?:(?!</button>).)*?' + re.escape(label) + r')',
        r'<button \1 type="button" onclick="ccSave(\'' + action + r'\', this)">\2',
        html, count=1, flags=re.S)


def render_login():
    """Wire login.html: email/password → POST /login; Google → POST /login (method=google).

    When Firebase is configured (FIREBASE_API_KEY env set), the same forms are
    intercepted client-side to sign in with Firebase and post the verified ID token to
    /auth/firebase. Without config, the dev login POST /login stays active.
    """
    html = _screen("login.html")
    html = html.replace('onsubmit="event.preventDefault();"', 'method="POST" action="/login"')
    html = html.replace('type="button"', 'type="submit"')  # the Google button becomes a submit
    html = re.sub(
        r'(<button class="w-full bg-surface-container-lowest.*?Sign in with Google\s*</button>)',
        r'<form method="POST" action="/login"><input type="hidden" name="method" value="google"/>\1</form>',
        html, count=1, flags=re.S)

    from webui import auth as fb
    cfg = fb.web_config()
    if cfg:
        script = (
            '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>'
            '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"></script>'
            '<script>firebase.initializeApp(' + json.dumps(cfg) + ');'
            'function ccErr(m){var e=document.getElementById("cc-login-err");'
            'if(!e){e=document.createElement("div");e.id="cc-login-err";'
            'e.className="mx-auto mb-4 max-w-sm rounded-lg border border-error/30 '
            'bg-error-container text-on-error-container px-4 py-3 text-sm text-center";'
            'var f=document.querySelector(\'form[action="/login"]\');'
            'if(f&&f.parentNode){f.parentNode.insertBefore(e,f);}else{document.body.prepend(e);}}'
            'e.textContent=m;e.style.display="block";e.scrollIntoView({block:"nearest"});}'
            'document.querySelectorAll(\'form[action="/login"]\').forEach(function(f){'
            'f.addEventListener("submit",async function(e){e.preventDefault();'
            'var pe=document.getElementById("cc-login-err");if(pe){pe.style.display="none";}'
            'var google=f.querySelector(\'input[name=method][value=google]\');try{var cred;'
            'if(google){cred=await firebase.auth().signInWithPopup(new firebase.auth.GoogleAuthProvider());}'
            'else{var em=(f.querySelector(\'input[type=email],input[name=email]\')||{}).value;'
            'var pw=(f.querySelector(\'input[type=password],input[name=password]\')||{}).value;'
            'cred=await firebase.auth().signInWithEmailAndPassword(em,pw);}'
            'var tok=await cred.user.getIdToken();'
            'var r=await fetch("/auth/firebase",{method:"POST",credentials:"same-origin",'
            'headers:{"Content-Type":"application/json"},body:JSON.stringify({idToken:tok})});'
            'if(r.ok){location.href="/";}else{var t=await r.text();'
            'ccErr((t||"").trim()||"Sign-in was rejected.");}'
            '}catch(err){if(err&&(err.code==="auth/popup-closed-by-user"'
            '||err.code==="auth/cancelled-popup-request"))return;'
            'ccErr((err&&err.message)||"Sign-in failed.");}});});</script>')
        html = html.replace("</body>", script + "</body>", 1)
    return html


def apply_auth(html, auth):
    """Inject the signed-in user/role, wire the role switcher + logout. Screens that
    lack the switcher (non-board) just get the name/role text updated."""
    role = auth.get("role", "operator")
    user = auth.get("user", "user@acme.com")
    initials = ("".join(w[0] for w in re.split(r"[@._ ]", user) if w)[:2].upper()) or "U"

    html = html.replace("Alex Rivera", esc(user))
    html = html.replace('mt-0.5">Admin</div>', f'mt-0.5">{esc(role.title())}</div>')
    html = html.replace('font-bold text-sm">AR</span>', f'font-bold text-sm">{esc(initials)}</span>')

    def btn(r):
        cls = ("bg-primary text-on-primary" if r == role
               else "text-on-surface-variant hover:bg-surface-variant transition-colors")
        return (f'<form method="POST" action="/assume-role" style="display:inline">'
                f'<input type="hidden" name="role" value="{r}"/>'
                f'<button class="px-3 py-1 rounded-md text-[10px] font-bold {cls}">{r.upper()}</button></form>')

    switcher_old = ('<div class="flex items-center gap-2 bg-surface-container px-2 py-1 rounded-lg '
                    'border border-outline-variant/20"><button class="px-3 py-1 rounded-md text-[10px] '
                    'font-bold bg-primary text-on-primary">ADMIN</button><button class="px-3 py-1 rounded-md '
                    'text-[10px] font-bold text-on-surface-variant hover:bg-surface-variant transition-colors">'
                    'OPERATOR</button></div>')
    switcher_new = ('<div class="flex items-center gap-2 bg-surface-container px-2 py-1 rounded-lg '
                    'border border-outline-variant/20">' + btn("admin") + btn("operator") + '</div>')
    html = html.replace(switcher_old, switcher_new)

    html = html.replace(
        '<button class="material-symbols-outlined text-on-surface-variant hover:text-error transition-colors p-2">logout</button>',
        '<a href="/logout" class="material-symbols-outlined text-on-surface-variant hover:text-error transition-colors p-2">logout</a>')
    return html

HERE = os.path.dirname(__file__)
BOARD_HTML = os.path.join(HERE, "screens", "board.html")
GROUND_TRUTH = os.path.join(HERE, "..", "eval", "ground_truth.json")

SEV = {
    "critical": "bg-error-container text-on-error-container border border-error/20",
    "high": "bg-tertiary-container/30 text-tertiary border border-tertiary/20",
    "medium": "bg-surface-variant text-on-surface-variant border border-outline-variant/30",
    "low": "bg-surface-variant text-on-surface-variant border border-outline-variant/30",
}


# --- data -------------------------------------------------------------------
def _last_scan():
    """The persisted result of the last authoritative scan (run_scan). The dashboard
    DISPLAYS this — it never re-runs the fleet, so delivery never duplicates and live
    findings show as scanned."""
    from agents.store import load_state
    return load_state("eval/last_scan.json", {})


def gather(project="demo-proj"):
    scan = _last_scan()
    fd = scan.get("findings", [])
    prs = scan.get("prs", [])
    pr_opened = sum(1 for r in prs if r.get("status") == "pr_opened")
    issues_opened = sum(1 for r in prs if r.get("status") in ("jira_issue", "ticket_opened"))
    savings = sum(f.get("est_monthly_savings_usd", 0) for f in fd)
    crit = sum(1 for f in fd if str(f.get("severity")) == "critical")
    high = sum(1 for f in fd if str(f.get("severity")) == "high")
    rc = estimate_runcost(len(fd), pr_opened)
    post = compliance_posture(fd)
    return {
        "n_findings": len(fd), "critical": crit, "high": high,
        "waste": f"${savings:,.0f}",
        "cost_run": f"${rc['monthly']:,.0f}",
        "cost_roi": f"{(savings / rc['monthly']) if rc['monthly'] else 0:.0f}×",
        "cost_perscan": f"${rc['per_scan']:.2f}",
        "compliance_cards": _compliance_cards(post),
        "pr_opened": pr_opened, "issues_opened": issues_opened,
        "fd": fd, "hist": FindingHistory(),
        "summary": scan.get("summary", ""), "reasoner": scan.get("reasoner", ""),
        "scan_ts": scan.get("scan_ts", ""), "mode": scan.get("mode", "mock"),
    }


# --- render fragments -------------------------------------------------------
def _compliance_cards(post):
    cards = ""
    for fw in FRAMEWORKS:
        p = post[fw]
        color = "text-primary" if p["failing"] == 0 else "text-tertiary" if p["score"] >= 0.5 else "text-error"
        cards += (
            '<div class="bg-surface p-4 rounded-xl border border-outline-variant/20 '
            'flex flex-col items-center justify-center text-center">'
            f'<div class="text-xs font-bold text-on-surface-variant uppercase mb-1">{esc(fw)}</div>'
            f'<div class="text-2xl font-headline font-bold {color}">{p["score"] * 100:.0f}%</div></div>'
        )
    return cards


_CAT_ICON = {"security": "shield", "iam": "key", "cost": "savings",
             "compliance": "verified_user", "inventory": "inventory_2"}


def _findings_rows(fd, hist):
    rows = ""
    for f in fd:
        md = f.get("metadata", {})
        sev = f["severity"]
        sevbadge = (f'<span class="px-2.5 py-1 rounded-md text-xs font-bold '
                    f'{SEV.get(sev, SEV["low"])}">{sev.upper()}</span>')

        rec = hist.get(f.get("fingerprint", "")) if hist else None
        life = ""
        if rec is None or rec.occurrences <= 1:
            life = ('<span class="px-1.5 py-0.5 rounded bg-primary text-on-primary '
                    'text-[9px] font-black uppercase">NEW</span>')
        elif rec.reopen_count > 0:
            life = ('<span class="px-1.5 py-0.5 rounded bg-tertiary text-on-tertiary '
                    'text-[9px] font-black uppercase">REOPENED</span>')

        cat = f.get("category", "")
        cat_cell = ('<span class="inline-flex items-center gap-1.5 whitespace-nowrap">'
                    f'<span class="material-symbols-outlined text-lg text-on-surface-variant">{_CAT_ICON.get(cat, "label")}</span>'
                    f'<span class="capitalize">{esc(cat)}</span></span>')

        res = esc(f["resource"])
        res_cell = f'<span class="block max-w-[12rem] truncate font-mono text-sm" title="{res}">{res}</span>'

        title_cell = (f'<div class="flex items-center gap-2 max-w-[18rem]"><span class="truncate" '
                      f'title="{esc(f["title"])}">{esc(f["title"])}</span>{life}</div>')

        sv = f.get("est_monthly_savings_usd", 0)
        savings_cell = (f'<span class="text-tertiary font-bold">${sv:,.0f}</span>' if sv
                        else '<span class="text-on-surface-variant/50">—</span>')

        # Merged ownership: managed → owning repo, unmanaged → ClickOps, conflict → multi-state.
        ostatus = md.get("ownership_status")
        if ostatus == "managed":
            own_cell = (f'<span title="managed by {esc(md.get("owner_repo",""))} · {esc(md.get("tf_address",""))}" '
                        'class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-primary-container/40 '
                        'text-primary border border-primary/20 max-w-[13rem]">'
                        '<span class="material-symbols-outlined text-sm">verified</span>'
                        f'<span class="truncate">{esc(md.get("owner_repo",""))}</span></span>')
        elif ostatus == "conflict":
            own_cell = ('<span class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs '
                        'bg-error-container/50 text-error border border-error/20 whitespace-nowrap">'
                        '<span class="material-symbols-outlined text-sm">error</span>Conflict · multi-state</span>')
        else:
            own_cell = ('<span class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs '
                        'bg-surface-variant text-on-surface-variant border border-outline-variant/30 whitespace-nowrap">'
                        '<span class="material-symbols-outlined text-sm">pan_tool</span>Unmanaged · ClickOps</span>')

        if ostatus == "managed":
            own_text = f"Managed · {md.get('owner_repo','')}"
        elif ostatus == "conflict":
            own_text = "Conflict · multiple Terraform states"
        else:
            own_text = "Unmanaged · ClickOps (in no Terraform state)"

        fp = esc(f.get("fingerprint", ""))
        # Hover-preview data (IDE-style popover on the Finding ID — see the JS in render_board).
        save_txt = f"${sv:,.0f}/mo" if sv else ""
        action_txt = (f.get("recommended_action") or "")[:220]
        id_link = (f'<a href="/finding?fp={fp}" class="cc-fid text-primary font-mono text-sm hover:underline" '
                   f'data-title="{esc(f["title"])}" data-sev="{esc(sev)}" data-cat="{esc(cat)}" '
                   f'data-res="{res}" data-save="{esc(save_txt)}" data-own="{esc(own_text)}" '
                   f'data-action="{esc(action_txt)}">{fp}</a>')
        accept = (
            '<form method="POST" action="/suppress" class="flex justify-end items-center gap-2 '
            'opacity-0 group-hover:opacity-100 transition-opacity">'
            f'<input type="hidden" name="fingerprint" value="{fp}"/>'
            f'<input type="hidden" name="resource" value="{res}"/>'
            '<select name="duration" class="bg-surface border-outline-variant/30 text-on-surface text-xs rounded-lg py-1 pl-2 pr-6">'
            '<option value="forever">forever</option><option value="month">30 days</option>'
            '<option value="week">7 days</option></select>'
            '<input name="reason" class="bg-surface border-outline-variant/30 text-on-surface text-xs '
            'rounded-lg py-1 px-2 w-24" placeholder="reason" type="text"/>'
            f'<button type="submit" class="{BTN_2ND} px-3 py-1 text-xs">Accept</button></form>')

        rows += (
            '<tr class="hover:bg-surface-container-lowest transition-colors group">'
            f'<td class="px-3 py-2.5 whitespace-nowrap">{id_link}</td>'
            f'<td class="px-3 py-2.5">{sevbadge}</td>'
            f'<td class="px-3 py-2.5 text-on-surface">{cat_cell}</td>'
            f'<td class="px-3 py-2.5 text-on-surface">{res_cell}</td>'
            f'<td class="px-3 py-2.5 text-on-surface">{title_cell}</td>'
            f'<td class="px-3 py-2.5">{savings_cell}</td>'
            f'<td class="px-3 py-2.5">{own_cell}</td>'
            f'<td class="px-3 py-2.5 text-right">{accept}</td></tr>')
    return rows


def _fid_tooltip():
    """IDE-style hover popover for a Finding ID — a single floating card that reads the
    hovered link's data-* attributes and shows a preview (title, severity, resource,
    ownership, recommended action) + a clickable link to the full finding. Interactive
    (a short hover-bridge keeps it open while you move into it). Text-escaped in JS."""
    return """
<div id="cc-fidtip" class="hidden fixed z-50 w-[30rem] max-w-[94vw] bg-surface border border-[#e0dbd1] rounded-lg shadow-xl p-5"></div>
<script>(function(){
  var SEV={critical:"bg-error-container text-on-error-container",high:"bg-tertiary-container text-on-tertiary-container",medium:"bg-surface-variant text-on-surface-variant",low:"bg-surface-variant text-on-surface-variant"};
  var tip=document.getElementById("cc-fidtip"); if(!tip) return;
  var timer=null;
  function esc(s){var d=document.createElement("div");d.textContent=s||"";return d.innerHTML;}
  function place(el){
    var r=el.getBoundingClientRect(), tw=tip.offsetWidth, th=tip.offsetHeight, m=12;
    var left=r.right+10; if(left+tw>window.innerWidth-m) left=r.left-tw-10; if(left<m) left=m;
    var top=r.bottom+8;                                  // prefer below the ID
    if(top+th>window.innerHeight-m) top=r.top-th-8;      // no room below → flip above
    if(top<m) top=window.innerHeight-th-m;               // still off → pin fully in view
    if(top<m) top=m;
    tip.style.left=left+"px"; tip.style.top=top+"px";
  }
  function show(el){
    var d=el.dataset, sev=(d.sev||"").toLowerCase();
    var h="<div class='font-semibold text-on-surface mb-1.5 leading-snug'>"+esc(d.title)+"</div>";
    h+="<div class='flex items-center gap-2 mb-2'>";
    h+="<span class='px-2 py-0.5 rounded text-xs font-bold uppercase "+(SEV[sev]||SEV.low)+"'>"+esc(d.sev)+"</span>";
    h+="<span class='text-xs text-on-surface-variant capitalize'>"+esc(d.cat)+"</span>";
    if(d.save)h+="<span class='ml-auto text-tertiary font-bold text-sm'>"+esc(d.save)+"</span>";
    h+="</div>";
    h+="<div class='font-mono text-xs text-on-surface-variant break-all mb-2'>"+esc(d.res)+"</div>";
    h+="<div class='text-sm text-on-surface mb-2'><span class='font-semibold'>Ownership:</span> "+esc(d.own)+"</div>";
    if(d.action)h+="<div class='text-sm text-on-surface-variant border-t border-outline-variant/40 pt-2 mb-3 leading-relaxed'>"+esc(d.action)+"</div>";
    h+="<a href='"+esc(el.getAttribute('href'))+"' class='inline-flex items-center gap-1 text-sm font-bold text-primary hover:underline'>Open full finding &rarr;</a>";
    tip.innerHTML=h; tip.classList.remove("hidden"); place(el);
  }
  function hide(){tip.classList.add("hidden");}
  function later(){timer=setTimeout(hide,220);}
  function keep(){if(timer){clearTimeout(timer);timer=null;}}
  tip.addEventListener("mouseenter",keep);
  tip.addEventListener("mouseleave",later);
  document.querySelectorAll(".cc-fid").forEach(function(el){
    el.addEventListener("mouseenter",function(){keep();show(el);});
    el.addEventListener("mouseleave",later);
  });
})();</script>"""


# --- assemble ---------------------------------------------------------------
def _base_page(content):
    head = open(BOARD_HTML).read()
    head = head[:head.index("<body")]
    return (head + '<body class="bg-background text-on-background min-h-screen flex antialiased">'
            '<aside></aside><main class="flex-1"><header></header>' + content + "</main></body></html>")


def _sev_badge(sev):
    colors = {"critical": "bg-error-container text-on-error-container", "high": "bg-tertiary-container text-on-tertiary-container",
              "medium": "bg-surface-variant text-on-surface-variant", "low": "bg-surface-variant text-on-surface-variant"}
    return (f'<span class="px-2 py-0.5 rounded text-[10px] font-bold uppercase {colors.get(sev, colors["low"])}">{esc(sev)}</span>')


def _card(title, inner):
    return ('<section class="bg-surface border border-outline-variant/40 rounded-xl overflow-hidden">'
            '<div class="px-5 py-3 border-b border-outline-variant/30 bg-surface-container-low">'
            f'<h2 class="text-xs font-bold uppercase tracking-wider text-on-surface-variant">{esc(title)}</h2></div>'
            f'<div class="p-5">{inner}</div></section>')


def _kv(k, v, stack=False):
    return ('<div class="flex justify-between gap-4 py-2 border-b border-outline-variant/10 last:border-0">'
            f'<span class="text-on-surface-variant text-base">{esc(k)}</span>'
            f'<span class="text-base text-on-surface text-right break-words">{v}</span></div>')


def render_finding(project, fp):
    from agents.remediation.pr_channel import propose
    all_fd = _findings_dicts(project)
    f = next((x for x in all_fd if x.get("fingerprint") == fp), None)
    if not f:
        return _base_page('<div><a href="/board" class="text-sm text-primary hover:underline">← Back to findings</a>'
                          f'<p class="mt-4 text-on-surface-variant text-sm">Finding <span class="font-mono">{esc(fp)}</span> '
                          'is no longer open (resolved or accepted as an exception).</p></div>')
    md = f.get("metadata", {})
    rec = FindingHistory().get(fp)
    plan = propose(f)
    sv = f.get("est_monthly_savings_usd", 0)

    # Proof
    proof = _kv("Recommended action", esc(f.get("recommended_action", "—")), stack=False)
    proof += _kv("Est. monthly impact", f'<span class="text-tertiary font-bold">${sv:,.0f}</span>' if sv else "—", stack=False)
    proof += _kv("Evidence", "Deterministic (Recommender + utilization) · peak utilization low over window", stack=False)
    proof += ('<p class="text-sm text-on-surface-variant mt-3">The LLM explains this evidence; it does not '
              'assert the conclusion. Production proof uses the 90-day trend.</p>')

    # Remediation
    if plan is None:
        remed = '<p class="text-sm text-on-surface-variant">Alert-only — no code remediation (e.g. blocked injection).</p>'
    else:
        code = plan.diff or "\n".join(f"# {n}\n{c}" for n, c in plan.files.items())
        if plan.commands:
            code += "\n\n" + "\n".join(plan.commands)
        q = plan.quarantine
        remed = (f'<div class="flex items-center gap-2 mb-3"><span class="px-2 py-0.5 rounded bg-primary-container '
                 f'text-on-primary-container text-[10px] font-bold uppercase">{esc(plan.change_kind)}</span>'
                 f'<span class="font-mono text-sm text-on-surface-variant">{esc(plan.branch)}</span></div>'
                 f'<pre class="font-mono text-sm leading-relaxed bg-background border border-outline-variant/40 '
                 f'rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-on-surface">{esc(code)}</pre>')
        if q:
            remed += ('<div class="mt-3 text-sm text-on-surface-variant"><b>Quarantine-first:</b> '
                      f'{esc(q["reversible"])} → soak → {esc(q["terminal"])}</div>')
        remed += ('<p class="text-sm text-on-surface-variant mt-3">Delivered as a human-approved PR. '
                  'The agent has <b>no cloud write access</b>.</p>')
        # If this scan actually opened a PR for this finding, link to it (deterministic evidence).
        from agents.store import load_state as _ls
        _prs = (_ls("eval/last_scan.json", {}) or {}).get("prs", [])
        _d = next((p for p in _prs if p.get("fingerprint") == fp
                   and p.get("status") == "pr_opened" and p.get("url")), None)
        if _d:
            remed += (f'<a href="{esc(_d["url"])}" target="_blank" rel="noopener" '
                      'class="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-on-primary '
                      'text-sm font-bold hover:bg-primary/90"><span class="material-symbols-outlined text-base">'
                      f'merge</span>View Pull Request #{esc(str(_d.get("number","")))} on GitHub</a>'
                      f'<div class="text-xs text-on-surface-variant mt-1 font-mono">{esc(_d.get("repo",""))} · '
                      f'{esc(", ".join(_d.get("files", [])))}</div>')

    # Change-freeze: if the project is frozen, remediation is HELD (report-only, no PR).
    from datetime import date as _date

    from agents.freeze import FreezeStore
    _fz = FreezeStore().active(md.get("project", ""), _date.today())
    if _fz:
        remed = (
            '<div class="mb-3 rounded-lg border border-tertiary/40 bg-tertiary-container/30 p-3">'
            '<div class="flex items-center gap-2 text-sm font-bold text-tertiary">'
            '<span class="material-symbols-outlined text-base">ac_unit</span>'
            f'Change freeze until {esc(_fz.get("until") or "further notice")}</div>'
            f'<p class="text-sm text-on-surface mt-1">{esc(_fz.get("reason", ""))}</p>'
            '<p class="text-xs text-on-surface-variant mt-1">Remediation is <b>held</b> — no PR, no branch. '
            'CloudCap reports only (Issue/Slack) during the freeze; accept as an exception to re-review later.</p>'
            '</div>') + remed

    # Overview
    src = md.get("management_source", "unknown")
    src_v = '<span class="text-primary font-semibold">UNMANAGED · ClickOps</span>' if src == "unmanaged" else esc(src)
    overview = (_kv("Resource", f'<span class="font-mono">{esc(f["resource"])}</span>') + _kv("Category", esc(f["category"]))
                + _kv("Severity", _sev_badge(f["severity"])) + _kv("Management", src_v))

    # Attribution
    attribution = ""
    if src == "unmanaged":
        attribution = _card("Attribution", _kv("Created by", f'<span class="font-mono">{esc(str(md.get("created_by")))}</span>')
                            + _kv("Real actor", esc(str(md.get("triggering_entity"))))
                            + _kv("Confidence", esc(str(md.get("attribution_confidence")))))

    # IaC ownership
    ost = md.get("ownership_status", "unknown")
    if ost == "managed":
        iac = _kv("Status", "managed") + _kv("Repo", f'<span class="font-mono">{esc(str(md.get("owner_repo")))}</span>') + _kv("Address", f'<span class="font-mono">{esc(str(md.get("tf_address")))}</span>')
    elif ost == "conflict":
        cands = ", ".join(c.get("repo", "") for c in md.get("owner_candidates", []))
        iac = _kv("Status", '<span class="text-error font-semibold">CONFLICT — multi-state</span>') + _kv("Claimed by", esc(cands))
    else:
        iac = _kv("Status", "unmanaged (no IaC) → codify-then-PR")

    # Compliance
    ctrls = md.get("controls")
    comp = "".join(_kv(fw, (f'<span class="font-mono">{esc(ctrls[fw])}</span>' if ctrls.get(fw)
                            else '<span class="text-on-surface-variant">—</span>')) for fw in FRAMEWORKS) if ctrls else '<p class="text-sm text-on-surface-variant">Not mapped to a control.</p>'
    if ctrls:
        comp = f'<p class="text-sm font-semibold mb-2">{esc(ctrls["name"])}</p>' + comp

    # Related findings — other findings that trip the SAME control
    rule = compliance_rule_for(f)
    siblings = [x for x in all_fd if x.get("fingerprint") != fp and compliance_rule_for(x) == rule] if rule else []
    related = ""
    if siblings:
        items = "".join(
            f'<a href="/finding?fp={esc(s.get("fingerprint", ""))}" class="flex justify-between items-center py-1.5 '
            'border-b border-outline-variant/10 last:border-0 hover:bg-surface-container-low">'
            f'<span class="font-mono text-sm text-primary hover:underline">{esc(s["resource"])}</span>'
            f'<span class="font-mono text-xs text-on-surface-variant">{esc(s.get("fingerprint", ""))}</span></a>'
            for s in siblings)
        related = _card(f"Related findings — same control ({len(siblings)})", items)

    # Lifecycle
    if rec:
        life = (_kv("State", esc(rec.state)) + _kv("First seen", (esc(rec.first_seen) + " UTC") if rec.first_seen else "—") + _kv("Last seen", (esc(rec.last_seen) + " UTC") if rec.last_seen else "—")
                + _kv("Occurrences", str(rec.occurrences)) + _kv("Reopens", str(rec.reopen_count)))
    else:
        life = _kv("State", "new (this scan)")

    accept = _card("Accept as exception (TTL)",
                   '<form method="POST" action="/suppress" class="flex flex-col gap-2">'
                   f'<input type="hidden" name="fingerprint" value="{esc(fp)}"/><input type="hidden" name="resource" value="{esc(f["resource"])}"/>'
                   '<select name="duration" class="bg-background border border-outline-variant/40 text-on-surface text-sm rounded-lg px-2 py-1.5"><option value="forever">forever</option><option value="month">30 days</option><option value="week">7 days</option></select>'
                   '<input name="reason" class="bg-background border border-outline-variant/40 text-on-surface text-sm rounded-lg px-2 py-1.5" placeholder="reason (e.g. compliance runner)" type="text"/>'
                   '<button type="submit" class="bg-primary text-on-primary text-sm font-bold rounded-lg px-3 py-2 hover:bg-primary/90">Accept</button></form>')

    # Right panel: the detail cards, balanced across two equal 25% columns
    # (main content gets the left 50%).
    right_items = [_card("Overview", overview)]
    if attribution:
        right_items.append(attribution)
    right_items.append(_card("IaC ownership", iac))
    right_items.append(_card("Compliance controls", comp))
    if related:
        right_items.append(related)
    right_items.append(_card("Lifecycle", life))
    right_items.append(accept)
    half = (len(right_items) + 1) // 2
    col_a = "".join(right_items[:half])
    col_b = "".join(right_items[half:])

    rank = md.get("priority_rank")
    rank_badge = (f'<span class="px-2 py-0.5 rounded text-xs font-bold bg-primary-container text-on-primary-container" '
                  f'title="Reasoner priority (1 = act first)">PRIORITY #{rank}</span>') if rank else ""
    rationale = md.get("priority_rationale", "")
    rationale_row = (
        '<div class="flex items-start gap-2 mb-6 bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-3">'
        '<span class="material-symbols-outlined text-primary text-lg">neurology</span>'
        f'<p class="text-sm text-on-surface leading-relaxed">{esc(rationale)}</p></div>') if rationale else ""

    content = (
        '<div class="mb-4"><a href="/board" class="text-sm text-primary hover:underline">← Back to findings</a></div>'
        '<div class="flex flex-wrap items-center gap-3 mb-4">'
        f'<span class="font-mono text-sm text-on-surface-variant">{esc(fp)}</span>{_sev_badge(f["severity"])}{rank_badge}'
        f'<h2 class="text-lg font-bold text-on-surface">{esc(f["title"])}</h2></div>'
        + rationale_row +
        '<div class="grid grid-cols-1 lg:grid-cols-4 gap-6">'
        '<div class="lg:col-span-2 space-y-6">' + _card("Proof", proof) + _card("Remediation", remed) + '</div>'
        '<div class="lg:col-span-1 space-y-6">' + col_a + '</div>'
        '<div class="lg:col-span-1 space-y-6">' + col_b + '</div>'
        '</div>')
    return _base_page(content)


def render_hub(project="demo-proj"):
    """The Hub — every component of the governance platform, its adapter, and status.
    This is the GEAP 'Agent Registry / system' view: what's running, live vs mock."""
    import os as _os

    from webui import auth as _wa
    gemini = _os.environ.get("CLOUDCAP_GEMINI", "").lower() in ("1", "true", "yes")
    live_mode = _os.environ.get("CLOUDCAP_SCAN_MODE", "mock").lower() == "live"
    store_fs = _os.environ.get("CLOUDCAP_STORE", "local").lower() == "firestore"
    audit_cloud = _os.environ.get("CLOUDCAP_AUDIT", "file").lower() == "cloud"
    _model = _os.environ.get("CLOUDCAP_GEMINI_MODEL", "gemini-3.7-flash")
    reasoner = f"{_model} · Vertex AI" if gemini else "Deterministic (mock)"
    prov = _wa.provider()
    L = "live" if live_mode else "mock"  # scanners follow the deployment's data mode

    # Agent Registry pillar — the fleet registered by the last scan (Firestore),
    # each agent verified against its real GCP service account.
    from agents.store import load_state as _ls
    _reg = _ls("agent_registry", {}) or {}
    _reg_agents = list(_reg.values())
    _reg_verified = sum(1 for a in _reg_agents if a.get("identity_verified"))

    # Ownership pillar — live Terraform-state resolution (managed vs ClickOps) from the
    # last scan's findings. LIVE when a GCS state backend is registered.
    _tf_sources = bool(_os.environ.get("CLOUDCAP_TFSTATE_SOURCES"))
    _last = _ls("last_scan", {}) or {}
    _own = [(f.get("metadata", {}) or {}).get("ownership_status") for f in _last.get("findings", [])]
    _managed = sum(1 for s in _own if s == "managed")
    _unmanaged = sum(1 for s in _own if s == "unmanaged")

    def dot(state):
        c = {"live": "primary", "mock": "tertiary", "ready": "primary", "off": "on-surface-variant"}.get(state, "primary")
        return f'<span class="inline-flex items-center gap-1.5"><span class="w-1.5 h-1.5 rounded-full bg-{c}"></span>{esc(state.upper())}</span>'

    summary = (
        '<section class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">'
        + "".join(f'<div class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-4">'
                  f'<div class="text-[11px] uppercase tracking-wider text-on-surface-variant">{esc(k)}</div>'
                  f'<div class="text-sm font-bold text-on-surface mt-1">{v}</div></div>'
                  for k, v in [("Hub project", "cloud-cap-506110"), ("Reasoner", esc(reasoner)),
                               ("Auth provider", esc(prov)), ("Storage", "Firestore / local")])
        + "</section>")

    # (name, adapter, pillar/detail, status)
    agents = [
        ("Orchestrator", "reasons + ranks over tool output", reasoner, "live" if gemini else "mock"),
        ("Cost Scanner", "Recommender + Cloud Run utilization + Monitoring", "cost waste (real usage)", L),
        ("Security Scanner", "Cloud Asset Inventory — public-exposure posture", "public access", L),
        ("IAM Scanner", "Asset Inventory + IAM Recommender", "primitive roles, over-privilege", L),
        ("Compliance Mapper", "CIS GCP · SOC 2 · ISO 27001 · PCI DSS", "control posture (real findings)", "live" if live_mode else "ready"),
        ("Remediation (GitOps)", "JIRA live · PR / Slack human-gated, no cloud write", "change-freeze aware", "ready"),
    ]
    ports = [
        ("Gateway", "GEAP Agent Gateway", "read-only allowlist enforced at runtime", L),
        ("Observability", "Cloud Logging", "hash-chained tamper-evident audit", "live" if audit_cloud else "ready"),
        ("Classifier", "Cloud Asset Inventory labels", "IaC-managed vs ClickOps", L),
        ("Memory", "finding-lifecycle store (Firestore)", "cross-scan new / recurring / resolved", "ready" if store_fs else "mock"),
        ("Identity", "runtime service account (cc-runtime)", "least-privilege, read-only", "ready"),
        ("Registry", "GEAP Agent Registry (Firestore)",
         (f"{len(_reg_agents)} agents · {_reg_verified} identities verified" if _reg_agents
          else "publish/discover; identities verified vs live SAs"),
         ("live" if _reg_agents and store_fs else ("ready" if _reg_agents else "mock"))),
        ("Guardrail", "Model Armor", "prompt-injection / tool-poisoning screen (+ deterministic backstop)", L),
        ("Ownership", "Terraform state resolver (GCS)",
         (f"{_managed} managed · {_unmanaged} unmanaged (from real TF state)" if _tf_sources and (_managed or _unmanaged)
          else "resource → owning repo / TF address, or ClickOps"),
         ("live" if _tf_sources else "mock")),
    ]

    def table(title, rows, c1):
        body = "".join(
            '<tr class="hover:bg-surface-container-low">'
            f'<td class="px-3 py-2 font-semibold text-on-surface">{esc(n)}</td>'
            f'<td class="px-3 py-2 text-on-surface-variant">{esc(a)}</td>'
            f'<td class="px-3 py-2 text-on-surface-variant">{esc(d)}</td>'
            f'<td class="px-3 py-2 text-xs font-bold text-on-surface-variant">{dot(s)}</td></tr>'
            for n, a, d, s in rows)
        return (
            '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm overflow-hidden mb-6">'
            f'<div class="px-4 py-2.5 border-b border-outline-variant/50"><h2 class="text-xs font-bold uppercase '
            f'tracking-wider text-on-surface-variant">{esc(title)}</h2></div>'
            '<div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead>'
            '<tr class="bg-surface-container text-xs uppercase tracking-wider text-on-surface-variant '
            f'border-b border-outline-variant/60"><th class="px-3 py-2">{esc(c1)}</th>'
            '<th class="px-3 py-2">Adapter</th><th class="px-3 py-2">Detail</th><th class="px-3 py-2">Status</th></tr>'
            f'</thead><tbody class="divide-y divide-outline-variant/60">{body}</tbody></table></div></section>')

    note = ('<p class="text-xs text-on-surface-variant">LIVE = calling a Google Cloud API in this deployment · '
            'READY = real logic on real persisted state (not a managed API) · MOCK = deterministic stand-in that '
            'swaps to the live Google adapter one line at a time (hexagonal ports). No agent logic changes between them.</p>')

    # Agent Registry detail — the concrete published fleet + live identity verification.
    registry_section = ""
    if _reg_agents:
        _order = {"orchestrator": 0, "cost_scanner": 1, "security_scanner": 2,
                  "iam_scanner": 3, "compliance_scanner": 4, "remediation": 5}
        rows = ""
        for a in sorted(_reg_agents, key=lambda x: _order.get(x.get("name"), 9)):
            sa = a.get("identity_sa") or "— (no standing identity · PR-brokered)"
            ok = a.get("identity_verified")
            badge = ('<span class="inline-flex items-center gap-1 text-primary font-semibold">'
                     '<span class="material-symbols-outlined text-base">verified</span>verified</span>') if ok else \
                    '<span class="text-error font-semibold">unverified</span>'
            caps = ", ".join(a.get("capabilities", []))
            depts = " ".join(f'<span class="px-2 py-0.5 rounded bg-surface-container text-xs '
                             f'text-on-surface-variant">{esc(d)}</span>' for d in a.get("departments", []))
            name = a.get("name", "")
            logs = (f'<a href="/logs?agent={esc(name)}" class="inline-flex items-center gap-1 text-primary '
                    'hover:underline text-sm" title="View this agent\'s Cloud Logging audit events">'
                    '<span class="material-symbols-outlined text-base">receipt_long</span>logs</a>')
            rows += ('<tr class="hover:bg-surface-container-low">'
                     f'<td class="px-3 py-3 font-semibold text-base text-on-surface">{esc(name)}</td>'
                     f'<td class="px-3 py-3 text-sm text-on-surface-variant">v{esc(a.get("version",""))}</td>'
                     f'<td class="px-3 py-3 text-sm text-on-surface-variant">{depts}</td>'
                     f'<td class="px-3 py-3 text-sm text-on-surface-variant">{esc(caps)}</td>'
                     f'<td class="px-3 py-3 text-sm font-mono text-on-surface-variant">{esc(sa)}</td>'
                     f'<td class="px-3 py-3 text-sm font-bold">{badge}</td>'
                     f'<td class="px-3 py-3">{logs}</td></tr>')
        registry_section = (
            '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm overflow-hidden mb-6">'
            '<div class="px-4 py-3 border-b border-outline-variant/50 flex items-center justify-between">'
            '<h2 class="text-sm font-bold uppercase tracking-wider text-on-surface-variant">Agent Registry '
            '· published fleet</h2>'
            f'<span class="text-sm text-on-surface-variant">{len(_reg_agents)} agents · '
            f'{_reg_verified} identities verified against live GCP service accounts</span></div>'
            '<div class="overflow-x-auto"><table class="w-full text-left text-base"><thead>'
            '<tr class="bg-surface-container text-sm uppercase tracking-wider text-on-surface-variant '
            'border-b border-outline-variant/60"><th class="px-3 py-3">Agent</th><th class="px-3 py-3">Version</th>'
            '<th class="px-3 py-3">Departments</th><th class="px-3 py-3">Capabilities</th>'
            '<th class="px-3 py-3">Identity (service account)</th><th class="px-3 py-3">Verified</th>'
            '<th class="px-3 py-3">Logs</th></tr>'
            f'</thead><tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div></section>')

    return _base_page(summary + table("Agents (the fleet)", agents, "Agent")
                      + table("Platform (GEAP pillars / ports)", ports, "Port")
                      + registry_section + note)


def _metric(label, value):
    return (f'<div><div class="text-on-surface-variant text-xs">{esc(label)}</div>'
            f'<div class="font-bold text-base text-on-surface">{value}</div></div>')


def render_not_ready(auth=None):
    """Shown to non-admins (and any pre-first-scan visitor who can't act) — the hub
    exists but has no scan yet, so there is nothing real to show."""
    who = (auth or {}).get("name", "there")
    inner = (
        '<div class="min-h-[60vh] flex flex-col items-center justify-center text-center">'
        '<div class="h-16 w-16 rounded-full bg-surface-container-highest flex items-center justify-center mb-5">'
        '<span class="material-symbols-outlined text-on-surface-variant text-3xl">hourglass_empty</span></div>'
        f'<h2 class="text-xl font-bold text-on-surface">Setting things up, {esc(who)}</h2>'
        '<p class="text-sm text-on-surface-variant mt-2 max-w-md">An administrator is configuring CloudCap '
        'and will run the first governance scan. There are no findings to show yet — check back shortly.</p>'
        '<a href="/logout" class="mt-6 text-sm text-primary hover:underline">Sign out</a></div>')
    return _base_page(inner)


def _doc_section(anchor, icon, title, body, badge=""):
    """One documentation card with a scroll anchor and an optional status pill."""
    return (
        f'<section id="{anchor}" class="scroll-mt-24 bg-surface border border-outline-variant/40 rounded-xl overflow-hidden">'
        '<div class="px-5 py-3 border-b border-outline-variant/30 bg-surface-container-low flex items-center gap-3">'
        f'<span class="material-symbols-outlined text-primary">{icon}</span>'
        f'<h2 class="text-sm font-bold text-on-surface">{esc(title)}</h2>{badge}</div>'
        f'<div class="p-5 space-y-3 text-sm text-on-surface-variant leading-relaxed">{body}</div></section>')


def _doc_pill(text, tone="ready"):
    tones = {
        "live": "bg-primary-container text-on-primary-container",
        "ready": "bg-tertiary-container text-on-tertiary-container",
        "report": "bg-surface-container-highest text-on-surface-variant",
    }
    return f'<span class="ml-auto px-2 py-0.5 rounded text-[10px] font-bold uppercase {tones.get(tone, tones["ready"])}">{esc(text)}</span>'


def _doc_table(rows, headers=("Setting", "Where it lives", "Notes")):
    head = "".join(f'<th class="text-left font-semibold text-on-surface px-3 py-2.5">{esc(h)}</th>' for h in headers)
    body = ""
    for r in rows:
        cells = "".join(f'<td class="px-3 py-2.5 align-top border-t border-outline-variant/20">{c}</td>' for c in r)
        body += f"<tr>{cells}</tr>"
    return ('<div class="overflow-x-auto rounded-lg border border-outline-variant/30">'
            f'<table class="w-full text-base"><thead class="bg-surface-container-low"><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def render_docs(project="demo-proj", topic=None):
    """In-app Documentation & Support (sidebar 'Docs' / 'Support' links point here).
    An INDEX of topics, each opening on its own page (/docs?topic=<slug>). Content mirrors
    docs/INTEGRATIONS.md in the repo — one source of truth, two surfaces."""
    _code = 'font-mono text-[13px] bg-surface-container-highest px-1.5 py-0.5 rounded text-on-surface'

    intro = (
        '<p>CloudCap is a <b class="text-on-surface">read-only</b> governance fleet. It connects to your '
        'GCP projects through a least-privilege runtime service account and never mutates cloud state — '
        'remediation is delivered as a <b class="text-on-surface">proposal</b> (a Pull Request, ticket, or '
        'notification) that a human approves. Every token you configure is written to '
        f'<span class="{_code}">Secret Manager</span>, never to the repo, Firestore, or Terraform vars.</p>')

    gcp = (
        '<p>The scanners read live GCP data through Google APIs using the runtime service account '
        f'<span class="{_code}">cc-runtime</span>. All roles are viewer/read-only:</p>'
        + _doc_table([
            ('Cost', 'Recommender API + Cloud Monitoring (CPU)', 'roles/recommender.viewer · monitoring.viewer'),
            ('Security', 'Cloud Asset Inventory (public IAM)', 'roles/cloudasset.viewer'),
            ('IAM', 'Asset Inventory + IAM Recommender', 'roles/iam.securityReviewer · recommender.iamViewer'),
            ('Inventory / IaC', 'Asset Inventory resource labels', 'roles/cloudasset.viewer · browser'),
            ('Audit trail', 'Cloud Logging (immutable, hash-chained)', 'write-only to its own log'),
        ], headers=("Pillar", "GCP source", "Role granted"))
        + '<p class="text-sm">Grant these to <span class="' + _code + '">cc-runtime</span> on any project you '
        'add to Scan Scope; no API keys are ever used.</p>')

    jira = (
        '<p>The <b class="text-on-surface">Issue</b> remediation action files each finding into a JIRA project '
        'you choose. Configure it on <a href="/sources" class="text-primary hover:underline">Sources → per-project</a> '
        'or globally:</p>'
        + _doc_table([
            ('Base URL', 'State store', f'e.g. <span class="{_code}">https://yourco.atlassian.net</span>'),
            ('Email', 'State store', 'the Atlassian account the token belongs to'),
            ('Project key', 'State store', f'e.g. <span class="{_code}">SEC</span> or <span class="{_code}">OPS</span>'),
            ('GCP project field', 'State store (optional)', 'custom field id to stamp the GCP project; else a label'),
            ('API token', 'Secret Manager only', f'read from <span class="{_code}">JIRA_API_TOKEN</span>, never persisted'),
        ])
        + '<p>Each issue is labelled with the GCP project, service, control name, every mapped framework, and the '
        'category. If JIRA isn’t configured, CloudCap falls back to a local ticket artifact so nothing is lost.</p>')

    channels = (
        '<p>Notifications have two parts. <b class="text-on-surface">Endpoints</b> (the addresses / webhook '
        'URLs) are configured <b class="text-on-surface">per project on the Sources page</b> — click '
        '<span class="' + _code + '">Set&nbsp;up</span> on a project. <b class="text-on-surface">Which channels '
        'fire</b> is toggled per project on the <a href="/policy" class="text-primary hover:underline">Policy</a> '
        'page. Any secret (bot token / routing key) goes to Secret Manager, never the repo.</p>'
        + _doc_table([
            ('Slack', 'Incoming webhook URL', 'delivery-wired · toggle on Policy'),
            ('Email', 'Address', 'delivery-wired · toggle on Policy'),
            ('MS Teams', 'Incoming webhook URL', 'configurable endpoint · delivery on roadmap'),
            ('PagerDuty', 'Routing (integration) key', 'configurable endpoint · delivery on roadmap'),
            ('Webhook', 'POST URL', 'configurable endpoint · delivery on roadmap'),
        ], headers=("Channel", "Endpoint (Sources)", "Status"))
        + '<p><b class="text-on-surface">Slack</b> and <b class="text-on-surface">Email</b> are the '
        'delivery-wired alert channels today (they emit a delivery event per finding, gated by the project '
        'policy). Teams, PagerDuty, and Webhook are storable endpoints whose outbound delivery is on the '
        'roadmap — so the Policy page only toggles PR, Issue, Slack, and Email. Every audited project stays '
        '<b class="text-on-surface">report-only</b> until you enable a channel on Policy.</p>')

    github = (
        '<p>The <b class="text-on-surface">Fix</b> action opens a <b class="text-on-surface">real Pull '
        'Request</b> on the finding’s owning repo with a concrete change — for example, moving a hardcoded '
        'secret in Terraform to a <span class="' + _code + '">sensitive</span> variable, or tightening a '
        'public IAM binding. The PR is opened for <b class="text-on-surface">human review</b>; CloudCap never '
        'merges and has no cloud write access.</p>'
        '<p>The owning repo is resolved from <b class="text-on-surface">real Terraform state</b> (the Ownership '
        f'pillar), so a fix routes only to the repo that manages the resource. The GitHub token is a '
        f'<b class="text-on-surface">fine-grained PAT</b> in <span class="{_code}">Secret Manager</span> '
        f'(<span class="{_code}">GITHUB_TOKEN</span>) scoped to just that repo — no unrelated repo can be '
        'touched. Findings with no concrete file fix are shown as advisory (no PR spam).</p>')

    secrets = (
        '<p>CloudCap separates <b class="text-on-surface">non-secret config</b> (URLs, project keys, org ids — '
        'kept in the state store / Firestore) from <b class="text-on-surface">secrets</b> (API tokens, webhook '
        f'secrets). Secrets are written to <span class="{_code}">Secret Manager</span> by the app and read at '
        'runtime via environment injection. They are never committed, never printed to logs, and never stored in '
        'Firestore or Terraform vars.</p>'
        '<p class="text-sm">Two-identity model: Terraform apply runs as an <b>owner</b> (bootstrap); the running '
        f'app uses the least-privilege <span class="{_code}">cc-runtime</span> SA, which cannot write to your cloud.</p>')

    auth = (
        '<p>Dashboard sign-in is <b class="text-on-surface">pluggable</b> — set '
        f'<span class="{_code}">CLOUDCAP_AUTH_PROVIDER</span> to choose how users authenticate. The security '
        'model is identical for every provider: a verified identity becomes '
        f'<span class="{_code}">{{email, name, picture}}</span>, then a shared allowlist + role mapping '
        '(admin / operator) applies. Only the credential-verification step differs.</p>'
        + _doc_table([
            ('firebase', 'Google &amp; email sign-in via Firebase Auth', 'current default · verified with the Firebase Admin SDK'),
            ('oidc', 'Any OIDC IdP — Microsoft Entra ID (Azure AD), Okta, Google Workspace', 'ID token verified against the IdP JWKS'),
            ('proxy', 'Enterprise SSO terminated at a gateway — Google IAP, oauth2-proxy, Cloudflare Access', 'identity trusted from the gateway header (X-Goog-Authenticated-User-Email, X-Auth-Request-Email, …)'),
            ('dev', 'Local development login', 'no external IdP'),
        ], headers=("Provider", "How users sign in", "Verification"))
        + '<p>The allowlist + role mapping is <b class="text-on-surface">provider-agnostic</b> — set admins via '
        f'<span class="{_code}">CLOUDCAP_ADMINS</span> (or <span class="{_code}">webui/users.json</span>). So '
        'switching from Firebase to <b class="text-on-surface">Entra ID / Azure AD</b> (OIDC), or fronting the '
        'app with <b class="text-on-surface">IAP</b> for AD/Okta/Workspace SSO, is a configuration change — the '
        'app logic and session handling do not change. Code: <span class="' + _code + '">webui/auth.py</span>.</p>')

    support = (
        '<p>Need help, or found something off in a finding?</p>'
        '<ul class="list-disc pl-5 space-y-1">'
        '<li><b class="text-on-surface">Full docs</b> live in the repo at '
        f'<span class="{_code}">docs/INTEGRATIONS.md</span> (this page mirrors it).</li>'
        '<li><b class="text-on-surface">Install &amp; setup:</b> '
        f'<span class="{_code}">INSTALL.md</span> covers requirements, auth options, and GCP wiring.</li>'
        '<li><b class="text-on-surface">Audit trail:</b> every scan and action is recorded immutably in Cloud '
        'Logging — use it to see who ran what and when.</li>'
        '<li><b class="text-on-surface">Every finding links to its control and frameworks</b> on the '
        '<a href="/compliance" class="text-primary hover:underline">Compliance</a> page, so you can trace the "why".</li>'
        '</ul>'
        '<p class="text-sm">Contact: mayurpawar1@gmail.com</p>')

    # (slug, icon, title, one-line blurb, status badge, full body)
    topics = [
        ("overview", "menu_book", "How CloudCap connects",
         "The read-only governance model and where secrets live.", "", intro),
        ("gcp", "cloud", "GCP data plane",
         "The Google APIs and least-privilege roles each scanner uses.", _doc_pill("live", "live"), gcp),
        ("jira", "confirmation_number", "JIRA integration",
         "File each finding as a labelled JIRA issue.", _doc_pill("ready"), jira),
        ("notifications", "notifications", "Notification channels",
         "Fan findings out to Slack, Email, Teams, PagerDuty, or a webhook.", _doc_pill("per-project"), channels),
        ("github", "merge", "GitHub Pull Requests",
         "Fix findings via real, human-approved Pull Requests.", _doc_pill("live", "live"), github),
        ("secrets", "key", "Secrets & identity",
         "Secret Manager and the two-identity (bootstrap vs runtime) model.", "", secrets),
        ("auth", "lock_person", "Authentication",
         "Pluggable sign-in — Firebase now, extensible to Entra ID / Okta / IAP.", _doc_pill("ready"), auth),
        ("support", "support_agent", "Support",
         "Docs, install guide, audit trail, and how to get help.", "", support),
    ]
    by = {t[0]: t for t in topics}

    def topic_nav(active):
        pills = ""
        for slug, icon, title, *_ in topics:
            on = slug == active
            cls = ("bg-primary text-on-primary" if on else
                   "bg-surface border border-outline-variant/40 text-on-surface-variant hover:border-primary/50")
            pills += (f'<a href="/docs?topic={slug}" class="inline-flex items-center gap-1.5 px-3 py-1.5 '
                      f'rounded-lg text-sm font-semibold {cls} transition-colors">'
                      f'<span class="material-symbols-outlined text-base">{icon}</span>{esc(title)}</a>')
        return f'<div class="flex flex-wrap gap-2 mb-7">{pills}</div>'

    # --- a single topic on its own page ---
    if topic in by:
        slug, icon, title, blurb, badge, body = by[topic]
        content = (
            '<div class="max-w-4xl">'
            '<a href="/docs" class="inline-flex items-center gap-1 text-sm text-primary hover:underline mb-4">'
            '<span class="material-symbols-outlined text-base">arrow_back</span>Documentation index</a>'
            + topic_nav(slug)
            + '<div class="flex items-center gap-3 mb-2">'
            f'<span class="material-symbols-outlined text-primary text-3xl">{icon}</span>'
            f'<h1 class="text-3xl font-bold text-on-surface">{esc(title)}</h1>{badge}</div>'
            f'<p class="text-lg text-on-surface-variant mb-5">{esc(blurb)}</p>'
            '<div class="bg-surface border border-outline-variant/40 rounded-xl p-6 md:p-8 space-y-4 '
            f'text-base text-on-surface-variant leading-relaxed">{body}</div></div>')
        return _base_page(content)

    # --- index: a card per topic ---
    cards = ""
    for slug, icon, title, blurb, badge, _body in topics:
        cards += (
            f'<a href="/docs?topic={slug}" class="block bg-surface border border-outline-variant/40 rounded-xl '
            'p-6 hover:border-primary/50 hover:shadow-sm transition-all">'
            '<div class="flex items-center gap-3 mb-2">'
            f'<span class="material-symbols-outlined text-primary text-2xl">{icon}</span>'
            f'<h2 class="text-xl font-bold text-on-surface">{esc(title)}</h2>{badge}</div>'
            f'<p class="text-base text-on-surface-variant leading-relaxed">{esc(blurb)}</p></a>')
    content = (
        '<div class="max-w-5xl">'
        '<div class="mb-7"><h1 class="text-3xl font-bold text-on-surface">Documentation &amp; Support</h1>'
        '<p class="text-lg text-on-surface-variant mt-2 max-w-3xl">How CloudCap connects to your GCP projects, '
        'JIRA, notifications, and GitHub — and where secrets live. Pick a topic.</p></div>'
        f'<div class="grid grid-cols-1 md:grid-cols-2 gap-5">{cards}</div></div>')
    return _base_page(content)


def _scan_button(label="Run scan", big=False, secondary=False):
    """A scan button that disables + shows a spinner on click (the scan is synchronous,
    so this is the 'scan initiated' feedback until the page reloads with results).
    secondary=True uses the taupe outline style (for a lower-key 'Re-scan')."""
    size = "px-6 py-3" if big else "px-4 py-2"
    style = (f"{BTN_2ND} text-sm" if secondary
             else "rounded-lg text-sm font-bold bg-primary text-on-primary hover:bg-primary/90")
    js = ("var b=this.querySelector('button');b.disabled=true;"
          "b.innerHTML='<span class=\\'material-symbols-outlined text-base animate-spin\\'>progress_activity</span>"
          " Scanning… (up to ~1 min)';")
    return (
        f'<form method="POST" action="/scan/run" onsubmit="{js}">'
        f'<button type="submit" class="{size} {style} '
        'inline-flex items-center gap-2 disabled:opacity-80 disabled:cursor-wait">'
        f'<span class="material-symbols-outlined text-base">radar</span>{esc(label)}</button></form>')


def _scan_history():
    from agents.store import load_state
    hist = load_state("eval/scan_history.json", [])
    if not isinstance(hist, list) or not hist:
        return ""
    rows = ""
    for h in hist[:8]:
        live = h.get("mode") == "live"
        badge = (f'<span class="px-1.5 py-0.5 rounded text-[10px] font-bold '
                 + ("bg-primary-container text-on-primary-container\">LIVE" if live
                    else "bg-surface-container-highest text-on-surface-variant\">DEMO") + "</span>")
        rows += (
            '<details class="border-b border-outline-variant/30 last:border-0">'
            '<summary class="flex items-center justify-between gap-3 px-4 py-2 cursor-pointer '
            'hover:bg-surface-container-low text-sm select-none">'
            f'<span class="flex items-center gap-2 min-w-0"><span class="font-mono text-xs text-on-surface-variant">'
            f'{esc(h.get("ts","")) + (" UTC" if h.get("ts") else "")}</span>{badge}</span>'
            f'<span class="text-xs text-on-surface-variant whitespace-nowrap">{h.get("findings",0)} findings · '
            f'${h.get("savings",0):,.0f}/mo</span></summary>'
            '<div class="px-4 py-2.5 text-xs text-on-surface-variant bg-surface-container-low/40 space-y-0.5">'
            f'<div>Target: <span class="font-mono text-on-surface">{esc(h.get("target",""))}</span> · mode: {esc(h.get("mode",""))}</div>'
            f'<div>Projects in scope: {esc(", ".join(h.get("projects",[])) or "—")}</div>'
            f'<div>Frameworks: {esc(", ".join(h.get("frameworks",[])) or "—")}</div>'
            f'<div>Severity: {h.get("critical",0)} critical · {h.get("high",0)} high · '
            f'{h.get("new",0)} new · {h.get("resolved",0)} resolved</div>'
            f'<div>Reasoner: {esc(h.get("reasoner","") or "—")}</div></div></details>')
    return (
        '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm overflow-hidden mt-6">'
        '<div class="px-4 py-2.5 border-b border-outline-variant/50">'
        '<h2 class="text-xs font-bold uppercase tracking-wider text-on-surface-variant">Scan history</h2></div>'
        f'{rows}</section>')


_ACT_ICON = {"scan_run": "radar", "deliver": "merge", "guardrail_block": "shield",
             "slack_notify": "notifications", "email_notify": "mail",
             "suppressions_applied": "filter_alt", "pr_suppressed_freeze": "ac_unit"}


def render_logs(project, entries, title, subtitle, back_url, back_active):
    """Render real Cloud Logging audit entries (from agents.audit_reader) as a page."""
    back = (f'<a href="{esc(back_url)}" class="inline-flex items-center gap-1 text-sm text-primary '
            'hover:underline mb-4"><span class="material-symbols-outlined text-base">arrow_back</span>Back</a>')
    head = (f'{back}<div class="mb-5"><h1 class="text-2xl font-bold text-on-surface">{esc(title)}</h1>'
            f'<p class="text-base text-on-surface-variant mt-1">{esc(subtitle)}</p></div>')
    if not entries:
        return _base_page(
            head + '<section class="bg-surface border border-outline-variant/40 rounded-lg p-8 text-center">'
            '<span class="material-symbols-outlined text-on-surface-variant text-4xl">receipt_long</span>'
            '<p class="text-base text-on-surface-variant mt-2">No audit entries found. The trail may be empty, '
            'or the reader lacks <span class="font-mono text-sm">roles/logging.viewer</span>.</p></section>')
    rows = ""
    for e in entries:
        det = e.get("detail")
        det = ", ".join(f"{k}={v}" for k, v in det.items()) if isinstance(det, dict) else str(det or "")
        icon = _ACT_ICON.get(e.get("action", ""), "chevron_right")
        rows += ('<tr class="hover:bg-surface-container-low align-top">'
                 f'<td class="px-4 py-2.5 font-mono text-xs text-on-surface-variant whitespace-nowrap">{esc(e.get("ts",""))}</td>'
                 '<td class="px-4 py-2.5"><span class="px-2 py-0.5 rounded bg-surface-container text-xs font-semibold '
                 f'text-on-surface whitespace-nowrap">{esc(e.get("agent",""))}</span></td>'
                 '<td class="px-4 py-2.5 font-semibold text-on-surface whitespace-nowrap">'
                 f'<span class="inline-flex items-center gap-1.5"><span class="material-symbols-outlined text-base '
                 f'text-on-surface-variant">{icon}</span>{esc(e.get("action",""))}</span></td>'
                 f'<td class="px-4 py-2.5 text-sm text-on-surface-variant">{esc(det[:200])}</td>'
                 f'<td class="px-4 py-2.5 font-mono text-xs text-on-surface-variant/70" title="hash-chained">{esc(e.get("hash",""))}</td></tr>')
    content = (
        head +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<div class="px-4 py-2.5 border-b border-outline-variant/50 flex items-center gap-2">'
        '<span class="material-symbols-outlined text-primary text-lg">verified_user</span>'
        f'<span class="text-sm text-on-surface-variant">{len(entries)} entries · hash-chained · immutable Cloud Logging '
        '(<span class="font-mono">cloudcap-audit</span>)</span></div>'
        '<div class="overflow-x-auto"><table class="w-full text-left text-base"><thead>'
        '<tr class="bg-surface-container-low text-sm uppercase tracking-wider text-on-surface-variant border-b border-outline-variant/60">'
        '<th class="px-4 py-3 font-semibold">Time (UTC)</th><th class="px-4 py-3 font-semibold">Agent</th>'
        '<th class="px-4 py-3 font-semibold">Action</th><th class="px-4 py-3 font-semibold">Detail</th>'
        '<th class="px-4 py-3 font-semibold">Chain hash</th></tr></thead>'
        f'<tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div></section>')
    return _base_page(content)


def render_history(project="demo-proj", page=1):
    """Full-page scan history — every governance scan with its profile. Its own page
    (was a cramped strip on the board)."""
    from agents.onboarding import OnboardingState
    from agents.store import load_state
    hist = load_state("eval/scan_history.json", [])
    if not isinstance(hist, list) or not hist or not OnboardingState().first_scan_done:
        return _base_page(
            '<div class="min-h-[50vh] flex flex-col items-center justify-center text-center">'
            '<span class="material-symbols-outlined text-on-surface-variant text-4xl mb-3">history</span>'
            '<h2 class="text-xl font-bold text-on-surface">No scans yet</h2>'
            '<p class="text-base text-on-surface-variant mt-2 max-w-md">Every governance scan is '
            'recorded here with its full profile. Run a scan to start the history.</p>'
            '<a href="/board" class="mt-5 px-5 py-2.5 rounded-lg text-sm font-bold bg-primary text-on-primary '
            'hover:bg-primary/90">Go to board</a></div>')

    per, total = 15, len(hist)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))
    window = hist[(page - 1) * per: page * per]

    def num(v, cls="text-on-surface"):
        return f'<span class="{cls}">{v}</span>' if v else '<span class="text-on-surface-variant/40">0</span>'

    rows = ""
    for h in window:
        live = h.get("mode") == "live"
        mode = (f'<span class="px-2.5 py-1 rounded text-xs font-bold '
                + ('bg-primary-container text-on-primary-container">LIVE' if live
                   else 'bg-surface-container-highest text-on-surface-variant">DEMO') + '</span>')
        fw = ", ".join(h.get("frameworks", [])) or "—"
        sav = h.get("savings", 0)
        rows += (
            '<tr class="hover:bg-surface-container-low transition-colors align-middle">'
            f'<td class="px-4 py-3.5 font-mono text-sm whitespace-nowrap"><a href="/logs?ts={esc(h.get("ts",""))}" '
            'class="inline-flex items-center gap-1 text-primary hover:underline" title="View the Cloud Logging audit trail">'
            f'<span class="material-symbols-outlined text-base">receipt_long</span>{esc(h.get("ts",""))}</a></td>'
            f'<td class="px-4 py-3.5">{mode}</td>'
            f'<td class="px-4 py-3.5 font-mono text-base text-on-surface">{esc(h.get("target",""))}</td>'
            f'<td class="px-4 py-3.5 text-center font-bold text-base">{num(h.get("findings",0))}</td>'
            f'<td class="px-4 py-3.5 text-center">{num(h.get("critical",0), "text-error font-bold")}</td>'
            f'<td class="px-4 py-3.5 text-center">{num(h.get("high",0), "text-tertiary font-semibold")}</td>'
            f'<td class="px-4 py-3.5 text-center">{num(h.get("new",0), "text-primary font-semibold")}</td>'
            f'<td class="px-4 py-3.5 text-center">{num(h.get("resolved",0), "text-on-surface-variant")}</td>'
            f'<td class="px-4 py-3.5 text-right font-semibold text-base text-tertiary">${sav:,.0f}<span class="text-xs text-on-surface-variant font-normal">/mo</span></td>'
            f'<td class="px-4 py-3.5 text-sm text-on-surface-variant">{esc(fw)}</td></tr>')

    th = ('<tr class="bg-surface-container-low text-sm uppercase tracking-wider text-on-surface-variant '
          'border-b border-outline-variant/60">'
          '<th class="px-4 py-3 font-semibold">When (UTC)</th><th class="px-4 py-3 font-semibold">Mode</th>'
          '<th class="px-4 py-3 font-semibold">Target</th><th class="px-4 py-3 font-semibold text-center">Findings</th>'
          '<th class="px-4 py-3 font-semibold text-center">Critical</th><th class="px-4 py-3 font-semibold text-center">High</th>'
          '<th class="px-4 py-3 font-semibold text-center">New</th><th class="px-4 py-3 font-semibold text-center">Resolved</th>'
          '<th class="px-4 py-3 font-semibold text-right">Savings</th><th class="px-4 py-3 font-semibold">Frameworks</th></tr>')

    header = ('<div class="mb-5"><h1 class="text-2xl font-bold text-on-surface">Scan history</h1>'
              f'<p class="text-base text-on-surface-variant mt-1">{total} governance scan(s) recorded — '
              'each one is an audited event with its full profile.</p></div>')
    content = (
        header +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<div class="overflow-x-auto"><table class="w-full text-left text-base">'
        f'<thead>{th}</thead><tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div>'
        f'{_pager("/history", page, total, per)}</section>')
    return _base_page(content)


def render_board(project="demo-proj", page=1, scanned=False):
    from agents.onboarding import OnboardingState
    ob = OnboardingState()
    if not ob.first_scan_done:
        # No official scan yet → never compute/show live findings. Admins get the
        # first-scan CTA; the serve gate sends non-admins to the not-ready screen.
        empty = (
            '<div class="min-h-[60vh] flex flex-col items-center justify-center text-center">'
            '<div class="h-16 w-16 rounded-full bg-primary-container/40 flex items-center justify-center mb-5">'
            '<span class="material-symbols-outlined text-primary text-3xl">radar</span></div>'
            '<h2 class="text-xl font-bold text-on-surface">Your hub is configured 🎉</h2>'
            '<p class="text-sm text-on-surface-variant mt-2 max-w-md">No findings yet. Run your first governance '
            'scan across the projects in scope — cost, security, IAM and compliance, all at once.</p>'
            '<div class="mt-6">' + _scan_button("Run first scan", big=True) + '</div></div>')
        return _base_page(empty)
    d = gather(project)
    post = compliance_posture(d["fd"])

    # --- 3 consolidated cards (Detection · Cost impact · Compliance) ---
    def card(title, big, big_sub, big_color, sub_html):
        return (
            '<div class="bg-surface border border-outline-variant/40 rounded-lg p-5">'
            f'<div class="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-3">{esc(title)}</div>'
            f'<div class="flex items-baseline gap-2"><span class="text-4xl font-bold text-{big_color}">{big}</span>'
            f'<span class="text-sm text-on-surface-variant">{esc(big_sub)}</span></div>'
            f'<div class="grid grid-cols-2 gap-x-4 gap-y-3 mt-5 text-sm">{sub_html}</div></div>')

    scan_label = "live scan" if d.get("mode") == "live" else "demo scan"
    detection = card("Findings", str(d["n_findings"]), scan_label, "primary",
                     _metric("Critical", f'<span class="text-error">{d["critical"]}</span>')
                     + _metric("High", str(d["high"]))
                     + _metric("Issues filed", str(d["issues_opened"]))
                     + _metric("PRs opened", str(d["pr_opened"])))
    cost = card("Cost impact", d["waste"], "monthly savings identified", "tertiary",
                _metric("Run cost", f'{d["cost_run"]}/mo') + _metric("ROI", d["cost_roi"])
                + _metric("Per scan", d["cost_perscan"]) + _metric("Net saved", d["waste"]))
    comp_rows = ""
    for fw in FRAMEWORKS:
        p = post[fw]
        col = "primary" if p["failing"] == 0 else "tertiary" if p["score"] >= 0.5 else "error"
        comp_rows += (f'<div class="flex justify-between"><span class="text-on-surface-variant">{esc(fw)}</span>'
                      f'<span class="font-bold text-{col}">{p["score"] * 100:.0f}%</span></div>')
    compliance = (
        '<div class="bg-surface border border-outline-variant/40 rounded-lg p-5">'
        '<div class="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-3">Compliance</div>'
        f'<div class="space-y-2 text-sm">{comp_rows}</div>'
        '<a href="/compliance" class="text-sm text-primary hover:underline mt-4 inline-block">View control matrix →</a></div>')

    cards = f'<section class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">{detection}{cost}{compliance}</section>'

    # --- orchestrator (LLM) summary strip ---
    summary_strip = ""
    if d.get("summary"):
        rname = d.get("reasoner", "") or "reasoner"
        live = "Gemini" in rname
        badge_cls = ("bg-primary-container text-on-primary-container" if live
                     else "bg-surface-container-highest text-on-surface-variant")
        badge_txt = "GEMINI · LIVE" if live else "DETERMINISTIC"
        summary_strip = (
            '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-5 mb-6">'
            '<div class="flex items-center gap-2 mb-2">'
            '<span class="material-symbols-outlined text-primary text-xl">neurology</span>'
            '<span class="text-sm font-bold uppercase tracking-wider text-on-surface-variant">Orchestrator summary</span>'
            f'<span class="ml-auto px-2 py-0.5 rounded text-[11px] font-bold {badge_cls}">{badge_txt}</span></div>'
            f'<p class="text-base text-on-surface leading-relaxed">{esc(d["summary"])}</p></section>')

    # --- findings table (dense, paginated) — ordered by the reasoner's priority rank ---
    per = PAGE_SIZE_FINDINGS
    fd = sorted(d["fd"], key=lambda x: (x.get("metadata", {}).get("priority_rank", 10**6),
                                        -x.get("est_monthly_savings_usd", 0)))
    pages = max(1, (len(fd) + per - 1) // per)
    page = max(1, min(page, pages))
    rows = _findings_rows(fd[(page - 1) * per: page * per], d["hist"])
    heads = ["Finding ID", "Severity", "Category", "Resource", "Title", "Est. savings",
             "Ownership", "Action"]
    thead = "".join(f'<th class="px-3 py-3 font-semibold">{h}</th>' for h in heads)
    findings = (
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<div class="px-4 py-3 border-b border-outline-variant/50">'
        '<h2 class="text-sm font-bold uppercase tracking-wider text-on-surface-variant">Findings</h2></div>'
        '<div class="overflow-x-auto"><table class="w-full text-left text-base"><thead>'
        '<tr class="bg-surface-container-low text-sm uppercase tracking-wider text-on-surface-variant '
        f'border-b border-outline-variant/60">{thead}</tr></thead>'
        f'<tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div>'
        f'{_pager("/board", page, len(fd), per)}</section>')

    ts = d.get("scan_ts", "")
    is_live = d.get("mode") == "live"
    demo_note = ("" if is_live else
                 '<div class="mb-4 flex items-start gap-2 rounded-lg border border-outline-variant/30 '
                 'bg-surface-container-low px-4 py-2.5 text-xs text-on-surface-variant">'
                 '<span class="material-symbols-outlined text-base select-none">savings</span>'
                 '<span><b class="text-on-surface">Seeded-demo mode.</b> Following the hackathon\'s '
                 'guidance to switch services off after the demo, the live billable resources were '
                 'decommissioned to keep costs near zero — scans return representative seeded findings. '
                 'The full <b class="text-on-surface">live</b> run against real GCP (real findings, a real '
                 'remediation PR) is in the demo video.</span></div>')
    scan_bar = (
        demo_note +
        '<div class="flex items-center justify-between mb-4">'
        f'<p class="text-xs text-on-surface-variant">Last scan: <span class="font-mono">{esc(ts)}</span>{" UTC" if ts else ""} · '
        f'<span class="font-semibold">{"LIVE" if is_live else "DEMO"}</span></p>'
        + _scan_button("Re-scan", secondary=True) + '</div>')
    # Unmistakable completion signal after a scan finishes (the POST redirects here with
    # ?scanned=1) — a big green banner so it's obvious the scan is done.
    banner = ""
    if scanned:
        _sav = sum(x.get("est_monthly_savings_usd", 0) for x in d["fd"])
        banner = (
            '<div id="cc-scandone" class="mb-5 flex items-center gap-3 rounded-lg border border-primary/40 '
            'bg-primary-container/50 px-5 py-4 shadow-sm">'
            '<span class="material-symbols-outlined text-primary text-3xl">check_circle</span>'
            '<div><div class="font-bold text-lg text-on-surface">Scan complete</div>'
            f'<div class="text-base text-on-surface-variant">{d["n_findings"]} findings across the fleet · '
            f'<span class="text-tertiary font-semibold">${_sav:,.0f}/mo</span> savings identified · '
            f'{d.get("critical", 0)} critical.</div></div>'
            '<button onclick="document.getElementById(\'cc-scandone\').remove()" '
            'class="ml-auto text-on-surface-variant hover:text-on-surface" title="Dismiss">'
            '<span class="material-symbols-outlined">close</span></button></div>')
    return _base_page(banner + scan_bar + summary_strip + cards + findings + _fid_tooltip())


# --- remaining screens ------------------------------------------------------
def _findings_dicts(project):
    # Pages (compliance, finding drill-down) read the persisted last scan — not a live
    # recompute — so they reflect exactly what was scanned (live or demo).
    return _last_scan().get("findings", [])


def _project_status(proj):
    """Setup completeness for an in-scope project. Green tick = repo + channel set;
    a project scans regardless (report-only if nothing is set)."""
    from agents.freeze import FreezeStore
    from agents.project_settings import ProjectSettings
    s = ProjectSettings().get(proj)
    frozen = FreezeStore().get(proj)
    repo, chan = bool(s.get("repo")), bool(s.get("channels"))
    if repo and chan:
        return ('<span class="inline-flex items-center gap-1.5 text-sm font-bold text-primary">'
                '<span class="material-symbols-outlined text-lg">check_circle</span>Configured</span>', frozen)
    label = "Partial" if (repo or chan or frozen) else "Report-only"
    return ('<span class="inline-flex items-center gap-1.5 text-sm font-semibold text-tertiary">'
            f'<span class="material-symbols-outlined text-lg">error</span>{label}</span>', frozen)


# Client-side validation + dynamic channel inputs for the per-project setup form.
SETUP_JS = """<script>
function ccPat(k){return {repo:/^(https?:\\/\\/)?(github\\.com|gitlab\\.com|bitbucket\\.org|dev\\.azure\\.com)\\/[\\w.\\-\\/]+$/i,
url:/^https:\\/\\/[^\\s]+\\.[^\\s]+$/i, email:/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/, text:/.+/}[k];}
function ccVal(el){var k=el.dataset.validate; if(!k) return true; var m=el.parentElement.querySelector('.cc-mark');
 var v=el.value.trim(); if(v===''){el.style.borderColor='';if(m)m.textContent='';return true;}
 var ok=ccPat(k).test(v); el.style.borderColor=ok?'#16a34a':'#dc2626';
 if(m){m.textContent=ok?'✓':'✗'; m.style.color=ok?'#16a34a':'#dc2626';} return ok;}
function ccChan(cb,key){var w=document.getElementById('chw_'+key); w.style.display=cb.checked?'':'none';
 if(cb.checked){var i=w.querySelector('input'); if(i) i.focus();}}
function ccSetupOK(f){var bad=false;
 f.querySelectorAll('input[data-validate=repo]').forEach(function(e){if(e.value.trim()&&!ccVal(e))bad=true;});
 f.querySelectorAll('input[type=checkbox][name^=ch_]').forEach(function(cb){if(cb.checked){
   var i=f.querySelector('[name=chv_'+cb.name.slice(3)+']');
   if(i&&i.value.trim()===''){bad=true;i.style.borderColor='#dc2626';}else if(i&&!ccVal(i))bad=true;}});
 if(bad){alert('Please fix the highlighted fields before saving.');return false;} return true;}
</script>"""


def project_setup_form(proj, action="/sources/project/save", submit="Save",
                       cancel_href="/sources", include_js=True):
    """Rich, validated per-project setup: repo (validated), freeze, and typed channels
    (Slack/email/Teams/PagerDuty/webhook), each with its own validated input. Reused by
    the Projects page and onboarding."""
    from agents.freeze import FreezeStore
    from agents.project_settings import CHANNEL_TYPES, ProjectSettings
    s = ProjectSettings().get(proj)
    fr = FreezeStore().get(proj) or {}
    have = {c["type"]: c["value"] for c in s.get("channels", [])}
    inp = "bg-background border border-outline-variant/40 rounded-lg px-3 py-2.5 text-base w-full"
    uid = re.sub(r"[^a-z0-9]+", "_", proj.lower())  # unique element ids per project

    chans = ""
    for c in CHANNEL_TYPES:
        k, on = c["key"], c["key"] in have
        wid = f"chw_{uid}_{k}"
        chans += (
            '<div class="rounded-lg border border-outline-variant/40 p-2.5">'
            f'<label class="flex items-center gap-2 text-sm text-on-surface cursor-pointer">'
            f'<input type="checkbox" name="ch_{k}" {"checked" if on else ""} class="accent-primary" '
            f'onchange="ccChan(this,\'{uid}_{k}\')"/>{esc(c["label"])}</label>'
            f'<div id="{wid}" style="{"" if on else "display:none"}" class="mt-2 relative">'
            f'<input name="chv_{k}" value="{esc(have.get(k,""))}" placeholder="{esc(c["placeholder"])}" '
            f'data-validate="{c["validate"]}" oninput="ccVal(this)" class="{inp} pr-7"/>'
            '<span class="cc-mark absolute right-2 top-2.5 text-sm font-bold"></span>'
            f'<div class="text-sm text-on-surface-variant mt-1">{esc(c["field"])}</div></div></div>')

    return (
        f'<form method="POST" action="{esc(action)}" onsubmit="return ccSetupOK(this)" class="space-y-4">'
        f'<input type="hidden" name="project" value="{esc(proj)}"/>'
        # repo + validation
        '<div><label class="text-sm font-semibold text-on-surface">Source repo <span class="font-normal '
        'text-on-surface-variant">(optional — where fix PRs are proposed)</span></label>'
        '<div class="relative mt-1">'
        f'<input name="repo" value="{esc(s.get("repo",""))}" data-validate="repo" oninput="ccVal(this)" '
        f'placeholder="github.com/org/repo" class="{inp} pr-7"/>'
        '<span class="cc-mark absolute right-2 top-2.5 text-sm font-bold"></span></div></div>'
        # freeze
        '<div class="rounded-lg border border-outline-variant/40 p-2.5">'
        '<label class="flex items-center gap-2 text-sm text-on-surface">'
        f'<input type="checkbox" name="freeze" value="1" {"checked" if fr else ""} class="accent-tertiary"/>'
        'Code frozen — no PRs until</label>'
        f'<input type="date" name="freeze_until" value="{esc(fr.get("until",""))}" '
        'class="mt-2 bg-background border border-outline-variant/40 rounded px-2 py-1 text-sm"/></div>'
        # channels
        '<div><div class="text-sm font-semibold text-on-surface mb-2">Notification channels '
        '<span class="font-normal text-on-surface-variant">(optional — pick any)</span></div>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{chans}</div></div>'
        '<div class="flex items-center gap-3 pt-1">'
        f'<button class="bg-primary text-on-primary rounded-lg px-4 py-2 text-sm font-bold hover:bg-primary/90">{esc(submit)}</button>'
        + (f'<a href="{esc(cancel_href)}" class="text-sm text-on-surface-variant hover:text-primary">Cancel</a>'
           if cancel_href else "")
        + '</div></form>' + (SETUP_JS if include_js else ""))


def _setup_panel(proj):
    return (
        '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-4 mb-4">'
        f'<div class="flex items-center justify-between mb-3"><h3 class="text-sm font-bold text-on-surface">Set up '
        f'<span class="font-mono">{esc(proj)}</span></h3>'
        '<a href="/sources" class="text-xs text-on-surface-variant hover:text-primary">✕ close</a></div>'
        + project_setup_form(proj) + '</section>')


def render_sources(project="demo-proj", page=1, setup=None):
    from agents.sources import folder_of
    sel = SourcesConfig().selected()
    rows_data = all_projects()
    per, total = 10, len(rows_data)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))
    body = ""
    for proj in rows_data[(page - 1) * per: page * per]:
        on = proj in sel
        chk = 'checked=""' if on else ""
        scope = ('<span class="px-2.5 py-1 rounded text-xs font-bold bg-primary-container text-on-primary-container">IN SCOPE</span>'
                 if on else '<span class="px-2.5 py-1 rounded text-xs font-bold bg-surface-variant text-on-surface-variant">EXCLUDED</span>')
        setup_status, _ = _project_status(proj) if on else ('<span class="text-sm text-on-surface-variant">—</span>', None)
        setup_btn = (f'<a href="/sources?setup={esc(proj)}" class="text-sm text-primary hover:underline">Set up</a>'
                     if on else "")
        body += (
            '<tr class="hover:bg-surface-container-low transition-colors">'
            f'<td class="px-3 py-3.5"><input type="checkbox" {chk} value="{esc(proj)}" onchange="ccToggle(this)" '
            'class="w-5 h-5 rounded border-outline text-primary cursor-pointer"/></td>'
            f'<td class="px-3 py-3.5 font-mono text-base text-on-surface">{esc(proj)}</td>'
            f'<td class="px-3 py-3.5 text-sm text-on-surface-variant">{esc(folder_of(proj))}</td>'
            f'<td class="px-3 py-3.5">{scope}</td>'
            f'<td class="px-3 py-3.5">{setup_status}</td>'
            f'<td class="px-3 py-3.5 text-right">{setup_btn}</td></tr>')
    in_scope = len(sel)
    header = (
        '<div class="flex items-center justify-between gap-4 mb-5">'
        f'<p class="text-base text-on-surface-variant max-w-3xl">Discovered <b class="text-on-surface">{total}</b> '
        f'project(s) · <b class="text-on-surface">{in_scope}</b> in scope. Toggle scope (saves instantly); '
        'the green tick shows projects with a repo + channel — a project still scans without them (report-only).</p>'
        f'<form method="POST" action="/sources/discover"><button class="{BTN_2ND} px-3 py-2 text-sm '
        'whitespace-nowrap">↻ Re-discover</button></form></div>')
    panel = _setup_panel(setup) if (setup and setup in sel) else ""
    content = (
        header + panel +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<table class="w-full text-left text-base"><thead>'
        '<tr class="bg-surface-container-low text-sm uppercase tracking-wider text-on-surface-variant '
        'border-b border-outline-variant/60"><th class="px-3 py-3 w-10"></th>'
        '<th class="px-3 py-3 font-semibold">Project</th><th class="px-3 py-3 font-semibold">Folder</th>'
        '<th class="px-3 py-3 font-semibold">Scope</th><th class="px-3 py-3 font-semibold">Setup</th>'
        '<th class="px-3 py-3"></th></tr></thead>'
        f'<tbody class="divide-y divide-outline-variant/60">{body}</tbody></table>'
        f'{_pager("/sources", page, total, per)}</section>'
        '<script>function ccToggle(el){fetch("/sources/toggle",{method:"POST",'
        'headers:{"Content-Type":"application/x-www-form-urlencoded"},'
        'body:"project="+encodeURIComponent(el.value)+"&on="+(el.checked?"1":"")}).then(()=>location.reload());}</script>')
    return _base_page(content)


INT_PILL = {"pass": ("✓ HEALTHY", "primary-container", "on-primary-container"),
            "fail": ("✗ FAILING", "error-container", "on-error-container"),
            "untested": ("• UNTESTED", "surface-variant", "on-surface-variant"),
            "disabled": ("— DISABLED", "surface-variant", "on-surface-variant")}
INT_KINDS = ["GitHub", "GitLab", "Bitbucket", "Jira", "ServiceNow", "Slack", "GCP scope", "IaC backend"]


def _int_input(label, name, value="", secret=False):
    return (f'<label class="flex flex-col gap-1"><span class="text-on-surface-variant">{esc(label)}</span>'
            f'<input name="{esc(name)}" value="{esc(value)}" type="{"password" if secret else "text"}" '
            'class="bg-background border border-outline-variant/50 rounded-md px-2.5 py-1.5 focus:outline-none '
            'focus:ring-1 focus:ring-primary"/></label>')


def render_integrations(project="demo-proj", add=False, edit=None):
    store = IntegrationsStore()
    healthy = sum(1 for i in store.items if i["status"] == "pass")
    enabled = sum(1 for i in store.items if i["enabled"])

    form = ""
    if add:
        opts = "".join(f"<option>{esc(k)}</option>" for k in INT_KINDS)
        form = (
            '<section class="bg-surface border border-outline-variant/40 rounded-lg p-4 mb-4">'
            '<h3 class="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-3">Add integration</h3>'
            '<form method="POST" action="/integrations/add" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">'
            + _int_input("Name", "name") + f'<label class="flex flex-col gap-1"><span class="text-on-surface-variant">Type</span>'
            f'<select name="kind" class="bg-background border border-outline-variant/50 rounded-md px-2.5 py-1.5">{opts}</select></label>'
            + _int_input("Endpoint / host", "endpoint") + _int_input("Token (→ Secret Manager)", "token", secret=True)
            + '<div class="md:col-span-2 flex gap-2 pt-1"><button class="bg-primary text-on-primary font-bold rounded-md px-4 py-1.5">Add</button>'
            '<a href="/integrations" class="border border-outline-variant/50 rounded-md px-4 py-1.5">Cancel</a></div></form></section>')
    elif edit and store.get(edit):
        i = store.get(edit)
        fields = "".join(_int_input(f["label"], f["key"], i["config"].get(f["key"], ""), f.get("secret"))
                         for f in i["fields"])
        chk = 'checked=""' if i["enabled"] else ""
        form = (
            '<section class="bg-surface border border-outline-variant/40 rounded-lg p-4 mb-4">'
            f'<h3 class="text-xs font-bold uppercase tracking-wider text-on-surface-variant mb-3">Edit — {esc(i["name"])}</h3>'
            '<form method="POST" action="/integrations/update" class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">'
            f'<input type="hidden" name="id" value="{esc(i["id"])}"/>{fields}'
            f'<label class="flex items-center gap-2 md:col-span-2"><input type="checkbox" name="enabled" {chk} class="w-4 h-4"/>'
            '<span>enabled</span></label>'
            '<div class="md:col-span-2 flex gap-2 pt-1"><button class="bg-primary text-on-primary font-bold rounded-md px-4 py-1.5">Save</button>'
            '<a href="/integrations" class="border border-outline-variant/50 rounded-md px-4 py-1.5">Cancel</a></div></form></section>')

    rows = ""
    for i in store.items:
        label, bg, fg = INT_PILL.get(i["status"], INT_PILL["untested"])
        pill = f'<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-{bg} text-{fg}">{label}</span>'
        endpoint = next((i["config"].get(f["key"], "") for f in i["fields"] if not f.get("secret")), "")
        detail = f'<span class="text-on-surface-variant">{esc(i["detail"])}</span>' if i["detail"] else ""
        rows += (
            '<tr class="hover:bg-surface-container-low transition-colors">'
            f'<td class="px-3 py-2 font-semibold text-on-surface">{esc(i["name"])}</td>'
            f'<td class="px-3 py-2 text-on-surface-variant">{esc(i.get("kind", ""))}</td>'
            f'<td class="px-3 py-2 font-mono text-sm text-on-surface-variant">{esc(endpoint) or "—"}</td>'
            f'<td class="px-3 py-2">{pill}</td>'
            f'<td class="px-3 py-2 text-on-surface-variant text-[11px]">{esc(i["last_checked"]) or "—"} {detail}</td>'
            '<td class="px-3 py-2 text-right whitespace-nowrap">'
            f'<form method="POST" action="/integrations/test" class="inline"><input type="hidden" name="id" value="{esc(i["id"])}"/>'
            '<button class="text-primary hover:underline text-xs mr-3">Test</button></form>'
            f'<a href="/integrations?edit={esc(i["id"])}" class="text-primary hover:underline text-xs">Edit</a></td></tr>')

    content = (
        '<div class="flex items-center justify-between mb-4">'
        f'<p class="text-xs text-on-surface-variant">{healthy}/{enabled} healthy · tokens stored in Secret Manager · Test probes live access</p>'
        '<div class="flex gap-2">'
        '<form method="POST" action="/integrations/testall" class="inline"><button class="border border-outline-variant/50 rounded-md px-3 py-1.5 text-xs font-semibold">Test all</button></form>'
        '<a href="/integrations?add=1" class="bg-primary text-on-primary rounded-md px-3 py-1.5 text-xs font-bold">+ Add integration</a></div></div>'
        + form +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead>'
        '<tr class="bg-surface-container-low text-xs uppercase tracking-wider text-on-surface-variant border-b border-outline-variant/60">'
        '<th class="px-3 py-2 font-semibold">Name</th><th class="px-3 py-2 font-semibold">Type</th>'
        '<th class="px-3 py-2 font-semibold">Endpoint</th><th class="px-3 py-2 font-semibold">Status</th>'
        '<th class="px-3 py-2 font-semibold">Last checked</th><th class="px-3 py-2 font-semibold text-right">Actions</th></tr></thead>'
        f'<tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div></section>')
    return _base_page(content)


def _chk(val, color, name=""):
    c = 'checked="" ' if val else ""
    n = f'name="{esc(name)}" ' if name else ""
    return (f'<input {c}{n}class="w-5 h-5 rounded border-outline text-{color} '
            f'focus:ring-{color} focus:ring-offset-surface bg-surface cursor-pointer" type="checkbox"/>')


def _replace_nth(html, pattern, repl, n):
    matches = list(re.finditer(pattern, html, flags=re.S))
    if len(matches) >= n:
        m = matches[n - 1]
        html = html[:m.start()] + repl + html[m.end():]
    return html


# One combined policy row = 9 checks: two major sections, each with sub-sections.
POLICY_COLS = [("pr", "PR"), ("issue", "Issue"), ("slack", "Slack"), ("email", "Email"),
               ("cost", "Cost"), ("security", "Security"),
               ("CIS GCP", "CIS GCP"), ("SOC 2", "SOC 2"), ("ISO 27001", "ISO 27001"), ("PCI DSS", "PCI DSS")]
ACTION_FIELDS = {"pr", "issue", "slack", "email"}
FRAMEWORK_FIELDS = {"CIS GCP", "SOC 2", "ISO 27001", "PCI DSS"}


def render_policy(project="demo-proj", page=1):
    from agents.project_settings import ProjectSettings
    pol, gov = ActionPolicy(), GovernanceConfig()
    ps = ProjectSettings()
    scopes = sorted(SourcesConfig().selected())   # only IN-SCOPE projects (from Sources)
    per, total = 15, len(scopes)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))

    def val(scope, field):
        d = pol.channels_for(scope) if field in ACTION_FIELDS else gov.profile_for(scope)
        return d.get(field)

    def sep(field):  # divider only between the two MAJOR sections
        return " border-l border-outline-variant/50" if field in ("pr", "cost") else ""

    from agents.jira import JiraConfig
    jira_ok = JiraConfig().configured

    def _has_channel(scope, kind):
        return any((c.get("type") == kind) for c in ps.channels(scope))

    def gated(scope, field):
        """An action is available only if its integration is set up: PR needs a repo,
        Slack/Email need that channel (per-project in Sources), Issue needs JIRA (global).
        Compliance frameworks require the Security domain to be enabled for the project."""
        if field == "pr" and not ps.repo(scope):
            return "Add a repo in Sources to enable PRs"
        if field == "slack" and not _has_channel(scope, "slack"):
            return "Add a Slack channel in Sources to enable Slack alerts"
        if field == "email" and not _has_channel(scope, "email"):
            return "Add an email address in Sources to enable email alerts"
        if field == "issue" and not jira_ok:
            return "Configure JIRA below to enable issues"
        if field in FRAMEWORK_FIELDS and not gov.profile_for(scope).get("security"):
            return "Enable the Security domain to scope compliance frameworks"
        return ""

    if not scopes:
        empty = ('<div class="min-h-[40vh] flex flex-col items-center justify-center text-center">'
                 '<span class="material-symbols-outlined text-on-surface-variant text-3xl mb-3">gavel</span>'
                 '<h2 class="text-lg font-bold text-on-surface">No projects in scope</h2>'
                 '<p class="text-sm text-on-surface-variant mt-2 max-w-md">Select projects on the Sources page '
                 'first — policy is set per in-scope project.</p>'
                 '<a href="/sources" class="mt-5 px-5 py-2.5 rounded-lg text-sm font-bold bg-primary text-on-primary '
                 'hover:bg-primary/90">Go to Sources</a></div>')
        return _base_page(empty)

    rows = ""
    for scope in scopes[(page - 1) * per: page * per]:
        cells = ""
        for field, _lbl in POLICY_COLS:
            accent = "text-primary" if field in ACTION_FIELDS else "text-tertiary"
            reason = gated(scope, field)
            if reason:
                cells += (f'<td class="px-2 py-2.5 text-center{sep(field)}" title="{esc(reason)}">'
                          '<input type="checkbox" disabled class="w-4 h-4 rounded border-outline '
                          'opacity-30 cursor-not-allowed"/></td>')
            else:
                chk = 'checked=""' if val(scope, field) else ""
                # The Security domain gates the frameworks — highlight it as the control.
                ring = " ring-2 ring-primary/40 ring-offset-1" if field == "security" else ""
                cells += (f'<td class="px-2 py-2.5 text-center{sep(field)}"><input type="checkbox" {chk} '
                          f'data-scope="{esc(scope)}" data-field="{esc(field)}" onchange="ccPol(this)" '
                          f'class="w-4 h-4 rounded border-outline {accent}{ring} cursor-pointer"/></td>')
        rows += ('<tr class="hover:bg-surface-container-low transition-colors">'
                 f'<td class="px-3 py-2.5 whitespace-nowrap font-mono text-base">{esc(scope)}</td>{cells}</tr>')

    hdr = ('<thead class="text-on-surface-variant">'
           # tier 1 — the two major groups
           '<tr class="bg-surface-container border-b border-outline-variant/50 text-xs uppercase tracking-wider">'
           '<th rowspan="3" class="px-3 py-2.5 text-left font-bold align-bottom text-on-surface">Scope</th>'
           '<th colspan="4" class="px-2 py-2 text-center font-bold text-primary border-l border-outline-variant/50">Actions</th>'
           '<th colspan="6" class="px-2 py-2 text-center font-bold text-tertiary border-l border-outline-variant/50">Governance Scope</th></tr>'
           # tier 2 — sub-groups, centered over their columns
           '<tr class="bg-surface-container-low border-b border-outline-variant/50 text-xs uppercase tracking-wider">'
           '<th colspan="2" class="px-2 py-1.5 text-center border-l border-outline-variant/50">Delivery</th>'
           '<th colspan="2" class="px-2 py-1.5 text-center">Alerts</th>'
           '<th colspan="2" class="px-2 py-1.5 text-center border-l border-outline-variant/50">Domains</th>'
           '<th colspan="4" class="px-2 py-1.5 text-center">Frameworks</th></tr>'
           # tier 3 — individual columns
           '<tr class="bg-surface-container-low border-b border-outline-variant/60 text-xs">'
           + "".join(f'<th class="px-2 py-2 text-center font-semibold{sep(f)}">{esc(l)}</th>' for f, l in POLICY_COLS)
           + '</tr></thead>')

    # --- JIRA connector (destination for the Issue action) ---
    jc = JiraConfig()
    inp = "bg-background border border-outline-variant/40 rounded-lg px-3 py-2.5 text-base w-full"
    tok = ('<span class="text-primary">✓ token present</span>' if jc.token
           else '<span class="text-tertiary">token via Secret Manager / JIRA_API_TOKEN (not set)</span>')
    status = ('<span class="inline-flex items-center gap-1 text-xs font-bold text-primary">'
              '<span class="material-symbols-outlined text-base">check_circle</span>Configured</span>'
              if jc.configured else
              '<span class="text-xs font-semibold text-tertiary">Not configured</span>')
    jira_card = (
        '<section class="bg-surface border border-[#d4d4d8] rounded-lg shadow-sm p-4 mb-5">'
        '<div class="flex items-center justify-between mb-2">'
        '<h3 class="text-sm font-bold text-on-surface">Issue tracker · JIRA</h3>' + status + '</div>'
        '<p class="text-xs text-on-surface-variant mb-3">Issues are filed into a JIRA project of your choice, '
        'labelled with GCP project, service, control, framework(s) and category for triage.</p>'
        '<form method="POST" action="/jira/save" class="grid grid-cols-1 sm:grid-cols-2 gap-3">'
        f'<input name="base_url" value="{esc(jc.base_url)}" placeholder="Site URL — https://your.atlassian.net" class="{inp}"/>'
        f'<input name="email" value="{esc(jc.email)}" placeholder="Account email" class="{inp}"/>'
        f'<input name="project_key" value="{esc(jc.project_key)}" placeholder="Project key — e.g. CC" class="{inp}"/>'
        f'<input name="gcp_project_field" value="{esc(jc.gcp_field)}" placeholder="GCP-project custom field id (optional) — customfield_xxxxx" class="{inp}"/>'
        f'<div class="sm:col-span-2 flex items-center gap-3"><button class="bg-primary text-on-primary rounded-lg px-4 py-2 text-sm font-bold hover:bg-primary/90">Save JIRA</button>'
        f'<span class="text-xs text-on-surface-variant">API {tok}</span></div>'
        '</form></section>')

    content = (
        '<p class="text-base text-on-surface-variant mb-5 max-w-3xl">Policy for your <b>in-scope projects</b>. '
        '<b>Actions</b> are gated by what you set up — <b>PR</b> needs a repo, <b>Slack</b>/<b>Email</b> need '
        'that channel (all in Sources), <b>Issue</b> needs JIRA (below); unavailable ones are greyed out. '
        'Compliance <b>Frameworks</b> require the <b>Security</b> domain to be enabled. Changes save instantly.</p>'
        + jira_card +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        f'<div class="overflow-x-auto"><table class="w-full text-left text-base">{hdr}'
        f'<tbody class="divide-y divide-outline-variant/60">{rows}</tbody></table></div>'
        f'{_pager("/policy", page, total, per)}</section>'
        '<script>function ccPol(el){fetch("/policy/toggle",{method:"POST",'
        'headers:{"Content-Type":"application/x-www-form-urlencoded"},'
        'body:"scope="+encodeURIComponent(el.dataset.scope)+"&field="+encodeURIComponent(el.dataset.field)'
        # toggling the Security domain enables/disables the frameworks - reload to reflect it
        '+"&on="+(el.checked?"1":"")}).then(function(){'
        'if(el.dataset.field==="security")location.reload();});}</script>')
    return _base_page(content)


def render_compliance(project="demo-proj", page=1, scope="overall"):
    from agents.onboarding import OnboardingState
    if not OnboardingState().first_scan_done:
        return _base_page(
            '<div class="min-h-[50vh] flex flex-col items-center justify-center text-center">'
            '<span class="material-symbols-outlined text-on-surface-variant text-3xl mb-3">verified_user</span>'
            '<h2 class="text-lg font-bold text-on-surface">No compliance posture yet</h2>'
            '<p class="text-sm text-on-surface-variant mt-2 max-w-md">Compliance is measured from findings. '
            'Run your first scan to populate the control matrix.</p>'
            '<a href="/board" class="mt-5 px-5 py-2.5 rounded-lg text-sm font-bold bg-primary text-on-primary '
            'hover:bg-primary/90">Go to board → Run first scan</a></div>')
    from agents.compliance import GROUPS, ScopeConfig
    sc = ScopeConfig()
    enabled = sc.as_dict()
    fd = _findings_dicts(project)
    if scope and scope != "overall":
        fd = [f for f in fd if f.get("metadata", {}).get("project") == scope]
    post = compliance_posture(fd, enabled)
    # project filter (default: overall)
    opts = f'<option value="overall"{" selected" if scope in (None, "overall") else ""}>Overall (all projects)</option>'
    for p in sorted(SourcesConfig().selected()):
        opts += f'<option value="{esc(p)}"{" selected" if scope == p else ""}>{esc(p)}</option>'
    filt = ('<div class="flex flex-col items-end gap-1.5 shrink-0 pr-1">'
            '<label class="text-xs text-on-surface-variant whitespace-nowrap">Compliance posture per project</label>'
            '<select onchange="location.href=\'/compliance?scope=\'+this.value" '
            'class="bg-surface border border-outline-variant/50 rounded-md pl-3 pr-8 py-2 text-sm '
            f'min-w-[14rem] max-w-[18rem] focus:outline-none focus:ring-1 focus:ring-primary">{opts}</select></div>')
    from agents.compliance import overall_score
    fails = {}  # rule -> [(resource, fp)]
    for f in fd:
        rule = compliance_rule_for(f)
        if rule:
            fails.setdefault(rule, []).append((f["resource"], f.get("fingerprint", "")))

    # --- customer headline: overall score + Compliant/Non-compliant ---
    compliant = all(p["failing"] == 0 for p in post.values())
    overall = overall_score(post) * 100
    ocol = "primary" if compliant else "error"
    badge = ('<span class="px-3 py-1 rounded text-sm font-bold bg-primary-container text-on-primary-container">COMPLIANT</span>'
             if compliant else
             '<span class="px-3 py-1 rounded text-sm font-bold bg-error-container text-on-error-container">NON-COMPLIANT</span>')
    headline = (
        '<div class="flex items-center justify-between mb-4"><div class="flex items-center gap-4">'
        f'<div class="text-5xl font-bold text-{ocol}">{overall:.0f}%</div>'
        '<div class="space-y-1"><div class="text-sm uppercase tracking-wider text-on-surface-variant">Overall compliance</div>'
        f'{badge}</div></div>{filt}</div>')

    # --- colored cell: framework control id, green=pass / red=fail(click→finding) / — ---
    def cell(rule, fw):
        cid = CONTROLS[rule].get(fw, "")
        if not cid:
            return '<td class="px-3 py-1.5 text-center text-base text-on-surface-variant/40">—</td>'
        if rule in fails:
            flist = fails[rule]
            fp, n = flist[0][1], len(flist)
            badge = f'<sup class="text-[11px] ml-0.5">×{n}</sup>' if n > 1 else ""
            tip = f"{n} finding(s): " + ", ".join(f"{r} ({f})" for r, f in flist)
            return (f'<td class="px-3 py-1.5 text-center bg-error-container/40"><a href="/finding?fp={esc(fp)}" '
                    f'title="{esc(tip)}" class="font-mono text-base text-error font-semibold hover:underline">{esc(cid)}{badge}</a></td>')
        return (f'<td class="px-3 py-1.5 text-center bg-primary-container/40">'
                f'<span class="font-mono text-base text-primary">{esc(cid)}</span></td>')

    rows = ""
    for rule, m in CONTROLS.items():
        failing = rule in fails
        rstat = (f'<span class="inline-flex items-center gap-1.5 text-xs font-bold text-{"error" if failing else "primary"}">'
                 f'<span class="w-2 h-2 rounded-full bg-{"error" if failing else "primary"}"></span>{"FAIL" if failing else "PASS"}</span>')
        cells = "".join(cell(rule, fw) for fw in FRAMEWORKS)
        rows += ('<tr class="hover:bg-surface-container-low transition-colors">'
                 f'<td class="px-3 py-1.5 font-semibold text-base text-on-surface">{esc(m["name"])}</td>'
                 f'<td class="px-3 py-1.5">{rstat}</td>{cells}</tr>')

    foot = '<td colspan="2" class="px-3 py-2 font-bold text-on-surface-variant uppercase tracking-wider text-sm">Framework score</td>'
    for fw in FRAMEWORKS:
        p = post[fw]
        col = "primary" if p["failing"] == 0 else "tertiary" if p["score"] >= 0.5 else "error"
        foot += (f'<td class="px-3 py-2 text-center"><div class="font-bold text-lg text-{col}">{p["score"] * 100:.0f}%</div>'
                 f'<div class="text-xs text-on-surface-variant">{p["passing"]}/{p["total"]}</div></td>')

    th = "".join(f'<th class="px-3 py-2 text-center font-semibold text-sm">{esc(fw)}</th>' for fw in FRAMEWORKS)

    # --- coverage caption: controls in scope vs full catalog ---
    from agents.compliance import FRAMEWORK_TOTALS
    in_scope = sum(p["total"] for p in post.values())
    catalog = sum(FRAMEWORK_TOTALS.values())
    coverage = (f'<p class="text-sm text-on-surface-variant mb-3">Scored against '
                f'<span class="font-semibold text-on-surface">{in_scope}</span> in-scope controls '
                f'of {catalog} across all frameworks — set by the control groups below.</p>')

    # --- scope config: per-framework control groups; mandatory baseline locked ---
    def group_panel(fw):
        chips = ""
        for g in GROUPS[fw]:
            on = g["key"] in enabled[fw]
            if g["locked"]:
                chips += (f'<label class="flex items-center gap-2 px-3 py-2 rounded-md bg-surface-container-low '
                          'border border-outline-variant/40 text-sm opacity-90" title="Mandatory baseline — always in scope">'
                          '<input type="checkbox" checked disabled class="accent-primary w-4 h-4"/>'
                          f'<span class="text-on-surface">{esc(g["name"])}</span>'
                          f'<span class="text-xs text-on-surface-variant">{g["count"]}</span>'
                          '<span class="text-[10px] font-bold text-primary uppercase tracking-wider ml-auto">Required</span></label>')
            else:
                chips += (f'<label class="flex items-center gap-2 px-3 py-2 rounded-md bg-surface '
                          'border border-outline-variant/40 text-sm hover:border-primary/40 cursor-pointer">'
                          f'<input type="checkbox" {"checked" if on else ""} class="accent-primary w-4 h-4" '
                          f'data-fw="{esc(fw)}" data-key="{esc(g["key"])}" onchange="ccScope(this)"/>'
                          f'<span class="text-on-surface">{esc(g["name"])}</span>'
                          f'<span class="text-xs text-on-surface-variant ml-auto">{g["count"]}</span></label>')
        return (f'<div class="space-y-2"><div class="text-xs font-bold uppercase tracking-wider '
                f'text-on-surface-variant mb-1.5">{esc(fw)}</div>{chips}</div>')

    scope_panel = (
        '<section class="bg-surface border border-outline-variant/40 rounded-lg p-4 mt-4">'
        '<div class="flex items-center justify-between mb-1">'
        '<h3 class="text-lg font-bold text-on-surface">Control groups in scope</h3>'
        '<span class="text-xs text-on-surface-variant">Admin · applies to the score above</span></div>'
        '<p class="text-sm text-on-surface-variant mb-3">Enable optional groups to widen the audit denominator. '
        'The baseline group of each framework is mandatory and cannot be disabled.</p>'
        '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">'
        + "".join(group_panel(fw) for fw in FRAMEWORKS) +
        '</div></section>'
        '<script>function ccScope(el){fetch("/compliance/scope/toggle",{method:"POST",'
        'headers:{"Content-Type":"application/x-www-form-urlencoded"},'
        'body:"fw="+encodeURIComponent(el.dataset.fw)+"&key="+encodeURIComponent(el.dataset.key)'
        '+"&on="+(el.checked?"1":"")}).then(function(){location.reload();});}</script>')

    content = (
        headline + coverage +
        '<section class="bg-surface border border-outline-variant/40 rounded-lg overflow-hidden">'
        '<div class="overflow-x-auto"><table class="w-full text-left text-base"><thead>'
        '<tr class="bg-surface-container-low text-sm uppercase tracking-wider text-on-surface-variant border-b border-outline-variant/60">'
        f'<th class="px-3 py-3 font-semibold">Control</th><th class="px-3 py-3 font-semibold">Status</th>{th}</tr></thead>'
        f'<tbody class="divide-y divide-outline-variant/60">{rows}</tbody>'
        f'<tfoot><tr class="border-t-2 border-outline-variant/60 bg-surface-container-low">{foot}</tr></tfoot>'
        '</table></div>'
        '<div class="px-4 py-3 border-t border-outline-variant/50 text-sm text-on-surface-variant">'
        'Red cell = failing control — click it to open the finding. Green = passing. — = not applicable to that framework.</div>'
        '</section>' + scope_panel)
    return _base_page(content)
