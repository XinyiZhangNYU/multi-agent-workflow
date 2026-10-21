"""Separate OS process / CLI lifecycle test, not a SQLite reopen substitute."""
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

CLI=Path(__file__).resolve().parents[1]/'board.py'


class LifecycleTest(unittest.TestCase):
    def test_stop_queue_start_restart_and_exact_stop(self):
        with tempfile.TemporaryDirectory(prefix='agent-board-lifecycle-') as tmp:
            root=Path(tmp); (root/'.ai-workflow').mkdir()
            (root/'.ai-workflow/WORKFLOW.md').write_text('test')
            config=root/'config.json'; runtime=root/'runtime'
            config.write_text(json.dumps({'project_root':str(root),'project':'Test','port':0}))
            cmd=[sys.executable,'-B',str(CLI),'--config',str(config),'--runtime',str(runtime)]
            def call(*args,code=0):
                p=subprocess.run(cmd+list(args),capture_output=True,encoding='utf-8',timeout=15)
                self.assertEqual(p.returncode,code,p.stdout+p.stderr)
                return json.loads(p.stdout)
            try:
                first=call('start'); self.assertEqual(first['status'],'RUNNING')
                call('task','test','--title','Lifecycle','--workstream','Test')
                call('run','test','z-first','--agent','script','--client','synthetic')
                call('claim','test','z-first','--expected-version','0')
                stopped=call('stop'); self.assertEqual(stopped['status'],'STOPPED')
                queued=call('report','test','z-first','--status','paused','--phase','offline',code=2)
                self.assertEqual(queued['status'],'QUEUED_NOT_RECORDED')
                second=call('start'); self.assertNotEqual(first['instance_id'],second['instance_id'])
                self.assertEqual(second['outbox']['status'],'NOT_REPLAYED')
                self.assertEqual(call('flush','--task','test')['recorded'],1)
                self.assertEqual(call('show','test')['state']['phase'],'offline')
                self.assertEqual(call('flush')['recorded'],0)
                before=call('show')['event_count']
                call('stop'); third=call('start')
                self.assertNotEqual(second['instance_id'],third['instance_id'])
                self.assertEqual(call('show')['event_count'],before)
                self.assertEqual(call('show','test')['current_run'],'z-first')
                # POST stop with wrong identity must not stop the verified instance.
                sys.path.insert(0,str(CLI.parent))
                import board
                with self.assertRaises(urllib.error.HTTPError) as mismatch:
                    board.request(runtime,'/api/stop',{'instance_id':first['instance_id']})
                self.assertEqual(mismatch.exception.code, 409)
                self.assertIn('instance identity mismatch', mismatch.exception.read().decode('utf-8'))
                self.assertEqual(call('status')['instance_id'],third['instance_id'])
            finally:
                if call('status')['status']=='RUNNING': call('stop')
