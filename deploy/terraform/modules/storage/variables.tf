variable "tenancy_ocid" {
  description = "Tenancy OCID (the Object Storage namespace is looked up through it)."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment that holds the bucket."
  type        = string
}
