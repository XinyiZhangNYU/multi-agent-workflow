"""Owned UI fixture via the unmodified Store and real HTTP server; never live data."""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Store


def make_fixtures(root):
    root = Path(root)
    anchor = datetime.now(timezone.utc) - timedelta(minutes=12)
    at = lambda minutes=0: (anchor - timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
    config = {'project_root': str(root.resolve()), 'project': 'Demo workspace', 'stale_seconds': 86400}
    stores = {}
    sha_a, sha_b = 'a' * 40, 'b' * 40
    for scene in ('normal', 'stress', 'empty'):
        store = Store(root / scene, config)
        seqs = {}

        def emit(op, task, data, run=None, minutes=0, source=None):
            seq = None
            if run:
                seqs[run] = seqs.get(run, 0) + 1
                seq = seqs[run]
            event = {'event_id': uuid.uuid4().hex, 'task_id': task, 'run_id': run, 'seq': seq,
                     'op': op, 'occurred_at': at(minutes), 'source': source or {'kind': 'agent_report', 'label': 'Synthetic self-report'}, 'data': data}
            store.submit(event)

        def task(key, title, stream, agent='agent-a', status='running', phase='Targeted verification', progress='Inputs and candidate checked.', next_step='Finish the remaining checks and prepare the handoff.', minutes=0, **extra):
            emit('task', key, {'title': title, 'workstream': stream, 'project': 'Demo workspace'})
            if agent:
                run = key + '-execution'
                emit('run', key, {'role': 'execution', 'agent': agent, 'client': 'client-b' if agent == 'agent-b' else 'client-a'}, run)
                emit('claim', key, {'expected_version': 0}, run)
                emit('progress', key, dict(status=status, phase=phase, progress=progress, next_step=next_step,
                     candidate_sha=sha_a, worktree='worktrees/demo-' + key,
                     evidence=['evidence/ui-demo/' + key + '/REPORT.md'], **extra), run, minutes)
                return run

        def audit(key, suffix='review', candidate=sha_a, minutes=0, status='running', **extra):
            run = key + '-' + suffix
            emit('run', key, {'role': 'audit', 'agent': 'reviewer', 'client': 'review-client'}, run)
            emit('progress', key, dict(status=status, phase='Counterexample recheck', progress='Rechecking candidate binding and edge cases.', next_step='Write the report after the remaining counterexamples.', candidate_sha=candidate,
                 audit_id='demo-' + key + '-' + suffix, evidence=['evidence/ui-demo/' + key + '/AUDIT.md'], **extra), run, minutes)
            return run

        if scene != 'empty':
            r = task('public-docs', 'Public docs · History snapshot retention', 'Frontend', agent='agent-b', status='waiting_input', phase='Option awaiting confirmation',
                     progress='Both retention options have isolated samples.', next_step='Update the display contract once retention is confirmed.',
                     human_input='required', decision='Keep history snapshots for 30 or 90 days?', unanswered_policy='Pause the retention branch; other approved pages continue.')
            r = task('doc-export', 'Document export · Consistent page numbering', 'Data export', count={'done': 24, 'total': 31, 'unit': 'targeted checks'},
                     phase='Pagination recheck', progress='Page breaks recomputed for long tables; checking section boundaries.', next_step='Add the 7 missing multi-section export checks.')
            audit('doc-export')
            emit('delivery', 'doc-export', {'stage': 'self_test', 'verdict': 'PASS', 'candidate_sha': sha_a, 'evidence': 'evidence/ui-demo/doc-export/TESTS.md', 'audit_id': None}, r)
            old = audit('doc-export', suffix='previous-review', candidate=sha_b, status='ended')
            emit('delivery', 'doc-export', {'stage': 'independent_audit', 'verdict': 'ACCEPTED', 'candidate_sha': sha_b, 'evidence': 'evidence/ui-demo/doc-export/OLD-AUDIT.md', 'audit_id': 'demo-old-export'}, old)
            task('form-validation', 'Form validation · Rebuild error summaries', 'Data export', status='ended', phase='Implementation round ended',
                 progress='Message ordering fix committed; the audit is on another candidate.', next_step='Check which candidate the audit report covers, then decide on a recheck.')
            audit('form-validation', candidate=sha_b)
            task('release-feed', 'Release feed · Changelog and publish-time seam', 'Backend', status='waiting_dependency', phase='Waiting for candidate freeze',
                 progress='Local regression done; the shared schema contract is not frozen yet.', next_step='Verify the seam once the frozen SHA arrives.', human_input='not_required', blocker='Waiting for the shared schema contract freeze.', dependencies=['doc-export'])
            task('notifications', 'Notification settings · Dedup and digest range', 'Backend', status='waiting_input', phase='Waiting for input',
                 progress='Source filter done; the item to confirm is not written yet.', next_step='The owning session adds the question the user must decide.')
            task('nightly', 'Nightly build · Latest report to check', 'Operations', minutes=3 * 24 * 60, phase='Observation log',
                 progress='Last self-report: checking the nightly output.', next_step='The owning session confirms the current phase; this is not a failure verdict.')
            emit('activity', 'nightly', {'summary': 'The synthetic log shows one completion line; it does not replace task progress.'},
                 source={'kind': 'program_observation', 'label': 'Synthetic log observation', 'path': 'evidence/ui-demo/nightly/activity.log', 'source_at': at(1), 'read_at': at(0)})
            task('audit-only', 'Board protocol · Audit-only task', 'Workflow', agent=None)
            audit('audit-only')
            task('board-layout', 'Board UI · Source disagreement check', 'Workflow', phase='UI recheck',
                 progress='Layout comparison and 8 interaction checks done.', next_step='Check why the document snapshot says ended.', count={'done': 8, 'total': 12, 'unit': 'interaction scenarios'})
            emit('snapshot', 'board-layout', {'fields': {'status': 'ended', 'phase': 'Wrap-up of an old version', 'candidate_sha': sha_b}, 'excerpt': 'A synthetic excerpt from an old document. It does not override the current execution self-report.'},
                 source={'kind': 'document_snapshot', 'label': 'Design handoff snapshot', 'path': 'evidence/ui-demo/board-layout/HANDOFF.md', 'sha256': 'c' * 64, 'source_at': at(-5), 'read_at': at(-6)})
            task('identity', 'Identity mapping · No current run connected', 'Operations', agent=None)
            emit('snapshot', 'identity', {'fields': {'phase': 'In progress per an old document', 'status': 'running'}, 'excerpt': 'Document snapshot only; what the current run is doing is unknown.'},
                 source={'kind': 'document_snapshot', 'label': 'Old phase note', 'path': 'evidence/ui-demo/identity/STATUS.md', 'sha256': 'd' * 64, 'source_at': at(4000), 'read_at': at(0)})
            task('settings-page', 'Settings page · Layout boundaries', 'Frontend', agent='agent-b', status='paused', phase='Stage paused', progress='Current implementation and evidence saved.', next_step='Continue when explicitly authorized later.')
            task('long-text', 'Cross-workstream task: check very long titles, different candidates, and the source times and evidence paths in stage handoffs, so nothing important disappears', 'Frontend',
                 phase='Long-text and accessibility checks', progress='Paths, long titles and source notes must stay readable on narrow screens.', next_step='Check keyboard order and reduced motion.',
                 unanswered_policy='Not agreed', audit_id=None)
            emit('progress', 'long-text', {'evidence': ['evidence/ui-demo/long-text/very-long-folder-name-for-stage-evidence/candidate-recheck-notes-and-explanations/OBSERVATION-WITH-A-LONG-FILENAME.md']}, 'long-text-execution')
            if scene == 'stress':
                for i in range(25):
                    task('stress-' + str(i), ('Many-task scenario · ' + str(i + 1)) + (' · very long display title with handoff notes' * 3 if i % 4 == 0 else ''), ['Frontend', 'Backend', 'Data export', 'Workflow'][i % 4], agent='agent-b' if i % 3 == 0 else 'agent-a', phase='Isolated layout check')
        stores[scene] = store
    return stores, at(), config

if __name__ == '__main__':
    import json
    from types import SimpleNamespace
    import board
    from server import atomic_json
    target = Path(sys.argv[1]).resolve()
    (target / '.ai-workflow').mkdir(parents=True)
    (target / '.ai-workflow/WORKFLOW.md').write_text('Owned synthetic UI test', encoding='utf-8')
    _, _, config = make_fixtures(target)
    config['port'] = 0
    config_path = target / 'config.json'
    atomic_json(config_path, config)
    # Existing synthetic stress database is served by the production HTTP adapter.
    print(json.dumps(board.start(SimpleNamespace(config=str(config_path), port=0), config, target / 'stress')))
