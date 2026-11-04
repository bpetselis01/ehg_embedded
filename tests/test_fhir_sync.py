"""Checks backend/fhir_sync/worker.py against a FAKE FHIR server that implements the conditional-create / conditional-update rules of a transaction Bundle.
What this proves: idempotence (restart, repeat, two workers), complete-chunk-only sending, late frames replace rather than duplicate, consent is required, server failures are
not silent and are retried. What it does NOT prove: that a real HAPI server accepts the resources (that is tests/test_hapi_integration.py, which needs Docker)."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from pathlib import Path

import httpx
import numpy as np
import pytest

from backend.fhir_sync.worker import NODE_SYSTEM, OBS_SYSTEM, FhirClient, FhirError, Worker, transaction
from tools.fhir_export import bundle

ROOT = Path(__file__).parents[1]

SRC = {"fs": np.float64(20.0), "gain": np.array([13107.0, 100.0]), "baseline": np.array([0, 5]), "channels": np.array(["1", "2"])}
T0 = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


class FakeFhir:
    """Just enough of a FHIR server: transaction with POST + If-None-Exist and PUT?identifier=, on an in-memory store."""

    def __init__(self):
        self.resources, self.posts, self.fail = [], 0, None

    def _match(self, rtype, query):
        system, value = unquote(parse_qs(query)["identifier"][0]).split("|")
        return [r for r in self.resources if r["resourceType"] == rtype and any(i["system"] == system and i["value"] == value for i in r.get("identifier", []))]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.posts += 1
        if self.fail:
            return httpx.Response(self.fail)
        tx = json.loads(request.content)
        assert tx["type"] == "transaction" and request.headers["content-type"] == "application/fhir+json"
        out = []
        for e in tx["entry"]:
            req, res = e["request"], e["resource"]
            if req["method"] == "POST":
                if "ifNoneExist" in req and self._match(res["resourceType"], req["ifNoneExist"]):
                    out.append({"response": {"status": "200 OK"}})
                    continue
                self.resources.append(res)
                out.append({"response": {"status": "201 Created"}})
            else:
                q = urlparse(req["url"])
                hits = self._match(q.path, q.query)
                assert len(hits) <= 1
                if hits:
                    self.resources[self.resources.index(hits[0])] = res
                else:
                    self.resources.append(res)
                out.append({"response": {"status": "200 OK" if hits else "201 Created"}})
        return httpx.Response(200, json={"resourceType": "Bundle", "type": "transaction-response", "entry": out})

    def of(self, rtype):
        return [r for r in self.resources if r["resourceType"] == rtype]


def make(consent=True, frames=None, chunk=10):
    server = FakeFhir()
    data = {"frames": frames if frames is not None else {i: [i, -i] for i in range(35)}}  # 3 complete chunks of 10 + 5 pending
    w = Worker(lambda n: (0, data["frames"], T0), lambda rid: SRC, lambda n: consent, FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))), chunk_frames=chunk)
    return w, server, data


def test_only_complete_chunks_are_sent_and_the_rest_is_pending():
    w, server, _ = make()
    r = w.sync_node("EHG-1")
    assert (r["sent"], r["pending"], r["skipped"]) == (3, 5, None)
    assert len(server.of("Observation")) == 3 and len(server.of("Device")) == 1, "one Device however many chunks"
    keys = sorted(o["identifier"][0]["value"] for o in server.of("Observation"))
    assert keys == ["EHG-1/-/0/0", "EHG-1/-/0/1", "EHG-1/-/0/2"]


def test_repeating_the_worker_sends_nothing_new_and_a_restarted_worker_creates_no_duplicates():
    w, server, data = make()
    w.sync_node("EHG-1")
    again = w.sync_node("EHG-1")
    assert (again["sent"], again["unchanged"]) == (0, 3)
    restarted = Worker(w._frames, w._meta, w._consent, w._client, chunk_frames=10)  # a fresh process has no memory of what it sent
    assert restarted.sync_node("EHG-1")["sent"] == 3
    assert len(server.of("Observation")) == 3 and len(server.of("Device")) == 1, "the conditional requests keep the server at one copy"


def test_a_late_recovered_frame_replaces_the_chunk_and_never_adds_a_second_one():
    frames = {i: [i, -i] for i in range(35) if i != 4}  # frame 4 never arrived
    w, server, data = make(frames=frames)
    w.sync_node("EHG-1")
    first = next(o for o in server.of("Observation") if o["identifier"][0]["value"] == "EHG-1/-/0/0")
    assert first["component"][0]["valueSampledData"]["data"].split()[4] == "E"
    data["frames"][4] = [4, -4]  # RESEND recovers it
    assert w.sync_node("EHG-1")["sent"] == 1, "only the changed chunk is sent again"
    obs = [o for o in server.of("Observation") if o["identifier"][0]["value"] == "EHG-1/-/0/0"]
    assert len(obs) == 1 and obs[0]["component"][0]["valueSampledData"]["data"].split()[4] == "4"


def test_samples_stay_raw_counts_and_chunk_times_follow_the_frame_index():
    w, server, _ = make()
    w.sync_node("EHG-1")
    second = next(o for o in server.of("Observation") if o["identifier"][0]["value"] == "EHG-1/-/0/1")
    assert [int(v) for v in second["component"][0]["valueSampledData"]["data"].split()] == list(range(10, 20)), "raw int16 counts, unscaled (H4)"
    assert second["effectivePeriod"]["start"] == "2026-11-03T09:00:00.500000+00:00", "frame 10 at 20 Hz is 0.5 s after frame 0"


def test_no_consent_means_nothing_is_sent_for_false_none_or_withdrawn():
    for consent in (False, None):
        w, server, _ = make(consent=consent)
        assert w.sync_node("EHG-1")["skipped"] == "consent"
        assert server.posts == 0, f"consent={consent!r}: not even a request may leave"


def test_consent_is_checked_on_every_round_not_once():
    state = {"ok": True}
    server = FakeFhir()
    w = Worker(lambda n: (0, {i: [i, i] for i in range(35)}, T0), lambda r: SRC, lambda n: state["ok"], FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))), chunk_frames=10)
    w.sync_node("EHG-1")
    sent = server.posts
    state["ok"] = False
    assert w.sync_node("EHG-1")["skipped"] == "consent" and server.posts == sent


def test_server_failure_is_reported_leaves_the_chunk_unsent_and_is_retried():
    w, server, _ = make()
    server.fail = 503
    r = w.sync_node("EHG-1")
    assert r["skipped"] == "error" and r["sent"] == 0 and server.resources == []
    server.fail = None
    assert w.sync_node("EHG-1")["sent"] == 3, "the next round sends what failed"


def test_an_entry_level_failure_inside_a_200_response_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"entry": [{"response": {"status": "201 Created"}}, {"response": {"status": "422 Unprocessable Entity"}}]})
    c = FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        c.send({"resourceType": "Bundle", "type": "transaction", "entry": []})
    except FhirError as e:
        assert "422" in str(e)
    else:
        raise AssertionError("a failed entry must not be treated as success")


def test_an_unreachable_server_is_a_fhir_error_without_leaking_the_url():
    def handler(request):
        raise httpx.ConnectError("boom http://secret-host/")
    c = FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        c.send({})
    except FhirError as e:
        assert "secret-host" not in str(e)
    else:
        raise AssertionError("expected FhirError")


def test_no_data_is_skipped_not_crashed():
    w = Worker(lambda n: None, lambda r: SRC, lambda n: True, None)
    assert w.sync_node("EHG-1")["skipped"] == "no data"


def test_transaction_does_not_modify_the_exported_bundle_and_uses_conditional_requests():
    b = bundle({0: [1, 2]}, SRC, "EHG-1", "rec", 0, T0)
    before = json.dumps(b, sort_keys=True)
    tx = transaction(b, "EHG-1", "EHG-1/0/0")
    assert json.dumps(b, sort_keys=True) == before and tx["type"] == "transaction"
    dev, obs = tx["entry"]
    assert dev["request"] == {"method": "POST", "url": "Device", "ifNoneExist": "identifier=" + "urn%3Athesisb%3Aehg%2Fnode-id%7CEHG-1"}
    assert obs["request"]["method"] == "PUT" and obs["request"]["url"].startswith("Observation?identifier=")
    assert obs["resource"]["identifier"] == [{"system": OBS_SYSTEM, "value": "EHG-1/0/0"}] and NODE_SYSTEM in dev["request"]["ifNoneExist"].replace("%3A", ":").replace("%2F", "/")
    assert obs["resource"]["device"]["reference"] == dev["fullUrl"], "the reference still points at the Device entry of the same transaction"


def test_resources_validate_as_fhir_r5_models():
    """Structure check with the fhir.resources R5 models (offline). Not a substitute for a real server: see tests/test_hapi_integration.py."""
    from fhir.resources.device import Device
    from fhir.resources.observation import Observation

    tx = transaction(bundle({0: [1, 2], 2: [5, 6]}, SRC, "EHG-1", "rec", 0, T0), "EHG-1", "EHG-1/0/0")
    dev, obs = tx["entry"]
    assert Device.model_validate(dev["resource"]).get_resource_type() == "Device"
    o = Observation.model_validate(obs["resource"])
    sd = o.component[0].valueSampledData
    assert sd.data == "1 E 5" and sd.dimensions == 1 and float(sd.interval) == 50.0


# ---- running as a service: metadata from the small JSON file instead of the .npz recordings

def test_load_records_gives_what_bundle_needs(tmp_path):
    import json
    from backend.fhir_sync.worker import load_records
    f = tmp_path / "records.json"
    f.write_text(json.dumps({"0": {"record": "tpehg1007", "fs": 20.0, "gain": [13107.0], "baseline": [0], "channels": ["1"]}}))
    meta = load_records(f)(0)
    assert float(meta["fs"]) == 20.0 and list(meta["channels"]) == ["1"] and float(meta["gain"][0]) == 13107.0 and int(meta["baseline"][0]) == 0
    with pytest.raises(KeyError):
        load_records(f)(7)  # unknown record: reported by the caller, never guessed


def test_the_committed_metadata_matches_the_converted_data_when_it_is_present():
    from tools import export_record_meta
    data = ROOT / "data/converted/tpehgdb/manifest.json"
    if not data.exists():
        pytest.skip("data/converted is not present (README step 0)")
    assert export_record_meta.main(["--check"]) == 0


def test_the_committed_metadata_is_well_formed_and_holds_no_samples():
    import json
    table = json.loads((ROOT / "deploy/fhir/records.json").read_text())
    assert len(table) >= 1
    for rid, r in table.items():
        assert rid.isdigit() and set(r) == {"record", "fs", "gain", "baseline", "channels"}, rid
        assert len(r["gain"]) == len(r["baseline"]) == len(r["channels"]) >= 1 and r["fs"] > 0


# ---- a node handed to another subject: the earlier subject's frames are never sent under the new one (security review, 2026-11-04)

def make_since(since_value, frames=None, chunk=10):
    server, since = FakeFhir(), {"v": since_value}
    data = {"frames": frames if frames is not None else {i: [i, -i] for i in range(35)}}
    w = Worker(lambda n: (0, data["frames"], T0), lambda rid: SRC, lambda n: True, FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))),
               chunk_frames=chunk, since_for=lambda n: since["v"])
    return w, server, since


def test_only_frames_from_the_assignment_time_on_are_sent():
    from datetime import timedelta
    w, server, _ = make_since(T0 + timedelta(seconds=0.5))   # 20 Hz: frame 10 is the first at or after 0.5 s; chunk 0 (frames 0..9) belongs to the previous subject
    r = w.sync_node("EHG-1")
    assert r["skipped"] is None
    assert sorted(o["identifier"][0]["value"] for o in server.of("Observation")) == ["EHG-1/-/0/1", "EHG-1/-/0/2"], "chunk 0 is never sent; chunk 3 is still filling"
    values = [int(v) for o in server.of("Observation") for c in o["component"][:1] for v in c["valueSampledData"]["data"].split() if v != "E"]
    assert min(values) == 10 and max(values) == 29


def test_a_node_whose_every_frame_is_older_than_the_assignment_sends_nothing():
    from datetime import timedelta
    w, server, _ = make_since(T0 + timedelta(hours=1))
    assert w.sync_node("EHG-1")["skipped"] == "no data" and server.posts == 0


def test_chunk_times_are_still_right_after_the_early_frames_are_dropped():
    from datetime import timedelta
    w, server, _ = make_since(T0 + timedelta(seconds=0.5))
    w.sync_node("EHG-1")
    starts = sorted(datetime.fromisoformat(o["effectivePeriod"]["start"]) for o in server.of("Observation"))
    assert starts[0] == T0 + timedelta(seconds=0.5), "frame 10 is at exactly 0.5 s"
    assert starts[1] == T0 + timedelta(seconds=1.0)


def test_reassignment_forgets_what_was_sent_for_the_old_subject():
    from datetime import timedelta
    w, server, since = make_since(None)
    w.sync_node("EHG-1")
    assert any(k.startswith("EHG-1/") for k in w._sent)
    since["v"] = T0 + timedelta(seconds=0.5)
    w.sync_node("EHG-1")
    assert all(not k.startswith("EHG-1/-/0/0") for k in w._sent), "chunk 0 (frames 0..9) is entirely before the assignment and is no longer remembered as sent"


# ---- review findings, 2026-11-04: keys must not collide across subjects, and consent is re-read right before every send

def test_a_node_handed_to_another_subject_never_overwrites_the_first_subjects_windows():
    """The same node, the same record and the same chunk number now give DIFFERENT observation keys for different subjects, so a conditional PUT cannot replace the first subject's window."""
    server = FakeFhir()
    frames = {i: [i, -i] for i in range(35)}
    state = {"subject": "s1", "since": None}
    w = Worker(lambda n: (0, frames, T0), lambda rid: SRC, lambda n: True, FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))),
               chunk_frames=10, since_for=lambda n: state["since"], subject_for=lambda n: state["subject"])
    w.sync_node("EHG-1")
    before = {o["identifier"][0]["value"]: json.dumps(o["component"], sort_keys=True) for o in server.of("Observation")}
    assert sorted(before) == ["EHG-1/s1/0/0", "EHG-1/s1/0/1", "EHG-1/s1/0/2"]
    state.update(subject="s2", since=T0)       # the same frames now count for the next subject (a new session of the same record)
    w.sync_node("EHG-1")
    after = {o["identifier"][0]["value"]: json.dumps(o["component"], sort_keys=True) for o in server.of("Observation")}
    assert all(after[k] == v for k, v in before.items()), "the first subject's windows are untouched"
    assert sorted(set(after) - set(before)) == ["EHG-1/s2/0/0", "EHG-1/s2/0/1", "EHG-1/s2/0/2"]
    assert len(server.of("Device")) == 1, "the Device is the node, shared by both"


def test_a_withdrawal_during_a_round_stops_the_next_send():
    """Consent is not read once at the start of the round: it is asked again before each transaction (a stale snapshot would send up to a whole round after a withdrawal)."""
    server, answers = FakeFhir(), iter([True, True, False, False, False])   # start of the round, first send, then withdrawn
    w = Worker(lambda n: (0, {i: [i, -i] for i in range(35)}, T0), lambda rid: SRC, lambda n: next(answers),
               FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))), chunk_frames=10)
    r = w.sync_node("EHG-1")
    assert r["sent"] == 1 and r["skipped"] == "changed" and len(server.of("Observation")) == 1, "chunk 0 went out, then the withdrawal stopped chunks 1 and 2"


def test_a_subject_change_during_a_round_also_stops_the_send():
    server, calls = FakeFhir(), {"n": 0}

    def subject(node):
        calls["n"] += 1
        return "s1" if calls["n"] <= 1 else "s2"        # read once at the start of the round, different when asked again before the first send
    w = Worker(lambda n: (0, {i: [i, -i] for i in range(35)}, T0), lambda rid: SRC, lambda n: True,
               FhirClient("http://fhir/fhir", http=httpx.Client(transport=httpx.MockTransport(server.handle))), chunk_frames=10, subject_for=subject)
    r = w.sync_node("EHG-1")
    assert r["skipped"] == "changed" and server.posts == 0


def test_node_state_reads_the_registry_now(pg_url):
    """The query the service uses before every send, on a real PostgreSQL."""
    import psycopg
    from backend.db import migrate
    from backend.fhir_sync.worker import node_state
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO registry.subject (id, pseudonym, source_dataset, consent_status) VALUES ('s1', 'p', 'd', 'active')")
        c.execute("INSERT INTO registry.node (id, subject_id, subject_since) VALUES ('EHG-1', 's1', '2026-11-03T12:00:00+00:00'), ('EHG-2', NULL, NULL)")
    assert node_state(dsn, "EHG-1")[:2] == (True, "s1") and node_state(dsn, "EHG-1")[2] == datetime(2026, 11, 3, 12, tzinfo=timezone.utc)  # an instant, whatever zone the server prints
    assert node_state(dsn, "EHG-2") == (None, None, None) and node_state(dsn, "ghost") == (None, None, None)
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE registry.subject SET consent_status = 'withdrawn'")
    assert node_state(dsn, "EHG-1")[0] is False, "a withdrawal is visible on the very next read"
