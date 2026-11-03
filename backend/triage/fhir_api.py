"""Read-only FHIR facade: the only door to the reference EHR for a browser, and it enforces consent and the role rules before it asks the FHIR server anything.

PURPOSE: HAPI FHIR itself is never exposed (deploy/caddy/Caddyfile does not route it). People and the triage page read EHG data through these routes, which are part of the triage API (decision 0013).
INPUT: GET /fhir/metadata; GET /fhir/Observation?node=<id>&_count=<1..20>; GET /fhir/Device?node=<id>. The `viewer` role or higher (Bearer token, backend/jwtauth.py).
  e.g. GET /fhir/Observation?node=EHG-1&_count=5 with a viewer token.
OUTPUT: a FHIR R5 searchset Bundle (Observation: newest first, SampledData unchanged), a Device Bundle, or a CapabilityStatement that lists only `read` and `search-type` interactions.
  Errors as {"error": ...}: 401, 403 (no ACTIVE consent for the node's subject, audited as consent.denied), 405 (any method other than GET), 422 (missing or malformed `node`, `_count` out of range), 502 (FHIR server unreachable or answered badly).
HOW: `node` is REQUIRED and must be a plain id, so there is no unscoped search that could list other subjects' data. The upstream query is built from that validated id with proper encoding, and every returned Observation is checked again
  (its identifier must belong to the requested node) before it leaves, so a wrong answer from the server cannot leak another node's data. Each read is audited (fhir.read). Consent is read from the registry on every request, so withdrawn consent stops reads at once.
  Strict rule: unlike the pipeline-health routes, a node with NO registered subject is refused here too, because the sync worker never exports one and nothing may be shown that has no consent behind it.
ROLE IN THESIS: The EHR panel of the operator interface (Unit 10) and the proof that consent is enforced at the clinical boundary. Operational data only; no clinical statement is made.
BEFORE -> AFTER: Before: FHIR existed only as an offline file. After: a consent-checked, audited, read-only window onto the reference EHR.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException

from backend.bus import valid_id
from backend.fhir_sync.worker import NODE_SYSTEM, OBS_SYSTEM


class Upstream:
    """GETs from the FHIR server's base URL; `http` is injectable for tests."""

    def __init__(self, base_url: str, token: str | None = None, http: httpx.Client | None = None):
        self._base, self._http = base_url.rstrip("/"), http or httpx.Client(timeout=15)
        self._headers = {"Accept": "application/fhir+json", **({"Authorization": f"Bearer {token}"} if token else {})}

    def search(self, resource: str, params: dict) -> dict:
        """One search; any failure becomes a 502 without the server's address or text."""
        try:
            r = self._http.get(f"{self._base}/{resource}", params=params, headers=self._headers)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "the FHIR server is unavailable") from None
        if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
            raise HTTPException(502, "the FHIR server answered unexpectedly")
        return body


def router(upstream, who, audit, registry) -> APIRouter:
    """`who(min_role)` is the app's authentication dependency factory. All routes are GET-only."""
    r = APIRouter(prefix="/fhir")

    def scoped(node: str | None, p) -> str:
        """Validate the node id and require ACTIVE consent (audited refusal)."""
        if node is None or not valid_id(node):
            raise HTTPException(422, "node is required and must be a plain node id")
        if registry.consent_ok(node) is not True:
            audit.append(p.user, p.role, "consent.denied", f"node:{node}", {"via": "fhir"})
            raise HTTPException(403, "no active consent record for this node's subject")
        return node

    @r.get("/metadata")
    def metadata(p=Depends(who("viewer"))):
        """What this facade can do: read and search only."""
        return {"resourceType": "CapabilityStatement", "status": "active", "kind": "instance", "fhirVersion": "5.0.0", "format": ["json"],
                "description": "Read-only facade over a reference FHIR server; consent is enforced per node. Research prototype, not for clinical use.",
                "rest": [{"mode": "server", "resource": [{"type": t, "interaction": [{"code": "read"}, {"code": "search-type"}], "searchParam": [{"name": "node", "type": "token"}]} for t in ("Observation", "Device")]}]}

    @r.get("/Observation")
    def observations(node: str | None = None, _count: int = 5, p=Depends(who("viewer"))):
        """Newest waveform windows of one node."""
        scoped(node, p)
        if not 1 <= _count <= 20:
            raise HTTPException(422, "_count must be 1..20")
        found = upstream.search("Observation", {"device.identifier": f"{NODE_SYSTEM}|{node}", "_sort": "-date", "_count": _count})
        entries = [e for e in found.get("entry", []) if _belongs(e.get("resource", {}), node)]
        audit.append(p.user, p.role, "fhir.read", f"node:{node}", {"resource": "Observation", "returned": len(entries)})
        return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries}

    @r.get("/Device")
    def devices(node: str | None = None, p=Depends(who("viewer"))):
        """The Device resource of one node."""
        scoped(node, p)
        found = upstream.search("Device", {"identifier": f"{NODE_SYSTEM}|{node}", "_count": 1})
        entries = [e for e in found.get("entry", []) if any(i.get("system") == NODE_SYSTEM and i.get("value") == node for i in e.get("resource", {}).get("identifier", []))]
        audit.append(p.user, p.role, "fhir.read", f"node:{node}", {"resource": "Device", "returned": len(entries)})
        return {"resourceType": "Bundle", "type": "searchset", "total": len(entries), "entry": entries}

    return r


def _belongs(resource: dict, node: str) -> bool:
    """True when the Observation's identifier key is `<node>/...` (the key the sync worker wrote)."""
    return resource.get("resourceType") == "Observation" and any(i.get("system") == OBS_SYSTEM and str(i.get("value", "")).startswith(node + "/") for i in resource.get("identifier", []))
