#!/bin/sh
# PURPOSE: Prove the enrolment API's CA calls against a REAL step-ca on this computer (no cloud): sign, post-issue checks, revoke, and a PEM CRL that OpenSSL verifies.
# USAGE: deploy/step-ca/local-check.sh      (needs a running Docker; pulls smallstep/step-ca:0.30.2; uses port 9000 on 127.0.0.1; removes its container at the end)
#   If `docker pull` hangs on "error getting credentials", run with DOCKER_CONFIG pointing at a folder holding {} (and a cli-plugins link): see docs/progress.md, 2026-11-04.
# HOW: starts the CA, adds the JWK provisioner "ehg-enroll" with deploy/step-ca/templates/ehg-gateway.tpl and the 7-day claims, enables the CRL, then deploy/step-ca/local_check.py does the calls with backend.enroll.stepca_client.
#   The audience of a token is the CA's own URL, so the CA must be reached on the port it was configured with (9000), not a mapped one. Keys and certificates go to a temporary folder that is deleted.
# RESULT (2026-11-04, step-ca 0.30.2): sign OK, CN and client-auth only and 7 days, revoke OK, CRL lists the serial and verifies.
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd -P)"
WORK="$(mktemp -d)"
CA_SECRET="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"  # throwaway CA, new value every run, never written to a file here
trap 'docker rm -f stepca-local-check >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
docker rm -f stepca-local-check >/dev/null 2>&1 || true
docker run -d --name stepca-local-check -p 127.0.0.1:9000:9000 -e DOCKER_STEPCA_INIT_NAME=EHG -e DOCKER_STEPCA_INIT_DNS_NAMES=localhost,step-ca \
  -e DOCKER_STEPCA_INIT_PROVISIONER_NAME=admin -e DOCKER_STEPCA_INIT_PASSWORD="$CA_SECRET" -e DOCKER_STEPCA_INIT_REMOTE_MANAGEMENT=true smallstep/step-ca:0.30.2 >/dev/null
i=0; until docker exec stepca-local-check step ca health --ca-url https://localhost:9000 --root /home/step/certs/root_ca.crt >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 40 ] || { echo "step-ca did not start"; exit 1; }; sleep 1; done
docker cp "$REPO/deploy/step-ca/templates/ehg-gateway.tpl" stepca-local-check:/home/step/ehg-gateway.tpl
docker exec stepca-local-check sh -c 'cd /home/step && step crypto jwk create /tmp/pub.json /tmp/priv.json --use sig --kty EC --crv P-256 --no-password --insecure --force >/dev/null 2>&1 \
 && step ca provisioner add ehg-enroll --type JWK --public-key /tmp/pub.json --x509-template /home/step/ehg-gateway.tpl --x509-min-dur 1h --x509-max-dur 168h --x509-default-dur 168h \
    --admin-subject step --admin-provisioner admin --admin-password-file /home/step/secrets/password --ca-url https://localhost:9000 --root /home/step/certs/root_ca.crt >/dev/null 2>&1 \
 && sed -i "s|\"db\": {|\"crl\": {\"enabled\": true, \"generateOnRevoke\": true, \"cacheDuration\": \"24h\", \"idpURL\": \"https://step-ca:9000/1.0/crl\"},\n\t\"db\": {|" /home/step/config/ca.json'
docker restart stepca-local-check >/dev/null
i=0; until docker exec stepca-local-check step ca health --ca-url https://localhost:9000 --root /home/step/certs/root_ca.crt >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 40 ] || { echo "step-ca did not restart"; exit 1; }; sleep 1; done
docker exec stepca-local-check step crypto key format --pem --no-password --insecure /tmp/priv.json > "$WORK/prov.pem"
docker cp stepca-local-check:/home/step/certs/root_ca.crt "$WORK/root_ca.crt"
docker cp stepca-local-check:/home/step/certs/intermediate_ca.crt "$WORK/intermediate_ca.crt"
docker exec stepca-local-check step certificate fingerprint /home/step/certs/root_ca.crt > "$WORK/fp"
chmod 600 "$WORK/prov.pem"
cd "$REPO" && uv run python deploy/step-ca/local_check.py "$WORK"
