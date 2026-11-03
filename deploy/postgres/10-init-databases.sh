#!/bin/sh
# PURPOSE: First start of PostgreSQL only: create the databases and login roles the cloud services use. Runs once, from /docker-entrypoint-initdb.d.
# INPUT: environment variables (from the decrypted secrets): EHG_DB_PASSWORD, HAPI_DB_PASSWORD, KEYCLOAK_DB_PASSWORD, EXPORTER_DB_PASSWORD.
#   e.g. creates roles ehg, hapi, keycloak, ehg_exporter and databases ehg, hapi, keycloak (one owner each).
# OUTPUT: nothing printed except psql's own messages; a missing variable stops the script with an error (set -eu), so the container fails visibly.
# HOW: psql variables with :'name' quoting put the passwords into the statements safely, so a quote in a password cannot break or inject SQL.
#   ehg_exporter gets only pg_monitor (read-only statistics) for postgres_exporter. TimescaleDB itself is enabled by the migrations (Unit 6), not here.
# STATUS: UNVERIFIED: the timescaledb-ha image is expected to run initdb.d scripts like the official Postgres image; nobody has started it yet.
# ROLE IN THESIS: One database server, three databases (docs/decisions/0012); least privilege per service.
# BEFORE -> AFTER: Before: an empty cluster. After: three empty databases with their owners.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres \
  -v ehg_pw="$EHG_DB_PASSWORD" -v hapi_pw="$HAPI_DB_PASSWORD" -v kc_pw="$KEYCLOAK_DB_PASSWORD" -v exp_pw="$EXPORTER_DB_PASSWORD" <<'SQL'
CREATE ROLE ehg LOGIN PASSWORD :'ehg_pw';
CREATE ROLE hapi LOGIN PASSWORD :'hapi_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE ROLE ehg_exporter LOGIN PASSWORD :'exp_pw';
GRANT pg_monitor TO ehg_exporter;
CREATE DATABASE ehg OWNER ehg;
CREATE DATABASE hapi OWNER hapi;
CREATE DATABASE keycloak OWNER keycloak;
SQL
