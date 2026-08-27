# CloudCap deploys as a HUB-AND-SPOKE control plane:
#   - the HUB (this project) is where CloudCap RUNS (Agent Runtime, Memory Bank,
#     Registry, dashboard, scanner SAs). ONE hub per organization.
#   - the SCAN SCOPE (org / folder / explicit projects) is WHAT it reads, granted
#     to the scanner SAs at that node so new projects are covered automatically.

variable "hub_project_id" {
  description = "Dedicated governance/hub project CloudCap is DEPLOYED into. One per org."
  type        = string
}

variable "region" {
  description = "Region for hub resources. Enforced for data residency."
  type        = string
  default     = "us-central1"
}

# --- Deploy: the app container + hub app config -----------------------------
variable "image" {
  description = "Container image for the CloudCap app (e.g. REGION-docker.pkg.dev/HUB/cloudcap/app:TAG)."
  type        = string
}

variable "admin_emails" {
  description = "Emails granted the Admin role in the dashboard (comma-joined into CLOUDCAP_ADMINS)."
  type        = list(string)
  default     = []
}

variable "auth_provider" {
  description = "Dashboard auth: firebase | oidc | proxy | dev. Use 'proxy' when fronting with IAP."
  type        = string
  default     = "firebase"
}

variable "gemini_enabled" {
  description = "Use real Gemini (Vertex AI) for the reasoner. Off = deterministic reasoner."
  type        = bool
  default     = true
}

variable "gemini_model" {
  description = "Gemini model id (must be 3.5+, served from the global endpoint)."
  type        = string
  default     = "gemini-3.7-flash"
}

variable "tfstate_sources" {
  description = "Terraform state backends for IaC ownership: 'gs://bucket/obj.tfstate|owner/repo' (comma-separated)."
  type        = string
  default     = ""
}

variable "firestore_location" {
  description = "Firestore location (region like us-central1, or multi-region nam5/eur3)."
  type        = string
  default     = "nam5"
}

# --- Scan behaviour (what 'Run scan' actually does) -------------------------
variable "scan_mode" {
  description = "mock (demo dataset) | live (real GCP read-only data)."
  type        = string
  default     = "mock"
}

variable "scan_target" {
  description = "Project to scan on 'Run scan' in live mode (empty → the app's project)."
  type        = string
  default     = ""
}

variable "scan_locations" {
  description = "Regions/zones to scan for Recommender + Cloud Run utilization."
  type        = string
  default     = "us-central1,us-east1,us-west1,europe-west1,asia-south1,asia-southeast1"
}

# --- Scan scope: WHERE read-only access is granted. Precedence: org > folder > projects.
variable "org_id" {
  description = "Organization ID for org-wide read access (whole-org hub). Empty to skip."
  type        = string
  default     = ""
}

variable "folder_id" {
  description = "Folder ID for folder-wide read access. Empty to skip."
  type        = string
  default     = ""
}

variable "scan_project_ids" {
  description = "Explicit project(s) to scan when neither org_id nor folder_id is set. Defaults to the hub project itself (single-project / demo)."
  type        = list(string)
  default     = []
}

# Projects the hub can DISCOVER (Browser = read-only metadata only, no data access).
# For no-org accounts (standalone projects), GCP can't auto-list projects, so list the
# ones you want visible in onboarding discovery here. Scanning still requires a project
# to be in scan_project_ids (heavier read roles) — discovery ≠ scan access.
variable "discover_project_ids" {
  description = "Projects granted Browser so they appear in discovery (metadata only)."
  type        = list(string)
  default     = []
}

# Per-agent identity + least-privilege roles. Scanners are read-only.
variable "agents" {
  description = "Fleet agents and their least-privilege IAM roles (granted at the scan scope)."
  type = map(object({
    roles       = list(string)
    departments = list(string)
  }))
  default = {
    orchestrator       = { roles = [], departments = ["finops", "secops", "platform"] }
    cost_scanner       = { roles = ["roles/recommender.viewer"], departments = ["finops"] }
    security_scanner   = { roles = ["roles/cloudasset.viewer", "roles/storage.objectViewer"], departments = ["secops"] }
    iam_scanner        = { roles = ["roles/iam.securityReviewer", "roles/recommender.iamViewer"], departments = ["secops"] }
    compliance_scanner = { roles = ["roles/cloudasset.viewer"], departments = ["grc"] }
    # remediation agent intentionally omitted: writes are brokered behind human
    # approval via GitOps PRs, not granted as standing cloud IAM.
  }
}
