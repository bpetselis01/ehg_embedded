#!/bin/sh
# PURPOSE: Creates the private CA for gateway certificates on YOUR computer, so the ROOT key never goes to the cloud VM (decision 0013). Run it once, offline-ish, with Docker.
# USAGE: deploy/step-ca/init-offline.sh OUTDIR        (OUTDIR must not exist; e.g. ~/ehg-ca)
# OUTPUT (all under OUTDIR; nothing is written inside the repository):
#   vm/        copy this folder to the VM as /opt/ehg/step-ca, then run `sudo deploy/fix-key-permissions.sh` on the VM (uid 1000 owns it, except the enrolment key). It holds the config, the root CERTIFICATE, the intermediate certificate and
#              key (folder keys/), the password file of the intermediate key, and enroll/provisioner.pem (the key the enrolment API signs one-time tokens with, mode 640) and enroll/root_ca.crt.
#   offline/   the ROOT KEY (root_ca_key, encrypted) and its password: keep OFFLINE (password manager / encrypted disk image). Not needed again until the intermediate must be replaced.
#   env.txt    one line to add to /opt/ehg/etc/site.env on the VM: EHG_STEPCA_ROOT_FP=<root fingerprint>   (plus COMPOSE_PROFILES=enrol to start the CA and the enrolment API)
#   The broker's trust chain for gateway certificates is vm/certs/intermediate_ca.crt followed by vm/certs/root_ca.crt: use it as /opt/ehg/pki/ca-chain.crt (with the broker's own certificate
#   issued by the same CA or kept as a separate trust anchor: see docs/deployment.md).
# HOW: TWO passwords: the root key keeps the first (offline/ca-password.txt), the intermediate key is re-encrypted under a second one that is the only one on the VM (vm/keys/password), so a stolen VM
#   does not unlock the root key file even if that file leaked. The init provisioner (which the CA's own password could unlock) is REMOVED again, leaving only ehg-enroll. No remote management: the provisioner is written INTO ca.json (a file you can read and back up), not into a database that would also have to be copied.
#   Runs smallstep/step-ca (pinned) in a throwaway container with a random password, adds the JWK provisioner "ehg-enroll" (leaf template deploy/step-ca/templates/ehg-gateway.tpl, 1 h to 7 days, default 7 days; renewal by the old certificate and the SSH CA switched OFF: a certificate is renewed only by enrolling again through the API, where the registry is checked),
#   enables the CRL with a 24 h cache (a CRL that expires locks EVERY client out), copies the files out and removes the container. The two passwords are random and only in files under OUTDIR (mode 600).
# STATUS: the same steps as deploy/step-ca/local-check.sh, which signs, revokes and verifies the CRL against a CA made this way (real step-ca 0.30.2). The compose profile "enrol" using the result was started locally once.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 OUTDIR"; exit 2; }
OUT="$1"
[ ! -e "$OUT" ] || { echo "$OUT exists: not overwriting"; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd -P)"
case "$(cd "$(dirname "$OUT")" && pwd -P)/" in "$REPO"/*) echo "OUTDIR must be OUTSIDE the repository"; exit 1 ;; esac
IMAGE="smallstep/step-ca:0.30.2@sha256:a2b17872915c193259b75a5474c398326f41bd199f0842093e52cf4182bc8270"
NAME="stepca-init-$$"
umask 077
mkdir -p "$OUT/vm" "$OUT/offline"
PW="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"      # the ROOT key's password: stays offline
VMPW="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"    # the INTERMEDIATE key's password: goes to the VM
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$NAME" -e DOCKER_STEPCA_INIT_NAME=EHG -e DOCKER_STEPCA_INIT_DNS_NAMES=step-ca,localhost -e DOCKER_STEPCA_INIT_PROVISIONER_NAME=bootstrap-unused \
  -e DOCKER_STEPCA_INIT_PASSWORD="$PW" "$IMAGE" >/dev/null
i=0; until docker exec "$NAME" step ca health --ca-url https://localhost:9000 --root /home/step/certs/root_ca.crt >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || { echo "step-ca did not start"; exit 1; }; sleep 1; done
docker cp "$REPO/deploy/step-ca/templates/ehg-gateway.tpl" "$NAME:/home/step/ehg-gateway.tpl"
docker exec -e VMPW="$VMPW" "$NAME" sh -c 'cd /home/step && step crypto jwk create /tmp/pub.json /tmp/priv.json --use sig --kty EC --crv P-256 --no-password --insecure --force >/dev/null 2>&1 \
 && step ca provisioner add ehg-enroll --type JWK --public-key /tmp/pub.json --x509-template /home/step/ehg-gateway.tpl --x509-min-dur 1h --x509-max-dur 168h --x509-default-dur 168h --disable-renewal --ssh=false \
    --ca-config /home/step/config/ca.json >/dev/null 2>&1 \
 && step ca provisioner remove bootstrap-unused --ca-config /home/step/config/ca.json >/dev/null 2>&1 \
 && printf %s "$VMPW" > /tmp/vmpw \
 && step crypto change-pass /home/step/secrets/intermediate_ca_key --password-file /home/step/secrets/password --new-password-file /tmp/vmpw --force >/dev/null 2>&1 \
 && cp /tmp/vmpw /home/step/secrets/password \
 && sed -i "s|\"db\": {|\"crl\": {\"enabled\": true, \"generateOnRevoke\": true, \"cacheDuration\": \"24h\", \"idpURL\": \"https://step-ca:9000/1.0/crl\"},\n\t\"db\": {|" /home/step/config/ca.json'
docker stop "$NAME" >/dev/null     # stop before copying so the database files are consistent
mkdir -p "$OUT/vm/enroll" "$OUT/vm/db"
docker cp "$NAME:/home/step/config" "$OUT/vm/config"
docker cp "$NAME:/home/step/certs" "$OUT/vm/certs"
docker cp "$NAME:/home/step/secrets" "$OUT/vm/keys"   # mounted as /home/step/secrets in the container
docker start "$NAME" >/dev/null
docker exec "$NAME" step crypto key format --pem --no-password --insecure /tmp/priv.json > "$OUT/vm/enroll/provisioner.pem"
cp "$OUT/vm/certs/root_ca.crt" "$OUT/vm/enroll/root_ca.crt"
FP="$(docker exec "$NAME" step certificate fingerprint /home/step/certs/root_ca.crt)"
mv "$OUT/vm/keys/root_ca_key" "$OUT/offline/root_ca_key"
printf '%s\n' "$PW" > "$OUT/offline/ca-password.txt"
rm -f "$OUT"/vm/config/*.bak 2>/dev/null || true
chmod 640 "$OUT/vm/enroll/provisioner.pem"
printf 'EHG_STEPCA_ROOT_FP=%s\nCOMPOSE_PROFILES=enrol\n' "$FP" > "$OUT/env.txt"
echo "CA written to $OUT: copy $OUT/vm to the VM as /opt/ehg/step-ca, run sudo deploy/fix-key-permissions.sh there, keep $OUT/offline OFFLINE, add $OUT/env.txt to /opt/ehg/etc/site.env"
