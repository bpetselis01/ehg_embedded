"""deploy/fix-key-permissions.sh: private keys are readable by exactly the container user that needs them. chown and chmod are replaced by recorders, so the test checks WHICH commands run and in WHAT ORDER."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/fix-key-permissions.sh"


def run(tmp_path, with_stepca=True, drop=()):
    pki, step = tmp_path / "pki", tmp_path / "step-ca"
    pki.mkdir()
    for f in ("broker.key", "ehg-bridge.key", "ca-chain.crt", "broker.crt", "ehg-bridge.crt", "ca.crl"):
        if f not in drop:
            (pki / f).write_text("x")
    if with_stepca:
        (step / "enroll").mkdir(parents=True)
        (step / "enroll/provisioner.pem").write_text("x")
        (step / "enroll/root_ca.crt").write_text("x")
    log = tmp_path / "log"
    for name in ("rec_chown", "rec_chmod"):
        (tmp_path / name).write_text(f'#!/bin/sh\necho "{name[4:]} $*" >> "{log}"\n')
        (tmp_path / name).chmod(0o755)
    env = {**os.environ, "CHOWN": str(tmp_path / "rec_chown"), "CHMOD": str(tmp_path / "rec_chmod")}
    r = subprocess.run(["sh", str(SCRIPT), "--pki", str(pki), "--step-ca", str(step)], capture_output=True, text=True, env=env)
    return r, (log.read_text().splitlines() if log.exists() else []), pki, step


def test_each_private_key_goes_to_exactly_its_consumer(tmp_path):
    r, log, pki, step = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"chown ehg:1883 {pki}/broker.key" in log and f"chmod 640 {pki}/broker.key" in log, "Mosquitto's gid"
    assert f"chown ehg:10001 {pki}/ehg-bridge.key" in log and f"chmod 640 {pki}/ehg-bridge.key" in log, "the Python image's uid"
    assert f"chown ehg:10001 {step}/enroll/provisioner.pem" in log and f"chmod 640 {step}/enroll/provisioner.pem" in log
    assert not any("chmod 644" in c and ".key" in c for c in log) and not any("chmod 6" in c and "644" in c and "provisioner" in c for c in log), "no private key is ever made world-readable"


def test_the_enrolment_key_is_set_after_the_recursive_chown(tmp_path):
    _, log, _, step = run(tmp_path)
    recursive = next(i for i, c in enumerate(log) if c.startswith("chown -R 1000:1000"))
    key = next(i for i, c in enumerate(log) if f"{step}/enroll/provisioner.pem" in c and c.startswith("chown"))
    assert recursive < key, "otherwise the recursive chown would hand the key back to uid 1000 and the enrolment API could not read it"


def test_certificates_stay_public_and_owned_by_the_deploy_user(tmp_path):
    _, log, pki, _ = run(tmp_path)
    for f in ("ca-chain.crt", "broker.crt", "ehg-bridge.crt", "ca.crl"):
        assert f"chown ehg:ehg {pki}/{f}" in log and f"chmod 644 {pki}/{f}" in log


def test_a_missing_required_key_fails_loudly_and_a_missing_ca_folder_is_only_a_note(tmp_path):
    r, _, _, _ = run(tmp_path, with_stepca=False, drop=("ehg-bridge.key",))
    assert r.returncode == 1 and "MISSING (required)" in r.stdout and "ehg-bridge.key" in r.stdout
    assert "profile enrol not in use" in r.stdout


def test_it_refuses_to_run_without_root_unless_chown_is_replaced(tmp_path):
    r = subprocess.run(["sh", str(SCRIPT), "--pki", str(tmp_path), "--step-ca", str(tmp_path)], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k not in ("CHOWN", "CHMOD")})
    assert (r.returncode == 2 and "sudo" in r.stdout) or os.getuid() == 0
