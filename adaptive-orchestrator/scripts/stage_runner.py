#!/usr/bin/env python3
"""Deterministic, crash-aware stage runner. It never starts models or tools."""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, sqlite3, time, uuid
from pathlib import Path

SCHEMA_VERSION = 3
STAGES = ("planning", "reviewing", "implementing", "verifying", "reporting")
ROLES = dict(zip(STAGES, ("planner", "reviewer", "implementer", "verifier", "synthesizer")))
NEXT = dict(zip(STAGES, STAGES[1:]))
TERMINAL = {"completed", "blocked", "unknown", "cancelled", "failed"}
SAFE_OPS = {"atomic_write", "apply_patch", "mkdir", "read_only", "run_argv"}
FATAL = {"production-change", "destructive-delete", "external-publish", "external-send", "financial", "legal-contract", "external-service-critical-config", "sensitive-data-external", "privilege-change"}

class RunnerError(RuntimeError): pass
def canonical(v): return json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def load(v): return json.loads(Path(v[1:]).read_text(encoding="utf-8")) if v.startswith("@") else json.loads(v)

def connect(path):
    c=sqlite3.connect(path, timeout=5, isolation_level=None); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=5000"); c.execute("PRAGMA journal_mode=WAL")
    return c

def migrate(c):
    c.executescript("""
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS ao_schema_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL,migrated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ao_jobs(job_id TEXT PRIMARY KEY,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,cwd_hash TEXT NOT NULL,prompt_hash TEXT NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,plan_digest TEXT,plan_revisions INTEGER NOT NULL DEFAULT 0,max_plan_revisions INTEGER NOT NULL DEFAULT 2,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ao_stages(job_id TEXT NOT NULL REFERENCES ao_jobs(job_id),name TEXT NOT NULL,status TEXT NOT NULL,current_attempt INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until INTEGER,version INTEGER NOT NULL DEFAULT 0,principal_id TEXT,artifact_digest TEXT,evidence_digest TEXT,result_status TEXT,updated_at INTEGER NOT NULL,PRIMARY KEY(job_id,name));
    CREATE TABLE IF NOT EXISTS ao_dispatches(dispatch_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,stage TEXT NOT NULL,role TEXT NOT NULL,attempt INTEGER NOT NULL,capability_hash TEXT NOT NULL UNIQUE,plan_digest TEXT,input_artifact_digests TEXT NOT NULL,context_policy TEXT NOT NULL,write_scope TEXT NOT NULL,acceptance_criteria TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,runtime_handle TEXT,principal_id TEXT,model_class TEXT,provider TEXT,usage_json TEXT,terminal_status TEXT,created_at INTEGER NOT NULL,completed_at INTEGER,FOREIGN KEY(job_id,stage) REFERENCES ao_stages(job_id,name));
    CREATE UNIQUE INDEX IF NOT EXISTS ao_one_active_dispatch ON ao_dispatches(job_id,stage) WHERE active=1;
    CREATE TABLE IF NOT EXISTS ao_operations(operation_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES ao_jobs(job_id),task_id TEXT NOT NULL,action_id TEXT NOT NULL,resource TEXT NOT NULL,operation_type TEXT NOT NULL,status TEXT NOT NULL,input_digest TEXT NOT NULL,receipt_digest TEXT,reconcile_json TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ao_artifacts(artifact_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES ao_jobs(job_id),stage TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,content_digest TEXT NOT NULL,principal_id TEXT NOT NULL,attempt INTEGER NOT NULL,created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ao_approvals(approval_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES ao_jobs(job_id),plan_digest TEXT NOT NULL,invocation_digest TEXT NOT NULL,scope_json TEXT NOT NULL,expires_at INTEGER NOT NULL,nonce_hash TEXT NOT NULL UNIQUE,consumed_at INTEGER);
    CREATE TABLE IF NOT EXISTS ao_events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,stage TEXT,event_type TEXT NOT NULL,detail_json TEXT NOT NULL,created_at INTEGER NOT NULL);
    INSERT OR IGNORE INTO ao_schema_meta VALUES(1,3,CAST(strftime('%s','now') AS INTEGER));
    CREATE TABLE IF NOT EXISTS ao_selection_audits(audit_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES ao_jobs,session_hash TEXT NOT NULL,turn_hash TEXT NOT NULL,registry_revision TEXT NOT NULL,taxonomy_version TEXT NOT NULL,observation_state TEXT NOT NULL CHECK(observation_state IN ('complete','incomplete','failed')),observation_window_closed INTEGER NOT NULL CHECK(observation_window_closed IN (0,1)),telemetry_health TEXT NOT NULL CHECK(telemetry_health IN ('complete','degraded','failed')),candidate_digest TEXT NOT NULL,created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS ao_selection_candidates(audit_id TEXT NOT NULL REFERENCES ao_selection_audits(audit_id),skill_key TEXT NOT NULL,source_json TEXT NOT NULL,classification TEXT NOT NULL CHECK(classification IN ('selected','candidate_signal','missed_candidate','not_observable','not_comparable')),reason_code TEXT NOT NULL,PRIMARY KEY(audit_id,skill_key));
    COMMIT;""")
    version=c.execute("SELECT version FROM ao_schema_meta WHERE singleton=1").fetchone()[0]
    if version == 2:
        with Tx(c):
            c.execute("UPDATE ao_schema_meta SET version=?,migrated_at=? WHERE singleton=1", (SCHEMA_VERSION, now(c)))
        version = SCHEMA_VERSION
    if version != SCHEMA_VERSION: raise RunnerError(f"unsupported schema version: {version}")

class Tx:
    def __init__(self,c): self.c=c
    def __enter__(self):
        for n in range(4):
            try: self.c.execute("BEGIN IMMEDIATE"); return self.c
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or n==3: raise
                time.sleep(.025*(2**n))
    def __exit__(self,t,v,b): self.c.execute("ROLLBACK" if t else "COMMIT")
def now(c): return int(c.execute("SELECT strftime('%s','now')").fetchone()[0])
def emit(c,j,s,k,d=None): c.execute("INSERT INTO ao_events(job_id,stage,event_type,detail_json,created_at) VALUES(?,?,?,?,?)",(j,s,k,canonical(d or {}),now(c)))

def create_job(c,p):
    keys=("job_id","session_hash","turn_hash","cwd_hash","prompt_hash")
    if any(not p.get(k) for k in keys): raise RunnerError("body-free job identity is required")
    with Tx(c):
        stamp=now(c); values=tuple(p[k] for k in keys)
        c.execute("INSERT OR IGNORE INTO ao_jobs(job_id,session_hash,turn_hash,cwd_hash,prompt_hash,state,created_at,updated_at) VALUES(?,?,?,?,?,'planning',?,?)",values+(stamp,stamp))
        for s in STAGES: c.execute("INSERT OR IGNORE INTO ao_stages(job_id,name,status,updated_at) VALUES(?,?,?,?)",(p["job_id"],s,"ready" if s=="planning" else "pending",stamp))
        emit(c,p["job_id"],"planning","job-created")
    return status(c,p["job_id"])

def bootstrap_job(c, job_id):
    """Complete the body-free job created by the Hook without prompt text."""
    with Tx(c):
        if not c.execute("SELECT 1 FROM ao_jobs WHERE job_id=?", (job_id,)).fetchone():
            raise RunnerError("job not found")
        stamp = now(c)
        for stage in STAGES:
            c.execute("INSERT OR IGNORE INTO ao_stages(job_id,name,status,updated_at) VALUES(?,?,?,?)", (job_id, stage, "ready" if stage == "planning" else "pending", stamp))
        emit(c, job_id, "planning", "job-bootstrapped")
    return status(c, job_id)

def status(c,j):
    job=c.execute("SELECT * FROM ao_jobs WHERE job_id=?",(j,)).fetchone()
    if not job: raise RunnerError("job not found")
    return {"schema":"orchestration_status_v2","job":dict(job),"stages":[dict(r) for r in c.execute("SELECT * FROM ao_stages WHERE job_id=? ORDER BY rowid",(j,))]}

def recover(c,j):
    with Tx(c):
        stamp=now(c); rows=list(c.execute("SELECT name,current_attempt FROM ao_stages WHERE job_id=? AND status='running' AND lease_until<?",(j,stamp)))
        for r in rows:
            c.execute("UPDATE ao_dispatches SET active=0,terminal_status='lease-expired',completed_at=? WHERE job_id=? AND stage=? AND active=1",(stamp,j,r["name"]))
            c.execute("UPDATE ao_stages SET status='retryable',lease_owner=NULL,lease_until=NULL,version=version+1,updated_at=? WHERE job_id=? AND name=?",(stamp,j,r["name"]))
            emit(c,j,r["name"],"lease-expired",{"attempt":r["current_attempt"]})
    return status(c,j)

def next_action(c,j):
    job=c.execute("SELECT * FROM ao_jobs WHERE job_id=?",(j,)).fetchone()
    if not job: raise RunnerError("job not found")
    if job["state"] in TERMINAL: return {"schema":"runtime_action_v1","action":"stop","state":job["state"]}
    s=c.execute("SELECT * FROM ao_stages WHERE job_id=? AND name=?",(j,job["state"])).fetchone()
    return {"schema":"runtime_action_v1","action":"claim" if s["status"] in {"ready","retryable"} else "wait","job_id":j,"stage":job["state"],"role":ROLES[job["state"]],"stage_status":s["status"],"expected_version":s["version"]}

def claim(c,p):
    j,s,w,e=p["job_id"],p["stage"],p["worker_id"],int(p["expected_version"]); seconds=min(max(int(p.get("lease_seconds",120)),10),900)
    with Tx(c):
        stamp=now(c); job=c.execute("SELECT * FROM ao_jobs WHERE job_id=?",(j,)).fetchone(); row=c.execute("SELECT * FROM ao_stages WHERE job_id=? AND name=?",(j,s)).fetchone()
        if not job or not row or job["state"]!=s: raise RunnerError("stage is not current")
        attempt=row["current_attempt"]+1
        changed=c.execute("UPDATE ao_stages SET status='running',current_attempt=?,lease_owner=?,lease_until=?,version=version+1,updated_at=? WHERE job_id=? AND name=? AND version=? AND status IN ('ready','retryable')",(attempt,w,stamp+seconds,stamp,j,s,e)).rowcount
        if changed!=1: raise RunnerError("stage claim conflict")
        c.execute("UPDATE ao_dispatches SET active=0,terminal_status='superseded',completed_at=? WHERE job_id=? AND stage=? AND active=1",(stamp,j,s))
        token=secrets.token_urlsafe(32); did=f"dispatch-{uuid.uuid4().hex}"; inputs=p.get("input_artifact_digests",[]); context=p.get("context_policy",{"fresh":True,"no_peer_history":s in {"reviewing","verifying"}}); scope=p.get("write_scope",[]); acceptance=p.get("acceptance_criteria",[])
        c.execute("INSERT INTO ao_dispatches(dispatch_id,job_id,stage,role,attempt,capability_hash,plan_digest,input_artifact_digests,context_policy,write_scope,acceptance_criteria,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(did,j,s,ROLES[s],attempt,hashlib.sha256(token.encode()).hexdigest(),job["plan_digest"],canonical(inputs),canonical(context),canonical(scope),canonical(acceptance),stamp))
        emit(c,j,s,"dispatch-created",{"dispatch_id":did,"attempt":attempt})
    return {"schema":"runtime_action_v1","action":"dispatch","dispatch_id":did,"dispatch_capability":token,"job_id":j,"stage":s,"role":ROLES[s],"attempt":attempt,"plan_digest":job["plan_digest"],"input_artifact_digests":inputs,"context_policy":context,"write_scope":scope,"acceptance_criteria":acceptance}

def heartbeat(c,p):
    with Tx(c):
        stamp=now(c); seconds=min(max(int(p.get("lease_seconds",120)),10),900)
        n=c.execute("UPDATE ao_stages SET lease_until=?,version=version+1,updated_at=? WHERE job_id=? AND name=? AND status='running' AND lease_owner=? AND version=? AND lease_until>=?",(stamp+seconds,stamp,p["job_id"],p["stage"],p["worker_id"],int(p["expected_version"]),stamp)).rowcount
        if n!=1: raise RunnerError("heartbeat rejected")
    return {"ok":True,"lease_until":stamp+seconds}

def verify_artifacts(c,j,items,principal,stage,attempt):
    for a in items:
        path=Path(a["path"]); observed=a.get("content_digest")
        if not observed or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=observed: raise RunnerError("artifact digest mismatch")
        c.execute("INSERT INTO ao_artifacts VALUES(?,?,?,?,?,?,?,?,?)",(f"artifact-{uuid.uuid4().hex}",j,stage,a.get("kind","result"),str(path.resolve()),observed,principal,attempt,now(c)))

def advance(c,job,stage,outcome,p):
    stamp=now(c); j=job["job_id"]
    if outcome in {"blocked","unknown"}: c.execute("UPDATE ao_jobs SET state=?,version=version+1,updated_at=? WHERE job_id=?",(outcome,stamp,j)); return
    if outcome=="failed":
        if stage=="reviewing" and p.get("material_findings",True) and job["plan_revisions"]<job["max_plan_revisions"]: target="planning"; c.execute("UPDATE ao_jobs SET state=?,plan_revisions=plan_revisions+1,version=version+1,updated_at=? WHERE job_id=?",(target,stamp,j))
        elif stage=="verifying":
            failure_class=p.get("failure_class","unrepairable")
            verifying_attempt=c.execute("SELECT current_attempt FROM ao_stages WHERE job_id=? AND name='verifying'",(j,)).fetchone()[0]
            if failure_class!="repairable" or verifying_attempt>2:
                c.execute("UPDATE ao_jobs SET state=?,version=version+1,updated_at=? WHERE job_id=?",("blocked" if failure_class!="repairable" else "failed",stamp,j)); return
            target="implementing"; c.execute("UPDATE ao_jobs SET state=?,version=version+1,updated_at=? WHERE job_id=?",(target,stamp,j))
        else: c.execute("UPDATE ao_jobs SET state='failed',version=version+1,updated_at=? WHERE job_id=?",(stamp,j)); return
        c.execute("UPDATE ao_stages SET status='retryable',version=version+1,updated_at=? WHERE job_id=? AND name=?",(stamp,j,target)); return
    if stage=="planning":
        if not p.get("plan_digest"): raise RunnerError("planning result requires plan_digest")
        c.execute("UPDATE ao_jobs SET plan_digest=? WHERE job_id=?",(p["plan_digest"],j))
    if stage=="reporting":
        v=c.execute("SELECT evidence_digest,result_status FROM ao_stages WHERE job_id=? AND name='verifying'",(j,)).fetchone()
        if v["result_status"]!="passed" or not v["evidence_digest"]: raise RunnerError("completion requires verification evidence")
        c.execute("UPDATE ao_jobs SET state='completed',version=version+1,updated_at=? WHERE job_id=?",(stamp,j)); return
    target=NEXT[stage]; c.execute("UPDATE ao_jobs SET state=?,version=version+1,updated_at=? WHERE job_id=?",(target,stamp,j)); c.execute("UPDATE ao_stages SET status='ready',version=version+1,updated_at=? WHERE job_id=? AND name=?",(stamp,j,target))

def record_result(c,p):
    cap=hashlib.sha256(p["dispatch_capability"].encode()).hexdigest()
    with Tx(c):
        stamp=now(c); d=c.execute("SELECT * FROM ao_dispatches WHERE dispatch_id=? AND capability_hash=? AND active=1",(p["dispatch_id"],cap)).fetchone()
        if not d: raise RunnerError("invalid or consumed dispatch capability")
        s=c.execute("SELECT * FROM ao_stages WHERE job_id=? AND name=?",(d["job_id"],d["stage"])).fetchone(); job=c.execute("SELECT * FROM ao_jobs WHERE job_id=?",(d["job_id"],)).fetchone()
        if job["state"]!=d["stage"] or s["current_attempt"]!=d["attempt"] or s["status"]!="running" or s["lease_until"]<stamp: raise RunnerError("stale dispatch result")
        handle,principal=p.get("runtime_handle"),p.get("principal_id")
        if not handle or not principal: raise RunnerError("parent-observed runtime identity is required")
        reviewer=c.execute("SELECT principal_id FROM ao_stages WHERE job_id=? AND name='reviewing'",(d["job_id"],)).fetchone()[0]; implementer=c.execute("SELECT principal_id FROM ao_stages WHERE job_id=? AND name='implementing'",(d["job_id"],)).fetchone()[0]
        if d["stage"]=="implementing" and reviewer==principal: raise RunnerError("reviewer and implementer principals must differ")
        if d["stage"]=="verifying" and implementer==principal: raise RunnerError("implementer and verifier principals must differ")
        outcome=p.get("outcome"); usage=p.get("runtime_usage") or {"source":"unavailable"}
        if outcome not in {"passed","failed","blocked","unknown"}: raise RunnerError("invalid outcome")
        if usage.get("source") not in {"runtime","provider","unavailable"}: raise RunnerError("invalid usage provenance")
        artifacts=p.get("artifacts",[]); verify_artifacts(c,d["job_id"],artifacts,principal,d["stage"],d["attempt"])
        c.execute("UPDATE ao_dispatches SET active=0,runtime_handle=?,principal_id=?,model_class=?,provider=?,usage_json=?,terminal_status=?,completed_at=? WHERE dispatch_id=?",(handle,principal,p.get("model_class","unavailable"),p.get("provider","unavailable"),canonical(usage),outcome,stamp,d["dispatch_id"]))
        c.execute("UPDATE ao_stages SET status=?,lease_owner=NULL,lease_until=NULL,principal_id=?,artifact_digest=?,evidence_digest=?,result_status=?,version=version+1,updated_at=? WHERE job_id=? AND name=?",("passed" if outcome=="passed" else outcome,principal,digest([a["content_digest"] for a in artifacts]) if artifacts else None,p.get("evidence_digest"),outcome,stamp,d["job_id"],d["stage"]))
        advance(c,job,d["stage"],outcome,p); emit(c,d["job_id"],d["stage"],"dispatch-completed",{"dispatch_id":d["dispatch_id"],"outcome":outcome,"usage_source":usage["source"]})
    return status(c,d["job_id"])

def workspace_path(root,value):
    base=Path(root).resolve(); target=(base/value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if target!=base and base not in target.parents: raise RunnerError("resource escapes workspace")
    return target

def prepare_operation(c,p):
    classes=set(p.get("side_effect_classes") or [])
    if not classes or "unknown" in classes:
        with Tx(c):
            stamp=now(c); c.execute("UPDATE ao_jobs SET state='blocked',version=version+1,updated_at=? WHERE job_id=? AND state NOT IN ('completed','failed','cancelled')",(stamp,p["job_id"])); emit(c,p["job_id"],"implementing","operation-blocked",{"reason":"unknown-side-effect"})
        raise RunnerError("unknown side effect is not executable")
    if classes & FATAL:
        with Tx(c):
            stamp=now(c); c.execute("UPDATE ao_jobs SET state='blocked',version=version+1,updated_at=? WHERE job_id=? AND state NOT IN ('completed','failed','cancelled')",(stamp,p["job_id"])); emit(c,p["job_id"],"implementing","operation-blocked",{"reason":"unsupported-fatal-operation"})
        raise RunnerError("unsupported_fatal_operation: PreToolUse enforcement is not enabled")
    kind=p.get("operation_type")
    if kind not in SAFE_OPS: raise RunnerError("operation type is not allowlisted")
    resource=str(workspace_path(Path(p["workspace"]),p["resource"]))
    if kind=="run_argv":
        argv=p.get("argv") or []; allowed=set(p.get("allowed_executables") or [])
        if not p.get("sandbox_attested") or p.get("shell") or not argv or Path(argv[0]).name not in allowed: raise RunnerError("run_argv requires attested sandbox, shell=false, and allowlisted executable")
    oid=digest({"job_id":p["job_id"],"task_id":p["task_id"],"action_id":p["action_id"],"resource":resource}); inp=digest(p.get("input",{}))
    with Tx(c):
        stamp=now(c); old=c.execute("SELECT * FROM ao_operations WHERE operation_id=?",(oid,)).fetchone()
        if old and old["status"] in {"applying","unknown"}: raise RunnerError("operation requires reconciliation before retry")
        c.execute("INSERT OR IGNORE INTO ao_operations(operation_id,job_id,task_id,action_id,resource,operation_type,status,input_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,'prepared',?,?,?)",(oid,p["job_id"],p["task_id"],p["action_id"],resource,kind,inp,stamp,stamp)); emit(c,p["job_id"],"implementing","operation-prepared",{"operation_id":oid,"operation_type":kind})
    return {"schema":"restricted_operation_v1","operation_id":oid,"operation_type":kind,"resource":resource,"input_digest":inp,"status":"prepared"}

def operation_transition(c,p,target):
    allowed={"applying":{"prepared"},"unknown":{"applying"},"reconciled":{"unknown","applying"},"committed":{"reconciled","applying"}}
    with Tx(c):
        row=c.execute("SELECT * FROM ao_operations WHERE operation_id=?",(p["operation_id"],)).fetchone()
        if not row or row["status"] not in allowed[target]: raise RunnerError(f"cannot transition operation to {target}")
        receipt=p.get("receipt_digest")
        if target in {"reconciled","committed"} and not receipt: raise RunnerError("receipt_digest is required")
        rec=canonical(p.get("reconcile",{})) if target=="reconciled" else row["reconcile_json"]
        c.execute("UPDATE ao_operations SET status=?,receipt_digest=COALESCE(?,receipt_digest),reconcile_json=?,updated_at=? WHERE operation_id=?",(target,receipt,rec,now(c),row["operation_id"]))
    return {"operation_id":row["operation_id"],"status":target,"receipt_digest":receipt or row["receipt_digest"]}

def approve(c,p): raise RunnerError("fatal approvals are disabled until Phase 3 PreToolUse enforcement")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--db",default=str(Path(__file__).with_name("orchestration.sqlite3"))); sub=ap.add_subparsers(dest="command",required=True)
    sub.add_parser("init"); sub.add_parser("migrate")
    payload_commands=("create-job","claim","heartbeat","record-result","prepare-operation","mark-applying","mark-unknown","reconcile-operation","commit-operation","approve")
    for n in payload_commands: sub.add_parser(n).add_argument("payload")
    for n in ("bootstrap","next","status","recover"): sub.add_parser(n).add_argument("job_id")
    a=ap.parse_args()
    try:
        c=connect(Path(a.db)); migrate(c)
        funcs={"create-job":create_job,"claim":claim,"heartbeat":heartbeat,"record-result":record_result,"prepare-operation":prepare_operation,"approve":approve}
        if a.command in {"init","migrate"}: result={"schema_version":SCHEMA_VERSION,"ok":True}
        elif a.command in funcs: result=funcs[a.command](c,load(a.payload))
        elif a.command=="bootstrap": result=bootstrap_job(c,a.job_id)
        elif a.command=="next": result=next_action(c,a.job_id)
        elif a.command=="status": result=status(c,a.job_id)
        elif a.command=="recover": result=recover(c,a.job_id)
        else: result=operation_transition(c,load(a.payload),{"mark-applying":"applying","mark-unknown":"unknown","reconcile-operation":"reconciled","commit-operation":"committed"}[a.command])
        print(json.dumps(result,ensure_ascii=True,indent=2,sort_keys=True)); return 0
    except (RunnerError,sqlite3.Error,OSError,ValueError,KeyError,json.JSONDecodeError) as e:
        print(json.dumps({"error":str(e)}),file=os.sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
