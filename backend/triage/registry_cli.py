"""Command line for the registry: add subjects, set consent, assign nodes. Every change is written to the audit log.

PURPOSE: Until now subjects and consent could only be changed with hand-written SQL. Consent decides what the FHIR sync worker exports and what the FHIR facade shows, so changing it needs a trail. Nothing here reads or stores a person's name: a subject is a CODE (the pseudonym).
INPUT: EHG_TRIAGE_DSN (a libpq connection string; the triage role may write the registry and append to the audit log). e.g. on the VM:
    docker exec -i ehg-triage-1 python -m backend.triage.registry_cli subject add tpehg1007 --pseudonym P-0001 --source-dataset tpehgdb
    docker exec -i ehg-triage-1 python -m backend.triage.registry_cli consent tpehg1007 active
    docker exec -i ehg-triage-1 python -m backend.triage.registry_cli node EHG-93C0 --subject tpehg1007 --gateway ehg-gw-01
    docker exec -i ehg-triage-1 python -m backend.triage.registry_cli list
OUTPUT: the changed row as JSON, or the lists for `list`; a refusal (unknown subject, bad id, bad status) prints a message and exits 1. Nothing is changed on a refusal.
HOW: thin wrapper over backend/triage/registry.py (which does the audited writes). The actor is `cli:<user>` with role `admin`: the person who can run this has shell access to the VM, which is the same trust as the admin role. Withdrawing consent stops further exports and reads; it does not recall what HAPI already holds.
ROLE IN THESIS: The missing piece for consent: a data-model demonstration, not a legal consent process (docs/security.md).
BEFORE -> AFTER: Before: SQL. After: a command with an audit trail.
"""

import argparse
import getpass
import json
import os
import sys

from backend.triage.registry import CONSENT_STATES, Registry, UnknownGateway, UnknownSubject


def main(argv=None, registry: Registry | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--actor", default=f"cli:{getpass.getuser()}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("subject").add_subparsers(dest="sub", required=True).add_parser("add")
    s.add_argument("id")
    s.add_argument("--pseudonym", required=True)
    s.add_argument("--source-dataset", required=True)
    s.add_argument("--consent", choices=CONSENT_STATES, default="none")
    c = sub.add_parser("consent")
    c.add_argument("subject")
    c.add_argument("status", choices=CONSENT_STATES)
    n = sub.add_parser("node")
    n.add_argument("id")
    n.add_argument("--subject")
    n.add_argument("--gateway")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if registry is None:
        import psycopg

        from backend.triage.audit import AuditLog
        dsn = os.environ.get("EHG_TRIAGE_DSN")
        if not dsn:
            print("set EHG_TRIAGE_DSN", file=sys.stderr)
            return 1
        connect = lambda: psycopg.connect(dsn, connect_timeout=5)  # noqa: E731
        registry = Registry(connect, AuditLog(connect))
    try:
        if a.cmd == "subject":
            out = registry.add_subject(a.id, a.pseudonym, a.source_dataset, a.actor, "admin", a.consent)
        elif a.cmd == "consent":
            out = registry.set_consent(a.subject, a.status, a.actor, "admin")
        elif a.cmd == "node":
            if a.subject is None and a.gateway is None:
                print("give --subject and/or --gateway", file=sys.stderr)
                return 1
            out = registry.assign_node(a.id, a.actor, "admin", a.subject, a.gateway)
        else:
            out = {"subjects": registry.subjects(), "nodes": registry.nodes(), "gateways": registry.gateways()}
    except (ValueError, UnknownSubject, UnknownGateway) as e:
        print(f"refused: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # a duplicate subject id, an unreachable database: say so without a traceback or the connection string
        print(f"failed: {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(out, default=str, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
