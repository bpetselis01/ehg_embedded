#!/bin/sh
# Make the certificates the TLS broker needs: an offline root, an intermediate that issues, the broker's own certificate and one client certificate per component.
#   tools/pki.sh init --host ehg.example.org       root + intermediate + broker cert + clients ehg-gw-01, ehg-bridge, ehg-viewer, ehg-ingest
#   tools/pki.sh client ehg-gw-02 --days 7         one more client certificate, CN = the name (the broker uses the CN as the user name)
#   tools/pki.sh revoke ehg-gw-02                  revoke every valid certificate with that CN and rewrite ca.crl
#   tools/pki.sh crl                               rewrite ca.crl (a CRL that has expired makes OpenSSL refuse EVERY client)
#   options: --out DIR (default backend/pki, gitignored)  --host NAME-or-IP (init)  --days N (client, default 30)
#
# PURPOSE: Gives the "cloud profile" broker (backend/mosquitto/mosquitto.tls.conf) real certificates, so MQTT can be tested with TLS 1.3 and
#   client certificates on this computer and later on the cloud VM (docs/decisions/0008-mqtt-mtls-and-topic-acls.md).
# INPUT: a sub-command, and OpenSSL 3 (brew install openssl@3; macOS /usr/bin/openssl is LibreSSL and is refused).
#   e.g. tools/pki.sh init --host localhost
# OUTPUT: files under --out (every private key has mode 600; keys are never printed):
#   root/root.crt root.key       self-signed root, 10 years, ECDSA P-256. Move root.key OFF this computer once the intermediate exists
#   intermediate/ca.crt ca.key   issues every other certificate, 5 years, CA:TRUE pathlen 0. index.txt = the issued/revoked list
#   ca-chain.crt                 intermediate + root: the broker's cafile and the clients' --ca
#   ca.crl                       revocation list signed by the intermediate (valid 365 days)
#   broker.crt broker.key        server certificate (EKU serverAuth), 1 year, SANs = --host + localhost + mosquitto (the compose service name) + 127.0.0.1
#   clients/<cn>.crt <cn>.key    client certificates (EKU clientAuth only)
#   e.g. `tools/pki.sh init --host localhost` ends with a table: "ehg-gw-01     Dec  3 08:15:41 2026 GMT" (30 days after a run on 3 Nov 2026)
# HOW: Plain openssl commands. The intermediate runs `openssl ca` so serial numbers and revocations are recorded in index.txt.
#   A client certificate has no SANs: the broker reads the CN. init refuses to run twice so it can never overwrite a key.
# ROLE IN THESIS: The security hop between gateway and broker (docs/security.md). Short-lived gateway certificates, enrolment and
#   the CA service come later (decision 0013); this script is the bootstrap path.
# BEFORE -> AFTER: Before: nothing, the local broker is anonymous on 1883. After: gateway/gateway.py --mqtt-tls, backend/ingest/ingest.py
#   and viewer/server.py present these files; tests/test_mqtt_tls_config.py checks them.
set -eu
umask 077

OPENSSL="${OPENSSL:-openssl}"
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
OUT="$REPO/backend/pki"
HOST=localhost
DAYS=30
SERVICES="ehg-gw-01 ehg-bridge ehg-viewer ehg-ingest"
SUBJ_BASE="/C=AU/O=Thesis B EHG local PKI"

die() { echo "pki.sh: $*" >&2; exit 1; }

usage() { sed -n '2,/^set -/p' "$0" | sed '$d;s/^# \{0,1\}//'; }

need_openssl3() {
    "$OPENSSL" version 2>/dev/null | grep -q '^OpenSSL 3' || die "needs OpenSSL 3 (found: $("$OPENSSL" version 2>&1)). Try: brew install openssl@3, then OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl"
}

new_key() { "$OPENSSL" genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; chmod 600 "$1"; }

is_ip() { echo "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; }

# write the openssl-ca config; paths are relative to the intermediate directory (we cd there) so a space in the repo path is harmless
write_ca_config() {
    cat >"$OUT/intermediate/ca.cnf" <<'EOF'
[ ca ]
default_ca = CA_default
[ CA_default ]
dir = .
database = $dir/index.txt
new_certs_dir = $dir/newcerts
serial = $dir/serial
crlnumber = $dir/crlnumber
private_key = $dir/ca.key
certificate = $dir/ca.crt
default_md = sha256
default_crl_days = 365
unique_subject = no
policy = policy_any
[ policy_any ]
countryName = optional
organizationName = optional
commonName = supplied
EOF
}

# issue KIND(server|client) CN DAYS OUTBASE [SAN-list]: key + CSR + certificate signed by the intermediate
issue() {
    kind=$1; cn=$2; days=$3; base=$4; san=${5:-}
    new_key "$base.key"
    "$OPENSSL" req -new -key "$base.key" -subj "$SUBJ_BASE/CN=$cn" -out "$base.csr"
    ext="$base.ext"
    if [ "$kind" = server ]; then
        printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=%s\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' "$san" >"$ext"
    else
        printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' >"$ext"
    fi
    (cd "$OUT/intermediate" && "$OPENSSL" ca -batch -config ca.cnf -in "$base.csr" -out "$base.crt" -days "$days" -extfile "$ext" -notext 2>/dev/null)
    rm -f "$base.csr" "$base.ext"
    chmod 644 "$base.crt"
}

write_crl() {
    (cd "$OUT/intermediate" && "$OPENSSL" ca -config ca.cnf -gencrl -out "$OUT/ca.crl" 2>/dev/null)
    chmod 644 "$OUT/ca.crl"
}

expiry() { "$OPENSSL" x509 -in "$1" -noout -enddate | sed 's/notAfter=//'; }

cmd_init() {
    [ ! -e "$OUT/root" ] || die "$OUT already holds a PKI; init never overwrites keys. Use 'client', 'revoke' or 'crl', or choose another --out"
    mkdir -p "$OUT/root" "$OUT/intermediate/newcerts" "$OUT/clients"
    # root
    new_key "$OUT/root/root.key"
    "$OPENSSL" req -x509 -new -key "$OUT/root/root.key" -days 3650 -subj "$SUBJ_BASE/CN=Thesis B EHG root CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:1" -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" -out "$OUT/root/root.crt"
    # intermediate, signed by the root
    new_key "$OUT/intermediate/ca.key"
    "$OPENSSL" req -new -key "$OUT/intermediate/ca.key" -subj "$SUBJ_BASE/CN=Thesis B EHG intermediate CA" -out "$OUT/intermediate/ca.csr"
    printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' >"$OUT/intermediate/ca.ext"
    "$OPENSSL" x509 -req -in "$OUT/intermediate/ca.csr" -CA "$OUT/root/root.crt" -CAkey "$OUT/root/root.key" -CAcreateserial \
        -days 1825 -extfile "$OUT/intermediate/ca.ext" -out "$OUT/intermediate/ca.crt" 2>/dev/null
    rm -f "$OUT/intermediate/ca.csr" "$OUT/intermediate/ca.ext" "$OUT/root/root.srl"
    : >"$OUT/intermediate/index.txt"
    echo 1000 >"$OUT/intermediate/serial"
    echo 1000 >"$OUT/intermediate/crlnumber"
    write_ca_config
    cat "$OUT/intermediate/ca.crt" "$OUT/root/root.crt" >"$OUT/ca-chain.crt"
    chmod 644 "$OUT/ca-chain.crt" "$OUT/root/root.crt" "$OUT/intermediate/ca.crt"
    write_crl
    # broker certificate: the SAN must match the name clients connect to
    case "$HOST" in
        localhost|127.0.0.1) san="DNS:localhost,DNS:mosquitto,IP:127.0.0.1" ;;
        *) if is_ip "$HOST"; then san="IP:$HOST,DNS:localhost,DNS:mosquitto,IP:127.0.0.1"; else san="DNS:$HOST,DNS:localhost,DNS:mosquitto,IP:127.0.0.1"; fi ;;
    esac
    issue server "$HOST" 365 "$OUT/broker" "$san"
    for cn in $SERVICES; do issue client "$cn" "$DAYS" "$OUT/clients/$cn"; done
    echo "PKI written to $OUT (keys mode 600, not shown)."
    echo "Move $OUT/root/root.key off this computer now; it is only needed to replace the intermediate."
    summary
}

cmd_client() {
    [ -f "$OUT/intermediate/ca.crt" ] || die "no PKI in $OUT; run 'init' first"
    [ -n "$CN" ] || die "client needs a name, e.g. tools/pki.sh client ehg-gw-02"
    echo "$CN" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' || die "name '$CN' must be letters, digits, '.', '_' or '-' (it becomes the MQTT user name)"
    [ ! -e "$OUT/clients/$CN.key" ] || die "$OUT/clients/$CN.key exists; revoke it and choose a new name, or delete it yourself"
    issue client "$CN" "$DAYS" "$OUT/clients/$CN"
    echo "issued $OUT/clients/$CN.crt (expires: $(expiry "$OUT/clients/$CN.crt"))"
}

cmd_revoke() {
    [ -f "$OUT/intermediate/index.txt" ] || die "no PKI in $OUT"
    [ -n "$CN" ] || die "revoke needs a name"
    serials=$(awk -F'\t' -v cn="$CN" '$1=="V" && $NF ~ ("/CN=" cn "$") {print $4}' "$OUT/intermediate/index.txt")
    [ -n "$serials" ] || die "no valid certificate with CN=$CN in $OUT/intermediate/index.txt"
    for s in $serials; do
        (cd "$OUT/intermediate" && "$OPENSSL" ca -config ca.cnf -revoke "newcerts/$s.pem" 2>/dev/null)
        echo "revoked serial $s (CN=$CN)"
    done
    write_crl
    echo "ca.crl rewritten. Mosquitto reads it at start-up; see docs/security.md for whether a running broker needs a restart."
}

summary() {
    printf '%-14s %s\n' "NAME" "EXPIRES"
    printf '%-14s %s\n' "broker" "$(expiry "$OUT/broker.crt")"
    for f in "$OUT"/clients/*.crt; do printf '%-14s %s\n' "$(basename "$f" .crt)" "$(expiry "$f")"; done
}

CMD=${1:-}; [ $# -gt 0 ] && shift
CN=
case "$CMD" in -h|--help|help) usage; exit 0 ;; esac
while [ $# -gt 0 ]; do
    case "$1" in
        --out) [ $# -ge 2 ] || die "--out needs a value"; OUT=$2; shift 2 ;;
        --host) [ $# -ge 2 ] || die "--host needs a value"; HOST=$2; shift 2 ;;
        --days) [ $# -ge 2 ] || die "--days needs a value"; DAYS=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (see --help)" ;;
        *) CN=$1; shift ;;
    esac
done
case "$DAYS" in ''|*[!0-9]*) die "--days must be a whole number" ;; esac
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac  # openssl ca runs inside the intermediate directory, so paths must be absolute
case "$CMD" in
    init) need_openssl3; cmd_init ;;
    client) need_openssl3; cmd_client ;;
    revoke) need_openssl3; cmd_revoke ;;
    crl) need_openssl3; [ -f "$OUT/intermediate/index.txt" ] || die "no PKI in $OUT"; write_crl; echo "ca.crl rewritten" ;;
    *) usage >&2; exit 2 ;;
esac
