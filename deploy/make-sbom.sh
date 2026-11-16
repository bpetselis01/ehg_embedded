#!/bin/sh
# USAGE: deploy/make-sbom.sh [OUTDIR]     (default docs/sbom; needs Docker, and the two images built: `docker compose -f deploy/compose/docker-compose.cloud.yml build`)
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
OUT="${1:-$REPO/docs/sbom}"
TRIVY=aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"; docker volume create trivy-cache >/dev/null
for img in ehg-platform ehg-caddy; do
  docker save "$img:cloud" -o "$TMP/image.tar"
  docker run --rm -v trivy-cache:/root/.cache -v "$TMP:/in:ro" "$TRIVY" image --input /in/image.tar --format cyclonedx --quiet > "$OUT/$img.cdx.json"
  echo "wrote $OUT/$img.cdx.json"
done
docker run --rm -v trivy-cache:/root/.cache -v "$REPO/uv.lock:/in/uv.lock:ro" "$TRIVY" fs /in/uv.lock --format cyclonedx --quiet > "$OUT/python-lock.cdx.json"
echo "wrote $OUT/python-lock.cdx.json"
