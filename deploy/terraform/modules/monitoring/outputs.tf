output "topic_id" {
  description = "OCID of the alerts topic."
  value       = oci_ons_notification_topic.alerts.id
}
