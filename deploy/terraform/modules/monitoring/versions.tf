# PURPOSE: Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint: terraform_required_providers / terraform_required_version).
# The root (../../versions.tf) pins the exact provider version; a module only names the source and the oldest version it needs.
terraform {
  required_version = ">= 1.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 9.11.0"
    }
  }
}
