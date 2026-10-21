"""Owned synthetic parallel-run cases served by the real adapter; no live board access."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Store, stamp
from server import atomic_json


def start_fixture(target):
    target = Path(target).resolve()
    (target / '.ai-workflow').mkdir(parents=True)
    (target / '.ai-workflow/WORKFLOW.md').write_text('Owned synthetic parallel fixture', encoding='utf-8')
    config = {'project_root': str(target), 'project': 'Test', 'stale_seconds': 86400, 'port': 0, 'display_ttl_seconds': 604800}
    runtime = target / 'runtime'
    store, seqs = Store(runtime, config), {}

    def emit(op, task, data, run=None, days=None):
        seq = None
        if run:
            seq = seqs[run] = seqs.get(run, 0) + 1
        event = board.envelope(op, task, data, run, seq)
        if days is not None:
            event['occurred_at'] = stamp((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
        store.submit(event)

    def run(task, name, agent, claim=True):
        emit('run', task, {'role': 'execution', 'agent': agent, 'client': 'synthetic'}, name)
        if claim:
            emit('claim', task, {'expected_version': store.view(task)['version']}, name)

    calm = {'status': 'running', 'human_input': 'not_required'}
    for key, title in (('peer-ask', 'Parallel run asks'), ('handoff-ask', 'Former owner asks'), ('ended-ask', 'Ended run never withdrew'),
                       ('fresh-peer', 'Owner silent, parallel run just reported'), ('quiet', 'Control without requests')):
        emit('task', key, {'title': title, 'project': 'Test', 'workstream': 'Parallel lab'})
    run('peer-ask', 'peer-owner', 'OwnerAgent'); emit('progress', 'peer-ask', dict(calm, phase='Owner executing'), 'peer-owner')
    run('peer-ask', 'peer-run', 'PeerAgent', claim=False)
    emit('progress', 'peer-ask', {'status': 'waiting_input', 'human_input': 'required', 'decision': 'Parallel run asks you to pick A or B'}, 'peer-run')
    run('handoff-ask', 'old-run', 'OldAgent')
    emit('progress', 'handoff-ask', {'status': 'waiting_input', 'human_input': 'required', 'decision': 'Request raised before handoff'}, 'old-run')
    run('handoff-ask', 'new-run', 'NewAgent'); emit('progress', 'handoff-ask', calm, 'new-run')
    run('ended-ask', 'solo-run', 'OwnerAgent'); emit('progress', 'ended-ask', calm, 'solo-run')
    run('ended-ask', 'gone-run', 'GoneAgent', claim=False)
    emit('progress', 'ended-ask', {'status': 'waiting_input', 'human_input': 'required', 'decision': 'Request raised before ending'}, 'gone-run')
    emit('progress', 'ended-ask', {'status': 'ended'}, 'gone-run')
    run('fresh-peer', 'stale-owner', 'OwnerAgent'); emit('progress', 'fresh-peer', calm, 'stale-owner', days=9)
    run('fresh-peer', 'busy-peer', 'PeerAgent', claim=False); emit('progress', 'fresh-peer', dict(calm, phase='Parallel execution in progress'), 'busy-peer')
    run('quiet', 'quiet-run', 'OwnerAgent'); emit('progress', 'quiet', calm, 'quiet-run')
    bad = runtime / 'outbox' / 'run-unreadable' / 'bad.json'  # A valid JSON value that is not a queue envelope.
    bad.parent.mkdir(parents=True); bad.write_text('{}', encoding='utf-8')
    config_path = target / 'config.json'
    atomic_json(config_path, config)
    return board.start(SimpleNamespace(config=str(config_path), port=0), config, runtime)


if __name__ == '__main__':
    print(json.dumps(start_fixture(sys.argv[1])))
