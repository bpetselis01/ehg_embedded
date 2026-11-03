output "bucket_name" {
  description = "Name of the backup bucket."
  value       = oci_objectstorage_bucket.backups.name
}

output "namespace" {
  description = "Object Storage namespace of the tenancy."
  value       = data.oci_objectstorage_namespace.ns.namespace
}
