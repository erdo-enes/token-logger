#!/usr/bin/env python3
"""Import Go token-logger JSONL (data/calls.jsonl) into the Python SQLite schema."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from token_logger.storage import SCHEMA

COLS = ["ts", "client", "model", "endpoint", "host", "path", "status_code", "latency_ms",
        "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
        "total_tokens", "cost_usd", "request_json", "response_json", "error"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", nargs="?", default="data/calls.jsonl")
    parser.add_argument("--db", default="data/token_logger.db")
    args = parser.parse_args()
    path = Path(args.jsonl)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    sql = f"INSERT INTO calls ({','.join(COLS)}) VALUES ({','.join('?' for _ in COLS)})"
    count = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            conn.execute(sql, [rec.get(col) for col in COLS])
            count += 1
    conn.commit()
    conn.close()
    print(f"imported {count} rows -> {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
