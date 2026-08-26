# CloudCap — 4-minute demo script

Rules: ≤4:00, live + unedited backend execution, must show all required pillars +
Google Cloud deployment. Persona-led ("a FinOps/Security lead's morning"), business-ROI
framing. Times are targets; keep total ≤ 3:55 to leave a beat.

**Pillar legend:** REG Registry · RT Runtime · MEM Memory Bank · ID Identity ·
GW Gateway · MA Model Armor · OBS Observability.

---

| Time | Screen / action | Say (narration) | Pillars shown |
|---|---|---|---|
| **0:00–0:18** | Title card → cut to a GCP org with idle VMs, a public bucket, an over-privileged SA. | "Every cloud org quietly leaks money and risk. The worst offenders are the ones nobody tracks. CloudCap is a background agent fleet that finds them and fixes them — safely." | — |
| **0:18–0:45** | Terminal: `terraform apply` on the hub project (pre-run; show the plan/apply tail). Then browser → app opens on the **Setup wizard**. | "The only prerequisite is running our Terraform in one hub project. On first boot, the app onboards itself." | ID, RT |
| **0:45–1:05** | Fill the wizard (git provider + token, repo, Slack) → **Save & run preflight**. Watch checks advance pending→running→pass live. Click **Start first scan**. | "Configure integrations once — tokens go straight to Secret Manager. A preflight verifies access, and only when everything's green does the first scan unlock." | ID, GW |
| **1:05–1:40** | The **board** paints: KPI row (recall, precision, **$584/mo**, ClickOps PASS), findings table with severity chips + stable Finding IDs. | "One read-only scan across the org. Real findings, ranked by dollars — and every issue has a stable ID." | RT, GW, REG |
| **1:40–2:05** | Point at the **audit/OTel panel** — highlight the red `guardrail_block` line. | "One bucket had a filename crafted to hijack the agent — a prompt-injection. Model Armor blocked it and flagged it as a security finding instead. Every step is an OpenTelemetry trace." | MA, OBS |
| **2:05–2:40** | Hover the purple **UNMANAGED · ClickOps** row (the out-of-band VM). Expand its PR. | "This VM is in no Terraform anywhere — created out of band. We still detect it, and from the audit logs we name who made it: a CI service account, resolved through the assumption chain to the real engineer. $240 a month, zero utilization." | MEM, GW |
| **2:40–3:10** | Open a generated **PR.md**: managed diff *and* the ClickOps **codify-then-PR** (flat recovery file + import) — proof + quarantine-first + "agent has NO cloud write access." | "Nothing is changed automatically. Every fix is a Pull Request, backed by deterministic proof, quarantine-first, human-approved. That's how it's safe for production." | GW, OBS |
| **3:10–3:30** | Back on the board: on the idle-VM row pick **1 month**, type reason "compliance runner", click **Accept**. It moves to the **Compliance exceptions** panel; recall stays 7/8. | "Some things must keep running for compliance. Accept it once, with a TTL — it's suppressed but still tracked, and detection accuracy is untouched." | MEM |
| **3:30–3:50** | Re-run the scan (reload). Show "already-tracked suppressed" continuity; the accepted item stays out. Glance at OTel `scan_complete`. | "Next scan remembers everything — accepted exceptions stay quiet, known issues don't re-nag. Persistent memory across weeks." | MEM, OBS |
| **3:50–4:00** | Zoom the scorecard: **7/8 recall · 100% precision · $584/mo · ClickOps PASS**. Tagline card. | "Detected on real data, proven, and fixed as reviewable code. CloudCap — find the waste and risk, prove it, fix it by PR. Safe enough to point at production." | — |

---

## Pillar coverage check (all seven visible on camera)
REG (1:05 registry/agents) · RT (0:18 deploy, 1:05 scan) · MEM (2:05 attribution recall, 3:10 exceptions, 3:30 continuity) ·
ID (0:18 SAs, 0:45 secrets) · GW (0:45 policy, 1:05 read-only routing) · MA (1:40 block) · OBS (1:40 + 3:30 traces).

## Live vs. mock (be honest in voiceover/README, never fake)
- **Live for the video (with credits):** real scan via Recommender/Asset Inventory, real Agent Engine runtime, real Model Armor block, real GitHub PR opening, Cloud Console proof shot.
- **Runs today without credits (mock):** identical UI/flow — wizard, preflight, board, accept/suppress, PRs-to-disk, eval scorecard. Use only for rehearsal; the submitted video must show the live backend + a Cloud Console / `.run` URL.

## Pre-record checklist
- [ ] Reset state: `--fresh-setup`, clear `eval/suppressions.json`, seed `chaos-env` (incl. the out-of-band VM).
- [ ] Cloud Console tab ready for the deployment-proof shot (rules require it).
- [ ] One clean take, no cuts; ≤4:00; English or subtitled; upload public (YouTube/Vimeo).
- [ ] Bonus (+0.6): publish a build blog, post with #AllThingsAgenticHackathon, integrate a Gemma/Veo/Lyria touch.
