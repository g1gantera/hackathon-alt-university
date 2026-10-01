import os
import copy
from concurrent.futures import ThreadPoolExecutor
import time
from sqlalchemy import create_engine, Column, Integer, Float, String, JSON, delete, select
from sqlalchemy.orm import DeclarativeBase, Session
from .domain import ROOT


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = 'records'
    id = Column(Integer,primary_key=True)
    created_at = Column(Float,index=True)
    epoch = Column(String,index=True)
    kind = Column(String,index=True)
    sim_time = Column(Float,index=True)
    payload = Column(JSON)


class Store:
    def __init__(self, url=None):
        (ROOT/'data').mkdir(exist_ok=True)
        self.engine = create_engine(url or os.environ.get('DATABASE_URL',f'sqlite:///{ROOT / "data/dispatch.sqlite"}'),connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.retention_hours = max(24,min(72,int(os.environ.get('RETENTION_HOURS','48'))))
        self.last_prune = 0
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-writer")
        self.pending = []

    def save(self,kind,epoch,sim_time,payload):
        # Freeze the public record, never copy/serialize private speed-profile arrays.
        for future in self.pending:
            if future.done():
                future.result()  # surface storage failures instead of silently losing history
        self.pending = [f for f in self.pending if not f.done()]
        self.pending.append(self.writer.submit(self._save, kind, epoch, sim_time,
                                                copy.deepcopy(payload), time.time()))

    def flush(self):
        for future in tuple(self.pending):
            future.result()

    def close(self):
        self.flush()
        self.writer.shutdown(wait=True)
        self.engine.dispose()

    def _save(self,kind,epoch,sim_time,payload,created_at):
        now = time.time()
        with Session(self.engine) as session:
            session.add(Record(kind=kind,epoch=epoch,sim_time=sim_time,payload=payload,created_at=created_at))
            if now-self.last_prune>60:
                session.execute(delete(Record).where(Record.created_at<now-self.retention_hours*3600))
                self.last_prune=now
            session.commit()

    def history(self,epoch,start,end):
        with Session(self.engine) as session:
            rows = session.scalars(select(Record).where(Record.epoch==epoch,Record.kind=='snapshot',Record.sim_time>=start,Record.sim_time<=end).order_by(Record.id)).all()
            return [r.payload for r in rows]
