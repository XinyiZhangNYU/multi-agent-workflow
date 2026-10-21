"""Registration checks, offline queue wording, parallel runs and workstream names; never an installed/live runtime."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import NotFound, Store, related_names
from server import BoardServer, atomic_json


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-registration-')
        self.root = Path(self.temp.name)
        (self.root / '.ai-workflow').mkdir()
        (self.root / '.ai-workflow/WORKFLOW.md').write_text('synthetic', encoding='utf-8')
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Synthetic', 'stale_seconds': 3600}
        self.store = Store(self.runtime, self.config)
        self.config_path = self.root / 'config.json'
        atomic_json(self.config_path, self.config)
        self.servers = []

    def tearDown(self):
        for server, worker in reversed(self.servers):
            server.shutdown(); server.server_close(); worker.join(timeout=5)
        self.temp.cleanup()

    def online(self):
        server = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start(); self.servers.append((server, worker))
        atomic_json(self.runtime / 'instance.json', {'url': f'http://127.0.0.1:{server.server_port}',
                    'pid': os.getpid(), 'instance_id': server.instance})
        (self.runtime / 'write-token').write_text(server.token, encoding='ascii')
        return server

    def offline(self):
        for server, worker in reversed(self.servers):
            server.shutdown(); server.server_close(); worker.join(timeout=5)
        self.servers.clear()  # instance.json now names a closed port.

    def call(self, *args):
        completed = subprocess.run([sys.executable, '-B', str(Path(board.__file__)), '--config', str(self.config_path),
                                    '--runtime', str(self.runtime), *args],
                                   capture_output=True, text=True, encoding='utf-8', timeout=20)
        return completed.returncode, json.loads(completed.stdout)

    def submit(self, op, task='t', run=None, seq=None, data=None, occurred=None):
        event = board.envelope(op, task, data or {}, run, seq)
        if occurred:
            event['occurred_at'] = occurred
        return self.store.submit(event)

    def task(self, task='t'):
        self.submit('task', task, data={'title': task, 'project': 'Synthetic', 'workstream': 'Test'})

    def add_run(self, run, task='t', claim=True, role='execution'):
        self.submit('run', task, run, 1, {'role': role, 'agent': 'Test', 'client': 'test'})
        if claim:
            self.submit('claim', task, run, 2, {'expected_version': self.store.view(task)['version']})

    # A1: an unregistered run never reaches the queue or consumes a sequence.
    def test_cli_refuses_progress_for_unregistered_run(self):
        self.task(); self.online()
        code, out = self.call('report', 't', 'ghost', '--phase', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('RUN_NOT_REGISTERED', out['error'])
        self.assertEqual(list((self.runtime / 'outbox').glob('*/*.json')), [])
        self.assertFalse((self.runtime / 'sequences').exists() and any((self.runtime / 'sequences').rglob('*.json')))

    def test_cli_refuses_run_for_missing_task(self):
        self.online()
        code, out = self.call('run', 'missing', 'r', '--agent', 'Test', '--client', 'test')
        self.assertEqual(code, 1, out)
        self.assertIn('TASK_NOT_REGISTERED', out['error'])
        self.assertEqual(list((self.runtime / 'outbox').glob('*/*.json')), [])

    def test_cli_run_registered_under_another_task_is_refused(self):
        self.task('a'); self.task('b'); self.add_run('r', 'a'); self.online()
        code, out = self.call('report', 'b', 'r', '--phase', 'x')
        self.assertEqual(code, 1, out)
        self.assertIn('RUN_NOT_REGISTERED', out['error'])

    def test_cli_registered_run_still_records(self):
        self.task(); self.add_run('r'); self.online()
        board.sequence(self.runtime, 'r', minimum=2)  # Registered directly, not through this CLI.
        code, out = self.call('report', 't', 'r', '--phase', 'ok')
        self.assertEqual((code, out['status']), (0, 'RECORDED'), out)
        self.assertNotIn('registration', out)
        self.assertEqual(self.store.view('t')['state']['phase'], 'ok')

    def test_cli_queued_registration_counts_offline(self):
        code, out = self.call('task', 't', '--title', 'T', '--workstream', 'W')
        self.assertEqual(code, 2, out)
        code, out = self.call('run', 't', 'r', '--agent', 'Test', '--client', 'test')
        self.assertEqual(code, 2, out)
        self.assertNotIn('registration', out)  # The queued task registration is accepted.
        code, out = self.call('report', 't', 'r', '--phase', 'offline')
        self.assertEqual(code, 2, out)
        self.assertNotIn('registration', out)
        self.assertFalse(out['board_updated'])
        self.assertIn('Board unconfirmed', out['warning'])

    # With no basis for the registration, an offline event is held, never queued.
    def test_offline_report_without_registration_basis_is_held(self):
        self.task()  # Known to the service store, never confirmed by this machine.
        code, out = self.call('report', 't', 'ghost', '--phase', 'x')
        self.assertEqual((code, out['status']), (4, 'UNVERIFIED_NOT_QUEUED'), out)
        self.assertFalse(out['board_updated'])
        self.assertIn('Not queued', out['warning'])
        self.assertEqual(list((self.runtime / 'outbox').glob('*/*.json')), [])
        self.assertFalse((self.runtime / 'sequences').exists())
        held = json.loads(Path(out['held']).read_text(encoding='utf-8'))
        self.assertEqual((held['event']['op'], held['event']['run_id'], held['event']['seq']), ('progress', 'ghost', None))
        self.online()
        code, out = self.call('run', 't', 'ghost', '--agent', 'Test', '--client', 'test')
        self.assertEqual(code, 0, out)
        code, out = self.call('report', 't', 'ghost', '--phase', 'x')
        self.assertEqual((code, out['status']), (0, 'RECORDED'), out)
        self.assertEqual(next(r for r in self.store.view('t')['runs'] if r['id'] == 'ghost')['state']['phase'], 'x')

    def test_offline_report_for_confirmed_run_is_queued(self):
        self.task(); self.online()
        code, out = self.call('run', 't', 'r', '--agent', 'Test', '--client', 'test')
        self.assertEqual(code, 0, out)
        self.offline()
        code, out = self.call('report', 't', 'r', '--phase', 'offline')
        self.assertEqual((code, out['status']), (2, 'QUEUED_NOT_RECORDED'), out)
        self.assertIn('unconfirmed', out['warning']); self.assertNotIn('rejected', out['warning'])
        self.online()
        self.assertEqual(board.flush(self.runtime, self.config, 't')['recorded'], 1)
        self.assertEqual(next(r for r in self.store.view('t')['runs'] if r['id'] == 'r')['state']['phase'], 'offline')

    def test_offline_retag_and_run_without_task_basis_are_held(self):
        self.task()
        code, out = self.call('retag', 't', '--workstream', 'Moved', '--reason', 'misfiled')
        self.assertEqual((code, out['status']), (4, 'UNVERIFIED_NOT_QUEUED'), out)
        code, out = self.call('run', 't', 'r', '--agent', 'Test', '--client', 'test')
        self.assertEqual((code, out['status']), (4, 'UNVERIFIED_NOT_QUEUED'), out)
        self.assertEqual(list((self.runtime / 'outbox').glob('*/*.json')), [])
        self.assertEqual(self.store.view('t')['workstream'], 'Test')

    # A lost acknowledgement is unconfirmed, not a rejection.
    def test_lost_ack_is_reported_as_unconfirmed(self):
        self.task(); self.add_run('r'); self.online()
        real = board.request

        def lost_ack(runtime, path, data=None, *args, **kwargs):
            result = real(runtime, path, data, *args, **kwargs)
            if path == '/api/events':
                raise ConnectionResetError('synthetic lost acknowledgement')
            return result
        event = board.envelope('progress', 't', {'phase': 'committed'}, 'r', 3)
        with mock.patch.object(board, 'request', lost_ack):
            result = board.send(self.runtime, event, self.config)
        self.assertEqual((result['status'], result['errors'][0]['kind']), ('QUEUED_NOT_RECORDED', 'unconfirmed'))
        self.assertIn('unconfirmed', result['warning']); self.assertNotIn('rejected', result['warning'])
        self.assertIn('same event_id', result['warning'])
        self.assertEqual(self.store.view('t')['state']['phase'], 'committed')  # It was in fact recorded.
        self.assertEqual(board.flush(self.runtime, self.config, 't')['recorded'], 1)
        self.assertEqual(sum(e['body']['event_id'] == event['event_id'] for e in self.store.view('t')['events']), 1)

    def test_confirmed_conflict_still_says_rejected(self):
        self.task(); self.add_run('r'); self.add_run('p', claim=False); self.online()
        result = board.send(self.runtime, board.envelope('claim', 't', {'expected_version': 99}, 'p', 2), self.config)
        self.assertEqual(result['status'], 'CONFLICT_NOT_RECORDED')
        self.assertIn('explicitly rejected', result['warning'])

    # A malformed backlog file is counted, never breaks the page, and a repair is seen.
    def test_malformed_outbox_file_is_counted_not_fatal(self):
        self.task(); self.online()
        bad = self.runtime / 'outbox' / 'run-x' / 'bad.json'
        bad.parent.mkdir(parents=True); bad.write_text('{}', encoding='utf-8')
        outbox = board.request(self.runtime, '/api/state')['outbox']
        self.assertEqual((outbox['pending'], outbox['unreadable']), (0, 1))
        bad.write_text('not json', encoding='utf-8')
        self.assertEqual(board.request(self.runtime, '/api/state')['outbox']['unreadable'], 1)
        atomic_json(bad, {'queue_version': 1, 'order': 1, 'event': board.envelope('progress', 't', {'phase': 'q'}, 'ghost', 1)})
        outbox = board.request(self.runtime, '/api/state')['outbox']
        self.assertEqual((outbox['pending'], outbox['unreadable']), (1, 0))
        self.assertTrue(bad.exists())

    # A2: a blocked queue head is named in every later result.
    def test_blocked_head_is_reported_plainly(self):
        self.task(); self.add_run('r'); self.online()
        board.enqueue(self.runtime, board.envelope('progress', 't', {'phase': 'orphan'}, 'ghost', 1), self.config)
        result = board.send(self.runtime, board.envelope('progress', 't', {'phase': 'later'}, 'r', 3), self.config)
        self.assertEqual(result['status'], 'QUEUED_NOT_RECORDED')
        self.assertFalse(result['board_updated'])
        self.assertEqual(result['pending_for_task'], 2)
        self.assertIn('blocks all later reports', result['warning'])
        self.assertEqual(result['errors'][0]['op'], 'progress')
        self.assertEqual(result['errors'][0]['run_id'], 'ghost')

    # A3: a missing task is a 404 with a readable message, not a generic 400.
    def test_missing_task_is_not_found(self):
        with self.assertRaises(NotFound):
            self.store.view('absent')
        self.online()
        with self.assertRaisesRegex(board.RemoteNotFound, 'HTTP 404: task not found'):
            board.request(self.runtime, '/api/task?id=absent')
        with self.assertRaisesRegex(ValueError, 'HTTP 400: invalid stable identifier'):
            board.request(self.runtime, '/api/task?id=..%2Fx')

    # A4: undelivered events are visible on every overview read.
    def test_state_reports_outbox_backlog(self):
        self.task(); self.add_run('r'); self.online()
        for phase in ('one', 'two'):
            board.enqueue(self.runtime, board.envelope('progress', 't', {'phase': phase}, 'ghost', 1 if phase == 'one' else 2), self.config)
        state = board.request(self.runtime, '/api/state')
        self.assertEqual(state['outbox']['pending'], 2)
        self.assertEqual(state['outbox']['by_task']['t']['count'], 2)
        self.assertEqual(state['outbox']['by_task']['t']['head_run_id'], 'ghost')
        self.assertEqual(state['outbox']['task_board_pending'], 0)

    # A6: a document older than the owner's newest report is history, not a conflict.
    def test_older_snapshot_is_not_a_conflict(self):
        self.task(); self.add_run('r')
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        source = self.root / 'state.md'; source.write_text('x', encoding='utf-8')
        snap = board.envelope('snapshot', 't', {'fields': {'phase': 'documented'}, 'excerpt': 'x'},
                              source={'kind': 'document_snapshot', 'path': str(source), 'source_at': old,
                                      'read_at': old, 'sha256': '0' * 64, 'label': 'State'})
        self.store.submit(snap)
        self.submit('progress', 't', 'r', 3, {'phase': 'reported'})
        task = self.store.view()['tasks'][0]
        self.assertEqual(task['conflicts'], [])
        self.assertEqual(task['superseded_snapshot_count'], 1)
        newer = dict(snap, event_id='newer-snapshot', source=dict(snap['source'], source_at=board.now(), read_at=board.now()))
        self.store.submit(newer)
        self.assertEqual([c['field'] for c in self.store.view()['tasks'][0]['conflicts']], ['phase'])

    # A7: unfinished non-owner execution runs are shown, never merged into task state.
    def test_parallel_runs_are_visible_and_bounded(self):
        self.task(); self.add_run('owner')
        for i in range(10):
            self.add_run(f'p{i}', claim=False)
            self.submit('progress', 't', f'p{i}', 2, {'status': 'running', 'phase': f'p{i}'})
        self.submit('progress', 't', 'p0', 3, {'human_input': 'required', 'decision': 'pick'})
        task = self.store.view()['tasks'][0]
        self.assertEqual(task['parallel_run_count'], 10)
        self.assertEqual(len(task['parallel_runs']), 8)
        self.assertIn('p0', [r['id'] for r in task['parallel_runs']])  # Requests survive the cap.
        self.assertTrue(task['human_input_required'])
        self.assertEqual(task['current_run'], 'owner')
        self.assertNotIn('phase', task['state'])
        self.assertGreaterEqual(task['latest_activity_at'], task['parallel_runs'][0]['business_at'])
        self.submit('progress', 't', 'p0', 4, {'status': 'ended', 'human_input': 'not_required'})
        task = self.store.view()['tasks'][0]
        self.assertEqual(task['parallel_run_count'], 9)
        self.assertFalse(task['human_input_required'])

    # Handoff is not an answer and ending is not a withdrawal.
    def test_replaced_owner_request_counts_until_withdrawn(self):
        self.task(); self.add_run('old')
        self.submit('progress', 't', 'old', 3, {'status': 'waiting_input', 'human_input': 'required', 'decision': 'A or B?'})
        self.add_run('new')
        task = self.store.view()['tasks'][0]
        old = next(r for r in task['parallel_runs'] if r['id'] == 'old')
        self.assertTrue(old['former_owner']); self.assertTrue(old['human_input_required'])
        self.assertTrue(task['human_input_required'])
        self.assertEqual(task['current_run'], 'new')
        self.assertNotEqual(task['state'].get('human_input'), 'required')  # Never merged into the owner's state.
        self.submit('progress', 't', 'old', 4, {'human_input': 'not_required'})
        self.assertFalse(self.store.view()['tasks'][0]['human_input_required'])

    def test_ended_parallel_request_counts_until_withdrawn(self):
        self.task(); self.add_run('owner'); self.add_run('peer', claim=False)
        self.submit('progress', 't', 'peer', 2, {'status': 'waiting_input', 'human_input': 'required', 'decision': 'pick'})
        self.submit('progress', 't', 'peer', 3, {'status': 'ended'})
        task = self.store.view()['tasks'][0]
        peer = next(r for r in task['parallel_runs'] if r['id'] == 'peer')
        self.assertEqual((peer['ended'], peer['former_owner'], peer['human_input_required']), (True, False, True))
        self.assertTrue(task['human_input_required'])
        self.submit('progress', 't', 'peer', 4, {'human_input': 'not_required'})
        task = self.store.view()['tasks'][0]
        self.assertFalse(task['human_input_required'])
        self.assertEqual((task['parallel_run_count'], task['parallel_runs']), (0, []))

    # Fresh work by a run that never owned the task keeps it on the default list.
    def test_fresh_parallel_run_keeps_task_visible(self):
        nine = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat().replace('+00:00', 'Z')
        self.task(); self.add_run('owner')
        self.submit('progress', 't', 'owner', 3, {'status': 'running', 'phase': 'owner'}, occurred=nine)
        self.add_run('peer', claim=False)
        self.submit('progress', 't', 'peer', 2, {'status': 'running', 'phase': 'peer'})
        task = self.store.view()['tasks'][0]
        self.assertFalse(task['parallel_runs'][0]['stale'])
        self.assertFalse(task['display_expired'])
        self.assertEqual(task['display_updated_at'], task['latest_activity_at'])
        self.assertEqual([b['name'] for b in self.store.active_workstreams()], ['Test'])
        self.assertEqual((task['current_run'], task['state']['phase']), ('owner', 'owner'))

    def test_overview_omits_deliveries(self):
        self.task(); self.add_run('r')
        self.assertNotIn('deliveries', self.store.view()['tasks'][0])
        self.assertEqual(self.store.view('t')['deliveries'], [])


    # Misfiled boards: an explicit recorded correction, plus a configured board list.
    def test_retag_moves_task_and_keeps_history(self):
        self.task(); self.add_run('r')
        result = self.submit('task_meta', data={'workstream': 'Moved', 'reason': 'misfiled'})
        self.assertTrue(result['applied'])
        task = self.store.view('t')
        self.assertEqual((task['workstream'], task['current_run'], task['version']), ('Moved', 'r', 1))
        self.assertFalse(self.submit('task_meta', data={'workstream': 'Moved', 'reason': 'again'})['applied'])
        with self.assertRaises(ValueError):
            self.submit('task_meta', data={'workstream': 'NoReason'})
        self.assertIn('task_meta', [e['op'] for e in task['events']])

    def test_board_names_follow_boards_in_use(self):
        """No configured list: variants reuse a shown board, new boards are accepted with a hint."""
        self.submit('task', 'c', data={'title': 'C', 'project': 'Synthetic', 'workstream': 'Native search rollout'})
        self.submit('task', 'old', data={'title': 'Old', 'project': 'Synthetic', 'workstream': 'Old board'})
        self.add_run('r-old', 'old')
        long_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace('+00:00', 'Z')
        self.submit('progress', 'old', 'r-old', 3, {'status': 'running'}, occurred=long_ago)
        self.task(); self.online()
        self.assertEqual({b['name'] for b in self.store.active_workstreams()}, {'Native search rollout', 'Test'})
        code, out = self.call('task', 'u', '--title', 'U', '--workstream', 'NativeSearch  ROLLOUT')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.store.view('u')['workstream'], 'Native search rollout')
        self.assertIn('filed under the existing spelling', out['workstream'])
        code, out = self.call('task', 'v', '--title', 'V', '--workstream', 'Native search rollout follow-up')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.store.view('v')['workstream'], 'Native search rollout follow-up')
        self.assertIn('New workstream', out['workstream']); self.assertIn('similar existing workstreams: Native search rollout', out['workstream'])
        # Non-Latin names share a hint through a common two-character pair.
        self.assertTrue(related_names('\u539f\u751f\u5207\u6362', '\u539f\u751f\u8fc1\u79fb'))
        code, out = self.call('task', 'w', '--title', 'W', '--workstream', 'Test')
        self.assertNotIn('workstream', out)
        # A board whose tasks all aged out is not offered, and nothing is refused.
        code, out = self.call('task', 'x', '--title', 'X', '--workstream', 'Old board')
        self.assertEqual((code, self.store.view('x')['workstream']), (0, 'Old board'))
        self.assertIn('New workstream', out['workstream'])

    def test_cli_retag_uses_board_in_use(self):
        self.task(); self.submit('task', 'c', data={'title': 'C', 'project': 'Synthetic', 'workstream': 'Native search rollout'})
        self.online()
        code, out = self.call('retag', 't', '--workstream', 'NativeSearch  ROLLOUT', '--reason', 'misfiled')
        self.assertEqual((code, out['status']), (0, 'RECORDED'), out)
        self.assertEqual(self.store.view('t')['workstream'], 'Native search rollout')
        code, out = self.call('retag', 'absent', '--workstream', 'Test', '--reason', 'x')
        self.assertIn('TASK_NOT_REGISTERED', out['error'])

    def test_cli_task_offline_keeps_name_and_says_unverified(self):
        code, out = self.call('task', 'z', '--title', 'Z', '--workstream', 'Anything')
        self.assertEqual(code, 2, out)
        self.assertIn('Could not check existing workstreams', out['workstream'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
