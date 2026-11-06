import time

from token_logger.storage import AsyncWriter, CallRecord


def test_roundtrip(tmp_path):
    db = str(tmp_path / "test.db")
    writer = AsyncWriter(db)
    writer.start()
    writer.put(CallRecord(ts="2026-01-01T00:00:00+00:00", model="claude-sonnet-4-5", prompt_tokens=1, completion_tokens=2, total_tokens=3))
    writer.stop()
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT model, total_tokens FROM calls").fetchone()
    assert row == ("claude-sonnet-4-5", 3)
    time.sleep(0.1)
