"""Checks the "cloud profile" transport (decision 0008): the broker config and ACL say what they must, the default stack is untouched,
tools/pki.sh makes certificates that do what the broker needs, and the clients refuse to fall back to plain MQTT.

Three groups. (1) text checks, always run. (2) PKI + a loopback TLS server, run when OpenSSL 3 is installed. (3) a real Mosquitto with
the repo's config and ACL, run only with EHG_INTEGRATION=1 and `mosquitto` on PATH (brew install mosquitto)."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from gateway import gateway

ROOT = Path(__file__).parents[1]
PKI_SH = ROOT / "tools/pki.sh"
TLS_CONF = ROOT / "backend/mosquitto/mosquitto.tls.conf"
ACL = ROOT / "backend/mosquitto/acl"
BASE_COMPOSE = ROOT / "backend/docker-compose.yml"
CLOUD_COMPOSE = ROOT / "backend/docker-compose.cloud.yml"
READERS = ("ehg-bridge", "ehg-ingest", "ehg-viewer")
UNBUFFERED = {**os.environ, "PYTHONUNBUFFERED": "1"}  # a piped, terminated gateway would otherwise lose its buffered output


def openssl3() -> bool:
    out = subprocess.run([os.environ.get("OPENSSL", "openssl"), "version"], capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.startswith("OpenSSL 3")


needs_openssl = pytest.mark.skipif(not openssl3(), reason="OpenSSL 3 not installed (brew install openssl@3)")
needs_broker = pytest.mark.skipif(
    not (os.environ.get("EHG_INTEGRATION") == "1" and shutil.which("mosquitto")),
    reason="set EHG_INTEGRATION=1 and install mosquitto to run the real-broker tests")


# ---------------------------------------------------------------- (1) text checks

def directives(path: Path) -> dict[str, list[str]]:
    """Mosquitto config: option -> every value given (comments and blank lines dropped)."""
    out: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition(" ")
            out.setdefault(key, []).append(value.strip())
    return out


def acl_stanzas() -> dict[str, dict[str, list[str]]]:
    """ACL file: user -> {"read": [topics], "write": [topics]}."""
    users: dict[str, dict[str, list[str]]] = {}
    user = None
    for line in ACL.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        if word == "user":
            user = users.setdefault(rest.strip(), {"read": [], "write": []})
        else:
            assert word == "topic" and user is not None, f"unexpected ACL line: {line}"
            mode, _, topic = rest.strip().partition(" ")
            assert mode in ("read", "write"), f"ACL mode must be read or write: {line}"
            user[mode].append(topic.strip())
    return users


def test_tls_conf_requires_tls13_and_client_certs():
    d = directives(TLS_CONF)
    assert d["tls_version"] == ["tlsv1.3"]
    assert d["require_certificate"] == ["true"]
    assert d["use_identity_as_username"] == ["true"]
    assert d["use_username_as_clientid"] == ["true"], "otherwise one identity can take over another's session"
    assert d["allow_anonymous"] == ["false"]
    assert d["listener"] == ["8883"], "the cloud profile must have one listener, and it must be TLS"
    for key in ("cafile", "certfile", "keyfile", "crlfile", "acl_file"):
        assert key in d, f"{key} missing"
    assert d["persistence"] == ["true"] and d["max_queued_messages"] == ["100000"]


def test_acl_is_least_privilege():
    users = acl_stanzas()
    assert set(users) == {"ehg-gw-01", *READERS}
    for user, grants in users.items():
        for topic in grants["read"] + grants["write"]:
            assert "#" not in topic, f"{user}: wildcard '#' grant {topic}"
    for reader in READERS:
        assert users[reader]["write"] == [], f"{reader} is a reader and must not publish"
    assert set(users["ehg-gw-01"]["write"]) == {"ehg/+/pkt", "ehg/_gw/ehg-gw-01/status"}
    assert users["ehg-gw-01"]["read"] == [], "a gateway has no reason to read"
    assert users["ehg-viewer"]["read"] == ["ehg/+/pkt"] and users["ehg-ingest"]["read"] == ["ehg/+/pkt"]


def load_compose(path: Path) -> dict:
    """Compose YAML; the !override / !reset tags are Compose extensions that plain YAML does not know."""
    class Loader(yaml.SafeLoader):
        pass
    for tag in ("!override", "!reset"):
        Loader.add_constructor(tag, lambda loader, node: loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
                               else loader.construct_scalar(node))
    return yaml.load(path.read_text(), Loader=Loader)


def test_default_compose_is_untouched():
    base = load_compose(BASE_COMPOSE)
    assert set(base["services"]) == {"mosquitto", "influxdb", "ingest", "grafana"}
    assert base["services"]["mosquitto"]["ports"] == ["127.0.0.1:1883:1883"]
    assert base["services"]["grafana"]["ports"] == ["127.0.0.1:3300:3000"]
    assert "mosquitto.tls.conf" not in BASE_COMPOSE.read_text()
    assert "MQTT_TLS" not in BASE_COMPOSE.read_text()


def test_cloud_override_publishes_only_8883():
    over = load_compose(CLOUD_COMPOSE)
    assert set(over["services"]) <= {"mosquitto", "ingest"}, "the override may only touch the broker and ingest"
    ports = over["services"]["mosquitto"]["ports"]
    assert ports and all(p.endswith(":8883:8883") for p in ports), ports
    assert "!override" in CLOUD_COMPOSE.read_text(), "without !override Compose would ADD to the base 1883 mapping"
    assert not any("1883" in p for p in ports)
    mounts = over["services"]["mosquitto"]["volumes"] + over["services"]["ingest"]["volumes"]
    assert all(m.endswith(":ro") for m in mounts), "certificates, config and ACL are mounted read-only"
    for m in mounts:
        source = m.split(":")[0]
        if "pki" in source:  # certificates: single files only; the pki directory also holds the CA private keys
            assert Path(source).suffix in (".crt", ".key", ".crl"), f"{m}: mount named files, never the pki directory"
            assert not re.search(r"intermediate|root|ca\.key", source), f"{m}: CA material must not reach a service container"
    env = over["services"]["ingest"]["environment"]
    assert env["MQTT_TLS"] == "1" and env["MQTT_PORT"] == "8883"


@pytest.mark.skipif(subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0
                    if shutil.which("docker") else True, reason="docker compose CLI not installed (the daemon is not needed)")
def test_compose_config_merge_matches_the_design():
    """Compose's own merge (reads files only, no daemon): the cloud profile replaces 1883 with 8883 and keeps the 4 services."""
    env = {**os.environ, **{k: "x" for k in ("INFLUX_ORG", "INFLUX_BUCKET", "INFLUX_TOKEN", "INFLUX_ADMIN_PASSWORD", "GRAFANA_ADMIN_PASSWORD")}}

    def config(*files: str) -> dict:
        cmd = ["docker", "compose", *[a for f in files for a in ("-f", f)], "config", "--format", "json"]
        r = subprocess.run(cmd, cwd=ROOT / "backend", capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    cloud = config("docker-compose.yml", "docker-compose.cloud.yml")
    assert set(cloud["services"]) == {"mosquitto", "influxdb", "ingest", "grafana"}
    mq = cloud["services"]["mosquitto"]
    assert [(p["target"], p["published"], p["host_ip"]) for p in mq["ports"]] == [(8883, "8883", "127.0.0.1")]
    targets = {v["target"]: v["source"] for v in mq["volumes"] if v["type"] == "bind"}
    assert targets["/mosquitto/config/mosquitto.conf"].endswith("mosquitto.tls.conf")
    assert targets["/mosquitto/config/acl"].endswith("mosquitto/acl")
    assert {t for t in targets if t.startswith("/mosquitto/pki/")} == {f"/mosquitto/pki/{n}" for n in ("ca-chain.crt", "broker.crt", "broker.key", "ca.crl")}
    for service in ("mosquitto", "ingest"):
        for v in cloud["services"][service]["volumes"]:
            if v["type"] == "bind" and "pki" in v["source"]:
                assert v["read_only"] and v["source"].rsplit(".", 1)[-1] in ("crt", "key", "crl"), f"{service}: {v['source']} must be one certificate file"
                assert not re.search(r"/(intermediate|root)/|ca\.key", v["source"])
    default = config("docker-compose.yml")
    assert [(p["target"], p["published"]) for p in default["services"]["mosquitto"]["ports"]] == [(1883, "1883")]


def test_keys_and_pki_are_gitignored():
    for path in ("backend/pki/ca-chain.crt", "backend/pki/clients/ehg-gw-01.key", "x/server.pem", "x/id.p12", "k.key"):
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, f"{path} is not gitignored"


def test_gateway_defaults_leave_tls_off():
    args = gateway.parse_args([])
    assert (args.mqtt_tls, args.mqtt_port, args.ca, args.cert, args.key) == (False, 1883, None, None, None)
    assert gateway.parse_args(["--mqtt-tls", "--ca", "a", "--cert", "b", "--key", "c"]).mqtt_port == 8883
    assert gateway.parse_args(["--mqtt-tls", "--ca", "a", "--cert", "b", "--key", "c", "--mqtt-port", "443"]).mqtt_port == 443


@pytest.mark.parametrize("argv", [["--mqtt-tls"], ["--mqtt-tls", "--ca", "a"], ["--ca", "a", "--cert", "b", "--key", "c"], ["--key", "c"]])
def test_gateway_refuses_half_a_tls_setup(argv):
    with pytest.raises(SystemExit):
        gateway.parse_args(argv)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=["ingest", "viewer"])
def mqtt_settings(request, monkeypatch):
    """The env-driven settings function of the ingest service and of the viewer (same code in both files)."""
    if request.param == "ingest":
        for k, v in (("INFLUX_BUCKET", "b"), ("INFLUX_URL", "http://localhost:1"), ("INFLUX_TOKEN", "t"), ("INFLUX_ORG", "o")):
            monkeypatch.setenv(k, v)
        return load_module("ehg_ingest_under_test", ROOT / "backend/ingest/ingest.py").mqtt_settings
    return load_module("ehg_viewer_under_test", ROOT / "viewer/server.py").mqtt_settings


def test_env_defaults_are_plain_local_broker(mqtt_settings):
    assert mqtt_settings({}) == ("localhost", 1883, None)
    assert mqtt_settings({"MQTT_HOST": "mosquitto"}) == ("mosquitto", 1883, None)
    assert mqtt_settings({"MQTT_PORT": "1884"}) == ("localhost", 1884, None)
    assert mqtt_settings({"MQTT_TLS": "0"}) == ("localhost", 1883, None)


@pytest.mark.parametrize("env", [
    {"MQTT_TLS": "1"},
    {"MQTT_TLS": "1", "MQTT_CA": "a", "MQTT_CERT": "b"},
    {"MQTT_CA": "a", "MQTT_CERT": "b", "MQTT_KEY": "c"},
    {"MQTT_KEY": "c"},
])
def test_env_refuses_half_a_tls_setup(mqtt_settings, env):
    with pytest.raises(SystemExit):
        mqtt_settings(env)


def test_pki_script_syntax_and_help():
    assert subprocess.run(["sh", "-n", str(PKI_SH)]).returncode == 0
    out = subprocess.run([str(PKI_SH), "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "PURPOSE:" in out.stdout and "BEFORE -> AFTER:" in out.stdout


# ---------------------------------------------------------------- (2) PKI and a loopback TLS server

def run_pki(out: Path, *args: str, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run([str(PKI_SH), *args, "--out", str(out)], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pki") / "pki"
    run_pki(out, "init", "--host", "broker.test")
    return out


def cert_text(path: Path) -> str:
    return subprocess.run(["openssl", "x509", "-in", str(path), "-noout", "-text"], capture_output=True, text=True).stdout


@needs_openssl
def test_pki_chain_extensions_and_key_modes(pki):
    for crt in [pki / "broker.crt", *(pki / "clients").glob("*.crt")]:
        r = subprocess.run(["openssl", "verify", "-CAfile", str(pki / "ca-chain.crt"), str(crt)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    gw = cert_text(pki / "clients/ehg-gw-01.crt")
    assert "TLS Web Client Authentication" in gw and "TLS Web Server Authentication" not in gw
    assert "CA:FALSE" in gw and "id-ecPublicKey" in gw and "prime256v1" in gw
    broker = cert_text(pki / "broker.crt")
    assert "TLS Web Server Authentication" in broker and "TLS Web Client Authentication" not in broker
    for san in ("DNS:broker.test", "DNS:localhost", "DNS:mosquitto", "IP Address:127.0.0.1"):
        assert san in broker, san
    for key in pki.rglob("*.key"):
        assert key.stat().st_mode & 0o777 == 0o600, f"{key.name} must be mode 600"
    assert "pathlen:0" in cert_text(pki / "intermediate/ca.crt")


@needs_openssl
def test_pki_init_never_overwrites_and_never_prints_keys(pki):
    key = pki / "intermediate/ca.key"
    before = hashlib.sha256(key.read_bytes()).hexdigest()
    r = run_pki(pki, "init", check=False)
    assert r.returncode != 0 and "never overwrites" in r.stderr
    assert hashlib.sha256(key.read_bytes()).hexdigest() == before
    first = subprocess.run([str(PKI_SH), "init", "--out", str(pki.parent / "second")], capture_output=True, text=True)
    assert "PRIVATE KEY" not in first.stdout + first.stderr


@needs_openssl
@pytest.mark.parametrize("name", ["bad name", "-x", "a/b", "", "x" * 70])
def test_pki_rejects_bad_client_names(pki, name):
    r = subprocess.run([str(PKI_SH), "client", name, "--out", str(pki)], capture_output=True, text=True)
    assert r.returncode != 0


def tls_server_context(pki: Path, check_crl: bool = False) -> ssl.SSLContext:
    """What the broker enforces: TLS 1.3 minimum, a client certificate signed by our CA, optionally the revocation list."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(pki / "broker.crt", pki / "broker.key")
    ctx.load_verify_locations(cafile=str(pki / "ca-chain.crt"))
    ctx.verify_mode = ssl.CERT_REQUIRED
    if check_crl:
        ctx.load_verify_locations(cafile=str(pki / "ca.crl"))
        ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    return ctx


def handshake(server_ctx: ssl.SSLContext, client_ctx: ssl.SSLContext, hostname: str = "localhost") -> tuple[dict, Exception | None]:
    """One TLS connection on loopback. Returns what the server saw ({'cn', 'version'} or {'error'}) and the client's error, if any."""
    seen: dict = {}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)

        def serve():
            try:
                conn, _ = listener.accept()
                with server_ctx.wrap_socket(conn, server_side=True) as s:
                    subject = dict(x[0] for x in s.getpeercert()["subject"])
                    seen.update(cn=subject["commonName"], version=s.version())
                    s.sendall(b"ok")
            except (ssl.SSLError, OSError) as e:
                seen["error"] = e

        t = threading.Thread(target=serve)
        t.start()
        client_error = None
        try:
            with socket.create_connection(("127.0.0.1", listener.getsockname()[1]), timeout=5) as raw:
                with client_ctx.wrap_socket(raw, server_hostname=hostname) as c:
                    assert c.recv(2) == b"ok"  # with TLS 1.3 a refused client certificate only shows up on the first read
        except (ssl.SSLError, OSError, AssertionError) as e:
            client_error = e
        t.join(10)
    return seen, client_error


def client_context(pki: Path, cn: str = "ehg-gw-01", **kw) -> ssl.SSLContext:
    return gateway.tls_context(str(pki / "ca-chain.crt"), str(pki / f"clients/{cn}.crt"), str(pki / f"clients/{cn}.key"))


@needs_openssl
def test_good_client_connects_over_tls13_and_server_sees_cn(pki):
    seen, err = handshake(tls_server_context(pki), client_context(pki))
    assert err is None and seen == {"cn": "ehg-gw-01", "version": "TLSv1.3"}


@needs_openssl
def test_client_without_certificate_is_refused(pki):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cafile=str(pki / "ca-chain.crt"))
    seen, err = handshake(tls_server_context(pki), ctx)
    assert "error" in seen and err is not None


@needs_openssl
def test_tls12_only_client_is_refused(pki):
    ctx = client_context(pki)
    ctx.minimum_version, ctx.maximum_version = ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2
    seen, err = handshake(tls_server_context(pki), ctx)
    assert "error" in seen and err is not None


@needs_openssl
def test_client_from_another_ca_is_refused(pki, tmp_path):
    other = tmp_path / "other"
    run_pki(other, "init", "--host", "broker.test")
    ctx = gateway.tls_context(str(pki / "ca-chain.crt"), str(other / "clients/ehg-gw-01.crt"), str(other / "clients/ehg-gw-01.key"))
    seen, err = handshake(tls_server_context(pki), ctx)
    assert "error" in seen and err is not None


@needs_openssl
def test_server_certificate_cannot_be_used_as_a_client_certificate(pki):
    ctx = gateway.tls_context(str(pki / "ca-chain.crt"), str(pki / "broker.crt"), str(pki / "broker.key"))
    seen, err = handshake(tls_server_context(pki), ctx)
    assert "error" in seen and err is not None, "the broker certificate has serverAuth only, so it must not authenticate a client"


@needs_openssl
def test_client_refuses_a_broker_whose_name_does_not_match(pki):
    seen, err = handshake(tls_server_context(pki), client_context(pki), hostname="not-the-broker.example")
    assert isinstance(err, ssl.SSLCertVerificationError)


@needs_openssl
def test_revoked_client_is_refused_once_the_crl_is_checked(tmp_path):
    p = tmp_path / "pki"
    run_pki(p, "init", "--host", "broker.test")
    assert handshake(tls_server_context(p, check_crl=True), client_context(p, "ehg-viewer"))[1] is None
    run_pki(p, "revoke", "ehg-viewer")
    seen, err = handshake(tls_server_context(p, check_crl=True), client_context(p, "ehg-viewer"))
    assert "error" in seen and err is not None
    assert handshake(tls_server_context(p, check_crl=True), client_context(p, "ehg-gw-01"))[1] is None, "other identities stay valid"


@needs_openssl
def test_expired_crl_locks_out_every_client_until_it_is_rewritten(tmp_path):
    """Why mosquitto.tls.conf warns about CRL expiry: with the CRL checked, a stale list refuses everyone, revoked or not."""
    p = tmp_path / "pki"
    run_pki(p, "init", "--host", "broker.test")
    ca = p / "intermediate"
    stale = subprocess.run(["openssl", "ca", "-config", "ca.cnf", "-gencrl", "-crl_lastupdate", "20200101000000Z",
                            "-crl_nextupdate", "20200102000000Z", "-out", str(p / "ca.crl")], cwd=ca, capture_output=True, text=True)
    assert stale.returncode == 0, stale.stderr
    seen, err = handshake(tls_server_context(p, check_crl=True), client_context(p, "ehg-gw-01"))
    assert "error" in seen and err is not None, "an expired CRL must not be silently ignored"
    run_pki(p, "crl")
    assert handshake(tls_server_context(p, check_crl=True), client_context(p, "ehg-gw-01"))[1] is None


@needs_openssl
def test_env_settings_build_a_tls13_context(mqtt_settings, pki):
    env = {"MQTT_TLS": "1", "MQTT_HOST": "broker.test", "MQTT_CA": str(pki / "ca-chain.crt"),
           "MQTT_CERT": str(pki / "clients/ehg-ingest.crt"), "MQTT_KEY": str(pki / "clients/ehg-ingest.key")}
    host, port, ctx = mqtt_settings(env)
    assert (host, port) == ("broker.test", 8883)
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3 and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


# ---------------------------------------------------------------- (3) a real Mosquitto with the repo's config and ACL

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker(tmp_path):
    """Mosquitto started with backend/mosquitto/mosquitto.tls.conf (only paths and the port rewritten) and the real ACL."""
    p = tmp_path / "pki"
    run_pki(p, "init", "--host", "localhost")
    port = free_port()
    conf = TLS_CONF.read_text()
    conf = conf.replace("/mosquitto/pki", str(p)).replace("/mosquitto/config/acl", str(ACL))
    conf = conf.replace("/mosquitto/data/", str(tmp_path / "data") + "/").replace("listener 8883", f"listener {port}")
    (tmp_path / "data").mkdir()
    (tmp_path / "m.conf").write_text(conf)
    proc = subprocess.Popen(["mosquitto", "-c", str(tmp_path / "m.conf")], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("mosquitto did not start: " + proc.stderr.read().decode())
    yield type("Broker", (), {"pki": p, "port": port, "proc": proc})
    proc.terminate()
    proc.wait(10)


def mqtt_client(b, cn: str):
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"test-{cn}-{time.time_ns()}")
    c.tls_set_context(client_context(b.pki, cn))
    return c


def collect(b, cn: str, topics: str | list[str], seconds: float, while_running) -> list[tuple[str, bytes]]:
    """Subscribe as `cn`, run `while_running()` once subscribed, return what arrived within `seconds`."""
    got: list[tuple[str, bytes]] = []
    subscribed = threading.Event()
    c = mqtt_client(b, cn)
    c.on_subscribe = lambda *a: subscribed.set()
    c.on_message = lambda cl, u, m: got.append((m.topic, m.payload))
    wanted = [(t, 1) for t in ([topics] if isinstance(topics, str) else topics)]
    c.on_connect = lambda cl, u, f, rc, p: cl.subscribe(wanted)
    c.connect("localhost", b.port)
    c.loop_start()
    assert subscribed.wait(5), f"{cn} could not subscribe to {topics}"
    while_running()
    time.sleep(seconds)
    c.loop_stop()
    c.disconnect()
    return got


def publish(b, cn: str, topic: str, payload: bytes):
    connected = threading.Event()
    c = mqtt_client(b, cn)
    c.on_connect = lambda *a: connected.set()
    c.connect("localhost", b.port)
    c.loop_start()
    assert connected.wait(5), f"{cn} could not connect"  # paho runs the TLS handshake in its thread and raises nothing itself
    info = c.publish(topic, payload, qos=1)
    info.wait_for_publish(5)
    assert info.is_published(), f"{cn}: no PUBACK for {topic}"
    c.loop_stop()
    c.disconnect()


def connack(b, ctx: ssl.SSLContext) -> bool:
    """True when the broker lets this TLS client carry an MQTT CONNECT and answers CONNACK; False when it drops or alerts."""
    try:
        with socket.create_connection(("127.0.0.1", b.port), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname="localhost") as s:
            s.sendall(bytes.fromhex("101000044d51545404020000000474657374"))  # MQTT 3.1.1 CONNECT, client id "test", clean session
            return s.recv(4)[:1] == b"\x20"
    except (ssl.SSLError, OSError):
        return False


@needs_openssl
@needs_broker
def test_broker_delivers_gateway_packet_to_authorised_reader(broker):
    got = collect(broker, "ehg-viewer", "ehg/+/pkt", 1, lambda: publish(broker, "ehg-gw-01", "ehg/EHG-1/pkt", b"hello"))
    assert got == [("ehg/EHG-1/pkt", b"hello")]


@needs_openssl
@needs_broker
def test_broker_drops_publishes_the_acl_does_not_allow(broker):
    def attempts():
        publish(broker, "ehg-viewer", "ehg/EHG-1/pkt", b"from-viewer")            # a reader cannot publish
        publish(broker, "ehg-gw-01", "ehg/_gw/ehg-gw-02/status", b"spoof")       # a gateway cannot write another gateway's status
        publish(broker, "ehg-gw-01", "ehg/_gw/ehg-gw-01/status", b"own")         # but can write its own
    # ehg-bridge is allowed to read exactly these two filters, so it sees everything that was let through
    got = collect(broker, "ehg-bridge", ["ehg/+/pkt", "ehg/_gw/+/status"], 1, attempts)
    assert got == [("ehg/_gw/ehg-gw-01/status", b"own")], f"only the permitted publish may arrive, got {got}"


@needs_openssl
@needs_broker
def test_one_identity_cannot_take_over_another_identitys_session(broker):
    """Without use_username_as_clientid, a reader that sends the ingest service's client id kicks ingest off and inherits its persistent session."""
    import paho.mqtt.client as mqtt
    api = mqtt.CallbackAPIVersion.VERSION2
    up, dropped = threading.Event(), threading.Event()
    victim = mqtt.Client(api, client_id="ehg-ingest", clean_session=False)
    victim.tls_set_context(client_context(broker.pki, "ehg-ingest"))
    victim.on_connect = lambda *a: up.set()
    victim.on_disconnect = lambda *a: dropped.set()
    victim.connect("localhost", broker.port)
    victim.loop_start()
    assert up.wait(5), "ingest identity could not connect"
    thief_up = threading.Event()
    thief = mqtt.Client(api, client_id="ehg-ingest")  # a different identity, claiming ingest's client id
    thief.tls_set_context(client_context(broker.pki, "ehg-viewer"))
    thief.on_connect = lambda *a: thief_up.set()
    thief.connect("localhost", broker.port)
    thief.loop_start()
    assert thief_up.wait(5), "the viewer identity should still be able to connect (under its own name)"
    time.sleep(1.5)
    assert not dropped.is_set(), "the viewer identity took over the ingest service's client id and session"
    for c in (thief, victim):
        c.loop_stop()
        c.disconnect()


@needs_openssl
@needs_broker
def test_real_broker_refuses_no_certificate_tls12_and_wrong_ca(broker, tmp_path):
    plain = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    plain.load_verify_locations(cafile=str(broker.pki / "ca-chain.crt"))
    assert connack(broker, client_context(broker.pki)), "control: a valid client must connect"
    assert not connack(broker, plain), "no client certificate"
    tls12 = client_context(broker.pki)
    tls12.minimum_version, tls12.maximum_version = ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2
    assert not connack(broker, tls12), "TLS 1.2 only"
    other = tmp_path / "other"
    run_pki(other, "init", "--host", "localhost")
    foreign = gateway.tls_context(str(broker.pki / "ca-chain.crt"), str(other / "clients/ehg-gw-01.crt"), str(other / "clients/ehg-gw-01.key"))
    assert not connack(broker, foreign), "certificate from another CA"


@needs_openssl
@needs_broker
def test_revoked_certificate_is_refused_after_the_broker_reloads(broker):
    """Observed on Mosquitto 2.1.2: the CRL is read at start-up and again on SIGHUP, not on its own (docs/security.md)."""
    assert connack(broker, client_context(broker.pki, "ehg-gw-01"))
    run_pki(broker.pki, "revoke", "ehg-gw-01")
    broker.proc.send_signal(signal.SIGHUP)
    time.sleep(1)
    assert not connack(broker, client_context(broker.pki, "ehg-gw-01")), "revoked identity must be refused after the reload"
    assert connack(broker, client_context(broker.pki, "ehg-viewer")), "other identities must still connect after the reload"


@needs_openssl
@needs_broker
def test_simulated_gateway_publishes_over_tls(broker):
    """gateway.py --simulate 1 --mqtt-tls end to end into the real broker, read back by the viewer identity."""
    p = broker.pki
    cmd = [sys.executable, "-m", "gateway.gateway", "--simulate", "1", "--mqtt", "localhost", "--mqtt-port", str(broker.port), "--mqtt-tls",
           "--ca", str(p / "ca-chain.crt"), "--cert", str(p / "clients/ehg-gw-01.crt"), "--key", str(p / "clients/ehg-gw-01.key"),
           "--sim-record", str(ROOT / "data/converted/tpehgdb/tpehg1007.npz")]
    if not (ROOT / "data/converted/tpehgdb/tpehg1007.npz").exists():
        pytest.skip("data/converted not present (README step 0)")
    holder: dict = {}

    def start():
        holder["p"] = subprocess.Popen(cmd, cwd=ROOT, env=UNBUFFERED, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    got = collect(broker, "ehg-viewer", "ehg/+/pkt", 4, start)
    holder["p"].terminate()
    out = holder["p"].communicate(timeout=10)[0]
    assert "connected over TLS" in out, out
    assert got, "no packet arrived through the TLS broker"
    msg = json.loads(got[0][1])
    assert got[0][0] == "ehg/SIM-0000/pkt" and msg["node_id"] == "SIM-0000" and re.fullmatch(r"\d+", str(msg["seq"]))
    assert len(msg["samples"]) == 10 and "t_gw_rx" in msg and "t_mqtt_pub" in msg


@needs_openssl
@needs_broker
def test_gateway_reports_a_refused_certificate_instead_of_staying_silent(broker, tmp_path):
    other = tmp_path / "other"
    run_pki(other, "init", "--host", "localhost")
    q = other / "clients/ehg-gw-01"
    cmd = [sys.executable, "-m", "gateway.gateway", "--simulate", "1", "--mqtt", "localhost", "--mqtt-port", str(broker.port), "--mqtt-tls",
           "--ca", str(broker.pki / "ca-chain.crt"), "--cert", f"{q}.crt", "--key", f"{q}.key"]
    if not (ROOT / "data/converted/tpehgdb/tpehg1007.npz").exists():
        pytest.skip("data/converted not present (README step 0)")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=UNBUFFERED, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(4)
    proc.terminate()
    out = proc.communicate(timeout=10)[0]
    assert "connection failed" in out or "refused" in out or "disconnected" in out, f"a refused TLS connection must be visible: {out!r}"
