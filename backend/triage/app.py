"""The triage service's HTTP API (FastAPI): worklist, node detail and trends, the alert workflow, registry, audit, configuration, health probes, metrics.

PURPOSE: What the triage page and operators talk to (decision 0011). Operational health of the telemetry pipeline only: no clinical statement is made anywhere in a response.
INPUT: HTTP requests with `Authorization: Bearer <token>` (backend/jwtauth.py). Wired by create_app(store, audit, auth, registry, ...).
  Routes and the minimum role: GET /api/session, /api/worklist, /api/nodes/{id}, /api/nodes/{id}/trend?range=24h|7d|30d|90d, /api/alerts?state=, /api/alerts/{id} (viewer);
  POST /api/alerts/{id}/ack|escalate|resolve|note with {"note": "..."} (operator); GET /api/registry/gateways|nodes|subjects, POST /api/registry/gateways/{id}/revoke,
  GET /api/audit?limit=, GET /api/audit/verify, GET|PUT /api/config (admin); GET /healthz, /readyz, /metrics (no login; /metrics is for the internal network and refuses proxied requests).
OUTPUT: JSON. Errors have a name and a status: 401 no/invalid token, 403 role too low or no active consent for the node's subject, 404 unknown id, 409 illegal transition, 422 bad input
  (missing note, bad range, bad threshold), 503 database unavailable. A server error returns a plain message, never a stack trace. No response contains a secret.
  e.g. POST /api/alerts/17/escalate {"note": ""} -> 422 {"error": "escalate needs a note explaining why"}
HOW: Every alert DETAIL read, transition, note, registry change, audit read/verify and configuration change writes an audit row (backend/triage/audit.py). Headers other than Authorization are never trusted.
  Anything that touches a node whose registered subject has no active consent is refused (403, and the refusal is audited): node detail, trends, alert details, workflow actions; such nodes and their alerts are left out of the worklist and the alert list. /readyz is 503 when the database or the bus consumer is unhealthy; /healthz only says the process is up.
ROLE IN THESIS: The operator-facing interface of the cloud core (docs/production-topology.md); the OpenAPI document is exported from this app in Unit 17.
BEFORE -> AFTER: Before: no operator interface beyond Grafana. After: a role-protected, audited API.
"""

from datetime import datetime, timezone

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict

from backend.jwtauth import AuthError
from backend.triage.registry import RANGES, UnknownGateway
from backend.triage.rules import Config
from backend.triage.state import IllegalTransition, NoteRequired, NotFound

PURPOSE = "Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims."
STATES = ("open", "acknowledged", "escalated", "resolved")
CONFIG_LIMITS = {"offline_min_s": (1, 3600), "offline_intervals": (1, 100), "loss_rate": (0.0001, 1), "loss_rate_min_packets": (1, 100000), "latency_warn_ms": (1, 600000),
                 "latency_crit_ms": (1, 600000), "saturation_pct": (0.1, 100)}


class NoteBody(BaseModel):
    """Body of the workflow actions."""

    model_config = ConfigDict(extra="forbid")
    note: str | None = None


def create_app(*, store, audit, auth, registry, known_nodes=lambda: [], ready=lambda: (True, ""), config: Config | None = None, on_revoke=None, clock=lambda: datetime.now(timezone.utc)) -> FastAPI:
    """Build the API around its collaborators (all injectable, so the tests need no network)."""
    app = FastAPI(title="EHG triage API", version="0.1.0", description=PURPOSE)
    cfg = config or Config()
    metrics = CollectorRegistry()
    alerts_gauge = Gauge("ehg_triage_alerts", "Alerts by state", ["state"], registry=metrics)

    def who(min_role: str):
        """Dependency: authenticate, then require at least `min_role`."""
        def dep(authorization: str | None = Header(default=None)):
            try:
                p = auth.authenticate(authorization)
            except AuthError:
                raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"}) from None
            if not p.at_least(min_role):
                raise HTTPException(403, f"needs the {min_role} role")
            return p
        return dep

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        """Errors as {"error": ...} with their status."""
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc):
        """404."""
        return JSONResponse({"error": f"not found: {exc}"}, status_code=404)

    @app.exception_handler(IllegalTransition)
    async def illegal(request: Request, exc):
        """409."""
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(NoteRequired)
    async def note_required(request: Request, exc):
        """422."""
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.exception_handler(UnknownGateway)
    async def unknown_gateway(request: Request, exc):
        """404."""
        return JSONResponse({"error": f"unknown gateway: {exc}"}, status_code=404)

    @app.exception_handler(psycopg.OperationalError)
    async def db_down(request: Request, exc):
        """503: degraded, visibly, without leaking connection details."""
        return JSONResponse({"error": "database unavailable"}, status_code=503)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        """No caching of API answers; no sniffing."""
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def visible(node: str) -> bool:
        """False for a node whose registered subject has no ACTIVE consent record (a node with no subject is not gated)."""
        return registry.consent_ok(node) is not False

    def consent_gate(node: str, p) -> None:
        """Refuse (and audit the refusal) for any read or change that touches a node without active consent, whatever the route or the caller's role."""
        if not visible(node):
            audit.append(p.user, p.role, "consent.denied", f"node:{node}", {})
            raise HTTPException(403, "no active consent record for this node's subject")

    @app.get("/api/session")
    def session(p=Depends(who("viewer"))):
        """Who am I, what role, the dev-mode banner and the intended-purpose statement for the footer."""
        return {"user": p.user, "role": p.role, "banner": auth.banner, "purpose": PURPOSE}

    @app.get("/api/worklist")
    def worklist(p=Depends(who("viewer"))):
        """Nodes, most urgent first, each with a 7-day delivery sparkline."""
        now = clock()
        rows = store.node_rows(now, known_nodes())
        shown = [r for r in rows if visible(r["node"])]  # a node without active consent is not listed at all, and no data about it is read
        for r in shown:
            r["sparkline"] = registry.sparkline(r["node"], now)
        return {"generated_at": now, "nodes": shown, "hidden_no_consent": len(rows) - len(shown)}

    @app.get("/api/nodes/{node_id}")
    def node_detail(node_id: str, p=Depends(who("viewer"))):
        """One node: registry entry, open alerts, latest health."""
        consent_gate(node_id, p)
        return {"node": node_id, "registry": registry.node(node_id), "consent": registry.consent_ok(node_id), "alerts": [a for a in store.list_alerts(node=node_id) if a["state"] != "resolved"],
                "health": registry.latest_health(node_id)}

    @app.get("/api/nodes/{node_id}/trend")
    def trend(node_id: str, range: str = "24h", p=Depends(who("viewer"))):
        """Delivery and latency history; says if the data do not span the range."""
        if range not in RANGES:
            raise HTTPException(422, f"range must be one of {', '.join(RANGES)}")
        consent_gate(node_id, p)
        return registry.trend(node_id, range, clock())

    @app.get("/api/alerts")
    def alerts(state: str | None = None, p=Depends(who("viewer"))):
        """Alerts, newest first."""
        if state is not None and state not in STATES:
            raise HTTPException(422, f"state must be one of {', '.join(STATES)}")
        return {"alerts": [x for x in store.list_alerts(state=state) if visible(x["node"])]}

    @app.get("/api/alerts/{alert_id}")
    def alert_detail(alert_id: int, p=Depends(who("viewer"))):
        """One alert with its history. Reading it is audited."""
        a = store.get(alert_id)
        consent_gate(a["node"], p)
        audit.append(p.user, p.role, "alert.read", f"alert:{alert_id}", {})
        return a

    def act(action: str):
        """Route for one workflow action."""
        def handler(alert_id: int, body: NoteBody, p=Depends(who("operator"))):
            """Apply the action; errors map to 404 / 409 / 422 (and 403 if the alert's node has no active consent)."""
            consent_gate(store.get(alert_id)["node"], p)
            return store.transition(alert_id, action, p.user, p.role, body.note, clock())
        return handler
    for action in ("ack", "escalate", "resolve", "note"):
        app.post(f"/api/alerts/{{alert_id}}/{action}", name=f"alert_{action}")(act(action))

    @app.get("/api/registry/gateways")
    def r_gateways(p=Depends(who("admin"))):
        """Gateways."""
        return {"gateways": registry.gateways()}

    @app.get("/api/registry/nodes")
    def r_nodes(p=Depends(who("admin"))):
        """Nodes."""
        return {"nodes": registry.nodes()}

    @app.get("/api/registry/subjects")
    def r_subjects(p=Depends(who("admin"))):
        """Pseudonymous research subjects and their consent state."""
        return {"subjects": registry.subjects()}

    @app.post("/api/registry/gateways/{gateway_id}/revoke")
    def revoke(gateway_id: str, p=Depends(who("admin"))):
        """Revoke a gateway; the enrolment API regenerates the CRL (reported in `crl`)."""
        g = registry.revoke_gateway(gateway_id, p.user, p.role, clock())
        crl = on_revoke(gateway_id) if on_revoke else "not regenerated: no enrolment service is connected"
        return {"gateway": g, "crl": crl}

    @app.get("/api/audit")
    def audit_tail(limit: int = 50, p=Depends(who("admin"))):
        """The newest audit rows. Reading them is audited."""
        if not 1 <= limit <= 500:
            raise HTTPException(422, "limit must be 1..500")
        audit.append(p.user, p.role, "audit.read", "audit", {"limit": limit})
        return {"rows": audit.tail(limit)}

    @app.get("/api/audit/verify")
    def audit_verify(p=Depends(who("admin"))):
        """Recompute the whole chain (and say what the head is, for the off-VM copy)."""
        r = audit.verify()
        audit.append(p.user, p.role, "audit.verify", "audit", {"ok": r.ok, "checked": r.checked})
        return {"ok": r.ok, "checked": r.checked, "head": r.head, "bad_id": r.bad_id, "reason": r.reason}

    @app.get("/api/config")
    def get_config(p=Depends(who("admin"))):
        """The alert thresholds currently in force."""
        return {k: getattr(cfg, k) for k in (*CONFIG_LIMITS, "latency_enabled")}

    @app.put("/api/config")
    def put_config(body: dict, p=Depends(who("admin"))):
        """Change thresholds (validated, audited with before and after)."""
        unknown = set(body) - set(CONFIG_LIMITS) - {"latency_enabled"}
        if unknown:
            raise HTTPException(422, f"unknown setting(s): {', '.join(sorted(unknown))}")
        for k, v in body.items():
            if k == "latency_enabled":
                if not isinstance(v, bool):
                    raise HTTPException(422, "latency_enabled must be true or false")
                continue
            lo, hi = CONFIG_LIMITS[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise HTTPException(422, f"{k} must be a number between {lo} and {hi}")
        before = {k: getattr(cfg, k) for k in body}
        for k, v in body.items():
            setattr(cfg, k, v)
        audit.append(p.user, p.role, "config.change", "config", {"before": before, "after": body})
        return {k: getattr(cfg, k) for k in (*CONFIG_LIMITS, "latency_enabled")}

    @app.get("/healthz")
    def healthz():
        """The process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        """Ready only if the database and the bus consumer are fine; otherwise 503 with the reason."""
        ok, why = ready()
        return JSONResponse({"status": "ready" if ok else "degraded", "reason": why}, status_code=200 if ok else 503)

    @app.get("/metrics")
    def prometheus(request: Request):
        """Prometheus text for the internal network; a request that came through the proxy is refused."""
        if "x-forwarded-for" in request.headers or "x-forwarded-host" in request.headers:
            raise HTTPException(404, "not found")
        counts = {s: 0 for s in STATES}
        for a in store.list_alerts():
            counts[a["state"]] += 1
        for s, n in counts.items():
            alerts_gauge.labels(s).set(n)
        return PlainTextResponse(generate_latest(metrics).decode(), media_type="text/plain; version=0.0.4")

    return app
