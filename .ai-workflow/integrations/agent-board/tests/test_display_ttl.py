"""Presentation TTL: no deletion, new producer fields, or authority changes."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Store, display_visibility, stamp

TTL = 604800


class DisplayTTLTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-ttl-')
        self.root = Path(self.temp.name)
        self.config = {'project_root': str(self.root), 'stale_seconds': 86400}
        self.store = Store(self.root / 'runtime', self.config)
        self.store.submit(board.envelope('task', 'task', {'title': 'TTL', 'project': 'Test', 'workstream': 'Test'}))
        self.seqs = {}

    def tearDown(self):
        self.temp.cleanup()

    def at(self, days=0):
        return stamp((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())

    def add_run(self, name='owner', role='execution', claim=True):
        self.seqs[name] = 1
        self.store.submit(board.envelope('run', 'task', {'role': role, 'agent': name}, name, 1))
        if role == 'execution' and claim:
            self.seqs[name] += 1
            self.store.submit(board.envelope('claim', 'task', {'expected_version': self.view()['version']}, name, self.seqs[name]))

    def progress(self, days=0, data=None, name='owner', seq=None):
        if seq is None:
            self.seqs[name] += 1
            seq = self.seqs[name]
        event = board.envelope('progress', 'task', data or {'status': 'running', 'human_input': 'not_required'}, name, seq)
        event['occurred_at'] = self.at(days)
        return self.store.submit(event)

    def snapshot(self, days=8, unknown=False):
        self.store.submit(board.envelope('snapshot', 'task', {'fields': {'status': 'running'}, 'excerpt': 'snapshot'},
            source={'kind': 'document_snapshot', 'label': 'document', 'path': 'report.md', 'sha256': 'a' * 64,
                    'source_at': None if unknown else self.at(days), 'read_at': self.at()}))

    def activity(self, days=0):
        self.store.submit(board.envelope('activity', 'task', {'summary': 'Actual task log activity'},
            source={'kind': 'program_observation', 'label': 'log', 'path': 'activity.log',
                    'source_at': self.at(days), 'read_at': self.at()}))

    def view(self):
        return self.store.view('task')

    def test_seven_days_default_and_read_only_api_keeps_all_tasks(self):
        self.add_run(); self.progress(8)
        view = self.store.view()
        self.assertEqual(view['display_ttl_seconds'], TTL)
        self.assertEqual(len(view['tasks']), 1)
        self.assertTrue(view['tasks'][0]['display_expired'])
        self.assertEqual(self.view()['state']['status'], 'running')
        self.assertEqual(len(self.view()['runs']), 1)

    def test_stale_and_display_expiration_are_separate(self):
        self.add_run(); self.progress(2)
        self.assertTrue(self.view()['stale'])
        self.assertFalse(self.view()['display_expired'])

    def test_ended_not_completion_and_uses_same_window(self):
        self.add_run(); self.progress(8, {'status': 'ended'})
        self.assertFalse(self.view()['stale'])
        self.assertTrue(self.view()['display_expired'])
        self.assertEqual(self.view()['deliveries'], [])

    def test_explicit_human_request_kept_even_ended(self):
        self.add_run(); self.progress(20, {'status': 'ended', 'human_input': 'required'})
        self.assertTrue(self.view()['human_input_required'])
        self.assertFalse(self.view()['display_expired'])
        self.progress(19, {'human_input': 'not_required'})
        self.assertTrue(self.view()['display_expired'])

    def test_unresolved_ended_audit_request_kept(self):
        self.add_run(); self.progress(20)
        self.add_run('review', 'audit'); self.progress(20, {'status': 'ended', 'human_input': 'required'}, 'review')
        self.assertFalse(self.view()['display_expired'])
        self.progress(19, {'human_input': 'not_required'}, 'review')
        self.assertTrue(self.view()['display_expired'])

    def test_status_or_natural_language_does_not_forge_human_request(self):
        self.add_run(); self.progress(8, {'status': 'waiting_input', 'decision': 'Please approve'})
        self.assertFalse(self.view()['human_input_required'])
        self.assertTrue(self.view()['display_expired'])

    def test_valid_progress_reappears(self):
        self.add_run(); self.progress(8)
        self.assertTrue(self.view()['display_expired'])
        self.progress(0)
        self.assertFalse(self.view()['display_expired'])

    def test_low_sequence_recent_event_does_not_renew(self):
        self.add_run(); self.progress(8, seq=5)
        before = self.view()['display_updated_at']
        result = self.progress(0, seq=4)
        self.assertEqual(result['reason'], 'late_sequence')
        self.assertEqual(self.view()['display_updated_at'], before)
        self.assertTrue(self.view()['display_expired'])

    def test_high_sequence_older_business_time_does_not_move_clock_back(self):
        self.add_run(); self.progress(0); before = self.view()['display_updated_at']
        self.progress(10)
        self.assertEqual(self.view()['display_updated_at'], before)
        self.assertFalse(self.view()['display_expired'])

    def test_old_execution_run_cannot_renew(self):
        self.add_run(); self.progress(10)
        self.add_run('new'); self.progress(8, name='new')
        before = self.view()['display_updated_at']
        self.progress(0)
        self.assertEqual(self.view()['display_updated_at'], before)
        self.assertTrue(self.view()['display_expired'])
        self.assertEqual(self.view()['current_run'], 'new')

    def test_never_owner_execution_run_renews_without_changing_state(self):
        self.add_run(); self.progress(10, {'status': 'running', 'phase': 'owner'})
        self.add_run('peer', claim=False); self.progress(0, {'status': 'running', 'phase': 'peer'}, 'peer')
        view = self.view()
        self.assertFalse(view['display_expired'])
        self.assertEqual(view['display_updated_at'], next(r for r in view['runs'] if r['id'] == 'peer')['business_at'])
        self.assertEqual((view['current_run'], view['state']['phase']), ('owner', 'owner'))

    def test_current_unknown_clock_kept_after_handoff(self):
        self.add_run(); self.progress(10)
        self.add_run('new')
        self.assertTrue(self.view()['display_time_unknown'])
        self.assertFalse(self.view()['display_expired'])

    def test_fresh_audit_including_ended_renews_without_changing_candidate(self):
        self.add_run(); self.progress(10, {'status': 'ended', 'candidate_sha': 'a' * 40})
        self.add_run('review', 'audit')
        self.progress(0, {'status': 'ended', 'candidate_sha': 'b' * 40}, 'review')
        view = self.view()
        self.assertFalse(view['display_expired'])
        self.assertEqual(view['state']['candidate_sha'], 'a' * 40)
        self.assertEqual(view['state']['status'], 'ended')
        self.assertEqual(view['business_at'], self.view()['runs'][0]['business_at'])
        self.assertEqual(view['deliveries'], [])

    def test_unknown_unfinished_audit_kept_but_historical_ended_unknown_not_blocking(self):
        self.add_run(); self.progress(8)
        self.add_run('review', 'audit')
        self.assertTrue(self.view()['display_time_unknown'])
        self.assertFalse(self.view()['display_expired'])
        self.progress(8, {'status': 'ended'}, 'review')
        self.assertFalse(self.view()['display_time_unknown'])
        self.assertTrue(self.view()['display_expired'])

    def test_audit_only_known_and_unknown(self):
        self.add_run('review', 'audit')
        self.assertFalse(self.view()['display_expired'])
        self.progress(8, name='review')
        self.assertTrue(self.view()['display_expired'])
        self.progress(0, name='review')
        self.assertFalse(self.view()['display_expired'])
        self.assertIsNone(self.view()['current_run'])
        self.assertIsNone(self.view()['business_at'])

    def test_document_only_original_time_not_received_or_read_time(self):
        self.snapshot(8)
        before = self.view()['display_updated_at']
        self.assertTrue(self.view()['display_expired'])
        # New receipt and read_at, same original source clock; never extends TTL.
        self.store.submit(board.envelope('snapshot', 'task', {'fields': {}, 'excerpt': 'Reimport'},
            source={'kind': 'document_snapshot', 'label': 'document', 'path': 'report.md', 'sha256': 'b' * 64,
                    'source_at': before, 'read_at': self.at()}))
        self.assertEqual(self.view()['display_updated_at'], before)
        self.assertTrue(self.view()['display_expired'])

    def test_recent_snapshot_does_not_renew_a_known_execution_clock(self):
        self.add_run(); self.progress(8); self.snapshot(0)
        self.assertTrue(self.view()['display_expired'])

    def test_unknown_source_clock_or_no_progress_kept(self):
        self.assertTrue(self.view()['display_time_unknown'])
        self.assertFalse(self.view()['display_expired'])
        self.snapshot(unknown=True)
        self.assertTrue(self.view()['display_time_unknown'])
        self.assertFalse(self.view()['display_expired'])

    def test_actual_observation_uses_source_not_ingestion_clock(self):
        self.add_run(); self.progress(10); self.activity(8)
        self.assertTrue(self.view()['display_expired'])
        self.activity(0)
        self.assertFalse(self.view()['display_expired'])

    def test_new_delivery_does_not_renew_or_inherit_acceptance(self):
        self.add_run(); self.progress(8, {'candidate_sha': 'a' * 40})
        event = board.envelope('delivery', 'task', {'stage': 'self_test', 'verdict': 'PASS',
            'candidate_sha': 'b' * 40, 'evidence': 'report.md', 'audit_id': None}, 'owner', 4)
        self.store.submit(event)
        self.assertTrue(self.view()['display_expired'])
        self.assertFalse(self.view()['deliveries'][0]['applicable'])

    def test_view_does_not_write_or_migrate_events_and_restart_keeps_history(self):
        self.add_run(); self.progress(8)
        with self.store.connect() as db:
            events = [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY ordinal')]
            schema = [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        for _ in range(3):
            self.assertTrue(self.view()['display_expired'])
        reopened = Store(self.root / 'runtime', self.config)
        self.assertTrue(reopened.view('task')['display_expired'])
        with reopened.connect() as db:
            self.assertEqual(events, [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY ordinal')])
            self.assertEqual(schema, [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')])

    def test_invalid_configuration_before_runtime_write_and_valid_override(self):
        for v in (0, -1, True, '604800', None, 1.5):
            runtime = self.root / 'must-not-exist'
            with self.subTest(value=v), self.assertRaises(ValueError):
                Store(runtime, dict(self.config, display_ttl_seconds=v))
            self.assertFalse(runtime.exists())
        self.add_run(); self.progress(2)
        short = Store(self.root / 'runtime', dict(self.config, display_ttl_seconds=86400))
        self.assertTrue(short.view('task')['display_expired'])

    def test_exact_utc_boundary_across_et_dst_and_offset_timestamps(self):
        # One week is 604800 elapsed seconds, not calendar dates in the display zone (DST fall-back).
        checked = datetime.fromisoformat('2026-11-05T16:00:00+00:00')
        r = {'role': 'execution', 'business_at': stamp('2026-10-29T12:00:00-04:00'), 'state': {}}
        view = lambda at: display_visibility(r, [r], [r], None, [], TTL, at)
        self.assertFalse(view(checked - timedelta(milliseconds=1))['display_expired'])
        self.assertTrue(view(checked)['display_expired'])
        self.assertEqual(view(checked)['display_updated_at'], '2026-10-29T16:00:00.000Z')


if __name__ == '__main__':
    unittest.main()
