"""Own disposable fixture, including malicious display text. Never uses live runtime."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Store, now
from server import atomic_json

root=Path(sys.argv[1]).resolve()
root.mkdir(parents=True,exist_ok=True)
(root/'.ai-workflow').mkdir()
(root/'.ai-workflow/WORKFLOW.md').write_text('isolated browser test',encoding='utf-8')
config={'project_root':str(root),'project':'Synthetic','port':0,'stale_seconds':1}
config_path=root/'config.json'; atomic_json(config_path,config)
runtime=root/'runtime'; store=Store(runtime,config)
for event in [board.envelope('task','xss',{'title':'<img src=x onerror="window.XSS=true">','project':'Synthetic','workstream':'Test'}),
              board.envelope('run','xss',{'role':'execution','agent':'Synthetic','client':'test'},'r',1),
              board.envelope('claim','xss',{'expected_version':0},'r',2),
              board.envelope('progress','xss',{'status':'paused','phase':'<script>window.XSS=true</script>','decision':'Injected markup must render as text only','candidate_sha':'b'*40},'r',3),
              board.envelope('delivery','xss',{'stage':'self_test','verdict':'PASS','candidate_sha':'a'*40,'evidence':'report.md','audit_id':None},'r',4),
              board.envelope('snapshot','xss',{'fields':{'status':'running'},'excerpt':'<svg onload="window.XSS=true">'},source={'kind':'document_snapshot','path':'source.md','source_at':'2026-01-01T00:00:00Z','read_at':now(),'sha256':'1'*64}),
              board.envelope('activity','xss',{'summary':'Test program observation, not a service heartbeat'},source={'kind':'program_observation','path':'observed.json','source_at':now(),'read_at':now()})]:
    store.submit(event)
# Later browser-check reports use the same CLI sequence file.
for _ in range(4): board.sequence(runtime,'r')
result=board.start(SimpleNamespace(config=str(config_path),port=0),config,runtime)
print(json.dumps(result))
