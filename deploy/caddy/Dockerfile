# Caddy rebuilt with a patched Go toolchain and patched libraries, because the official 2.11.7 image (the newest release, nothing newer exists) carried 1 CRITICAL and 8 HIGH fixable findings (trivy, 2026-11-04): Go standard library (net/http, crypto/tls, crypto/x509; fixed in Go 1.26.10 / 1.27.2+), golang.org/x/net and go-jose (fixed in v0.60.0 / v4.1.6), and Alpine's brotli and curl (fixed by `apk upgrade`).
FROM caddy:2.11.7-builder@sha256:c4cd20eaa0b69112fd5d4f721ffc9671687e1d5d0b155c8620cf7546dbef94c0 AS build
RUN xcaddy build v2.11.7 --output /caddy \
      --replace golang.org/x/net=golang.org/x/net@v0.60.0 \
      --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6
FROM caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5
RUN apk upgrade --no-cache
COPY --from=build /caddy /usr/bin/caddy
