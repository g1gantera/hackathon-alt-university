"""Accelerated 24/48/72-hour retention and a dense 2 Hz playback window.

The storage clock advances deterministically; this is explicitly NOT a 72-hour
wall-clock soak. Databases are isolated under .logs for inspection afterwards.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from backend.app.simulator import Simulator
from backend.app.storage import Store,Record,RecordBody
from backend.app.reports import csv_report


def verify(directory,step):
    directory.mkdir(parents=True,exist_ok=False)
    snapshot=Simulator().snapshot()
    epoch=snapshot['epoch']
    cases=[]
    for hours in (24,48,72):
        os.environ['RETENTION_HOURS']=str(hours)
        now=[1_800_000_000.0]
        url='sqlite:///'+(directory/f'{hours}h.sqlite').resolve().as_posix()
        store=Store(url,clock=lambda:now[0])
        began=time.perf_counter()
        total=int((hours+2)*3600/step)+1
        for index in range(total):
            now[0]=1_800_000_000.0+index*step
            state={**snapshot,'state_version':index,'sim_time_s':index}
            store.save('snapshot',epoch,index,state)
        while store.maintain(force=True)==1000:
            pass
        with Session(store.engine) as session:
            retained=session.scalar(select(func.count()).select_from(Record))
            expired=session.scalar(select(func.count()).select_from(Record).where(Record.created_at<now[0]-hours*3600))
            blobs=session.scalar(select(func.count()).select_from(RecordBody))
            mean_body=session.scalar(select(func.avg(func.length(RecordBody.compressed))))
        assert expired==0 and retained==blobs==int(hours*3600/step)+1
        window=store.archive_window(epoch)
        frozen=''.join(csv_report(store,window))
        store.engine.dispose()
        store=Store(url,clock=lambda:now[0])
        assert ''.join(csv_report(store,window))==frozen
        now[0]+=(hours*3600)+1
        while store.maintain(force=True)==1000:
            pass
        assert store.runs()==[] and store.archive_window(epoch) is None
        with Session(store.engine) as session:
            assert session.scalar(select(func.count()).select_from(RecordBody))==0
        stats=store.statistics()
        store.engine.dispose()
        cases.append({'retention_hours':hours,'records_written':total,'retained_at_boundary':retained,
                      'expired_rows':expired,'restart_export_identical':True,'idle_pruning':True,
                      'orphan_blobs':0,'mean_compressed_body_bytes':round(mean_body,1),
                      'elapsed_s':round(time.perf_counter()-began,2),'after_idle_prune':stats})
        print(json.dumps(cases[-1]),flush=True)

    os.environ['RETENTION_HOURS']='72'
    now=[1_800_000_000.0]
    store=Store('sqlite:///'+(directory/'dense.sqlite').resolve().as_posix(),clock=lambda:now[0])
    durations=[]
    for index in range(1801):
        now[0]=1_800_000_000.0+index*.5
        state=copy.deepcopy(snapshot)
        state.update(state_version=index,sim_time_s=index//2)
        state['trains'][0]['position_m']=index*1.5
        started=time.perf_counter()
        store.save('snapshot',epoch,index//2,state)
        durations.append((time.perf_counter()-started)*1000)
    started=time.perf_counter()
    window=store.archive_window(epoch,15)
    query_ms=(time.perf_counter()-started)*1000
    assert len(window['frames'])==1801
    for frame in (window['frames'][0],window['frames'][900],window['frames'][-1]):
        assert store.archive_snapshot(epoch,frame['id'])['state_version']==frame['state_version']
    dense={**store.statistics(),'samples':1801,'sample_hz':2,'window_query_ms':round(query_ms,2),
           'median_write_ms':round(statistics.median(durations),2),'max_write_ms':round(max(durations),2)}
    store.engine.dispose()
    return {'status':'passed','mode':'accelerated_storage_clock','calendar_sample_seconds':step,
            'wall_clock_72h_soak':False,'cases':cases,'dense_window':dense}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-seconds',type=float,default=30)
    parser.add_argument('--output',type=Path,default=Path('.logs/retention-results.json'))
    args=parser.parse_args()
    if args.sample_seconds<=0 or (3600/args.sample_seconds)%1:
        parser.error('--sample-seconds must evenly divide 3600')
    result=verify(Path('.logs')/('retention-'+uuid.uuid4().hex[:8]),args.sample_seconds)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
