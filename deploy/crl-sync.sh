#!/bin/sh
# PURPOSE: Make a revocation reach the broker. Fetches the current certificate revocation list (CRL) from the enrolment API, checks it, installs it where Mosquitto reads it, and tells Mosquitto to reload.
#   Without this, revoking a gateway only changes the registry: the broker reads its CRL at start-up and on SIGHUP only (docs/security.md), so a revoked certificate would keep working until the broker restarts.
# INPUT: --url URL (default https://$EHG_DOMAIN/enroll-api/crl, EHG_DOMAIN from /opt/ehg/etc/site.env), --pki-dir DIR (default /opt/ehg/pki: needs ca-chain.crt and ca.crl), --container NAME (default ehg-mosquitto-1), --dry-run.
#   Commands can be replaced for tests: CURL, DOCKER, OPENSSL (default curl, docker, openssl). OpenSSL 3 is expected (the checks use `openssl crl`).
#   e.g. crl-sync.sh --dry-run
# OUTPUT: "unchanged" (nothing to do), "installed CRL number N and reloaded <container>", or an error and a non-zero exit with the OLD list left untouched. A one-line message goes to the system log (logger) when it can.
# HOW: 1. fetch to a temporary file (HTTPS, fail on error, 20 s limit). 2. refuse anything that is not a PEM CRL, whose signature does not verify against ca-chain.crt, that has already expired, or whose CRL number is LOWER than the installed one (an old list replayed would un-revoke
#   certificates). 3. if identical to the installed list, stop. 4. keep a copy as ca.crl.prev and write the new list INTO THE EXISTING FILE (cat >, same inode): the compose file bind-mounts the single file ca.crl, and a rename would leave the container looking at the old one.
#   5. send SIGHUP to the broker container. The broker keeps its old list if the new one cannot be read, and the signal is only sent after a verified install.
#   Run it from cron every 5 minutes (cloud-init installs /etc/cron.d/ehg-crl-sync); a revocation then takes effect within about 5 minutes. Existing connections are not dropped by a reload: a revoked client is refused when it next connects (<= its next reconnect), documented in docs/security.md.
# STATUS: tested with real OpenSSL CRLs from tools/pki.sh and a stub `docker` (tests/test_crl_sync.py). NEVER run against a live broker container or the enrolment API.
# ROLE IN THESIS: Closes the "revocation does not reach the broker" gap of the certificate lifecycle (decision 0013).
# BEFORE -> AFTER: Before: revoke changed the registry and the CA only. After: it reaches the broker within one cron interval.
set -eu

OPENSSL="${OPENSSL:-openssl}"
CURL="${CURL:-curl}"
DOCKER="${DOCKER:-docker}"
PKI=/opt/ehg/pki
CONTAINER=ehg-mosquitto-1
URL=""
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --pki-dir) PKI="$2"; shift 2 ;;
    --container) CONTAINER="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$CONTAINER" in *[!A-Za-z0-9_.-]*|'') echo "unsafe container name" >&2; exit 2 ;; esac
if [ -z "$URL" ]; then
  [ -f /opt/ehg/etc/site.env ] && . /opt/ehg/etc/site.env
  [ -n "${EHG_DOMAIN:-}" ] || { echo "no --url and no EHG_DOMAIN in /opt/ehg/etc/site.env" >&2; exit 2; }
  URL="https://$EHG_DOMAIN/enroll-api/crl"
fi

log() { echo "$*"; logger -t ehg-crl-sync "$*" 2>/dev/null || true; }
fail() { log "ERROR: $*"; exit 1; }

if [ "$DRY" = 1 ]; then
  echo "[dry-run] would fetch $URL, verify it against $PKI/ca-chain.crt, refuse an expired list or a lower CRL number than $PKI/ca.crl,"
  echo "[dry-run] then write it into $PKI/ca.crl (same file, keeping a copy as ca.crl.prev) and run: $DOCKER kill --signal=HUP $CONTAINER"
  exit 0
fi

[ -f "$PKI/ca-chain.crt" ] && [ -f "$PKI/ca.crl" ] || fail "$PKI must hold ca-chain.crt and ca.crl"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
"$CURL" --fail --silent --show-error --max-time 20 --output "$TMP" "$URL" || fail "could not fetch the revocation list from $URL"
grep -q "BEGIN X509 CRL" "$TMP" || fail "the answer is not a PEM revocation list"
# NOTE: `openssl crl -verify` prints "verify failure" but still EXITS 0, so the exit status proves nothing: the printed result is what is checked (a test with a list from another CA caught this)
VERIFY=$("$OPENSSL" crl -in "$TMP" -noout -CAfile "$PKI/ca-chain.crt" -verify 2>&1 || true)
[ "$VERIFY" = "verify OK" ] || fail "the revocation list's signature does not verify against $PKI/ca-chain.crt"
NEXT=$("$OPENSSL" crl -in "$TMP" -noout -nextupdate | sed 's/^nextUpdate=//')
NEW_NUM=$("$OPENSSL" crl -in "$TMP" -noout -crlnumber | sed 's/^crlNumber=//')
OLD_NUM=$("$OPENSSL" crl -in "$PKI/ca.crl" -noout -crlnumber 2>/dev/null | sed 's/^crlNumber=//' || true)
# the list must still be valid: nextUpdate in the future (GNU date on the VM, BSD date on a Mac; 0 if neither can read it, which fails safe)
EXPIRES=$(date -u -d "$NEXT" +%s 2>/dev/null || date -u -j -f "%b %e %T %Y %Z" "$NEXT" +%s 2>/dev/null || echo 0)
[ "$EXPIRES" -gt "$(date -u +%s)" ] || fail "the revocation list has expired (nextUpdate $NEXT)"
HEX_NEW=$(printf '%s' "$NEW_NUM" | tr -d '\n'); HEX_NEW=${HEX_NEW#0x}; HEX_OLD=$(printf '%s' "${OLD_NUM:-0}" | tr -d '\n'); HEX_OLD=${HEX_OLD#0x}  # openssl prints the number in hex, with or without 0x
if [ "$(printf '%d' "0x${HEX_NEW:-0}")" -lt "$(printf '%d' "0x${HEX_OLD:-0}")" ]; then fail "refusing an OLDER list (number $HEX_NEW, installed $HEX_OLD): it could un-revoke certificates"; fi
if cmp -s "$TMP" "$PKI/ca.crl"; then echo "unchanged (CRL number $HEX_NEW)"; exit 0; fi

cp "$PKI/ca.crl" "$PKI/ca.crl.prev"
cat "$TMP" > "$PKI/ca.crl"
"$DOCKER" kill --signal=HUP "$CONTAINER" >/dev/null || fail "installed the list but could not signal $CONTAINER (it will pick the list up at its next restart)"
log "installed CRL number $HEX_NEW and reloaded $CONTAINER"
