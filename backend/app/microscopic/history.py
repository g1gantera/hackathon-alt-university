"""SQLite journal and replay snapshots, plus a bounded latest-state event bus."""
import asyncio
import json
import sqlite3
import time
import uuid
from collections import deque


class History:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,wall REAL,sim REAL,kind TEXT,actor TEXT,entities TEXT,before_json TEXT,after_json TEXT,message TEXT,run TEXT);
        CREATE INDEX IF NOT EXISTS event_time ON events(wall);
        CREATE TABLE IF NOT EXISTS snapshots(run TEXT,sim REAL,wall REAL,body TEXT,PRIMARY KEY(run,sim));
        CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(wall);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,body TEXT);
        ''')
        self.recent = deque(maxlen=150)
        self.run = uuid.uuid4().hex[:12]
        self.last_prune = 0

    def log(self, sim, kind, message, entities=(), actor='engine', before=None, after=None):
        e = dict(id=uuid.uuid4().hex, wall_time=time.time(), sim_time=round(sim,3), kind=kind, actor=actor, entities=list(entities), before=before, after=after, message=message, run=self.run)
        self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',(e['id'],e['wall_time'],e['sim_time'],kind,actor,json.dumps(e['entities']),json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),message,self.run))
        self.db.commit()
        self.recent.append(e)
        return e

    def query(self, search='', kind='', limit=300, run=None):
        rows=self.db.execute('SELECT * FROM events WHERE (?="" OR kind=?) AND (?="" OR message LIKE ? OR entities LIKE ?) AND (? IS NULL OR run=?) ORDER BY wall DESC LIMIT ?', (kind,kind,search,'%'+search+'%','%'+search+'%',run,run,limit)).fetchall()
        return [dict(id=r[0],wall_time=r[1],sim_time=r[2],kind=r[3],actor=r[4],entities=json.loads(r[5]),before=json.loads(r[6]),after=json.loads(r[7]),message=r[8],run=r[9]) for r in rows]

    def snapshot(self, state, retention_hours=48):
        self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)',(self.run,state['sim_time'],time.time(),json.dumps(state,separators=(',',':'),ensure_ascii=False)))
        if time.time()-self.last_prune>60:
            cutoff=time.time()-retention_hours*3600
            self.db.execute('DELETE FROM events WHERE wall<?',(cutoff,))
            self.db.execute('DELETE FROM snapshots WHERE wall<?',(cutoff,))
            self.last_prune=time.time()
        self.db.commit()

    def replay(self, start, end, run=None, limit=901):
        # At most one sample per simulation second, at most 15 minutes per request.
        rows=self.db.execute('SELECT body FROM snapshots WHERE run=? AND sim>=? AND sim<=? ORDER BY sim LIMIT ?', (run or self.run,start,end,limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def runs(self):
        return [dict(run=r[0],start_s=r[1],end_s=r[2],frames=r[3]) for r in self.db.execute('SELECT run,MIN(sim),MAX(sim),COUNT(*) FROM snapshots GROUP BY run ORDER BY MAX(wall) DESC LIMIT 20')]

    def load_settings(self):
        row=self.db.execute('SELECT body FROM settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save_settings(self, config):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps(config),))
        self.db.commit()


class EventBus:
    def __init__(self):
        self.subscribers=set()
        self.dropped=0

    def subscribe(self):
        q=asyncio.Queue(maxsize=2)
        self.subscribers.add(q)
        return q

    def publish(self, state):
        for q in tuple(self.subscribers):
            if q.full():
                q.get_nowait()
                self.dropped+=1
            q.put_nowait(state)
