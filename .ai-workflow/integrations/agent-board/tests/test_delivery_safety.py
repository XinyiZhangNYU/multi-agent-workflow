"""Owned synthetic counterexamples for delivery, receipts, conflicts and snapshot import."""
import base64
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
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Protocol, Store, now, packed
from server import BoardServer, atomic_json


CLI = Path(board.__file__)


class DeliverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-board-audit-fix-')
        self.root = Path(self.temp.name)
        (self.root / '.ai-workflow').mkdir()
        (self.root / '.ai-workflow/WORKFLOW.md').write_text('synthetic', encoding='utf-8')
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Synthetic', 'port': 0}
        self.store = Store(self.runtime, self.config)
        self.servers = []

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

    def event(self, op, task='t', data=None, run=None, seq=None, source=None):
        return board.envelope(op, task, data or {}, run, seq, source)

    def task(self):
        return self.event('task', data={'title': 'Task', 'project': 'Synthetic', 'workstream': 'Test'})

    def test_distinct_bad_receipts_keep_original(self):
        response = {}
        sent_replies = []

        class Fake(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = packed(response).encode()
                sent_replies.append(json.loads(payload))
                self.send_response(200); self.send_header('Content-Length', str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            def log_message(self, *args):
                pass

        server = self.listen(ThreadingHTTPServer(('127.0.0.1', 0), Fake))
        atomic_json(self.runtime / 'instance.json', {'url': f'http://127.0.0.1:{server.server_port}',
                    'pid': 999, 'instance_id': 'fake'})
        (self.runtime / 'write-token').write_text('synthetic-only', encoding='ascii')
        event = self.task()
        valid = {'service': 'agent-board-v1', 'instance_id': 'fake', 'recorded': True,
                 'event_id': event['event_id'],
                 'digest': hashlib.sha256(packed(Protocol(self.config).validate(event)).encode()).hexdigest(),
                 'duplicate': False, 'applied': True}
        cases = [
            ('empty', {}),
            ('service', dict(valid, service='unrelated')),
            ('instance', dict(valid, instance_id='wrong')),
            ('event_id', dict(valid, event_id='wrong')),
            ('digest', dict(valid, digest='0'*64)),
            ('recorded', dict(valid, recorded=False)),
            ('duplicate_type', dict(valid, duplicate='false')),
            ('applied_type', dict(valid, applied=1)),
        ]
        original_hash = None
        for name, reply in cases:
            response = reply
            result = board.send(self.runtime, event, self.config)
            self.assertEqual(sent_replies[-1], reply, name)
            self.assertEqual(result['status'], 'QUEUED_NOT_RECORDED', name)
            queued = board.pending(self.runtime)['events']
            self.assertEqual(len(queued), 1, name)
            original_hash = original_hash or queued[0]['queue_sha256']
            self.assertEqual(queued[0]['queue_sha256'], original_hash, name)
            self.assertEqual(self.store.view()['event_count'], 0, name)
        self.assertEqual(sent_replies, [reply for _, reply in cases])

    def test_redirect_never_forwards_credential(self):
        captured = []

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append(self.headers.get('Authorization'))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self, *args):
                pass

        target = self.listen(ThreadingHTTPServer(('127.0.0.1', 0), Target))

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{target.server_port}/elsewhere')
                self.end_headers()
            def log_message(self, *args):
                pass

        source = self.listen(ThreadingHTTPServer(('127.0.0.1', 0), Redirect))
        atomic_json(self.runtime / 'instance.json', {'url': f'http://127.0.0.1:{source.server_port}',
                    'pid': 999, 'instance_id': 'fake'})
        (self.runtime / 'write-token').write_text('synthetic-only', encoding='ascii')
        self.assertEqual(board.send(self.runtime, self.task(), self.config)['status'], 'QUEUED_NOT_RECORDED')
        self.assertEqual(captured, [])
        self.assertEqual(board.pending(self.runtime)['remaining'], 1)

    def test_lost_ack_then_duplicate_receipt_clears_queue(self):
        self.online()
        event = self.task()
        board.enqueue(self.runtime, event, self.config)
        original = board.request
        def lost_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('synthetic dropped reply after commit')
        board.request = lost_ack
        try:
            self.assertEqual(board.flush(self.runtime, self.config)['remaining'], 1)
        finally:
            board.request = original
        self.assertEqual(self.store.view()['event_count'], 1)
        self.assertEqual(board.flush(self.runtime, self.config)['remaining'], 0)
        self.assertEqual(self.store.view()['event_count'], 1)

    def test_wrong_instance_header_cannot_write_event(self):
        server = self.online()
        event = self.task()
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/events',
                    data=packed(event).encode('utf-8'), headers={'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + server.token, 'X-Agent-Board-Instance': 'stale-instance'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.store.view()['event_count'], 0)

    def test_explicit_conflict_quarantine_then_corrected_claim(self):
        self.online()
        initial = [self.task(), self.event('run', data={'role': 'execution', 'agent': 'one', 'client': 'test'}, run='first', seq=1),
                   self.event('claim', data={'expected_version': 0}, run='first', seq=2),
                   self.event('run', data={'role': 'execution', 'agent': 'two', 'client': 'test'}, run='second', seq=1)]
        for item in initial:
            self.assertEqual(board.send(self.runtime, item, self.config)['status'], 'RECORDED')
        wrong = self.event('claim', data={'expected_version': 0}, run='second', seq=2)
        corrected = self.event('claim', data={'expected_version': 1}, run='second', seq=3)
        board.enqueue(self.runtime, wrong, self.config); board.enqueue(self.runtime, corrected, self.config)
        self.assertEqual(board.flush(self.runtime, self.config)['remaining'], 2)
        config_path = self.root / 'config.json'
        atomic_json(config_path, self.config)
        cmd = [sys.executable, '-B', str(CLI), '--config', str(config_path), '--runtime', str(self.runtime)]
        listing = subprocess.run(cmd + ['pending'], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
        entry = json.loads(listing.stdout)['events'][0]
        arguments = ['quarantine', wrong['event_id'], '--reason', 'verified old version', '--expected-sha256']
        rejected = subprocess.run(cmd + arguments + ['0'*64], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(board.pending(self.runtime)['remaining'], 2)
        accepted = subprocess.run(cmd + arguments + [entry['queue_sha256']], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        result = json.loads(accepted.stdout)
        self.assertEqual(result['status'], 'QUARANTINED')
        preserved = json.loads(Path(result['preserved_at']).read_text(encoding='utf-8'))
        self.assertEqual(preserved['queue_item']['event'], Protocol(self.config).validate(wrong))
        self.assertEqual(board.flush(self.runtime, self.config)['remaining'], 0)
        self.assertEqual(self.store.view('t')['current_run'], 'second')

    def test_per_source_selection_does_not_erase_conflict(self):
        self.store.submit(self.task())
        base = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        for n in range(202):
            label, phase = ('old', 'different') if n == 0 else ('new', 'latest')
            source = {'kind': 'document_snapshot', 'path': f'{label}.md', 'label': label,
                      'source_at': base, 'read_at': now(), 'sha256': '1'*64}
            self.store.submit(self.event('snapshot', data={'fields': {'phase': phase}, 'excerpt': phase}, source=source))
        view = self.store.view('t')
        self.assertEqual(len(view['snapshots']), 2)
        self.assertEqual(view['conflicts'][0]['field'], 'phase')
        self.assertFalse(view['snapshots_truncated'])
        self.assertTrue(view['needs_attention'])

    def test_many_distinct_sources_are_explicitly_truncated(self):
        self.store.submit(self.task())
        for n in range(201):
            source = {'kind': 'document_snapshot', 'path': f'source-{n}.md',
                      'source_at': '2026-01-01T00:00:00Z', 'read_at': now(), 'sha256': '1'*64}
            self.store.submit(self.event('snapshot', data={'fields': {'phase': 'same'}, 'excerpt': 'same'}, source=source))
        view = self.store.view('t')
        self.assertEqual(len(view['snapshots']), 200)
        self.assertEqual(view['snapshot_source_count'], 201)
        self.assertTrue(view['snapshots_truncated'])
        self.assertTrue(view['needs_attention'])

    def test_future_source_rejected_and_stored_bad_clock_not_selected(self):
        self.store.submit(self.task())
        source = {'kind': 'program_observation', 'path': 'observation.json',
                  'source_at': now(), 'read_at': now(), 'label': 'local'}
        current = self.event('activity', data={'summary': 'current'}, source=source)
        self.store.submit(current)
        future = self.event('activity', data={'summary': 'stored clock error'}, source=dict(source, source_at='2999-01-01T00:00:00Z'))
        with self.assertRaises(ValueError):
            Protocol(self.config).validate(future)
        body = packed(future)
        with self.store.connect() as db:
            db.execute('INSERT INTO events(id,digest,task_id,run_id,seq,op,received_at,occurred_at,body,applied,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (future['event_id'], hashlib.sha256(body.encode()).hexdigest(), 't', None, None,
                        'activity', now(), future['occurred_at'], body, 1, 'synthetic'))
            mismatch = self.event('activity', data={'summary': 'stored read before source'},
                source=dict(source, source_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
                            read_at=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()))
            mismatched_body = packed(mismatch)
            db.execute('INSERT INTO events(id,digest,task_id,run_id,seq,op,received_at,occurred_at,body,applied,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (mismatch['event_id'], hashlib.sha256(mismatched_body.encode()).hexdigest(), 't', None, None,
                        'activity', now(), mismatch['occurred_at'], mismatched_body, 1, 'synthetic'))
        view = self.store.view('t')
        self.assertEqual(view['activity']['data']['summary'], 'current')
        self.assertEqual(view['time_warnings'], 2)
        self.assertTrue(view['needs_attention'])

    def test_nested_and_batch_exit_codes(self):
        self.assertEqual(board.exit_code({'status': 'RUNNING', 'outbox': {'remaining': 2}}), 2)
        self.assertEqual(board.exit_code([{'status': 'RECORDED'}, {'status': 'QUEUED_NOT_RECORDED'}]), 2)
        self.assertEqual(board.exit_code({'status': 'STOP_UNCONFIRMED'}), 1)

    def test_start_does_not_replay_queue_and_explicit_flush_reports_conflict(self):
        config_path = self.root / 'config.json'
        atomic_json(config_path, self.config)
        for event in [self.task(),
                      self.event('run', data={'role': 'execution', 'agent': 'test', 'client': 'test'}, run='r', seq=1),
                      self.event('claim', data={'expected_version': 1}, run='r', seq=2)]:
            board.enqueue(self.runtime, event, self.config)
        cmd = [sys.executable, '-B', str(CLI), '--config', str(config_path), '--runtime', str(self.runtime)]
        try:
            started = subprocess.run(cmd + ['start'], capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            result = json.loads(started.stdout)
            self.assertEqual(result['status'], 'RUNNING')
            self.assertEqual(result['outbox']['status'], 'NOT_REPLAYED')
            self.assertEqual(board.pending(self.runtime)['remaining'],3)
            explicit=subprocess.run(cmd+['flush','--task','t'],capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(explicit.returncode,2,explicit.stdout+explicit.stderr)
            flushed=json.loads(explicit.stdout)
            self.assertEqual(flushed['remaining'],1)
            self.assertEqual(flushed['errors'][0]['event_id'],board.pending(self.runtime)['events'][0]['event_id'])
        finally:
            try:
                board.health(self.runtime)
            except (OSError, ValueError, RuntimeError, KeyError):
                pass
            else:
                stopped = subprocess.run(cmd + ['stop'], capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)

    def test_real_cli_snapshot_import_and_refresh(self):
        source = self.root / 'source.md'
        raw = b'## State\r\nSnapshot text\r\n'
        source.write_bytes(raw)
        config_path = self.root / 'config.json'
        snapshot = {'task_id': 'snapshot-task', 'title': 'Snapshot', 'workstream': 'Research',
                    'path': 'source.md', 'heading': '## State', 'source_at': None,
                    'fields': {'phase': 'first'}}
        self.config['snapshots'] = [snapshot]
        atomic_json(config_path, self.config)
        cmd = [sys.executable, '-B', str(CLI), '--config', str(config_path), '--runtime', str(self.runtime)]
        def call(command, expected):
            run = subprocess.run(cmd + [command], capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
            return json.loads(run.stdout)
        queued = call('import-snapshots', 2)
        self.assertEqual([item['status'] for item in queued], ['QUEUED_NOT_RECORDED']*2)
        stored = [json.loads(p.read_text(encoding='utf-8'))['event'] for p in (self.runtime/'outbox').glob('*/*.json')]
        snap = next(item for item in stored if item['op'] == 'snapshot')
        self.assertNotIn('\r', snap['data']['excerpt'])
        self.assertEqual(snap['source']['sha256'], hashlib.sha256(raw).hexdigest())
        self.online()
        self.assertEqual(board.flush(self.runtime, self.config)['remaining'], 0)
        snapshot['source_at'] = (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        snapshot['fields']['phase'] = 'second'
        atomic_json(config_path, self.config)
        self.assertEqual([item['status'] for item in call('import-snapshots', 0)], ['RECORDED']*2)
        self.assertEqual(self.store.view('snapshot-task')['snapshots'][0]['data']['fields']['phase'], 'second')
        self.assertEqual(self.store.view('snapshot-task')['title'], 'Snapshot')

    def test_import_does_not_overwrite_existing_task_metadata(self):
        self.online()
        self.store.submit(self.task())
        source = self.root / 'status.md'
        source.write_text('## State\nObserved\n', encoding='utf-8')
        config_path = self.root / 'config.json'
        self.config['snapshots'] = [{'task_id': 't', 'title': 'Different imported title',
                    'workstream': 'Other', 'path': 'status.md', 'heading': '## State',
                    'source_at': None, 'fields': {'phase': 'document'}}]
        atomic_json(config_path, self.config)
        cmd = [sys.executable, '-B', str(CLI), '--config', str(config_path), '--runtime', str(self.runtime), 'import-snapshots']
        run = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual([item['status'] for item in json.loads(run.stdout)], ['RECORDED']*2)
        view = self.store.view('t')
        self.assertEqual(view['title'], 'Task')
        self.assertEqual(view['snapshots'][0]['data']['fields']['phase'], 'document')
        self.assertEqual(next(e for e in view['events'] if e['op'] == 'ensure_task')['reason'],
                         'task_exists_metadata_unchanged')


if __name__ == '__main__':
    unittest.main(verbosity=2)
