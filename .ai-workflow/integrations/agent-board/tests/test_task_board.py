"""Author checks: isolated data, no live tasks, providers or service writes."""
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board as plans
import task_board_cli as helper
from model import Conflict, Store, packed
from server import BoardServer, atomic_json


class TaskBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='task-board-test-')
        self.root = Path(self.temp.name); self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Test'}
        self.store = Store(self.runtime, self.config); plans.initialize(self.store)
        self.http = None; self.counter = 0
        self.task('t'); self.task('other')

    def tearDown(self):
        if self.http:
            self.http.shutdown(); self.http.server_close(); self.worker.join()
        self.temp.cleanup()

    def task(self, tid):
        self.store.submit(board.envelope('task', tid, {'title': tid, 'project': 'Test', 'workstream': 'test'}))

    def req(self, op, data=None, task='t', version=None):
        self.counter += 1
        return {'event_id': 'e-' + str(self.counter), 'task_id': task, 'expected_version': plans.read(self.store, task)['version'] if version is None else version, 'op': op, 'data': data or {}}

    def write(self, op, data=None, task='t', version=None):
        return plans.apply(self.store, self.req(op, data, task, version))

    def line(self, mid='first'):
        return {'id': mid, 'goal': mid + ' deliverable', 'completion': 'verified result', 'owner': 'agent-a', 'next_step': 'implement'}

    def active(self, task='t', mode='plan_execute'):
        self.write('create', {'goal': 'User task', 'mode': mode, 'user_request': 'user explicitly enabled this task'}, task)
        self.write('draft', {'mainlines': [self.line(), self.line('second')]}, task)
        self.write('freeze', task=task)

    def online(self):
        self.http = BoardServer(('127.0.0.1', 0), self.store, 'isolated-test-only-1234567890')
        self.url = 'http://127.0.0.1:' + str(self.http.server_port)
        atomic_json(self.runtime / 'instance.json', {'url': self.url, 'pid': os.getpid(), 'instance_id': self.http.instance})
        (self.runtime / 'write-token').write_text(self.http.token)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True); self.worker.start()

    def old_rows(self):
        with self.store.connect() as db:
            return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)] for table in ('tasks', 'runs', 'events')}

    def test_no_implicit_creation(self):
        before = self.old_rows()
        self.assertEqual(plans.listing(self.store), [])
        self.assertFalse(plans.read(self.store, 't')['exists'])
        with self.assertRaises(Conflict): self.write('freeze')
        self.assertEqual(before, self.old_rows())

    def test_plan_execute_does_not_add_confirmation(self):
        self.active(); self.assertEqual(plans.read(self.store, 't')['board']['state'], 'active')
        self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'running'}})
        with self.assertRaises(Conflict): self.write('confirm', {'reason': 'duplicate approval', 'decision_ref': 'user'})

    def test_plan_wait_blocks_execution_until_explicit_confirmation(self):
        self.active(mode='plan_wait')
        self.assertEqual(plans.read(self.store, 't')['board']['state'], 'awaiting_confirmation')
        with self.assertRaises(Conflict): self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'running'}})
        self.write('confirm', {'reason': 'user confirmed plan', 'decision_ref': 'explicit user reply'})
        self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'running'}})

    def test_frozen_baseline_cannot_bulk_replace_or_weaken(self):
        self.active(); before = plans.read(self.store, 't')
        with self.assertRaises(Conflict): self.write('draft', {'mainlines': []})
        for field in ('goal', 'completion', 'mainlines'):
            with self.assertRaises(ValueError): self.write('progress', {'mainline_id': 'first', 'fields': {field: 'weakened'}})
        with self.assertRaises(ValueError): self.write('delete_mainline', {'mainline_id': 'first'})
        self.assertEqual(before, plans.read(self.store, 't'))

    def test_explicit_change_retains_original_and_reason(self):
        self.active()
        self.write('change', {'action': 'replace', 'mainline_id': 'first', 'replacement': self.line('replacement'), 'reason': 'user changed result', 'decision_ref': 'user message'})
        b = plans.read(self.store, 't')['board']
        self.assertEqual([v['id'] for v in b['mainlines']], ['first', 'second', 'replacement'])
        self.assertEqual(b['mainlines'][0]['completion'], 'verified result')
        self.assertEqual(b['mainlines'][0]['status'], 'replaced')
        self.assertEqual(b['changes'][0]['reason'], 'user changed result')
        with self.assertRaises(Conflict): self.write('change', {'action': 'cancel', 'mainline_id': 'first', 'reason': 'overwrite old reason', 'decision_ref': 'user'})

    def test_progress_and_branch_cleanup_do_not_complete_mainline(self):
        self.active(); self.write('add_branch', {'mainline_id': 'first', 'id': 'branch', 'goal': 'resolve question', 'next_step': 'test'})
        with self.assertRaises(Conflict): self.write('delete_branch', {'branch_id': 'branch'})
        with self.assertRaises(ValueError): self.write('branch_progress', {'branch_id': 'branch', 'fields': {'status': 'done'}})
        self.write('focus', {'mainline_id': 'first', 'branch_id': 'branch'})
        self.write('complete_branch', {'branch_id': 'branch', 'result': 'resolved', 'evidence': [str(self.root / 'evidence.md')]})
        self.write('delete_branch', {'branch_id': 'branch'})
        b = plans.read(self.store, 't')['board']
        self.assertEqual(len(b['mainlines']), 2); self.assertEqual(b['mainlines'][0]['status'], 'planned')
        self.assertEqual(b['mainlines'][0]['branch_results'][0]['result'], 'resolved')
        self.assertEqual(b['focus']['branch_id'], None)
        with self.assertRaises(Conflict): self.write('add_branch', {'mainline_id': 'first', 'id': 'branch', 'goal': 'reuse', 'next_step': 'test'})

    def test_cancel_branch_retains_short_reason_before_cleanup(self):
        self.active(); self.write('add_branch', {'mainline_id': 'first', 'id': 'side', 'goal': 'optional', 'next_step': 'inspect'})
        self.write('cancel_branch', {'branch_id': 'side', 'reason': 'not needed in authorized scope'})
        self.write('delete_branch', {'branch_id': 'side'})
        self.assertTrue(plans.read(self.store, 't')['board']['mainlines'][0]['branch_results'][0]['cancelled'])

    def test_manual_mainline_done_requires_result(self):
        self.active()
        with self.assertRaises(ValueError): self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'done'}})
        self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'done', 'result': 'delivered'}})
        self.assertEqual(len(plans.read(self.store, 't')['board']['mainlines']), 2)

    def test_restart_and_model_handoff_restore_same_record(self):
        self.active(); self.write('progress', {'mainline_id': 'first', 'fields': {'owner': 'agent-b', 'next_step': 'resume same plan'}})
        previous = plans.read(self.store, 't'); reopened = Store(self.runtime, self.config); plans.initialize(reopened)
        self.assertEqual(previous, plans.read(reopened, 't'))
        with self.assertRaises(Conflict): self.write('create', {'goal': 'replacement', 'mode': 'plan_execute', 'user_request': 'old task'})

    def test_additive_schema_and_all_updates_leave_old_data_unchanged(self):
        before = self.old_rows(); self.active(); self.write('close', {'reason': 'finished task'})
        self.write('delete', {'reason': 'user requested deletion', 'decision_ref': 'user'})
        self.assertEqual(before, self.old_rows()); self.assertEqual(plans.listing(self.store), [])
        self.assertTrue(plans.read(self.store, 't')['deleted'])
        # Old helpers can still submit ordinary events after migration.
        self.task('third'); self.assertIn('third', [t['id'] for t in self.store.view()['tasks']])

    def test_deletion_retry_and_stale_create_cannot_resurrect(self):
        create = self.req('create', {'goal': 'old', 'mode': 'plan_execute', 'user_request': 'user'}, version=0)
        plans.apply(self.store, create); self.write('close', {'reason': 'end'})
        delete = self.req('delete', {'reason': 'user deletion', 'decision_ref': 'user'})
        receipt = plans.apply(self.store, delete)
        self.assertEqual(receipt['version'], plans.apply(self.store, delete)['version'])
        with self.assertRaises(Conflict): plans.apply(self.store, create)
        self.assertFalse(plans.read(self.store, 't')['exists'])

    def test_concurrent_writes_conflict_not_silent_overwrite(self):
        self.active(); version = plans.read(self.store, 't')['version']
        requests = [self.req('progress', {'mainline_id': 'first', 'fields': {'next_step': str(i)}}, version=version) for i in range(8)]
        def send(r):
            try: return plans.apply(self.store, r)['recorded']
            except Conflict: return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(send, requests))
        self.assertEqual(results.count(True), 1)

    def test_idempotent_write_and_digest_conflict(self):
        req = self.req('create', {'goal': 'test', 'mode': 'plan_execute', 'user_request': 'user'})
        plans.apply(self.store, req); self.assertTrue(plans.apply(self.store, req)['duplicate'])
        req['data']['goal'] = 'changed'
        with self.assertRaises(Conflict): plans.apply(self.store, req)

    def test_focus_summary_same_source_and_scoped_branches(self):
        self.active(); self.write('add_branch', {'mainline_id': 'second', 'id': 'side', 'goal': 'check', 'next_step': 'test'})
        value = plans.read(self.store, 't'); summary = plans.focus_summary(value)['summary']
        self.assertEqual(len(summary['mainlines']), 2); self.assertEqual(summary['branches'], [])
        scoped = plans.focus_summary(value, 'second')['summary']; self.assertEqual(len(scoped['mainlines']), 1)
        self.assertEqual(scoped['branches'], value['board']['branches'])

    def test_invalid_ids_and_sensitive_evidence_write_nothing(self):
        req = self.req('create', {'goal': 'test', 'mode': 'plan_execute', 'user_request': 'user'})
        for bad in ('../../escape', 'CON', 'x/y'):
            req['event_id'] = bad
            with self.assertRaises(ValueError): helper.enqueue(self.runtime, req, self.config)
        self.assertFalse((self.runtime / 'task-board-outbox').exists())
        self.active()
        with self.assertRaises(ValueError): self.write('progress', {'mainline_id': 'first', 'fields': {'evidence': ['../outside', '.env']}})

    def test_http_host_origin_credentials_and_instance_guards(self):
        self.online(); req = self.req('create', {'goal': 'test', 'mode': 'plan_execute', 'user_request': 'user'})
        for headers, code in [({}, 401), ({'Authorization': 'Bearer ' + self.http.token, 'Origin': 'http://evil.test'}, 403),
                              ({'Authorization': 'Bearer ' + self.http.token, 'X-Agent-Board-Instance': 'wrong'}, 409)]:
            raw = urllib.request.Request(self.url + '/api/task-board-events', packed(req).encode(), {'Content-Type': 'application/json', **headers})
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(raw)
            self.assertEqual(error.exception.code, code); error.exception.close()
        value = board.request(self.runtime, '/api/task-board?task_id=t'); self.assertFalse(value['exists'])
        receipt = helper.deliver(self.runtime, req, board.request, board.health(self.runtime))
        self.assertEqual(receipt['status'], 'RECORDED')
        self.assertTrue(helper.deliver(self.runtime, req, board.request, board.health(self.runtime))['duplicate'])

    def test_offline_recovery_preserves_causal_order_and_old_outbox(self):
        sentinel = self.runtime / 'outbox' / 'existing' / 'event.json'; sentinel.parent.mkdir(parents=True); sentinel.write_text('keep')
        requests = [self.req('create', {'goal': 'test', 'mode': 'plan_execute', 'user_request': 'user'}, version=0),
                    self.req('draft', {'mainlines': [self.line()]}, version=1), self.req('freeze', version=2)]
        for r in requests: helper.enqueue(self.runtime, r, self.config)
        result = helper.flush(self.runtime, self.config, board.request, board.health)
        self.assertEqual(result['status'], 'QUEUED_NOT_RECORDED'); self.assertEqual(result['remaining'], 3)
        self.online(); result = helper.flush(self.runtime, self.config, board.request, board.health)
        self.assertEqual(result['recorded'], 3); self.assertEqual(result['remaining'], 0)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertEqual(helper.flush(self.runtime, self.config, board.request, board.health)['recorded'], 0)

    def test_conflict_blocks_only_its_own_task_and_never_rewrites_version(self):
        self.active(); self.online()
        conflict = self.req('freeze', version=0); other = self.req('create', {'goal': 'other', 'mode': 'plan_execute', 'user_request': 'user'}, task='other', version=0)
        helper.enqueue(self.runtime, conflict, self.config); helper.enqueue(self.runtime, other, self.config)
        result = helper.flush(self.runtime, self.config, board.request, board.health)
        self.assertEqual(result['recorded'], 1); self.assertEqual(result['remaining'], 1)
        self.assertEqual(result['status'], 'CONFLICT_NOT_RECORDED')
        self.assertEqual(helper.load(self.runtime / 'task-board-outbox' / helper.filename(conflict['event_id']))['request'], conflict)

    def test_cli_real_commands_and_explicit_cached_baseline(self):
        self.online(); (self.root / '.ai-workflow').mkdir(); (self.root / '.ai-workflow' / 'WORKFLOW.md').write_text('synthetic project')
        config = self.root / 'config.json'; atomic_json(config, self.config)
        data = self.root / 'create.json'; atomic_json(data, {'goal': 'user task', 'mode': 'plan_execute', 'user_request': 'user'})
        cli = [sys.executable, '-B', str(Path(board.__file__)), '--config', str(config), '--runtime', str(self.runtime), 'task-board']
        result = subprocess.run(cli + ['create', 't', '--expected-version', '0', '--data', str(data), '--event-id', 'cli-create'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(json.loads(result.stdout)['status'], 'RECORDED')
        result = subprocess.run(cli + ['summary', 't'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(json.loads(result.stdout)['summary']['state'], 'draft')
        result = subprocess.run(cli + ['summary', 't', '--cached'], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'CACHED_BASELINE')

    def test_explicit_withdraw_retains_failed_request_without_remote_mutation(self):
        self.active(); before = plans.read(self.store, 't')
        request = self.req('freeze', version=0); helper.enqueue(self.runtime, request, self.config)
        digest = hashlib.sha256(packed(request).encode()).hexdigest()
        args = SimpleNamespace(plan_command='withdraw', event_id=request['event_id'], expected_digest='wrong', reason='reconcile conflict')
        with self.assertRaises(ValueError): helper.run(args, self.config, self.runtime, board.request, board.health)
        args.expected_digest=digest
        result=helper.run(args, self.config, self.runtime, board.request, board.health)
        self.assertEqual(result['status'], 'WITHDRAWN_LOCAL_ONLY')
        self.assertEqual(helper.load(result['evidence'])['request'], request)
        self.assertEqual(plans.read(self.store, 't'), before)
        self.assertFalse((self.runtime/'task-board-outbox'/helper.filename(request['event_id'])).exists())

    def test_no_ttl_or_business_status_inheritance(self):
        self.active(); value = plans.read(self.store, 't')
        self.assertNotIn('display_expired', value); self.assertNotIn('deliveries', value)
        self.write('progress', {'mainline_id': 'first', 'fields': {'status': 'done', 'result': 'author complete'}})
        self.assertEqual(self.store.view('t')['deliveries'], [])

    def test_frozen_execution_arrangement_can_change_without_replanning(self):
        self.active(); before = plans.read(self.store, 't')['board']['mainlines']
        self.write('progress', {'mainline_id': 'first', 'fields': {'dependencies': ['second'], 'parallel_with': [], 'next_step': 'do independent second first'}})
        after = plans.read(self.store, 't')['board']['mainlines']
        self.assertEqual([v['id'] for v in before], [v['id'] for v in after])
        self.assertEqual(before[0]['completion'], after[0]['completion'])
        with self.assertRaises(ValueError): self.write('progress', {'mainline_id': 'first', 'fields': {'dependencies': ['missing']}})

    def test_close_reopen_and_delete_are_scoped_to_own_plan(self):
        self.active(); self.active('other'); other = plans.read(self.store, 'other')
        with self.assertRaises(Conflict): self.write('delete', {'reason': 'requested', 'decision_ref': 'user'})
        self.write('close', {'reason': 'pause close'})
        with self.assertRaises(Conflict): self.write('progress', {'mainline_id': 'first', 'fields': {'next_step': 'late'}})
        self.write('reopen', {'reason': 'resume same task', 'decision_ref': 'user'})
        self.assertEqual(plans.read(self.store, 't')['board']['state'], 'active')
        self.write('close', {'reason': 'end task'});self.write('delete', {'reason': 'delete own board', 'decision_ref': 'user'})
        self.assertEqual(other, plans.read(self.store, 'other'))


if __name__ == '__main__': unittest.main()
