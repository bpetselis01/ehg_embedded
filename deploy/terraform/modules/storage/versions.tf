# Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint: terraform_required_providers / terraform_required_version).
terraform {
  required_version = ">= 1.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 9.11.0"
    }
  }
}
