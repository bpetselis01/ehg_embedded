variable "compartment_ocid" {
  description = "Compartment that holds the network."
  type        = string
}

variable "admin_cidr" {
  description = "Only source allowed to reach SSH."
  type        = string
}
