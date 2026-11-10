"""Checks tools/run.sh and tools/demo.sh through their dry runs: each run starts what its label says, and no run can do the operations that would lose data."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUN = ROOT / "tools/run.sh"
FAULTS = "--faults drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3"


def run(*args, stdin=""):
    return subprocess.run(["sh", str(RUN), *args], cwd=ROOT, input=stdin, capture_output=True, text=True)


def dry(name):
    out = run("--dry-run", name)
    assert out.returncode == 0, out.stderr
    return out.stdout


def demo(**env):
    out = subprocess.run(["sh", str(ROOT / "tools/demo.sh")], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "DEMO_DRY_RUN": "1", **env})
    assert out.returncode == 0, out.stderr
    return dict(line.split("\t", 1) for line in out.stderr.splitlines())


def listed():
    return [line.split()[0] for line in run("--list").stdout.splitlines()]


def window(windows, prefix):
    return next(cmd for title, cmd in windows.items() if title.startswith(prefix))


def test_demo_defaults_are_the_plain_pipeline():
    w = demo()
    assert window(w, "3 GATEWAY") == "uv run python -m gateway.gateway"
    assert window(w, "4 MQTT") == "mosquitto_sub -h localhost -v -t 'ehg/+/pkt' | cut -c1-170"
    assert window(w, "6 VIEWER") == "uv run python viewer/server.py"
    assert window(w, "7 METRICS") == "uv run python -m backend.signal_metrics.worker --node EHG-93C0"


def test_demo_overrides_reach_their_windows():
    w = demo(GATEWAY_ARGS="--no-outbox", VIEWER_ENV="MQTT_TLS=1", MOSQ_SUB_ARGS="-h localhost -p 8883")
    assert window(w, "3 GATEWAY") == "uv run python -m gateway.gateway --no-outbox"
    assert window(w, "6 VIEWER") == "MQTT_TLS=1 uv run python viewer/server.py"
    assert "-p 8883" in window(w, "4 MQTT")


def test_default_run_is_plain_firmware_base_backend_with_the_outbox():
    out = dry("default")
    assert "Thesis A default" in out
    assert "+ firmware/build.sh flash\n" in out
    assert "+ docker compose -f backend/docker-compose.yml up -d\n" in out
    assert "GATEWAY_ARGS=''" in out and "--mqtt-tls" not in out and "--no-outbox" not in out


def test_measured_run_repeats_the_recorded_configuration():
    out = dry("measured")
    assert FAULTS in out and "GATEWAY_ARGS='--no-outbox'" in out


def test_every_runnable_run_has_commands_and_a_status():
    for name in listed():
        out = dry(name)
        first = out.splitlines()[0]
        assert re.match(rf"{name}: (Thesis A default|Tested on hardware|In development|Planned|Not runnable locally yet)\.", first), first
        if not re.search("Planned|Not runnable", first):
            assert "+ " in out, name


def test_only_the_recorded_runs_claim_hardware_testing():
    lines = run("--list").stdout.splitlines()
    assert {l.split()[0] for l in lines if "Tested on hardware" in l} == {"measured", "faults"}


def test_cloud_only_names_start_nothing():
    for name in ("cloud", "keycloak", "hapi-fhir", "observability", "oracle"):
        assert "+ " not in dry(name), name


def test_unknown_name_is_an_error_with_the_list():
    out = run("--dry-run", "nonsense")
    assert out.returncode == 2 and "Unknown run: nonsense" in out.stdout and "default" in out.stdout


def test_menu_enter_selects_default_and_a_number_selects_that_row():
    assert "default: Thesis A default" in run("--dry-run", stdin="\n").stdout
    assert "measured: Tested on hardware" in run("--dry-run", stdin="2\n").stdout


def test_tls_run_uses_certificates_on_every_client():
    out = dry("tls")
    assert "backend/docker-compose.cloud.yml up -d" in out and "--mqtt-tls" in out and "MQTT_TLS=1" in out and "-p 8883" in out


def test_leaving_nothing_behind_and_no_dangerous_operation_is_possible():
    text = RUN.read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "down -v" not in code and " down" not in code
    assert not re.search(r">\s*\S*backend/\.env", code) and "cp backend" not in code
    assert "open -a Docker" not in code and "killall" not in code
    assert not re.search(r"\bwest\b", code)
    assert ".run-state" in (ROOT / ".gitignore").read_text()


def test_end_of_input_is_never_a_yes_to_flashing():
    script = (
        f'sed -e "/^NAME=\\"\\"$/,\\$d" "{RUN}" > "$T"; . "$T"; '
        'if ask "Flash?" y </dev/null >/dev/null; then echo yes; else echo no; fi; '
        'if echo | ask "Flash?" y >/dev/null; then echo yes; else echo no; fi'
    )
    out = subprocess.run(["sh", "-c", f'T=$(mktemp); {script}; rm -f "$T"'], cwd=ROOT, capture_output=True, text=True)
    assert out.stdout.split() == ["no", "yes"], out.stdout + out.stderr


def test_dry_run_writes_no_state():
    before = (ROOT / ".run-state").read_text() if (ROOT / ".run-state").exists() else None
    dry("default")
    after = (ROOT / ".run-state").read_text() if (ROOT / ".run-state").exists() else None
    assert before == after


def test_readme_names_every_run():
    readme = (ROOT / "README.md").read_text()
    missing = [name for name in listed() if f"`{name}`" not in readme]
    assert not missing, missing


def test_readme_never_overwrites_an_existing_env():
    assert "[ -f backend/.env ] || cp backend/.env.example backend/.env" in (ROOT / "README.md").read_text()
