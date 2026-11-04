#!/bin/sh
# PURPOSE: Scans every image the cloud stack runs for FIXABLE high and critical vulnerabilities (trivy), and fails if an INTERNET-FACING image has any.
# USAGE: deploy/scan-images.sh          (needs Docker; pulls aquasec/trivy; ~10 minutes the first time, the vulnerability database is cached in the volume trivy-cache)
#   The two images this repository builds (ehg-caddy:cloud, ehg-platform:cloud) must exist: `docker compose -f deploy/compose/docker-compose.cloud.yml build` first.
# OUTPUT: one line per image: name, CRITICAL count, HIGH count, the affected packages. Exit 1 when ehg-caddy:cloud (80/443) or eclipse-mosquitto (8883) has any finding.
# WHY ONLY THOSE TWO FAIL THE RUN: everything else sits on the internal Docker network. Third-party images (Keycloak, HAPI, Grafana, step-ca, TimescaleDB, cAdvisor, the exporters) were pinned at the NEWEST release on 2026-11-04 and still carried fixable
#   findings inside their bundled libraries; only the vendor can fix those. They are listed so a newer release is noticed (docs/supply-chain.md has the baseline and the reasoning).
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
docker volume create trivy-cache >/dev/null
images="$(grep -hoE 'image: [^ ]+@sha256:[0-9a-f]{64}' "$REPO/deploy/compose/docker-compose.cloud.yml" | sed 's/image: //' | sort -u) ehg-caddy:cloud ehg-platform:cloud"
bad=0
for img in $images; do
  json="$(docker run --rm -v trivy-cache:/root/.cache -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image "$img" --severity HIGH,CRITICAL --ignore-unfixed --exit-code 0 --quiet --format json 2>/dev/null || true)"
  line="$(printf '%s' "$json" | python3 -c '
import sys, json, collections
try: r = json.load(sys.stdin)
except Exception: print("SCAN FAILED"); sys.exit()
c = collections.Counter(); pk = set()
for res in r.get("Results", []):
    for v in res.get("Vulnerabilities") or []:
        c[v["Severity"]] += 1; pk.add(v["PkgName"])
print("CRITICAL %d HIGH %d | %s" % (c["CRITICAL"], c["HIGH"], ", ".join(sorted(pk))[:80]))')"
  printf '%-58s %s\n' "${img%%@*}" "$line"
  case "$img" in ehg-caddy:cloud*|eclipse-mosquitto*) case "$line" in "CRITICAL 0 HIGH 0"*) ;; *) bad=1 ;; esac ;; esac
done
exit "$bad"
