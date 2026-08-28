"""Serve the polished Stitch UI wired to live CloudCap data + interactivity.

    python -m webui.serve            # http://localhost:8090

Auth: login gate (email/password + Google), session, role switcher (Admin/Operator),
logout. Data screens render live; more actions (Save/Accept/Test) land next.
(Tailwind/fonts via CDN — vendored later.)
"""

from __future__ import annotations

import argparse
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from webui.sessions import build_sessions

from webui import onboarding as ob
from webui.render import (neutralize, normalize_layout, render_board, render_compliance,
                          render_docs, render_finding, render_history, render_hub,
                          render_integrations, render_login, render_logs, render_not_ready,
                          render_policy, render_sources, restyle, shell)


def _final(html):
    return restyle(neutralize(html))

ROUTES = {
    "/": render_board, "/board": render_board, "/sources": render_sources,
    "/integrations": render_integrations, "/compliance": render_compliance,
    "/policy": render_policy, "/hub": render_hub, "/docs": render_docs,
    "/history": render_history,
}

# Per-user sessions keyed by an opaque cookie id. Firestore-backed on deploy (survives
# Cloud Run cold starts + multi-instance), in-memory for local dev — see webui.sessions.
SESSIONS = build_sessions()
COOKIE_NAME = "cc_session"
_ANON = {"logged_in": False, "user": "", "name": "", "email": "", "picture": None,
         "role": "operator", "roles": []}


def _new_session(**data) -> str:
    return SESSIONS.create({**_ANON, "logged_in": True, **data})


def _save_project_settings(g):
    """Persist one project's repo + typed channels + freeze from a setup form (`g` reads
    a single form value by name). Shared by the Projects page and onboarding."""
    from agents.freeze import FreezeStore
    from agents.project_settings import CHANNEL_KEYS, ProjectSettings
    proj = g("project")
    if not proj:
        return
    channels = [{"type": k, "value": g(f"chv_{k}")} for k in CHANNEL_KEYS if g(f"ch_{k}")]
    ProjectSettings().set(proj, g("repo"), channels)
    fz = FreezeStore()
    if g("freeze"):
        fz.set(proj, g("freeze_until") or None, "code freeze (per-project setup)")
    else:
        fz.clear(proj)


def _session_cookie(sid: str) -> str:
    return f"{COOKIE_NAME}={sid}; HttpOnly; SameSite=Lax; Path=/"


def _clear_cookie() -> str:
    return f"{COOKIE_NAME}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/"


def make_handler(project):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, code=200, ctype="text/html; charset=utf-8", set_cookie=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to="/", set_cookie=None):
            self.send_response(303)
            self.send_header("Location", to)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.end_headers()

        def _sid(self):
            # Parse the Cookie header MANUALLY: http.cookies.SimpleCookie chokes on
            # cookies with JSON/special-char values (e.g. Google Sign-In's `g_state`)
            # and would then drop our cc_session entirely.
            raw = self.headers.get("Cookie", "")
            prefix = COOKIE_NAME + "="
            for part in raw.split(";"):
                part = part.strip()
                if part.startswith(prefix):
                    return part[len(prefix):]
            return None

        def _session(self):
            """The signed-in session for THIS request's cookie (or None)."""
            sid = self._sid()
            return SESSIONS.get(sid) if sid else None

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/favicon"):
                self.send_response(204); self.end_headers(); return
            if self.path == "/logout":
                sid = self._sid()
                if sid:
                    SESSIONS.delete(sid)
                return self._redirect("/", set_cookie=_clear_cookie())
            auth = self._session()
            if not auth:
                # Trusted-proxy / IAP mode: identity is asserted by the gateway on every
                # request (no login page, no cookie needed) — the enterprise SSO path.
                from webui import auth as _wa
                if _wa.provider() == "proxy":
                    ident = _wa.identity_from_proxy(self.headers)
                    if ident:
                        auth = {**ident, "logged_in": True, "user": ident["name"] or ident["email"]}
                if not auth:
                    return self._send(_final(render_login()))

            from agents.onboarding import OnboardingState
            state = OnboardingState()

            # Onboarding wizard (focused layout — no app sidebar).
            wiz = {
                "/onboarding": ob.render_welcome(auth),
                "/onboarding/discover": ob.render_discover(),
                "/onboarding/policy": ob.render_policy(),
                "/onboarding/integrations": ob.render_integrations(),
                "/onboarding/review": ob.render_review(),
            }
            is_admin = auth["role"] == "admin"
            # Onboarding wizard is ADMIN-only.
            if self.path in wiz:
                if not is_admin:
                    return self._send(_final(shell(render_not_ready(auth), "/board", project, auth)))
                return self._send(_final(normalize_layout(wiz[self.path])))

            # Before the FIRST scan: admins may freely REVIEW/adjust their setup
            # (Sources, Policy, Integrations, Hub). Only the findings-based pages
            # (board, compliance, finding) show a "run first scan" state until then.
            if not state.first_scan_done:
                if is_admin:
                    if not state.completed:
                        return self._redirect("/onboarding")           # finish setup first
                    # else: fall through — config pages render; findings pages self-gate.
                else:
                    # Operators/viewers: the hub isn't ready — no live data for anyone yet.
                    return self._send(_final(shell(render_not_ready(auth), "/board", project, auth)))

            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            query = dict(urllib.parse.parse_qsl(parts.query))
            try:
                page = max(1, int(query.get("page", "1")))
            except ValueError:
                page = 1

            if path == "/compliance":
                content = render_compliance(project, page, query.get("scope", "overall"))
                html = normalize_layout(shell(content, "/compliance", project, auth))
                return self._send(_final(html))

            if path == "/finding":
                if not state.first_scan_done:
                    return self._redirect("/board")   # no findings to inspect pre-scan
                fp = query.get("fp", "")
                html = normalize_layout(shell(render_finding(project, fp), "/board", project, auth,
                                              title=f"Finding · {fp}"))
                return self._send(_final(html))

            if path == "/integrations":
                # Superseded by per-project repo/channels on the Sources page.
                return self._redirect("/sources")

            if path == "/sources":
                content = render_sources(project, page, setup=query.get("setup"))
                html = normalize_layout(shell(content, "/sources", project, auth))
                return self._send(_final(html))

            if path == "/docs":
                content = render_docs(project, query.get("topic"))
                html = normalize_layout(shell(content, "/docs", project, auth))
                return self._send(_final(html))

            if path == "/logs":
                from agents.audit_reader import read_audit
                agent = query.get("agent")
                entries = read_audit(agent=agent, limit=120)
                if agent:
                    title, subtitle, back = (f"Agent logs · {agent}",
                                             f"Cloud Logging audit events emitted by the {agent} agent.", "/hub")
                else:
                    title, subtitle, back = ("Scan audit log",
                                             "The immutable, hash-chained Cloud Logging audit trail — most recent governance events.",
                                             "/history")
                content = render_logs(project, entries, title, subtitle, back, back)
                html = normalize_layout(shell(content, back, project, auth))
                return self._send(_final(html))

            active = "/board" if path in ("/", "/board") else path
            try:
                page = max(1, int(query.get("page", "1")))
            except ValueError:
                page = 1
            renderer = ROUTES.get(path, render_board)
            paged = (render_board, render_compliance, render_sources, render_policy, render_history)
            content = renderer(project, page) if renderer in paged else renderer(project)
            html = normalize_layout(shell(content, active, project, auth))
            return self._send(_final(html))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""

            # Token sign-in (Firebase or generic OIDC): verify server-side, open session.
            if self.path in ("/auth/firebase", "/auth/oidc"):
                import json as _json
                from webui import auth as _wa
                try:
                    token = _json.loads(raw.decode() or "{}").get("idToken", "")
                except Exception:
                    token = ""
                verify = _wa.verify_id_token if self.path.endswith("firebase") else _wa.verify_oidc_token
                verified = verify(token)
                if not verified:
                    return self._send(_wa.last_error or "unauthorized", code=401)
                sid = _new_session(user=verified["name"] or verified["email"], name=verified["name"],
                                   email=verified["email"], picture=verified.get("picture"),
                                   role=verified["role"], roles=verified["roles"])
                return self._send("ok", set_cookie=_session_cookie(sid))

            form = urllib.parse.parse_qs(raw.decode()) if raw else {}
            g = lambda k, d="": form.get(k, [d])[0]

            if self.path == "/login":
                if g("method") == "google":
                    sid = _new_session(user="admin@acme.com (Google)", name="Admin",
                                       email="admin@acme.com", role="admin", roles=["admin", "operator"])
                else:
                    email = g("email") or "user@acme.com"
                    role = "admin" if "admin" in email.lower() else "operator"
                    sid = _new_session(user=email, name=email, email=email, role=role,
                                       roles=["admin", "operator"])
                return self._redirect("/", set_cookie=_session_cookie(sid))

            auth = self._session()
            if not auth:
                return self._redirect("/")
            if self.path == "/assume-role":
                if g("role") in auth["roles"]:
                    auth["role"] = g("role")
                    SESSIONS.save(self._sid(), auth)  # persist the switch (Firestore/mem)
                return self._redirect(self.headers.get("Referer", "/"))

            # --- Onboarding wizard steps (admin-only) ---
            if self.path.startswith("/onboarding/") or self.path == "/scan/run":
                from datetime import date

                from agents.freeze import FreezeStore
                from agents.governance import GOV_OPTIONS, GovernanceConfig
                from agents.onboarding import OnboardingState
                from agents.policy import ACTIONS, ActionPolicy
                from agents.project_settings import ProjectSettings
                from agents.sources import SourcesConfig, all_projects
                if auth["role"] != "admin":
                    return self._redirect("/")
                st = OnboardingState()

                if self.path == "/onboarding/start":
                    return self._redirect("/onboarding/discover")
                if self.path == "/onboarding/rediscover":
                    from agents.discovery import discover_projects
                    discover_projects(refresh=True)
                    return self._redirect("/onboarding/discover")
                if self.path == "/onboarding/project/save":
                    _save_project_settings(g)
                    return self._redirect("/onboarding/integrations")
                if self.path == "/onboarding/sources":
                    SourcesConfig().set_selected(form.get("proj", []))
                    return self._redirect("/onboarding/policy")
                if self.path == "/onboarding/policy":
                    default = {o: bool(g(f"gov__{o}")) for o in GOV_OPTIONS}
                    GovernanceConfig().save_all(default, GovernanceConfig().overrides)
                    ActionPolicy().save_all({a: bool(g(f"act__{a}")) for a in ACTIONS},
                                            ActionPolicy().overrides)
                    return self._redirect("/onboarding/integrations")
                if self.path == "/onboarding/integrations":
                    ps, fz = ProjectSettings(), FreezeStore()
                    for p in (SourcesConfig().selected() or all_projects()):
                        ps.set(p, g(f"repo__{p}"), g(f"channel__{p}"))
                        if g(f"freeze__{p}"):
                            fz.set(p, g(f"freeze_until__{p}") or None, "code freeze (set at onboarding)")
                        else:
                            fz.clear(p)
                    return self._redirect("/onboarding/review")
                if self.path == "/onboarding/finish":
                    st.set(completed=True, step="review")
                    return self._redirect("/board")
                if self.path == "/scan/run":
                    # A REAL scan: run the fleet, persist the lifecycle baseline, audit
                    # it — then mark the milestone. (Reused by the scheduled daily scan.)
                    import asyncio

                    from agents.scan import run_scan
                    scan_mode = os.environ.get("CLOUDCAP_SCAN_MODE", "mock")
                    scan_proj = os.environ.get("CLOUDCAP_SCAN_PROJECT", project)
                    asyncio.run(run_scan(scan_proj, mode=scan_mode, durable_audit=True))
                    st.set(first_scan_done=True)
                    return self._redirect("/board")
                return self._redirect("/onboarding")

            back = self.headers.get("Referer", "/")

            # Accept/suppress a finding (both roles may triage).
            if self.path == "/suppress":
                from datetime import date
                from agents.suppressions import Suppression, SuppressionStore, parse_duration
                fp = g("fingerprint")
                if fp:
                    until = parse_duration(g("duration", "forever"), date.today())
                    SuppressionStore().add(Suppression(
                        fingerprint=fp, resource=g("resource"),
                        reason=g("reason") or "accepted via dashboard", until=until,
                        created_by=auth["user"], created_at=date.today().isoformat()))
                return self._redirect(back)

            # Config mutations are Admin-only.
            admin_routes = {"/sources/save", "/sources/toggle", "/sources/discover",
                            "/sources/project/save", "/policy/save", "/policy/toggle",
                            "/governance/save", "/jira/save", "/compliance/scope/toggle"}
            if self.path in admin_routes and auth["role"] != "admin":
                return self._redirect(back)

            if self.path == "/jira/save":
                from agents.jira import JiraConfig
                JiraConfig().save(g("base_url"), g("email"), g("project_key"), g("gcp_project_field"))
                return self._redirect("/policy")

            if self.path == "/sources/discover":
                from agents.discovery import discover_projects
                discover_projects(refresh=True)
                return self._redirect("/sources")
            if self.path == "/sources/project/save":
                _save_project_settings(g)
                return self._redirect("/sources")

            if self.path == "/compliance/scope/toggle":
                from agents.compliance import ScopeConfig
                ScopeConfig().toggle(g("fw"), g("key"), bool(g("on")))
                return self._redirect(back)

            if self.path in ("/integrations/test", "/integrations/testall"):
                from datetime import datetime
                from agents.integrations import IntegrationsStore, run_check
                store = IntegrationsStore()
                ts = datetime.now().strftime("%H:%M:%S")
                targets = store.items if self.path.endswith("testall") else [store.get(g("id"))]
                for integ in filter(None, targets):
                    status, detail = run_check(integ)
                    store.set_result(integ["id"], status, detail, ts)
                return self._redirect(back)
            if self.path == "/integrations/add":
                from agents.integrations import IntegrationsStore
                IntegrationsStore().add(g("name") or "Integration", g("kind"), g("endpoint"), g("token"))
                return self._redirect("/integrations")
            if self.path == "/integrations/update":
                from agents.integrations import IntegrationsStore
                store = IntegrationsStore()
                integ = store.get(g("id"))
                if integ:
                    cfg = {f["key"]: g(f["key"]) for f in integ["fields"]}
                    store.update(g("id"), cfg, bool(g("enabled")))
                return self._redirect("/integrations")

            if self.path == "/policy/toggle":
                from agents.governance import GovernanceConfig
                from agents.policy import ActionPolicy
                scope, field, on = g("scope"), g("field"), bool(g("on"))
                from agents.policy import ACTIONS as _POLICY_ACTIONS
                if field in _POLICY_ACTIONS:
                    pol = ActionPolicy()
                    if scope == "default":
                        d = dict(pol.default); d[field] = on; pol.save_all(d, pol.overrides)
                    else:
                        ov = dict(pol.overrides); cur = dict(pol.channels_for(scope)); cur[field] = on
                        ov[scope] = cur; pol.save_all(pol.default, ov)
                else:
                    gov = GovernanceConfig()
                    if scope == "default":
                        d = dict(gov.default); d[field] = on; gov.save_all(d, gov.overrides)
                    else:
                        ov = dict(gov.overrides); cur = dict(gov.profile_for(scope)); cur[field] = on
                        ov[scope] = cur; gov.save_all(gov.default, ov)
                return self._redirect(back)

            if self.path == "/sources/toggle":
                from agents.sources import SourcesConfig
                sc = SourcesConfig()
                sel = sc.selected()
                proj = g("project")
                (sel.add if g("on") else sel.discard)(proj)
                sc.set_selected(sel)
                return self._redirect(back)
            if self.path == "/sources/save":
                from agents.sources import SourcesConfig
                SourcesConfig().set_selected(form.get("proj", []))
                return self._redirect(back)
            if self.path == "/policy/save":
                from agents.policy import ACTIONS, ActionPolicy
                from agents.sources import all_projects
                default = {a: bool(g(f"{a}__default")) for a in ACTIONS}
                overrides = {p: {a: bool(g(f"{a}__{p}")) for a in ACTIONS} for p in all_projects()}
                ActionPolicy().save_all(default, overrides)
                return self._redirect(back)
            if self.path == "/governance/save":
                from agents.governance import GOV_OPTIONS, GovernanceConfig
                from agents.sources import all_projects
                default = {o: bool(g(f"{o}__default")) for o in GOV_OPTIONS}
                overrides = {p: {o: bool(g(f"{o}__{p}")) for o in GOV_OPTIONS} for p in all_projects()}
                GovernanceConfig().save_all(default, overrides)
                return self._redirect(back)

            self._send("not found", code=404)

        def log_message(self, *a):
            pass

    return Handler


def main():
    p = argparse.ArgumentParser(description="CloudCap polished UI (live data + auth)")
    # Cloud Run injects $PORT and expects the container to listen on 0.0.0.0:$PORT.
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8090)))
    p.add_argument("--project", default=os.environ.get("CLOUDCAP_PROJECT", "demo-proj"))
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    a = p.parse_args()
    # Bind to all interfaces when containerized (Cloud Run), localhost otherwise.
    host = "0.0.0.0" if os.environ.get("PORT") else a.host  # noqa: S104 (container ingress)
    # Startup diagnostic: does Firestore import + connect in this container?
    if os.environ.get("CLOUDCAP_STORE", "").lower() == "firestore":
        try:
            from agents.store import firestore_client
            firestore_client().collection("cloudcap").document("_diag").get()
            print(f"[diag] firestore OK (db={os.environ.get('CLOUDCAP_FIRESTORE_DB') or '(default)'})", flush=True)
        except Exception as _e:
            print(f"[diag] firestore FAILED: {type(_e).__name__}: {str(_e)[:260]}", flush=True)

    srv = HTTPServer((host, a.port), make_handler(a.project))
    print(f"CloudCap UI → http://{host}:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
