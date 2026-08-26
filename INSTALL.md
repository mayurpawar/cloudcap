# CloudCap — Installation & Requirements

Governed multi-agent Cloud Cost / Security / Compliance governance for GCP.
Runs fully in **mock mode** on a laptop (no cloud), then swaps one adapter at a time
into **live mode** — the hexagonal (ports & adapters) design means agent logic never
changes between the two.

---

## 1. Prerequisites

| Requirement | Version / note |
|---|---|
| **Python** | 3.9+ — **use the interpreter that has the pip packages** (here: `/usr/local/bin/python3`, not Xcode's `/usr/bin/python3`). Check: `python3 -c "import sys; print(sys.executable)"` |
| pip packages (mock) | none — mock mode is **stdlib only** |
| pip packages (auth) | `firebase-admin` (Firebase) or `pyjwt` (OIDC) — only for the auth provider you pick |
| pip packages (Gemini) | `google-genai` |
| pip packages (live data) | `google-cloud-recommender`, `google-cloud-logging`, `google-cloud-asset` |
| `gcloud` CLI | for live mode auth + enabling APIs |

Install everything for a full live setup:
```bash
pip install firebase-admin google-genai google-cloud-recommender google-cloud-logging google-cloud-asset pyjwt
```

---

## 2. Quick start (mock mode — no cloud, no auth setup)

```bash
python3 -m agents.run --mode mock --project demo-proj      # run the fleet (CLI)
python3 -m eval.score                                       # scorecard (recall/precision)
python3 -m agents.audit                                     # verify the tamper-evident audit trail
python3 -m webui.serve --port 9000                          # dashboard → http://localhost:9000
```
With no auth configured, the dashboard uses a **dev login** (any email; `admin@…` = Admin).

---

## 3. Authentication (pick ONE provider)

Auth is **pluggable**. CloudCap only needs a verified `{email, name, picture}`; the
**allowlist + roles** (`webui/users.json`) and **cookie sessions** are shared across all
providers. Select with `CLOUDCAP_AUTH_PROVIDER` (auto-detects Firebase if configured,
else `dev`).

### Users & roles — `webui/users.json` (all providers)
```json
{ "admin@yourco.com": "admin", "analyst@yourco.com": "operator" }
```
When non-empty this is an **allowlist** — only listed emails may sign in. Overridable via
`CLOUDCAP_ADMINS` / `CLOUDCAP_OPERATORS` env.

### Option A — Enterprise SSO via trusted proxy / Google IAP  ★ recommended for enterprise
No IdP wiring in the app: put CloudCap behind IAP / oauth2-proxy / Cloudflare Access; the
gateway authenticates (against your **Active Directory / Entra ID / Okta / Workspace**) and
injects the user in a header CloudCap trusts.
```bash
CLOUDCAP_AUTH_PROVIDER=proxy python3 -m webui.serve --port 9000
```
Reads `X-Goog-Authenticated-User-Email` (IAP) / `X-Auth-Request-Email` (oauth2-proxy) /
`X-Forwarded-Email`. **Only enable when the proxy is the sole ingress** (else headers spoof).

### Option B — OIDC (Active Directory / Entra ID, Okta, Ping, Workspace)
```bash
CLOUDCAP_AUTH_PROVIDER=oidc \
CLOUDCAP_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0 \
CLOUDCAP_OIDC_AUDIENCE=<client-id> \
python3 -m webui.serve --port 9000
```
Server-side token verification (JWKS + iss/aud/exp) is implemented; the browser
redirect/callback flow to the IdP is the remaining wiring.

### Option C — Firebase Auth (fastest for a demo / small team)
Config lives in `webui/firebase_web.json` (the web `apiKey` is public, not a secret).
1. Firebase Console → your project → **Authentication** → enable **Google** and/or **Email/Password**.
2. Add authorized users, and set their roles in `webui/users.json`.
```bash
python3 -m webui.serve --port 9000     # auto-detected when firebase_web.json exists
```
> Note: the Firebase project can differ from the GCP data/hub project — verification
> targets the Firebase project id.

### Option D — Dev login (local only)
Default when nothing else is configured. Not for production.

---

## 4. Live mode on GCP — **no API keys** (IAM identity, not keys)

The enterprise path authenticates with a **service account / ADC**, not API keys. You
need: **enabled APIs + a least-privilege service account + ADC** (local) or Workload
Identity (deployed).

### Two projects (they can be the same)
- **Hub** `cloud-cap-506110` — where the app, Vertex/Gemini, and Cloud Logging live.
- **Target(s)** — the project(s) being audited (read-only).

### Enable APIs (hub)
```bash
gcloud config set project cloud-cap-506110
gcloud services enable aiplatform.googleapis.com logging.googleapis.com
```
### Enable APIs (each target)
```bash
gcloud services enable recommender.googleapis.com cloudasset.googleapis.com monitoring.googleapis.com --project <TARGET>
```
### Service account + least-privilege read-only roles
```bash
gcloud iam service-accounts create cloudcap-agent --project cloud-cap-506110
# Hub: reason + audit
gcloud projects add-iam-policy-binding cloud-cap-506110 \
  --member="serviceAccount:cloudcap-agent@cloud-cap-506110.iam.gserviceaccount.com" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding cloud-cap-506110 \
  --member="serviceAccount:cloudcap-agent@cloud-cap-506110.iam.gserviceaccount.com" --role="roles/logging.logWriter"
# Each target: READ ONLY
gcloud projects add-iam-policy-binding <TARGET> \
  --member="serviceAccount:cloudcap-agent@cloud-cap-506110.iam.gserviceaccount.com" --role="roles/recommender.viewer"
gcloud projects add-iam-policy-binding <TARGET> \
  --member="serviceAccount:cloudcap-agent@cloud-cap-506110.iam.gserviceaccount.com" --role="roles/cloudasset.viewer"
```
### Auth for local dev (ADC)
```bash
gcloud auth application-default login
# optional: impersonate the SA instead of using your own creds
gcloud config set auth/impersonate_service_account cloudcap-agent@cloud-cap-506110.iam.gserviceaccount.com
```
### Run live
```bash
GOOGLE_CLOUD_PROJECT=cloud-cap-506110 CLOUDCAP_GEMINI=1 CLOUDCAP_LOCATIONS=us-central1 \
  python3 -m agents.run --mode live --project <TARGET>
```
`--project` = project to AUDIT; `GOOGLE_CLOUD_PROJECT` = hub (Vertex + logging).

---

## 5. Environment variables (reference)

| Var | Purpose |
|---|---|
| `CLOUDCAP_AUTH_PROVIDER` | `firebase` \| `oidc` \| `proxy` \| `dev` |
| `CLOUDCAP_ADMINS` / `CLOUDCAP_OPERATORS` | role overrides (comma-separated emails) |
| `CLOUDCAP_OIDC_ISSUER` / `CLOUDCAP_OIDC_AUDIENCE` | OIDC provider config |
| `GOOGLE_CLOUD_PROJECT` | hub project (Vertex + Cloud Logging) |
| `CLOUDCAP_GEMINI` | `1` → real Gemini reasoning (else deterministic) |
| `CLOUDCAP_GEMINI_MODEL` | override Gemini model |
| `CLOUDCAP_LOCATIONS` | comma-separated regions/zones to scan for Recommender |

## 6. Data & state (where things persist)

Local files under `eval/` today; live-mode targets in parentheses:
- `audit_log.jsonl` — tamper-evident hash-chained audit trail *(Cloud Logging)*
- `findings_history.json`, `suppressions.json`, `memory_state.json` *(Firestore / Memory Bank)*
- `compliance_scope.json`, `sources_state.json`, `policy_state.json`, `integrations_state.json` *(Firestore)*
- `prs/` — proposed GitOps remediation PRs *(GitHub)*
