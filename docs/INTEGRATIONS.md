# CloudCap — Integrations & Support

This document is the source of truth for how CloudCap connects to your systems. The
in-app **Documentation & Support** page (`/docs`, reachable from the sidebar **Docs** /
**Support** links) mirrors it.

CloudCap is a **read-only governance fleet**. It reads your GCP projects through a
least-privilege service account and **never mutates cloud state**. Every correction is
delivered as a *proposal* — a Pull Request, a ticket, or a notification — that a human
approves. Every token you configure is written to **Secret Manager**, never to the repo,
Firestore, or Terraform variables.

---

## 1. GCP data plane (read-only) — `LIVE`

The scanners read live GCP data through Google APIs using the runtime service account
**`cc-runtime`**. All roles are viewer / read-only.

| Pillar          | GCP source                                   | Role granted                                        |
|-----------------|----------------------------------------------|-----------------------------------------------------|
| Cost            | Recommender API + Cloud Monitoring (CPU)     | `roles/recommender.viewer`, `roles/monitoring.viewer` |
| Security        | Cloud Asset Inventory (public IAM policies)  | `roles/cloudasset.viewer`                           |
| IAM             | Asset Inventory + IAM Recommender            | `roles/iam.securityReviewer`, `roles/recommender.iamViewer` |
| Inventory / IaC | Asset Inventory resource labels              | `roles/cloudasset.viewer`, `roles/browser`          |
| Audit trail     | Cloud Logging (immutable, hash-chained)      | write-only to its own log                            |

Grant these roles to `cc-runtime` on any project you add to **Scan Scope**. **No API keys
are ever used** — access is via the service account's ADC identity.

**Two-identity model.** `terraform apply` runs as an **owner** (bootstrap, one-time
provisioning). The running app uses the least-privilege **`cc-runtime`** SA, which cannot
write to your cloud.

---

## 2. JIRA integration — `READY`

The **Issue** remediation action files each finding into a JIRA project you choose.
Configure it on **Sources → per-project**, or globally.

| Setting             | Where it lives          | Notes                                                        |
|---------------------|-------------------------|-------------------------------------------------------------|
| Base URL            | State store             | e.g. `https://yourco.atlassian.net`                         |
| Email               | State store             | the Atlassian account the token belongs to                  |
| Project key         | State store             | e.g. `SEC`, `OPS`                                            |
| GCP project field   | State store (optional)  | custom field id to stamp the GCP project; else a label      |
| **API token**       | **Secret Manager only** | read from `JIRA_API_TOKEN`; never persisted to disk/Firestore |

Environment variables read by `JiraConfig` (`agents/jira.py`): `JIRA_BASE_URL`,
`JIRA_EMAIL`, `JIRA_PROJECT_KEY`, `JIRA_GCP_PROJECT_FIELD`, and the secret `JIRA_API_TOKEN`.

Each issue is labelled with the GCP project, the GCP service, the control name, every
mapped compliance framework, and the category (e.g. `cost-optimization`). If JIRA is not
configured, CloudCap falls back to a **local ticket artifact** so nothing is lost.

- `configured` = base URL + email + project key present (enables the Issue button).
- `ready` = also has the token (can actually POST).

---

## 3. Notification channels (per project) — `PER-PROJECT`

Findings can fan out to notification channels, configured per project on the **Sources**
page. Webhook URLs / addresses are stored per project; any secrets go to Secret Manager.

| Channel    | Field                    | Example                                    |
|------------|--------------------------|--------------------------------------------|
| Slack      | Incoming webhook URL     | `https://hooks.slack.com/services/…`       |
| Email      | Address                  | `secops@yourco.com`                        |
| MS Teams   | Incoming webhook URL     | `https://outlook.office.com/webhook/…`     |
| PagerDuty  | Routing (integration) key | for high / critical escalation             |
| Webhook    | POST URL                 | generic JSON to your own endpoint          |

Delivery is governed by each project's **action policy**. In the current deployment the
audited projects are **report-only** (detect + record, no outbound firing) until a channel
is explicitly enabled on the **Policy** page.

---

## 4. GitHub Pull Requests — `REPORT-ONLY`

The **Fix** action drafts a Pull Request that codifies the correction (e.g. remove a
public IAM binding, right-size an instance) against the project's configured repo. The PR
is always opened as a **draft for human review** — CloudCap never merges.

This deployment ships with a **disk-writing PR backend** (no GitHub credentials are
installed): proposals are written to `eval/prs/` and shown inline in the UI. To open real
PRs, add a GitHub **host / org / token** on the Integrations config — the token goes to
Secret Manager.

---

## 5. Secrets & identity

CloudCap separates **non-secret config** (URLs, project keys, org ids — kept in the state
store / Firestore) from **secrets** (API tokens, webhook secrets):

- Secrets are written to **Secret Manager** by the app and read at runtime via environment
  injection.
- Secrets are **never** committed to the repo, printed to logs, or stored in Firestore or
  Terraform variables.
- The app runs as the least-privilege **`cc-runtime`** SA, which has no write access to
  your cloud.

---

## 6. Support

- **Full docs:** this file (`docs/INTEGRATIONS.md`); the `/docs` page mirrors it.
- **Install & setup:** `INSTALL.md` — requirements, auth options (Firebase / OIDC / IAP /
  proxy / dev), and GCP wiring (no API keys).
- **Audit trail:** every scan and action is recorded immutably in Cloud Logging — use it to
  see who ran what and when.
- **Traceability:** every finding links to its control and mapped frameworks on the
  **Compliance** page.
- **Contact:** mayurpawar1@gmail.com

---

## Appendix — chaos-env (eval ground truth)

`terraform/chaos-env/` seeds a throwaway GCP project with deliberately-flawed resources
(idle/oversized VM, orphan disk, unused IPs, public bucket, prompt-injection bait object,
over-privileged SA, oversized Cloud SQL, and an out-of-band "ClickOps" VM). `outputs.tf` is
the machine-readable ground truth used to score the fleet's precision/recall. Apply it into
a dedicated throwaway project, add that project to Scan Scope, then **`terraform destroy`**
when done — several findings (idle VM/SQL) only appear after Recommender collects a few
days of utilization data.
