# PURPOSE: One Oracle Always Free VM (Ampere A1, 2 OCPU / 12 GB) with its network, a data volume, a backup bucket, a "VM down" alarm and a cost guard.
# INPUT: variables.tf. The cloud-init template deploy/cloud-init/user-data.yaml.tftpl becomes the VM's first-boot script.
# OUTPUT: outputs.tf (public IP, SSH command, bucket). Nothing here is created until YOU run `terraform apply` after reading the plan.
# HOW: network (VCN, subnet, security list) -> compute (VM + 150 GB block volume) -> storage (bucket) -> monitoring (alarm, budget).
#   Sizes follow the Always Free limits checked on 2026-11-03: 2 OCPU + 12 GB for A1, 200 GB boot + block combined (50 + 150), 20 GB object storage.
# ROLE IN THESIS: The cloud core of the production chain (docs/production-topology.md), written as code so it can be rebuilt (docs/decisions/0017).
# BEFORE -> AFTER: Before: no cloud resources. After `terraform apply` (by Byron only): a VM that runs cloud-init and waits for the age key.
provider "oci" {
  region              = var.region
  config_file_profile = var.config_file_profile
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
}

module "network" {
  source           = "./modules/network"
  compartment_ocid = var.compartment_ocid
  admin_cidr       = var.admin_cidr
}

module "compute" {
  source              = "./modules/compute"
  compartment_ocid    = var.compartment_ocid
  availability_domain = local.availability_domain
  subnet_id           = module.network.subnet_id
  ssh_public_key      = var.ssh_public_key
  user_data = templatefile("${path.module}/../cloud-init/user-data.yaml.tftpl", {
    admin_cidr  = var.admin_cidr
    domain_name = var.domain_name
    repo_url    = var.repo_url
    repo_tag    = var.repo_tag
  })
}

module "storage" {
  source           = "./modules/storage"
  tenancy_ocid     = var.tenancy_ocid
  compartment_ocid = var.compartment_ocid
}

module "monitoring" {
  source             = "./modules/monitoring"
  tenancy_ocid       = var.tenancy_ocid
  compartment_ocid   = var.compartment_ocid
  instance_id        = module.compute.instance_id
  notification_email = var.notification_email
}
