# fleet — the PRODUCT INSTALLER. Runs in the CUSTOMER's GCP org.
# One `terraform apply` stands up the whole governed fleet. Data never leaves
# the customer cloud (data-sovereignty). This is the enterprise distribution model.
#
# Skeleton for D6-D10 — resource blocks filled in as each pillar is wired.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

# The provider targets the HUB project — where CloudCap is deployed.
provider "google" {
  project = var.hub_project_id
  region  = var.region
}

# --- Required APIs (enabled on the HUB project) -----------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com",           # Vertex AI / Agent Engine / Memory Bank
    "run.googleapis.com",                  # dashboard + any Cloud Run agents
    "recommender.googleapis.com",          # cost/IAM recommendations
    "monitoring.googleapis.com",           # Cloud Run utilization (usage-based cost)
    "cloudasset.googleapis.com",           # asset inventory (org/folder-level export)
    "modelarmor.googleapis.com",           # Model Armor guardrails
    "cloudtrace.googleapis.com",           # OTel -> Cloud Trace
    "logging.googleapis.com",              # immutable audit sink
    "firestore.googleapis.com",            # durable state (config, findings, lifecycle)
    "secretmanager.googleapis.com",        # tokens (GitHub/Slack) — never in config
    "cloudresourcemanager.googleapis.com", # enumerate projects under org/folder
    "cloudscheduler.googleapis.com",       # unattended daily background scan
    "iam.googleapis.com",
    "iap.googleapis.com", # optional: enterprise SSO in front of Cloud Run
  ])
  project            = var.hub_project_id
  service            = each.value
  disable_on_destroy = false
}

# --- PILLAR: Agent Identity (one least-privilege SA per agent, in the HUB) ---
# Scanners are READ-ONLY; the remediation agent gets no standing cloud IAM.
resource "google_service_account" "agent" {
  for_each = var.agents
  project  = var.hub_project_id
  # SA account_id: lowercase, hyphens only (no underscores), <=30 chars.
  account_id   = "cc-${replace(each.key, "_", "-")}"
  display_name = "CloudCap ${each.key}"
}

locals {
  # Least-privilege: scanners read; nobody here gets write. Remediation writes are
  # brokered separately behind human approval via GitOps PRs.
  role_bindings = flatten([
    for name, cfg in var.agents : [
      for role in cfg.roles : { agent = name, role = role }
    ]
  ])

  # Scan-scope selection (hub-and-spoke). Precedence: org > folder > projects.
  use_org       = var.org_id != ""
  use_folder    = !local.use_org && var.folder_id != ""
  use_project   = !local.use_org && !local.use_folder
  scan_projects = length(var.scan_project_ids) > 0 ? var.scan_project_ids : [var.hub_project_id]
}

# --- Read-only role bindings, granted at the chosen scope -------------------
# ORG-wide hub: new projects are covered automatically.
resource "google_organization_iam_member" "agent_org" {
  for_each = local.use_org ? { for b in local.role_bindings : "${b.agent}-${b.role}" => b } : {}
  org_id   = var.org_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.agent[each.value.agent].email}"
}

# FOLDER-wide hub.
resource "google_folder_iam_member" "agent_folder" {
  for_each = local.use_folder ? { for b in local.role_bindings : "${b.agent}-${b.role}" => b } : {}
  folder   = var.folder_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.agent[each.value.agent].email}"
}

# PROJECT-scoped (single-project / demo): bind each role on each scan project.
resource "google_project_iam_member" "agent_project" {
  for_each = local.use_project ? {
    for pair in setproduct(local.role_bindings, local.scan_projects) :
    "${pair[0].agent}-${pair[0].role}-${pair[1]}" => {
      agent = pair[0].agent, role = pair[0].role, project = pair[1]
    }
  } : {}
  project = each.value.project
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.agent[each.value.agent].email}"
}

# ============================================================================
# BOOTSTRAP: the hub the app runs in. `terraform apply` is run ONCE by an admin
# (the BOOTSTRAP identity — Owner/Editor on the hub). It provisions everything and
# creates the least-privilege RUNTIME identity the app then runs as. Two-identity
# model: elevated create-rights live here, not at runtime.
# ============================================================================

# --- Durable state: Firestore (Native) --------------------------------------
# The one resource that needs a create-right we can't assume at runtime — created
# here, once, by the bootstrap identity.
resource "google_firestore_database" "hub" {
  project     = var.hub_project_id
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# NAMED database — the app uses this (no "(default)" parens → no REST-encoding bug).
resource "google_firestore_database" "named" {
  project     = var.hub_project_id
  name        = "cloudcap"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# --- RUNTIME identity: what the Cloud Run app runs as (least-privilege) ------
resource "google_service_account" "runtime" {
  project      = var.hub_project_id
  account_id   = "cc-runtime"
  display_name = "CloudCap runtime (Cloud Run)"
}

locals {
  # The single app process performs all scans, so the runtime SA holds the union of
  # the per-agent read roles, plus what the Cloud Run cost analyzer needs.
  runtime_read_roles = distinct(concat(
    flatten([for a in var.agents : a.roles]),
    ["roles/run.viewer", "roles/monitoring.viewer", "roles/browser"],
  ))
  # Hub-local roles: reason (Vertex), persist (Firestore), audit read+write (Logging), secrets.
  runtime_hub_roles = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/logging.viewer", # read the cloudcap-audit trail for the History / Hub logs views
    "roles/secretmanager.secretAccessor",
  ]
}

resource "google_project_iam_member" "runtime_hub" {
  for_each = toset(local.runtime_hub_roles)
  project  = var.hub_project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

# Runtime read access at the scan scope (org > folder > projects), mirroring the
# scanner SAs. READ-ONLY — the app never gets write/standing IAM on targets.
resource "google_organization_iam_member" "runtime_org" {
  for_each = local.use_org ? toset(local.runtime_read_roles) : []
  org_id   = var.org_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_folder_iam_member" "runtime_folder" {
  for_each = local.use_folder ? toset(local.runtime_read_roles) : []
  folder   = var.folder_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_project" {
  for_each = local.use_project ? {
    for pair in setproduct(local.runtime_read_roles, local.scan_projects) :
    "${pair[0]}-${pair[1]}" => { role = pair[0], project = pair[1] }
  } : {}
  project = each.value.project
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Discovery visibility: Browser (project metadata only — no data) on each listed
# project, so no-org accounts still see their projects in onboarding discovery.
resource "google_project_iam_member" "runtime_discover" {
  for_each = toset(var.discover_project_ids)
  project  = each.value
  role     = "roles/browser"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

# --- Interaction surface: the dashboard app (Cloud Run) ---------------------
resource "google_cloud_run_v2_service" "dashboard" {
  project  = var.hub_project_id
  name     = "cloudcap"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email
    scaling { min_instance_count = 0 } # scale-to-zero — we practice what we preach

    containers {
      image = var.image
      ports { container_port = 8080 }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.hub_project_id
      }
      env {
        name  = "CLOUDCAP_AUTH_PROVIDER"
        value = var.auth_provider
      }
      env {
        name  = "CLOUDCAP_ADMINS"
        value = join(",", var.admin_emails)
      }
      env {
        name  = "CLOUDCAP_GEMINI"
        value = var.gemini_enabled ? "1" : ""
      }
      # Gemini 3.5+ (hackathon requirement) is served from the `global` endpoint.
      env {
        name  = "CLOUDCAP_GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "CLOUDCAP_GEMINI_LOCATION"
        value = "global"
      }
      env {
        name  = "CLOUDCAP_STORE"
        value = "firestore"
      }
      env {
        name  = "CLOUDCAP_FIRESTORE_DB"
        value = "cloudcap" # named DB avoids the (default) REST-encoding bug
      }
      env {
        name  = "CLOUDCAP_AUDIT"
        value = "cloud" # immutable audit trail -> Cloud Logging
      }
      env {
        name  = "CLOUDCAP_DISCOVERY"
        value = "live" # list real projects via Cloud Resource Manager
      }
      env {
        name  = "CLOUDCAP_SCAN_MODE"
        value = var.scan_mode # mock | live
      }
      env {
        name  = "CLOUDCAP_SCAN_PROJECT"
        value = var.scan_target # project scanned live on 'Run scan'
      }
      # Unattended background scan: Cloud Scheduler POSTs /tasks/scan with an OIDC token
      # from this SA; the app verifies the token email + audience before running the fleet.
      env {
        name  = "CLOUDCAP_SCHED_SA"
        value = google_service_account.runtime.email
      }
      env {
        name  = "CLOUDCAP_SCHED_AUDIENCE"
        value = "${var.hub_url}/tasks/scan"
      }
      # Terraform state backends registered for IaC ownership resolution (GCS state →
      # owning repo). Resources in no indexed state resolve as ClickOps (unmanaged).
      env {
        name  = "CLOUDCAP_TFSTATE_SOURCES"
        value = var.tfstate_sources
      }
      env {
        name  = "CLOUDCAP_LOCATIONS"
        value = var.scan_locations
      }
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1" # flush stderr/stdout so diagnostics reach Cloud Logging
      }
      # JIRA API token (OPTIONAL) — mounted only when jira_api_token_secret is set to an
      # existing Secret Manager secret. Empty by default so a fresh `apply` never fails on
      # a missing secret; the Issue action falls back to local tickets until configured.
      dynamic "env" {
        for_each = var.jira_api_token_secret != "" ? [1] : []
        content {
          name = "JIRA_API_TOKEN"
          value_source {
            secret_key_ref {
              secret  = var.jira_api_token_secret
              version = "latest"
            }
          }
        }
      }
      # GitHub token for GitOps PR remediation (OPTIONAL) — mounted only when
      # github_token_secret is set. Use a fine-grained PAT scoped to the owning repo. The
      # PR channel opens PRs solely against a finding's resolved owner_repo. Empty by
      # default so a fresh `apply` works without the secret (findings show as advisory).
      dynamic "env" {
        for_each = var.github_token_secret != "" ? [1] : []
        content {
          name = "GITHUB_TOKEN"
          value_source {
            secret_key_ref {
              secret  = var.github_token_secret
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_project_service.apis, google_firestore_database.hub]
}

# Access: the app self-gates via its own auth (Firebase/OIDC), so allUsers may reach
# the login page. For enterprise SSO, set auth_provider="proxy" and front this service
# with an external LB + IAP instead (then remove this binding).
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count    = var.auth_provider == "proxy" ? 0 : 1
  project  = var.hub_project_id
  location = var.region
  name     = google_cloud_run_v2_service.dashboard.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Unattended daily background scan — the autonomous fleet run. Cloud Scheduler POSTs
# /tasks/scan with an OIDC token minted for cc-runtime; the app verifies the token's
# email + audience (CLOUDCAP_SCHED_SA / CLOUDCAP_SCHED_AUDIENCE) before running the fleet.
# No human, no prompt. Disabled when hub_url or scan_schedule is empty.
resource "google_cloud_scheduler_job" "daily_scan" {
  count            = var.hub_url == "" || var.scan_schedule == "" ? 0 : 1
  name             = "cloudcap-daily-scan"
  project          = var.hub_project_id
  region           = var.region
  description      = "Unattended daily CloudCap governance scan (background agent fleet)."
  schedule         = var.scan_schedule
  time_zone        = "Etc/UTC"
  attempt_deadline = "600s"

  http_target {
    http_method = "POST"
    uri         = "${var.hub_url}/tasks/scan"
    oidc_token {
      service_account_email = google_service_account.runtime.email
      audience              = "${var.hub_url}/tasks/scan"
    }
  }
  depends_on = [google_project_service.apis]
}

# --- PILLARS still wired live in-app (documented, not TF): Memory Bank, Agent
# Registry, Agent Gateway + Model Armor, OTel->Cloud Trace. Their adapters live in
# agents/adapters/google_geap.py and activate via env, no infra change.
