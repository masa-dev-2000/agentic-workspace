#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("ADAPTIVE_ORCHESTRATOR_HOME", Path.home() / ".codex" / "adaptive-orchestrator")) / "usage.sqlite3"

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT NOT NULL, task_id TEXT, role TEXT NOT NULL,
      model TEXT, provider TEXT, input_tokens INTEGER, output_tokens INTEGER,
      total_tokens INTEGER, latency_ms INTEGER, cost REAL, created_at TEXT NOT NULL
    )""")
    return db

def record(args):
    with connect() as db:
        cur = db.execute("""INSERT INTO usage_events
          (job_id, task_id, role, model, provider, input_tokens, output_tokens,
           total_tokens, latency_ms, cost, created_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (args.job_id, args.task_id, args.role, args.model, args.provider,
           args.input_tokens, args.output_tokens,
           args.total_tokens if args.total_tokens is not None else
           ((args.input_tokens or 0) + (args.output_tokens or 0) if args.input_tokens is not None or args.output_tokens is not None else None),
           args.latency_ms, args.cost, datetime.now(timezone.utc).isoformat()))
    print(json.dumps({"id": cur.lastrowid, "database": str(DB)}, ensure_ascii=False))

def list_events(args):
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM usage_events WHERE (? IS NULL OR job_id = ?) ORDER BY id DESC",
            (args.job_id, args.job_id)).fetchall()]
    print(json.dumps({"events": rows}, ensure_ascii=False, indent=2))

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
r = sub.add_parser("record")
r.add_argument("--job-id", required=True); r.add_argument("--task-id")
r.add_argument("--role", required=True); r.add_argument("--model"); r.add_argument("--provider")
r.add_argument("--input-tokens", type=int); r.add_argument("--output-tokens", type=int)
r.add_argument("--total-tokens", type=int); r.add_argument("--latency-ms", type=int); r.add_argument("--cost", type=float)
r.set_defaults(func=record)
l = sub.add_parser("list"); l.add_argument("--job-id"); l.set_defaults(func=list_events)
args = parser.parse_args(); args.func(args)
