# deploy/step-ca: the private CA for gateway certificates

[`templates/ehg-gateway.tpl`](templates/ehg-gateway.tpl) is the leaf template the enrolment API relies on: the certificate carries the common name, the `digitalSignature` key usage and the **client-authentication usage only** (no server auth, no CA flag).
The 7-day lifetime is requested by the API (`notAfter`) and capped in the provisioner's claims (`maxTLSCertDuration`, `defaultTLSCertDuration`), and the API **re-checks** every certificate it gets back (`backend/enroll/csr.py`), so a wrong template shows up as a refused enrolment, not as a wrong certificate in a phone.

**Checked on a real step-ca 0.30.2 (2026-11-04, `deploy/step-ca/local-check.sh`, needs Docker):** sign, the API's post-issue checks (CN, client-auth only, 7 days), revoke, and a CRL that OpenSSL verifies. It found three things a fake CA could not:
1. the one-time token needs a `kid` header (the key's RFC 7638 thumbprint), else 401 "provisioner not found";
2. the revoke token's subject must be the certificate **serial**, else 403;
3. the leaf template must take the subject from the CSR (`{{ toJson .Subject }}`): the earlier `.Insecure.User.commonName` is empty unless the client sends template data, and the certificate came back with NO name.
Also: the CRL is served at `GET /crl`, as **DER** unless you ask `?pem=true`; enable it in `ca.json` (`"crl": {"enabled": true, "generateOnRevoke": true, "cacheDuration": "24h"}`). **Use a long `cacheDuration`:** a CRL that expires makes OpenSSL (and so Mosquitto) refuse EVERY client, and `deploy/crl-sync.sh` only polls every 5 minutes.
**Still not verified:** whether the OIDC provisioner could consume Keycloak tokens directly (a simpler route); step-ca running in the cloud compose file (it is not in it); the provisioner key handling on the VM; `deploy/crl-sync.sh` against this CA end to end (its OpenSSL checks pass on this CRL by hand).
Initialise the CA by hand (`step ca init`), keep the **root key offline**, and put only the intermediate on the VM (decision 0013). Nothing here contains a key or a password.
