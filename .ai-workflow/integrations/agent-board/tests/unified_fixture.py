"""Owned synthetic cases (plan in detail, links, questions) on a random loopback port; no live data."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
import task_board
from model import Store, stamp
from server import atomic_json


def start_fixture(target):
    target = Path(target).resolve()
    (target / '.ai-workflow').mkdir(parents=True)
    (target / '.ai-workflow/WORKFLOW.md').write_text('Owned synthetic unified-detail fixture', encoding='utf-8')
    config = {'project_root': str(target), 'project': 'Test', 'stale_seconds': 86400, 'port': 0, 'display_ttl_seconds': 604800}
    runtime = target / 'runtime'
    store, seqs, ids = Store(runtime, config), {}, {}
    task_board.initialize(store)

    def emit(op, task, data, run=None, days=None):
        seq = None
        if run:
            seq = seqs[run] = seqs.get(run, 0) + 1
        event = board.envelope(op, task, data, run, seq)
        if days is not None:
            event['occurred_at'] = stamp((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
        store.submit(event)
        return event['event_id']

    def run(task, name, agent, role='execution', claim=False):
        emit('run', task, {'role': role, 'agent': agent, 'client': 'synthetic'}, name)
        if claim:
            emit('claim', task, {'expected_version': store.view(task)['version']}, name)

    count = [0]

    def plan(op, data):
        count[0] += 1
        task_board.apply(store, {'event_id': 'plan-task-' + str(count[0]), 'task_id': 'plan-task',
                                 'expected_version': task_board.read(store, 'plan-task')['version'], 'op': op, 'data': data})

    emit('task', 'plan-task', {'title': 'Task with a plan', 'project': 'Test', 'workstream': 'Unified detail'})
    emit('task', 'no-plan-task', {'title': 'Task without a plan', 'project': 'Test', 'workstream': 'Unified detail'})
    plan('create', {'goal': 'Unified-detail sample plan · not live', 'mode': 'plan_execute', 'user_request': 'synthetic opt-in'})
    plan('draft', {'mainlines': [dict(id='m1', goal='Mainline one · in progress', completion='Can be rechecked', owner='Plan text owner', next_step='Mainline one next step'),
                                 dict(id='m2', goal='Mainline two · not started', completion='Boundary checks pass', owner='Plan text owner', next_step='Mainline two next step')]})
    plan('freeze', {})
    plan('progress', {'mainline_id': 'm1', 'fields': {'status': 'running'}})
    plan('add_branch', {'mainline_id': 'm1', 'id': 'b1', 'goal': 'Branch one · <img src=x onerror=alert(1)>', 'next_step': 'Branch one next step'})
    plan('focus', {'mainline_id': 'm1', 'branch_id': 'b1'})

    run('plan-task', 'exec-1', 'ExecAgent', claim=True)
    emit('progress', 'plan-task', {'status': 'running', 'phase': 'Implementing', 'next_step': 'Run self-report, not shown in the overview', 'item': {'mainline': 'm1'}}, 'exec-1')
    ids['q1'] = emit('ask', 'plan-task', {'question': 'Question one: pick A or B?', 'options': ['A', 'B'], 'item': {'mainline': 'm1'}}, 'exec-1')
    ids['q2'] = emit('ask', 'plan-task', {'question': 'Question two: keep the old field?', 'unanswered_policy': 'Keep it'}, 'exec-1')
    emit('resolve', 'plan-task', {'question_id': ids['q1'], 'answer': 'The user picked A'}, 'exec-1')
    emit('withdraw', 'plan-task', {'question_id': 'no-such-question', 'reason': 'Wrong ID'}, 'exec-1')
    run('plan-task', 'audit-1', 'AuditAgent', role='audit')
    emit('progress', 'plan-task', {'status': 'running', 'phase': 'Auditing', 'item': {'mainline': 'm1', 'branch': 'b1'}}, 'audit-1')
    run('plan-task', 'peer-1', 'PeerAgent')
    emit('progress', 'plan-task', {'status': 'waiting_input', 'human_input': 'required', 'decision': 'Declaration: please confirm the scope'}, 'peer-1')
    run('plan-task', 'peer-2', 'PeerAgent')  # Written past the CLI preflight: the read side still marks it.
    emit('progress', 'plan-task', {'status': 'running', 'item': {'mainline': 'm9'}}, 'peer-2')
    run('plan-task', 'peer-3', 'QuietAgent')
    emit('progress', 'plan-task', {'status': 'running', 'phase': 'Stopped long ago'}, 'peer-3', days=3)

    run('no-plan-task', 'exec-np', 'SoloAgent', claim=True)
    emit('progress', 'no-plan-task', {'status': 'running', 'next_step': 'Without a plan the run self-report is used'}, 'exec-np')
    config_path = target / 'config.json'
    atomic_json(config_path, config)
    return dict(board.start(SimpleNamespace(config=str(config_path), port=0), config, runtime), questions=ids)


if __name__ == '__main__':
    print(json.dumps(start_fixture(sys.argv[1])))
