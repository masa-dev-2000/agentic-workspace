import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_observer as O


class RuntimeObserverTests(unittest.TestCase):
    def test_missing_ingress_is_partial_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "obs.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE ao_hook_ingress(ingress_id TEXT PRIMARY KEY,job_id TEXT,hook_revision TEXT,created_at INTEGER); CREATE TABLE ao_jobs(job_id TEXT PRIMARY KEY,state TEXT,version INTEGER,plan_digest TEXT,updated_at INTEGER); CREATE TABLE ao_stages(job_id TEXT,name TEXT,status TEXT,current_attempt INTEGER,version INTEGER,result_status TEXT,evidence_digest TEXT); CREATE TABLE ao_dispatches(dispatch_id TEXT,job_id TEXT,stage TEXT,attempt INTEGER,active INTEGER,terminal_status TEXT,model_class TEXT,provider TEXT,created_at INTEGER); CREATE TABLE ao_events(event_type TEXT,job_id TEXT,stage TEXT,created_at INTEGER,id INTEGER);")
            conn.execute("INSERT INTO ao_jobs VALUES('j','planning',0,NULL,1)")
            conn.execute("INSERT INTO ao_stages VALUES('j','planning','ready',0,0,NULL,NULL)")
            conn.commit(); conn.close()
            result = O.observe(db, "j", "missing-ingress")
            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(result["observations"]["HOOK_RECEIVED"]["status"], "NOT_OBSERVABLE")

    def test_full_local_chain_is_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            db = pathlib.Path(directory) / "obs.sqlite3"
            conn = sqlite3.connect(db)
            conn.executescript("CREATE TABLE ao_hook_ingress(ingress_id TEXT PRIMARY KEY,job_id TEXT,hook_revision TEXT,created_at INTEGER); CREATE TABLE ao_jobs(job_id TEXT PRIMARY KEY,state TEXT,version INTEGER,plan_digest TEXT,updated_at INTEGER); CREATE TABLE ao_stages(job_id TEXT,name TEXT,status TEXT,current_attempt INTEGER,version INTEGER,result_status TEXT,evidence_digest TEXT); CREATE TABLE ao_dispatches(dispatch_id TEXT,job_id TEXT,stage TEXT,attempt INTEGER,active INTEGER,terminal_status TEXT,model_class TEXT,provider TEXT,created_at INTEGER); CREATE TABLE ao_events(event_type TEXT,job_id TEXT,stage TEXT,created_at INTEGER,id INTEGER);")
            conn.execute("INSERT INTO ao_hook_ingress VALUES('i','j','hook-v2',1)"); conn.execute("INSERT INTO ao_jobs VALUES('j','completed',1,'p',2)"); conn.execute("INSERT INTO ao_stages VALUES('j','planning','passed',1,1,'passed','e')"); conn.execute("INSERT INTO ao_dispatches VALUES('d','j','planning',1,0,'passed','m','p',1)"); conn.execute("INSERT INTO ao_events VALUES('root-integrated','j','reporting',2,1)"); conn.commit(); conn.close()
            result = O.observe(db, "j", "i")
            self.assertEqual(result["status"], "OBSERVED")


if __name__ == "__main__":
    unittest.main()
