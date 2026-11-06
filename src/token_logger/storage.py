from __future__ import annotations

import json
import queue
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  client TEXT, model TEXT, endpoint TEXT, host TEXT, path TEXT,
  status_code INTEGER, latency_ms REAL,
  prompt_tokens INTEGER, completion_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_tokens INTEGER, total_tokens INTEGER,
  cost_usd REAL,
  request_json TEXT, response_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_calls_model ON calls(model);
"""


@dataclass
class CallRecord:
    ts: str
    client: str = ""
    model: str = ""
    endpoint: str = ""
    host: str = ""
    path: str = ""
    status_code: int | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    request_json: str = ""
    response_json: str = ""
    error: str = ""

    def as_row(self) -> dict:
        row = asdict(self)
        return row


class AsyncWriter:
    def __init__(self, db_path: str, max_queue: int = 1000, batch_size: int = 50, flush_interval: float = 0.5):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.queue: queue.Queue[CallRecord] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="token-logger-writer", daemon=True)
        self.dropped = 0

    def start(self) -> None:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self._thread.start()

    def put(self, record: CallRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        cols = list(CallRecord("").as_row().keys())
        sql = f"INSERT INTO calls ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
        while not self._stop.is_set() or not self.queue.empty():
            batch: list[CallRecord] = []
            try:
                batch.append(self.queue.get(timeout=self.flush_interval))
                while len(batch) < self.batch_size:
                    try:
                        batch.append(self.queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
            try:
                conn.executemany(sql, [tuple(r.as_row()[c] for c in cols) for r in batch])
                conn.commit()
            except sqlite3.Error:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
        conn.close()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class CompositeWriter:
    def __init__(self, writers):
        self.writers = list(writers)

    def start(self) -> None:
        for writer in self.writers:
            try:
                writer.start()
            except Exception:
                pass

    def put(self, record: CallRecord) -> None:
        for writer in self.writers:
            try:
                writer.put(record)
            except Exception:
                pass

    def stop(self) -> None:
        for writer in self.writers:
            try:
                writer.stop()
            except Exception:
                pass


def dump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)
