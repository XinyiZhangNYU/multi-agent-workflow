"""Task-scoped flush/receipts, history snapshots and close-reason quota; isolated SQLite/HTTP/outboxes, never live data."""
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_task_board as base
from test_task_board import board, plans, helper, packed


class ScopedFlushAndQuotaTests(unittest.TestCase):
    setUp = base.TaskBoardTests.setUp
    tearDown = base.TaskBoardTests.tearDown
    task = base.TaskBoardTests.task
    req = base.TaskBoardTests.req
    write = base.TaskBoardTests.write
    line = base.TaskBoardTests.line
    active = base.TaskBoardTests.active
    online = base.TaskBoardTests.online

    def progress_pair(self):
        for tid in ('A', 'B'):
            board.enqueue(self.runtime, board.envelope('task', tid,
                {'title': tid, 'project': 'Test', 'workstream': 'test'}), self.config)
        return {p: p.read_bytes() for p in (self.runtime/'outbox').glob('*/*.json')
                if json.loads(p.read_bytes())['event']['task_id'] == 'B'}

    def plan_pair(self):
        for tid in ('t', 'other'):
            helper.enqueue(self.runtime, self.req('create',
                {'goal': tid, 'mode': 'plan_execute', 'user_request': 'synthetic user'}, tid), self.config)
        return {p: p.read_bytes() for p in (self.runtime/'task-board-outbox').glob('*.json')
                if json.loads(p.read_bytes())['request']['task_id'] == 'other'}

    def test_progress_scoped_receipt_and_exit_ignore_unrelated_pending(self):
        self.online(); untouched = self.progress_pair()
        result = board.flush(self.runtime, self.config, task_id='A')
        self.assertEqual((result['recorded'], result['remaining'], board.exit_code(result)), (1, 0, 0))
        self.assertEqual(result['scoped_remaining'], 0); self.assertEqual(result['global_remaining'], 1)
        self.assertEqual({p:p.read_bytes() for p in untouched}, untouched)
        empty = board.flush(self.runtime, self.config, task_id='absent')
        self.assertEqual((empty['remaining'], empty['global_remaining'], board.exit_code(empty)), (0, 1, 0))
        global_result = board.flush(self.runtime, self.config)
        self.assertEqual((global_result['recorded'],global_result['remaining'],global_result['global_remaining']), (1,0,0))

    def test_scoped_progress_actual_cli_exit_zero(self):
        self.online(); untouched = self.progress_pair()
        wf = self.root/'.ai-workflow'; wf.mkdir(); (wf/'WORKFLOW.md').write_text('isolated', encoding='utf-8')
        config = self.root/'config.json'; config.write_text(json.dumps(self.config), encoding='utf-8')
        p = subprocess.run([sys.executable, '-B', board.__file__, '--config', str(config),
            '--runtime', str(self.runtime), '--project-root', str(self.root), 'flush', '--task', 'A'], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(json.loads(p.stdout)['global_remaining'], 1)
        self.assertEqual({p:p.read_bytes() for p in untouched}, untouched)

    def test_plan_scoped_manual_flush_and_global_counts(self):
        self.online(); untouched = self.plan_pair()
        args = SimpleNamespace(plan_command='flush', task='t')
        result = helper.run(args, self.config, self.runtime, board.request, board.health)
        self.assertEqual((result['recorded'],result['remaining'],board.exit_code(result)), (1,0,0))
        self.assertEqual(result['status'],'SYNCED'); self.assertEqual(result['global_remaining'],1)
        self.assertEqual({p:p.read_bytes() for p in untouched}, untouched)

    def test_plan_offline_scoped_counts_and_parser(self):
        self.plan_pair()
        result = helper.flush(self.runtime, self.config, board.request, board.health, task_id='t')
        self.assertEqual((result['remaining'], result['scoped_remaining'], result['global_remaining']), (1,1,2))
        self.assertEqual(board.exit_code(result),2)
        import argparse
        parser=argparse.ArgumentParser(); helper.parser(parser.add_subparsers(dest='command'))
        self.assertEqual(parser.parse_args(['task-board','flush','--task','t']).task,'t')

    def test_history_uses_one_validated_sqlite_snapshot_across_concurrent_update(self):
        self.active(); self.online(); original = plans.read
        expected = original(self.store,'t')['version']; snapshots=[]
        def read_then_write(store, tid):
            value = original(store,tid); snapshots.append(value['version'])
            if len(snapshots)==1:
                # Write through another SQLite connection AFTER taking the requested snapshot.
                plans.apply(store, {'event_id':'concurrent-plan-change','task_id':tid,
                    'expected_version':value['version'],'op':'change','data':{'action':'cancel',
                    'mainline_id':'first','reason':'synthetic concurrent change','decision_ref':'synthetic user'}})
            return value
        with patch.object(plans, 'read', side_effect=read_then_write):
            value=board.request(self.runtime,f'/api/task-board?task_id=t&section=history&version={expected}')
        self.assertEqual(value['version'],expected); self.assertEqual(value['history'],[])
        self.assertEqual(snapshots,[expected])
        self.assertEqual(original(self.store,'t')['version'],expected+1)

    def test_cli_rejects_wrong_requested_history_version_before_cache(self):
        self.active(); self.online()
        def transport(*args,**kwargs):
            value=board.request(*args,**kwargs); value['version']+=1; return value
        args=SimpleNamespace(plan_command='history',task_id='t',mainline=None,offset=0,cached=False,version=3)
        with self.assertRaisesRegex(RuntimeError,'version'):
            helper.run(args,self.config,self.runtime,transport,board.health)
        self.assertFalse((self.runtime/'task-board-summary-cache').exists())

    def test_exact_quota_close_multibyte_reasons_remains_readable_and_deletable(self):
        self.online()
        for i,reason in enumerate(('\u4e2d'*2000,'😀'*2000)):
            with self.subTest(reason=reason[0]):
                tid='close-'+str(i); self.task(tid); self.active(tid)
                # Fill above the existing 4096-byte configuration minimum.
                for mid in ('first','second'):
                    self.write('progress',{'mainline_id':mid,'fields':{'result':'x'*2000}},tid)
                size=len(packed(plans.read(self.store,tid)['board']).encode())
                self.config.update(max_task_board_bytes=size,max_task_board_receipts=1,max_database_bytes=1)
                with patch.object(plans,'CLOSE_RESERVE_BYTES',4096):
                    with self.assertRaisesRegex(ValueError,'capacity'):
                        self.write('close',{'reason':reason},tid)
                self.write('close',{'reason':reason},tid)
                value=board.request(self.runtime,'/api/task-board?task_id='+tid)
                self.assertEqual(value['board']['close_reason'],reason)
                args=SimpleNamespace(plan_command='show',task_id=tid,cached=False)
                helper.run(args,self.config,self.runtime,board.request,board.health)
                args.cached=True; self.assertEqual(helper.run(args,self.config,self.runtime,board.request,board.health)['board']['close_reason'],reason)
                self.write('delete',{'reason':'user deletes synthetic plan','decision_ref':'synthetic user'},tid)
                self.assertTrue(plans.read(self.store,tid)['deleted'])
                for key in ('max_task_board_bytes','max_task_board_receipts','max_database_bytes'):self.config.pop(key)

if __name__=='__main__':unittest.main()
