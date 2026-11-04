#!/bin/sh
# PURPOSE: Gives every container user read access to exactly the key files it needs, and nobody else. Run it once with sudo after copying the certificates (and the CA bundle) to the VM, and again after replacing any of them.
# USAGE: sudo deploy/fix-key-permissions.sh [--pki /opt/ehg/pki] [--step-ca /opt/ehg/step-ca] [--owner ehg]
# WHY: bind-mounted files keep their host owner and mode on Linux (on a Mac they look permissive, so this never showed there). The containers run as fixed non-root users:
#   Mosquitto uid/gid 1883 (reads broker.key), the Python services uid 10001 (read ehg-bridge.key and the enrolment key), step-ca uid 1000 (owns its folders). A key that is 600 and owned by `ehg` is unreadable to them.
#   Certificates are public and stay world-readable; every PRIVATE key is group-readable by its one consumer only (640, group = that container's gid), never world-readable.
# WHAT IT SETS: pki/broker.key ehg:1883 640 | pki/ehg-bridge.key and ehg-exporter.key ehg:10001 640 | pki/*.crt, ca.crl ehg:ehg 644 (ca.crl is rewritten by deploy/crl-sync.sh as ehg) |
#   step-ca/ recursively 1000:1000 (database, intermediate key, password), THEN step-ca/enroll/provisioner.pem ehg:10001 640 (the enrolment API reads it; the order matters) and enroll/root_ca.crt 644.
# OUTPUT: one line per file; exit 1 if a required key is missing (broker.key, ehg-bridge.key); the step-ca part is skipped with a note if that folder does not exist (profile "enrol" not in use).
# TEST: tests/test_fix_key_permissions.py runs it against a temporary folder with chown/chmod replaced by recorders (CHOWN=, CHMOD=). NEVER run on a real VM before today: expect the first run to need a look.
set -eu
OWNER=ehg; PKI=/opt/ehg/pki; STEPCA=/opt/ehg/step-ca
while [ $# -gt 0 ]; do
  case "$1" in
    --pki) PKI="$2"; shift 2 ;;
    --step-ca) STEPCA="$2"; shift 2 ;;
    --owner) OWNER="$2"; shift 2 ;;
    *) echo "usage: $0 [--pki DIR] [--step-ca DIR] [--owner USER]"; exit 2 ;;
  esac
done
CHOWN="${CHOWN:-chown}"; CHMOD="${CHMOD:-chmod}"
if [ "$CHOWN" = chown ] && [ "$(id -u)" != 0 ]; then echo "run with sudo (chown to another user needs root)"; exit 2; fi

set_file() {  # path owner:group mode required(1/0)
  if [ -e "$1" ]; then
    "$CHOWN" "$2" "$1"; "$CHMOD" "$3" "$1"; echo "ok   $1 -> $2 $3"
  elif [ "$4" = 1 ]; then
    echo "MISSING (required) $1"; MISSING=1
  else
    echo "skip $1 (not present)"
  fi
}
MISSING=0
set_file "$PKI/broker.key" "$OWNER:1883" 640 1
set_file "$PKI/ehg-bridge.key" "$OWNER:10001" 640 1
set_file "$PKI/ehg-exporter.key" "$OWNER:10001" 640 1
for f in ca-chain.crt broker.crt ehg-bridge.crt ehg-exporter.crt ca.crl; do set_file "$PKI/$f" "$OWNER:$OWNER" 644 0; done
if [ -d "$STEPCA" ]; then
  "$CHOWN" -R 1000:1000 "$STEPCA"; echo "ok   $STEPCA -> 1000:1000 (recursive)"
  set_file "$STEPCA/enroll/provisioner.pem" "$OWNER:10001" 640 1   # AFTER the recursive chown, or it would go back to 1000
  set_file "$STEPCA/enroll/root_ca.crt" "$OWNER:$OWNER" 644 1
else
  echo "skip $STEPCA (profile enrol not in use)"
fi
[ "$MISSING" = 0 ] || exit 1
