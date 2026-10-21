"""User closes a request from the local page: the page's only write.

Isolated synthetic stores only; never an installed/live runtime.
"""
import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board as plans
from model import Conflict, Store
from server import BoardServer


class UserCloseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-user-close-')
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Synthetic', 'stale_seconds': 3600}
        self.store = Store(self.runtime, self.config); plans.initialize(self.store)
        self.submit('task', 't', data={'title': 't', 'project': 'Synthetic', 'workstream': 'Test'})
        self.submit('run', 't', 'r1', 1, {'role': 'execution', 'agent': 'Test', 'client': 'test'})
        self.submit('claim', 't', 'r1', 2, {'expected_version': self.store.view('t')['version']})
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.worker.join(timeout=5)
        self.temp.cleanup()

    def submit(self, op, task='t', run=None, seq=None, data=None):
        event = board.envelope(op, task, data or {}, run, seq)
        self.store.submit(event)
        return event['event_id']

    def questions(self):
        return self.store.view('t')['questions']

    def open_list(self):
        task = next(t for t in self.store.view()['tasks'] if t['id'] == 't')
        return task['open_questions']

    def user_event(self, data, task='t'):
        return {'event_id': 'user-close-test-' + str(len(data)), 'task_id': task, 'op': 'user_close',
                'occurred_at': board.envelope('task', task, {})['occurred_at'],
                'source': {'kind': 'user_web', 'label': 'Board page'}, 'data': data}

    def online(self):
        self.server = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True); self.worker.start()
        self.host = '127.0.0.1:' + str(self.server.server_port)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        payload = json.dumps(body).encode() if body is not None else None
        base = {'Host': self.host}
        if payload is not None:
            base['Content-Type'] = 'application/json'
        conn.request(method, path, body=payload, headers=dict(base, **(headers or {})))
        response = conn.getresponse(); data = response.read(); conn.close()
        return response.status, data

    def page_headers(self):
        status, html = self.request('GET', '/')
        self.assertEqual(status, 200)
        token = re.search(rb'name="board-page-token" content="([^"]+)"', html).group(1).decode()
        return {'Origin': 'http://' + self.host, 'X-Board-Page-Token': token, 'X-Agent-Board-Instance': self.server.instance}

    def test_closes_an_ask_without_any_run(self):
        qid = self.submit('ask', 't', 'r1', 3, {'question': 'A or B?\nmore'})
        self.assertEqual([q['question_id'] for q in self.open_list()], [qid])
        self.store.submit(self.user_event({'question_id': qid}), user=True)
        self.assertEqual(self.open_list(), [])
        closed = self.questions()['closed'][0]['closed_by']
        self.assertEqual((closed['op'], closed['run_id']), ('user_close', None))

    def test_closes_a_declaration_and_a_new_one_reopens(self):
        self.submit('progress', 't', 'r1', 3, {'human_input': 'required'})
        self.assertEqual(len(self.open_list()), 1)
        self.store.submit(self.user_event({'target_run': 'r1'}), user=True)
        self.assertEqual(self.open_list(), [])
        self.submit('progress', 't', 'r1', 4, {'human_input': 'required', 'decision': 'again'})
        self.assertEqual([q['question'] for q in self.open_list()], ['again'])

    def test_agents_cannot_submit_user_closures(self):
        qid = self.submit('ask', 't', 'r1', 3, {'question': 'q'})
        with self.assertRaises(ValueError):
            self.store.submit(self.user_event({'question_id': qid}))
        forged = board.envelope('progress', 't', {'status': 'running'}, 'r1', 4)
        forged['source'] = {'kind': 'user_web', 'label': 'x'}
        for user in (False, True):
            with self.subTest(user=user), self.assertRaises(ValueError):
                self.store.submit(forged, user=user)
        with self.assertRaises(ValueError):
            self.store.submit(board.envelope('progress', 't', {'status': 'running'}, 'r1', 4), user=True)
        self.assertEqual(len(self.open_list()), 1)

    def test_target_must_exist_in_this_task(self):
        for data in ({'question_id': 'nope'}, {'target_run': 'nope'}):
            with self.subTest(data=data), self.assertRaises(Conflict):
                self.store.submit(self.user_event(data), user=True)
        for data in ({}, {'question_id': 'a', 'target_run': 'b'}, {'question_id': 'a', 'x': 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.submit(self.user_event(data), user=True)

    def test_close_does_not_extend_display_time(self):
        qid = self.submit('ask', 't', 'r1', 3, {'question': 'q'})
        before = self.store.view('t')['display_updated_at']
        self.store.submit(self.user_event({'question_id': qid}), user=True)
        self.assertEqual(self.store.view('t')['display_updated_at'], before)

    def test_http_endpoint_requires_page_origin_and_token(self):
        qid = self.submit('ask', 't', 'r1', 3, {'question': 'q'})
        self.online()
        good = self.page_headers()
        status, html = self.request('GET', '/')
        self.assertNotIn(b'__PAGE_TOKEN__', html)
        body = {'task_id': 't', 'question_id': qid}
        for drop in ('Origin', 'X-Board-Page-Token', 'X-Agent-Board-Instance'):
            with self.subTest(missing=drop):
                headers = {k: v for k, v in good.items() if k != drop}
                self.assertEqual(self.request('POST', '/api/user-close', body, headers)[0], 403)
        for key, value in (('Origin', 'http://evil.test'), ('X-Board-Page-Token', 'x' * 32), ('X-Agent-Board-Instance', '0' * 32)):
            with self.subTest(wrong=key):
                self.assertEqual(self.request('POST', '/api/user-close', body, dict(good, **{key: value}))[0], 403)
        self.assertEqual(self.request('POST', '/api/user-close', body, dict(good, Host='evil.test'))[0], 403)
        self.assertEqual(len(self.open_list()), 1)
        # The agent write path refuses a user closure even with the bearer token.
        forged = self.user_event({'question_id': qid})
        status, _ = self.request('POST', '/api/events', forged, {'Authorization': 'Bearer ' + self.server.token,
                                                                 'X-Agent-Board-Instance': self.server.instance})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.open_list()), 1)
        status, data = self.request('POST', '/api/user-close', {'task_id': 't', 'question_id': 'nope'}, good)
        self.assertEqual(status, 409)
        status, data = self.request('POST', '/api/user-close', body, good)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.open_list(), [])
        status, data = self.request('POST', '/api/user-close', body, good)
        self.assertEqual(status, 200)  # a second close is kept as a duplicate, not an error
        self.assertEqual(len(self.questions()['closed'][0]['duplicates']), 1)

    def test_page_token_rotates_with_each_start(self):
        a = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        b = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        try:
            self.assertNotEqual(a.page_token, b.page_token)
        finally:
            a.server_close(); b.server_close()


if __name__ == '__main__':
    unittest.main()
