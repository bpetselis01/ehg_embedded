variable "tenancy_ocid" {
  description = "Tenancy OCID (budgets live at the tenancy level)."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment being watched."
  type        = string
}

variable "instance_id" {
  description = "OCID of the VM whose metrics are watched."
  type        = string
}

variable "notification_email" {
  description = "Email address that receives the alerts."
  type        = string
}
