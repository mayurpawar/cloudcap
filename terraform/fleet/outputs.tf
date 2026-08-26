output "hub_project_id" {
  description = "Project CloudCap is deployed into (the control-plane hub)."
  value       = var.hub_project_id
}

output "scan_scope" {
  description = "Effective read scope the scanner SAs were granted."
  value = (
    local.use_org ? "organization:${var.org_id}" :
    local.use_folder ? "folder:${var.folder_id}" :
    "projects:${join(",", local.scan_projects)}"
  )
}

output "agent_service_accounts" {
  description = "Per-agent least-privilege identities (read-only scanners)."
  value       = { for k, sa in google_service_account.agent : k => sa.email }
}

output "runtime_service_account" {
  description = "Least-privilege identity the Cloud Run app runs as (read-only on targets)."
  value       = google_service_account.runtime.email
}

output "dashboard_url" {
  description = "CloudCap dashboard URL — sign in here to onboard + run the first scan."
  value       = google_cloud_run_v2_service.dashboard.uri
}

output "firestore_database" {
  description = "Durable state store provisioned for the hub."
  value       = google_firestore_database.hub.name
}
