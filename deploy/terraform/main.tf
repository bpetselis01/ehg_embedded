# One Oracle Always Free VM (Ampere A1, 2 OCPU / 12 GB) with its network, a data volume, a backup bucket, a "VM down" alarm and a cost guard.
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
