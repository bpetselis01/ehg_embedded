# PURPOSE: The one VM (Ampere A1, 2 OCPU, 12 GB, arm64, Ubuntu 24.04) plus a 150 GB data volume.
# INPUT: compartment_ocid, availability_domain, subnet_id, ssh_public_key, user_data (the rendered cloud-init text).
# OUTPUT: instance_id, public_ip.
# HOW: 50 GB boot volume + 150 GB block volume = 200 GB, the whole Always Free storage allowance. The block volume is attached paravirtualised at
#   /dev/oracleoci/oraclevdb; cloud-init formats and mounts it at /srv/ehg-data and moves Docker's data there (UNVERIFIED until the first real boot).
#   A newer Ubuntu image appearing later must not replace the VM, so changes to the image id are ignored.
# ROLE IN THESIS: The cloud core host (docs/decisions/0010). Sized to the Always Free limits checked on 2026-11-03; it can be reclaimed, so it must be rebuildable.
# BEFORE -> AFTER: Before: nothing. After apply: a VM that runs cloud-init on first boot.
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_instance" "ehg" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "ehg-core"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = 2
    memory_in_gbs = 12
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 50
  }

  create_vnic_details {
    subnet_id        = var.subnet_id
    assign_public_ip = true
    hostname_label   = "ehg-core"
  }

  agent_config {
    is_monitoring_disabled = false
    is_management_disabled = false
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(var.user_data)
  }

  lifecycle {
    ignore_changes = [source_details[0].source_id]
  }
}

resource "oci_core_volume" "data" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "ehg-data"
  size_in_gbs         = 150
}

resource "oci_core_volume_attachment" "data" {
  attachment_type = "paravirtualized"
  instance_id     = oci_core_instance.ehg.id
  volume_id       = oci_core_volume.data.id
  device          = "/dev/oracleoci/oraclevdb"
  is_read_only    = false
}
