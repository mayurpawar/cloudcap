# CloudCap — UI content spec (for redesign / Stitch)

A structure-only spec (no imposed colors) so the UI can be redesigned cleanly.
Semantic states that MUST stay visually distinct are called out explicitly.

## Product in one line
An autonomous cloud **cost & security governance** tool: scans a GCP org (read-only),
finds waste + risk (including untracked "ClickOps" resources), and ships every fix as a
**human-approved GitOps Pull Request** — safe for production because it never writes to
the cloud directly.

---

## Auth & roles (login + assume-role)

**Login screen** (two methods)
- CloudCap logo + tagline.
- **Sign in with Google** button (OAuth / Identity-Aware Proxy); org-domain restricted.
- **— or —** divider.
- **Email + password** form: email field, password field (masked), **Sign in** button,
  "Forgot password?" link. Inline error area for bad credentials.
- Footer: "Contact your admin for access" (accounts are admin-invited, not self-signup).
- After auth, role is resolved from IAM / Google Group (Google login) or the app user
  record (email/password). Optional MFA step.
- **First-run bootstrap:** the first user (set during Terraform install) is the initial
  Governance Admin, who then invites others and assigns roles.

**Header identity area** (all pages)
- User avatar + name.
- **Active role badge.**
- **Assume-role switcher** — dropdown of the roles the user is entitled to (like GCP
  "view as"); an Admin can drop down to Operator to preview/limit themselves. Active role
  is held in session and gates every control below.

**Roles (two, +optional Viewer)**
| Capability | Governance Admin | Operator (partial) |
|---|---|---|
| View Board & findings | ✓ | ✓ |
| View Compliance posture & audit report | ✓ | ✓ |
| **Governance scope** — edit per-project checks/frameworks | ✓ | view-only |
| Trigger a scan | ✓ | ✓ |
| Accept / suppress finding (TTL) | ✓ | ✓ |
| Restore (un-accept) | ✓ | ✓ |
| **Sources** — edit scan scope | ✓ | view-only |
| **Integrations** — config + Test | ✓ | view status only |
| **Policy** — action matrix | ✓ | view-only |
| **Setup wizard** / re-run onboarding | ✓ | — |
| Manage users & roles | ✓ | — |

Rule of thumb: **Operator triages and acts on findings; Admin configures the system.**
(A read-only **Viewer** is a trivial third role: everything view-only.)

Gating pattern in the UI: config pages render for Operators but inputs/buttons are
disabled with a small "Admin only" hint, rather than hiding the page.

---

## Global shell (all pages)
- **Logo + product name** (CloudCap) + one-line tagline ("Governance Control Plane").
- **Primary nav (5):** Board · Sources · Integrations · Compliance · Policy.
- **Right side:** page-context status chip (e.g. "scan scope 3/5", "12/13 healthy") +
  identity area (avatar, role badge, assume-role switcher).

---

## 1. Board (control plane)
Top → bottom:

**a) Scan-scope strip** — "Scanning N of M projects" + selected project chips + "manage" link.

**b) KPI row — 4 stat cards** (big number + label + sub-caption):
- Recall (found / planted, %) · Precision (%) · Monthly waste found ($) · ClickOps detection (PASS/FAIL).

**c) Compliance-posture strip** — one score card per *enabled* framework
(CIS GCP · SOC 2 · ISO 27001 · PCI DSS): big **%** + "N/M controls passing", color by
health (green all-pass / amber partial / red). Links: "details" → Compliance page,
"audit report". Only frameworks enabled for the in-scope project(s) appear.

**d) Run-cost / ROI panel** — 4 mini-stats + breakdown:
- CloudCap run cost ($/mo) · Waste it finds ($/mo) · **ROI (×)** · cost per scan.
- Breakdown chips: Gemini Pro, Gemini Flash, Memory Bank, Model Armor, Runtime.

**e) Findings table** — columns:
- Finding ID (stable, e.g. CC-b24f9e52)
- **Severity** badge — critical / high / medium / low (**4 distinct colors**)
- Category (cost / security / iam)
- Resource (monospace id)
- Title + optional inline **NEW** / **REOPENED** badge, **+ a small control sub-line**
  (e.g. "Public data exposure: CIS 5.1 · SOC2 CC6.1 · ISO A.9.4.1 · PCI 1.3")
- Est. savings ($/mo)
- **Management source** — Terraform / **UNMANAGED·ClickOps** badge (must stand out)
- **IaC owner** — repo name / **CONFLICT·multi-state** badge / "no IaC"
- **Accept action** (Operator+Admin) — duration dropdown [forever / 1 mo / 1 wk] + reason field + Accept button

**f) Recently resolved panel** — ID · resource · title · "RESOLVED · $X/mo recovered".

**g) Compliance exceptions panel** — ID · resource · reason · TTL (until date / forever) · by-whom · **Restore** button.

**h) Remediation actions list** — per finding: action-kind badge (rightsize / iam-tighten / delete / codify) · branch name or ticket key · resource · outcome (PR OPENED / TICKET / CONFLICT-TRIAGE / SKIPPED) · channel tags (+PR / +issue / +Slack).

**i) Observability log** — reasoning-chain / audit rows: agent · action · detail (guardrail-block rows visually distinct).

---

## 2. Sources (scan scope) — Admin edits; Operator view-only
- Intro line: two-layer note (IAM = hard scope via Terraform; this = soft selection).
- **Org tree grouped by Folder** (name + folder-id); each folder = collapsible group with:
  - a folder-level **"select all" checkbox**
  - **project rows**: checkbox + project id (monospace)
- **Save scan scope** button (Admin only).

---

## 3. Integrations (endpoints + health) — Admin edits; Operator sees status
- Rollup chip: "N/M healthy". · **Test all** button.
- **Integration cards** (grid): GCP scope, IaC state backends, GitHub, Jira, Slack. Each:
  - Name + **status pill** — ✓ HEALTHY / ✗ FAILING / — DISABLED / • UNTESTED (**4 states**)
  - Enabled toggle
  - Config fields (secret fields masked, tagged "→ Secret Manager")
  - Detail line: last check result + timestamp
  - Buttons: Save · Test

---

## 4. Policy (per-project action matrix) — Admin edits; Operator view-only
- **Matrix table**: rows = highlighted **"default (all projects)"** + one per project;
  columns = **Open PR · File issue · Slack notify** (checkbox cells).
- **Save policy** button.
- Helper: "per-project rows override the default."

---

## 5. Compliance (Enterprise Governance / audit) — both roles view
- **Posture cards** — one per *enabled* framework (CIS GCP · SOC 2 · ISO 27001 · PCI DSS):
  big **%** score + "N/M controls passing", health color (green / amber / red).
- **Control matrix table** — columns:
  - **Control** (name, e.g. "Public data exposure")
  - **CIS GCP** · **SOC 2** · **ISO 27001** · **PCI DSS** (control IDs, monospace)
  - **Status** badge — **PASS** (green) / **FAIL** (red)
  - **Evidence** — the resource(s) that trip the control (monospace), or "—"
- **Download audit report** button → continuous-compliance evidence (markdown/PDF).
- Empty state: "No frameworks enabled for the in-scope projects — enable them under Policy → Governance scope."

## 6. Governance scope (per-project) — on the Policy page; Admin edits, Operator view-only
A second matrix beneath the action matrix (own panel):
- **Table**: rows = highlighted **"default (all projects)"** + one per project;
  columns = **Cost · Security · CIS GCP · SOC 2 · ISO 27001 · PCI DSS** (checkbox cells).
- **Save governance scope** button.
- Helper: "A PCI project can be audited to PCI DSS; a sandbox can run cost-only. Disabled
  categories aren't scanned; posture uses only the enabled frameworks."
- (This is what makes governance selectable **per project**, not global.)

## Stitch prompt for the two new frames (match the existing system)
> Two new screens for the CloudCap governance dashboard, **same light theme, green
> accent, sidebar shell, and typography as the existing Board/Sources/Integrations/Policy
> frames**. (1) **Compliance** — a row of 4 framework score cards (CIS GCP, SOC 2,
> ISO 27001, PCI DSS) each showing a big % and "N/M controls passing", above a **control
> matrix table** (Control name, four control-ID columns, PASS/FAIL status badge, Evidence),
> plus a "Download audit report" button. (2) **Governance scope** — a permission-matrix
> table (default row + per-project rows) with checkbox columns Cost, Security, CIS GCP,
> SOC 2, ISO 27001, PCI DSS, and a Save button; place it as a second section on the Policy
> screen. Keep PASS green / FAIL red unambiguous.


> Enterprise **security/FinOps governance control-plane** dashboard. Dense but calm,
> trustworthy, data-first. Needs: a KPI stat row, a primary data table with status badges
> (4 severity levels + lifecycle/ownership tags), list panels, and config screens (forms +
> a tree selector + a permission matrix), plus a login screen and a role switcher in the
> header. Restrained neutral surface with ONE accent, clear typographic hierarchy, generous
> spacing, small monospace for IDs. Avoid neon-on-black "AI dashboard" clichés.

## Multi-cloud roadmap indicator (UI treatment)
CloudCap is **GCP-native** (deploys on GCP). Show the multi-cloud vision **only** as a
single, clearly-labelled, non-interactive greyed indicator — e.g. in the scan-scope strip:
`Clouds: GCP ●  ·  AWS — coming soon  ·  Azure — coming soon`. It must read as roadmap,
never as working functionality (nothing faked). If it risks confusing the demo, drop it —
it's a nice-to-have, not required.

## Preserve in any redesign (meaning, not decoration)
1. **Severity = 4 distinct colors**; **UNMANAGED·ClickOps** and **CONFLICT** badges must stand out.
2. **Status states** (healthy/failing/disabled/untested; new/reopened/resolved) unambiguous.
3. **Role-gated controls** clearly indicated (disabled + "Admin only" hint for Operators).
