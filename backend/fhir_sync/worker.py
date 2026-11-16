"""FHIR sync worker: copies stored EHG windows into a FHIR R5 server (HAPI) as Device + Observation(SampledData), once per window, only with active consent."""

import argparse
import copy
import hashlib
import json
import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

from tools.fhir_export import CODE_SYSTEM, bundle

log = logging.getLogger("fhir_sync")
NODE_SYSTEM = f"{CODE_SYSTEM}/node-id"
OBS_SYSTEM = f"{CODE_SYSTEM}/obs-key"
CHUNK_FRAMES = 600  # 30 s at 20 Hz: small enough to arrive soon, large enough to keep the resource count low


class FhirError(Exception):
    """The FHIR server refused or failed a transaction."""


def _search(system: str, value: str) -> str:
    """identifier=<system>|<value>, percent-encoded as FHIR search URLs expect."""
    return "identifier=" + quote(f"{system}|{value}", safe="")


def transaction(window: dict, node: str, key: str) -> dict:
    """Turn bundle()'s two-entry collection into a transaction with conditional requests. The input is not modified."""
    device, observation = copy.deepcopy(window["entry"])
    observation["resource"]["identifier"] = [{"system": OBS_SYSTEM, "value": key}]
    device["request"] = {"method": "POST", "url": "Device", "ifNoneExist": _search(NODE_SYSTEM, node)}
    observation["request"] = {"method": "PUT", "url": "Observation?" + _search(OBS_SYSTEM, key)}
    return {"resourceType": "Bundle", "type": "transaction", "entry": [device, observation]}


class FhirClient:
    """Posts transactions to the server's base URL. `http` is injectable so tests need no network."""

    def __init__(self, base_url: str, token: str | None = None, http: httpx.Client | None = None):
        self._base, self._http = base_url.rstrip("/"), http or httpx.Client(timeout=30)
        self._headers = {"Content-Type": "application/fhir+json", **({"Authorization": f"Bearer {token}"} if token else {})}

    def send(self, tx: dict) -> None:
        """POST the transaction; raise FhirError unless the server and every entry answered 2xx."""
        try:
            r = self._http.post(self._base, content=json.dumps(tx), headers=self._headers)
        except httpx.HTTPError as e:
            raise FhirError(f"FHIR server unreachable: {type(e).__name__}") from None
        if r.status_code != 200:
            raise FhirError(f"transaction refused: HTTP {r.status_code}")
        for e in r.json().get("entry", []):
            if not str(e.get("response", {}).get("status", "")).startswith("2"):
                raise FhirError(f"entry failed: {e.get('response', {}).get('status')}")


class Worker:
    """frames_for(node) -> (record_id, {frame: [counts]}, time of the first frame) or None; meta_for(record_id) -> the record's fs/gain/baseline/channels;
    consent_ok(node) -> True / False / None (registry semantics); client has.send(transaction).
    since_for(node) -> when the node's current subject was assigned (earlier frames belong to someone else and are never sent); subject_for(node) -> that subject's id.
    consent_ok, since_for and subject_for are asked AGAIN right before every transaction, so a withdrawal or a reassignment that happens while a round is running stops the very next send."""

    def __init__(self, frames_for, meta_for, consent_ok, client, chunk_frames: int = CHUNK_FRAMES, since_for=None, subject_for=None):
        self._frames, self._meta, self._consent, self._client, self._n = frames_for, meta_for, consent_ok, client, chunk_frames
        self._since_for = since_for or (lambda node: None)
        self._subject_for = subject_for or (lambda node: "-")
        self._since_seen: dict[str, object] = {}
        self._sent: dict[str, str] = {}  # observation key -> fingerprint of what the server has

    def sync_node(self, node: str) -> dict:
        """Send this node's complete, new or changed chunks. Returns counts: sent, unchanged, pending (incomplete frames), skipped ('consent' or 'no data' or 'error')."""
        out = {"sent": 0, "unchanged": 0, "pending": 0, "skipped": None}
        if self._consent(node) is not True:
            out["skipped"] = "consent"
            return out
        got = self._frames(node)
        if not got:
            out["skipped"] = "no data"
            return out
        record_id, frames, start = got
        src = self._meta(record_id)
        fs = float(src["fs"])
        since, subject = self._since_for(node), self._subject_for(node)
        period = (since, subject)
        if self._since_seen.get(node, period) != period:  # the node was given to another subject: forget what was sent for the old one
            self._sent = {k: v for k, v in self._sent.items() if not k.startswith(node + "/")}
        self._since_seen[node] = period
        first = min(frames)  # `start` is the time of THIS frame
        if since is not None:
            frames = {i: c for i, c in frames.items() if start + timedelta(seconds=(i - first) / fs) >= since}
            if not frames:
                out["skipped"] = "no data"
                return out
        hi = max(frames)
        chunks: dict[int, dict] = {}
        for i, counts in frames.items():
            chunks.setdefault(i // self._n, {})[i] = counts
        for k in sorted(chunks):
            if (k + 1) * self._n > hi:  # still filling: nothing newer has arrived
                out["pending"] += len(chunks[k])
                continue
            part = chunks[k]
            key = f"{node}/{subject}/{record_id}/{k}"  # the SUBJECT is part of the key: a conditional PUT can never replace a window that was written for another subject
            print_ = hashlib.sha256(json.dumps(sorted(part.items())).encode()).hexdigest()
            if self._sent.get(key) == print_:
                out["unchanged"] += 1
                continue
            t0 = start + timedelta(seconds=(min(part) - first) / fs)
            if self._consent(node) is not True or self._since_for(node) != since or self._subject_for(node) != subject:  # re-read now, not at the start of the round
                log.warning("%s: consent or subject changed while syncing; nothing more is sent this round", node)
                out["skipped"] = "changed"
                return out
            try:
                self._client.send(transaction(bundle(part, src, node, f"record {record_id}", record_id, t0), node, key))
            except FhirError as e:
                log.error("chunk %s not sent: %s", key, e)
                out["skipped"] = "error"
                return out
            self._sent[key] = print_
            out["sent"] += 1
        return out


def node_state(dsn: str, node: str) -> tuple:
    """(consent_ok, subject id, subject_since) of one node, read NOW from the registry. consent is None for a node with no subject (or an unknown node)."""
    import psycopg
    with psycopg.connect(dsn) as c:
        r = c.execute("SELECT s.consent_status = 'active', n.subject_id, n.subject_since FROM registry.node n LEFT JOIN registry.subject s ON s.id = n.subject_id WHERE n.id = %s", (node,)).fetchone()
    return tuple(r) if r else (None, None, None)


def load_records(path) -> "callable":
    """meta_for(record_id) from the JSON file tools/export_record_meta.py writes (fs, gain, baseline, channels per record). An unknown record is a KeyError the caller reports; nothing is guessed."""
    import numpy as np
    table = json.loads(Path(path).read_text())

    def meta_for(record_id):
        r = table[str(record_id)]
        return {"fs": np.float64(r["fs"]), "gain": np.array(r["gain"], dtype=float), "baseline": np.array(r["baseline"], dtype=int), "channels": np.array(r["channels"])}
    return meta_for


def main() -> None:
    """Run forever: every --interval seconds, sync every registered node."""
    import numpy as np
    import psycopg

    from eval.backend_client import query_frames

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=float(os.environ.get("FHIR_SYNC_INTERVAL", 60)))
    ap.add_argument("--since", default=os.environ.get("FHIR_SYNC_SINCE", "24h"))
    ap.add_argument("--converted-dir", default="data/converted/tpehgdb", help="folder with manifest.json and the .npz files (gain, baseline, channel names)")
    ap.add_argument("--records-file", default=os.environ.get("FHIR_RECORDS_FILE"), help="JSON from tools/export_record_meta.py; used INSTEAD of --converted-dir (this is how the cloud service runs)")
    a = ap.parse_args()
    dsn = os.environ["EHG_DB_DSN"]
    os.environ.setdefault("EHG_TS_DSN", dsn)  # eval/backend_client.py reads the timescale store from this name
    client = FhirClient(os.environ["FHIR_BASE_URL"], os.environ.get("FHIR_TOKEN"))

    def frames_for(node):
        try:
            return query_frames(node, a.since, backend="timescale")
        except SystemExit:  # query_frames says "no data" this way
            return None

    def state(node):
        return node_state(dsn, node)

    def nodes():
        with psycopg.connect(dsn) as c:
            return [r[0] for r in c.execute("SELECT id FROM registry.node ORDER BY id")]

    def meta_from_npz(record_id):
        d = Path(a.converted_dir)
        manifest = json.loads((d / "manifest.json").read_text())
        return np.load(d / next(e["file"] for e in manifest if e["record_id"] == record_id))  # same lookup as tools/convert.py:source_file, without its wfdb import

    meta_for = load_records(a.records_file) if a.records_file else meta_from_npz

    w = Worker(frames_for, meta_for, lambda n: state(n)[0], client, since_for=lambda n: state(n)[2], subject_for=lambda n: state(n)[1])
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            for n in nodes():  # a node with no subject has consent None and is not exported
                try:
                    log.info("%s: %s", n, w.sync_node(n))
                except KeyError as e:  # a record the metadata file does not know: this node is skipped and the others still sync
                    log.error("%s: no signal metadata for record %s (run tools/export_record_meta.py)", n, e)
        except psycopg.Error as e:
            log.error("registry unavailable: %s", type(e).__name__)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
