"""Owned synthetic TTL cases served by the real adapter; no live board access."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from workstream_fixture import make_fixtures
import board
from server import atomic_json


def start_fixture(target):
    target = Path(target).resolve()
    (target / '.ai-workflow').mkdir(parents=True)
    (target / '.ai-workflow/WORKFLOW.md').write_text('Owned synthetic TTL fixture', encoding='utf-8')
    stores, _, config = make_fixtures(target)
    store = stores['stress']
    config.update(port=0, display_ttl_seconds=604800)
    at = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def run(task, name, role='execution', days=10, human='not_required', status='running'):
        store.submit(board.envelope('run', task, {'role': role, 'agent': 'TTL-' + role}, name, 1))
        seq = 2
        if role == 'execution':
            store.submit(board.envelope('claim', task, {'expected_version': store.view(task)['version']}, name, seq))
            seq += 1
        if days is not None:
            event = board.envelope('progress', task, {'status': status, 'human_input': human, 'phase': 'TTL synthetic case',
                'decision': 'Please confirm: keep option A or B?' if human == 'required' else None,
                'next_step': 'Keep the record; do no work'}, name, seq)
            event['occurred_at'] = at(days)
            store.submit(event)

    for name, title, stream in (
        ('expired-run', 'Expired execution task', 'Old stream'), ('expired-doc', 'Expired document task', 'Old stream'),
        ('expired-audit', 'Expired audit task', 'Old stream'), ('old-human', 'Expired but needs you', 'To-do lab'),
        ('old-audit-human', 'Ended audit still has a human request', 'To-do lab'), ('unknown-run', 'Execution time unknown', 'Unknown lab'),
        ('unknown-doc', 'Document time unknown', 'Unknown lab'), ('renewed-audit', 'Recent audit reappears', 'Workflow')):
        store.submit(board.envelope('task', name, {'title': title, 'project': 'Test', 'workstream': stream}))
    run('expired-run', 'expired-execution', days=10)
    run('expired-audit', 'expired-review', 'audit', days=15)
    run('old-human', 'old-execution-human', days=20, human='required', status='ended')
    run('old-audit-human', 'old-review-human', 'audit', days=20, human='required', status='ended')
    run('unknown-run', 'unknown-execution', days=None)
    run('renewed-audit', 'old-ended-execution', days=20, status='ended')
    run('renewed-audit', 'new-ended-review', 'audit', days=0, status='ended')
    for task, source_at in (('expired-doc', at(12)), ('unknown-doc', None)):
        store.submit(board.envelope('snapshot', task, {'fields': {'status': 'running'}, 'excerpt': 'Owned fixture'},
            source={'kind': 'document_snapshot', 'label': 'TTL snapshot', 'path': 'evidence/ttl/' + task + '.md',
                    'sha256': 'e' * 64, 'source_at': source_at, 'read_at': at(0)}))
    config_path = target / 'config.json'
    atomic_json(config_path, config)
    return board.start(SimpleNamespace(config=str(config_path), port=0), config, target / 'stress')


if __name__ == '__main__':
    print(json.dumps(start_fixture(sys.argv[1])))
