from __future__ import annotations
import hashlib, json, sqlite3, sys, importlib.util, os
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get("AO_ORCHESTRATION_DB", str(ROOT/"orchestration.sqlite3")))
HOOK_REVISION="orchestration-hook-v2"
CONTEXT="""Adaptive Orchestrator is active for this request.
Mandatory workflow:
PLAN -> REVIEW -> IMPLEMENT -> VERIFY -> REPORT.
For non-trivial work, create a structured plan, run an independent review, implement only the reviewed plan, verify with observable evidence, and report roles, model/provider class, duration, tokens, cost, retries, and evidence. Use unavailable when runtime data is not exposed. Only fatal operations require human approval. The LLM may propose routing but cannot grant authority or bypass policy.
"""
def stamp(): return datetime.now(timezone.utc).isoformat()
def h(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()
def main():
    try:
        event=json.loads(sys.stdin.read().lstrip("﻿"))
        if not isinstance(event,dict): return 0
        prompt=next((event.get(k) for k in ("prompt","user_prompt","message") if isinstance(event.get(k),str)),"")
        if not prompt: return 0
        session=str(event.get("session_id") or "unknown"); turn=str(event.get("turn_id") or "unknown"); cwd=str(event.get("cwd") or "")
        ingress_id=str(event.get("event_id") or h("|".join((session,turn,cwd,prompt))))
        job_id=h("|".join((session,turn,cwd,ingress_id)))[:32]
        DB.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,cwd_hash TEXT NOT NULL,prompt_hash TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS stages(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,role TEXT,model_class TEXT,started_at TEXT,ended_at TEXT,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,cost REAL,evidence_ref TEXT)")
            conn.execute("INSERT OR IGNORE INTO jobs(job_id,session_hash,turn_hash,cwd_hash,prompt_hash,stage,status,started_at,updated_at) VALUES(?,?,?,?,?,'plan','running',?,?)",(job_id,h(session),h(turn),h(cwd),h(prompt),stamp(),stamp()))
            now=int(conn.execute("SELECT strftime('%s','now')").fetchone()[0])
            conn.execute("CREATE TABLE IF NOT EXISTS ao_jobs(job_id TEXT PRIMARY KEY,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,cwd_hash TEXT NOT NULL,prompt_hash TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,plan_digest TEXT,plan_revisions INTEGER NOT NULL DEFAULT 0,max_plan_revisions INTEGER NOT NULL DEFAULT 2,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS ao_hook_ingress(ingress_id TEXT PRIMARY KEY,job_id TEXT NOT NULL UNIQUE,hook_revision TEXT NOT NULL,created_at INTEGER NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO ao_hook_ingress(ingress_id,job_id,hook_revision,created_at) VALUES(?,?,?,?)",(ingress_id,job_id,HOOK_REVISION,now))
            conn.execute("INSERT OR IGNORE INTO ao_jobs(job_id,session_hash,turn_hash,cwd_hash,prompt_hash,state,created_at,updated_at) VALUES(?,?,?,?,?,'planning',?,?)",(job_id,h(session),h(turn),h(cwd),h(prompt),now,now))
            conn.commit()
            stage_path=ROOT/"stage_runner.py"
            spec=importlib.util.spec_from_file_location("adaptive_stage_runner_hook",stage_path)
            if spec and spec.loader:
                runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner); runner.migrate(conn); runner.bootstrap_job(conn,job_id)
        runtime_context = CONTEXT + "\nHook revision: " + HOOK_REVISION + "\nIngress id: " + ingress_id + "\nVisible status contract: use the status emitter for this job; it emits ACTIVE only when the runner reports a running stage and otherwise emits NOT_CONNECTED or a persisted terminal state. Never claim ACTIVE from context alone.\nRuntime job_id: " + job_id + "\nRunner bootstrap: python -X utf8 " + str(ROOT / "stage_runner.py") + " bootstrap " + job_id + "\nRunner next: python -X utf8 " + str(ROOT / "stage_runner.py") + " next " + job_id + "\nStatus emitter: python -X utf8 " + str(ROOT / "status_emitter.py") + " " + job_id + " <canonical-skill-name>"
        runtime_context += "\nStatus emitter: python -X utf8 " + str(ROOT / "status_emitter.py") + " " + job_id + " <canonical-skill-name>"
        runtime_context += "\nStatus emitter: python -X utf8 " + str(ROOT / "status_emitter.py") + " " + job_id + " <canonical-skill-name>"
        print(json.dumps({"continue":True,"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":runtime_context}},ensure_ascii=False))
    except Exception:
        pass
    return 0
if __name__=="__main__": raise SystemExit(main())



