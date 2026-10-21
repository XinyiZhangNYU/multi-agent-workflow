"""Bounded author regressions: no live data, queues or client accounts."""
import json
import time
import unittest
from types import SimpleNamespace
import test_task_board as base
from test_task_board import plans, helper, board, Conflict, Store, packed


class IncrementalTests(unittest.TestCase):
    setUp=base.TaskBoardTests.setUp
    tearDown=base.TaskBoardTests.tearDown
    task=base.TaskBoardTests.task
    req=base.TaskBoardTests.req
    write=base.TaskBoardTests.write
    line=base.TaskBoardTests.line
    active=base.TaskBoardTests.active
    online=base.TaskBoardTests.online
    old_rows=base.TaskBoardTests.old_rows

    def seed_history(self, size):
        b=plans.read(self.store,'t')['board']
        b['mainlines'][0]['branch_results']=[{'branch_id':'r-'+str(i),'result':'x'*1900,'evidence':[]} for i in range(size)]
        with self.store.connect() as db:
            db.execute('UPDATE task_boards SET body=? WHERE task_id=?',(packed(b),'t'))
        return len(packed(b).encode())

    def test_wait_revision_preserves_baseline_and_wait_then_confirm(self):
        self.active(mode='plan_wait'); old=self.old_rows()
        self.write('change',{'action':'replace','mainline_id':'first','replacement':self.line('revised'),'reason':'user revised scope','decision_ref':'synthetic user reply'})
        self.write('progress',{'mainline_id':'revised','fields':{'owner':'agent-b','next_step':'wait for user'}})
        b=plans.read(self.store,'t')['board'];self.assertEqual(b['state'],'awaiting_confirmation')
        self.assertEqual(b['baseline_mainline_ids'],['first','second']);self.assertEqual(b['mainlines'][0]['status'],'replaced')
        with self.assertRaises(Conflict):self.write('draft',{'mainlines':[]})
        with self.assertRaises(Conflict):self.write('progress',{'mainline_id':'revised','fields':{'status':'running'}})
        self.write('confirm',{'reason':'user confirmed revised plan','decision_ref':'synthetic approval'})
        self.write('progress',{'mainline_id':'revised','fields':{'status':'running'}})
        self.assertEqual(self.old_rows(),old)

    def test_overview_counts_not_cancel_completion_or_focus_total(self):
        self.active();self.write('progress',{'mainline_id':'first','fields':{'status':'running'}})
        self.write('progress',{'mainline_id':'second','fields':{'status':'running'}})
        o=plans.overview(plans.read(self.store,'t')['board']);self.assertEqual(o['running_ids'],['first','second'])
        self.write('change',{'action':'cancel','mainline_id':'first','reason':'user cancelled','decision_ref':'user'})
        o=plans.overview(plans.read(self.store,'t')['board']);self.assertEqual((o['done'],o['active'],o['cancelled'],o['baseline']),(0,1,1,2))
        self.assertTrue(o['has_mainlines']);self.assertEqual(o['incomplete'],1)

    def test_summary_history_not_linear_and_pages_lossless(self):
        self.active();original=len(packed(plans.focus_summary(plans.read(self.store,'t'))).encode())
        self.seed_history(1000);value=plans.read(self.store,'t');summary=plans.focus_summary(value)
        self.assertLess(len(packed(summary).encode())-original,10)
        self.assertNotIn('branch_results',summary['summary']['mainlines'][0]);self.assertNotIn('changes',summary['summary'])
        offset=0;seen=[]
        while offset is not None:
            page=plans.history(self.store,'t','first',offset,8192);self.assertLessEqual(len(packed(page).encode()),8192)
            seen.extend(v['branch_id'] for v in page['history']);offset=page['page']['next_offset']
        self.assertEqual(seen,['r-'+str(i) for i in range(1000)])

    def test_summary_budget_pagination_scoped_and_oversize_explicit(self):
        self.active()
        for mid in ('first','second'):
            self.write('progress',{'mainline_id':mid,'fields':{'result':'x'*1800}})
        value=plans.read(self.store,'t');p=plans.focus_summary(value,budget=4096)
        self.assertEqual(p['page']['next_offset'],1);q=plans.focus_summary(value,offset=1,budget=4096)
        self.assertEqual(q['summary']['mainlines'][0]['id'],'second')
        scoped=plans.focus_summary(value,'second');self.assertEqual(len(scoped['summary']['mainlines']),1)
        with self.assertRaises(ValueError):plans.focus_summary(value,budget=2000)

    def test_http_summary_separate_cache_full_above_256k_restart(self):
        self.active()
        # Reach >256KiB via real reducer operations, not a single oversized request.
        for i in range(150):
            self.write('add_branch',{'mainline_id':'first','id':'side-'+str(i),'goal':'bounded side','next_step':'return'})
            self.write('complete_branch',{'branch_id':'side-'+str(i),'result':'x'*1900})
            self.write('delete_branch',{'branch_id':'side-'+str(i)})
        self.assertGreater(len(packed(plans.read(self.store,'t')['board']).encode()),262144);self.online()
        calls=[]
        def transport(*args,**kwargs):calls.append(args[1]);return board.request(*args,**kwargs)
        args=SimpleNamespace(plan_command='summary',task_id='t',mainline=None,offset=0,cached=False,version=None)
        result=helper.run(args,self.config,self.runtime,transport,board.health)
        self.assertIn('summary=1',calls[0]);self.assertNotIn('board',result);self.assertLess(len(packed(result).encode()),32768)
        self.assertFalse((self.runtime/'task-board-cache').exists())
        args.plan_command='show';full=helper.run(args,self.config,self.runtime,transport,board.health)
        self.assertGreater(len(packed(full).encode()),262144)
        self.store=Store(self.runtime,self.config);plans.initialize(self.store)
        args.cached=True;cached=helper.run(args,self.config,self.runtime,transport,board.health)
        self.assertEqual(cached['status'],'CACHED_BASELINE');self.assertEqual(cached['board'],full['board'])
        args.plan_command='summary';cached=helper.run(args,self.config,self.runtime,transport,board.health)
        self.assertIn('summary',cached);self.assertNotIn('board',cached)

    def test_near_10m_full_read_summary_close_delete_at_receipt_limit(self):
        self.active();size=self.seed_history(5300);self.assertGreater(size,10_000_000);self.assertLess(size,plans.DEFAULT_PLAN_BYTES)
        self.online();started=time.perf_counter();full=board.request(self.runtime,'/api/task-board?task_id=t');full_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter();short=board.request(self.runtime,'/api/task-board?task_id=t&summary=1');short_ms=(time.perf_counter()-started)*1000
        self.assertLess(len(packed(short).encode()),32768)
        self.config['max_task_board_receipts']=1;self.config['max_task_board_bytes']=size
        self.config['max_database_bytes']=1  # Artificial quota, not actual disk exhaustion.
        self.write('close',{'reason':'user closes full plan'})
        self.write('delete',{'reason':'user explicitly deletes only test plan','decision_ref':'synthetic user'})
        self.assertTrue(plans.read(self.store,'t')['deleted'])
        with self.assertRaises(Conflict):self.write('create',{'goal':'stale','mode':'plan_execute','user_request':'old'},version=0)
        print(json.dumps({'probe':'10MiB','plan_bytes':size,'full_response_bytes':len(packed(full).encode()),'summary_bytes':len(packed(short).encode()),'full_ms':round(full_ms,2),'summary_ms':round(short_ms,2)}))

    def test_quota_overflow_rolls_back_and_close_uses_small_reserve(self):
        self.active();size=self.seed_history(100)
        self.config['max_task_board_bytes']=size
        before=plans.read(self.store,'t')
        with self.assertRaises(ValueError):self.write('progress',{'mainline_id':'second','fields':{'result':'x'*2000}})
        self.assertEqual(before,plans.read(self.store,'t'))
        self.write('close',{'reason':'x'*2000});self.assertEqual(plans.read(self.store,'t')['board']['state'],'closed')

    def test_same_workstream_independent_plans_and_progress_link(self):
        self.active();self.active('other')
        listed=plans.listing(self.store);self.assertEqual(len(listed),2);self.assertEqual({v['workstream'] for v in listed},{'test'})
        v=plans.attach_overviews(self.store,self.store.view('t'));self.assertEqual(v['task_board']['task_id'],'t')
        self.assertNotIn('branch_results',packed(listed));self.assertEqual(v['state']['status'],'unknown')

    def test_automatic_progress_send_never_replays_other_task(self):
        self.online()
        event=board.envelope('claim','other',{'expected_version':999},'absent',1)
        board.enqueue(self.runtime,event,self.config)
        raw=[(p,p.read_bytes()) for p in (self.runtime/'outbox').glob('*/*.json')]
        own=board.envelope('task','new',{'title':'new','project':'Test','workstream':'new'})
        result=board.send(self.runtime,own,self.config);self.assertEqual(result['status'],'RECORDED')
        self.assertEqual([(p,p.read_bytes()) for p in (self.runtime/'outbox').glob('*/*.json')],raw)

    def test_history_version_and_instance_guards(self):
        self.active();self.online()
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:board.request(self.runtime,'/api/task-board?task_id=t&summary=1&version=1')
        self.assertEqual(ctx.exception.code,409)

    def test_actual_cli_summary_stdout_keeps_byte_budget(self):
        import subprocess,sys
        self.active()
        for mid in ('first','second'):
            self.write('progress',{'mainline_id':mid,'fields':{'result':'\u8fb9\u754c\u7ed3\u679c'*170}})
        self.config['max_task_board_summary_bytes']=4096
        self.online()
        installed=self.root/'.ai-workflow';installed.mkdir()
        (installed/'WORKFLOW.md').write_text('isolated CLI test',encoding='utf-8')
        path=self.root/'config.json';path.write_text(json.dumps(self.config),encoding='utf-8')
        result=subprocess.run([sys.executable,'-B',str(board.__file__),'--config',str(path),
            '--runtime',str(self.runtime),'--project-root',str(self.root),
            'task-board','summary','t'],capture_output=True)
        self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
        self.assertLessEqual(len(result.stdout.rstrip()),4096)
        value=json.loads(result.stdout);self.assertIn('summary',value)
        self.assertNotIn('board',value)

if __name__=='__main__':unittest.main()
