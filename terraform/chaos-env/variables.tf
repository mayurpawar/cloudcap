variable "project_id" {
  description = "GCP project ID for the throwaway chaos environment."
  type        = string
}

variable "region" {
  description = "Default region. Also used to demonstrate data-sovereignty policy."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Default zone for zonal resources."
  type        = string
  default     = "us-central1-a"
}

variable "oversized_sql_tier" {
  description = "Deliberately oversized Cloud SQL tier to trigger a rightsizing finding."
  type        = string
  default     = "db-custom-4-16384"
}

variable "manual_vm_name" {
  description = "Name of the out-of-band (ClickOps) expensive VM the fleet must catch."
  type        = string
  default     = "cc-manual-orphan-vm"
}

variable "manual_vm_machine_type" {
  description = "Machine type for the manual VM. Costly but quota-safe; stands in for a forgotten GPU box."
  type        = string
  default     = "e2-standard-8"
}
