"""Author focused checks: document routing and real interfaces in isolated data.

Not a real agent startup probe or an independent audit. No live service,
model calls, project scans, writes to deployed copies, or unrelated outbox flush.
"""
import argparse
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations' / 'agent-board'))
import board
import task_board as plans
import task_board_cli as cli
from model import Conflict, Store


def flat(text):
    return ' '.join(text.split())


class TaskBoardWorkflowDocuments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / 'WORKFLOW.md').read_text(encoding='utf-8-sig')
        section = cls.workflow.split('### On-demand task board\n', 1)[1].split('\n### Single home for each kind of content', 1)[0]
        cls.section = flat(section)
        cls.flat_workflow = flat(cls.workflow)
        cls.canonical = flat((ROOT / 'startup-contract/STARTUP-CONTRACT.md').read_text(encoding='utf-8-sig'))
        cls.manual = (ROOT / 'runbooks/TASK-BOARD.md').read_text(encoding='utf-8-sig')

    def test_actual_startup_routes_workflow_manual_and_task_record(self):
        for entry in ('WORKFLOW section "On-demand task board"', '.ai-workflow/runbooks/TASK-BOARD.md',
                      "this task's compact server summary and related branches"):
            self.assertIn(entry, self.canonical)
        for text in ('Otherwise skip it', 'does not create a second board',
                     'Neither task complexity nor an enabled project progress board means the task board is enabled'):
            self.assertIn(text, self.canonical)

    def test_disabled_small_tasks_skip_plan_without_canceling_progress_reports(self):
        for text in ('two independent switches', 'skip the dedicated manual', 'do not create an empty record',
                     'do not scan other task boards',
                     'The existing project-level progress reporting obligation is not cancelled'):
            self.assertIn(text, self.section)

    def test_execute_and_wait_routes_have_distinct_authorization(self):
        for text in ('plan first and then execute', 'without waiting for another confirmation',
                     'plan first, wait for my confirmation', 'only do the planning and wait',
                     'never a new business authorization'):
            self.assertIn(text, self.section)
        self.assertIn('plan_execute', self.manual)
        self.assertIn('plan_wait', self.manual)

    def test_restore_keeps_stable_plan_and_narrow_read(self):
        for text in ('bound to a stable task_id', 'does not re-plan over the old content',
                     'needs no second enablement', 'does not authorize a new task',
                     'The lead reads the compact summary of all mainlines', 'direct dependencies',
                     'read the same persisted record'):
            self.assertIn(text, self.section)
        self.assertIn('no new create/draft', self.manual)

    def test_mainline_retention_and_branch_cleanup_are_not_contradictory(self):
        for text in ('including completed ones', 'lower completion criteria',
                     'Completed branches may be deleted', 'Unfinished items cannot be deleted',
                     'never deletes source code, evidence, audit reports or user files',
                     'never automatically mean a mainline is complete'):
            self.assertIn(text, self.section)

    def test_link_targets_are_real_and_navigation_is_connected(self):
        for relative in ('README.md', 'runbooks/TASK-BOARD.md', 'runbooks/AGENT-BOARD.md', 'projects/example/README.md'):
            path = ROOT / relative
            contents = path.read_text(encoding='utf-8-sig')
            self.assertIn('task board', contents)
            for target in re.findall(r'\]\(([^)]+)\)', contents):
                if '://' in target or target.startswith('#'):
                    continue
                self.assertTrue((path.parent / target.split('#', 1)[0]).exists(), (relative, target))
        self.assertIn('runbooks/TASK-BOARD.md', (ROOT / 'README.md').read_text(encoding='utf-8-sig'))
        self.assertIn('TASK-BOARD-INTERFACE.md', self.manual)

    def test_configured_copies_are_the_example_targets(self):
        config = json.loads((ROOT / 'projects/example/startup-targets.json').read_text(encoding='utf-8-sig'))
        self.assertEqual({t['path'] for t in config['targets']}, {'AGENTS.md', 'CLAUDE.md'})
        self.assertEqual(config['canonical'], '../../startup-contract/STARTUP-CONTRACT.md')

    def test_scope_remains_observational(self):
        self.assertRegex(self.workflow, r'(?m)^version: v[1-9]\d*$')
        for text in ('never claim to be synced or bump a conflicting version automatically',
                     'it is not authority for product facts', 'wake models on a timer',
                     'dispatch a resident agent', 'does not stop unrelated authorized work'):
            self.assertIn(text, self.section)

    def test_wait_revision_summary_budget_and_phase_route(self):
        for entry in ('The plan stays awaiting_confirmation', 'Use the service summary and pagination by default',
                      'cancelled and replaced items are listed separately',
                      'When a stage ends, report first and then give the final reply', 'never switch IDs'):
            self.assertIn(entry, self.flat_workflow)
        self.assertIn('report briefly when a stage completes before replying', self.canonical)
        self.assertIn('task-board history', self.manual)

    def test_documented_cli_calls_parse_with_actual_interface(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='command', required=True)
        cli.parser(sub)
        calls = re.findall(r'py -3 -B \$Board task-board ([^\n]+)', self.manual)
        self.assertGreaterEqual(len(calls), 15)
        for call in calls:
            # This is argument parsing only, never invoke the helper or HTTP.
            args = ['task-board'] + call.split(' |', 1)[0].split()
            args = ['2' if a == '$Current.version' else 'example-task' if a == '$Task' else a.strip("'") for a in args]
            parser.parse_args(args)

    def test_recovery_examples_are_scoped_and_start_does_not_replay(self):
        progress = (ROOT / 'runbooks/AGENT-BOARD.md').read_text(encoding='utf-8-sig')
        recovery = progress.split('A real 409 keeps the original', 1)[1].split('Defaults: one event', 1)[0]
        self.assertNotIn('py -3 -B $board flush\n', recovery)
        self.assertIn('`start` only starts the service and **does not flush the queue**', flat(recovery))
        self.assertIn('remaining/scoped_remaining', recovery)
        self.assertIn('global_remaining', recovery)
        self.assertIn('task-board flush --task $Task', self.manual)


class TaskBoardManualLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workflow-task-board-')
        self.root = Path(self.temp.name)
        self.config = {'project_root': str(self.root), 'project': 'WorkflowTest'}
        self.store = Store(self.root / 'runtime', self.config)
        plans.initialize(self.store)
        self.store.submit(board.envelope('task', 'task', {'title': 'isolated', 'workstream': 'test', 'project': 'WorkflowTest'}))
        self.manual = (ROOT / 'runbooks/TASK-BOARD.md').read_text(encoding='utf-8-sig')
        self.seq = 0

    def tearDown(self):
        self.temp.cleanup()

    def data(self, filename):
        section = self.manual.split('### ' + filename + '\n', 1)[1]
        return json.loads(re.search(r'```json\n(.*?)\n```', section, re.S).group(1))

    def write(self, op, data=None):
        self.seq += 1
        return plans.apply(self.store, {'event_id': 'workflow-test-' + str(self.seq), 'task_id': 'task',
                                      'expected_version': plans.read(self.store, 'task')['version'], 'op': op, 'data': data or {}})

    def active(self, mode='plan_execute'):
        data = self.data('create-data.json'); data['mode'] = mode
        self.write('create', data)
        self.write('draft', self.data('draft-data.json'))
        self.write('freeze')

    def test_no_plan_created_by_progress_task_or_reads(self):
        self.assertEqual(plans.listing(self.store), [])
        self.assertFalse(plans.read(self.store, 'task')['exists'])
        self.assertEqual(plans.listing(self.store), [])

    def test_execute_example_freezes_without_extra_confirmation(self):
        self.active()
        self.assertEqual(plans.read(self.store, 'task')['board']['state'], 'active')
        self.write('progress', self.data('progress-data.json'))
        self.write('focus', self.data('focus-data.json'))
        self.assertEqual(plans.focus_summary(plans.read(self.store, 'task'))['summary']['focus']['mainline_id'], 'main-1')

    def test_wait_example_cannot_start_until_confirmation(self):
        self.active('plan_wait')
        self.assertEqual(plans.read(self.store, 'task')['board']['state'], 'awaiting_confirmation')
        with self.assertRaises(Conflict):
            self.write('progress', self.data('progress-data.json'))
        self.write('confirm', self.data('confirm-data.json'))
        self.write('progress', self.data('progress-data.json'))

    def test_restore_and_handoff_read_same_baseline(self):
        self.active(); self.write('progress', self.data('progress-data.json'))
        before = plans.read(self.store, 'task')
        another_session = Store(self.root / 'runtime', self.config); plans.initialize(another_session)
        self.assertEqual(before, plans.read(another_session, 'task'))
        self.assertEqual(plans.focus_summary(before)['summary'], plans.focus_summary(plans.read(another_session, 'task'))['summary'])
        with self.assertRaises(Conflict):
            self.write('create', self.data('create-data.json'))

    def test_frozen_mainlines_survive_branch_completion_and_deletion(self):
        self.active()
        before = plans.read(self.store, 'task')
        with self.assertRaises(Conflict):
            self.write('draft', {'mainlines': []})
        self.assertEqual(before, plans.read(self.store, 'task'))
        evidence = self.root / 'scratch/incoming/own-task/REPORT.md'
        evidence.parent.mkdir(parents=True); evidence.write_text('owned synthetic evidence', encoding='utf-8')
        self.write('add_branch', self.data('add-branch-data.json'))
        with self.assertRaises(Conflict):
            self.write('delete_branch', self.data('delete-branch-data.json'))
        self.write('complete_branch', self.data('finish-branch-data.json'))
        self.write('delete_branch', self.data('delete-branch-data.json'))
        plan = plans.read(self.store, 'task')['board']
        self.assertEqual([m['id'] for m in plan['mainlines']], ['main-1', 'main-2'])
        self.assertEqual(plan['mainlines'][0]['status'], 'planned')
        self.assertEqual(plan['mainlines'][0]['branch_results'][0]['result'], self.data('finish-branch-data.json')['result'])
        self.assertEqual(evidence.read_text(encoding='utf-8'), 'owned synthetic evidence')

    def test_closed_plan_keeps_mainlines_and_original_progress_task(self):
        self.active(); self.write('close', self.data('close-data.json'))
        self.assertEqual(len(plans.read(self.store, 'task')['board']['mainlines']), 2)
        self.assertEqual(self.store.view()['tasks'][0]['id'], 'task')


if __name__ == '__main__':
    unittest.main(verbosity=2)
