"""Quarantine primitives — reversible-first decommission, per resource type.

No ADK / cloud imports, so both the ADK remediation agent and the deterministic
PR channel (mock mode) can share it. Each type: cut cost/risk WITHOUT destroying
data first; delete only after a soak.
"""

from __future__ import annotations

QUARANTINE_PRIMITIVES: dict[str, dict[str, str]] = {
    "compute.instance": {
        "reversible": "stop instance + snapshot boot/attached disks",
        "terminal":   "delete instance after soak (snapshot retained)",
    },
    "compute.disk": {
        "reversible": "snapshot the disk",
        "terminal":   "delete unattached disk after soak (snapshot retained)",
    },
    "sql.instance": {
        "reversible": "take a final on-demand backup/export; stop the instance",
        "terminal":   "delete instance after soak (backup retained)",
    },
    "storage.bucket": {
        # Do NOT delete data. Neutralize exposure only.
        "reversible": "strip public IAM (allUsers/allAuthenticatedUsers) + attach deny-all; enable versioning",
        "terminal":   "(data preserved) optionally archive/lifecycle after owner sign-off",
    },
    "iam.serviceAccount": {
        "reversible": "disable SA + remove over-broad role bindings (keep the SA)",
        "terminal":   "delete SA after soak if confirmed unused",
    },
    "compute.address": {
        "reversible": "no reversible step needed; release is safe once unattached",
        "terminal":   "release the reserved static IP",
    },
}


def quarantine_for(resource_type: str) -> dict[str, str]:
    """Return the reversible-first quarantine primitive for a resource type."""
    return QUARANTINE_PRIMITIVES.get(
        resource_type,
        {"reversible": "isolate/downscale without deleting data", "terminal": "delete after owner sign-off"},
    )
