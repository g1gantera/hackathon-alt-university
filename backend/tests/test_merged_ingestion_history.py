import asyncio
import copy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.ingestion_client import IngestionClient
from backend.app.plan_comparison import comparison
from backend.app.storage import RecordBody, Store


def snapshot(epoch="a", version=1):
    return dict(
        epoch=epoch,
        state_version=version,
        sim_time_s=0.0,
        trains=[dict(id="T", position_m=0, speed_mps=0)],
        sections=[],
        switches=[],
        plan={"movements": []},
        metrics={"index": 90},
        active_plan_id="p",
    )


def test_ingestion_never_delivers_an_old_epoch_and_waits_for_normalized_reset(monkeypatch):
    monkeypatch.delenv("INGEST_URL", raising=False)

    async def check():
        received = []
        current = ["a", 1]
        client = IngestionClient(lambda s, *_: received.append(s), lambda: tuple(current))
        client.publish(snapshot())
        assert (await client.current_snapshot())["epoch"] == "a"
        current[:] = ["b", 0]
        client.accept(snapshot(), 0)
        assert len(received) == 1
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.current_snapshot(), 0.02)
        client.publish(snapshot("b", 0))
        assert (await client.current_snapshot())["epoch"] == "b"
        client.url = "http://unused"
        for version in range(1, 10):
            client.publish(snapshot("b", version))
        assert client.queue.qsize() == 1
        assert client.queue.get_nowait()[0]["payload"]["state_version"] == 9
        await client.close()

    asyncio.run(check())


def test_history_compression_reopen_frozen_manifest_and_retention(tmp_path):
    clock = [100000.0]
    url = f"sqlite:///{tmp_path / 'merged.sqlite'}"
    store = Store(url, clock=lambda: clock[0])
    large = snapshot()
    large["details"] = "Кокшетау " * 1000
    store.save("snapshot", "a", 0, large)
    original = copy.deepcopy(large)
    large["details"] = "mutated"
    store.flush()
    assert store.history("a", 0, 0) == [original]
    with Session(store.engine) as session:
        assert session.scalar(select(func.count()).select_from(RecordBody)) == 1
    window = store.archive_window("a")
    store.save("snapshot", "a", 1, snapshot(version=2))
    store.flush()
    assert store.archive_window("a", end=0, through_id=window["through_id"]) == window
    store.close()
    reopened = Store(url, clock=lambda: clock[0])
    assert reopened.archive_snapshot("a", window["frames"][0]["id"]) == original
    clock[0] += 73 * 3600
    reopened.maintain(force=True)
    assert reopened.history("a", 0, 10) == []
    with Session(reopened.engine) as session:
        assert session.scalar(select(func.count()).select_from(RecordBody)) == 0
    reopened.close()


def test_invalid_old_forecast_cannot_be_presented_as_measured_savings():
    old = dict(id="a", movements=[], metrics={"energy_kwh": 100}, forecast={})
    new = dict(id="b", movements=[], metrics={"energy_kwh": 80}, forecast={})
    state = dict(
        active_plan=old,
        awaiting_plan=True,
        epoch="r",
        state_version=1,
        constraint_version=1,
        sim_time_s=10,
    )
    result = comparison(state, new)
    assert result["before"] is None and not result["before_applicable"]
    assert result["after"]["energy_kwh"] == 80
