# CloudCap bootstrap installer

One `terraform apply`, run **once** by an admin, stands up the whole hub and hands off
to a least-privilege runtime. This is the enterprise distribution model — CloudCap runs
**in the customer's own GCP**; data never leaves it.

## The two-identity model (why this is safe)

| Identity | Used | Rights | Provisions / does |
|---|---|---|---|
| **Bootstrap** (you, running `terraform apply`) | once, at install | Owner/Editor on the hub | Creates Firestore, enables APIs, creates the runtime SA, grants roles, deploys Cloud Run |
| **Runtime** (`cc-runtime` SA) | every day, by the app | **read-only** on targets; Firestore/Vertex/Logging on the hub only | Scans, reasons, persists state, writes audit logs — **never writes to your cloud** |

The elevated "create Firestore" right lives only in the bootstrap step. The running app
can't create infra or mutate targets.

## What it provisions
- **APIs** on the hub (Vertex, Run, Recommender, Monitoring, Asset, Model Armor, Firestore, Secret Manager, Logging, Resource Manager, IAP).
- **Firestore (Native)** — durable state (config, findings, lifecycle).
- **`cc-runtime` service account** + least-privilege IAM: read-only at the scan scope (org/folder/projects); Vertex/Firestore/Logging/Secrets on the hub.
- **Per-agent scanner SAs** (the Agent-Identity pillar).
- **Cloud Run** service `cloudcap` running as `cc-runtime`, scale-to-zero, wired with auth/admins/Gemini env.

## Install (once)

**1. Auth as the bootstrap identity**
```bash
gcloud auth application-default login          # an Owner/Editor on the hub project
gcloud config set project cloud-cap-506110
```

**2. Build & push the app image** (Artifact Registry)
```bash
gcloud artifacts repositories create cloudcap --repository-format=docker --location=us-central1
gcloud builds submit --tag us-central1-docker.pkg.dev/cloud-cap-506110/cloudcap/app:v1
```

**3. Apply**
```bash
cp terraform.tfvars.example terraform.tfvars   # then edit: image, admins, scan scope
terraform init
terraform apply
```

**4. Open the dashboard** (from the `dashboard_url` output) → sign in as an admin →
you land in the **onboarding wizard** (discover → scope → policy → channels → first scan).

## Notes
- **Enterprise SSO:** set `auth_provider = "proxy"` and front the service with an external
  Load Balancer + **IAP** (then the `allUsers` invoker binding is skipped automatically).
  Otherwise the app self-gates via Firebase/OIDC.
- **Scan scope:** prefer `org_id` or `folder_id` so new projects are covered automatically;
  `scan_project_ids` is the safe way to start small.
- **Uninstall:** `terraform destroy` (Firestore has deletion protection by default — remove
  it explicitly if you really mean it).
