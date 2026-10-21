"""Owned HTTP fixture for overview regression; never opens the live database."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from server import atomic_json

root = Path(sys.argv[1]).resolve()
(root / '.ai-workflow').mkdir()
(root / '.ai-workflow/WORKFLOW.md').write_text('isolated test', encoding='utf-8')
config = {'project_root':str(root), 'project':'Synthetic', 'port':0, 'stale_seconds':86400}
config_path = root / 'config.json'
runtime = root / 'runtime'
atomic_json(config_path, config)
result = board.start(SimpleNamespace(config=str(config_path), port=0), config, runtime)
try:
    events = []
    for task, title in [('reviewed','Execution ended, audit continues'),('input','Waiting for input, item not stated'),('orphan','Audit with no executor yet')]:
        events.append(board.envelope('task', task, {'title':title,'project':'Synthetic','workstream':'Review' if task != 'input' else 'Implementation'}))
    for task, run, role, agent in [('reviewed','impl','execution','Implementer'),('reviewed','audit-one','audit','Reviewer-one'),
                                  ('reviewed','audit-two','audit','Reviewer-two'),('input','input-owner','execution','Implementer'),
                                  ('orphan','orphan-review','audit','Reviewer-orphan')]:
        events.append(board.envelope('run', task, {'role':role,'agent':agent,'client':'synthetic CLI'}, run, 1))
        if role == 'execution':
            events.append(board.envelope('claim', task, {'expected_version':0}, run, 2))
    for task, run, seq, state in [
        ('reviewed','impl',3,{'status':'ended','phase':'Implementation done','candidate_sha':'a'*40}),
        ('reviewed','audit-one',2,{'status':'running','phase':'Checking event boundaries','candidate_sha':'a'*40,'progress':'<img src=x onerror="window.XSS=true">'}),
        ('reviewed','audit-two',2,{'status':'running','phase':'Audit of another candidate','candidate_sha':'b'*40}),
        ('input','input-owner',3,{'status':'waiting_input'}),
        ('orphan','orphan-review',2,{'status':'waiting_input'})]:
        events.append(board.envelope('progress',task,state,run,seq))
    for event in events:
        board.send(runtime,event,config,queue=False)
    # The fixture uses the same CLI sequence files for later browser-test updates.
    for run, seq in [('impl',3),('audit-one',2),('audit-two',2),('input-owner',3),('orphan-review',2)]:
        for _ in range(seq): board.sequence(runtime,run)
    print(json.dumps(result))
except BaseException:
    board.request(runtime,'/api/stop',{'instance_id':result['instance_id']})
    raise
