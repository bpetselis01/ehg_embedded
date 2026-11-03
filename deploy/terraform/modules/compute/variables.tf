variable "compartment_ocid" {
  description = "Compartment that holds the VM."
  type        = string
}

variable "availability_domain" {
  description = "Availability domain name."
  type        = string
}

variable "subnet_id" {
  description = "Subnet the VM joins."
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key for the default user."
  type        = string
}

variable "user_data" {
  description = "Rendered cloud-init text (plain, base64-encoded here)."
  type        = string
}
