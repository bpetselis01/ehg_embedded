# PURPOSE: The few values you need after `terraform apply`.
# INPUT: the modules. OUTPUT: shown by `terraform output`.
#   e.g. public_ip = "152.67.1.2", ssh_command = "ssh ubuntu@152.67.1.2"
# HOW: Plain pass-through of module outputs.
# ROLE IN THESIS: Feeds the runbook: point the DNS name at public_ip, then SSH in to copy the age key.
# BEFORE -> AFTER: Before: nothing. After apply: these are printed.
output "public_ip" {
  description = "Public address of the VM. Point your DNS name at it."
  value       = module.compute.public_ip
}

output "ssh_command" {
  description = "How to log in (the default Ubuntu user is 'ubuntu'; cloud-init also creates the 'ehg' service user without a login shell key)."
  value       = "ssh ubuntu@${module.compute.public_ip}"
}

output "backup_bucket" {
  description = "Object Storage bucket for encrypted backups (20 GB free cap)."
  value       = module.storage.bucket_name
}

output "object_storage_namespace" {
  description = "Namespace used in the S3-compatible endpoint (backend.tf.example, restic)."
  value       = module.storage.namespace
}

output "instance_id" {
  description = "OCID of the VM."
  value       = module.compute.instance_id
}
