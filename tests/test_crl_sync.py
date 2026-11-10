"""Checks deploy/crl-sync.sh with REAL OpenSSL revocation lists made by tools/pki.sh and a stub `docker`: a revoked certificate's list is verified and installed into the existing file (same inode, as a single-file bind mount needs),
the broker container is signalled only after a verified install, and a forged, expired, older, malformed or unreachable list changes nothing and sends no signal. NOT covered: a live Mosquitto container (the reload itself was tested in test_mqtt_tls_config)."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT, PKI_SH = ROOT / "deploy/crl-sync.sh", ROOT / "tools/pki.sh"


def openssl3() -> bool:
    out = subprocess.run([os.environ.get("OPENSSL", "openssl"), "version"], capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.startswith("OpenSSL 3")


pytestmark = pytest.mark.skipif(not openssl3(), reason="OpenSSL 3 not installed (brew install openssl@3)")


def pki(tmp_path, name="pki"):
    out = tmp_path / name
    subprocess.run(["sh", str(PKI_SH), "init", "--host", "localhost", "--out", str(out)], check=True, capture_output=True)
    return out


def pki_cmd(out, *args):
    subprocess.run(["sh", str(PKI_SH), *args, "--out", str(out)], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path):
    """A PKI, a 'VM' pki dir holding the first CRL, a served-CRL file and a stub docker that records its arguments."""
    ca = pki(tmp_path)
    vm = tmp_path / "vm"
    vm.mkdir()
    shutil.copy(ca / "ca-chain.crt", vm / "ca-chain.crt")
    shutil.copy(ca / "ca.crl", vm / "ca.crl")
    calls = tmp_path / "docker-calls"
    stub = tmp_path / "docker"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
    stub.chmod(0o755)
    served = tmp_path / "served.crl"
    e = type("E", (), {})()
    e.ca, e.vm, e.calls, e.stub, e.served = ca, vm, calls, stub, served
    shutil.copy(ca / "ca.crl", served)
    return e


def run(e, *extra, url=None):
    return subprocess.run(["sh", str(SCRIPT), "--url", url or f"file://{e.served}", "--pki-dir", str(e.vm), "--container", "ehg-mosquitto-1", *extra],
                          env={**os.environ, "DOCKER": str(e.stub)}, capture_output=True, text=True)


def signals(e):
    return e.calls.read_text().splitlines() if e.calls.exists() else []


def test_an_unchanged_list_does_nothing_and_signals_nothing(env):
    r = run(env)
    assert r.returncode == 0 and "unchanged" in r.stdout and signals(env) == []


def test_a_new_list_with_a_revocation_is_verified_installed_in_the_same_file_and_the_broker_is_signalled(env):
    pki_cmd(env.ca, "client", "ehg-gw-02", "--days", "7")
    pki_cmd(env.ca, "revoke", "ehg-gw-02")
    shutil.copy(env.ca / "ca.crl", env.served)
    inode = (env.vm / "ca.crl").stat().st_ino
    old = (env.vm / "ca.crl").read_text()
    r = run(env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (env.vm / "ca.crl").read_text() == env.served.read_text() != old
    assert (env.vm / "ca.crl").stat().st_ino == inode, "written in place: a single-file bind mount follows the inode, not the name"
    assert (env.vm / "ca.crl.prev").read_text() == old
    assert signals(env) == ["kill --signal=HUP ehg-mosquitto-1"]
    shown = subprocess.run(["openssl", "crl", "-in", str(env.vm / "ca.crl"), "-noout", "-text"], capture_output=True, text=True).stdout
    assert "Serial Number" in shown, "the installed list really lists the revoked certificate"


def test_a_list_signed_by_another_ca_is_refused(env, tmp_path):
    other = pki(tmp_path, "other")
    pki_cmd(other, "client", "ehg-gw-02", "--days", "7")
    pki_cmd(other, "revoke", "ehg-gw-02")
    shutil.copy(other / "ca.crl", env.served)
    before = (env.vm / "ca.crl").read_text()
    r = run(env)
    assert r.returncode == 1 and "signature does not verify" in r.stdout
    assert (env.vm / "ca.crl").read_text() == before and signals(env) == []


def test_an_older_list_cannot_replace_a_newer_one(env):
    old_copy = env.vm / "ca.crl"
    pki_cmd(env.ca, "client", "ehg-gw-02", "--days", "7")
    pki_cmd(env.ca, "revoke", "ehg-gw-02")
    shutil.copy(env.ca / "ca.crl", old_copy)  # the VM already has the NEWER list (number 2)
    r = run(env)  # the endpoint still serves the first list (number 1)
    assert r.returncode == 1 and "OLDER" in r.stdout and signals(env) == []


def test_malformed_or_unreachable_answers_change_nothing(env, tmp_path):
    before = (env.vm / "ca.crl").read_text()
    env.served.write_text("<html>login</html>")
    r = run(env)
    assert r.returncode == 1 and "not a PEM revocation list" in r.stdout
    gone = run(env, url=f"file://{tmp_path}/does-not-exist")
    assert gone.returncode == 1 and "could not fetch" in gone.stdout
    assert (env.vm / "ca.crl").read_text() == before and signals(env) == []


def test_an_expired_list_is_refused(env, tmp_path):
    ca = tmp_path / "pki-expired"
    subprocess.run(["sh", str(PKI_SH), "init", "--host", "localhost", "--out", str(ca)], check=True, capture_output=True)
    cnf = ca / "intermediate/ca.cnf"
    shutil.copy(ca / "ca-chain.crt", env.vm / "ca-chain.crt")
    shutil.copy(ca / "ca.crl", env.vm / "ca.crl")
    past = time.strftime("%Y%m%d%H%M%SZ", time.gmtime(time.time() - 3 * 86400))
    start = time.strftime("%Y%m%d%H%M%SZ", time.gmtime(time.time() - 10 * 86400))
    r = subprocess.run(["openssl", "ca", "-config", "ca.cnf", "-gencrl", "-crl_lastupdate", start, "-crl_nextupdate", past, "-out", str(env.served)], capture_output=True, text=True, cwd=cnf.parent)
    if r.returncode != 0:
        pytest.skip("this OpenSSL cannot date a CRL in the past: " + r.stderr[-120:])
    out = run(env)
    assert out.returncode == 1 and "expired" in out.stdout and signals(env) == []


def test_a_failing_signal_is_reported_after_a_verified_install(env):
    pki_cmd(env.ca, "client", "ehg-gw-02", "--days", "7")
    pki_cmd(env.ca, "revoke", "ehg-gw-02")
    shutil.copy(env.ca / "ca.crl", env.served)
    env.stub.write_text("#!/bin/sh\nexit 1\n")
    r = run(env)
    assert r.returncode == 1 and "could not signal" in r.stdout and (env.vm / "ca.crl").read_text() == env.served.read_text()


def test_dry_run_and_unsafe_arguments(env):
    assert "[dry-run]" in run(env, "--dry-run").stdout and signals(env) == []
    bad = subprocess.run(["sh", str(SCRIPT), "--container", "x; rm -rf /", "--url", "file:///x"], capture_output=True, text=True)
    assert bad.returncode == 2
    assert subprocess.run(["sh", str(SCRIPT), "--nope"], capture_output=True, text=True).returncode == 2
