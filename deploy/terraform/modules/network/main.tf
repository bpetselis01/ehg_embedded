# PURPOSE: The VM's private network: one VCN, one public subnet, an internet gateway and a security list that opens only what the design needs.
# INPUT: compartment_ocid, admin_cidr (variables.tf). OUTPUT: subnet_id.
# HOW: Inbound TCP 22 only from admin_cidr; 80 and 443 (Caddy, the Let's Encrypt challenge and the web pages) and 8883 (MQTT over TLS 1.3 with client certificates) from anywhere.
#   Everything else inbound is closed (so Postgres 5432, InfluxDB 8086 and the old Grafana 3300 are NOT reachable). ICMP type 3 code 4
#   is allowed so path-MTU discovery works. All outbound is allowed. The VM's own firewall (cloud-init) repeats these rules.
# ROLE IN THESIS: The network boundary of the cloud core (docs/security.md, docs/production-topology.md).
# BEFORE -> AFTER: Before: no network. After apply: a /16 VCN with a /24 public subnet.
resource "oci_core_vcn" "ehg" {
  compartment_id = var.compartment_ocid
  display_name   = "ehg-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "ehg"
}

resource "oci_core_internet_gateway" "ehg" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.ehg.id
  display_name   = "ehg-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.ehg.id
  display_name   = "ehg-public-routes"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.ehg.id
  }
}

resource "oci_core_security_list" "ehg" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.ehg.id
  display_name   = "ehg-security-list"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
    stateless   = false
  }

  ingress_security_rules {
    description = "SSH from the administrator only"
    protocol    = "6"
    source      = var.admin_cidr
    stateless   = false

    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    description = "HTTP (Let's Encrypt challenge, redirect to HTTPS)"
    protocol    = "6"
    source      = "0.0.0.0/0"
    stateless   = false

    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    description = "HTTPS (Caddy: Grafana, Keycloak)"
    protocol    = "6"
    source      = "0.0.0.0/0"
    stateless   = false

    tcp_options {
      min = 443
      max = 443
    }
  }

  ingress_security_rules {
    description = "MQTT over TLS 1.3, client certificate required"
    protocol    = "6"
    source      = "0.0.0.0/0"
    stateless   = false

    tcp_options {
      min = 8883
      max = 8883
    }
  }

  ingress_security_rules {
    description = "Path MTU discovery"
    protocol    = "1"
    source      = "0.0.0.0/0"
    stateless   = false

    icmp_options {
      type = 3
      code = 4
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.ehg.id
  display_name      = "ehg-public"
  cidr_block        = "10.0.1.0/24"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.ehg.id]
}
