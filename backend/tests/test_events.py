"""The workflow event bus, as a live subscriber sees it."""

from __future__ import annotations

import asyncio

import pytest

from app.models.enums import ExecutionEvent
from app.services.orchestrator import events


@pytest.fixture(autouse=True)
async def in_process_only(monkeypatch):
    """No Redis: the bus must work on its in-process fan-out alone."""

    async def no_redis():
        return None

    monkeypatch.setattr(events, "_get_redis", no_redis)


async def _collect(workflow_id: str, count: int) -> list[dict]:
    received: list[dict] = []
    async for event in events.subscribe(workflow_id):
        received.append(event)
        if len(received) == count:
            break
    return received


class TestSubscribe:
    async def test_the_first_event_says_the_subscription_is_live(self) -> None:
        stream = events.subscribe("w1")
        first = await anext(stream)
        assert first["event"] == "subscribed"
        # Registered by then: an event published now is not lost.
        await events.publish("w1", ExecutionEvent.WORKFLOW_STARTED)
        assert (await anext(stream))["event"] == "workflow_started"
        await stream.aclose()

    async def test_a_copy_arriving_by_a_second_route_is_dropped(self) -> None:
        """Regression: with Redis up, an in-process publisher reached each
        subscriber twice — once locally, once back through Redis — so every
        event appeared twice in the browser."""
        stream = events.subscribe("w2")
        await anext(stream)

        await events.publish("w2", ExecutionEvent.PLANNING_STARTED)
        queue = next(iter(events._local_subscribers["w2"]))
        original = await asyncio.wait_for(anext(stream), timeout=1)
        queue.put_nowait(dict(original))  # the same event, by the other route
        await events.publish("w2", ExecutionEvent.WORKFLOW_FAILED)

        after = await asyncio.wait_for(anext(stream), timeout=1)
        assert original["event"] == "planning_started"
        assert after["event"] == "workflow_failed"
        await stream.aclose()

    async def test_events_carry_distinct_ids(self) -> None:
        stream = events.subscribe("w3")
        await anext(stream)
        await events.publish("w3", ExecutionEvent.TASK_STARTED, {"task_key": "t1"})
        await events.publish("w3", ExecutionEvent.TASK_STARTED, {"task_key": "t1"})
        one = await asyncio.wait_for(anext(stream), timeout=1)
        two = await asyncio.wait_for(anext(stream), timeout=1)
        # Identical content is still two events, not a duplicate.
        assert one["id"] != two["id"]
        await stream.aclose()

    async def test_closing_the_stream_unregisters_it(self) -> None:
        stream = events.subscribe("w4")
        await anext(stream)
        assert "w4" in events._local_subscribers
        await stream.aclose()
        assert "w4" not in events._local_subscribers
