from __future__ import annotations

import argparse
import sqlite3
import sys

from .config import Config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--model", default="")
    parser.add_argument("--db", default="")
    args = parser.parse_args()
    db = args.db or Config.load().db_path
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sql = "SELECT ts, client, model, status_code, latency_ms, prompt_tokens, completion_tokens, total_tokens, cost_usd FROM calls"
    params: list = []
    if args.model:
        sql += " WHERE model LIKE ?"
        params.append(f"%{args.model}%")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(args.last)
    for row in conn.execute(sql, params):
        print(dict(row))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
