"""Checks tools/gen_acl.py: the ACL it writes is least privilege per gateway, refuses ids that could widen a grant, and rewrites the file in place only when it changed.
The real-broker proof (a gateway cannot publish for another gateway's node) is in tests/test_mqtt_tls_config.py (needs EHG_INTEGRATION=1 and mosquitto)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import gen_acl

ROOT = Path(__file__).parents[1]


def stanzas(text: str) -> dict[str, list[str]]:
    users, user = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        if word == "user":
            user = rest
            users.setdefault(rest, [])
        else:
            users[user].append(rest)
    return users


def test_each_gateway_gets_only_its_nodes_and_its_own_status():
    u = stanzas(gen_acl.render({"ehg-gw-02": ["EHG-2"], "ehg-gw-01": ["EHG-9", "EHG-1", "EHG-1"]}))
    assert u["ehg-gw-01"] == ["write ehg/EHG-1/pkt", "write ehg/EHG-9/pkt", "write ehg/_gw/ehg-gw-01/status"]  # sorted, de-duplicated
    assert u["ehg-gw-02"] == ["write ehg/EHG-2/pkt", "write ehg/_gw/ehg-gw-02/status"]
    assert all("+" not in t and "#" not in t for g in ("ehg-gw-01", "ehg-gw-02") for t in u[g])


def test_service_identities_match_the_hand_written_acl():
    hand = stanzas((ROOT / "backend/mosquitto/acl").read_text())
    gen = stanzas(gen_acl.render({}))
    for svc in ("ehg-bridge", "ehg-ingest", "ehg-viewer"):
        assert gen[svc] == hand[svc]


def test_a_gateway_without_nodes_can_only_write_its_status():
    assert stanzas(gen_acl.render({"ehg-gw-03": []}))["ehg-gw-03"] == ["write ehg/_gw/ehg-gw-03/status"]


@pytest.mark.parametrize("bad", ["EHG-+", "EHG/1", "#", "a b", "", "x" * 65, "EHG.1"])
def test_ids_that_could_widen_a_grant_are_refused(bad):
    with pytest.raises(ValueError):
        gen_acl.render({"ehg-gw-01": [bad]})
    with pytest.raises(ValueError):
        gen_acl.render({bad: ["EHG-1"]})


def test_a_gateway_cannot_be_named_like_a_service():
    with pytest.raises(ValueError):
        gen_acl.render({"ehg-bridge": ["EHG-1"]})


def test_nodes_must_be_a_list_not_a_string():
    with pytest.raises(ValueError):
        gen_acl.render({"ehg-gw-01": "EHG-1"})  # a string would be iterated per character


def test_the_file_is_rewritten_in_place_and_only_on_change(tmp_path):
    out = tmp_path / "acl"
    out.write_text("old")
    inode = os.stat(out).st_ino
    assert gen_acl.write_in_place(out, "new") is True
    assert os.stat(out).st_ino == inode, "a single-file bind mount would keep reading the old file"
    mtime = os.stat(out).st_mtime_ns
    assert gen_acl.write_in_place(out, "new") is False and os.stat(out).st_mtime_ns == mtime


def test_cli_writes_the_file_and_a_bad_id_writes_nothing(tmp_path):
    good, bad, out = tmp_path / "g.json", tmp_path / "b.json", tmp_path / "acl"
    good.write_text(json.dumps({"ehg-gw-01": ["EHG-1"]}))
    bad.write_text(json.dumps({"ehg-gw-01": ["EHG-#"]}))
    run = lambda src: subprocess.run([sys.executable, str(ROOT / "tools/gen_acl.py"), "--gateways", str(src), "--out", str(out)], capture_output=True, text=True)
    r = run(good)
    assert r.returncode == 0 and r.stdout.strip() == "changed" and "topic write ehg/EHG-1/pkt" in out.read_text()
    assert run(good).stdout.strip() == "unchanged"
    before = out.read_text()
    r = run(bad)
    assert r.returncode == 1 and "plain ids" in r.stderr and out.read_text() == before
    out.unlink()
    assert run(bad).returncode == 1 and not out.exists()


def test_the_example_gateways_file_is_valid():
    data = json.loads((ROOT / "deploy/acl/gateways.json").read_text())
    gen_acl.render(data)
