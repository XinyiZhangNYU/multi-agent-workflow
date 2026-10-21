"""Explicit human requests, not inferred from task status or free text."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Protocol, Store, packed


class HumanInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-human-input-')
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Test', 'stale_seconds': 60}
        self.store = Store(self.runtime, self.config)
        self.store.submit(board.envelope('task', 'task', {'title': 'Test', 'workstream': 'Test', 'project': 'Test'}))
        self.sequences = {}

    def tearDown(self):
        self.temp.cleanup()

    def add_run(self, name='owner', role='execution', claim=True):
        self.store.submit(board.envelope('run', 'task', {'role': role, 'agent': name}, name, 1))
        self.sequences[name] = 1
        if role == 'execution' and claim:
            version = self.store.view('task')['version']
            self.sequences[name] += 1
            self.store.submit(board.envelope('claim', 'task', {'expected_version': version}, name, self.sequences[name]))
        return name

    def progress(self, name='owner', data=None, seq=None, occurred=None):
        if seq is None:
            self.sequences[name] += 1
            seq = self.sequences[name]
        event = board.envelope('progress', 'task', data or {}, name, seq)
        if occurred:
            event['occurred_at'] = occurred
        return self.store.submit(event)

    def view(self):
        return self.store.view('task')

    def test_declaration_is_independent_of_every_running_state(self):
        self.add_run()
        for status in ('running', 'waiting_input', 'waiting_dependency', 'paused', 'ended', 'unknown'):
            for declaration in ('required', 'not_required', 'unknown'):
                with self.subTest(status=status, declaration=declaration):
                    self.progress(data={'status': status, 'human_input': declaration})
                    self.assertEqual(self.view()['human_input_required'], declaration == 'required')

    def test_free_text_and_waiting_status_do_not_infer_a_request(self):
        self.add_run()
        self.progress(data={'status': 'waiting_input', 'decision': 'Please approve this'})
        task = self.view()
        self.assertTrue(task['needs_attention'])
        self.assertFalse(task['human_input_required'])
        self.assertNotIn('human_input', task['state'])
        self.progress(data={'status': 'waiting_dependency', 'next_step': 'No further user authorization needed'})
        self.assertFalse(self.view()['human_input_required'])

    def test_invalid_values_rejected_before_offline_filesystem_write(self):
        for value in (None, True, False, 1, [], {}, 'yes', 'REQUIRED', ''):
            event = board.envelope('progress', 'task', {'human_input': value}, 'owner', 3)
            isolated = self.root / 'must-not-exist'
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    board.enqueue(isolated, event, self.config)
                self.assertFalse(isolated.exists())

    def test_events_without_declaration_keep_their_digest(self):
        event = board.envelope('progress', 'task', {'status': 'running'}, 'owner', 3)
        old = Protocol(self.config).validate(event)
        old_digest = hashlib.sha256(packed(old).encode()).hexdigest()
        self.add_run()
        self.assertEqual(self.store.submit(event)['digest'], old_digest)
        self.assertTrue(self.store.submit(event)['duplicate'])
        self.assertNotIn('human_input', self.view()['events'][0]['body']['data'])

    def test_incremental_status_and_ended_do_not_resolve_explicit_request(self):
        self.add_run()
        self.progress(data={'human_input': 'required', 'decision': 'Choose A or B', 'status': 'waiting_input'})
        self.progress(data={'status': 'ended'})
        self.assertTrue(self.view()['human_input_required'])
        self.progress(data={'human_input': 'not_required', 'decision': None})
        self.assertFalse(self.view()['human_input_required'])
        self.assertIsNone(self.view()['state']['decision'])
        self.assertEqual(self.view()['events'][2]['body']['data']['human_input'], 'required')

    def test_late_declaration_cannot_clear_newer_request(self):
        self.add_run()
        self.progress(data={'human_input': 'required'}, seq=5)
        self.assertFalse(self.progress(data={'human_input': 'not_required'}, seq=4)['applied'])
        self.assertTrue(self.view()['human_input_required'])

    def test_handoff_old_owner_request_stays_open_without_changing_owner_state(self):
        self.add_run()
        self.progress(data={'human_input': 'required'})
        self.add_run('new-owner')
        self.progress(name='new-owner', data={'status': 'running', 'human_input': 'not_required'})
        self.progress(data={'human_input': 'required'})
        task = self.view()
        self.assertTrue(task['human_input_required'])  # Handoff is not an answer.
        self.assertEqual(task['state']['human_input'], 'not_required')
        self.assertEqual(task['current_run'], 'new-owner')
        self.assertEqual(len(task['runs']), 2)
        self.progress(data={'human_input': 'not_required'})
        self.assertFalse(self.view()['human_input_required'])

    def test_multiple_audits_do_not_replace_owner_or_make_acceptance(self):
        self.add_run()
        self.progress(data={'status': 'ended', 'candidate_sha': 'a' * 40, 'human_input': 'not_required'})
        for name in ('audit-a', 'audit-b'):
            self.add_run(name, 'audit')
            self.progress(name, {'status': 'waiting_input', 'human_input': 'required', 'candidate_sha': 'b' * 40})
        task = self.view()
        self.assertTrue(task['human_input_required'])
        self.assertEqual(sum(r['human_input_required'] for r in task['overview_runs']), 2)
        self.assertEqual(task['current_run'], 'owner')
        self.assertEqual(task['state']['status'], 'ended')
        self.assertEqual(task['deliveries'], [])

    def test_finished_audit_request_survives_until_explicit_clear(self):
        self.add_run('audit', 'audit')
        self.progress('audit', {'status': 'ended', 'human_input': 'required'})
        task = self.view()
        self.assertTrue(task['human_input_required'])
        self.assertEqual(task['overview_runs'][0]['state']['status'], 'ended')
        self.assertIsNone(task['current_run'])
        self.assertEqual(task['state'], {'status': 'unknown'})
        self.progress('audit', {'human_input': 'not_required', 'decision': None})
        self.assertFalse(self.view()['human_input_required'])
        self.assertEqual(self.view()['overview_runs'], [])
        self.assertEqual(len(self.view()['runs']), 1)

    def test_stale_request_not_discarded_as_nonhuman_quality_warning(self):
        self.add_run()
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.progress(data={'status': 'waiting_dependency', 'human_input': 'required'}, occurred=old)
        task = self.view()
        self.assertTrue(task['stale'])
        self.assertTrue(task['human_input_required'])
        self.assertIsNone(task['activity'])

    def test_snapshot_cannot_forge_current_human_request(self):
        self.store.submit(board.envelope('snapshot', 'task', {'fields': {'human_input': 'required', 'decision': 'Old approval'}, 'excerpt': 'Old snapshot'},
            source={'kind': 'document_snapshot', 'label': 'old', 'path': 'old.md', 'source_at': board.now(), 'read_at': board.now(), 'sha256': 'a' * 64}))
        task = self.view()
        self.assertFalse(task['human_input_required'])
        self.assertEqual(task['snapshots'][0]['data']['fields']['human_input'], 'required')

    def test_not_required_keeps_contradictory_text_for_inspection(self):
        self.add_run()
        self.progress(data={'status': 'waiting_dependency', 'human_input': 'not_required', 'decision': 'Old decision still present'})
        task = self.view()
        self.assertFalse(task['human_input_required'])
        self.assertEqual(task['state']['decision'], 'Old decision still present')
        self.assertTrue(task['needs_attention'])

    def test_reopen_preserves_event_bytes_and_state_without_migration(self):
        self.add_run()
        self.progress(data={'human_input': 'required', 'decision': 'Request'})
        before = self.view()
        reopened = Store(self.runtime, self.config).view('task')
        self.assertTrue(reopened['human_input_required'])
        self.assertEqual(reopened['events'], before['events'])
        self.assertEqual(reopened['state'], before['state'])

    def test_actual_cli_accepts_new_flag_and_queues_without_live_service(self):
        (self.root / '.ai-workflow').mkdir()
        (self.root / '.ai-workflow/WORKFLOW.md').write_text('Synthetic', encoding='utf-8')
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config), encoding='utf-8')
        runtime = self.root / 'offline-cli'
        board.remember_registration(runtime, 'task', 'owner')  # Confirmed earlier on this machine.
        result = subprocess.run([sys.executable, '-B', str(Path(board.__file__)), '--config', str(config), '--runtime', str(runtime),
            'report', 'task', 'owner', '--human-input', 'required', '--decision', 'Choose A or B'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'QUEUED_NOT_RECORDED')
        queued = list((runtime / 'outbox').glob('*/*.json'))
        self.assertEqual(len(queued), 1)
        self.assertEqual(json.loads(queued[0].read_text(encoding='utf-8'))['event']['data']['human_input'], 'required')


if __name__ == '__main__':
    unittest.main()
