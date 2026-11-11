# PURPOSE: Pins the Terraform CLI and the Oracle Cloud provider so every run uses the same code.
# INPUT: none. OUTPUT: none (declarations only).
# HOW: Terraform 1.6 or later (the CLI is under the Business Source Licence 1.1: free for our own use, see docs/licences.md).
#   The provider is pinned to one exact version (9.12.1, moved from 9.11.0 by Dependabot on 2026-11-10) so a new release cannot change a plan unannounced.
# ROLE IN THESIS: Infrastructure as code for the Oracle Always Free VM (docs/decisions/0010-oracle-always-free-cloud-target.md).
# BEFORE -> AFTER: Before: nothing. After: `terraform init` downloads exactly this provider.
terraform {
  required_version = ">= 1.6"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 9.12.1"
    }
  }
}
