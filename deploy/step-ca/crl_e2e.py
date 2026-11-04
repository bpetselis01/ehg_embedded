"""Revocation end to end on a laptop: enrol through the real enrolment API, connect to a real Mosquitto with the step-ca certificate, revoke through the API, run deploy/crl-sync.sh, connect again.

USAGE (the 22-service stack and the profile enrol must be running, a Keycloak user named like the gateway with the role `gateway`, and `admin1` with `admin`; each gateway name works ONCE because a revoked gateway cannot enrol again):
  uv run python deploy/step-ca/crl_e2e.py <scratch-dir-with-cloud/pki,cloud/caddy-root.crt,ehg-ca2/vm/certs> <gateway-name>
Expected: connects, still connects before crl-sync (the broker reads its CRL on SIGHUP), crl-sync installs the new CRL in the SAME file, then the certificate is refused. Paths below are those of the 2026-11-04 session; adapt them.
"""
import os, subprocess, sys, time, threading, shutil, httpx
sys.path.insert(0, ".")
from pathlib import Path
from backend.enroll.cli import make_key_and_csr
from tools import gen_acl
from gateway import gateway
import paho.mqtt.client as mqtt
GW = sys.argv[2]
SP = Path(sys.argv[1]); W = SP / "crl"; base = "https://localhost"
c = httpx.Client(verify=str(SP / "cloud/caddy-root.crt"))
def token(user, pw="PhonePw-12345"):
    return c.post(base + "/auth/realms/ehg/protocol/openid-connect/token", data=dict(grant_type="password", client_id="ios-gateway", username=user, password=pw, scope="openid")).json()["access_token"]
key, csr = make_key_and_csr(GW)
r = c.post(base + "/enroll-api/enroll", json={"csr": csr}, headers={"Authorization": "Bearer " + token(GW)}); assert r.status_code == 200, r.text
(W / "leaf.crt").write_text(r.json()["certificate"]); (W / "leaf.key").write_bytes(key); os.chmod(W / "leaf.key", 0o600)
certs = SP / "ehg-ca2/vm/certs"   # the API returns [leaf, intermediate]: the broker needs the ROOT too, as a trust anchor
(W / "ca-chain.crt").write_text((certs / "intermediate_ca.crt").read_text() + (certs / "root_ca.crt").read_text())
pki = W / "pki"; pki.mkdir(exist_ok=True)
shutil.copy(W / "ca-chain.crt", pki / "ca-chain.crt")
for f in ("broker.crt", "broker.key"): shutil.copy(SP / "cloud/pki" / f, pki / f)
(pki / "ca.crl").write_bytes(c.get(base + "/enroll-api/crl").content)
(W / "acl").write_text(gen_acl.render({GW: ["EHG-IT"]}))
(W / "mosquitto.conf").write_text(Path("backend/mosquitto/mosquitto.tls.conf").read_text())
subprocess.run(["docker", "kill", "crlsync-broker"], capture_output=True)
time.sleep(1)
image = [l for l in Path("deploy/compose/docker-compose.cloud.yml").read_text().splitlines() if "eclipse-mosquitto" in l][0].split("image:")[1].strip()
subprocess.run(["docker", "run", "--rm", "-d", "--name", "crlsync-broker", "-p", "127.0.0.1:18883:8883", "-v", f"{W}/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro", "-v", f"{W}/acl:/mosquitto/config/acl:ro",
                "-v", f"{pki}/ca-chain.crt:/mosquitto/pki/ca-chain.crt:ro", "-v", f"{pki}/broker.crt:/mosquitto/pki/broker.crt:ro", "-v", f"{pki}/broker.key:/mosquitto/pki/broker.key:ro",
                "-v", f"{pki}/ca.crl:/mosquitto/pki/ca.crl", image], check=True, capture_output=True)
time.sleep(3)
ctx = gateway.tls_context(str(SP / "cloud/pki/ca-chain.crt"), str(W / "leaf.crt"), str(W / "leaf.key"))   # trust the broker's pki.sh chain; present the step-ca certificate
def connects():
    up = threading.Event(); m = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="x")
    m.tls_set_context(ctx); m.on_connect = lambda *a: up.set() if not a[3].is_failure else None
    try: m.connect("localhost", 18883)
    except Exception: return False
    m.loop_start(); ok = up.wait(4); m.loop_stop(); m.disconnect(); return ok
ok1 = connects(); print("1 before revocation, the step-ca certificate connects:", ok1)
if not ok1: print("   broker log:", subprocess.run(["docker", "logs", "crlsync-broker"], capture_output=True, text=True).stderr[-500:])
r = c.post(base + "/enroll-api/revoke/" + GW, headers={"Authorization": "Bearer " + token("admin1")}); print("2 revoke via the API:", r.status_code)
print("3 after revocation but BEFORE crl-sync (broker still has the old CRL), connects:", connects())
wrapper = W / "curl"; wrapper.write_text(f'#!/bin/sh\nexec curl --cacert {SP}/cloud/caddy-root.crt "$@"\n'); wrapper.chmod(0o755)
before = os.stat(pki / "ca.crl").st_ino
out = subprocess.run(["sh", "deploy/crl-sync.sh", "--url", base + "/enroll-api/crl", "--pki-dir", str(pki), "--container", "crlsync-broker"], capture_output=True, text=True, env={**os.environ, "CURL": str(wrapper)})
print("4 crl-sync.sh exit", out.returncode, "|", (out.stdout + out.stderr).strip()[:160])
print("5 the CRL file kept its inode (the single-file bind mount follows it):", os.stat(pki / "ca.crl").st_ino == before)
time.sleep(1.5)
print("6 after crl-sync, the revoked certificate connects:", connects())
subprocess.run(["docker", "kill", "crlsync-broker"], capture_output=True)
if os.environ.get("SHOWLOG"):
    pass
