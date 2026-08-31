"""Firebase Authentication for the CloudCap dashboard (USER auth).

This authenticates humans logging into the dashboard. It is SEPARATE from how CloudCap
authenticates to GCP to read data (that's ADC / Workload Identity on the service side).

Flow: the browser signs in with the Firebase JS SDK and posts the resulting ID token
here; we verify it with the Firebase Admin SDK and map the email to a role.

Graceful by design: if firebase-admin isn't installed or configured, `available()` is
False and the dashboard keeps its dev login — local work is never blocked.

Env:
  GOOGLE_CLOUD_PROJECT / FIREBASE_PROJECT_ID : project id (cloud-cap-506110)
  CLOUDCAP_ADMINS                            : comma-separated admin emails
"""

from __future__ import annotations

import json
import os

_HERE = os.path.dirname(__file__)
_WEB_CONFIG_FILE = os.path.join(_HERE, "firebase_web.json")
_USERS_FILE = os.path.join(_HERE, "users.json")


def _load_users() -> dict:
    """Authorized users → role. Source: webui/users.json, overlaid by env
    (CLOUDCAP_ADMINS / CLOUDCAP_OPERATORS). When non-empty it's an ALLOWLIST — only
    these emails may sign in. Empty = dev mode (any verified user, as operator)."""
    users: dict[str, str] = {}
    if os.path.exists(_USERS_FILE):
        try:
            users.update({str(k).strip().lower(): str(v).strip().lower()
                          for k, v in json.load(open(_USERS_FILE)).items()})
        except (ValueError, OSError):
            pass
    for e in os.environ.get("CLOUDCAP_ADMINS", "").split(","):
        if e.strip():
            users[e.strip().lower()] = "admin"
    for e in os.environ.get("CLOUDCAP_OPERATORS", "").split(","):
        if e.strip():
            users.setdefault(e.strip().lower(), "operator")
    return users


# Friendly display names for shared/role accounts (e.g. hackathon judges), so the
# dashboard greets them by role rather than a bare email. These win over the identity
# provider's name. Overlaid by env CLOUDCAP_DISPLAY_NAMES = "email:Name,email:Name".
_DISPLAY_NAMES = {
    "testing@devpost.com": "DevPost Judge",
    "cloudhackathons@google.com": "Hackathon Judge",
}


def _display_names() -> dict:
    names = dict(_DISPLAY_NAMES)
    for pair in os.environ.get("CLOUDCAP_DISPLAY_NAMES", "").split(","):
        e, _, n = pair.partition(":")
        if e.strip() and n.strip():
            names[e.strip().lower()] = n.strip()
    return names


_app = None
_init_failed = False
# Last verification/init failure reason (surfaced to the client + server log).
last_error = ""


def _firebase_project_id() -> str | None:
    """The FIREBASE (auth) project id — verifies token audience. May differ from the
    data/hub project (GOOGLE_CLOUD_PROJECT). Order: env → web config file → hub."""
    pid = os.environ.get("FIREBASE_PROJECT_ID")
    if pid:
        return pid
    cfg = web_config()
    if cfg and cfg.get("projectId"):
        return cfg["projectId"]
    return os.environ.get("GOOGLE_CLOUD_PROJECT")


def _ensure_app():
    """Lazily initialize the Firebase Admin app for the AUTH project. Returns app/None.

    Token verification checks the JWT against Google's public certs and that the
    audience == this project id, so the auth project must match the tokens' project.
    """
    global _app, _init_failed, last_error
    if _app is not None or _init_failed:
        return _app
    try:
        import firebase_admin
        if firebase_admin._apps:
            _app = firebase_admin.get_app()
        else:
            project = _firebase_project_id()
            _app = firebase_admin.initialize_app(options={"projectId": project} if project else None)
    except Exception as exc:
        import sys
        _init_failed = True
        _app = None
        last_error = f"init: {type(exc).__name__}: {exc}"
        print(f"[auth] initialize_app failed: {last_error}", file=sys.stderr)
    return _app


def available() -> bool:
    """True when Firebase verification is usable (SDK installed + app initialized)."""
    return _ensure_app() is not None


def role_for(email: str) -> str:
    return _load_users().get((email or "").lower(), "operator")


def provider() -> str:
    """The active auth provider. CLOUDCAP_AUTH_PROVIDER = firebase | oidc | proxy | dev.
    Auto-detects: firebase if a web config is present, else dev (local login)."""
    p = os.environ.get("CLOUDCAP_AUTH_PROVIDER", "").strip().lower()
    if p:
        return p
    return "firebase" if web_config() else "dev"


def _apply_allowlist(email: str | None, name: str | None, picture: str | None) -> dict | None:
    """Provider-agnostic gate: map a verified identity to an allowed CloudCap session.

    This is the SHARED core every provider funnels through — the allowlist + role
    mapping (webui/users.json) and session shape do not depend on how the identity was
    proven (Firebase / OIDC / IAP / dev). None → not authorized (reason in last_error).
    """
    global last_error
    email = (email or "").lower()
    users = _load_users()
    if users and email not in users:
        last_error = f"{email or 'this account'} is not an authorized CloudCap user"
        return None
    role = users.get(email, "operator")
    return {
        "email": email,
        "name": _display_names().get(email) or name or email or "user",
        "picture": picture,
        "role": role,
        "roles": ["admin", "operator"] if role == "admin" else ["operator"],
    }


def identity_from_proxy(headers) -> dict | None:
    """Trust an identity asserted by an authenticating reverse proxy / Google IAP.

    Enterprises commonly terminate SSO (AD/Okta/Workspace) at a gateway that injects
    the verified user in a header. CloudCap runs behind it and trusts the header — the
    simplest enterprise integration (no IdP wiring in the app). Only enable when the app
    is NOT directly reachable (the proxy must be the sole ingress), else headers spoof.
      Google IAP:  X-Goog-Authenticated-User-Email
      oauth2-proxy: X-Auth-Request-Email / X-Auth-Request-User
      generic:     X-Forwarded-Email / X-Forwarded-User
    """
    email = (headers.get("X-Goog-Authenticated-User-Email")
             or headers.get("X-Auth-Request-Email")
             or headers.get("X-Forwarded-Email"))
    if email and ":" in email:      # IAP prefixes "accounts.google.com:"
        email = email.split(":")[-1]
    if not email:
        return None
    name = (headers.get("X-Auth-Request-User") or headers.get("X-Forwarded-User") or email)
    return _apply_allowlist(email, name, None)


def verify_oidc_token(token: str) -> dict | None:
    """Verify a generic OIDC ID token — the enterprise path for Active Directory /
    Entra ID (Azure AD), Okta, Ping, Google Workspace, any OIDC IdP.

    Validates the JWT signature against the issuer's JWKS and checks iss/aud/exp, then
    maps standard OIDC claims (email, name, picture) through the shared allowlist.
    Config: CLOUDCAP_OIDC_ISSUER, CLOUDCAP_OIDC_AUDIENCE (client id).
    """
    global last_error
    issuer = os.environ.get("CLOUDCAP_OIDC_ISSUER")
    audience = os.environ.get("CLOUDCAP_OIDC_AUDIENCE")
    if not issuer or not audience:
        last_error = "OIDC not configured (set CLOUDCAP_OIDC_ISSUER + CLOUDCAP_OIDC_AUDIENCE)"
        return None
    try:
        # PyJWT + PyJWKClient: fetch signing keys from the IdP and verify.
        import jwt
        from jwt import PyJWKClient
        jwks = PyJWKClient(issuer.rstrip("/") + "/.well-known/jwks.json")
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=issuer)
    except Exception as exc:
        last_error = f"OIDC verify failed: {type(exc).__name__}: {exc}"
        return None
    return _apply_allowlist(claims.get("email"), claims.get("name"), claims.get("picture"))


def verify_id_token(token: str) -> dict | None:
    """Verify a Firebase ID token. Returns a session dict, or None if invalid.

    {uid, email, name, picture, role, roles} — drops straight into a server session.
    """
    global last_error
    last_error = ""
    if not token:
        last_error = "no idToken in request"
        return None
    if not _ensure_app():
        if not last_error:  # keep the specific init error if _ensure_app set one
            last_error = "Firebase Admin app not initialized (firebase-admin missing or no project id)"
        return None
    try:
        from firebase_admin import auth as fb_auth
        decoded = fb_auth.verify_id_token(token)
    except Exception as exc:
        import sys
        last_error = f"{type(exc).__name__}: {exc}"
        print(f"[auth] verify_id_token failed: {last_error}", file=sys.stderr)
        return None
    ident = _apply_allowlist(decoded.get("email"), decoded.get("name"), decoded.get("picture"))
    if ident:
        ident["uid"] = decoded.get("uid")
        if decoded.get("admin") is True or decoded.get("role") == "admin":
            ident.update(role="admin", roles=["admin", "operator"])  # custom claim wins
    return ident


def web_config() -> dict | None:
    """Public Firebase web-app config for the browser SDK. None if not configured.

    Source order: env (FIREBASE_API_KEY…) → webui/firebase_web.json. apiKey is a PUBLIC
    identifier (safe to serve to the browser), not a secret.
    """
    api_key = os.environ.get("FIREBASE_API_KEY")
    if api_key:
        project = os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        return {
            "apiKey": api_key,
            "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", f"{project}.firebaseapp.com"),
            "projectId": project,
            "appId": os.environ.get("FIREBASE_APP_ID", ""),
            "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
            "messagingSenderId": os.environ.get("FIREBASE_SENDER_ID", ""),
        }
    if os.path.exists(_WEB_CONFIG_FILE):
        try:
            cfg = json.load(open(_WEB_CONFIG_FILE))
            if cfg.get("apiKey"):
                return cfg
        except (ValueError, OSError):
            pass
    return None
