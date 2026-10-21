"""Owned synthetic UI fixture; starts a random loopback port, never live data."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board
from server import atomic_json
from workstream_fixture import make_fixtures

if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve()
    (root / '.ai-workflow').mkdir(parents=True)
    (root / '.ai-workflow/WORKFLOW.md').write_text('Owned synthetic test', encoding='utf-8')
    stores, _, config = make_fixtures(root)
    store = stores['stress']; task_board.initialize(store)
    store.submit(board.envelope('task','export-second',{'title':'Second independent task in the same workstream','project':'Test','workstream':'Data export'}))
    for tid in ('public-docs', 'doc-export', 'export-second'):
        count = 0
        def emit(op, data):
            global count
            count += 1
            return task_board.apply(store, {'event_id': tid + '-plan-' + str(count), 'task_id': tid,
                'expected_version': task_board.read(store, tid)['version'], 'op': op, 'data': data})
        emit('create', {'goal': ('Page delivery' if tid == 'public-docs' else 'Data recheck') + ' · synthetic / not live', 'mode': 'plan_execute', 'user_request': 'synthetic opt-in'})
        emit('draft', {'mainlines': [dict(id='first', goal='Mainline one · Finish a deliverable result', completion='Result and evidence can be rechecked', owner='agent-a', next_step='Check the inputs first'),
                                    dict(id='second', goal='Mainline two · Non-tool-call checklist', completion='Boundary checks pass', owner='agent-b', next_step='Run proportionate verification', dependencies=['first'])]})
        emit('freeze', {})
        emit('progress', {'mainline_id': 'first', 'fields': {'status': 'done', 'result': 'Done; still kept on the mainline', 'evidence': ['evidence/task-board-isolated-demo/evidence-with-a-very-long-path-name/REPORT.md']}})
        emit('add_branch', {'mainline_id': 'second', 'id': 'side', 'goal': 'Required branch · <img src=x onerror=alert(1)>', 'next_step': 'Return to mainline two when done'})
        emit('focus', {'mainline_id': 'second', 'branch_id': 'side'})
        emit('progress', {'mainline_id':'second','fields':{'status':'running'}})
    config['port'] = 0; path = root / 'config.json'; atomic_json(path, config)
    print(json.dumps(board.start(SimpleNamespace(config=str(path), port=0), config, root / 'stress')))
