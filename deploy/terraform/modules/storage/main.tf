# A private Object Storage bucket for encrypted backups.
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
