"""Eval harness — score fleet findings against planted ground-truth.

    python -m eval.score --findings eval/last_findings.json --ground-truth eval/ground_truth.json

Reports precision, recall, $ waste identified, and the UNMANAGED/attribution check.
This is the quantified, verifiable claim the demo makes on camera.
"""

from __future__ import annotations

import argparse
import json


def _match(gt_resource: str, f_resource: str) -> bool:
    a, b = gt_resource.lower(), f_resource.lower()
    return a in b or b in a


def score(findings: list[dict], ground_truth: list[dict]) -> dict:
    matched_gt, missed = [], []
    for gt in ground_truth:
        hit = next(
            (f for f in findings
             if f["category"] == gt["category"] and _match(gt["resource"], f["resource"])),
            None,
        )
        (matched_gt if hit else missed).append(gt)

    true_positive_findings = [
        f for f in findings
        if any(f["category"] == gt["category"] and _match(gt["resource"], f["resource"])
               for gt in ground_truth)
    ]

    recall = len(matched_gt) / len(ground_truth) if ground_truth else 0.0
    precision = len(true_positive_findings) / len(findings) if findings else 0.0
    savings = sum(f.get("est_monthly_savings_usd", 0) for f in findings if f["category"] == "cost")

    # UNMANAGED / attribution check (the ClickOps hero case).
    unmanaged_gt = next((g for g in ground_truth if g.get("unmanaged")), None)
    unmanaged_ok = False
    attribution = {}
    if unmanaged_gt:
        f = next((f for f in findings if _match(unmanaged_gt["resource"], f["resource"])), None)
        if f:
            md = f.get("metadata", {})
            unmanaged_ok = md.get("management_source") == "unmanaged" and bool(md.get("created_by"))
            attribution = {
                "created_by": md.get("created_by"),
                "real_actor": md.get("triggering_entity"),
                "confidence": md.get("attribution_confidence"),
            }

    return {
        "recall": recall, "precision": precision,
        "found": len(matched_gt), "total": len(ground_truth),
        "missed": [g["id"] for g in missed],
        "savings_identified_usd": savings,
        "unmanaged_detection_ok": unmanaged_ok,
        "attribution": attribution,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--findings", default="eval/last_findings.json")
    p.add_argument("--ground-truth", default="eval/ground_truth.json")
    a = p.parse_args()

    findings = json.load(open(a.findings))
    gt = json.load(open(a.ground_truth))["issues"]
    r = score(findings, gt)

    print("\n" + "=" * 56)
    print("  CloudCap — Eval Scorecard")
    print("=" * 56)
    print(f"  Recall (planted issues found) : {r['found']}/{r['total']}  ({r['recall']:.0%})")
    print(f"  Precision (findings that are real): {r['precision']:.0%}")
    print(f"  Monthly waste identified      : ${r['savings_identified_usd']:,.0f}")
    if r["missed"]:
        print(f"  Missed                        : {', '.join(r['missed'])}")
    print("-" * 56)
    print(f"  ClickOps/UNMANAGED detection  : {'PASS' if r['unmanaged_detection_ok'] else 'FAIL'}")
    if r["attribution"]:
        print(f"    created_by  : {r['attribution']['created_by']}")
        print(f"    real actor  : {r['attribution']['real_actor']}")
        print(f"    confidence  : {r['attribution']['confidence']}")
    print("=" * 56 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
