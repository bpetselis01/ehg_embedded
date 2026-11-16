# Two out-of-band safety nets that do not depend on the VM being alive: a "VM down" alarm and a cost guard.
resource "oci_ons_notification_topic" "alerts" {
  compartment_id = var.compartment_ocid
  name           = "ehg-alerts"
  description    = "VM down alarm and cost guard"
}

resource "oci_ons_subscription" "email" {
  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "EMAIL"
  endpoint       = var.notification_email
}

resource "oci_monitoring_alarm" "vm_down" {
  compartment_id        = var.compartment_ocid
  metric_compartment_id = var.compartment_ocid
  display_name          = "ehg-vm-down"
  is_enabled            = true
  namespace             = "oci_computeagent"
  query                 = "CpuUtilization[1m]{resourceId = \"${var.instance_id}\"}.absent()"
  severity              = "CRITICAL"
  pending_duration      = "PT5M"
  destinations          = [oci_ons_notification_topic.alerts.id]
  body                  = "The EHG cloud VM has stopped reporting metrics. Check the OCI console, then the disaster-recovery notes in the repository."
}

resource "oci_budget_budget" "guard" {
  compartment_id = var.tenancy_ocid
  amount         = 1
  reset_period   = "MONTHLY"
  display_name   = "ehg-cost-guard"
  description    = "Always Free only: any spend at all is a mistake."
  target_type    = "COMPARTMENT"
  targets        = [var.compartment_ocid]
}

resource "oci_budget_alert_rule" "any_spend" {
  budget_id      = oci_budget_budget.guard.id
  display_name   = "any-spend"
  type           = "ACTUAL"
  threshold      = 1
  threshold_type = "PERCENTAGE"
  recipients     = var.notification_email
  message        = "Spend detected on an Always Free account. Find the paid resource and remove it."
}
