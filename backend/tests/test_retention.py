import copy

import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session

from backend.app.simulator import Simulator
from backend.app.storage import Store,Record,RecordBody
from backend.app.reports import csv_report


@pytest.mark.parametrize('hours',[24,48,72])
def test_retention_boundary_idle_pruning_restart_and_no_orphan_bodies(tmp_path,monkeypatch,hours):
    monkeypatch.setenv('RETENTION_HOURS',str(hours))
    now=[1_800_000_000.0]
    store=Store(f'sqlite:///{tmp_path / "retention.sqlite"}',clock=lambda:now[0])
    snapshot=Simulator().snapshot()
    epoch=snapshot['epoch']
    store.save('snapshot',epoch,0,snapshot)
    window=store.archive_window(epoch)
    record_id=window['frames'][0]['id']
    assert store.archive_snapshot(epoch,record_id)==snapshot
    csv=''.join(csv_report(store,window))
    store.engine.dispose()
    store=Store(f'sqlite:///{tmp_path / "retention.sqlite"}',clock=lambda:now[0])
    now[0]+=hours*3600
    store.maintain(force=True)
    assert store.archive_snapshot(epoch,record_id)==snapshot  # boundary inclusive
    assert ''.join(csv_report(store,window))==csv
    now[0]+=.001
    store.maintain(force=True)  # paused: no new save needed
    assert store.archive_window(epoch) is None
    with Session(store.engine) as session:
        assert session.scalar(select(func.count()).select_from(Record))==0
        assert session.scalar(select(func.count()).select_from(RecordBody))==0
    store.engine.dispose()


def test_legacy_and_compressed_records_share_query_and_csv_contract(tmp_path):
    store=Store(f'sqlite:///{tmp_path / "migration.sqlite"}')
    original=Simulator().snapshot()
    epoch=original['epoch']
    with Session(store.engine) as session:
        session.add(Record(kind='snapshot',epoch=epoch,sim_time=0,payload=original,created_at=store.clock()))
        session.commit()
    updated=copy.deepcopy(original)
    updated['state_version']=1
    store.save('snapshot',epoch,0,updated)
    assert store.history(epoch,0,0)==[original,updated]
    window=store.archive_window(epoch)
    assert len(window['frames'])==2
    assert store.archive_snapshot(epoch,window['frames'][1]['id'])==updated
    rows=list(store.archive_records(window))
    assert [row.payload for row in rows]==[original,updated]
    with Session(store.engine) as session:
        body=session.scalar(select(RecordBody.compressed))
        assert len(body)<len(str(original))*.4
    assert store.statistics()['payload_codec']=='zlib-v1'
    store.engine.dispose()
