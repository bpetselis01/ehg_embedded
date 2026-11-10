"""An idle JetStream consumer must not crash its service. nats-py's fetch() raises the BUILT-IN TimeoutError when nothing arrives, and the code used to catch only
nats.errors.TimeoutError (a SUBCLASS of the built-in one, so it does not catch its parent): ts-writer and triage crashed on an idle stream on the first start of the cloud
compose file (2026-11-04). Tests that raised nats.errors.TimeoutError themselves could never see it."""

import asyncio
import re
from pathlib import Path

import nats.errors
import pytest

from backend import bus

ROOT = Path(__file__).parents[1]


class FakeSub:
    def __init__(self, outcome):
        self.outcome = outcome

    async def fetch(self, batch, timeout):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("raised", [TimeoutError(), asyncio.TimeoutError(), nats.errors.TimeoutError()])
def test_an_idle_stream_gives_an_empty_batch_whichever_timeout_class_nats_raises(raised):
    assert asyncio.run(bus.fetch_batch(FakeSub(raised), 10)) == []


def test_messages_pass_through_and_other_errors_still_raise():
    assert asyncio.run(bus.fetch_batch(FakeSub(["a", "b"]), 10)) == ["a", "b"]
    with pytest.raises(nats.errors.ConnectionClosedError):
        asyncio.run(bus.fetch_batch(FakeSub(nats.errors.ConnectionClosedError()), 10))


def test_the_class_relation_that_caused_it_is_still_true():
    assert issubclass(nats.errors.TimeoutError, TimeoutError) and not issubclass(TimeoutError, nats.errors.TimeoutError)


def test_no_consumer_loop_catches_only_the_subclass():
    for rel in ("backend/ts_writer/writer.py", "backend/triage/stream.py", "backend/ingest/ingest.py", "backend/bridge/bridge.py"):
        text = (ROOT / rel).read_text()
        assert not re.search(r"except nats\.errors\.TimeoutError", text), f"{rel} catches only the subclass"
