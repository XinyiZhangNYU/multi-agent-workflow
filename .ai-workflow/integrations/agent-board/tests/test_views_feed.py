"""Brief / agents / feed views: the read-only feed endpoint and the views script.

Isolated synthetic stores only; never an installed/live runtime.
"""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board as plans
from model import Store
from server import BoardServer


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-feed-')
        root = Path(self.temp.name)
        self.store = Store(root / 'runtime', {'project_root': str(root), 'project': 'Synthetic', 'stale_seconds': 3600})
        plans.initialize(self.store)
        self.submit('task', data={'title': '<b>t</b>', 'project': 'Synthetic', 'workstream': 'Test'})
        self.submit('run', 'r1', 1, {'role': 'execution', 'agent': 'agent-b', 'client': 'test'})
        self.submit('claim', 'r1', 2, {'expected_version': self.store.view('t')['version']})
        self.submit('progress', 'r1', 3, {'status': 'running', 'phase': 'first line\nsecond line'})
        self.qid = self.submit('ask', 'r1', 4, {'question': 'A or B?\nmore', 'options': ['A', 'B']})
        self.store.submit({'event_id': 'user-close-feed', 'task_id': 't', 'op': 'user_close',
                           'occurred_at': board.envelope('task', 't', {})['occurred_at'],
                           'source': {'kind': 'user_web', 'label': 'Board page'}, 'data': {'question_id': self.qid}}, user=True)
        self.submit('progress', 'r1', 5, {'status': 'ended', 'phase': 'x' * 300})
        self.server = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(timeout=5)
        self.temp.cleanup()

    def submit(self, op, run=None, seq=None, data=None):
        event = board.envelope(op, 't', data or {}, run, seq)
        self.store.submit(event)
        return event['event_id']

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn.request('GET', path, headers=dict({'Host': '127.0.0.1:' + str(self.server.server_port)}, **(headers or {})))
        response = conn.getresponse(); data = response.read(); conn.close()
        return response.status, response.getheader('Content-Type'), data

    def test_feed_lists_reports_newest_first_without_registrations(self):
        status, _, data = self.get('/api/feed')
        self.assertEqual(status, 200)
        events = json.loads(data)['events']
        self.assertEqual([e['op'] for e in events], ['progress', 'user_close', 'ask', 'progress', 'claim'])
        ended, closed, ask, running = events[0], events[1], events[2], events[3]
        self.assertEqual((ended['status'], len(ended['text'])), ('ended', 241))
        self.assertEqual((closed['agent'], closed['run_id'], closed['question_id']), (None, None, self.qid))
        self.assertEqual((ask['text'], ask['question_id'], ask['agent']), ('A or B?', self.qid, 'agent-b'))
        self.assertEqual((running['text'], running['title'], running['workstream']), ('first line', '<b>t</b>', 'Test'))

    def test_feed_limit_and_query_are_validated(self):
        self.assertEqual(len(json.loads(self.get('/api/feed?limit=2')[2])['events']), 2)
        for bad in ('limit=0', 'limit=501', 'limit=x', 'limit=1&limit=2', 'task=t'):
            with self.subTest(bad=bad):
                self.assertEqual(self.get('/api/feed?' + bad)[0], 400)

    def test_feed_checks_instance_and_host(self):
        self.assertEqual(self.get('/api/feed', {'X-Agent-Board-Instance': 'other'})[0], 409)
        self.assertEqual(self.get('/api/feed', {'X-Agent-Board-Instance': self.server.instance})[0], 200)
        self.assertEqual(self.get('/api/feed', {'Host': 'evil.test'})[0], 403)

    def test_views_script_is_served(self):
        status, mime, data = self.get('/views.js')
        self.assertEqual((status, mime), (200, 'text/javascript; charset=utf-8'))
        self.assertIn(b'renderViews', data)
        self.assertIn(b'/views.js', self.get('/')[2])


if __name__ == '__main__':
    unittest.main()
