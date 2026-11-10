# PURPOSE: A private Object Storage bucket for encrypted backups.
# INPUT: tenancy_ocid (to look up the namespace), compartment_ocid. OUTPUT: bucket_name, namespace.
# HOW: Private (no public access), Standard tier. The Always Free allowance is 20 GB and 50,000 requests a month in total, so the backup jobs (Unit 12) must stay inside it.
# ROLE IN THESIS: Off-VM copy of the backups (docs/decisions/0017-backup-dr-and-design-only-ha.md); the encryption key is NOT on the VM.
# BEFORE -> AFTER: Before: no bucket. After apply: ehg-backups exists and is empty.
data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.tenancy_ocid
}

resource "oci_objectstorage_bucket" "backups" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = "ehg-backups"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
}
