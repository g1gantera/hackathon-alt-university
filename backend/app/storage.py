import copy
import json
import os
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    create_engine,
    delete,
    event,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session

from .domain import ROOT


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_run_kind_time", "epoch", "kind", "sim_time"),
        {"sqlite_autoincrement": True},
    )
    id = Column(Integer, primary_key=True)
    created_at = Column(Float, index=True)
    epoch = Column(String, index=True)
    kind = Column(String, index=True)
    sim_time = Column(Float, index=True)
    payload = Column(JSON)


class RecordBody(Base):
    __tablename__ = "record_bodies"
    record_id = Column(Integer, ForeignKey("records.id", ondelete="CASCADE"), primary_key=True)
    compressed = Column(LargeBinary, nullable=False)


def pack(payload):
    """Keep query metadata small and full observations lossless, including old CSVs."""
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) < 1024:
        return payload, None
    metadata = {
        key: payload[key] for key in ("state_version", "active_plan_id", "type") if key in payload
    }
    if "metrics" in payload:
        metadata["metrics"] = {
            key: payload["metrics"].get(key) for key in ("index", "quality_signature")
        }
    if isinstance(payload.get("payload"), dict):
        metadata["payload"] = {
            key: payload["payload"].get(key) for key in ("kind", "target_id", "action")
        }
    metadata["_storage_codec"] = "zlib-v1"
    return metadata, zlib.compress(encoded, level=3)


def unpack(metadata, compressed):
    if metadata.get("_storage_codec") != "zlib-v1":
        return metadata  # Existing databases stay readable without rewriting history.
    if compressed is None:
        raise RuntimeError("Missing compressed history body")
    return json.loads(zlib.decompress(compressed))


class Store:
    def __init__(self, url=None, *, clock=None):
        (ROOT / "data").mkdir(exist_ok=True)
        self.engine = create_engine(
            url or os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'data/dispatch.sqlite'}"),
            connect_args={"check_same_thread": False},
        )
        self.clock = clock or (lambda: time.time())

        @event.listens_for(self.engine, "connect")
        def sqlite_options(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            if not connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
            ).first():
                connection.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")
        Base.metadata.create_all(self.engine)
        # create_all skips new indexes on a table that already exists.
        next(
            index for index in Record.__table__.indexes if index.name == "ix_records_run_kind_time"
        ).create(self.engine, checkfirst=True)
        self.retention_hours = max(24, min(72, int(os.environ.get("RETENTION_HOURS", "48"))))
        self.last_prune = float("-inf")
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-writer")
        self.pending = []

    def save(self, kind, epoch, sim_time, payload):
        self.pending = [f for f in self.pending if not self._finished(f)]
        self.pending.append(
            self.writer.submit(
                self._save, kind, epoch, sim_time, copy.deepcopy(payload), self.clock()
            )
        )

    @staticmethod
    def _finished(future):
        if future.done():
            future.result()
            return True
        return False

    def flush(self):
        for future in tuple(self.pending):
            future.result()

    def close(self):
        try:
            self.flush()
        finally:
            self.writer.shutdown(wait=True)
            self.engine.dispose()

    def heartbeat(self):
        if self.clock() - self.last_prune >= 60 and not any(not f.done() for f in self.pending):
            self.pending.append(self.writer.submit(self.maintain))

    def _save(self, kind, epoch, sim_time, payload, created_at):
        now = created_at
        metadata, compressed = pack(payload)
        with Session(self.engine) as session:
            record = Record(
                kind=kind, epoch=epoch, sim_time=sim_time, payload=metadata, created_at=now
            )
            session.add(record)
            session.flush()
            if compressed is not None:
                session.add(RecordBody(record_id=record.id, compressed=compressed))
            session.commit()
        self.maintain()

    def maintain(self, *, force=False):
        """Bounded pruning also runs from the heartbeat while paused. No full VACUUM."""
        now = self.clock()
        if not force and now - self.last_prune < 60:
            return 0
        with Session(self.engine) as session:
            ids = list(
                session.scalars(
                    select(Record.id)
                    .where(Record.created_at < now - self.retention_hours * 3600)
                    .limit(1000)
                )
            )
            if ids:
                session.execute(delete(Record).where(Record.id.in_(ids)))
            session.commit()
        self.last_prune = now if len(ids) < 1000 else now - 60
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA incremental_vacuum(32)")
        return len(ids)

    def statistics(self):
        with self.engine.connect() as connection:
            page_size = connection.exec_driver_sql("PRAGMA page_size").scalar()
            pages = connection.exec_driver_sql("PRAGMA page_count").scalar()
            free = connection.exec_driver_sql("PRAGMA freelist_count").scalar()
        return {
            "retention_hours": self.retention_hours,
            "allocated_bytes": pages * page_size,
            "reusable_bytes": free * page_size,
            "payload_codec": "zlib-v1",
            "last_maintenance_at": self.last_prune if self.last_prune != float("-inf") else None,
        }

    def history(self, epoch, start, end):
        with Session(self.engine) as session:
            rows = session.execute(
                select(Record.payload, RecordBody.compressed)
                .outerjoin(RecordBody)
                .where(
                    *self.archive_filter(epoch),
                    Record.kind == "snapshot",
                    Record.sim_time >= start,
                    Record.sim_time <= end,
                )
                .order_by(Record.id)
            ).all()
            return [unpack(metadata, body) for metadata, body in rows]

    def archive_filter(self, epoch):
        return (
            Record.epoch == epoch,
            Record.created_at >= self.clock() - self.retention_hours * 3600,
        )

    def runs(self):
        with Session(self.engine) as session:
            return [
                dict(row)
                for row in session.execute(
                    select(
                        Record.epoch,
                        func.min(Record.sim_time).label("first_time_s"),
                        func.max(Record.sim_time).label("last_time_s"),
                        func.max(Record.created_at).label("saved_at"),
                        func.count().label("snapshot_count"),
                    )
                    .where(
                        Record.kind == "snapshot",
                        Record.created_at >= self.clock() - self.retention_hours * 3600,
                    )
                    .group_by(Record.epoch)
                    .order_by(func.max(Record.id).desc())
                    .limit(20)
                ).mappings()
            ]

    def archive_window(self, epoch, minutes=15, end=None, through_id=None):
        """Freeze a compact manifest; full snapshots are fetched individually."""
        with Session(self.engine) as session:
            filters = list(self.archive_filter(epoch))
            if through_id is not None:
                filters.append(Record.id <= through_id)
            latest = session.scalar(
                select(Record)
                .where(*filters, Record.kind == "snapshot")
                .order_by(Record.id.desc())
                .limit(1)
            )
            if latest is None:
                return None
            anchor = session.scalar(select(func.max(Record.id)).where(*filters))
            end = min(latest.sim_time, end) if end is not None else latest.sim_time
            start = max(0, end - minutes * 60)
            filters.extend((Record.id <= anchor, Record.sim_time >= start, Record.sim_time <= end))
            frames = [
                dict(row)
                for row in session.execute(
                    select(
                        Record.id,
                        Record.sim_time.label("sim_time_s"),
                        Record.created_at.label("saved_at"),
                        Record.payload["state_version"].as_integer().label("state_version"),
                        Record.payload["metrics"]["index"].as_float().label("quality_index"),
                        Record.payload["active_plan_id"].as_string().label("plan_id"),
                    )
                    .where(*filters, Record.kind == "snapshot")
                    .order_by(Record.id)
                ).mappings()
            ]
            # Don't send solver candidates or full comparison payloads to the timeline.
            events = [
                dict(row)
                for row in session.execute(
                    select(
                        Record.id,
                        Record.sim_time.label("sim_time_s"),
                        Record.payload["state_version"].as_integer().label("state_version"),
                        Record.payload["type"].as_string().label("type"),
                        Record.payload["payload"]["kind"].as_string().label("kind"),
                        Record.payload["payload"]["target_id"].as_string().label("target_id"),
                        Record.payload["payload"]["action"].as_string().label("action"),
                    )
                    .where(*filters, Record.kind == "event")
                    .order_by(Record.id)
                ).mappings()
            ]
            return {
                "epoch": epoch,
                "from_s": start,
                "to_s": end,
                "through_id": anchor,
                "minutes": minutes,
                "retention_hours": self.retention_hours,
                "frames": frames,
                "events": events,
            }

    def archive_snapshot(self, epoch, record_id):
        with Session(self.engine) as session:
            row = session.execute(
                select(Record.payload, RecordBody.compressed)
                .outerjoin(RecordBody)
                .where(
                    *self.archive_filter(epoch), Record.kind == "snapshot", Record.id == record_id
                )
            ).first()
            return unpack(*row) if row else None

    def archive_records(self, window):
        """Bounded batches keep exports from loading all train histories into RAM."""
        last_id = 0
        while True:
            with Session(self.engine) as session:
                rows = session.execute(
                    select(Record, RecordBody.compressed)
                    .outerjoin(RecordBody)
                    .where(
                        *self.archive_filter(window["epoch"]),
                        Record.id > last_id,
                        Record.id <= window["through_id"],
                        Record.kind.in_(("snapshot", "event")),
                        Record.sim_time >= window["from_s"],
                        Record.sim_time <= window["to_s"],
                    )
                    .order_by(Record.id)
                    .limit(25)
                ).all()
            if not rows:
                return
            for row, body in rows:
                row.payload = unpack(row.payload, body)
                yield row
            last_id = rows[-1][0].id

    def quality_history(self, epoch, start, end, signature, max_points=180):
        """Project only chart data; don't load train/geometry payloads for this view."""
        with Session(self.engine) as session:
            rows = (
                session.execute(
                    select(
                        Record.sim_time.label("sim_time_s"),
                        Record.payload["state_version"].as_integer().label("state_version"),
                        Record.payload["metrics"]["index"].as_float().label("index"),
                    )
                    .where(
                        *self.archive_filter(epoch),
                        Record.kind == "snapshot",
                        Record.sim_time >= start,
                        Record.sim_time <= end,
                        Record.payload["metrics"]["quality_signature"].as_string() == signature,
                    )
                    .order_by(Record.id)
                )
                .mappings()
                .all()
            )
            points = [dict(row) for row in rows]
        # Bucket extrema preserve incident drops, recovery, and the latest sample.
        if len(points) > max_points:
            interior = points[1:-1]
            buckets = max(1, (max_points - 2) // 2)
            indices = {0, len(points) - 1}
            for bucket in range(buckets):
                a = 1 + bucket * len(interior) // buckets
                b = 1 + (bucket + 1) * len(interior) // buckets
                group = range(a, b)
                if a < b:
                    indices.add(min(group, key=lambda i: points[i]["index"]))
                    indices.add(max(group, key=lambda i: points[i]["index"]))
            points = [points[i] for i in sorted(indices)]
        return points
