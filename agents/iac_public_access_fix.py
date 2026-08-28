"""Cloud→IaC fix generator for public-access findings.

Detection is 100% from the LIVE cloud (google_geap `_public_buckets` reads the
bucket's real IAM policy — it never parses Terraform to *find* the problem). This
module runs only AFTER a public-exposure finding exists and its owning repo has been
resolved from Terraform state. Its sole job is to *write the fix*: fetch the owning
repo's `*.tf`, locate the HCL block that grants `allUsers` / `allAuthenticatedUsers`
on the offending bucket, delete that block, and hand back the edited file contents so
the PR channel can open a real, reviewable Pull Request.

This is the cloud-custodian model: catch drift/ClickOps on the live cloud, reconcile
the fix back into IaC. Read-only against the repo (the PR channel does the writing).
"""

from __future__ import annotations

import os
import re

from agents.iac_secret_scan import _default_branch, _get_file, _list_tf_files

_PUBLIC_MEMBERS = ("allusers", "allauthenticatedusers")
_RESOURCE_HEAD = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')


def _block_bounds(lines: list[str], start: int) -> int:
    """Return the index of the line closing the HCL block that opens at `start`
    (the `resource ... {` line), by brace-matching. Falls back to `start`."""
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            return i
    return start


def _grants_public(body: str) -> bool:
    """True if this resource block grants access to allUsers/allAuthenticatedUsers."""
    m = re.search(r'members?\s*=\s*(.+)', body, re.I | re.S)
    if not m:
        return False
    return any(pm in m.group(1).lower() for pm in _PUBLIC_MEMBERS)


def _remove_public_blocks(content: str) -> tuple[str, list[str]]:
    """Delete every `*_iam_member`/`*_iam_binding` block granting a public member.
    Returns (new_content, [removed tf addresses]). Also swallows one immediately
    preceding comment line and collapses the blank line left behind."""
    lines = content.splitlines()
    remove = [False] * len(lines)
    removed: list[str] = []
    i = 0
    while i < len(lines):
        m = _RESOURCE_HEAD.match(lines[i])
        if m and ("iam_member" in m.group(1) or "iam_binding" in m.group(1)):
            end = _block_bounds(lines, i)
            body = "\n".join(lines[i:end + 1])
            if _grants_public(body):
                start = i
                # swallow the block's preceding comment lines (e.g. "# PUBLIC access ...")
                while start > 0 and lines[start - 1].lstrip().startswith("#"):
                    start -= 1
                for j in range(start, end + 1):
                    remove[j] = True
                # collapse a trailing blank line so we don't leave a double gap
                if end + 1 < len(lines) and not lines[end + 1].strip():
                    remove[end + 1] = True
                removed.append(f"{m.group(1)}.{m.group(2)}")
            i = end + 1
            continue
        i += 1
    if not removed:
        return content, []
    kept = [ln for k, ln in enumerate(lines) if not remove[k]]
    new = "\n".join(kept)
    if content.endswith("\n"):
        new += "\n"
    return new, removed


def build_public_access_fix(repo: str, token: str | None = None) -> dict | None:
    """Fetch `repo`'s Terraform and compute the fix that removes public IAM bindings.

    Returns a metadata patch (fix_kind/fix_files/fix_branch/tf_removed) ready to attach
    to the finding, or None if nothing public was found in the IaC / the repo is
    unreachable. Best-effort and read-only."""
    repo = (repo or "").strip()
    if not repo or "/" not in repo:
        return None
    token = token or os.environ.get("GITHUB_TOKEN") or None
    try:
        branch = _default_branch(repo, token)
        paths = _list_tf_files(repo, branch, token)
    except Exception:
        return None

    fix_files: dict[str, str] = {}
    removed_all: list[str] = []
    for path in paths:
        try:
            content = _get_file(repo, path, branch, token)
        except Exception:
            continue
        if not any(pm in content.lower() for pm in _PUBLIC_MEMBERS):
            continue
        new_content, removed = _remove_public_blocks(content)
        if removed:
            fix_files[path] = new_content
            removed_all.extend(removed)

    if not fix_files:
        return None
    return {
        "fix_kind": "remove-public-iam",
        "fix_files": fix_files,
        "fix_branch": "cloudcap/remove-public-access",
        "tf_removed": removed_all,
    }
