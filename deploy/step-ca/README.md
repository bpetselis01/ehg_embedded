# deploy/step-ca: the private CA for gateway certificates

[`templates/ehg-gateway.tpl`](templates/ehg-gateway.tpl) is the leaf template the enrolment API relies on: the certificate carries the common name, the `digitalSignature` key usage and the **client-authentication usage only** (no server auth, no CA flag).
The 7-day lifetime is requested by the API (`notAfter`) and capped in the provisioner's claims (`maxTLSCertDuration`, `defaultTLSCertDuration`), and the API **re-checks** every certificate it gets back (`backend/enroll/csr.py`), so a wrong template shows up as a refused enrolment, not as a wrong certificate in a phone.

**Not verified (step-ca was not available):** the template variable names (`.Insecure.User` for a JWK provisioner token's custom data), the provisioner claims, the CRL/revocation options of the pinned version (`smallstep/step-ca:0.30.2`), and whether the OIDC provisioner could consume Keycloak tokens directly (a simpler route).
Initialise the CA by hand (`step ca init`), keep the **root key offline**, and put only the intermediate on the VM (decision 0013). Nothing here contains a key or a password.
