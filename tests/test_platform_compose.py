"""Checks the platform image (migration job and FHIR sync worker): unprivileged, no secrets, everything its services import is in it. Also the ingest service's JetStream mode."""

import asyncio
import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCKERFILE = ROOT / "backend/platform.Dockerfile"


def test_platform_image_is_unprivileged_and_carries_no_secrets():
    text = DOCKERFILE.read_text()
    assert "USER 10001" in text and "FROM python:3.13-slim" in text
    assert re.search(r"^COPY backend /app/backend$", text, re.M) and not re.search(r"COPY .*(\.env|data|pki|\.\.)", text)


def load_ingest(monkeypatch):
    for k, v in (("INFLUX_BUCKET", "b"), ("INFLUX_URL", "http://localhost:1"), ("INFLUX_TOKEN", "t"), ("INFLUX_ORG", "o")):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("ingest_js", ROOT / "backend/ingest/ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ingest_reads_jetstream_only_when_asked_and_keeps_the_mqtt_default(monkeypatch):
    ing = load_ingest(monkeypatch)
    calls = []
    monkeypatch.setattr(asyncio, "run", lambda coro: (calls.append("jetstream"), coro.close()))
    monkeypatch.setenv("INGEST_SOURCE", "jetstream")
    ing.main()
    assert calls == ["jetstream"]
    assert ing.handle and ing.on_message, "the MQTT callback still exists and shares handle()"


def test_ingest_dockerfile_has_the_nats_client():
    assert "nats-py" in (ROOT / "backend/ingest/Dockerfile").read_text()


def test_a_poison_message_in_the_influx_writer_loop_is_terminated_and_a_transient_error_is_retried(monkeypatch):
    ing = load_ingest(monkeypatch)
    from types import SimpleNamespace
    calls = []

    class M:
        def __init__(self, data):
            self.data = data

        async def ack(self):
            calls.append((self.data, "ack"))

        async def term(self):
            calls.append((self.data, "term"))

        async def nak(self, delay=None):
            calls.append((self.data, ("nak", delay)))

    def fake_handle(payload):
        if payload == b"poison":
            raise KeyError("node_id")
        if payload == b"influx-down":
            raise ConnectionError("influx unreachable")
    monkeypatch.setattr(ing, "handle", fake_handle)
    asyncio.run(ing.consume([M(b"poison"), M(b"influx-down"), M(b"fine")]))
    assert calls == [(b"poison", "term"), (b"influx-down", ("nak", 5)), (b"fine", "ack")], "one message's failure must not stop the others"
    assert SimpleNamespace is not None


# ---------------------------------------------------------------- the image contains everything its services import

PIP_TO_IMPORT = {"psycopg": "psycopg", "alembic": "alembic", "sqlalchemy": "sqlalchemy", "numpy": "numpy", "httpx": "httpx"}
TRANSITIVE = {"starlette", "pydantic", "cryptography", "psycopg_binary", "mako", "anyio", "h11", "idna", "certifi", "httpcore", "click", "typing_extensions"}
# imports that sit inside the command-line main() of tools/fhir_export.py: the sync worker uses only its bundle() function, never main()
CLI_ONLY = {("tools/fhir_export.py", "backend_client"), ("tools/fhir_export.py", "tools.convert"),
            ("eval/backend_client.py", "influxdb_client")}  # the last one is the InfluxDB store; the worker asks for the timescale store only
SERVICE_MODULES = ["backend.db.migrate", "backend.fhir_sync.worker"]


def image_files():
    """Python files the Dockerfile copies (read from its COPY lines), as repo-relative paths."""
    out = set()
    for line in DOCKERFILE.read_text().splitlines():
        parts = line.split()
        if parts[:1] == ["COPY"]:
            srcs = parts[1:-1]
            for s in srcs:
                p = ROOT / s
                out |= {f.relative_to(ROOT).as_posix() for f in p.rglob("*.py")} if p.is_dir() else ({s} if p.is_file() else set())
    return out


def imports_of(path: Path):
    import ast
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}  # `from backend import bus` imports the submodule backend.bus
    return names


def test_every_module_the_services_import_is_in_the_image_or_installed_into_it():
    import sys
    pip = {m.group(1).split("[")[0].lower() for m in re.finditer(r'"([A-Za-z0-9_.\[\]-]+)>=', DOCKERFILE.read_text())}
    assert pip == set(PIP_TO_IMPORT), f"the Dockerfile's packages changed: update PIP_TO_IMPORT ({pip ^ set(PIP_TO_IMPORT)})"
    installed = set(PIP_TO_IMPORT.values()) | TRANSITIVE
    present = image_files()
    seen, todo = set(), [m.replace(".", "/") + ".py" for m in SERVICE_MODULES]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        assert f in present, f"{f} is imported by a service but the Dockerfile does not copy it"
        for name in imports_of(ROOT / f):
            top = name.split(".")[0]
            if (f, name) in CLI_ONLY or (f, top) in CLI_ONLY:
                continue
            if top in sys.stdlib_module_names:
                continue
            if top in {"backend", "gateway", "tools", "eval"}:
                if (ROOT / (name.replace(".", "/") + ".py")).exists():  # a module file; a bare package directory or an attribute (backend.bus.valid_id) is not one
                    todo.append(name.replace(".", "/") + ".py")
            else:
                assert top in installed, f"{f} imports {top}, which the image does not install"
    assert len(seen) >= 4, seen


def test_the_build_context_excludes_secrets_and_keys_but_not_what_the_image_copies():
    """The build context is the repository root; a .dockerignore keeps keys, secrets and local data out, and must not exclude anything the Dockerfile COPYs."""
    import fnmatch
    ignore = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines() if l.strip() and not l.startswith("#")]
    for must in (".git", "data", "**/*.key", "**/*.pem", "**/.env", "backend/pki", "deploy/secrets", ".venv"):
        assert must in ignore, f".dockerignore must list {must}"

    def ignored(path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.replace("**/", "")) or path == pat or path.startswith(pat.rstrip("/") + "/") for pat in ignore)
    for f in image_files():
        assert not ignored(f), f"{f} is copied by the Dockerfile but excluded by .dockerignore"
    for secret in ("backend/pki/clients/ehg-bridge.key", "deploy/secrets/secrets.sops.yaml", "backend/.env", "data/converted/tpehgdb/tpehg1007.npz", "ca/x.pem"):
        assert ignored(secret), f"{secret} must not reach the build context"

