# The few values you need after `terraform apply`.
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
