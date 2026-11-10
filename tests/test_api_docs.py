"""Checks docs/api: the OpenAPI documents are exactly what the FastAPI apps produce now (so they cannot drift), every route is documented with a summary or docstring, and the AsyncAPI document matches the code:
channel addresses appear in the sources, the payload keys are the ones the gateway publishes, and the required keys are the bridge's."""

import json
import re
from pathlib import Path

import yaml

from backend.bridge.bridge import REQUIRED
from tools.export_api_docs import OUT, documents

ROOT = Path(__file__).parents[1]
ASYNC = yaml.safe_load((OUT / "asyncapi.yaml").read_text())


def test_openapi_documents_are_current():
    for name, text in documents().items():
        assert (OUT / name).read_text() == text, f"{name} is stale: run uv run python tools/export_api_docs.py"


def test_every_operation_has_a_description_and_the_secured_ones_say_so():
    for name in ("openapi-triage.json", "openapi-enroll.json"):
        doc = json.loads((OUT / name).read_text())
        for path, ops in doc["paths"].items():
            for method, op in ops.items():
                assert op.get("description") or op.get("summary"), f"{name} {method.upper()} {path} has no description"
    triage = json.loads((OUT / "openapi-triage.json").read_text())
    assert {"/fhir/Observation", "/fhir/Device", "/fhir/metadata", "/api/worklist", "/ui-config.json", "/healthz"} <= set(triage["paths"])
    assert "Not for clinical" in triage["info"]["description"] or "not for clinical" in triage["info"]["description"].lower()


def test_asyncapi_parses_and_every_operation_points_at_a_real_channel():
    assert ASYNC["asyncapi"].startswith("3.")
    for op in ASYNC["operations"].values():
        assert op["channel"]["$ref"].split("/")[-1] in ASYNC["channels"]
    for ch in ASYNC["channels"].values():
        for m in ch["messages"].values():
            assert m["$ref"].split("/")[-1] in ASYNC["components"]["messages"]


def test_channel_addresses_match_the_code():
    sources = "\n".join(p.read_text() for d in ("gateway", "backend") for p in (ROOT / d).rglob("*.py")) + (ROOT / "backend/mosquitto/acl").read_text()
    addresses = {k: v["address"] for k, v in ASYNC["channels"].items()}
    assert "ehg/{nodeId}/pkt" == addresses["packet"] and 'f"ehg/{node_id}/pkt"' in sources and "ehg/+/pkt" in sources
    assert addresses["gatewayStatus"] == "ehg/_gw/{gatewayId}/status" and 'f"ehg/_gw/{args.gateway_id}/status"' in sources
    for prefix, key in (("EHG.PKT", "busPacket"), ("EHG.GW", "busGateway"), ("EHG.BAD", "busBad")):
        assert addresses[key].startswith(prefix + ".") and f'"{prefix}"' in sources


def test_payload_keys_are_the_ones_the_gateway_publishes_and_required_are_the_bridges():
    schema = ASYNC["components"]["schemas"]["packet"]
    gateway_keys = {"node_id", "seq", "frame_idx", "t_inject", "t_inject_host", "n_ch", "fs_hz", "record_id", "flags", "samples", "t_gw_rx", "t_mqtt_pub"}
    assert set(schema["properties"]) == gateway_keys | {"replayed"}
    spec = (ROOT / "docs/interface-spec.md").read_text()
    assert all(k in spec for k in gateway_keys) and "replayed" in spec
    assert tuple(schema["required"]) == REQUIRED
    gateway_src = (ROOT / "gateway/gateway.py").read_text()
    for k in gateway_keys:
        assert f'"{k}"' in gateway_src, f"the gateway does not publish {k}"
    assert re.fullmatch(r"\^\[A-Za-z0-9_-\]\{1,64\}\$", schema["properties"]["node_id"]["pattern"])


def test_bad_reasons_listed_are_the_bridges():
    text = (ROOT / "backend/bridge/bridge.py").read_text()
    listed = re.search(r"One of ([a-z_, ]+)", ASYNC["channels"]["busBad"]["parameters"]["reason"]["description"]).group(1).split(", ")
    for reason in listed:
        assert reason in text, reason
