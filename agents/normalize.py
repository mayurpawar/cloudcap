"""Normalization helpers shared by live scanners and the deterministic runner.

No ADK / cloud imports here on purpose, so the mock pipeline runs with stdlib only.
"""

from __future__ import annotations

from typing import Any

from agents.ports.interfaces import Finding, Severity


def run_service_to_finding(svc: dict[str, Any]) -> Finding:
    """Turn a Cloud Run utilization record into a governed cost Finding.

    Usage-BASED (actual requests + billable instance-time), catching over-provisioned
    always-on services that GCP's Recommender largely misses. The recommendation is a
    human-gated trade-off (cold-start latency vs. cost), never an autonomous delete."""
    util = float(svc.get("cpuUtilization", 0) or 0)
    reqs = svc.get("requestsPerDay", 0)
    cost = float(svc.get("estMonthlyCostUsd", 0) or 0)
    mins = svc.get("minInstances", 0)
    hours = svc.get("billableInstanceHoursMonthly", 0)
    detail = (f"min-instances={mins} keeps {mins} instance(s) always-on (~{hours}h/mo billed). "
              f"Actual usage ~{reqs} req/day, CPU utilization {util:.1%} — under 1% useful work. "
              f"Deliberately kept warm to avoid cold starts, but ~${cost:.0f}/mo for near-idle capacity.")
    return Finding(
        id=f"run/{svc.get('resource', 'unknown')}",
        category="cost",
        severity=Severity.HIGH if cost >= 100 else Severity.MEDIUM,
        resource=svc.get("resource", "unknown"),
        title="Cloud Run always-on min-instances at <1% utilization",
        detail=detail,
        est_monthly_savings_usd=cost,
        recommended_action=svc.get("recommendedAction", ""),
        metadata={
            "analyzer": "cloud-run-utilization",
            "min_instances": mins,
            "requests_per_day": reqs,
            "cpu_utilization": util,
            "billable_instance_hours": hours,
            "region": svc.get("region"),
        },
    )


def recommendation_to_finding(rec: dict[str, Any]) -> Finding:
    """Turn a Recommender entry (real or fixture) into a normalized cost Finding."""
    units = rec.get("primaryImpact", {}).get("costProjection", {}).get("cost", {}).get("units", 0)
    savings = abs(float(units or 0))
    return Finding(
        id=rec.get("name", "unknown"),
        category="cost",
        severity=Severity.HIGH if savings >= 100 else Severity.MEDIUM,
        resource=rec.get("targetResource", "unknown"),
        title=rec.get("description", "Cost optimization"),
        detail=rec.get("description", ""),
        est_monthly_savings_usd=savings,
        recommended_action=rec.get("recommendedAction", ""),
        metadata={"recommender": rec.get("recommenderSubtype", "")},
    )
