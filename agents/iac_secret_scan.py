"""IaC secret scanner — flags hardcoded secrets in a connected repo's Terraform.

CloudCap's GCP pillars read the *running cloud*; this reads the *source of truth*
(the Terraform in the repo bound to a project). It fetches the repo's `*.tf` files
via the GitHub REST API (anonymous for public repos, token for private), and flags
plaintext credentials assigned to secret-named HCL attributes.

Each hit becomes a finding mapped to SOC 2 CC6.1 (compliance.py rule
'hardcoded-secret') and carries a precomputed FIX in metadata — the literal moved to
a `sensitive` Terraform variable — so the PR channel can open a real, reviewable Pull
Request. Read-only: this module never writes to the repo (the PR channel does that).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import urllib.request

_API = "https://api.github.com"

# secret-named HCL attributes; a quoted literal assigned to one of these is a finding.
_SECRET_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey",
                "access_key", "secret_key", "private_key", "client_secret"}
_ASSIGN = re.compile(r'^(?P<indent>\s*)(?P<key>[a-z0-9_]+)\s*=\s*"(?P<val>[^"]*)"\s*$', re.I)
# values that are clearly NOT real secrets (placeholders / interpolations / var refs)
_PLACEHOLDERS = {"", "changeme", "change-me", "todo", "xxx", "example", "placeholder"}


def _gh(url: str, token: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "cloudcap"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _default_branch(repo: str, token: str | None) -> str:
    try:
        return _gh(f"{_API}/repos/{repo}", token).get("default_branch") or "main"
    except Exception:
        return "main"


def _list_tf_files(repo: str, branch: str, token: str | None) -> list[str]:
    tree = _gh(f"{_API}/repos/{repo}/git/trees/{urllib.parse.quote(branch)}?recursive=1", token)
    return [n["path"] for n in tree.get("tree", [])
            if n.get("type") == "blob" and n["path"].endswith(".tf")]


def _get_file(repo: str, path: str, branch: str, token: str | None) -> str:
    d = _gh(f"{_API}/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}", token)
    return base64.b64decode(d["content"]).decode("utf-8", "replace")


def _is_secret(key: str, val: str) -> bool:
    if key.lower() not in _SECRET_KEYS:
        return False
    v = val.strip()
    if v.lower() in _PLACEHOLDERS or v.startswith("${") or v.startswith("var."):
        return False
    return len(v) >= 6  # ignore trivially short values


def _var_name(key: str) -> str:
    return "db_password" if key.lower() in ("password", "passwd") else key.lower()


def _redact(val: str) -> str:
    return (val[:2] + "***" + val[-1:]) if len(val) > 4 else "***"


def _variable_block(var: str) -> str:
    return (f'\nvariable "{var}" {{\n'
            f'  description = "Sensitive value. Provide via TF_VAR_{var} sourced from '
            f'Secret Manager; never hardcode in the repo."\n'
            f'  type        = string\n'
            f'  sensitive   = true\n'
            f'}}\n')


def _fingerprint(repo: str, path: str, key: str, lineno: int) -> str:
    return "scode-" + hashlib.sha1(f"{repo}:{path}:{key}:{lineno}".encode()).hexdigest()[:12]


def _repo_for(project: str) -> str:
    """The GitHub 'owner/name' bound to this project, or '' if none is configured."""
    from agents.project_settings import ProjectSettings
    return (ProjectSettings().get(project).get("repo") or "").strip()


def scan_repo_secrets(project: str, repo: str | None = None,
                      token: str | None = None) -> list[dict]:
    """Return finding dicts (finding_to_dict shape) for hardcoded secrets in the
    Terraform of the repo bound to `project`. Best-effort: any fetch error → []."""
    repo = (repo or _repo_for(project)).strip()
    if not repo or "/" not in repo:
        return []
    import os
    token = token or os.environ.get("GITHUB_TOKEN") or None
    try:
        branch = _default_branch(repo, token)
        paths = _list_tf_files(repo, branch, token)
    except Exception:
        return []

    findings: list[dict] = []
    for path in paths:
        try:
            content = _get_file(repo, path, branch, token)
        except Exception:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            m = _ASSIGN.match(line)
            if not m or not _is_secret(m.group("key"), m.group("val")):
                continue
            key, val, indent = m.group("key"), m.group("val"), m.group("indent")
            var = _var_name(key)
            lineno = i + 1

            # Precompute the fix: this file with the literal → var ref; variables.tf gains
            # a sensitive variable (created if absent). This is what the PR will commit.
            fixed = lines.copy()
            fixed[i] = f"{indent}{key} = var.{var}"
            fix_files = {path: "\n".join(fixed) + ("\n" if content.endswith("\n") else "")}
            if path != "variables.tf":
                try:
                    vf = _get_file(repo, "variables.tf", branch, token)
                except Exception:
                    vf = ""
                if f'variable "{var}"' not in vf:
                    fix_files["variables.tf"] = (vf.rstrip("\n") + "\n" if vf else "") + _variable_block(var)

            findings.append({
                "id": f"compliance/hardcoded-secret/{path}:{lineno}",
                "fingerprint": _fingerprint(repo, path, key, lineno),
                "category": "compliance",
                "severity": "high",
                "resource": f"{repo}/{path}",
                "title": f"Hardcoded secret in Terraform: plaintext credential '{key}'",
                "detail": (f"{repo}/{path}:{lineno} assigns a plaintext value to `{key}` "
                           f"(`{_redact(val)}`). Committing credentials to source control is a "
                           f"SOC 2 CC6.1 violation — the secret is exposed to anyone with repo "
                           f"read access and lives forever in git history."),
                "est_monthly_savings_usd": 0,
                "recommended_action": (f"Replace the literal with `var.{var}` and declare a "
                                       f"`sensitive` variable, sourced from Secret Manager via "
                                       f"TF_VAR_{var}. Then rotate the exposed credential."),
                "metadata": {
                    "project": project,
                    "owner_repo": repo,
                    "management_source": "terraform",
                    "file": path,
                    "line": lineno,
                    "secret_key": key,
                    "fix_kind": "redact-secret",
                    "fix_files": fix_files,
                    "fix_branch": f"cloudcap/redact-secret-{var}",
                },
            })
    return findings
