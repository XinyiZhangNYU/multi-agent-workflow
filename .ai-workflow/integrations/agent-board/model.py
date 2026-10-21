"""Local observation store. Labels and reports never grant authority."""
import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATES = {'running', 'waiting_input', 'waiting_dependency', 'paused', 'ended', 'unknown'}
HUMAN_INPUT = {'required', 'not_required', 'unknown'}
KINDS = {'agent_report', 'document_snapshot', 'program_observation', 'user_web'}
OPS = {'task', 'ensure_task', 'task_meta', 'run', 'claim', 'progress', 'delivery', 'snapshot', 'activity',
       'ask', 'resolve', 'withdraw', 'user_close'}
# Run-scoped events carry run_id + seq; questions and their closures are run events too.
RUN_OPS = {'run', 'claim', 'progress', 'delivery', 'ask', 'resolve', 'withdraw'}
STAGES = {'implementation', 'self_test', 'independent_audit', 'deployment', 'post_deploy'}
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$')
SHA = re.compile(r'^[a-fA-F0-9]{40}$')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must include timezone')
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return d.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def ident(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('invalid stable identifier')
    return value


def text(value, limit=2000):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('invalid or oversized text')
    return value


def item_ref(value):
    """The plan item a run works on (or reviews); None unlinks. Existence is judged on read."""
    if value is None:
        return None
    if not isinstance(value, dict) or 'mainline' not in value or set(value) - {'mainline', 'branch'}:
        raise ValueError('item requires mainline and optional branch')
    return {k: ident(value[k]) for k in ('mainline', 'branch') if k in value}


def question_data(op, d):
    """An ask is identified by its own event_id; a closure names it or a declaring run."""
    if op == 'ask':
        if set(d) - {'question', 'options', 'item', 'unanswered_policy'} or not text(d.get('question')) or not d['question'].strip():
            raise ValueError('ask requires a question')
        options = d.get('options', [])
        if not isinstance(options, list) or len(options) > 8 or any(not text(o, 200) or not o.strip() for o in options):
            raise ValueError('ask allows at most 8 nonempty options of 200 characters')
        text(d.get('unanswered_policy'))
        return dict(d, item=item_ref(d['item'])) if 'item' in d else d
    field = 'answer' if op == 'resolve' else 'reason'
    if set(d) - {'question_id', 'target_run', field} or ('question_id' in d) == ('target_run' in d):
        raise ValueError(op + ' requires exactly one of question_id/target_run plus ' + field)
    ident(d.get('question_id', d.get('target_run')))
    if not text(d.get(field)) or not d[field].strip():
        raise ValueError(op + ' requires ' + field)
    return d


def user_close_data(d):
    """The user closing one open request from the local page: no run, no answer text needed."""
    if not isinstance(d, dict) or set(d) - {'question_id', 'target_run', 'note'} or ('question_id' in d) == ('target_run' in d):
        raise ValueError('user_close requires exactly one of question_id/target_run')
    ident(d.get('question_id', d.get('target_run')))
    text(d.get('note'), 400)
    return d


def question_view(db, task_id, runs, current_run, owners, checked_at, stale_after):
    """Open requests of one task: asks not closed by ID, plus human_input declarations.

    Independent of arrival order across runs: the earliest closure of a question
    wins and later ones are kept as duplicates; a closure naming nothing in this
    task is listed as unmatched. A human_input=required declaration closes
    when its own run reports otherwise, or when a target_run closure arrives after
    it; a later declaration reopens it. The user may close either kind from the
    local page (user_close, no run). Who asked, ownership, ending and TTL never
    close anything.
    """
    by_id = {r['id']: r for r in runs}

    def who(run_id):
        r = by_id[run_id]
        at = r['business_at']
        return {'run_id': run_id, 'agent': r['agent'], 'client': r['client'], 'role': r['role'],
                'run_ended': r['state'].get('status') == 'ended',
                'former_owner': run_id in owners and run_id != current_run, 'run_business_at': at,
                'run_stale': not at or (checked_at - datetime.fromisoformat(at.replace('Z', '+00:00'))).total_seconds() > stale_after}

    def entry(run_id, kind, **fields):
        return dict(who(run_id), kind=kind, **fields, closed_by=None, duplicates=[])

    declared = {}
    for row in db.execute('''SELECT ordinal,run_id,occurred_at,json_extract(body,'$.data.human_input') AS value,
            json_extract(body,'$.data.decision') AS decision FROM events WHERE task_id=? AND op='progress'
            AND reason!='late_sequence' AND json_extract(body,'$.data.human_input') IS NOT NULL ORDER BY ordinal''', (task_id,)):
        declared.setdefault(row['run_id'], []).append(dict(row))
    asks, closures = {}, []
    for row in db.execute('''SELECT ordinal,id,run_id,op,occurred_at,received_at,body FROM events
            WHERE task_id=? AND op IN ('ask','resolve','withdraw','user_close') ORDER BY ordinal''', (task_id,)):
        d = json.loads(row['body'])['data']
        if row['op'] == 'ask':
            asks[row['id']] = entry(row['run_id'], 'ask', question_id=row['id'], question=d['question'],
                                    options=d.get('options', []), item=d.get('item'),
                                    unanswered_policy=d.get('unanswered_policy'), asked_at=row['occurred_at'])
        else:
            closures.append({'op': row['op'], 'event_id': row['id'], 'ordinal': row['ordinal'], 'run_id': row['run_id'],
                             'question_id': d.get('question_id'), 'target_run': d.get('target_run'),
                             'text': d.get('answer', d.get('reason', d.get('note'))), 'at': row['occurred_at'], 'received_at': row['received_at']})
    declared_questions, unmatched = {}, []
    for c in closures:
        q, why = None, 'unknown_question'
        if c['question_id'] is not None:
            q = asks.get(c['question_id'])
        elif c['target_run'] not in by_id:
            why = 'other_task_run'
        else:
            # The declaration in force when this closure arrived.
            prior = [x for x in declared.get(c['target_run'], []) if x['ordinal'] < c['ordinal']]
            why = 'no_open_declaration'
            if prior and prior[-1]['value'] == 'required':
                d = prior[-1]
                q = declared_questions.setdefault((c['target_run'], d['ordinal']), entry(
                    c['target_run'], 'declaration', question_id=None, question=d['decision'], options=[], item=None,
                    unanswered_policy=None, asked_at=d['occurred_at']))
        if q is None:
            unmatched.append(dict(c, why=why))
        elif q['closed_by'] is None:
            q['closed_by'] = c
        else:
            q['duplicates'].append(c)
    open_list = [q for q in asks.values() if q['closed_by'] is None]
    for r in runs:
        if r['state'].get('human_input') != 'required':
            continue
        marks = declared.get(r['id']) or [{'ordinal': 0, 'occurred_at': None}]
        current = declared_questions.get((r['id'], marks[-1]['ordinal']))
        if current is None:
            current = entry(r['id'], 'declaration', question_id=None, asked_at=marks[-1]['occurred_at'])
            open_list.append(current)
        # The declaration in force is described by the run's current state.
        current.update(question=r['state'].get('decision'), options=[], item=r['state'].get('item'),
                       unanswered_policy=r['state'].get('unanswered_policy'))
    open_list.sort(key=lambda q: (q['asked_at'] or '', q['run_id']))
    closed = sorted((q for q in [*asks.values(), *declared_questions.values()] if q['closed_by']),
                    key=lambda q: q['closed_by']['ordinal'], reverse=True)
    return ({'open': open_list, 'closed': closed[:50], 'closed_count': len(closed),
             'unmatched': unmatched[::-1][:50], 'unmatched_count': len(unmatched)},
            {q['run_id'] for q in open_list})


def squeeze_name(value):
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def related_names(a, b):
    """Loose overlap for a hint only: containment, a shared ASCII word or CJK pair."""
    a, b = squeeze_name(a), squeeze_name(b)
    if a in b or b in a:
        return True
    words = lambda v: set(re.findall(r'[a-z0-9]{2,}', v))
    pairs = lambda v: {v[i:i + 2] for i in range(len(v) - 1) if all('\u4e00' <= c <= '\u9fff' for c in v[i:i + 2])}
    return bool(words(a) & words(b) or pairs(a) & pairs(b))


def workstream_choice(value, active):
    """Pick the board spelling for a new/retagged task; never refuses a name.

    `active` is the board list currently shown (derived, not configured). A
    spacing/case/width variant reuses the existing spelling; anything else is a
    new board, returned with the nearby names so the caller can see it.
    """
    names = [b['name'] for b in active]
    if value in names:
        return value, None
    for name in names:
        if squeeze_name(name) == squeeze_name(value):
            return name, 'Workstream "' + value + '" filed under the existing spelling "' + name + '"'
    near = [n for n in names if related_names(n, value)]
    return value, ('New workstream "' + value + '"' + ('; similar existing workstreams: ' + ', '.join(near) if near else '')
                   + '. If it belongs to an existing workstream, correct it with retag after approval')


def display_visibility(current, runs, visible, activity, snapshots, ttl, checked_at, peers=(), held=None):
    """Read-time presentation only: never expire records, ownership or requests.

    Replaced execution runs cannot renew the task; execution runs that never
    owned it (peers) can, like audits, whose clocks remain eligible after
    ending. An open request (held; else a required declaration in visible)
    protects the task regardless.
    Source receipts/read clocks are never business clocks.
    Snapshots are a fallback for tasks with no known progress/observed activity.
    Unknown clocks on current/unfinished runs conservatively prevent hiding.
    """
    eligible = ([current] if current else []) + [r for r in runs if r['role'] == 'audit'] + list(peers)
    times = [r['business_at'] for r in eligible if r.get('business_at')]
    unknown = any(not r.get('business_at') for r in visible)
    if activity:
        times.append(activity['source']['source_at'])
    if not times:
        times = [s['source']['source_at'] for s in snapshots if s['source'].get('source_at')]
        unknown = unknown or any(not s['source'].get('source_at') for s in snapshots)
    latest = max(times) if times else None
    unknown = unknown or latest is None
    required = held if held is not None else any(r['state'].get('human_input') == 'required' for r in visible)
    age = (checked_at - datetime.fromisoformat(latest.replace('Z', '+00:00'))).total_seconds() if latest else None
    return {'display_updated_at': latest, 'display_time_unknown': unknown,
            'display_expired': bool(not unknown and not required and age >= ttl)}


class Conflict(Exception):
    pass


class NotFound(ValueError):
    """A well-formed read for a record that does not exist (HTTP 404)."""


class Protocol:
    def __init__(self, config):
        self.root = Path(config['project_root']).resolve()

    def validate(self, event):
        # Pure normalization: used BEFORE any offline filesystem side effect.
        if not isinstance(event, dict) or set(event) - {'event_id', 'task_id', 'run_id', 'seq', 'op', 'occurred_at', 'source', 'data'}:
            raise ValueError('invalid event envelope')
        b = json.loads(packed(event))
        ident(b.get('event_id')); ident(b.get('task_id'))
        op = b.get('op')
        if op not in OPS:
            raise ValueError('invalid operation')
        b['occurred_at'] = stamp(b.get('occurred_at'))
        if datetime.fromisoformat(b['occurred_at'].replace('Z', '+00:00')).timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise ValueError('future event clock beyond 5 minutes')
        b['source'] = self.source(b.get('source'))
        if op in RUN_OPS:
            ident(b.get('run_id'))
            if type(b.get('seq')) is not int or not 1 <= b['seq'] <= 10**12:
                raise ValueError('positive run sequence required')
        elif b.get('run_id') is not None or b.get('seq') is not None:
            raise ValueError('unexpected run sequence')
        b.setdefault('run_id', None); b.setdefault('seq', None)
        d = b.get('data')
        if not isinstance(d, dict):
            raise ValueError('data must be object')
        if op in {'task', 'ensure_task', 'task_meta'} | RUN_OPS and b['source']['kind'] != 'agent_report':
            raise ValueError('must be labelled agent_report')
        if op in {'task', 'ensure_task'}:
            if set(d) != {'title', 'project', 'workstream'} or any(not text(v, 180) for v in d.values()):
                raise ValueError('invalid task metadata')
        elif op == 'task_meta':
            if set(d) - {'title', 'workstream', 'reason'} or not text(d.get('reason'), 400) or not ({'title', 'workstream'} & set(d)) \
                    or any(not text(d[k], 180) for k in ('title', 'workstream') if k in d):
                raise ValueError('task_meta requires title and/or workstream plus a reason')
        elif op == 'run':
            if set(d) - {'role', 'agent', 'client'} or d.get('role') not in {'execution', 'audit'}:
                raise ValueError('invalid run role/fields')
            text(d.get('agent'), 100); text(d.get('client'), 100)
        elif op == 'claim':
            if set(d) != {'expected_version'} or type(d['expected_version']) is not int or d['expected_version'] < 0:
                raise ValueError('invalid expected_version')
        elif op == 'progress':
            b['data'] = self.fields(d)
        elif op in {'ask', 'resolve', 'withdraw'}:
            b['data'] = question_data(op, d)
        elif op == 'user_close':
            if b['source']['kind'] != 'user_web':
                raise ValueError('user_close must be labelled user_web')
            b['data'] = user_close_data(d)
        elif op == 'delivery':
            if set(d) != {'stage', 'verdict', 'candidate_sha', 'evidence', 'audit_id'} or d['stage'] not in STAGES:
                raise ValueError('invalid delivery fields')
            if not isinstance(d['candidate_sha'], str) or not SHA.fullmatch(d['candidate_sha']):
                raise ValueError('delivery requires full candidate SHA')
            if not text(d['verdict'], 180):
                raise ValueError('verdict required')
            text(d['audit_id'], 100)
            if d['stage'] == 'independent_audit' and not d['audit_id']:
                raise ValueError('audit_id required')
            d['evidence'] = self.path_check(d['evidence'])
            d['candidate_sha'] = d['candidate_sha'].lower()
        elif op == 'snapshot':
            s = b['source']
            if s['kind'] != 'document_snapshot' or not s.get('sha256') or not s.get('read_at') or set(d) != {'fields', 'excerpt'}:
                raise ValueError('invalid document snapshot')
            d['fields'] = self.fields(d['fields']); text(d['excerpt'], 8000)
        elif op == 'activity':
            if b['source']['kind'] != 'program_observation' or not b['source'].get('source_at') or set(d) != {'summary'}:
                raise ValueError('invalid observation')
            text(d['summary'])
        if len(packed(b).encode('utf-8')) > 65536:
            raise ValueError('event exceeds 64 KiB')
        return b

    def path_check(self, value):
        return Store.path_check(self, value)

    def source(self, value):
        return Store.source(self, value)

    def fields(self, value):
        return Store.fields(self, value)


class Store(Protocol):
    def __init__(self, runtime, config):
        self.display_ttl_seconds = config.get('display_ttl_seconds', 7 * 86400)
        if type(self.display_ttl_seconds) is not int or self.display_ttl_seconds <= 0:
            raise ValueError('display_ttl_seconds must be a positive integer')
        self.display_time_zone = config.get('display_time_zone', 'UTC')
        if type(self.display_time_zone) is not str or not re.fullmatch(r'[A-Za-z0-9_+/-]{1,64}', self.display_time_zone):
            raise ValueError('display_time_zone must be an IANA time zone name such as UTC')
        self.runtime = Path(runtime)
        self.root = Path(config['project_root']).resolve()
        self.config = config
        self.path = self.runtime / 'board.sqlite3'
        self.runtime.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, project TEXT NOT NULL, workstream TEXT NOT NULL,
                  current_run TEXT, version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, role TEXT NOT NULL, agent TEXT, client TEXT,
                  last_seq INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, business_at TEXT);
                CREATE TABLE IF NOT EXISTS events (
                  ordinal INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
                  task_id TEXT NOT NULL, run_id TEXT, seq INTEGER, op TEXT NOT NULL,
                  received_at TEXT NOT NULL, occurred_at TEXT NOT NULL, body TEXT NOT NULL,
                  applied INTEGER NOT NULL, reason TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS run_sequence ON events(run_id,seq) WHERE run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS event_task ON events(task_id,ordinal);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=3000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def path_check(self, value):
        if not text(value, 1000):
            raise ValueError('path required')
        p = Path(value)
        p = (self.root / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.is_relative_to(self.root) or p == self.root:
            raise ValueError('evidence path outside allowed project')
        parts = [x.lower() for x in p.relative_to(self.root).parts]
        if any(x in {'.git', '.ssh', 'secrets', 'private', '.codex', '.env'} or
               x.startswith('.env.') or 'token' in x or 'credential' in x for x in parts):
            raise ValueError('sensitive evidence path excluded')
        return str(p)

    def source(self, s):
        if not isinstance(s, dict) or set(s) - {'kind', 'path', 'source_at', 'read_at', 'sha256', 'label'}:
            raise ValueError('invalid source')
        if s.get('kind') not in KINDS:
            raise ValueError('invalid source kind')
        s = dict(s)
        s['label'] = text(s.get('label'), 180)
        for field in ('source_at', 'read_at'):
            s[field] = stamp(s[field]) if s.get(field) else None
            if (s[field]
                    and datetime.fromisoformat(s[field].replace('Z', '+00:00')) > datetime.now(timezone.utc) + timedelta(minutes=5)):
                raise ValueError(f'future {field} clock beyond 5 minutes')
        if (s['source_at'] and s['read_at']
                and datetime.fromisoformat(s['source_at'].replace('Z', '+00:00')) > datetime.fromisoformat(s['read_at'].replace('Z', '+00:00')) + timedelta(minutes=5)):
            raise ValueError('source time is later than read time')
        if s.get('path'):
            s['path'] = self.path_check(s['path'])
        if s.get('sha256') and not re.fullmatch('[a-f0-9]{64}', s['sha256']):
            raise ValueError('invalid evidence hash')
        if s['kind'] in {'document_snapshot', 'program_observation'} and not s.get('path'):
            raise ValueError('snapshot/observation requires source path')
        return s

    def fields(self, data):
        if not isinstance(data, dict):
            raise ValueError('fields must be object')
        allowed = {'status', 'phase', 'progress', 'next_step', 'blocker', 'decision', 'unanswered_policy',
                   'dependencies', 'candidate_sha', 'audit_id', 'worktree', 'evidence', 'count', 'human_input', 'item'}
        if set(data) - allowed:
            raise ValueError('unknown progress fields')
        result = {}
        for key, value in data.items():
            if key == 'status':
                if value not in STATES:
                    raise ValueError('invalid run status')
            elif key == 'human_input':
                if not isinstance(value, str) or value not in HUMAN_INPUT:
                    raise ValueError('human_input requires required/not_required/unknown')
            elif key == 'candidate_sha':
                if value is not None and (not isinstance(value, str) or not SHA.fullmatch(value)):
                    raise ValueError('candidate SHA must be full 40 hex')
                value = value.lower() if value else None
            elif key in {'evidence', 'dependencies'}:
                if not isinstance(value, list) or len(value) > 12:
                    raise ValueError('list limit 12')
                value = [self.path_check(x) if key == 'evidence' else ident(x) for x in value]
            elif key == 'worktree' and value:
                value = self.path_check(value)
            elif key == 'item':
                value = item_ref(value)
            elif key == 'count' and value is not None:
                if not isinstance(value, dict) or set(value) != {'done', 'total', 'unit'}:
                    raise ValueError('count requires done/total/unit')
                if any(type(value[k]) is not int for k in ('done', 'total')) or not 0 <= value['done'] <= value['total'] <= 10**12:
                    raise ValueError('invalid count')
                text(value['unit'], 32)
            else:
                value = text(value)
            result[key] = value
        return result

    def submit(self, event, user=False):
        event = self.validate(event)
        # Only the local page's own endpoint speaks for the user; agent/CLI
        # submissions can never carry a user closure.
        speaks_for_user = event['op'] == 'user_close' or event['source']['kind'] == 'user_web'
        if speaks_for_user != bool(user) or (user and not (event['op'] == 'user_close' and event['source']['kind'] == 'user_web')):
            raise ValueError('user closures come only from the local page')
        if not isinstance(event, dict) or set(event) - {'event_id', 'task_id', 'run_id', 'seq', 'op', 'occurred_at', 'source', 'data'}:
            raise ValueError('invalid event envelope')
        ident(event.get('event_id')); ident(event.get('task_id'))
        op = event.get('op')
        if op not in OPS:
            raise ValueError('invalid operation')
        occurred = stamp(event.get('occurred_at'))
        if datetime.fromisoformat(occurred.replace('Z', '+00:00')).timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise ValueError('future event clock beyond 5 minutes')
        src = self.source(event.get('source'))
        data = event.get('data', {})
        if not isinstance(data, dict):
            raise ValueError('data must be object')
        run_id = event.get('run_id')
        seq = event.get('seq')
        if op in RUN_OPS:
            ident(run_id)
            if type(seq) is not int or not 1 <= seq <= 10**12:
                raise ValueError('positive run sequence required')
        elif run_id is not None or seq is not None:
            raise ValueError('this event must not impersonate run sequence')
        if op in {'task', 'ensure_task', 'task_meta'} | RUN_OPS and src['kind'] != 'agent_report':
            raise ValueError('run/task/delivery updates must be labelled agent_report')
        body = dict(event, occurred_at=occurred, source=src)
        digest = hashlib.sha256(packed(body).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest,applied,reason FROM events WHERE id=?', (event['event_id'],)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise Conflict('event_id reused with different content')
                return {'event_id': event['event_id'], 'digest': digest, 'duplicate': True,
                        'applied': bool(old['applied']), 'reason': old['reason']}
            if db.execute('SELECT count(*) FROM events').fetchone()[0] >= self.config.get('max_events', 100000):
                raise ValueError('event capacity reached; nothing recorded')
            if sum(p.stat().st_size for p in self.runtime.glob('board.sqlite3*')) > self.config.get('max_database_bytes', 268435456):
                raise ValueError('database capacity reached; nothing recorded')
            if run_id and db.execute('SELECT 1 FROM events WHERE run_id=? AND seq=?', (run_id, seq)).fetchone():
                raise Conflict('sequence reused; keep the original event_id for retry')
            task_id = event['task_id']
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone() if run_id else None
            received = now()
            applied, reason = True, 'recorded'
            if op in {'task', 'ensure_task'}:
                if set(data) != {'title', 'workstream', 'project'}:
                    raise ValueError('task requires title/workstream/project')
                for v in data.values():
                    if not text(v, 180):
                        raise ValueError('empty task metadata')
                if task:
                    if op == 'task':
                        raise Conflict('task exists; reuse stable task_id and start another run')
                    applied = False
                    reason = ('task_exists' if all(task[k] == data[k] for k in ('title', 'project', 'workstream'))
                              else 'task_exists_metadata_unchanged')
                else:
                    if db.execute('SELECT count(*) FROM tasks').fetchone()[0] >= 300:
                        raise ValueError('task capacity 300 reached')
                    db.execute('INSERT INTO tasks(id,title,project,workstream,created_at) VALUES(?,?,?,?,?)',
                               (task_id, data['title'], data['project'], data['workstream'], received))
            elif not task:
                raise Conflict('task missing; create it first')
            elif op == 'task_meta':
                # An explicit, recorded correction of a misfiled board/title. The
                # stable task_id and every run, event and plan stay unchanged.
                if set(data) - {'title', 'workstream', 'reason'} or not text(data.get('reason'), 400):
                    raise ValueError('task_meta requires title and/or workstream plus a reason')
                change = {k: text(data[k], 180) for k in ('title', 'workstream') if k in data and data[k] != task[k]}
                if change:
                    db.execute('UPDATE tasks SET ' + ','.join(k + '=?' for k in change) + ' WHERE id=?', (*change.values(), task_id))
                else:
                    applied, reason = False, 'task_meta_unchanged'
            elif op == 'run':
                if run or set(data) - {'role', 'agent', 'client'} or data.get('role') not in {'execution', 'audit'}:
                    raise Conflict('run exists or invalid role/fields')
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',
                           (run_id, task_id, data['role'], text(data.get('agent'), 100), text(data.get('client'), 100),
                            seq, packed({'status': 'unknown'}), None))
            elif op in RUN_OPS:
                if not run or run['task_id'] != task_id:
                    raise Conflict('run/task mismatch')
                late = seq <= run['last_seq']
                if op == 'claim':
                    if set(data) != {'expected_version'} or type(data['expected_version']) is not int:
                        raise ValueError('claim requires integer expected_version')
                    if late or run['role'] != 'execution' or data['expected_version'] != task['version']:
                        raise Conflict('ownership version conflict or audit role; inspect task before claiming')
                    db.execute('UPDATE tasks SET current_run=?,version=version+1 WHERE id=?', (run_id, task_id))
                elif op == 'progress':
                    fields = self.fields(data)
                    if not late:
                        state = json.loads(run['state']); state.update(fields)
                        business = max(occurred, run['business_at'] or occurred)
                        db.execute('UPDATE runs SET state=?,business_at=? WHERE id=?', (packed(state), business, run_id))
                    if late:
                        applied, reason = False, 'late_sequence'
                    elif task['current_run'] != run_id:
                        applied, reason = False, 'not_current_execution_run'
                elif op in {'ask', 'resolve', 'withdraw'}:
                    # A question stands on its own event ID: never late, never owner-only.
                    data = question_data(op, data)
                else:
                    if set(data) != {'stage', 'verdict', 'candidate_sha', 'evidence', 'audit_id'}:
                        raise ValueError('delivery requires stage/verdict/candidate_sha/evidence/audit_id')
                    if data['stage'] not in STAGES or not SHA.fullmatch(data.get('candidate_sha') or ''):
                        raise ValueError('delivery requires stage and full candidate SHA')
                    text(data['verdict'], 180); text(data['audit_id'], 100)
                    data['evidence'] = self.path_check(data['evidence'])
                    if data['stage'] == 'independent_audit' and run['role'] != 'audit':
                        raise Conflict('independent audit requires separate audit run; execution run role is immutable')
                db.execute('UPDATE runs SET last_seq=MAX(last_seq,?) WHERE id=?', (seq, run_id))
            elif op == 'user_close':
                data = user_close_data(data)
                if 'question_id' in data:
                    known = db.execute("SELECT 1 FROM events WHERE id=? AND task_id=? AND op='ask'", (data['question_id'], task_id)).fetchone()
                else:
                    known = db.execute('SELECT 1 FROM runs WHERE id=? AND task_id=?', (data['target_run'], task_id)).fetchone()
                if not known:
                    raise Conflict('no such request in this task')
            elif op == 'snapshot':
                if src['kind'] != 'document_snapshot' or not src.get('sha256') or not src.get('read_at'):
                    raise ValueError('snapshot requires document hash and read time')
                if set(data) != {'fields', 'excerpt'}:
                    raise ValueError('snapshot requires fields/excerpt')
                data['fields'] = self.fields(data['fields']); text(data['excerpt'], 8000)
            elif op == 'activity':
                if src['kind'] != 'program_observation' or not src.get('source_at'):
                    raise ValueError('activity requires observed time and program source')
                if set(data) != {'summary'}:
                    raise ValueError('activity requires summary only')
                text(data['summary'])
            db.execute('INSERT INTO events(id,digest,task_id,run_id,seq,op,received_at,occurred_at,body,applied,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (event['event_id'], digest, task_id, run_id, seq, op, received, occurred, packed(body), int(applied), reason))
            return {'event_id': event['event_id'], 'digest': digest, 'duplicate': False, 'applied': applied, 'reason': reason,
                    'task_version': db.execute('SELECT version FROM tasks WHERE id=?', (task_id,)).fetchone()[0]}

    def task_view(self, db, task, detail=False):
        t = dict(task)
        runs = [dict(x) for x in db.execute('SELECT * FROM runs WHERE task_id=? ORDER BY rowid', (t['id'],))]
        for r in runs:
            r['state'] = json.loads(r['state'])
        current = next((r for r in runs if r['id'] == t['current_run']), None)
        t['current'] = current
        t['state'] = current['state'] if current else {'status': 'unknown'}
        t['business_at'] = current['business_at'] if current else None
        # Choose the latest event for EACH source before applying the display cap.
        # A cap on raw events could silently erase a conflicting older source.
        cutoff = stamp((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
        rows = list(db.execute('''
            WITH ranked AS (
              SELECT body,received_at,ordinal,
                ROW_NUMBER() OVER (
                  PARTITION BY json_extract(body,'$.source.path'),json_extract(body,'$.source.label')
                  ORDER BY COALESCE(json_extract(body,'$.source.source_at'),'') DESC,ordinal DESC
                ) AS rank
              FROM events WHERE task_id=? AND op='snapshot'
                AND (json_extract(body,'$.source.source_at') IS NULL OR json_extract(body,'$.source.source_at')<=?)
                AND (json_extract(body,'$.source.read_at') IS NULL OR json_extract(body,'$.source.read_at')<=?)
                AND (json_extract(body,'$.source.source_at') IS NULL OR json_extract(body,'$.source.read_at') IS NULL
                     OR julianday(json_extract(body,'$.source.source_at'))<=julianday(json_extract(body,'$.source.read_at'))+5.0/1440)
            )
            SELECT body,received_at,COUNT(*) OVER () AS total_sources FROM ranked WHERE rank=1
            ORDER BY COALESCE(json_extract(body,'$.source.source_at'),'') DESC,ordinal DESC LIMIT 201
        ''', (t['id'], cutoff, cutoff)))
        t['snapshot_source_count'] = rows[0]['total_sources'] if rows else 0
        t['snapshots_truncated'] = t['snapshot_source_count'] > 200
        snapshots = [dict(json.loads(row['body']), received_at=row['received_at']) for row in rows[:200]]
        t['snapshots'] = snapshots
        conflicts = []
        claims = [('Current execution self-report', t['state'])] if current else []
        # A document written before the owner's latest report is history, not a
        # competing claim. Unknown document clocks stay comparable (conservative).
        newer = [s for s in snapshots if not t['business_at'] or not s['source'].get('source_at')
                 or s['source']['source_at'] >= t['business_at']]
        t['superseded_snapshot_count'] = len(snapshots) - len(newer)
        claims += [(s['source'].get('label') or s['source']['path'], s['data']['fields']) for s in newer]
        for key in ('status', 'candidate_sha', 'phase', 'next_step'):
            values = [(name, fields[key]) for name, fields in claims if fields.get(key) not in (None, 'unknown', '')]
            if len({packed(v) for _, v in values}) > 1:
                conflicts.append({'field': key, 'claims': values})
        t['conflicts'] = conflicts
        t['time_warnings'] = db.execute('''SELECT COUNT(*) FROM events WHERE task_id=? AND op IN ('snapshot','activity')
            AND (json_extract(body,'$.source.source_at')>? OR json_extract(body,'$.source.read_at')>?
                 OR julianday(json_extract(body,'$.source.source_at'))>julianday(json_extract(body,'$.source.read_at'))+5.0/1440)''',
            (t['id'], cutoff, cutoff)).fetchone()[0]
        activity = db.execute('''SELECT body,received_at FROM events WHERE task_id=? AND op='activity'
            AND json_extract(body,'$.source.source_at')<=?
            AND (json_extract(body,'$.source.read_at') IS NULL OR json_extract(body,'$.source.read_at')<=?)
            AND (json_extract(body,'$.source.read_at') IS NULL
                 OR julianday(json_extract(body,'$.source.source_at'))<=julianday(json_extract(body,'$.source.read_at'))+5.0/1440)
            ORDER BY json_extract(body,'$.source.source_at') DESC,ordinal DESC LIMIT 1''', (t['id'], cutoff, cutoff)).fetchone()
        t['activity'] = dict(json.loads(activity['body']), received_at=activity['received_at']) if activity else None
        checked_at = datetime.now(timezone.utc)
        stale_after = self.config.get('stale_seconds', 86400)
        owners = {x[0] for x in db.execute("SELECT DISTINCT run_id FROM events WHERE task_id=? AND op='claim' AND applied=1", (t['id'],))}
        questions, open_runs = question_view(db, t['id'], runs, t['current_run'], owners, checked_at, stale_after)
        # Execution ownership is not a task-wide lifecycle. Keep unfinished reviews
        # visible without synthesizing an authority/status shared by unrelated runs.
        visible = ([current] if current else []) + [
            r for r in runs if r['role'] == 'audit' and (r['state'].get('status') != 'ended' or r['id'] in open_runs)]
        t['overview_runs'] = []
        for r in visible:
            state = r['state']
            age = (checked_at - datetime.fromisoformat(r['business_at'].replace('Z', '+00:00'))).total_seconds() if r['business_at'] else None
            stale = age is None or (age > stale_after and state.get('status') != 'ended')
            candidate = state.get('candidate_sha')
            scope = ('current' if candidate == t['state'].get('candidate_sha') else 'other') if candidate and t['state'].get('candidate_sha') else 'unknown'
            t['overview_runs'].append({
                'id': r['id'], 'role': r['role'], 'agent': r['agent'], 'client': r['client'],
                'state': state, 'business_at': r['business_at'], 'stale': stale,
                'candidate_scope': scope,
                'human_input_required': r['id'] in open_runs,
                'needs_attention': bool(stale or state.get('status') == 'waiting_input' or state.get('blocker') or state.get('decision') or r['id'] in open_runs)})
        # State/ownership/business_at stay execution-only. Without an owner, a
        # fresh unfinished audit supplies overview freshness, not execution state.
        # Each other stale audit still independently requires attention below.
        t['stale'] = t['overview_runs'][0]['stale'] if current else not any(
            not r['stale'] for r in t['overview_runs'])
        # Execution runs that do not own the task still report real work
        # (applied=False). Show them separately; they never become the task
        # state or ownership. Unfinished reporting ones are listed; a run with an
        # open request stays, ended or silent, so it counts for joint filters.
        parallel = sorted((r for r in runs if r['role'] == 'execution' and r['id'] != t['current_run']
                           and ((r['business_at'] and r['state'].get('status') != 'ended') or r['id'] in open_runs)),
                          key=lambda r: r['business_at'] or '', reverse=True)
        # Handoff is not an answer and ending is not a withdrawal (question_view).
        # Requests are never truncated by the display cap.
        asked = [r for r in parallel if r['id'] in open_runs]
        shown = asked + [r for r in parallel if r not in asked][:max(0, 8 - len(asked))]
        t['parallel_run_count'] = len(parallel)
        t['parallel_runs'] = [{
            'id': r['id'], 'agent': r['agent'], 'client': r['client'], 'state': r['state'],
            'business_at': r['business_at'],
            'stale': not r['business_at'] or (checked_at - datetime.fromisoformat(r['business_at'].replace('Z', '+00:00'))).total_seconds() > stale_after,
            'former_owner': r['id'] in owners, 'ended': r['state'].get('status') == 'ended',
            'human_input_required': r['id'] in open_runs}
            for r in sorted(shown, key=lambda r: r['business_at'] or '', reverse=True)]
        clocks = [r['business_at'] for r in runs if r['business_at']]
        if t['activity']:
            clocks.append(t['activity']['source']['source_at'])
        t['latest_activity_at'] = max(clocks) if clocks else None
        t['needs_attention'] = bool(t['stale'] or conflicts or t['snapshots_truncated'] or t['time_warnings']
                                    or any(r['needs_attention'] for r in t['overview_runs'])
                                    or bool(questions['open']))
        # Self-reported requests, not a quality warning or authority. An agent
        # that never asked is not detected; never parse free text.
        t['human_input_required'] = bool(questions['open'])
        t['open_questions'] = questions['open']
        # Compact run->item rows; task_board judges links against the plan.
        t['run_items'] = [{
            'id': r['id'], 'role': r['role'], 'agent': r['agent'], 'client': r['client'],
            'item': r['state'].get('item'), 'status': r['state'].get('status', 'unknown'), 'phase': r['state'].get('phase'),
            'business_at': r['business_at'],
            'stale': not r['business_at'] or (checked_at - datetime.fromisoformat(r['business_at'].replace('Z', '+00:00'))).total_seconds() > stale_after,
            'current': r['id'] == t['current_run'], 'open_request': r['id'] in open_runs}
            for r in runs if r['state'].get('status') != 'ended' or r['id'] in open_runs]
        # A former owner's late write is not the current owner's progress.
        peers = [r for r in runs if r['role'] == 'execution' and r['id'] != t['current_run'] and r['id'] not in owners]
        t.update(display_visibility(current, runs, visible + asked, t['activity'], snapshots,
                                    self.display_ttl_seconds, checked_at, peers, held=bool(questions['open'])))
        if detail:
            # The overview never renders deliveries; keep the list payload small.
            t['deliveries'] = []
            for row in db.execute("SELECT body,received_at FROM events WHERE task_id=? AND op='delivery' ORDER BY ordinal DESC LIMIT 100", (t['id'],)):
                b = json.loads(row['body'])
                t['deliveries'].append(dict(b, received_at=row['received_at'], applicable=bool(t['state'].get('candidate_sha')) and b['data']['candidate_sha'] == t['state']['candidate_sha']))
        t['dependency_states'] = []
        for dep in t['state'].get('dependencies', []):
            r = db.execute('SELECT r.state FROM tasks t LEFT JOIN runs r ON r.id=t.current_run WHERE t.id=?', (dep,)).fetchone()
            ended = bool(r and r['state'] and json.loads(r['state']).get('status') == 'ended')
            t['dependency_states'].append({'task_id': dep, 'reported_ended': ended, 'note': 'Reported ended; only a hint that work can continue, never executed' if ended else 'Unknown or not ended'})
        if detail:
            t['questions'] = questions
            t['runs'] = runs
            t['events'] = [dict(x, body=json.loads(x['body'])) for x in map(dict, db.execute('SELECT id,run_id,seq,op,received_at,occurred_at,body,applied,reason FROM events WHERE task_id=? ORDER BY ordinal DESC LIMIT 100', (t['id'],)))]
        return t

    def active_workstreams(self):
        """Boards of the tasks the default list shows now, newest first.

        Derived on every read, never configured: a board appears with its first
        task and leaves once all its tasks pass the display TTL.
        """
        boards = {}
        with self.connect() as db:
            for row in db.execute('SELECT * FROM tasks').fetchall():
                t = self.task_view(db, row)
                if t.get('display_expired'):
                    continue
                b = boards.setdefault(t['workstream'], {'name': t['workstream'], 'tasks': 0, 'updated_at': None})
                b['tasks'] += 1
                if t.get('display_updated_at') and (b['updated_at'] is None or t['display_updated_at'] > b['updated_at']):
                    b['updated_at'] = t['display_updated_at']
        return sorted(boards.values(), key=lambda b: (b['updated_at'] or '', b['name']), reverse=True)

    FEED_OPS = ('progress', 'claim', 'ask', 'resolve', 'withdraw', 'user_close', 'delivery')

    def feed(self, limit=200):
        """Recent reports across all tasks, newest business clock first; display only."""
        rows = []
        with self.connect() as db:
            for e in db.execute(f'''SELECT e.task_id,e.run_id,e.op,e.occurred_at,e.body,t.title,t.workstream,r.agent,r.role
                FROM events e JOIN tasks t ON t.id=e.task_id LEFT JOIN runs r ON r.id=e.run_id
                WHERE e.op IN ({','.join('?' * len(self.FEED_OPS))})
                ORDER BY e.occurred_at DESC,e.ordinal DESC LIMIT ?''', (*self.FEED_OPS, limit)):
                body = json.loads(e['body'])
                d = body.get('data') or {}
                if e['op'] == 'progress':
                    said = d.get('phase') or d.get('progress') or d.get('next_step') or ''
                elif e['op'] == 'ask':
                    said = d.get('question', '')
                elif e['op'] == 'delivery':
                    said = ' · '.join(str(d.get(k)) for k in ('stage', 'verdict') if d.get(k))
                else:
                    said = d.get('answer') or d.get('reason') or d.get('note') or ''
                line = next((x.strip() for x in str(said).split('\n') if x.strip()), '')
                rows.append({'task_id': e['task_id'], 'title': e['title'], 'workstream': e['workstream'],
                             'run_id': e['run_id'], 'agent': e['agent'], 'role': e['role'], 'op': e['op'],
                             'at': e['occurred_at'], 'status': d.get('status'),
                             'question_id': d.get('question_id') or (body.get('event_id') if e['op'] == 'ask' else None),
                             'text': line[:240] + ('…' if len(line) > 240 else '')})
        return rows

    def view(self, task_id=None):
        with self.connect() as db:
            if task_id:
                ident(task_id)
                t = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                if not t:
                    raise NotFound('task not found')
                return self.task_view(db, t, True)
            return {'generated_at': now(), 'timezone': self.display_time_zone,
                    'display_ttl_seconds': self.display_ttl_seconds,
                    'tasks': [self.task_view(db, t) for t in db.execute('SELECT * FROM tasks ORDER BY workstream,title')],
                    'event_count': db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}
