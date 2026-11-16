#!/bin/sh
# First start of PostgreSQL only: create the databases and login roles the cloud services use.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres \
  -v ehg_pw="$EHG_DB_PASSWORD" -v hapi_pw="$HAPI_DB_PASSWORD" -v kc_pw="$KEYCLOAK_DB_PASSWORD" -v exp_pw="$EXPORTER_DB_PASSWORD" -v fhir_pw="$FHIR_DB_PASSWORD" <<'SQL'
CREATE ROLE ehg LOGIN PASSWORD :'ehg_pw';
CREATE ROLE hapi LOGIN PASSWORD :'hapi_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'kc_pw';
CREATE ROLE ehg_exporter LOGIN PASSWORD :'exp_pw';
CREATE ROLE ehg_triage NOLOGIN;
CREATE ROLE ehg_enroll NOLOGIN;
CREATE ROLE ehg_fhir LOGIN PASSWORD :'fhir_pw';
GRANT pg_monitor TO ehg_exporter;
CREATE DATABASE ehg OWNER ehg;
CREATE DATABASE hapi OWNER hapi;
CREATE DATABASE keycloak OWNER keycloak;
\connect ehg
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'timescaledb could not be created here (%): migration 0003 will say so', SQLERRM;
END $$;
SQL
