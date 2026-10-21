"""Owned HTTP fixture for the brief / agents / feed views; never opens the live database."""
import json
import sys
from datetime import datetime, timedelta, timezone
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


def at(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


try:
    events, seqs = [], {}
    def add(op, task, data, run=None, days=0):
        if run: seqs[run] = seqs.get(run, 0) + 1
        event = board.envelope(op, task, data, run, seqs.get(run) if run else None)
        if days: event['occurred_at'] = at(days)
        events.append(event)
    tasks = [('asked', '<img src=x onerror="window.XSS=1">Pick an approach', 'Implementation', 'asker', 'Claude-Code', 0),
             ('moving', 'Task in progress', 'Implementation', 'worker', 'Codex', 0),
             ('done', 'Just finished task', 'Review', 'finisher', 'Claude-Code', 0),
             ('quiet', 'Task silent for three days', 'Review', 'sleeper', 'Codex', 3),
             ('ancient', 'Old task from ten days ago', 'Review', 'elder', 'Codex', 10)]
    for task, title, stream, run, agent, days in tasks:
        add('task', task, {'title':title, 'project':'Synthetic', 'workstream':stream}, days=days)
        add('run', task, {'role':'execution', 'agent':agent, 'client':'synthetic CLI'}, run, days)
        add('claim', task, {'expected_version':0}, run, days)
    add('progress', 'asked', {'status':'running', 'phase':'Comparing two approaches'}, 'asker')
    add('progress', 'moving', {'status':'running', 'phase':'Writing tests\nsecond line is hidden'}, 'worker')
    add('progress', 'done', {'status':'ended', 'phase':'Implementation done'}, 'finisher')
    add('progress', 'quiet', {'status':'running', 'phase':'A phase from long ago'}, 'sleeper', 3)
    add('progress', 'ancient', {'status':'ended', 'phase':'Ended long ago'}, 'elder', 10)
    add('ask', 'asked', {'question':'<script>window.XSS=2</script>Use approach A or B?', 'options':['A', 'B'],
                         'unanswered_policy':'Default to A'}, 'asker')
    for event in events:
        board.send(runtime, event, config, queue=False)
    # Later browser-test reports use the same CLI sequence files.
    for run, seq in seqs.items():
        for _ in range(seq): board.sequence(runtime, run)
    print(json.dumps(result))
except BaseException:
    board.request(runtime, '/api/stop', {'instance_id':result['instance_id']})
    raise
