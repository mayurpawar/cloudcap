"""CloudCap self-metering — estimate CloudCap's OWN run cost vs. the waste it finds.

Transparent model over 2026 Google list prices. Deliberately an *estimate* (a single
agent turn bills across several SKUs); the point is the ROI ratio, not the cent.
Excludes optional Security Command Center Premium (off by default).
Verify unit prices on the official Vertex AI pricing page at build time.
"""

from __future__ import annotations

# --- 2026 unit prices -------------------------------------------------------
PRO_IN = 2.00 / 1e6          # Gemini 3.x Pro  $/input token
PRO_OUT = 12.00 / 1e6        #                 $/output token (est.)
FLASH_IN = 0.50 / 1e6        # Gemini 3.x Flash
FLASH_OUT = 3.00 / 1e6
MEMORY_EVENT = 0.25 / 1000   # Sessions + Memory Bank, per event/memory
ARMOR_INSPECTION = 0.001     # Model Armor per inspection (est.)
RUNTIME_PER_SCAN = 0.01      # Agent Engine, scheduled → mostly within free tier

# --- per-scan token assumptions ---------------------------------------------
ORCH_IN, ORCH_OUT = 30_000, 10_000            # orchestrator (Pro)
SCANNER_IN, SCANNER_OUT, N_SCANNERS = 15_000, 4_000, 4   # scanners (Flash)
PR_IN, PR_OUT = 2_000, 1_000                  # per remediation PR body (Flash)

DEFAULT_SCANS_PER_DAY = 4     # every 6 hours


def estimate(findings: int, prs: int, scans_per_day: int = DEFAULT_SCANS_PER_DAY) -> dict:
    llm_pro = ORCH_IN * PRO_IN + ORCH_OUT * PRO_OUT
    llm_flash = (N_SCANNERS * (SCANNER_IN * FLASH_IN + SCANNER_OUT * FLASH_OUT)
                 + prs * (PR_IN * FLASH_IN + PR_OUT * FLASH_OUT))
    memory = (findings * 2 + prs) * MEMORY_EVENT
    armor = (findings + 3) * ARMOR_INSPECTION
    runtime = RUNTIME_PER_SCAN

    per_scan = llm_pro + llm_flash + memory + armor + runtime
    monthly = per_scan * scans_per_day * 30
    return {
        "per_scan": per_scan,
        "monthly": monthly,
        "scans_per_day": scans_per_day,
        "breakdown": {
            "Gemini Pro (orchestrator)": llm_pro,
            "Gemini Flash (scanners + PRs)": llm_flash,
            "Memory Bank events": memory,
            "Model Armor": armor,
            "Agent Runtime": runtime,
        },
    }
