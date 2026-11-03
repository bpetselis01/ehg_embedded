# PURPOSE: Two out-of-band safety nets that do not depend on the VM being alive: a "VM down" alarm and a cost guard.
# INPUT: tenancy_ocid, compartment_ocid, instance_id, notification_email. OUTPUT: topic_id.
# HOW: The alarm fires when the VM's CPU metric stops arriving for 5 minutes (absent()), and emails through an OCI Notifications topic. This needs the Oracle
#   Cloud Agent's monitoring plugin on the VM: UNVERIFIED until the first boot. The budget is 1 USD a month on the compartment with an email alert as soon as ANY
#   cost is forecast or actual, so an accidental paid resource shows up at once (the account stays Always Free only). Messages carry no data from the pipeline.
# ROLE IN THESIS: The "operator is told when the box is gone" requirement (docs/decisions/0016-observability-and-slos.md).
# BEFORE -> AFTER: Before: no alerts. After apply: one email subscription to confirm (click the link Oracle sends), one alarm, one budget.
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
