"""Checks the deployment code in deploy/ (Unit 4) without any cloud account, Docker daemon or network: Terraform is well-formed and sized to the Always Free limits,
the cloud-init template renders to valid YAML in the right order, the compose file is pinned, hardened, budgeted and exposes only 80/443/8883, update.sh refuses
unsafe input and has a dry run, and no secret-looking text or state file can reach git.

Opt-in (EHG_INTEGRATION=1, needs network): `terraform init` + `terraform validate` against the real Oracle provider schema, and shellcheck."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import hcl2
import pytest
import yaml

ROOT = Path(__file__).parents[1]
DEPLOY = ROOT / "deploy"
TF = DEPLOY / "terraform"
COMPOSE = DEPLOY / "compose/docker-compose.cloud.yml"
CLOUD_INIT = DEPLOY / "cloud-init/user-data.yaml.tftpl"
UPDATE_SH = DEPLOY / "update.sh"
INTEGRATION = os.environ.get("EHG_INTEGRATION") == "1"

# Services that arrive with later units (their config does not exist yet). Their memory is reserved NOW so the finished stack is known to fit (MiB).
RESERVED_MIB = {"hapi (Unit 9)": 2300, "keycloak (Unit 7)": 1360, "step-ca (Unit 7)": 100, "bridge, ts-writer, triage, fhir-sync, enroll (Units 6-9)": 500}
BUDGET_MIB = int(9.5 * 1024)  # the whole-stack ceiling: ~9.5 GB of the 12 GB, leaving the OS, Docker and headroom
EXPECTED_SERVICES = {"caddy", "mosquitto", "nats", "postgres", "prometheus", "alertmanager", "loki", "grafana", "alloy", "node-exporter", "cadvisor", "postgres-exporter"}
SECRET_ENV = {"EHG_DOMAIN": "x.example", "POSTGRES_PASSWORD": "x", "EHG_DB_PASSWORD": "x", "HAPI_DB_PASSWORD": "x", "KEYCLOAK_DB_PASSWORD": "x",
              "EXPORTER_DB_PASSWORD": "x", "GRAFANA_ADMIN_PASSWORD": "x"}


def deploy_files() -> list[Path]:
    """Files git tracks or would track (not ignored) under deploy/."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "deploy"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines() if (ROOT / p).is_file()]


have_terraform = pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
have_docker_cli = pytest.mark.skipif(
    shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0,
    reason="docker compose CLI not installed (the daemon is not needed)")


# ---------------------------------------------------------------- Terraform

@have_terraform
def test_terraform_is_formatted():
    r = subprocess.run(["terraform", "fmt", "-check", "-recursive", "-diff", str(TF)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@have_terraform
@pytest.mark.skipif(not INTEGRATION, reason="terraform init downloads the Oracle provider (network): set EHG_INTEGRATION=1")
def test_terraform_validates_against_the_real_provider_schema(tmp_path):
    work = tmp_path / "deploy"
    shutil.copytree(DEPLOY, work, ignore=shutil.ignore_patterns(".terraform", "*.tfstate*"))  # validate a copy: init writes .terraform/ and a lock file
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    init = subprocess.run(["terraform", "init", "-backend=false", "-input=false", "-no-color"], cwd=work / "terraform", capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    v = subprocess.run(["terraform", "validate", "-no-color"], cwd=work / "terraform", capture_output=True, text=True, env=env)
    assert v.returncode == 0, v.stdout + v.stderr


def test_every_terraform_file_parses_and_the_provider_is_pinned_exactly():
    files = sorted(TF.rglob("*.tf"))
    assert len(files) == 16, "4 root files + 4 modules x (main, variables, outputs)"
    for f in files:
        with f.open() as fh:
            hcl2.load(fh)
    versions = (TF / "versions.tf").read_text()
    assert re.search(r'required_version\s*=\s*">=\s*1\.6"', versions)
    assert re.search(r'source\s*=\s*"oracle/oci"', versions) and re.search(r'version\s*=\s*"=\s*\d+\.\d+\.\d+"', versions), "the provider must be pinned to one exact version"


def test_terraform_fits_the_always_free_limits():
    compute = (TF / "modules/compute/main.tf").read_text()
    ocpus, mem = int(re.search(r"ocpus\s*=\s*(\d+)", compute).group(1)), int(re.search(r"memory_in_gbs\s*=\s*(\d+)", compute).group(1))
    boot, block = int(re.search(r"boot_volume_size_in_gbs\s*=\s*(\d+)", compute).group(1)), int(re.search(r"size_in_gbs\s*=\s*(\d+)", compute).group(1))
    assert (ocpus, mem) == (2, 12) and 'shape               = "VM.Standard.A1.Flex"' in compute
    assert 50 <= boot and boot + block <= 200, f"boot {boot} + block {block} GB must fit the 200 GB Always Free total (minimum boot 50 GB)"
    assert len(re.findall(r'resource "oci_core_instance"', compute)) == 1 and len(re.findall(r'resource "oci_core_volume"', compute)) == 1
    assert 'access_type    = "NoPublicAccess"' in (TF / "modules/storage/main.tf").read_text()
    for forbidden in ("oci_load_balancer", "oci_database_", "oci_core_vpn", "oci_containerengine", "oci_file_storage", "oci_core_public_ip"):
        assert forbidden not in "".join(f.read_text() for f in TF.rglob("*.tf")), f"{forbidden} may cost money or exceed Always Free"


def test_security_list_opens_only_ssh_from_admin_plus_80_443_8883():
    text = (TF / "modules/network/main.tf").read_text()
    blocks = re.findall(r"ingress_security_rules \{(.*?)\n  \}\n", text, re.S)
    rules = []
    for b in blocks:
        proto, source = re.search(r'protocol\s*=\s*"(\w+)"', b).group(1), re.search(r'source\s*=\s*(.+)', b).group(1).strip()
        port = re.search(r"min = (\d+)", b)
        rules.append((proto, source, int(port.group(1)) if port else None))
    assert ("6", "var.admin_cidr", 22) in rules, "SSH only from admin_cidr"
    tcp_open = sorted(p for proto, src, p in rules if proto == "6" and src == '"0.0.0.0/0"')
    assert tcp_open == [80, 443, 8883]
    assert [r for r in rules if r[0] not in ("6", "1")] == [], "only TCP and ICMP"
    assert sum(1 for r in rules if r[2] == 22) == 1
    assert 'protocol    = "all"' in text, "egress open"
    variables = (TF / "variables.tf").read_text()
    assert "0.0.0.0/0" in variables and "must not be 0.0.0.0/0" in variables, "admin_cidr must refuse the whole internet"


def test_no_credentials_or_state_in_the_terraform_directory():
    tf_text = "".join(f.read_text() for f in TF.rglob("*") if f.is_file() and ".terraform" not in f.parts)
    assert "ocid1." not in re.sub(r"ocid1\.[a-z]+\.oc1\.\.REPLACE_ME", "", tf_text), "a real-looking OCID is in the repository"
    assert not re.search(r"private_key|fingerprint\s*=|api_key", tf_text, re.I), "the API key must stay in ~/.oci/config"
    assert "BEGIN" not in tf_text


# ---------------------------------------------------------------- cloud-init

def render_cloud_init() -> str:
    values = {"admin_cidr": "203.0.113.7/32", "domain_name": "ehg-demo.duckdns.org", "repo_url": "https://example.org/r.git", "repo_tag": "v0.0.1"}
    text = CLOUD_INIT.read_text()
    unknown = set(re.findall(r"\$\{(\w+)\}", text)) - set(values)
    assert not unknown, f"template uses variables Terraform does not pass: {unknown}"
    return re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], text)


def test_cloud_init_renders_to_valid_cloud_config():
    rendered = render_cloud_init()
    assert rendered.startswith("#cloud-config\n")
    assert "${" not in rendered
    doc = yaml.safe_load(rendered)
    assert {"packages", "users", "write_files", "runcmd"} <= set(doc)
    assert {"git", "ufw", "unattended-upgrades", "age"} <= set(doc["packages"])
    assert "docker.io" not in doc["packages"], "Docker is installed by the first-boot script, after the data volume is mounted"
    ehg = next(u for u in doc["users"] if isinstance(u, dict) and u["name"] == "ehg")
    assert ehg["shell"] == "/usr/sbin/nologin" and ehg["lock_passwd"] is True
    files = {f["path"]: f for f in doc["write_files"]}
    assert {"/etc/ssh/sshd_config.d/99-ehg-hardening.conf", "/etc/docker/daemon.json", "/usr/local/sbin/ehg-first-boot.sh", "/etc/apt/apt.conf.d/52ehg-unattended-upgrades"} <= set(files)
    ssh = files["/etc/ssh/sshd_config.d/99-ehg-hardening.conf"]["content"]
    assert "PasswordAuthentication no" in ssh and "PermitRootLogin no" in ssh
    assert yaml.safe_load(files["/etc/docker/daemon.json"]["content"])["data-root"] == "/srv/ehg-data/docker"


def test_first_boot_script_order_and_firewall():
    doc = yaml.safe_load(render_cloud_init())
    script = next(f for f in doc["write_files"] if f["path"].endswith("ehg-first-boot.sh"))["content"]
    assert script.index("mount -a") < script.index("apt-get install -y docker.io"), "Docker must be installed only after the data volume is mounted"
    assert script.index("apt-get install -y docker.io") < script.index("ufw --force enable") < script.index("git clone")
    assert "exit 1" in script[: script.index("apt-get install")], "no data volume means no Docker (it would fill the boot disk)"
    allows = re.findall(r"ufw allow (?:from \S+ to any port )?(\d+)", script)
    assert sorted(allows) == ["22", "443", "80", "8883"]
    assert "ufw allow from 203.0.113.7/32 to any port 22 proto tcp" in script
    assert subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True).returncode == 0
    assert not re.search(r"age\.key|AGE-SECRET|PASSWORD=|BEGIN ", script), "no secret may be written by cloud-init"


def test_metadata_service_is_locked_down_twice():
    assert "are_legacy_imds_endpoints_disabled = true" in (TF / "modules/compute/main.tf").read_text()
    doc = yaml.safe_load(render_cloud_init())
    files = {f["path"]: f["content"] for f in doc["write_files"]}
    unit = files["/etc/systemd/system/ehg-block-imds.service"]
    assert "iptables -I DOCKER-USER -d 169.254.169.254 -j DROP" in unit and "After=docker.service" in unit
    script = files["/usr/local/sbin/ehg-first-boot.sh"]
    assert "enable --now ehg-block-imds.service" in script and script.index("apt-get install -y docker.io") < script.index("enable --now ehg-block-imds.service")


def test_container_logs_go_to_the_journal_and_disk_metrics_need_no_host_mount():
    doc = yaml.safe_load(render_cloud_init())
    files = {f["path"]: f["content"] for f in doc["write_files"]}
    assert yaml.safe_load(files["/etc/docker/daemon.json"])["log-driver"] == "journald"
    assert "Storage=persistent" in files["/etc/systemd/journald.conf.d/ehg.conf"]
    df = files["/usr/local/sbin/ehg-df-metrics.sh"]
    assert subprocess.run(["sh", "-n"], input=df, text=True, capture_output=True).returncode == 0
    assert "ehg_disk_avail_bytes" in df and "mv \"$tmp\" \"$out\"" in df, "written atomically"
    assert "ehg-df-metrics.sh" in files["/etc/cron.d/ehg-df-metrics"]
    alloy = (DEPLOY / "observability/config.alloy").read_text()
    assert re.search(r'source_labels\s*=\s*\["__journal_container_name"\]\s*\n\s*regex\s*=\s*"\.\+"\s*\n\s*action\s*=\s*"keep"', alloy), "host logs (sshd, sudo) must be dropped before they are sent"
    assert services()["alloy"]["cap_drop"] == ["ALL"] and services()["alloy"]["read_only"] is True
    assert "loki.source.journal" in alloy and not re.search(r"^[^/\n]*(/var/lib/docker|docker\.sock)", alloy, re.M), "Alloy reads the journal only"


# ---------------------------------------------------------------- update.sh and the Postgres init script

def run_update(*args: str, tmp: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "EHG_HOME": str(tmp or "/nonexistent-ehg-home")}
    return subprocess.run([str(UPDATE_SH), *args], capture_output=True, text=True, env=env)


GOOD_SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("script", [UPDATE_SH, DEPLOY / "postgres/10-init-databases.sh"], ids=lambda p: p.name)
def test_deploy_shell_scripts_are_valid_and_executable(script):
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    assert os.access(script, os.X_OK), f"{script.name} must be executable"
    assert script.read_text().startswith("#!/bin/sh")


@pytest.mark.skipif(not (INTEGRATION and shutil.which("shellcheck")), reason="shellcheck not installed or EHG_INTEGRATION unset")
def test_shellcheck_is_clean():
    for script in (UPDATE_SH, DEPLOY / "postgres/10-init-databases.sh", ROOT / "tools/pki.sh"):
        r = subprocess.run(["shellcheck", "-s", "sh", str(script)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout


def test_update_help_is_the_header():
    out = run_update("--help")
    assert out.returncode == 0 and "PURPOSE:" in out.stdout and "BEFORE -> AFTER:" in out.stdout


@pytest.mark.parametrize("args, message", [
    ([], "give --tag"),
    (["--tag", "v1"], "give --tag"),
    (["--tag", "v1", "--commit", "abc123"], "40-character"),
    (["--tag", "v1;rm -rf /", "--commit", GOOD_SHA], "may only contain"),
    (["--tag", "v1", "--commit", GOOD_SHA.upper()], "40-character"),
    (["--tag", "v1", "--commit", GOOD_SHA, "--timeout", "soon"], "whole number"),
    (["--bogus"], "unknown option"),
])
def test_update_refuses_unsafe_or_incomplete_input(args, message):
    r = run_update(*args)
    assert r.returncode != 0 and message in r.stderr


def test_update_dry_run_prints_every_step_and_changes_nothing(tmp_path):
    r = run_update("--dry-run", "--tag", "v0.2.0", "--commit", GOOD_SHA, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    steps = [l for l in r.stdout.splitlines() if l.startswith("DRY-RUN:")]
    text = "\n".join(steps)
    for expected in ("git fetch --depth 1 origin tag v0.2.0", f"refuse unless git rev-parse v0.2.0^{{commit}} = {GOOD_SHA}", f"git checkout --detach {GOOD_SHA}",
                     "sops --decrypt", "pull", "up -d --remove-orphans", "wait up to 300s", "nothing was changed"):
        assert expected in text, f"missing step: {expected}"
    assert list(tmp_path.iterdir()) == [], "a dry run must not create anything"


def test_update_services_limits_what_is_started_and_what_is_waited_for(tmp_path):
    r = run_update("--dry-run", "--bootstrap", "--services", "caddy mosquitto", tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "up -d --remove-orphans caddy mosquitto" in r.stdout and "wait up to 300s for caddy mosquitto services" in r.stdout
    plain = run_update("--dry-run", "--bootstrap", tmp=tmp_path)
    assert "up -d --remove-orphans\n" in plain.stdout + "\n" and "for all services" in plain.stdout


@pytest.mark.parametrize("services", ["caddy; rm -rf /", "Caddy", "a  b", "$(id)", "caddy,mosquitto"])
def test_update_refuses_an_unsafe_services_list(services):
    r = run_update("--bootstrap", "--services", services)
    assert r.returncode != 0 and "service names separated by single spaces" in r.stderr


def test_private_repository_is_cloned_over_ssh_with_a_deploy_key_and_strict_host_keys():
    doc = yaml.safe_load(render_cloud_init())
    files = {f["path"]: f["content"] for f in doc["write_files"]}
    git = files["/opt/ehg/.gitconfig"]
    assert "-i /opt/ehg/secrets/deploy_key" in git and "StrictHostKeyChecking=yes" in git and "IdentitiesOnly=yes" in git and "UserKnownHostsFile=/opt/ehg/etc/known_hosts" in git
    assert "StrictHostKeyChecking=no" not in CLOUD_INIT.read_text()
    assert 'repo_url            = "git@github.com:' in (TF / "terraform.tfvars.example").read_text(), "the example shows the SSH form for a private repository"


def test_update_over_ssh_runs_the_same_command_as_the_service_user():
    r = run_update("--dry-run", "--ssh", "ubuntu@ehg.example.org", "--tag", "v0.2.0", "--commit", GOOD_SHA, "--verify-signature", tmp=Path("/opt/ehg"))  # the ssh branch touches nothing locally
    assert r.returncode == 0
    assert f"ssh ubuntu@ehg.example.org sudo -u ehg /opt/ehg/repo/deploy/update.sh --timeout 300 --tag v0.2.0 --commit {GOOD_SHA} --verify-signature --dry-run" in r.stdout


def test_update_bootstrap_without_the_age_key_waits_instead_of_failing(tmp_path):
    (tmp_path / "state").mkdir()
    r = subprocess.run([str(UPDATE_SH), "--bootstrap"], capture_output=True, text=True, env={**os.environ, "EHG_HOME": str(tmp_path), "EHG_RUNTIME_ENV": str(tmp_path / "rt.env")})
    assert r.returncode == 0 and "age.key is not there yet" in r.stdout
    assert not (tmp_path / "rt.env").exists() and not (tmp_path / "state/update.lock").exists(), "lock released, nothing decrypted"


def test_update_never_decrypts_to_a_persistent_location_by_default():
    text = UPDATE_SH.read_text()
    assert 'RUNTIME_ENV="${EHG_RUNTIME_ENV:-/run/ehg/runtime.env}"' in text, "decrypted secrets belong in /run (memory)"
    assert "umask 077" in text


# ---------------------------------------------------------------- compose

def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def mib(value: str) -> int:
    m = re.fullmatch(r"(\d+)([mg])", value)
    assert m, f"mem_limit {value!r} must look like 64m or 2g"
    return int(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def test_compose_has_exactly_the_planned_services():
    assert set(services()) == EXPECTED_SERVICES


def test_every_image_is_pinned_by_version_and_digest_for_arm64_hosts():
    for name, svc in services().items():
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", svc["image"]), f"{name}: {svc['image']} must be repo:tag@sha256:digest"
        assert ":latest" not in svc["image"]


def test_only_80_443_8883_are_published_and_nothing_dangerous_is_mounted():
    published = {}
    for name, svc in services().items():
        for p in svc.get("ports", []):
            published[name] = published.get(name, []) + [str(p).split(":")[-2] if str(p).count(":") >= 1 else str(p)]
        assert svc.get("privileged") is not True and svc.get("network_mode") != "host" and svc.get("pid") != "host", name
        for v in svc.get("volumes", []):
            assert "docker.sock" not in str(v), f"{name}: the Docker socket is root-equivalent"
    assert published == {"caddy": ["80", "443"], "mosquitto": ["8883"]}
    mosquitto_mounts = " ".join(services()["mosquitto"]["volumes"])
    assert re.search(r"/pki\}?/ca-chain\.crt", mosquitto_mounts) and "/mosquitto/pki:" not in mosquitto_mounts, "mount single certificate files, never the PKI directory"
    assert not re.search(r"intermediate|root\.key|ca\.key", COMPOSE.read_text())


# The only host paths a container may see. NOT allowed, with the reason: "/" , "/run", "/var/run" and "/var/lib/docker" or Docker's data-root (they reach
# the Docker socket or every container's environment, which holds the database passwords), "/etc" and "/root" (credentials), "/srv/ehg-data" (all volumes).
ALLOWED_HOST_MOUNTS = {"/proc", "/sys", "/var/log/journal", "/etc/machine-id", "/var/lib/ehg-metrics"}


def test_no_host_mount_can_reach_the_docker_socket_or_other_containers_secrets():
    for name, svc in services().items():
        for v in svc.get("volumes", []):
            source = str(v).split(":")[0]
            if source.startswith("/"):
                assert source in ALLOWED_HOST_MOUNTS, f"{name} mounts {source}: only {sorted(ALLOWED_HOST_MOUNTS)} are allowed"
                assert str(v).endswith(":ro"), f"{name}: host path {source} must be read-only"
    text = COMPOSE.read_text()
    assert not re.search(r"^\s*-\s*(/|/run|/var/run|/var/lib/docker|/srv/ehg-data[^ ]*)\s*:", text, re.M)


def test_only_caddy_and_mosquitto_have_a_route_to_the_internet():
    doc = yaml.safe_load(COMPOSE.read_text())
    assert doc["networks"]["internal"]["internal"] is True, "the internal network must have no route in or out"
    assert not doc["networks"]["edge"].get("internal")
    for name, svc in doc["services"].items():
        assert svc["networks"] == (["edge", "internal"] if name in {"caddy", "mosquitto"} else ["internal"]), f"{name}: wrong networks {svc.get('networks')}"


def test_every_service_is_hardened_limited_and_health_checked_or_says_why_not():
    for name, svc in services().items():
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert svc["restart"] == "unless-stopped", name
        assert "mem_limit" in svc, f"{name}: every service needs a memory limit"
        has_check = "healthcheck" in svc
        reason = str(svc.get("labels", {}).get("ehg.healthcheck", ""))
        assert has_check or reason.startswith("none: "), f"{name}: add a healthcheck or the label ehg.healthcheck: 'none: <why>'"


def test_memory_budget_including_what_later_units_will_add():
    limits = {name: mib(svc["mem_limit"]) for name, svc in services().items()}
    defined, reserved = sum(limits.values()), sum(RESERVED_MIB.values())
    assert defined + reserved <= BUDGET_MIB, f"{defined} MiB defined + {reserved} MiB reserved = {defined + reserved} MiB; the ceiling is {BUDGET_MIB} MiB"
    assert limits["postgres"] == 2048 and limits["nats"] == 400
    nats = (DEPLOY / "nats/nats.conf").read_text()
    assert "max_memory_store: 256MB" in nats and "max_file_store:" in nats, "NATS defaults to 75% of system RAM unless capped"
    assert "GOMEMLIMIT: 384MiB" in COMPOSE.read_text()
    assert "-c\", \"shared_buffers=1GB\"" in COMPOSE.read_text()


@have_docker_cli
def test_compose_config_resolves_and_a_missing_secret_stops_it_by_name():
    base = ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"]
    ok = subprocess.run(base, capture_output=True, text=True, env={**os.environ, **SECRET_ENV})
    assert ok.returncode == 0, ok.stderr
    import json
    cfg = json.loads(ok.stdout)
    ports = {(n, p["published"]) for n, s in cfg["services"].items() for p in s.get("ports", [])}
    assert ports == {("caddy", "80"), ("caddy", "443"), ("mosquitto", "8883")}
    for forbidden in ("1883", "5432", "8086", "3300", "8780", "8080"):
        assert not any(p == forbidden for _, p in ports)
    env = {k: v for k, v in {**os.environ, **SECRET_ENV}.items() if k != "POSTGRES_PASSWORD"}
    bad = subprocess.run(base, capture_output=True, text=True, env=env)
    assert bad.returncode != 0 and "POSTGRES_PASSWORD" in bad.stderr


# ---------------------------------------------------------------- secrets and state never reach git

@pytest.mark.parametrize("path, ignored", [
    ("deploy/terraform/terraform.tfstate", True), ("deploy/terraform/terraform.tfstate.backup", True), ("deploy/terraform/terraform.tfvars", True),
    ("deploy/terraform/.terraform/providers/x", True), ("deploy/secrets/secrets.yaml", True), ("deploy/secrets/runtime.env", True),
    ("deploy/compose/.env", True), ("deploy/age.key", True), ("keys/ehg.agekey", True),
    ("deploy/terraform/terraform.tfvars.example", False), ("deploy/secrets/secrets.sops.yaml", False), ("deploy/secrets/secrets.example.yaml", False),
    ("deploy/secrets/README.md", False), ("deploy/.sops.yaml", False), ("deploy/terraform/backend.tf.example", False),
])
def test_gitignore_covers_state_and_secrets_but_not_the_safe_files(path, ignored):
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    assert (r.returncode == 0) == ignored, f"{path}: ignored={r.returncode == 0}, expected {ignored}"


def test_only_encrypted_or_example_files_live_in_deploy_secrets():
    names = sorted(p.name for p in deploy_files() if p.parent == DEPLOY / "secrets")
    for n in names:
        assert n.endswith((".sops.yaml", ".example.yaml")) or n == "README.md", f"deploy/secrets/{n} must not be committed"


SECRET_PATTERNS = {
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "age secret key": r"AGE-SECRET-KEY-1[A-Z0-9]+",
    "real OCID": r"ocid1\.[a-z]+\.oc1\.[a-z0-9-]*\.(?!REPLACE_ME)[a-z0-9]{20,}",
    "password assignment": r"\b\w*(?i:password|passwd|secret|token)\w*\s*[:=]\s*['\"]?(?!CHANGE-ME|\$\{|\$|REPLACE|<|\"?\s*$|[A-Z][A-Z0-9_]+\b)[A-Za-z0-9+/=_-]{8,}",
    "AWS-style access key": r"AKIA[0-9A-Z]{16}",
}


def secret_hits(text: str) -> list[str]:
    return [label for label, pat in SECRET_PATTERNS.items() if re.search(pat, text)]


@pytest.mark.parametrize("sample, label", [
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private key block"),
    ("AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ", "age secret key"),
    ("compartment_ocid = \"ocid1.compartment.oc1..aaaaaaaabbbbbbbbccccccccdddd\"", "real OCID"),
    ("POSTGRES_PASSWORD: hunter2hunter2", "password assignment"),
    ("db_password = \"s3cr3tValue99\"", "password assignment"),
    ("access_key = AKIAABCDEFGHIJKLMNOP", "AWS-style access key"),
])
def test_the_secret_scanner_catches_what_it_claims_to(sample, label):
    assert label in secret_hits(sample)


@pytest.mark.parametrize("safe", [
    "POSTGRES_PASSWORD: CHANGE-ME-superuser", "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?not set}", "secrets: POSTGRES_PASSWORD",
    "tenancy_ocid = \"ocid1.tenancy.oc1..REPLACE_ME\"", "password: $name", "token = REPLACE_ME",
])
def test_the_secret_scanner_ignores_placeholders_and_variable_names(safe):
    assert secret_hits(safe) == []


def test_no_secret_looking_text_anywhere_in_deploy():
    hits = []
    for f in deploy_files():
        for label in secret_hits(f.read_text(errors="ignore")):
            hits.append(f"{f.relative_to(ROOT)}: {label}")
    assert not hits, "\n".join(hits)


def test_sops_config_holds_a_public_key_or_a_placeholder_only():
    text = (DEPLOY / ".sops.yaml").read_text()
    assert "AGE-SECRET-KEY" not in text
    recipient = re.search(r"^\s*age:\s*(\S+)", text, re.M).group(1)
    assert recipient == "REPLACE_WITH_YOUR_AGE_PUBLIC_KEY" or re.fullmatch(r"age1[a-z0-9]{58}", recipient)
    example = yaml.safe_load((DEPLOY / "secrets/secrets.example.yaml").read_text())
    needed = set(re.findall(r"\$\{([A-Z_]+_PASSWORD)(?::\?|\})", COMPOSE.read_text())) | {"POSTGRES_PASSWORD"}
    assert needed <= set(example), f"secrets.example.yaml lacks {needed - set(example)}"
    assert all(str(v).startswith("CHANGE-ME") for v in example.values())
