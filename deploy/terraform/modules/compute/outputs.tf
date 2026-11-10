output "instance_id" {
  description = "OCID of the VM."
  value       = oci_core_instance.ehg.id
}

output "public_ip" {
  description = "Public address of the VM (ephemeral: it changes if the VM is recreated)."
  value       = oci_core_instance.ehg.public_ip
}
