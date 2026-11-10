# PURPOSE: Caddy rebuilt with a patched Go toolchain and patched libraries, because the official 2.11.7 image (the newest release, nothing newer exists) carried 1 CRITICAL and 8 HIGH fixable findings (trivy, 2026-11-04):
#   Go standard library (net/http, crypto/tls, crypto/x509; fixed in Go 1.26.10 / 1.27.2+), golang.org/x/net and go-jose (fixed in v0.60.0 / v4.1.6), and Alpine's brotli and curl (fixed by `apk upgrade`).
#   Caddy is the only service that faces the internet, so it is the one image worth rebuilding.
# HOW: the official builder image (Go 1.27.3) compiles the SAME Caddy release (v2.11.7) with xcaddy, replacing the two vulnerable modules; the runtime stage is the official image with the OS packages upgraded and the binary swapped in.
#   Built on the VM by `docker compose build` (deploy/update.sh does this); both base images are pinned by digest. Remove this file and go back to the plain image once an official build no longer shows findings (docs/supply-chain.md).
# STATUS: built and run on the local stack 2026-11-04 (config validates, routes and headers unchanged). Build time on the 2-OCPU arm64 VM is NOT measured.
FROM caddy:2.11.7-builder@sha256:c4cd20eaa0b69112fd5d4f721ffc9671687e1d5d0b155c8620cf7546dbef94c0 AS build
RUN xcaddy build v2.11.7 --output /caddy \
      --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
      --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6
FROM caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5
RUN apk upgrade --no-cache
COPY --from=build /caddy /usr/bin/caddy
