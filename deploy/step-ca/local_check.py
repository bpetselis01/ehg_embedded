"""Calls a real step-ca the way the enrolment API does (see local-check.sh): sign, vet the answer, use it at a real broker, revoke, fetch and verify the CRL, and see the broker refuse the revoked certificate.

INPUT: argv[1] = folder with prov.pem (the provisioner's EC key), root_ca.crt, intermediate_ca.crt, fp (the root fingerprint).
  The broker hop needs `mosquitto` and OpenSSL 3 on PATH (brew install mosquitto openssl@3); without them it is skipped and says so.
OUTPUT: lines "ok: ..." and exit 0, or "FAIL: ..." and exit 1.
HOW (broker hop): a real Mosquitto with backend/mosquitto/mosquitto.tls.conf; its cafile is THIS CA's chain (so it verifies gateway certificates issued by step-ca), its own server certificate comes from tools/pki.sh (step-ca's template issues
  client-auth certificates only), the ACL comes from tools/gen_acl.py, and the CRL is the CA's own, installed in place followed by SIGHUP, as deploy/crl-sync.sh does.
"""

import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from cryptography import x509

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))
from backend.enroll.cli import make_key_and_csr  # noqa: E402
from backend.enroll.csr import check_issued, validate_csr  # noqa: E402
from backend.enroll.stepca_client import StepCaClient  # noqa: E402
from gateway import gateway  # noqa: E402
from tools import gen_acl  # noqa: E402

GATEWAY_ID = "ehg-gw-check"


class Broker:
    """A running Mosquitto and the TLS context a gateway would use against it."""

    def __init__(self, proc, port, ctx, crl_path):
        self.proc, self.port, self.ctx, self.crl_path = proc, port, ctx, crl_path

    def connects(self) -> bool:
        import paho.mqtt.client as mqtt
        up = threading.Event()
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"check-{time.time_ns()}")
        c.tls_set_context(self.ctx)
        c.on_connect = lambda cl, u, f, rc, p: up.set() if not rc.is_failure else None
        try:
            c.connect("localhost", self.port)
        except (OSError, ssl.SSLError):
            return False
        c.loop_start()
        ok = up.wait(4)
        c.loop_stop()
        c.disconnect()
        return ok

    def install_crl(self, pem: bytes):
        with open(self.crl_path, "wb") as f:  # the SAME file, as crl-sync.sh does (a single-file bind mount follows the inode)
            f.write(pem)
        self.proc.send_signal(signal.SIGHUP)
        time.sleep(1.0)

    def stop(self):
        self.proc.terminate()
        self.proc.wait(10)


def openssl3() -> bool:
    out = subprocess.run(["openssl", "version"], capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.startswith("OpenSSL 3")


def start_broker(work: Path, chain: Path, initial_crl: bytes, leaf_pem: str, key_pem: bytes) -> Broker:
    pki = work / "broker_pki"
    server = work / "server_pki"
    r = subprocess.run([str(ROOT / "tools/pki.sh"), "init", "--host", "localhost", "--out", str(server)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("FAIL: tools/pki.sh init: " + r.stderr)
    pki.mkdir()
    shutil.copy(chain, pki / "ca-chain.crt")                    # who may CONNECT: the step-ca chain
    shutil.copy(server / "broker.crt", pki / "broker.crt")      # who the broker IS: the offline pki.sh CA
    shutil.copy(server / "broker.key", pki / "broker.key")
    (pki / "ca.crl").write_bytes(initial_crl)
    acl = work / "acl"
    acl.write_text(gen_acl.render({GATEWAY_ID: ["EHG-1"]}))
    with socket_free_port() as port:
        pass
    conf = (ROOT / "backend/mosquitto/mosquitto.tls.conf").read_text().replace("/mosquitto/pki", str(pki)).replace("/mosquitto/config/acl", str(acl))
    (work / "data").mkdir()
    conf = conf.replace("/mosquitto/data/", str(work / "data") + "/").replace("listener 8883", f"listener {port}")
    (work / "m.conf").write_text(conf)
    proc = subprocess.Popen(["mosquitto", "-c", str(work / "m.conf")], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            __import__("socket").create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        sys.exit("FAIL: mosquitto did not start: " + proc.stderr.read().decode())
    (work / "leaf.crt").write_text(leaf_pem); (work.parent / "leaf.crt").write_text(leaf_pem)
    (work / "leaf.key").write_bytes(key_pem)
    (work / "leaf.key").chmod(0o600)
    ctx = gateway.tls_context(str(server / "ca-chain.crt"), str(work / "leaf.crt"), str(work / "leaf.key"))
    return Broker(proc, port, ctx, pki / "ca.crl")


class socket_free_port:
    def __enter__(self):
        import socket
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        return self.s.getsockname()[1]

    def __exit__(self, *a):
        self.s.close()


work = Path(sys.argv[1])
url = "https://localhost:9000"
ca = StepCaClient(url, "ehg-enroll", (work / "prov.pem").read_text(), (work / "fp").read_text().strip(), verify=str(work / "root_ca.crt"))
key_pem, csr = make_key_and_csr(GATEWAY_ID)
signed = ca.sign(csr, GATEWAY_ID)
cert = check_issued(signed["certificate"], validate_csr(csr), GATEWAY_ID)  # raises CsrError on a wrong name, usage or lifetime
print(f"ok: signed, CN={GATEWAY_ID}, client-auth only, valid until {cert.not_valid_after_utc:%Y-%m-%d}")
http = httpx.Client(verify=str(work / "root_ca.crt"))
chain = work / "ca-chain.crt"
chain.write_bytes((work / "intermediate_ca.crt").read_bytes() + (work / "root_ca.crt").read_bytes())
broker = None
if shutil.which("mosquitto") and shutil.which("openssl") and openssl3():
    broker = start_broker(Path(tempfile.mkdtemp(dir=work)), chain, http.get(url + "/crl?pem=true").content, signed["certificate"], key_pem)
    if not broker.connects():
        broker.proc.terminate()
        err = broker.proc.communicate(timeout=10)[1].decode()[-300:]
        err += "\nopenssl verify says: " + subprocess.run(["openssl", "verify", "-crl_check", "-CAfile", str(chain), "-CRLfile", str(broker.crl_path), str(chain.parent / "leaf.crt")], capture_output=True, text=True).stdout[-300:]
        err += subprocess.run(["openssl", "crl", "-in", str(broker.crl_path), "-noout", "-text"], capture_output=True, text=True).stdout[:500]
        sys.exit("FAIL: the broker refused a valid certificate from this CA. Broker log: " + err)
    print("ok: the real broker accepts the certificate (its cafile is this CA's chain)")
else:
    print("skipped: the broker hop (needs mosquitto and OpenSSL 3 on PATH)")
ca.revoke(str(cert.serial_number))
print("ok: revoked")
pem = http.get(url + "/crl?pem=true").content
crl = x509.load_pem_x509_crl(pem)
if crl.get_revoked_certificate_by_serial_number(cert.serial_number) is None:
    sys.exit("FAIL: the revoked serial is not in the CRL")
(work / "crl.pem").write_bytes(pem)
out = subprocess.run(["openssl", "crl", "-in", str(work / "crl.pem"), "-CAfile", str(chain), "-noout", "-verify"], capture_output=True, text=True)
if "verify OK" not in out.stdout + out.stderr:  # openssl exits 0 even when the check fails
    sys.exit("FAIL: openssl did not verify the CRL: " + out.stderr)
print(f"ok: the CRL lists the serial and verifies; next update {crl.next_update_utc:%Y-%m-%d %H:%M}")
if broker is not None:
    broker.install_crl(pem)
    refused = not broker.connects()
    broker.stop()
    if not refused:
        sys.exit("FAIL: the broker still accepts the revoked certificate after the CRL was installed and SIGHUP sent")
    print("ok: with the CA's CRL installed and the broker reloaded, the revoked certificate is refused")
