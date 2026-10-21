"""Storage identity, case-distinct names, quarantine and service-pointer checks; never an installed/live runtime."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Protocol, Store, packed
from server import BoardServer, atomic_json


class StorageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-bsa-')
        self.root = Path(self.temp.name)
        (self.root / '.ai-workflow').mkdir()
        (self.root / '.ai-workflow/WORKFLOW.md').write_text('synthetic', encoding='utf-8')
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Synthetic'}
        self.store = Store(self.runtime, self.config)
        self.servers = []
        self.config_path = self.root / 'config.json'
        atomic_json(self.config_path, self.config)

    def tearDown(self):
        for server, worker in reversed(self.servers):
            server.shutdown(); server.server_close(); worker.join(timeout=5)
        self.temp.cleanup()

    def listen(self, server):
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start(); self.servers.append((server, worker))
        return server

    def online(self):
        server = self.listen(BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345'))
        atomic_json(self.runtime / 'instance.json', {'url': f'http://127.0.0.1:{server.server_port}',
                    'pid': os.getpid(), 'instance_id': server.instance})
        (self.runtime / 'write-token').write_text(server.token, encoding='ascii')
        return server

    def call(self, *args):
        return subprocess.run([sys.executable, '-B', str(Path(board.__file__)),
            '--config', str(self.config_path), '--runtime', str(self.runtime), *args],
            capture_output=True, text=True, encoding='utf-8', timeout=15)

    def event(self, op, event_id, task='t', run=None, seq=None, data=None):
        return dict(board.envelope(op, task, data or {}, run, seq), event_id=event_id)

    def initialize(self, task='t', run='r'):
        for event in [self.event('task', 'task-'+task, task,
                     data={'title': task, 'project': 'Synthetic', 'workstream': 'Test'}),
                     self.event('run', 'run-'+run, task, run, 1,
                     {'role': 'execution', 'agent': 'test', 'client': 'test'}),
                     self.event('claim', 'claim-'+run, task, run, 2, {'expected_version': 0})]:
            self.store.submit(event)

    def bad_audit(self, event_id='bad-audit', seq=3):
        return self.event('delivery', event_id, run='r', seq=seq,
            data={'stage': 'independent_audit', 'verdict': 'PASS', 'candidate_sha': 'a'*40,
                  'evidence': 'synthetic-report.md', 'audit_id': 'synthetic-audit'})

    def test_case_distinct_events_and_runs_deliver_without_loss(self):
        self.initialize('t', 'CaseRun')
        self.initialize('T', 'caserun')
        events = [self.event('progress', 'CaseEvent', 't', 'CaseRun', 3, {'phase': 'first'}),
                  self.event('progress', 'caseevent', 'T', 'caserun', 3, {'phase': 'second'})]
        for event in events:
            board.enqueue(self.runtime, event, self.config)
        self.assertEqual(board.pending(self.runtime)['remaining'], 2)
        self.assertEqual({p['event_id'] for p in board.pending(self.runtime)['events']}, {'CaseEvent', 'caseevent'})
        first_bytes = {p: p.read_bytes() for p in (self.runtime/'outbox').glob('*/*.json')}
        board.enqueue(self.runtime, events[0], self.config)
        self.assertEqual({p: p.read_bytes() for p in first_bytes}, first_bytes)
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            board.enqueue(self.runtime, dict(events[0], data={'phase': 'altered'}), self.config)
        self.assertEqual({p: p.read_bytes() for p in first_bytes}, first_bytes)
        self.online()
        self.assertEqual(board.flush(self.runtime, self.config)['recorded'], 2)
        self.assertEqual(self.store.view('t')['state']['phase'], 'first')
        self.assertEqual(self.store.view('T')['state']['phase'], 'second')
        self.assertEqual(board.pending(self.runtime)['remaining'], 0)

    def test_task_and_run_namespaces_and_case_are_distinct(self):
        events = [self.event('task', 'task-A', 'CaseTask', data={'title':'A','project':'Synthetic','workstream':'Test'}),
                  self.event('task', 'task-a', 'casetask', data={'title':'a','project':'Synthetic','workstream':'Test'}),
                  self.event('progress', 'progress', 'CaseTask', 'task-CaseTask', 1, {'phase':'separate'})]
        paths = [board.enqueue(self.runtime, e, self.config) for e in events]
        self.assertEqual(len({p.parent for p in paths}), 3)
        self.assertEqual(board.pending(self.runtime)['remaining'], 3)

    def test_sequence_keeps_case_distinct_identities(self):
        self.assertEqual(board.sequence(self.runtime, 'CaseRun'), 1)
        # Case-insensitive file systems must not merge the two counters.
        self.assertEqual(board.sequence(self.runtime, 'caserun'), 1)
        self.assertEqual(board.sequence(self.runtime, 'CaseRun'), 2)
        self.assertEqual(board.sequence(self.runtime, 'caserun', minimum=80), 81)
        self.assertEqual(board.sequence(self.runtime, 'CaseRun'), 3)

    def test_physical_collision_never_replaces_another_event(self):
        first = self.event('task', 'first', data={'title':'T','project':'Synthetic','workstream':'Test'})
        second = dict(first, event_id='second')
        with patch.object(board, 'storage_name', return_value='synthetic-collision'):
            path = board.enqueue(self.runtime, first, self.config)
            original = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'physical identity collision'):
                board.enqueue(self.runtime, second, self.config)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(board.pending(self.runtime)['remaining'], 1)

    def test_case_distinct_quarantine_preserves_both_raw_inputs(self):
        self.initialize(); self.online()
        first = self.bad_audit('CaseConflict', 3)
        second = self.bad_audit('caseconflict', 4)
        raw = {}
        for event in [first, second]:
            path = board.enqueue(self.runtime, event, self.config)
            raw[event['event_id']] = path.read_bytes()
        archives = []
        for event in [first, second]:
            digest = hashlib.sha256(raw[event['event_id']]).hexdigest()
            result = board.quarantine(self.runtime, self.config, event['event_id'], digest, 'verified role mismatch')
            archives.append(Path(result['preserved_at']))
            self.assertEqual(base64.b64decode(board.read_json(archives[-1])['queue_raw_b64']), raw[event['event_id']])
        self.assertNotEqual(archives[0], archives[1])
        self.assertEqual(board.pending(self.runtime)['remaining'], 0)
        self.assertEqual(self.store.view()['event_count'], 3)

    def test_cli_role_conflict_quarantine_then_valid_progress(self):
        self.initialize(); self.online()
        bad = self.bad_audit()
        raw_path = board.enqueue(self.runtime, bad, self.config)
        raw = raw_path.read_bytes()
        good = self.event('progress', 'good-progress', run='r', seq=4, data={'phase':'recovered'})
        board.enqueue(self.runtime, good, self.config)
        denied = self.call('flush')
        self.assertEqual(denied.returncode, 2, denied.stdout+denied.stderr)
        self.assertIn('execution run role is immutable', denied.stdout)
        self.assertEqual(self.store.view()['event_count'], 3)
        self.assertEqual(self.store.view('t')['deliveries'], [])
        listing = json.loads(self.call('pending').stdout)
        self.assertEqual(listing['remaining'], 2)
        head = listing['events'][0]
        rejected = self.call('quarantine', bad['event_id'], '--expected-sha256','0'*64, '--reason','role mismatch')
        self.assertEqual(rejected.returncode, 1)
        accepted = self.call('quarantine', bad['event_id'], '--expected-sha256',head['queue_sha256'], '--reason','verified role mismatch')
        self.assertEqual(accepted.returncode, 0, accepted.stdout+accepted.stderr)
        result = json.loads(accepted.stdout)
        archive = board.read_json(Path(result['preserved_at']))
        self.assertEqual(base64.b64decode(archive['queue_raw_b64']), raw)
        self.assertEqual(result['verification'], 'confirmed_remote_409')
        self.assertEqual(self.store.view()['event_count'], 3)
        flushed = self.call('flush')
        self.assertEqual(flushed.returncode, 0, flushed.stdout+flushed.stderr)
        self.assertEqual(self.store.view('t')['state']['phase'], 'recovered')
        self.assertEqual(self.store.view('t')['version'], 1)

    def test_capacity_is_not_quarantinable_conflict(self):
        self.initialize(); self.config['max_events'] = 3; self.online()
        event = self.event('progress', 'capacity', run='r', seq=3, data={'phase':'retain'})
        path = board.enqueue(self.runtime, event, self.config)
        raw = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'HTTP 400'):
            board.quarantine(self.runtime, self.config, event['event_id'], hashlib.sha256(raw).hexdigest(), 'capacity')
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.store.view()['event_count'], 3)

    def test_cli_stale_pointer_rejects_all_reads_and_drafts(self):
        self.initialize()
        server = self.online()
        info = board.read_json(self.runtime/'instance.json')
        atomic_json(self.runtime/'instance.json', dict(info, instance_id='old-instance'))
        self.assertEqual(json.loads(self.call('status').stdout)['status'], 'STOPPED_OR_UNVERIFIED')
        for arguments in [('show',), ('show','t'), ('draft','t')]:
            result = self.call(*arguments)
            self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'ERROR')
            self.assertNotIn('"current_run"', result.stdout)
        atomic_json(self.runtime/'instance.json', dict(info, instance_id=server.instance))
        for arguments in [('show',), ('show','t'), ('draft','t')]:
            result = self.call(*arguments)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn(server.instance, result.stdout)
            self.assertIn('"current_run":"r"' if arguments[0]=='draft' else '"current_run": "r"', result.stdout)

    def test_response_identity_bound_to_pointer_before_request(self):
        runtime = self.runtime
        observed_headers = []
        class ReusedPort(BaseHTTPRequestHandler):
            def do_GET(self):
                observed_headers.append(self.headers.get('X-Agent-Board-Instance'))
                old = board.read_json(runtime/'instance.json')
                atomic_json(runtime/'instance.json', dict(old, instance_id='replacement'))
                payload = packed({'service':'agent-board-v1', 'instance_id':'replacement', 'tasks':[]}).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = self.listen(ThreadingHTTPServer(('127.0.0.1',0), ReusedPort))
        atomic_json(runtime/'instance.json', {'url':f'http://127.0.0.1:{server.server_port}', 'instance_id':'expected','pid':0})
        with self.assertRaisesRegex(RuntimeError, 'UNCONFIRMED_READ_IDENTITY'):
            board.request(runtime, '/api/state')
        self.assertEqual(observed_headers, ['expected'])

    def test_quarantine_rejects_instance_replacement_and_retains_queue(self):
        self.initialize(); server = self.online()
        event = self.bad_audit('restart-conflict')
        path = board.enqueue(self.runtime, event, self.config)
        raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        info = board.read_json(self.runtime/'instance.json')
        real_health = board.health
        replacement = 'synthetic-restarted-instance'
        def restart_after_health(runtime):
            observed = real_health(runtime)
            server.instance = replacement
            atomic_json(runtime/'instance.json', dict(info, instance_id=replacement))
            return observed
        # Both health and POST use real HTTP; force only their restart ordering.
        with patch.object(board, 'health', side_effect=restart_after_health):
            with self.assertRaisesRegex(RuntimeError, 'UNCONFIRMED_CONFLICT_RESPONSE'):
                board.quarantine(self.runtime, self.config, event['event_id'], digest, 'restart boundary')
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(board.pending(self.runtime)['remaining'], 1)
        self.assertEqual(list((self.runtime/'quarantine').rglob('*.json')), [])
        self.assertEqual(self.store.view()['event_count'], 3)
        # A new explicit verification of the replacement can now recover safely.
        result = board.quarantine(self.runtime, self.config, event['event_id'], digest, 'verified replacement role mismatch')
        archive = board.read_json(Path(result['preserved_at']))
        self.assertEqual(archive['resolution']['instance_id'], replacement)
        self.assertEqual(base64.b64decode(archive['queue_raw_b64']), raw)
        self.assertEqual(board.pending(self.runtime)['remaining'], 0)

    def test_actual_restart_to_new_url_retains_queue_then_explicit_retry(self):
        self.initialize(); first = self.online()
        first_worker = self.servers[-1][1]
        event = self.bad_audit('actual-restart-conflict')
        path = board.enqueue(self.runtime, event, self.config)
        raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        real_health = board.health
        replacement = []
        def replace_after_health(runtime):
            observed = real_health(runtime)
            first.shutdown(); first.server_close(); first_worker.join(timeout=5)
            self.assertFalse(first_worker.is_alive())
            reopened = Store(runtime, self.config)
            second = self.listen(BoardServer(('127.0.0.1',0), reopened, first.token))
            replacement.append(second)
            atomic_json(runtime/'instance.json', {'url':f'http://127.0.0.1:{second.server_port}',
                        'pid':os.getpid(), 'instance_id':second.instance})
            return observed
        with patch.object(board, 'health', side_effect=replace_after_health):
            with self.assertRaises((OSError, RuntimeError)):
                board.quarantine(self.runtime, self.config, event['event_id'], digest, 'actual restart boundary')
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(board.pending(self.runtime)['remaining'], 1)
        self.assertEqual(list((self.runtime/'quarantine').rglob('*.json')), [])
        self.assertEqual(self.store.view()['event_count'], 3)
        result = board.quarantine(self.runtime, self.config, event['event_id'], digest, 'explicit retry after replacement')
        archive = board.read_json(Path(result['preserved_at']))
        self.assertEqual(archive['resolution']['instance_id'], replacement[0].instance)
        self.assertEqual(base64.b64decode(archive['queue_raw_b64']), raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
