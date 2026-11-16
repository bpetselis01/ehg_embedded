# Pins the Terraform CLI and the Oracle Cloud provider so every run uses the same code.
terraform {
  required_version = ">= 1.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 9.12.1"
    }
  }
}
