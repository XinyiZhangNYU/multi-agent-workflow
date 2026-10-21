"""Synthetic, isolated acceptance regressions. No project business execution."""
import concurrent.futures
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Conflict, Protocol, Store, now, packed
from server import BoardServer, atomic_json


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-board-test-')
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Test', 'stale_seconds': 60}
        self.store = Store(self.runtime, self.config)
        self.http = None

    def tearDown(self):
        if self.http:
            self.http.shutdown(); self.http.server_close(); self.worker.join()
        self.temp.cleanup()

    def event(self, op, task='t', data=None, run=None, seq=None, **kw):
        return board.envelope(op, task, data or {}, run, seq, **kw)

    def task(self, task='t'):
        return self.event('task', task, {'title': task, 'project': 'Test', 'workstream': task})

    def run_event(self, run='r', task='t', role='execution'):
        return self.event('run', task, {'role': role, 'agent': 'test', 'client': 'script'}, run, 1)

    def claim(self, run='r', version=0, task='t'):
        return self.event('claim', task, {'expected_version': version}, run, 2)

    def init(self):
        for e in (self.task(), self.run_event(), self.claim()): self.store.submit(e)

    def online(self):
        self.http = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-test-token-only-0123456789')
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)
        atomic_json(self.runtime / 'instance.json', {'url': self.url, 'pid': 0, 'instance_id': self.http.instance})
        (self.runtime / 'write-token').write_text(self.http.token)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True); self.worker.start()

    def test_duplicate_and_conflicting_id(self):
        e = self.task(); self.store.submit(e)
        self.assertTrue(self.store.submit(e)['duplicate'])
        e['data']['title'] = 'changed'
        with self.assertRaises(Conflict): self.store.submit(e)

    def test_late_sequence_and_old_owner(self):
        self.init()
        self.store.submit(self.event('progress', data={'phase': 'new'}, run='r', seq=5))
        self.assertFalse(self.store.submit(self.event('progress', data={'phase': 'old'}, run='r', seq=4))['applied'])
        self.assertEqual(self.store.view('t')['state']['phase'], 'new')
        self.store.submit(self.run_event('next')); self.store.submit(self.claim('next', 1))
        self.assertFalse(self.store.submit(self.event('progress', data={'phase': 'late owner'}, run='r', seq=6))['applied'])
        self.assertEqual(self.store.view('t')['current_run'], 'next')
        self.assertNotIn('phase', self.store.view('t')['state'])

    def test_claim_conflict_and_audit_separation(self):
        self.init(); self.store.submit(self.run_event('audit', role='audit'))
        with self.assertRaises(Conflict): self.store.submit(self.claim('audit', 1))
        self.store.submit(self.run_event('next'))
        with self.assertRaises(Conflict): self.store.submit(self.claim('next', 0))
        self.assertEqual(self.store.view('t')['current_run'], 'r')

    def test_overview_running_audit_does_not_replace_ended_owner(self):
        self.init()
        self.store.submit(self.event('progress', data={'status':'ended','candidate_sha':'a'*40}, run='r', seq=3))
        self.store.submit(self.run_event('review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'running','phase':'checking','candidate_sha':'a'*40}, run='review', seq=2))
        t = self.store.view()['tasks'][0]
        self.assertEqual(t['current_run'], 'r')
        self.assertEqual(t['state']['status'], 'ended')  # This field remains execution-only.
        self.assertEqual([(r['id'],r['state']['status']) for r in t['overview_runs']], [('r','ended'),('review','running')])
        self.assertEqual(t['overview_runs'][1]['candidate_scope'], 'current')
        self.assertFalse(t['needs_attention'])  # Working normally is not a user decision.
        self.assertEqual(t['business_at'], t['current']['business_at'])
        self.assertNotIn('deliveries', t)  # Overview payload omits them; detail keeps them.
        self.assertEqual(self.store.view('t')['deliveries'], [])

    def test_overview_waiting_input_without_reason_for_both_roles(self):
        self.init(); self.store.submit(self.run_event('review', role='audit'))
        for run,seq in [('r',3),('review',2)]:
            with self.subTest(run=run):
                self.store.submit(self.event('progress', data={'status':'waiting_input'}, run=run, seq=seq))
                t = self.store.view()['tasks'][0]
                r = next(r for r in t['overview_runs'] if r['id']==run)
                self.assertFalse(r['stale']); self.assertTrue(r['needs_attention'])
                self.assertTrue(t['needs_attention'])
                self.assertNotIn('decision', r['state'])
                self.store.submit(self.event('progress', data={'status':'ended'}, run=run, seq=seq+1))
        self.assertFalse(self.store.view()['tasks'][0]['needs_attention'])

    def test_overview_audit_staleness_is_separate_from_execution(self):
        self.init()
        self.store.submit(self.event('progress', data={'status':'ended'}, run='r', seq=3))
        self.store.submit(self.run_event('review', role='audit'))
        e = self.event('progress', data={'status':'running'}, run='review', seq=2)
        e['occurred_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.store.submit(e)
        t = self.store.view()['tasks'][0]
        self.assertFalse(t['stale'])
        self.assertTrue(t['overview_runs'][1]['stale']); self.assertTrue(t['needs_attention'])
        self.assertEqual(t['overview_runs'][1]['state']['status'], 'running')
        self.assertIsNone(t['activity'])  # A summary is not a verified process heartbeat.

    def test_overview_audit_candidate_scope_unknown_and_historical(self):
        self.init(); self.store.submit(self.run_event('review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'running'}, run='review', seq=2))
        self.assertEqual(self.store.view()['tasks'][0]['overview_runs'][1]['candidate_scope'], 'unknown')
        self.store.submit(self.event('progress', data={'status':'ended','candidate_sha':'b'*40}, run='r', seq=3))
        self.store.submit(self.event('progress', data={'candidate_sha':'a'*40}, run='review', seq=3))
        t = self.store.view()['tasks'][0]
        self.assertEqual(t['overview_runs'][1]['candidate_scope'], 'other')
        self.assertEqual(t['overview_runs'][1]['state']['candidate_sha'], 'a'*40)
        self.assertEqual(t['state']['candidate_sha'], 'b'*40)
        self.assertFalse(t['conflicts'])  # Different run roles/objects are not conflicting authorities.

    def test_overview_finished_audit_does_not_create_acceptance(self):
        self.init(); self.store.submit(self.run_event('review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'ended'}, run='r', seq=3))
        self.store.submit(self.event('progress', data={'status':'ended'}, run='review', seq=4))
        t = self.store.view()['tasks'][0]
        self.assertEqual([r['id'] for r in t['overview_runs']], ['r'])
        self.assertEqual(self.store.view('t')['deliveries'], [])
        self.assertEqual(len(self.store.view('t')['runs']), 2)  # History is retained.
        late = self.store.submit(self.event('progress', data={'status':'running'}, run='review', seq=3))
        self.assertFalse(late['applied'])
        self.assertEqual([r['id'] for r in self.store.view()['tasks'][0]['overview_runs']], ['r'])

    def test_overview_no_owner_and_handoff_preserve_run_boundaries(self):
        self.store.submit(self.task()); self.store.submit(self.run_event('review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'waiting_input'}, run='review', seq=2))
        t = self.store.view()['tasks'][0]
        self.assertIsNone(t['current_run']); self.assertEqual(t['state']['status'], 'unknown')
        self.assertEqual([r['id'] for r in t['overview_runs']], ['review'])
        self.store.submit(self.run_event()); self.store.submit(self.claim())
        self.store.submit(self.event('progress', data={'status':'waiting_input'}, run='r', seq=3))
        self.store.submit(self.run_event('next')); self.store.submit(self.claim('next', 1))
        self.store.submit(self.event('progress', data={'status':'running'}, run='r', seq=4))
        t = self.store.view()['tasks'][0]
        self.assertEqual([r['id'] for r in t['overview_runs']], ['next','review'])
        self.assertEqual(t['current_run'], 'next')

    def test_fresh_audit_only_not_attention_or_execution(self):
        self.store.submit(self.task()); self.store.submit(self.run_event('review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'running','candidate_sha':'a'*40}, run='review', seq=2))
        t = self.store.view('t')
        self.assertFalse(t['stale']); self.assertFalse(t['needs_attention'])
        self.assertIsNone(t['current_run']); self.assertIsNone(t['current'])
        self.assertIsNone(t['business_at']); self.assertIsNone(t['activity'])
        self.assertEqual(t['state'], {'status':'unknown'})
        self.assertEqual(t['overview_runs'][0]['candidate_scope'], 'unknown')
        self.assertEqual(t['deliveries'], [])
        for seq, state in enumerate([
            {'status':'waiting_input'}, {'status':'running','blocker':'blocked'},
            {'blocker':None,'decision':'choose'}, {'decision':None}], start=3):
            self.store.submit(self.event('progress', data=state, run='review', seq=seq))
            t = self.store.view('t')
            self.assertFalse(t['stale'])
            self.assertEqual(t['needs_attention'], seq != 6)

    def test_no_active_audit_still_attention_and_no_late_revival(self):
        self.store.submit(self.task())
        self.assertTrue(self.store.view('t')['needs_attention'])
        self.store.submit(self.run_event('review', role='audit'))
        self.assertTrue(self.store.view('t')['stale'])  # No business update yet.
        self.store.submit(self.event('progress', data={'status':'ended'}, run='review', seq=4))
        self.assertFalse(self.store.submit(self.event('progress', data={'status':'running'}, run='review', seq=3))['applied'])
        t = self.store.view('t')
        self.assertTrue(t['stale']); self.assertTrue(t['needs_attention'])
        self.assertEqual(t['overview_runs'], []); self.assertEqual(len(t['runs']), 1)
        self.assertEqual(t['deliveries'], [])

    def test_fresh_audit_does_not_hide_other_stale_audit(self):
        self.store.submit(self.task())
        self.store.submit(self.run_event('old-review', role='audit'))
        e = self.event('progress', data={'status':'running'}, run='old-review', seq=2)
        e['occurred_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.store.submit(e)
        self.assertTrue(self.store.view('t')['stale'])
        self.assertTrue(self.store.view('t')['needs_attention'])
        self.store.submit(self.run_event('fresh-review', role='audit'))
        self.store.submit(self.event('progress', data={'status':'running'}, run='fresh-review', seq=2))
        t = self.store.view('t')
        self.assertFalse(t['stale']); self.assertTrue(t['needs_attention'])
        self.assertEqual([r['stale'] for r in t['overview_runs']], [True, False])

    def test_cross_run_offline_handoff(self):
        events = [self.task(), self.run_event('z-first'), self.claim('z-first'),
                  self.event('progress', data={'phase': 'first'}, run='z-first', seq=3),
                  self.run_event('a-second'), self.claim('a-second', 1),
                  self.event('progress', data={'phase': 'second'}, run='a-second', seq=3)]
        for e in events: self.assertEqual(board.send(self.runtime,e,self.config)['status'], 'QUEUED_NOT_RECORDED')
        self.online()
        result = board.flush(self.runtime, self.config)
        self.assertEqual((result['recorded'],result['remaining']), (7,0))
        self.assertEqual(self.store.view('t')['state']['phase'], 'second')
        self.assertEqual(board.flush(self.runtime,self.config)['recorded'],0)
        self.assertTrue(board.send(self.runtime,events[-1],self.config,queue=False)['duplicate'])

    def test_conflict_does_not_block_other_task(self):
        for e in [self.task(),self.run_event(),self.claim(version=1),self.event('progress',data={'phase':'blocked'},run='r',seq=3), self.task('other')]:
            board.enqueue(self.runtime,e,self.config)
        self.online(); result=board.flush(self.runtime,self.config)
        self.assertEqual(result['remaining'],2)
        self.assertEqual(self.store.view('other')['title'],'other')
        self.assertEqual(self.store.view('t')['version'],0)
        self.assertEqual(board.flush(self.runtime,self.config)['remaining'],2)

    def test_invalid_offline_envelope_no_writes(self):
        for field in ('event_id','run_id','task_id'):
            e=self.run_event(); e[field]='../../escaped-proof'
            untouched=self.root / ('not-created-'+field)
            with self.assertRaises(ValueError): board.send(untouched,e,self.config)
            self.assertFalse(untouched.exists())
        e=self.task(); e['data']['unexpected']='no'
        with self.assertRaises(ValueError): board.send(self.root/'invalid',e,self.config)
        self.assertFalse((self.root/'invalid').exists())

    def test_outbox_duplicate_and_capacity(self):
        e=self.task(); board.enqueue(self.runtime,e,self.config); board.enqueue(self.runtime,e,self.config)
        self.assertEqual(len(list((self.runtime/'outbox').glob('*/*.json'))),1)
        with self.assertRaises(RuntimeError): board.enqueue(self.runtime,self.task('second'),dict(self.config,max_outbox_events=1))
        e['data']['title']='changed'
        with self.assertRaises(ValueError): board.enqueue(self.runtime,e,self.config)

    def test_install_and_worktree_topology(self):
        installed=self.root/'.ai-workflow'; installed.mkdir(); (installed/'WORKFLOW.md').write_text('test')
        for directory in (installed, self.root/'worktrees/board'):
            config=directory/'projects/example/agent-board.json'; config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'project_root':'../../..'}))
            args=SimpleNamespace(config=str(config),runtime=None,project_root=None)
            if directory==installed:
                self.assertEqual(board.settings(args)[0]['project_root'],str(self.root))
            else:
                with self.assertRaises(ValueError): board.settings(args)
                args.project_root=str(self.root)
                self.assertEqual(board.settings(args)[1],self.root/'.ai-workflow/runtime/agent-board')

    def test_default_config_follows_state(self):
        base=self.root/'workflow'; base.mkdir()
        self.assertIsNone(board.default_config(base))
        (base/'STATE.md').write_text('~~~yaml\nproject_configuration: projects/sample/README.md\n~~~\n',encoding='utf-8')
        self.assertEqual(board.default_config(base),base/'projects/sample/agent-board.json')
        (base/'STATE.md').write_text('# STATE\n',encoding='utf-8')
        self.assertIsNone(board.default_config(base))
        self.assertEqual(board.default_config(),board.BASE/'projects/example/agent-board.json')

    def test_display_time_zone_is_configured(self):
        self.assertEqual(self.store.view()['timezone'],'UTC')
        self.assertEqual(Store(self.root/'zoned',dict(self.config,display_time_zone='Asia/Tokyo')).view()['timezone'],'Asia/Tokyo')
        for bad in ('', 'Zone with spaces', 7):
            with self.assertRaises(ValueError): Store(self.root/'bad',dict(self.config,display_time_zone=bad))

    def test_two_http_producers(self):
        self.online()
        def producer(task):
            for e in [self.task(task),self.run_event(task,task),self.claim(task,0,task)]+[
                self.event('progress',task,{'phase':str(n)},task,n) for n in range(3,15)]:
                board.send(self.runtime,e,self.config,queue=False)
        with concurrent.futures.ThreadPoolExecutor(2) as pool: list(pool.map(producer,['alpha','beta']))
        self.assertEqual(self.store.view()['event_count'],30)
        for t in ('alpha','beta'): self.assertEqual(self.store.view(t)['state']['phase'],'14')

    def test_two_helper_producers(self):
        self.online()
        def producer(task):
            for e in [self.task(task),self.run_event(task,task),self.claim(task,0,task)]+[
                self.event('progress',task,{'phase':str(n)},task,n) for n in range(3,8)]:
                self.assertEqual(board.send(self.runtime,e,self.config)['status'],'RECORDED')
        with concurrent.futures.ThreadPoolExecutor(2) as pool: list(pool.map(producer,['helper-a','helper-b']))
        self.assertEqual(self.store.view()['event_count'],16)
        self.assertEqual(board.flush(self.runtime,self.config)['remaining'],0)

    def test_future_invalid_and_reserved_ids(self):
        e=self.task();e['occurred_at']='2999-01-01T00:00:00Z'
        with self.assertRaises(ValueError): board.send(self.root/'future',e,self.config)
        self.assertFalse((self.root/'future').exists())
        e=self.task();e['event_id']='CON'
        with self.assertRaises(ValueError):board.send(self.root/'reserved',e,self.config)
        self.assertFalse((self.root/'reserved').exists())

    def test_delivery_retry_normalizes_paths(self):
        self.init()
        d={'stage':'self_test','verdict':'PASS','candidate_sha':'A'*40,'evidence':'report.md','audit_id':None}
        e=self.event('delivery',data=d,run='r',seq=3)
        self.store.submit(e);self.assertTrue(self.store.submit(e)['duplicate'])

    def test_old_snapshot_import_does_not_supersede_newer_source(self):
        self.store.submit(self.task())
        src={'kind':'document_snapshot','path':'status.md','read_at':now(),'sha256':'1'*64}
        for day,phase in [('2026-01-02','new'),('2026-01-01','old')]:
            self.store.submit(self.event('snapshot',data={'fields':{'phase':phase},'excerpt':phase},source=dict(src,source_at=day+'T00:00:00Z')))
        self.assertEqual(self.store.view('t')['snapshots'][0]['data']['fields']['phase'],'new')

    def test_http_security_and_static_assets(self):
        self.online()
        for path in ('/','/app.js','/style.css','/api/health'):
            with urllib.request.urlopen(self.url+path) as r:
                self.assertEqual(r.status,200); self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        cases=[({},401),({'Origin':'https://evil.example'},403),({'Host':'evil.example'},403)]
        for headers,status in cases:
            req=urllib.request.Request(self.url+'/api/events',data=packed(self.task()).encode(),headers=dict({'Content-Type':'application/json'},**headers))
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
            self.assertEqual(error.exception.code,status)
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(self.url+'/api/events')
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(self.url+'/../../secret')
        self.assertEqual(self.store.view()['event_count'],0)

    def test_provenance_conflict_stale_and_restart(self):
        self.init()
        e=self.event('progress',data={'status':'running','phase':'self'},run='r',seq=3)
        e['occurred_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(); self.store.submit(e)
        src={'kind':'document_snapshot','path':'status.md','source_at':e['occurred_at'],'read_at':now(),'sha256':'1'*64}
        self.store.submit(self.event('snapshot',data={'fields':{'status':'paused','phase':'document'},'excerpt':'<script>bad</script>'},source=src))
        t=Store(self.runtime,self.config).view('t')
        self.assertTrue(t['stale']); self.assertTrue(t['conflicts']); self.assertIsNone(t['activity'])
        self.assertEqual(t['state']['status'],'running'); self.assertEqual(t['snapshots'][0]['source']['sha256'],'1'*64)

    def test_candidate_binding_and_selftest_not_audit(self):
        self.init(); self.store.submit(self.event('progress',data={'candidate_sha':'a'*40},run='r',seq=3))
        d={'stage':'self_test','verdict':'PASS','candidate_sha':'a'*40,'evidence':'report.md','audit_id':None}
        self.store.submit(self.event('delivery',data=d,run='r',seq=4))
        self.assertTrue(self.store.view('t')['deliveries'][0]['applicable'])
        self.store.submit(self.event('progress',data={'candidate_sha':'b'*40},run='r',seq=5))
        self.assertFalse(self.store.view('t')['deliveries'][0]['applicable'])
        d['stage']='independent_audit'; d['audit_id']='audit-1'
        with self.assertRaises(Conflict): self.store.submit(self.event('delivery',data=d,run='r',seq=6))
        self.assertEqual(len(self.store.view('t')['deliveries']),1)

    def test_evidence_paths_and_schema(self):
        for path in ('../escape','secrets/api.txt','.env','runtime/write-token'):
            with self.assertRaises(ValueError): Protocol(self.config).path_check(path)
        for data in ({'count':{'done':2,'total':1,'unit':'items'}},{'candidate_sha':'short'},{'status':'approved'}):
            with self.assertRaises(ValueError): Protocol(self.config).validate(self.event('progress',data=data,run='r',seq=2))


if __name__=='__main__': unittest.main(verbosity=2)
