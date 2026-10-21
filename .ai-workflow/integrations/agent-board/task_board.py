"""Opt-in, two-level execution plans. Never an approval or task/run authority."""
import hashlib
import json

from model import Conflict, Protocol, ident, now, packed, text

OPERATIONS = {'create', 'draft', 'freeze', 'confirm', 'progress', 'focus', 'change',
              'add_branch', 'branch_progress', 'complete_branch', 'cancel_branch',
              'delete_branch', 'close', 'reopen', 'delete'}
LINE_STATES = {'planned', 'running', 'blocked', 'done'}
FIELDS = {'status', 'owner', 'next_step', 'blocker', 'result', 'evidence'}
DEFAULT_PLAN_BYTES = 10 * 1024 * 1024
# Legal reason: 2000 Unicode code points, at most 4 UTF-8 bytes each.
# JSON quote/backslash/newline/tab escapes use <=2 bytes per code point.
# The remaining 1024 bytes cover terminal state keys and values, not new plan work.
CLOSE_RESERVE_BYTES = 2000 * 4 + 1024
DEFAULT_SUMMARY_BYTES = 32768


def limit(config, key, default):
    value = config.get(key, default)
    if type(value) is not int or value < 4096:
        raise ValueError('invalid positive byte limit: ' + key)
    return value


def overview(b):
    """Item completion, not time/acceptance. Original IDs survive explicit changes."""
    lines = b['mainlines']
    states = {s: sum(m['status'] == s for m in lines) for s in LINE_STATES | {'cancelled', 'replaced'}}
    active = sum(states[s] for s in LINE_STATES)
    baseline = b.get('baseline_mainline_ids')
    return dict(states, active=active, incomplete=active-states['done'],
                baseline=len(baseline) if baseline is not None else None,
                baseline_known=baseline is not None,
                added=sum(c.get('action') == 'add' for c in b['changes']),
                replacements=sum(c.get('action') == 'replace' for c in b['changes']),
                has_mainlines=bool(lines), running_ids=[m['id'] for m in lines if m['status'] == 'running'])


def required(value, limit=2000):
    if not text(value, limit) or not value.strip():
        raise ValueError('nonempty bounded text required')
    return value


def keys(data, allowed, required_keys=()):
    if not isinstance(data, dict) or set(data) - set(allowed) or not set(required_keys) <= set(data):
        raise ValueError('invalid task-board operation fields')


def refs(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('bounded unique ID list required')
    result = [ident(v) for v in value]
    if len(set(result)) != len(result):
        raise ValueError('bounded unique ID list required')
    return result


def progress_fields(data, protocol, schedule=False):
    keys(data, FIELDS | ({'dependencies', 'parallel_with'} if schedule else set()))
    if not data:
        raise ValueError('progress fields required')
    for key, value in data.items():
        if key in {'dependencies', 'parallel_with'}:
            refs(value)
        elif key == 'status':
            if value not in LINE_STATES:
                raise ValueError('invalid progress status; cancel/replace require separate operations')
        elif key == 'evidence':
            if not isinstance(value, list) or len(value) > 20:
                raise ValueError('bounded evidence list required')
            for entry in value:
                protocol.path_check(entry)
        else:
            text(value, 2000)


def line(value, protocol):
    keys(value, {'id', 'goal', 'completion', 'owner', 'next_step', 'dependencies', 'parallel_with'},
         {'id', 'goal', 'completion', 'owner', 'next_step'})
    ident(value['id']); required(value['goal']); required(value['completion']); text(value['owner'], 2000)
    text(value['next_step'], 2000)
    result = dict(value, status='planned', blocker=None, result=None, evidence=[], branch_results=[])
    for key in ('dependencies', 'parallel_with'):
        result[key] = refs(value.get(key, []))
        if value['id'] in result[key]:
            raise ValueError('self dependency/parallel link rejected')
    return result


def validate(config, request):
    keys(request, {'event_id', 'task_id', 'expected_version', 'op', 'data'},
         {'event_id', 'task_id', 'expected_version', 'op', 'data'})
    result = json.loads(packed(request))
    ident(result['event_id']); ident(result['task_id'])
    if type(result['expected_version']) is not int or not 0 <= result['expected_version'] < 10**12:
        raise ValueError('nonnegative expected_version required')
    op, d = result['op'], result['data']; protocol = Protocol(config)
    if op not in OPERATIONS:
        raise ValueError('unknown task-board operation')
    if op == 'create':
        keys(d, {'goal', 'mode', 'user_request'}, {'goal', 'mode', 'user_request'})
        required(d['goal']); required(d['user_request'])
        if d['mode'] not in {'plan_execute', 'plan_wait'}:
            raise ValueError('mode must be plan_execute or plan_wait')
    elif op == 'draft':
        keys(d, {'goal', 'mainlines'}, {'mainlines'})
        if 'goal' in d:
            required(d['goal'])
        if not isinstance(d['mainlines'], list) or len(d['mainlines']) > 60:
            raise ValueError('at most 60 mainlines')
        lines = [line(v, protocol) for v in d['mainlines']]
        ids = {v['id'] for v in lines}
        if len(ids) != len(lines) or any(set(v['dependencies'] + v['parallel_with']) - ids for v in lines):
            raise ValueError('duplicate mainline or unknown dependency')
    elif op == 'progress':
        keys(d, {'mainline_id', 'fields'}, {'mainline_id', 'fields'})
        ident(d['mainline_id']); progress_fields(d['fields'], protocol, schedule=True)
    elif op == 'focus':
        keys(d, {'mainline_id', 'branch_id'}, {'mainline_id'})
        if d['mainline_id'] is not None:
            ident(d['mainline_id'])
        if d.get('branch_id') is not None:
            ident(d['branch_id'])
    elif op == 'change':
        keys(d, {'action', 'mainline_id', 'replacement', 'goal', 'reason', 'decision_ref'},
             {'action', 'reason', 'decision_ref'})
        required(d['reason']); required(d['decision_ref'])
        if d['action'] in {'cancel', 'replace'}:
            ident(d.get('mainline_id'))
        if d['action'] in {'add', 'replace'}:
            line(d.get('replacement'), protocol)
        elif d['action'] == 'goal':
            required(d.get('goal'))
        elif d['action'] != 'cancel':
            raise ValueError('invalid explicit change action')
    elif op == 'add_branch':
        keys(d, {'mainline_id', 'id', 'goal', 'next_step', 'owner'}, {'mainline_id', 'id', 'goal', 'next_step'})
        ident(d['mainline_id']); ident(d['id']); required(d['goal']); text(d['next_step']); text(d.get('owner'))
    elif op == 'branch_progress':
        keys(d, {'branch_id', 'fields'}, {'branch_id', 'fields'})
        ident(d['branch_id']); progress_fields(d['fields'], protocol)
        if d['fields'].get('status') == 'done':
            raise ValueError('use complete_branch to adopt result before cleanup')
    elif op in {'complete_branch', 'cancel_branch'}:
        field = 'result' if op == 'complete_branch' else 'reason'
        keys(d, {'branch_id', field, 'evidence'} if op == 'complete_branch' else {'branch_id', field}, {'branch_id', field})
        ident(d['branch_id']); required(d[field])
        if 'evidence' in d:
            progress_fields({'evidence': d['evidence']}, protocol)
    elif op == 'delete_branch':
        keys(d, {'branch_id'}, {'branch_id'}); ident(d['branch_id'])
    elif op in {'confirm', 'reopen', 'delete'}:
        keys(d, {'decision_ref', 'reason'}, {'decision_ref', 'reason'})
        required(d['decision_ref']); required(d['reason'])
    elif op == 'close':
        keys(d, {'reason'}, {'reason'}); required(d['reason'])
    elif op == 'freeze':
        keys(d, set())
    if len(packed(result).encode()) > 65536:
        raise ValueError('task-board request exceeds 64 KiB')
    return result


def initialize(store):
    # Additive schema only. Old event tables/rows and old helpers are untouched.
    with store.connect() as db:
        db.executescript('''
          CREATE TABLE IF NOT EXISTS task_boards (
            task_id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS task_board_receipts (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, digest TEXT NOT NULL, version INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS task_board_receipt_task ON task_board_receipts(task_id);
        ''')


def read(store, task_id):
    ident(task_id)
    with store.connect() as db:
        row = db.execute('SELECT * FROM task_boards WHERE task_id=?', (task_id,)).fetchone()
    return {'task_id': task_id, 'exists': bool(row and row['body']), 'deleted': bool(row and not row['body']),
            'version': row['version'] if row else 0,
            'board': json.loads(row['body']) if row and row['body'] else None,
            'updated_at': row['updated_at'] if row else None}


def plan_rows(store):
    with store.connect() as db:
        rows = db.execute('SELECT b.*,t.title,t.workstream FROM task_boards b JOIN tasks t ON t.id=b.task_id WHERE b.body IS NOT NULL ORDER BY b.updated_at DESC,b.task_id').fetchall()
    return [(r, json.loads(r['body'])) for r in rows]


def listing(store, rows=None):
    result = []
    for r, b in plan_rows(store) if rows is None else rows:
        result.append({'task_id': r['task_id'], 'title': r['title'], 'workstream': r['workstream'],
                       'version': r['version'], 'updated_at': r['updated_at'], 'overview': overview(b),
                       'next_step': (plan_next_step(b, r['updated_at']) or {}).get('text'),
                       **{key: b[key] for key in ('goal', 'state', 'frozen', 'focus')}})
    return result


def plan_next_step(b, at=None):
    """The plan's focus is the task's next step while the plan is in force."""
    focus = b['focus'] if b else {}
    if not b or not b['frozen'] or b['state'] not in {'active', 'awaiting_confirmation'} or not focus.get('mainline_id'):
        return None
    m = next((m for m in b['mainlines'] if m['id'] == focus['mainline_id']), None)
    if m is None:
        return None
    s = next((s for s in b['branches'] if s['id'] == focus.get('branch_id')), None)
    # An open focus branch speaks for the plan; a finished or silent one falls back.
    if s and s['status'] not in {'done', 'cancelled'} and s.get('next_step'):
        return {'text': s['next_step'], 'source': 'plan_focus', 'item': {'mainline': m['id'], 'branch': s['id']}, 'run_id': None, 'at': at}
    return {'text': m['next_step'] or None, 'source': 'plan_focus', 'item': {'mainline': m['id']}, 'run_id': None, 'at': at}


def next_step(task, b=None, at=None):
    """One authoritative next step for every view: plan focus, else the owner run."""
    plan = plan_next_step(b, at)
    if plan:
        return plan
    current = task.get('current')
    if current and current['state'].get('next_step'):
        return {'text': current['state']['next_step'], 'source': 'current_run', 'item': current['state'].get('item'),
                'run_id': current['id'], 'at': current['business_at']}
    return {'text': None, 'source': None, 'item': None, 'run_id': None, 'at': None}


def link_state(b, item):
    """Judge a run's explicit link on read; never guess one."""
    if not item:
        return 'none'
    if not b:
        return 'no_plan'
    m = next((m for m in b['mainlines'] if m['id'] == item['mainline']), None)
    if m is None:
        return 'unknown_item'
    finished = m['status'] in {'done', 'cancelled', 'replaced'}
    if 'branch' in item:
        s = next((s for s in b['branches'] if s['id'] == item['branch']), None)
        if s is None:
            # A deleted branch survives only in its own mainline's results.
            return 'finished_item' if any(x['branch_id'] == item['branch'] for x in m['branch_results']) else 'unknown_item'
        if s['mainline_id'] != m['id']:
            return 'unknown_item'
        finished = finished or s['status'] in {'done', 'cancelled'}
    return 'finished_item' if finished else 'linked'


def focus_summary(value, mainline_id=None, offset=0, budget=DEFAULT_SUMMARY_BYTES):
    # Page, network summary and explicit cached recovery all derive from one body.
    b = value['board']
    if not b:
        return value
    if mainline_id is not None and mainline_id not in {m['id'] for m in b['mainlines']}:
        raise ValueError('mainline not found')
    lines = [m for m in b['mainlines'] if mainline_id is None or m['id'] == mainline_id]
    related = mainline_id or b['focus']['mainline_id']
    result = {k: v for k, v in value.items() if k != 'board'} | {'summary': {
        'goal': b['goal'], 'state': b['state'], 'frozen': b['frozen'], 'mode': b['mode'], 'focus': b['focus'],
        'overview': overview(b), 'mainlines': [], 'branches': [],
        'updated_at': value['updated_at']}, 'page': {'offset': offset, 'next_offset': None, 'total': 0}}
    items = [('mainline', {k: m[k] for k in ('id', 'goal', 'completion', 'owner', 'status', 'next_step', 'blocker', 'dependencies', 'parallel_with', 'result', 'evidence')}
              | {'history_count': len(m['branch_results'])}
              | ({'change_reason': m['change_reason']} if 'change_reason' in m else {})) for m in lines]
    items += [('branch', s) for s in b['branches'] if s['mainline_id'] == related and s['status'] not in {'done', 'cancelled'}]
    result['page']['total'] = len(items)
    if not 0 <= offset <= len(items):
        raise ValueError('invalid summary offset')
    for i, (kind, item) in enumerate(items[offset:], offset):
        target = result['summary']['mainlines' if kind == 'mainline' else 'branches']
        target.append(item)
        result['page']['next_offset'] = i + 1 if i + 1 < len(items) else None
        # Reserve response identity/cache metadata, never trim a field or item.
        if len(packed(result).encode()) + 1024 > budget:
            target.pop(); result['page']['next_offset'] = i
            if i == offset:
                raise ValueError('summary item exceeds byte budget; use explicit full show or increase summary budget')
            break
    return result


def history(store, task_id, mainline_id=None, offset=0, budget=DEFAULT_SUMMARY_BYTES):
    return history_value(read(store, task_id), mainline_id, offset, budget)


def history_value(value, mainline_id=None, offset=0, budget=DEFAULT_SUMMARY_BYTES):
    """Paginate exactly the already-validated snapshot, never reread a newer plan."""
    b = value['board']
    if not b:
        raise ValueError('task-board missing')
    items = find(b['mainlines'], mainline_id)['branch_results'] if mainline_id else b['changes']
    result = {k: v for k, v in value.items() if k != 'board'} | {'history': [], 'mainline_id': mainline_id,
        'page': {'offset': offset, 'next_offset': None, 'total': len(items)}}
    if not 0 <= offset <= len(items):
        raise ValueError('invalid history offset')
    for i, item in enumerate(items[offset:], offset):
        result['history'].append(item); result['page']['next_offset'] = i+1 if i+1 < len(items) else None
        if len(packed(result).encode()) + 1024 > budget:
            result['history'].pop(); result['page']['next_offset'] = i
            if i == offset:
                raise ValueError('history item exceeds byte budget; explicit full show required')
            break
    return result


def attach_overviews(store, value):
    """Plan overview, the single next step and run links for each task view.

    The list keeps only counts; the detail adds runs grouped by item and the
    unassigned runs. Plan progress never derives from links.
    """
    rows = plan_rows(store)
    entries = {e['task_id']: e for e in listing(store, rows)}
    bodies = {r['task_id']: (b, r['updated_at']) for r, b in rows}
    detail = 'id' in value
    for task in value.get('tasks', [value] if detail else []):
        b, at = bodies.get(task['id'], (None, None))
        task['task_board'] = entries.get(task['id'])
        task['next_step'] = next_step(task, b, at)
        for q in task.get('open_questions', []):
            q['link_state'] = link_state(b, q['item'])
        items = task.get('run_items', []) if detail else task.pop('run_items', [])
        for r in items:
            r['link_state'] = link_state(b, r['item'])
        unassigned = [r for r in items if r['status'] != 'ended' and r['link_state'] in {'none', 'unknown_item'}]
        task['unassigned_run_count'] = len(unassigned) if b else None
        if detail:
            grouped = {}
            for r in items:
                if r['link_state'] in {'linked', 'finished_item'}:
                    key = r['item']['mainline'] + ('/' + r['item']['branch'] if 'branch' in r['item'] else '')
                    grouped.setdefault(key, []).append(r)
            task['item_runs'] = grouped if b else None
            # Recently active first, then stale; newest first within each group.
            task['unassigned_runs'] = sorted(sorted(unassigned, key=lambda r: r['business_at'] or '', reverse=True),
                                             key=lambda r: r['stale']) if b else None
    return value


def find(entries, key):
    for entry in entries:
        if entry['id'] == key:
            return entry
    raise ValueError('plan item not found')


def apply(store, request):
    request = validate(store.config, request)
    eid, tid, op, d = (request[k] for k in ('event_id', 'task_id', 'op', 'data'))
    digest = hashlib.sha256(packed(request).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = db.execute('SELECT * FROM task_board_receipts WHERE id=?', (eid,)).fetchone()
        if receipt:
            if receipt['digest'] != digest:
                raise Conflict('task-board event_id reused with different content')
            return {'event_id': eid, 'digest': digest, 'recorded': True, 'duplicate': True, 'version': receipt['version']}
        row = db.execute('SELECT * FROM task_boards WHERE task_id=?', (tid,)).fetchone()
        version = row['version'] if row else 0
        if version != request['expected_version']:
            raise Conflict(f'task-board version conflict: expected {request["expected_version"]}, actual {version}; read before deciding')
        if not db.execute('SELECT 1 FROM tasks WHERE id=?', (tid,)).fetchone():
            raise Conflict('registered task missing; task-board never auto-creates progress tasks')
        # Close/delete reclaim control reserve; never clear other tasks' receipts.
        reclaim = op in {'close', 'delete'}
        if not reclaim and db.execute('SELECT count(*) FROM task_board_receipts').fetchone()[0] >= store.config.get('max_task_board_receipts', store.config.get('max_events', 100000)):
            raise ValueError('task-board receipt capacity reached')
        if not reclaim and sum(p.stat().st_size for p in store.runtime.glob('board.sqlite3*')) > store.config.get('max_database_bytes', 268435456):
            raise ValueError('database capacity reached')
        b = json.loads(row['body']) if row and row['body'] else None
        if op == 'create':
            if b:
                raise Conflict('task-board exists; resume it instead of replanning')
            b = {'task_id': tid, 'goal': d['goal'], 'mode': d['mode'], 'user_request': d['user_request'],
                 'state': 'draft', 'frozen': False, 'mainlines': [], 'branches': [], 'changes': [],
                 'focus': {'mainline_id': None, 'branch_id': None}}
        else:
            if b is None:
                raise Conflict('no task-board; explicit create required')
            if b['state'] == 'closed' and op not in {'reopen', 'delete'}:
                raise Conflict('task-board closed; explicit reopen required')
            if op == 'draft':
                if b['frozen']:
                    raise Conflict('frozen mainlines cannot be replaced by draft/bulk writes')
                b['mainlines'] = [line(v, Protocol(store.config)) for v in d['mainlines']]
                b['goal'] = d.get('goal', b['goal']); b['focus'] = {'mainline_id': None, 'branch_id': None}
            elif op == 'freeze':
                if b['frozen'] or not b['mainlines']:
                    raise Conflict('freeze requires nonempty draft')
                b['frozen'] = True; b['state'] = 'active' if b['mode'] == 'plan_execute' else 'awaiting_confirmation'
                b['baseline_mainline_ids'] = [m['id'] for m in b['mainlines']]
                b['focus']['mainline_id'] = b['mainlines'][0]['id']
            elif op == 'confirm':
                if b['state'] != 'awaiting_confirmation':
                    raise Conflict('confirmation applies only to frozen plan_wait')
                b['state'] = 'active'; b['changes'].append({'op': op, **d})
            elif op in {'progress', 'add_branch', 'branch_progress', 'complete_branch', 'cancel_branch', 'delete_branch', 'focus', 'change'}:
                if not b['frozen'] or b['state'] not in {'active', 'awaiting_confirmation'}:
                    raise Conflict('operation requires frozen plan; not execution authorization')
                if b['state'] == 'awaiting_confirmation' and (op == 'complete_branch' or
                        (op in {'progress', 'branch_progress'} and d['fields'].get('status', 'planned') != 'planned')):
                    raise Conflict('plan_wait remains awaiting confirmation; planning edits do not authorize execution')
                if op == 'progress':
                    m = find(b['mainlines'], d['mainline_id'])
                    if m['status'] in {'cancelled', 'replaced'}:
                        raise Conflict('original cancelled/replaced mainline retained and not editable')
                    for field in ('dependencies', 'parallel_with'):
                        if field in d['fields'] and (m['id'] in d['fields'][field] or set(d['fields'][field]) - {v['id'] for v in b['mainlines']}):
                            raise ValueError('invalid updated execution links')
                    m.update(d['fields'])
                    if m['status'] == 'done' and not m['result']:
                        raise ValueError('done mainline requires explicit result, not branch/run counts')
                elif op == 'focus':
                    if d['mainline_id'] is not None:
                        find(b['mainlines'], d['mainline_id'])
                    sid = d.get('branch_id')
                    if sid is not None and find(b['branches'], sid)['mainline_id'] != d['mainline_id']:
                        raise ValueError('focus branch belongs to another mainline')
                    b['focus'] = {'mainline_id': d['mainline_id'], 'branch_id': sid}
                elif op == 'change':
                    action = d['action']
                    if action in {'replace', 'cancel'}:
                        m = find(b['mainlines'], d['mainline_id'])
                        if m['status'] in {'cancelled', 'replaced'}:
                            raise Conflict('original change record retained and not editable')
                        m['status'] = 'replaced' if action == 'replace' else 'cancelled'
                        m['change_reason'] = d['reason']
                    if action in {'replace', 'add'}:
                        m = line(d['replacement'], Protocol(store.config))
                        if any(v['id'] == m['id'] for v in b['mainlines']):
                            raise Conflict('replacement must have new stable ID; original remains')
                        if set(m['dependencies'] + m['parallel_with']) - {v['id'] for v in b['mainlines']}:
                            raise ValueError('unknown dependency')
                        b['mainlines'].append(m)
                    if action == 'goal':
                        b['changes'].append({'old_goal': b['goal'], **d}); b['goal'] = d['goal']
                    else:
                        b['changes'].append(dict(d))
                elif op == 'add_branch':
                    m = find(b['mainlines'], d['mainline_id'])
                    used_ids = {s['id'] for s in b['branches']} | {s['branch_id'] for v in b['mainlines'] for s in v['branch_results']}
                    if m['status'] in {'cancelled', 'replaced', 'done'} or d['id'] in used_ids:
                        raise Conflict('branch requires open parent and unique ID')
                    b['branches'].append(dict(d, owner=d.get('owner'), status='planned', blocker=None, result=None, evidence=[]))
                else:
                    s = find(b['branches'], d['branch_id'])
                    if op == 'branch_progress':
                        if s['status'] in {'done', 'cancelled'}:
                            raise Conflict('branch finished; retained result cannot be overwritten')
                        s.update(d['fields'])
                    elif op == 'complete_branch':
                        if s['status'] in {'done', 'cancelled'}:
                            raise Conflict('branch already finished')
                        s.update(status='done', result=d['result'], evidence=d.get('evidence', []))
                        find(b['mainlines'], s['mainline_id'])['branch_results'].append({'branch_id': s['id'], 'result': s['result'], 'evidence': s['evidence']})
                    elif op == 'cancel_branch':
                        if s['status'] in {'done', 'cancelled'}:
                            raise Conflict('branch already finished')
                        s.update(status='cancelled', result=d['reason'])
                        find(b['mainlines'], s['mainline_id'])['branch_results'].append({'branch_id': s['id'], 'cancelled': True, 'result': s['result'], 'evidence': []})
                    elif op == 'delete_branch':
                        if s['status'] not in {'done', 'cancelled'}:
                            raise Conflict('unfinished branch cannot disappear; complete or explicitly cancel with reason')
                        b['branches'].remove(s)
                    if op in {'complete_branch', 'cancel_branch', 'delete_branch'} and b['focus']['branch_id'] == s['id']:
                        b['focus'] = {'mainline_id': s['mainline_id'], 'branch_id': None}
            elif op == 'close':
                b['before_close'] = b['state']; b['state'] = 'closed'; b['close_reason'] = d['reason']
            elif op == 'reopen':
                if b['state'] != 'closed':
                    raise Conflict('not closed')
                b['state'] = b.pop('before_close'); b['changes'].append({'op': op, **d})
            elif op == 'delete':
                if b['state'] != 'closed':
                    raise Conflict('close task-board before explicit deletion')
                b = None
                # Delete plan contents/old receipts, retain tiny retry tombstone.
                db.execute('DELETE FROM task_board_receipts WHERE task_id=?', (tid,))
        max_bytes = limit(store.config, 'max_task_board_bytes', DEFAULT_PLAN_BYTES)
        if b and (len(b['mainlines']) > 60 or len(b['branches']) > 200 or len(b['changes']) > 100 or
                  len(packed(b).encode()) > max_bytes + (CLOSE_RESERVE_BYTES if op == 'close' else 0)):
            raise ValueError('task-board capacity reached; nothing saved')
        version += 1
        db.execute('INSERT INTO task_boards VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET version=excluded.version,body=excluded.body,updated_at=excluded.updated_at',
                   (tid, version, packed(b) if b else None, now()))
        db.execute('INSERT INTO task_board_receipts VALUES(?,?,?,?)', (eid, tid, digest, version))
        return {'event_id': eid, 'digest': digest, 'recorded': True, 'duplicate': False, 'version': version}
