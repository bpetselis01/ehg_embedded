"""The Thesis A default run on the real board, end to end, judged by eval/run_check.py.

Run (Terminal.app only: macOS gives Bluetooth to Terminal, and kills other processes that touch it):
    EHG_HIL=1 uv run pytest -m hardware -s
    EHG_HIL=1 EHG_HIL_SECONDS=60 uv run pytest -m hardware -s     (shorter run)

PURPOSE: Show that the default run still works on hardware after a change, with one command and a pass/fail answer.
INPUT: the DK on USB, flashed with the default firmware (tools/run.sh default asks to flash; this test never flashes),
  Docker Desktop already running, data/converted/tpehgdb/tpehg1007.npz.
OUTPUT: PASS, or the failing checks and the log folder gateway/logs/hil-<UTC time>/ (one log per process).
HOW: The commands are the ones tools/demo.sh opens windows for (DEMO_DRY_RUN=1), so this cannot drift from the presented run.
  Feeder first, until the board accepts CONFIG; then gateway, viewer and metrics, each in its own process group. After
  EHG_HIL_SECONDS (300) the feeder stops, the outbox drains, and the whole run is evaluated with no settle margin.
  Teardown: Ctrl-C to the gateway (it sends STOP, so the board is idle for the next run), then the rest.
  Never flashes, never starts or restarts Docker Desktop, never writes backend/.env, never deletes the outbox.
ROLE IN THESIS: The repeatable hardware check of the default run. GitHub CI skips it (no board); the parse of demo.sh
  below is tested offline there.
"""

import fcntl
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEV_ID = os.environ.get("DEV_ID", "1050208294")
NODE = "EHG-93C0"
SERIAL = "/dev/cu.usbmodem0010502082941"
RECORD = ROOT / "data/converted/tpehgdb/tpehg1007.npz"
OUTBOX = ROOT / "gateway/outbox.db"
KEEP = {"1": ("feeder", "tools/feed.py"), "3": ("gateway", "-m gateway.gateway"),
        "6": ("viewer", "viewer/server.py"), "7": ("metrics", "backend.signal_metrics.worker")}  # 2, 4, 5 only display


def parse_demo_windows(dry_run_stderr: str) -> dict[str, str]:
    """name -> command for the processes the default run needs, from DEMO_DRY_RUN=1 tools/demo.sh ("N TITLE: ...<TAB>command")."""
    out = {}
    for row in dry_run_stderr.splitlines():
        title, _, command = row.partition("\t")
        number = title.split(" ", 1)[0]
        if number in KEEP:
            name, marker = KEEP[number]
            if marker not in command:
                raise AssertionError(f"tools/demo.sh window {number} no longer runs {marker!r}: {command!r}; update KEEP")
            out[name] = command.strip()
    if set(out) != {n for n, _ in KEEP.values()}:
        raise AssertionError(f"tools/demo.sh windows changed: found {sorted(out)}, expected {sorted(n for n, _ in KEEP.values())}")
    return out


def demo_windows() -> dict[str, str]:
    out = subprocess.run(["sh", "tools/demo.sh"], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "DEMO_DRY_RUN": "1"})
    assert out.returncode == 0, out.stderr
    return parse_demo_windows(out.stderr)


def test_the_default_run_commands_still_parse_from_demo_sh():
    """Offline (runs in CI): the drift guard the hardware test relies on."""
    w = demo_windows()
    assert w["feeder"].startswith("uv run python tools/feed.py") and w["gateway"] == "uv run python -m gateway.gateway"


def test_a_changed_demo_window_is_caught():
    with pytest.raises(AssertionError, match="window 3 no longer runs"):
        parse_demo_windows("1 BOARD\tuv run python tools/feed.py x.npz\n3 GATEWAY\tuv run python gateway/other.py\n")


# ---------------------------------------------------------------- hardware

def require(ok: bool, message: str):
    if not ok:
        pytest.fail(message, pytrace=False)


def sh(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def outbox_free() -> bool:
    """Same probe as tools/run.sh check_outbox; the lock is released before the gateway starts."""
    with open(ROOT / "gateway/outbox.db.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
    return True


def http_ok(url: str, timeout: float = 3) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def wait_for(predicate, seconds: float, step: float = 0.5) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class Processes:
    """The run's processes, each in its own session so a signal reaches python and not only `uv`."""

    def __init__(self, log_dir: Path):
        self.log_dir, self.procs = log_dir, {}

    def start(self, name: str, command: str):
        log = open(self.log_dir / f"{name}.log", "w")
        self.procs[name] = subprocess.Popen(shlex.split(command), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                            env={**os.environ, "PYTHONUNBUFFERED": "1"})

    def log(self, name: str) -> str:
        return (self.log_dir / f"{name}.log").read_text(errors="replace")

    def alive(self, name: str) -> bool:
        return self.procs[name].poll() is None

    def stop(self, name: str, wait_s: float):
        p = self.procs.get(name)
        if p is None or p.poll() is not None:
            return
        try:
            os.killpg(p.pid, signal.SIGINT)
            p.wait(wait_s)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(5)
        except ProcessLookupError:
            pass

    def leftovers(self) -> list[str]:
        left = []
        for name, p in self.procs.items():
            try:
                os.killpg(p.pid, 0)
                left.append(name)
            except ProcessLookupError:
                pass
        return left


@pytest.mark.hardware
def test_default_run_on_the_board():
    sys.path.insert(0, str(ROOT / "eval"))
    import run_check as rc

    seconds = float(os.environ.get("EHG_HIL_SECONDS", "300"))
    require(DEV_ID in sh("nrfutil", "device", "list").stdout, f"The board ({DEV_ID}) is not connected. Plug in the DK and try again.")
    require(sh("docker", "info").returncode == 0, "Docker is not running. Open Docker Desktop, wait until it is ready, and try again.")
    require(RECORD.exists(), f"{RECORD.relative_to(ROOT)} is missing: see README step 0.")
    require(outbox_free(), "A gateway is already running and holds the outbox. Stop it with Ctrl-C, then try again.")
    require(not sh("lsof", SERIAL).stdout.strip(), f"{SERIAL} is held by another process (an nRF Terminal or a feeder?). Close it and try again.")
    waiting = rc.outbox_pending(OUTBOX) or 0
    require(waiting == 0, f"gateway/outbox.db holds {waiting} packets from an earlier run. Run tools/run.sh default once and answer the "
                          "delete question, or keep them and rerun when they are sent.")
    windows = demo_windows()
    up = sh("docker", "compose", "-f", "backend/docker-compose.yml", "up", "-d")
    require(up.returncode == 0, "docker compose up failed:\n" + up.stderr[-800:])
    require(wait_for(lambda: http_ok("http://localhost:8086/health"), 120), "InfluxDB did not answer on :8086 within 2 minutes.")

    log_dir = ROOT / "gateway/logs" / time.strftime("hil-%Y%m%dT%H%M%SZ", time.gmtime())
    log_dir.mkdir(parents=True)
    run = Processes(log_dir)
    t_start = time.time()
    try:
        run.start("feeder", windows["feeder"])
        require(wait_for(lambda: re.search(r"board \S+ ready", run.log("feeder")) or not run.alive("feeder"), 30)
                and run.alive("feeder"), "The board did not accept CONFIG. If the log says 'CONFIG ignored while running', press Button 1 "
                                         "(or reset the board) and rerun. Feeder log:\n" + run.log("feeder")[-800:])
        run.start("gateway", windows["gateway"])
        run.start("viewer", windows["viewer"])
        run.start("metrics", windows["metrics"])
        require(wait_for(lambda: f"[{NODE}] connected" in run.log("gateway") or not run.alive("gateway"), 60) and run.alive("gateway"),
                "The gateway did not connect within 60 s. Gateway log:\n" + run.log("gateway")[-800:])
        outbox_start = rc.outbox_pending(OUTBOX)
        deadline = time.time() + seconds
        while time.time() < deadline:
            dead = [n for n in ("feeder", "gateway", "viewer", "metrics") if not run.alive(n)]
            require(not dead, f"{', '.join(dead)} exited during the run; logs in {log_dir.relative_to(ROOT)}")
            time.sleep(2)

        run.stop("feeder", 10)
        newest = []

        def settled():
            acks = [p["t_db_ack"] for p in stored_pkts(time.time() - t_start)]
            newest.append(max(acks, default=0))
            return (rc.outbox_pending(OUTBOX) or 0) == 0 and len(newest) >= 3 and newest[-1] == newest[-3]
        wait_for(settled, 60, step=2)
        window = f"{int(time.time() - t_start) + 30}s"
        checks = rc.gather(NODE, window, "tpehgdb", OUTBOX, outbox_start, settle_s=0)
        checks = [c for c in checks if c.name != "arriving"]  # the feeder is stopped on purpose by now
        checks.append(rc.Check("viewer", http_ok("http://localhost:8765"), "http://localhost:8765"))
        checks.append(rc.Check("grafana", http_ok("http://localhost:3300/api/health"), "http://localhost:3300/api/health"))
    finally:
        run.stop("gateway", 15)
        for name in ("feeder", "viewer", "metrics"):
            run.stop(name, 10)
    print("\n" + rc.line(checks) + f"\nlogs: {log_dir.relative_to(ROOT)}")
    assert "sent STOP" in run.log("gateway"), "the gateway did not send STOP on Ctrl-C, so the board is still sampling"
    assert not run.leftovers(), f"processes still running after teardown: {run.leftovers()}"
    assert all(c.ok for c in checks), rc.line(checks)


def stored_pkts(elapsed_s: float) -> list[dict]:
    from backend_client import query_pkts
    try:
        return query_pkts(NODE, f"{int(elapsed_s) + 30}s")
    except SystemExit:
        return []
