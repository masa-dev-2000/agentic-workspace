import importlib.util, pathlib, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("stage_runner",ROOT/"scripts"/"stage_runner.py")
R=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)

class StageRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.conn=R.connect(pathlib.Path(self.tmp.name)/"runner.db"); R.migrate(self.conn); self.job="job-test"
        R.create_job(self.conn,{"job_id":self.job,"session_hash":"s","turn_hash":"t","cwd_hash":"c","prompt_hash":"p"})
    def tearDown(self): self.conn.close(); self.tmp.cleanup()
    def claim(self,stage="planning",version=0,worker="w1"): return R.claim(self.conn,{"job_id":self.job,"stage":stage,"worker_id":worker,"expected_version":version,"lease_seconds":120})
    def finish(self,d,principal,outcome="passed",**extra): return R.record_result(self.conn,{"dispatch_id":d["dispatch_id"],"dispatch_capability":d["dispatch_capability"],"runtime_handle":"runtime-"+principal,"principal_id":principal,"outcome":outcome,**extra})
    def test_only_one_claim_wins(self):
        self.claim()
        with self.assertRaises(R.RunnerError): self.claim(worker="w2")
    def test_hook_job_can_be_bootstrapped_without_prompt_body(self):
        self.conn.execute("DELETE FROM ao_stages WHERE job_id=?",(self.job,))
        result=R.bootstrap_job(self.conn,self.job)
        self.assertEqual(result["job"]["state"],"planning")
        self.assertEqual(len(result["stages"]),5)
    def test_capability_is_single_use(self):
        d=self.claim(); self.finish(d,"planner",plan_digest="pd")
        with self.assertRaises(R.RunnerError): self.finish(d,"planner",plan_digest="pd")
    def test_expired_lease_rejects_stale_result(self):
        d=self.claim(); self.conn.execute("UPDATE ao_stages SET lease_until=0 WHERE job_id=? AND name='planning'",(self.job,)); R.recover(self.conn,self.job)
        with self.assertRaises(R.RunnerError): self.finish(d,"planner",plan_digest="pd")
    def test_reviewer_cannot_implement(self):
        self.finish(self.claim(),"planner",plan_digest="pd"); n=R.next_action(self.conn,self.job); self.finish(self.claim("reviewing",n["expected_version"]),"reviewer"); n=R.next_action(self.conn,self.job); d=self.claim("implementing",n["expected_version"])
        with self.assertRaises(R.RunnerError): self.finish(d,"reviewer")
    def test_artifact_digest_is_verified(self):
        d=self.claim(); f=pathlib.Path(self.tmp.name)/"a"; f.write_text("x")
        with self.assertRaises(R.RunnerError): self.finish(d,"planner",plan_digest="pd",artifacts=[{"path":str(f),"content_digest":"bad"}])
    def test_fatal_unknown_and_escape_denied(self):
        base={"job_id":self.job,"task_id":"t","action_id":"a","workspace":self.tmp.name,"resource":"x","operation_type":"atomic_write","input":{}}
        for change in ({"side_effect_classes":["destructive-delete"]},{"side_effect_classes":[]},{"resource":"../escape","side_effect_classes":["local-reversible"]}):
            with self.assertRaises(R.RunnerError): R.prepare_operation(self.conn,base|change)
        self.assertEqual(R.status(self.conn,self.job)["job"]["state"],"blocked")
    def test_verify_failure_requires_repairable_class(self):
        self.conn.execute("UPDATE ao_jobs SET state='verifying' WHERE job_id=?",(self.job,))
        self.conn.execute("UPDATE ao_stages SET current_attempt=1 WHERE job_id=? AND name='verifying'",(self.job,))
        job=self.conn.execute("SELECT * FROM ao_jobs WHERE job_id=?",(self.job,)).fetchone()
        R.advance(self.conn,job,"verifying","failed",{"failure_class":"unrepairable"})
        self.assertEqual(R.status(self.conn,self.job)["job"]["state"],"blocked")
    def test_uncertain_operation_requires_reconcile(self):
        p={"job_id":self.job,"task_id":"t","action_id":"a","workspace":self.tmp.name,"resource":"x","operation_type":"atomic_write","input":{},"side_effect_classes":["local-reversible"]}; op=R.prepare_operation(self.conn,p); R.operation_transition(self.conn,{"operation_id":op["operation_id"]},"applying"); R.operation_transition(self.conn,{"operation_id":op["operation_id"]},"unknown")
        with self.assertRaises(R.RunnerError): R.prepare_operation(self.conn,p)
        self.assertEqual(R.operation_transition(self.conn,{"operation_id":op["operation_id"],"receipt_digest":"r","reconcile":{"observed":True}},"reconciled")["status"],"reconciled")
    def test_unknown_schema_fails_closed(self):
        self.conn.execute("UPDATE ao_schema_meta SET version=99 WHERE singleton=1")
        with self.assertRaises(R.RunnerError): R.migrate(self.conn)
if __name__=="__main__": unittest.main()
