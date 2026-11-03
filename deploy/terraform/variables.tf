# PURPOSE: Everything you must set before `terraform plan`. No secret is ever a variable default or lives in git.
# INPUT: values from terraform.tfvars (gitignored; copy terraform.tfvars.example) or TF_VAR_* environment variables.
#   e.g. region = "ap-sydney-1", admin_cidr = "203.0.113.7/32" (your own public address, not 0.0.0.0/0)
# OUTPUT: none (declarations only).
# HOW: The OCI API key itself is NOT a variable: the provider reads your ~/.oci/config profile (config_file_profile), which stays on your Mac.
# ROLE IN THESIS: The knobs of the Oracle deployment runbook (the deployment runbook, Unit 5).
# BEFORE -> AFTER: Before: nothing. After: main.tf wires these into the four modules.
variable "region" {
  description = "Home region identifier, e.g. ap-sydney-1 or ap-melbourne-1. Always Free compute must be in the home region, which cannot be changed later."
  type        = string
}

variable "tenancy_ocid" {
  description = "OCID of your tenancy (OCI console, profile menu, Tenancy)."
  type        = string
}

variable "compartment_ocid" {
  description = "OCID of the compartment that will hold everything. Use the tenancy OCID to use the root compartment."
  type        = string
}

variable "config_file_profile" {
  description = "Profile name in ~/.oci/config holding your API key."
  type        = string
  default     = "DEFAULT"
}

variable "availability_domain_index" {
  description = "Which availability domain to use (0, 1, 2). If apply fails with 'out of host capacity', try the next one, or retry later."
  type        = number
  default     = 0
}

variable "admin_cidr" {
  description = "The only address range allowed to reach SSH (port 22), e.g. your public IP as 203.0.113.7/32. 0.0.0.0/0 is refused."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR block and must not be 0.0.0.0/0."
  }
}

variable "ssh_public_key" {
  description = "Contents of your SSH PUBLIC key (the .pub file). Never put the private key anywhere in this repository."
  type        = string
}

variable "notification_email" {
  description = "Where the 'VM down' alarm and the cost-guard budget alert are sent."
  type        = string
}

variable "domain_name" {
  description = "DNS name that points at the VM (e.g. a DuckDNS name); Caddy gets its certificate for it and the broker certificate is made for it."
  type        = string
}

variable "repo_url" {
  description = "Git URL the VM clones. For a private repository use the SSH form git@github.com:OWNER/REPO.git; the read-only deploy key is placed by hand afterwards (the deployment runbook, Unit 5)."
  type        = string
}

variable "repo_tag" {
  description = "Git tag the VM checks out at first boot (the code that runs on it)."
  type        = string
}
