"""Questions with IDs, run->item links and one next step per task.

Isolated synthetic stores only; never an installed/live runtime.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board as plans
from model import Conflict, Store
from server import BoardServer, atomic_json

OLD = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()


def line(mid):
    return {'id': mid, 'goal': mid + ' goal', 'completion': 'verified', 'owner': 'Lead (text)',
            'next_step': 'plan says ' + mid}


class QuestionAndLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='board-questions-')
        self.root = Path(self.temp.name)
        (self.root / '.ai-workflow').mkdir()
        (self.root / '.ai-workflow/WORKFLOW.md').write_text('synthetic', encoding='utf-8')
        self.runtime = self.root / 'runtime'
        self.config = {'project_root': str(self.root), 'project': 'Synthetic', 'stale_seconds': 3600}
        self.store = Store(self.runtime, self.config); plans.initialize(self.store)
        self.config_path = self.root / 'config.json'
        atomic_json(self.config_path, self.config)
        self.servers = []; self.counter = 0

    def tearDown(self):
        self.offline()
        self.temp.cleanup()

    # -- helpers -------------------------------------------------------------
    def online(self):
        server = BoardServer(('127.0.0.1', 0), self.store, 'synthetic-only-token-0123456789012345')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start(); self.servers.append((server, worker))
        atomic_json(self.runtime / 'instance.json', {'url': f'http://127.0.0.1:{server.server_port}',
                    'pid': os.getpid(), 'instance_id': server.instance})
        (self.runtime / 'write-token').write_text(server.token, encoding='ascii')

    def offline(self):
        for server, worker in reversed(self.servers):
            server.shutdown(); server.server_close(); worker.join(timeout=5)
        self.servers.clear()

    def call(self, *args):
        completed = subprocess.run([sys.executable, '-B', str(Path(board.__file__)), '--config', str(self.config_path),
                                    '--runtime', str(self.runtime), *args],
                                   capture_output=True, text=True, encoding='utf-8', timeout=20)
        return completed.returncode, json.loads(completed.stdout)

    def event(self, op, task='t', run=None, seq=None, data=None, occurred=None):
        event = board.envelope(op, task, data or {}, run, seq)
        if occurred:
            event['occurred_at'] = occurred
        return event

    def submit(self, *args, **kwargs):
        event = self.event(*args, **kwargs)
        self.store.submit(event)
        return event['event_id']

    def task(self, task='t'):
        self.submit('task', task, data={'title': task, 'project': 'Synthetic', 'workstream': 'Test'})

    def add_run(self, run, task='t', claim=True, role='execution'):
        self.submit('run', task, run, 1, {'role': role, 'agent': 'Test', 'client': 'test'})
        if claim:
            self.submit('claim', task, run, 2, {'expected_version': self.store.view(task)['version']})

    def ask(self, run, seq, question='A or B?', task='t', occurred=None, **data):
        return self.submit('ask', task, run, seq, dict(question=question, **data), occurred)

    def write(self, op, data=None, task='t'):
        self.counter += 1
        return plans.apply(self.store, {'event_id': 'plan-' + str(self.counter), 'task_id': task, 'op': op,
                                        'expected_version': plans.read(self.store, task)['version'], 'data': data or {}})

    def plan(self, task='t'):
        self.write('create', {'goal': 'G', 'mode': 'plan_execute', 'user_request': 'user enabled'}, task)
        self.write('draft', {'mainlines': [line('m1'), line('m2')]}, task)
        self.write('freeze', task=task)

    def detail(self, task='t'):
        return plans.attach_overviews(self.store, self.store.view(task))

    def listed(self, task='t'):
        return next(t for t in plans.attach_overviews(self.store, self.store.view())['tasks'] if t['id'] == task)

    def open_ids(self, task='t'):
        return [q['question_id'] for q in self.listed(task)['open_questions']]

    def seq_value(self, run):
        path = self.runtime / 'sequences' / 'v2' / (board.storage_name('run', run) + '.json')
        return json.loads(path.read_text(encoding='utf-8'))['seq'] if path.exists() else 0

    def queued(self):
        return list((self.runtime / 'outbox').glob('*/*.json'))

    # -- protocol ------------------------------------------------------------
    def test_new_operations_are_validated(self):
        self.task(); self.add_run('r'); self.task('u'); self.add_run('s', 'u')
        bad = [('ask', {}), ('ask', {'question': ' '}), ('ask', {'question': 'q', 'options': ['x'] * 9}),
               ('ask', {'question': 'q', 'options': ['x' * 201]}), ('ask', {'question': 'q', 'extra': 1}),
               ('ask', {'question': 'q', 'item': {'branch': 'b1'}}), ('ask', {'question': 'q', 'item': {'mainline': 'm1', 'x': 'y'}}),
               ('resolve', {'answer': 'a'}), ('resolve', {'question_id': 'q1', 'target_run': 'r', 'answer': 'a'}),
               ('resolve', {'question_id': 'q1'}), ('withdraw', {'target_run': 'r'}), ('withdraw', {'question_id': 'q1', 'answer': 'a'}),
               ('progress', {'item': {'mainline': 'bad id'}}), ('progress', {'item': 'm1'})]
        for seq, (op, data) in enumerate(bad, 10):
            with self.subTest(op=op, data=data), self.assertRaises(ValueError):
                self.submit(op, 't', 'r', seq, data)
        with self.assertRaises(Conflict):
            self.ask('s', 3)  # A run registered under another task.
        with self.assertRaises(ValueError):
            self.store.submit(self.event('ask', 't', None, None, {'question': 'q'}))

    # 1. Two questions on one item close independently .
    def test_two_questions_on_one_item_close_independently(self):
        self.task(); self.add_run('r'); self.plan()
        q1 = self.ask('r', 3, 'Q1', item={'mainline': 'm1'})
        q2 = self.ask('r', 4, 'Q2', item={'mainline': 'm1'})
        self.assertEqual(self.open_ids(), [q1, q2])
        self.assertTrue(self.listed()['human_input_required'])
        self.submit('resolve', 't', 'r', 5, {'question_id': q1, 'answer': 'A'})
        self.assertEqual(self.open_ids(), [q2])
        closed = self.detail()['questions']['closed']
        self.assertEqual([(q['question_id'], q['closed_by']['op'], q['closed_by']['text']) for q in closed], [(q1, 'resolve', 'A')])
        self.assertTrue(self.listed()['human_input_required'])
        self.submit('withdraw', 't', 'r', 6, {'question_id': q2, 'reason': 'superseded'})
        self.assertEqual(self.open_ids(), [])
        self.assertFalse(self.listed()['human_input_required'])

    # 2. Arrival order does not matter.
    def test_resolve_before_ask_closes_on_arrival(self):
        self.task(); self.add_run('r'); self.add_run('h', claim=False)
        ask = self.event('ask', 't', 'r', 3, {'question': 'Q'})
        self.submit('resolve', 't', 'h', 2, {'question_id': ask['event_id'], 'answer': 'relayed'})
        self.assertEqual([c['why'] for c in self.detail()['questions']['unmatched']], ['unknown_question'])
        self.store.submit(ask)
        q = self.detail()['questions']
        self.assertEqual((q['open'], q['unmatched']), ([], []))
        self.assertEqual(q['closed'][0]['question_id'], ask['event_id'])

    # 3. Unknown or cross-task IDs are listed as unmatched and close nothing.
    def test_unknown_and_cross_task_question_ids_are_unmatched(self):
        self.task(); self.add_run('r'); self.task('u'); self.add_run('s', 'u')
        other = self.ask('s', 3, task='u'); mine = self.ask('r', 3)
        self.submit('resolve', 't', 'r', 4, {'question_id': other, 'answer': 'wrong task'})
        self.submit('withdraw', 't', 'r', 5, {'question_id': 'no-such-question', 'reason': 'typo'})
        self.assertEqual(self.open_ids(), [mine])
        self.assertEqual(self.open_ids('u'), [other])
        self.assertEqual([c['why'] for c in self.detail()['questions']['unmatched']], ['unknown_question'] * 2)

    # 4. The earliest closure wins; later ones are duplicates.
    def test_duplicate_closure_first_wins(self):
        self.task(); self.add_run('r')
        q = self.ask('r', 3)
        self.submit('resolve', 't', 'r', 4, {'question_id': q, 'answer': 'first'})
        self.submit('withdraw', 't', 'r', 5, {'question_id': q, 'reason': 'second'})
        closed = self.detail()['questions']['closed'][0]
        self.assertEqual((closed['closed_by']['op'], closed['closed_by']['text']), ('resolve', 'first'))
        self.assertEqual([d['op'] for d in closed['duplicates']], ['withdraw'])
        self.assertEqual(self.detail()['questions']['unmatched'], [])

    # 5. Handoff, ending and display TTL never hide an open question.
    def test_question_survives_handoff_end_and_ttl(self):
        for task, asks in (('t', True), ('c', False)):
            self.task(task); self.add_run(task + '-r', task)
            self.submit('progress', task, task + '-r', 3, {'status': 'running', 'phase': 'p'}, OLD)
            if asks:
                q = self.ask(task + '-r', 4, 'Old question', task, OLD)
            self.submit('progress', task, task + '-r', 5, {'status': 'ended'}, OLD)
        self.assertTrue(self.listed('c')['display_expired'])
        self.assertFalse(self.listed('t')['display_expired'])
        self.assertEqual(self.open_ids('t'), [q])
        self.add_run('t-new', 't')  # Handoff: the new owner does not answer it.
        self.submit('progress', 't', 't-new', 3, {'status': 'running'})
        entry = self.listed('t')['open_questions'][0]
        self.assertEqual((entry['run_id'], entry['former_owner'], entry['run_ended']), ('t-r', True, True))
        self.submit('withdraw', 't', 't-new', 4, {'question_id': q, 'reason': 'user answered in chat'})
        self.assertEqual(self.open_ids('t'), [])

    def test_asker_run_counts_for_joint_filters(self):
        self.task(); self.add_run('owner'); self.add_run('peer', claim=False); self.add_run('rev', claim=False, role='audit')
        self.ask('peer', 2); self.ask('rev', 2)  # Neither ever reported progress.
        task = self.listed()
        self.assertEqual([r['id'] for r in task['parallel_runs'] if r['human_input_required']], ['peer'])
        self.assertEqual([r['id'] for r in task['overview_runs'] if r['human_input_required']], ['rev'])
        self.assertFalse(task['overview_runs'][0]['human_input_required'])

    # 6. target_run closes a declaration; a later declaration reopens it.
    def test_target_run_closes_declaration_and_reopens(self):
        self.task(); self.add_run('old'); self.task('u'); self.add_run('x', 'u')
        self.submit('progress', 't', 'old', 3, {'status': 'waiting_input', 'human_input': 'required', 'decision': 'pick'})
        self.add_run('new'); self.submit('progress', 't', 'new', 3, {'status': 'running', 'human_input': 'not_required'})
        declared = self.listed()['open_questions']
        self.assertEqual([(q['kind'], q['run_id'], q['question']) for q in declared], [('declaration', 'old', 'pick')])
        self.submit('withdraw', 'u', 'x', 3, {'target_run': 'old', 'reason': 'wrong task'})
        self.assertTrue(self.listed()['human_input_required'])  # Never crosses tasks.
        self.assertEqual([c['why'] for c in self.detail('u')['questions']['unmatched']], ['other_task_run'])
        self.submit('withdraw', 't', 'new', 4, {'target_run': 'old', 'reason': 'answered before handoff'})
        task = self.listed()
        self.assertFalse(task['human_input_required'])
        self.assertFalse(next(r for r in task['parallel_runs'] if r['id'] == 'old')['human_input_required'])
        self.assertEqual(self.detail()['questions']['closed'][0]['kind'], 'declaration')
        self.assertEqual(self.store.view('t')['runs'][0]['state']['human_input'], 'required')  # State untouched.
        self.submit('progress', 't', 'old', 4, {'human_input': 'required', 'decision': 'pick again'})
        self.assertEqual([q['question'] for q in self.listed()['open_questions']], ['pick again'])
        self.submit('resolve', 't', 'new', 5, {'target_run': 'new', 'answer': 'nothing open'})
        self.assertEqual([c['why'] for c in self.detail()['questions']['unmatched']], ['no_open_declaration'])

    def test_closed_declaration_does_not_hold_display(self):
        self.task(); self.add_run('r')
        self.submit('progress', 't', 'r', 3, {'status': 'ended', 'human_input': 'required'}, OLD)
        self.assertFalse(self.listed()['display_expired'])
        self.submit('withdraw', 't', 'r', 4, {'target_run': 'r', 'reason': 'answered'}, OLD)
        self.assertTrue(self.listed()['display_expired'])

    # 7. Unlinked, unknown and mismatched links are unassigned; finished items keep their runs.
    def test_links_and_unassigned_runs(self):
        self.task(); self.add_run('a'); self.plan()
        self.write('add_branch', {'mainline_id': 'm1', 'id': 'b1', 'goal': 'side', 'next_step': 'branch says'})
        self.write('progress', {'mainline_id': 'm2', 'fields': {'status': 'done', 'result': 'shipped'}})
        links = {'b': {'mainline': 'm1'}, 'c': {'mainline': 'm9'}, 'd': {'mainline': 'm2', 'branch': 'b1'},
                 'e': {'mainline': 'm1', 'branch': 'b1'}, 'g': {'mainline': 'm2'}, 'f': {'mainline': 'm1'}}
        for run, item in links.items():
            self.add_run(run, claim=False)
            self.submit('progress', 't', run, 2, {'status': 'ended' if run == 'f' else 'running', 'item': item})
        self.submit('progress', 't', 'a', 3, {'status': 'running'})
        view = self.detail()
        self.assertEqual({k: [r['id'] for r in v] for k, v in view['item_runs'].items()}, {'m1': ['b'], 'm1/b1': ['e'], 'm2': ['g']})
        self.assertEqual(view['item_runs']['m2'][0]['link_state'], 'finished_item')
        self.assertEqual({r['id']: r['link_state'] for r in view['unassigned_runs']}, {'a': 'none', 'c': 'unknown_item', 'd': 'unknown_item'})
        task = self.listed()
        self.assertEqual(task['unassigned_run_count'], 3)
        self.assertNotIn('run_items', task); self.assertNotIn('item_runs', task)
        self.submit('progress', 't', 'b', 3, {'item': None})  # Explicit unlink.
        self.assertEqual(self.listed()['unassigned_run_count'], 4)
        self.task('u'); self.add_run('n', 'u'); self.submit('progress', 'u', 'n', 3, {'item': {'mainline': 'm1'}})
        plain = self.detail('u')
        self.assertIsNone(plain['unassigned_runs']); self.assertIsNone(self.listed('u')['unassigned_run_count'])
        self.assertEqual(plain['run_items'][0]['link_state'], 'no_plan')

    # 8. Links follow the run's own sequence and ownership rules.
    def test_late_sequence_and_former_owner_links(self):
        self.task(); self.add_run('old'); self.plan()
        self.submit('progress', 't', 'old', 5, {'item': {'mainline': 'm1'}})
        self.assertFalse(self.store.submit(self.event('progress', 't', 'old', 4, {'item': {'mainline': 'm2'}}))['applied'])
        self.add_run('new'); self.submit('progress', 't', 'new', 3, {'item': {'mainline': 'm2'}})
        self.submit('progress', 't', 'old', 6, {'item': {'mainline': 'm2'}})
        items = {r['id']: r['item'] for r in self.detail()['run_items']}
        self.assertEqual(items, {'old': {'mainline': 'm2'}, 'new': {'mainline': 'm2'}})
        self.submit('progress', 't', 'old', 7, {'item': {'mainline': 'm1'}})
        self.assertEqual(self.detail()['state']['item'], {'mainline': 'm2'})

    # 9. Plan progress comes only from item statuses.
    def test_plan_counts_ignore_links(self):
        self.task(); self.add_run('a'); self.plan()
        before = plans.listing(self.store)[0]['overview']
        for i in range(3):
            self.add_run('l' + str(i), claim=False)
            self.submit('progress', 't', 'l' + str(i), 2, {'status': 'ended', 'item': {'mainline': 'm1'}})
        self.assertEqual(plans.listing(self.store)[0]['overview'], before)
        self.assertEqual(plans.read(self.store, 't')['board']['mainlines'][0]['status'], 'planned')

    # 10. One authoritative next step and its source.
    def test_next_step_source_switching(self):
        self.task(); self.add_run('r')
        self.assertEqual(self.listed()['next_step']['source'], None)
        self.submit('progress', 't', 'r', 3, {'next_step': 'run says'})
        self.assertEqual({k: self.listed()['next_step'][k] for k in ('text', 'source', 'run_id')},
                         {'text': 'run says', 'source': 'current_run', 'run_id': 'r'})
        self.write('create', {'goal': 'G', 'mode': 'plan_execute', 'user_request': 'user enabled'})
        self.write('draft', {'mainlines': [line('m1'), line('m2')]})
        self.assertEqual(self.listed()['next_step']['source'], 'current_run')  # Draft is not authoritative.
        self.write('freeze')
        step = self.listed()['next_step']
        self.assertEqual((step['text'], step['source'], step['item']), ('plan says m1', 'plan_focus', {'mainline': 'm1'}))
        self.assertEqual(plans.listing(self.store)[0]['next_step'], 'plan says m1')
        self.assertEqual(self.detail()['next_step'], step)
        self.write('add_branch', {'mainline_id': 'm1', 'id': 'b1', 'goal': 'side', 'next_step': 'branch says'})
        self.write('focus', {'mainline_id': 'm1', 'branch_id': 'b1'})
        step = self.listed()['next_step']
        self.assertEqual((step['text'], step['item']), ('branch says', {'mainline': 'm1', 'branch': 'b1'}))
        self.write('complete_branch', {'branch_id': 'b1', 'result': 'merged'})
        self.assertEqual(self.listed()['next_step']['text'], 'plan says m1')
        self.write('close', {'reason': 'done'})
        self.assertEqual(self.listed()['next_step']['text'], 'run says')

    # 11/12. CLI: item preflight online, queued offline, question_id receipt, next-step note.
    def test_cli_links_questions_and_next_step_note(self):
        self.task(); self.add_run('r'); self.plan(); self.task('u'); self.add_run('s', 'u'); self.online()
        board.sequence(self.runtime, 'r', minimum=2); board.sequence(self.runtime, 's', minimum=2)
        before = self.seq_value('r')
        for args, marker in ((('--mainline', 'm9'), 'UNKNOWN_ITEM'), (('--mainline', 'm1', '--branch', 'b9'), 'UNKNOWN_ITEM'),
                             (('--branch', 'b1'), 'requires --mainline'), (('--mainline', 'm1', '--unlink'), 'either')):
            code, out = self.call('report', 't', 'r', *args)
            self.assertEqual(code, 1, out); self.assertIn(marker, out['error'])
        code, out = self.call('report', 'u', 's', '--mainline', 'm1')
        self.assertEqual(code, 1, out); self.assertIn('NO_PLAN', out['error'])
        self.assertEqual((self.seq_value('r'), self.queued()), (before, []))
        code, out = self.call('report', 't', 'r', '--mainline', 'm1', '--phase', 'linked')
        self.assertEqual(code, 0, out); self.assertNotIn('note', out)
        self.assertEqual(self.detail()['state']['item'], {'mainline': 'm1'})
        code, out = self.call('report', 't', 'r', '--next-step', 'run note')
        self.assertEqual(code, 0, out); self.assertIn('The task next step comes from the plan focus', out['note'])
        self.assertEqual((self.detail()['state']['next_step'], self.detail()['next_step']['text']), ('run note', 'plan says m1'))
        code, out = self.call('report', 'u', 's', '--next-step', 'plain')
        self.assertEqual(code, 0, out); self.assertNotIn('note', out)
        code, out = self.call('ask', 't', 'r', '--question', 'A or B?', '--option', 'A', '--option', 'B', '--mainline', 'm1')
        self.assertEqual(code, 0, out); self.assertEqual(out['question_id'], out['event_id'])
        entry = self.listed()['open_questions'][0]
        self.assertEqual((entry['question_id'], entry['options'], entry['item'], entry['link_state']),
                         (out['question_id'], ['A', 'B'], {'mainline': 'm1'}, 'linked'))
        code, failed = self.call('resolve', 't', 'r', '--answer', 'A')
        self.assertEqual(code, 1, failed); self.assertIn('question-id', failed['error'])
        code, done = self.call('resolve', 't', 'r', '--question-id', out['question_id'], '--answer', 'A')
        self.assertEqual(code, 0, done); self.assertEqual(self.open_ids(), [])
        code, state = self.call('show')
        task = next(t for t in state['tasks'] if t['id'] == 't')
        self.assertEqual((task['next_step']['source'], task['open_questions'], task['unassigned_run_count']), ('plan_focus', [], 0))
        self.offline()
        code, out = self.call('report', 't', 'r', '--mainline', 'm9')
        self.assertEqual((code, out['status']), (2, 'QUEUED_NOT_RECORDED'), out)  # Judged at read time once delivered.
        code, out = self.call('ask', 't', 'r', '--question', 'offline?')
        self.assertEqual((code, out['status']), (2, 'QUEUED_NOT_RECORDED'), out)
        queued = [json.loads(p.read_text(encoding='utf-8'))['event'] for p in self.queued()]
        self.assertIn(out['question_id'], [e['event_id'] for e in queued if e['op'] == 'ask'])
        code, out = self.call('withdraw', 't', 'r', '--target-run', 'r', '--reason', 'x')
        self.assertEqual(code, 2, out)


if __name__ == '__main__':
    unittest.main()
