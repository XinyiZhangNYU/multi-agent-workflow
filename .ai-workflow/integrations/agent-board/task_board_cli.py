"""Local task-board helper: verified HTTP, own bounded outbox, no SQLite writes."""
import hashlib
import json
from pathlib import Path
import urllib.error
import uuid

from model import ident, now, packed
from server import FileLock, atomic_json
from task_board import OPERATIONS, DEFAULT_PLAN_BYTES, DEFAULT_SUMMARY_BYTES, CLOSE_RESERVE_BYTES, limit, validate


def parser(sub):
    p = sub.add_parser('task-board', help='explicit opt-in execution plan; not progress-task approval')
    commands = p.add_subparsers(dest='plan_command', required=True)
    commands.add_parser('list'); commands.add_parser('pending')
    commands.add_parser('flush').add_argument('--task', help='only recover this task; omitted means explicit global recovery')
    s = commands.add_parser('withdraw', help='explicitly withdraw queued request, never undo remote writes')
    s.add_argument('event_id'); s.add_argument('--expected-digest', required=True); s.add_argument('--reason', required=True)
    for name in ('show', 'summary', 'history'):
        s = commands.add_parser(name); s.add_argument('task_id'); s.add_argument('--cached', action='store_true')
        if name in {'summary', 'history'}:
            s.add_argument('--mainline')
            s.add_argument('--offset', type=int, default=0)
            s.add_argument('--version', type=int)
    s = commands.add_parser('submit'); s.add_argument('file', help='immutable request JSON; reuse event_id on retry')
    for name in sorted(OPERATIONS):
        s = commands.add_parser(name); s.add_argument('task_id'); s.add_argument('--expected-version', required=True, type=int)
        s.add_argument('--data', help='UTF-8 JSON operation fields (required except freeze)')
        s.add_argument('--event-id', help='stable retry ID; otherwise created once and preserved in outbox')


def filename(value):
    ident(value)
    return hashlib.sha256(value.encode('ascii')).hexdigest() + '.json'


def load(path, max_bytes=262144):
    if Path(path).stat().st_size > max_bytes:
        raise ValueError('task-board local file too large')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def enqueue(runtime, request, config):
    request = validate(config, request)  # Before ANY physical write.
    root = (runtime / 'task-board-outbox').resolve(); target = root / filename(request['event_id'])
    if not root.is_relative_to(runtime.resolve()) or not target.resolve().is_relative_to(root):
        raise ValueError('task-board queue path outside root')
    with FileLock(runtime / 'task-board-outbox.lock'):
        files = list(root.glob('*.json'))
        if target.exists():
            if load(target)['request'] != request:
                raise ValueError('queued task-board ID content mismatch')
            return target
        if len(files) >= config.get('max_outbox_events', 1000) or sum(f.stat().st_size for f in files) + len(packed(request).encode()) + 128 > config.get('max_outbox_bytes', 8388608):
            raise RuntimeError('TASK_BOARD_OUTBOX_FULL: not queued; retain input')
        order = max((load(f)['order'] for f in files), default=0) + 1
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(target, {'order': order, 'request': request})
        return target


def deliver(runtime, body, transport, info):
    digest = hashlib.sha256(packed(body).encode()).hexdigest()
    try:
        receipt = transport(runtime, '/api/task-board-events', body, expected_info=info)
    except urllib.error.HTTPError as e:
        try:
            response = json.loads(e.read(2000))
        finally:
            e.close()
        if e.code in {400, 409} and response.get('instance_id') == info['instance_id'] and response.get('service') == 'agent-board-v1' and response.get('event_id') == body['event_id']:
            return {'status': 'CONFLICT_NOT_RECORDED' if e.code == 409 else 'REJECTED_NOT_RECORDED', 'error': response.get('error')}
        raise RuntimeError('unconfirmed task-board response; retain original request') from e
    if not isinstance(receipt, dict) or receipt.get('service') != 'agent-board-v1' or receipt.get('instance_id') != info['instance_id'] or receipt.get('event_id') != body['event_id'] or receipt.get('digest') != digest or receipt.get('recorded') is not True or type(receipt.get('duplicate')) is not bool or type(receipt.get('version')) is not int:
        raise RuntimeError('unconfirmed task-board receipt; retain original request')
    return dict(receipt, status='RECORDED')


def flush(runtime, config, transport, health, task_id=None):
    if task_id is not None:
        ident(task_id)
    result = {'status': 'SYNCED', 'recorded': 0, 'remaining': 0, 'completed': [], 'receipts': {}, 'errors': []}
    with FileLock(runtime / 'task-board-flush.lock'):
        with FileLock(runtime / 'task-board-outbox.lock'):
            files = [(f, load(f)) for f in (runtime / 'task-board-outbox').glob('*.json')]
        files.sort(key=lambda pair: pair[1]['order']); blocked = set()
        if task_id is not None:
            files = [(f, i) for f, i in files if i['request']['task_id'] == task_id]
        def remaining_counts():
            with FileLock(runtime / 'task-board-outbox.lock'):
                pending = [load(f) for f in (runtime / 'task-board-outbox').glob('*.json')]
            scoped = sum(task_id is None or item['request']['task_id'] == task_id for item in pending)
            return {'remaining': scoped, 'scoped_remaining': scoped, 'global_remaining': len(pending)}
        try:
            info = health(runtime) if files else None
        except (OSError, ValueError, RuntimeError, KeyError) as e:
            result.update(status='QUEUED_NOT_RECORDED', errors=[{'error': str(e)[:200]}], **remaining_counts()); return result
        for path, item in files:
            body = validate(config, item['request'])
            if body['task_id'] in blocked:
                continue
            try:
                receipt = deliver(runtime, body, transport, info)
                if receipt['status'] != 'RECORDED':
                    result['errors'].append({'event_id': body['event_id'], **receipt}); blocked.add(body['task_id']); continue
                with FileLock(runtime / 'task-board-outbox.lock'):
                    if load(path) != item:
                        raise RuntimeError('queued request changed; no cleanup')
                    path.unlink()
                result['recorded'] += 1; result['completed'].append(body['event_id'])
                result['receipts'][body['event_id']] = receipt
            except (OSError, ValueError, RuntimeError, KeyError) as e:
                result['errors'].append({'event_id': body['event_id'], 'error': str(e)[:200]}); break
        result.update(remaining_counts())
        if result['remaining']:
            statuses = {e.get('status') for e in result['errors']}
            result['status'] = 'CONFLICT_NOT_RECORDED' if 'CONFLICT_NOT_RECORDED' in statuses else 'REJECTED_NOT_RECORDED' if 'REJECTED_NOT_RECORDED' in statuses else 'QUEUED_NOT_RECORDED'
    return result


def run(args, config, runtime, transport, health):
    from urllib.parse import urlencode
    command = args.plan_command
    if command == 'list':
        return transport(runtime, '/api/task-boards')
    if command == 'pending':
        with FileLock(runtime / 'task-board-outbox.lock'):
            items = [load(f) for f in (runtime / 'task-board-outbox').glob('*.json')]
        return {'remaining': len(items), 'requests': [{'order': i['order'], 'digest': hashlib.sha256(packed(i['request']).encode()).hexdigest(), **{k: i['request'][k] for k in ('event_id', 'task_id', 'op', 'expected_version')}} for i in sorted(items, key=lambda i: i['order'])]}
    if command == 'withdraw':
        from task_board import required
        ident(args.event_id); required(args.reason)
        path = runtime / 'task-board-outbox' / filename(args.event_id)
        with FileLock(runtime / 'task-board-flush.lock'), FileLock(runtime / 'task-board-outbox.lock'):
            item = load(path)
            if hashlib.sha256(packed(item['request']).encode()).hexdigest() != args.expected_digest:
                raise ValueError('withdraw digest mismatch; retained unchanged')
            destination = runtime / 'task-board-withdrawn' / filename(args.event_id)
            files = list(destination.parent.glob('*.json'))
            if len(files) >= config.get('max_outbox_events', 1000) or sum(f.stat().st_size for f in files) + len(packed(item).encode()) + 2200 > config.get('max_outbox_bytes', 8388608):
                raise RuntimeError('withdraw evidence capacity reached; nothing removed')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError('withdraw evidence exists; retained queued request')
            atomic_json(destination, dict(item, withdrawn_at=now(), reason=args.reason))
            path.unlink()
        return {'status': 'WITHDRAWN_LOCAL_ONLY', 'event_id': args.event_id, 'remote_state': 'unchanged_or_unconfirmed; read before resubmission', 'evidence': str(destination)}
    if command == 'flush':
        return flush(runtime, config, transport, health, getattr(args, 'task', None))
    if command in {'show', 'summary', 'history'}:
        ident(args.task_id)
        query = {'task_id': args.task_id}
        if command != 'show':
            query.update({'summary': '1'} if command == 'summary' else {'section': 'history'})
            query['offset'] = getattr(args, 'offset', 0)
            if getattr(args, 'mainline', None): query['mainline'] = args.mainline
            if getattr(args, 'version', None) is not None: query['version'] = args.version
        cache_key = hashlib.sha256(packed(query).encode()).hexdigest()+'.json'
        cache = runtime / ('task-board-cache' if command == 'show' else 'task-board-summary-cache') / (filename(args.task_id) if command == 'show' else cache_key)
        cache_limit = limit(config, 'max_task_board_bytes', DEFAULT_PLAN_BYTES)+CLOSE_RESERVE_BYTES+16384 if command == 'show' else limit(config, 'max_task_board_summary_bytes', DEFAULT_SUMMARY_BYTES)+8192
        if args.cached:
            value = load(cache, cache_limit)
            if value.get('task_id') != args.task_id or value.get('service') != 'agent-board-v1':
                raise ValueError('cached baseline identity mismatch')
            if 'version' in query and value.get('version') != query['version']:
                raise RuntimeError('cached task-board version mismatch')
            value = dict(value, status='CACHED_BASELINE', warning='last verified read only; pending changes not saved here; current version unconfirmed')
        else:
            value = transport(runtime, '/api/task-board?' + urlencode(query))
            if value.get('task_id') != args.task_id or value.get('service') != 'agent-board-v1' or not value.get('instance_id'):
                raise RuntimeError('task-board read identity mismatch')
            if 'version' in query and value.get('version') != query['version']:
                raise RuntimeError('task-board read version mismatch; reread summary')
            if len(packed(value).encode()) > cache_limit:
                raise RuntimeError('read exceeds configured cache limit; not cached')
            with FileLock(runtime / 'task-board-cache.lock'):
                previous = load(cache, cache_limit) if cache.exists() else None
                if previous is None or value['version'] >= previous.get('version', -1):
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    value = dict(value, read_at=now(), cache_kind=command, verified_read=True)
                    cached_files = [p for name in ('task-board-cache', 'task-board-summary-cache') for p in (runtime/name).glob('*.json') if p != cache]
                    if sum(p.stat().st_size for p in cached_files) + len(packed(value).encode()) > limit(config, 'max_task_board_cache_bytes', 64*1024*1024):
                        # Read is valid; cache failure is not a fabricated save.
                        return dict(value, cache_status='NOT_CACHED_CAPACITY', warning='verified server read; cache budget reached, previous caches unchanged')
                    atomic_json(cache, value)
        return value
    if command == 'submit':
        body = load(args.file)
    else:
        body = {'event_id': args.event_id or str(uuid.uuid4()), 'task_id': args.task_id,
                'expected_version': args.expected_version, 'op': command,
                'data': load(args.data) if args.data else {}}
    body = validate(config, body)
    enqueue(runtime, body, config)
    result = flush(runtime, config, transport, health, body['task_id'])
    if body['event_id'] in result['completed']:
        return result['receipts'][body['event_id']]
    # Another flusher may have removed this ID. Verify it; absence is not receipt.
    path = runtime / 'task-board-outbox' / filename(body['event_id'])
    if not path.exists():
        try:
            return deliver(runtime, body, transport, health(runtime))
        except (OSError, ValueError, RuntimeError, KeyError):
            enqueue(runtime, body, config)
    return dict(result, event_id=body['event_id'], recorded=False)
