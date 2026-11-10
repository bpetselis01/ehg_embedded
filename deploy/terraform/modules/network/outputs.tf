output "subnet_id" {
  description = "The public subnet the VM joins."
  value       = oci_core_subnet.public.id
}
