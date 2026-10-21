"""Stdlib CLI. One HTTP event protocol for every agent client and for ordinary scripts."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from model import Protocol, ident, now, packed, stamp, workstream_choice
from server import FileLock, atomic_json, serve
import task_board

BASE = Path(__file__).resolve().parents[2]


def default_config(base=BASE):
    """The board config next to the project configuration that STATE.md names, or None."""
    state = base / 'STATE.md'
    if not state.is_file():
        return None
    match = re.search(r'^project_configuration:\s*(\S+)\s*$', state.read_text(encoding='utf-8'), re.M)
    if not match or Path(match.group(1)).is_absolute():
        return None
    return base / Path(match.group(1)).parent / 'agent-board.json'


class RemoteConflict(ValueError):
    """A 409 confirmed by the expected board instance, not an arbitrary listener."""


class RemoteNotFound(ValueError):
    """A 404 confirmed by the expected board instance."""


class RegistrationUnverified(Exception):
    """Offline, with no local basis that the task/run is registered."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(path, max_bytes=131072):
    if Path(path).stat().st_size > max_bytes:
        raise ValueError('local JSON input exceeds size limit')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def settings(args):
    config = read_json(args.config)
    root = Path(getattr(args, 'project_root', None) or config['project_root'])
    if not root.is_absolute():
        root = (Path(args.config).resolve().parent / root).resolve()
    config['project_root'] = str(root)
    if not (root / '.ai-workflow/WORKFLOW.md').is_file():
        raise ValueError('project_root is not an installed project; in a development worktree use --project-root explicitly')
    runtime = Path(args.runtime) if args.runtime else root / '.ai-workflow/runtime/agent-board'
    return config, runtime.resolve()


def request(runtime, path, data=None, timeout=1.5, *, expected_info=None):
    # Bind the reply to the metadata captured BEFORE this request. Rereading the
    # pointer afterwards could accept a different instance during replacement.
    info = expected_info if expected_info is not None else read_json(runtime / 'instance.json')
    url = info['url']
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port is None
            or parsed.netloc != f'127.0.0.1:{parsed.port}' or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('invalid local instance address')
    headers = {'X-Agent-Board-Instance': info['instance_id']}
    if data is not None:
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (runtime / 'write-token').read_text(encoding='ascii').strip(),
                   'X-Agent-Board-Instance': info['instance_id']}
    req = urllib.request.Request(url + path, data=packed(data).encode() if data is not None else None, headers=headers)
    # Do not follow any redirect with a local write credential.
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as r:
            result = json.load(r)
    except urllib.error.HTTPError as error:
        if data is None:
            try:
                body = json.loads(error.read(2000))
            except (ValueError, OSError):
                body = None
            finally:
                error.close()
            detail = body.get('error') if isinstance(body, dict) else None
            if error.code == 404 and detail and body.get('service') == 'agent-board-v1' and body.get('instance_id') == info['instance_id']:
                raise RemoteNotFound(f'HTTP 404: {detail}') from error
            if error.code == 400 and detail:
                raise ValueError(f'HTTP 400: {detail}') from error
        raise
    if data is None and (not isinstance(result, dict) or result.get('service') != 'agent-board-v1'
                         or result.get('instance_id') != info['instance_id']):
        raise RuntimeError('UNCONFIRMED_READ_IDENTITY: verify runtime and service version')
    return result


def health(runtime):
    info = read_json(runtime / 'instance.json')
    actual = request(runtime, '/api/health', expected_info=info)
    if actual['service'] != 'agent-board-v1' or actual['instance_id'] != info['instance_id'] or actual['pid'] != info['pid']:
        raise RuntimeError('service identity mismatch')
    return dict(actual, url=info['url'])


def storage_name(kind, value):
    """Case-sensitive protocol IDs mapped to bounded, case-safe physical names.

    Contents are still checked before replacement: a hash/path collision must
    fail closed.
    """
    ident(value)
    return kind + '-' + hashlib.sha256(value.encode('ascii')).hexdigest()


def registration_path(runtime, task_id, run_id=None):
    kind, value = ('run', run_id) if run_id is not None else ('task', task_id)
    return Path(runtime) / 'registrations' / 'v1' / (storage_name(kind, value) + '.json')


def remember_registration(runtime, task_id, run_id=None):
    """Note a registration this machine saw confirmed: the offline basis for later events."""
    records = [(None, {'task_id': task_id})] + ([(run_id, {'task_id': task_id, 'run_id': run_id})] if run_id is not None else [])
    for run, record in records:
        path = registration_path(runtime, task_id, run)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, record)


def known_registration(runtime, task_id, run_id=None):
    expected = {'task_id': task_id} if run_id is None else {'task_id': task_id, 'run_id': run_id}
    try:
        return read_json(registration_path(runtime, task_id, run_id), max_bytes=4096) == expected
    except (OSError, ValueError):
        return False  # An unreadable note is no basis.


def enqueue(runtime, event, config):
    event = Protocol(config).validate(event)
    runtime = Path(runtime).resolve()
    kind, owner = ('run', event['run_id']) if event.get('run_id') else ('task', event['task_id'])
    directory = runtime / 'outbox' / storage_name(kind, owner)
    path = (directory / (storage_name('event', event['event_id']) + '.json')).resolve()
    outbox = (runtime / 'outbox').resolve()
    if not outbox.is_relative_to(runtime) or not path.is_relative_to(outbox) or path.parent != directory.resolve():
        raise ValueError('outbox path outside runtime')
    with FileLock(runtime / 'outbox.lock'):
        files = list((runtime / 'outbox').glob('*/*.json'))
        for f in files:
            stored = read_json(f)['event']
            if stored['event_id'] == event['event_id']:
                if stored != event:
                    raise ValueError('outbox event ID content mismatch')
                return f
        if path.exists():
            raise RuntimeError('outbox physical identity collision; original queue unchanged')
        if len(files) >= config.get('max_outbox_events', 1000) or sum(p.stat().st_size for p in files) + len(packed(event).encode()) + 128 > config.get('max_outbox_bytes', 8388608):
            raise RuntimeError('OUTBOX_FULL: nothing queued; preserve input and report')
        counter = runtime / 'enqueue-sequence.json'
        order = max(read_json(counter)['value'] if counter.exists() else 0,
                    max((read_json(f)['order'] for f in files), default=0)) + 1
        atomic_json(counter, {'value': order})
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(path, {'queue_version': 1, 'order': order, 'event': event})
        return path


def send(runtime, event, config, queue=True, *, expected_info=None):
    event = Protocol(config).validate(event)
    if queue:
        path = enqueue(runtime, event, config)
        result = flush(runtime, config, task_id=event['task_id'])
        if event['event_id'] in result['completed']:
            return {'status': 'RECORDED', 'event_id': event['event_id']}
        if not path.exists():
            # Another helper may already have flushed this event. Confirm the same
            # immutable event with the service; absence alone is not a receipt.
            try:
                return send(runtime,event,config,queue=False)
            except OSError:
                enqueue(runtime,event,config)
        own = next((e for e in result['errors'] if e['event_id'] == event['event_id']), None)
        status = 'CONFLICT_NOT_RECORDED' if own and own.get('kind') == 'conflict' else 'REJECTED_NOT_RECORDED' if own and own.get('kind') == 'rejected' else 'QUEUED_NOT_RECORDED'
        head = result['errors'][0] if result['errors'] else None
        # Only a confirmed 400/409 is a rejection. A timeout, a refused connection
        # or a lost receipt leaves it unknown whether the service recorded it.
        if own and own.get('kind') != 'unconfirmed':
            warning = (f'Board not updated: the service explicitly rejected this event ({own["error"][:160]}). '
                       'Fix the cause and retry with the same event_id; do not switch to a new ID to hide it.')
        elif head and head.get('kind') != 'unconfirmed':
            warning = (f'Board not updated: this event is queued, but an earlier queued event {head["event_id"]} ({head.get("op")}) '
                       f'for the same task was rejected by the service and blocks all later reports for this task '
                       f'({result["scoped_remaining"]} pending). Check with pending; the task owner resolves it.')
        else:
            warning = (f'Board unconfirmed: the service is unavailable or the receipt was lost, so it is unknown whether '
                       f'this event was recorded; it stays queued ({result["scoped_remaining"]} unconfirmed for this task). '
                       f'When the service is back, run flush --task {event["task_id"]}; '
                       'the retry keeps the same event_id and content. Do not edit the content or switch IDs.')
        return {'status': status, 'event_id': event['event_id'], 'board_updated': False,
                'pending_for_task': result['scoped_remaining'], 'warning': warning, 'errors': result['errors']}
    try:
        info = expected_info if expected_info is not None else read_json(runtime / 'instance.json')
        result = request(runtime, '/api/events', event, expected_info=info)
        expected = hashlib.sha256(packed(event).encode('utf-8')).hexdigest()
        instance = info['instance_id']
        if (not isinstance(result, dict) or result.get('service') != 'agent-board-v1'
                or result.get('instance_id') != instance or result.get('event_id') != event['event_id']
                or result.get('digest') != expected or result.get('recorded') is not True
                or type(result.get('duplicate')) is not bool or type(result.get('applied')) is not bool):
            raise RuntimeError('UNCONFIRMED_EVENT_RECEIPT: retain original event')
        try:
            # A confirmed receipt proves the task (and the run) is registered.
            remember_registration(runtime, event['task_id'], event.get('run_id') or None)
        except OSError:
            pass  # Without the note a later offline event is held, never misqueued.
        return dict(result, status='RECORDED')
    except urllib.error.HTTPError as e:
        try:
            message = e.read(1000).decode('utf-8', errors='replace')
        finally:
            e.close()
        if e.code == 409:
            try:
                body = json.loads(message)
            except json.JSONDecodeError:
                body = None
            instance = info['instance_id']
            if (isinstance(body, dict) and body.get('service') == 'agent-board-v1'
                    and body.get('instance_id') == instance and body.get('event_id') == event['event_id']):
                raise RemoteConflict(f'REJECTED HTTP 409: {body.get("error", "conflict")}') from e
            raise RuntimeError('UNCONFIRMED_CONFLICT_RESPONSE: retain original event') from e
        if e.code == 400:
            raise ValueError(f'REJECTED HTTP 400: {message}') from e
        if e.code < 500:
            raise RuntimeError(f'UNCONFIRMED_HTTP_{e.code}: retain original event') from e
        raise
    except (OSError, TimeoutError):
        raise


def flush(runtime, config, task_id=None):
    if task_id is not None:
        ident(task_id)
    result = {'recorded': 0, 'remaining': 0, 'errors': [], 'completed': []}
    with FileLock(runtime / 'flush.lock'):
        with FileLock(runtime / 'outbox.lock'):
            files = [(f, read_json(f)) for f in (runtime / 'outbox').glob('*/*.json')]
        # Durable global enqueue order preserves causality ACROSS runs. A real conflict
        # blocks only this task's descendants, not unrelated workstreams.
        files.sort(key=lambda pair: pair[1]['order'])
        if task_id is not None:
            files = [(f, item) for f, item in files if item['event']['task_id'] == task_id]
        blocked = set()
        unavailable = False
        for f, item in files:
            event = item['event']
            if event['task_id'] in blocked or unavailable:
                continue
            try:
                send(runtime, event, config, queue=False)
                with FileLock(runtime / 'outbox.lock'):
                    f.unlink()
                result['recorded'] += 1
                result['completed'].append(event['event_id'])
            except (OSError, ValueError, RuntimeError) as e:
                blocked.add(event['task_id'])
                result['errors'].append({'event_id': event['event_id'], 'op': event['op'], 'run_id': event.get('run_id'),
                                         'kind': 'conflict' if isinstance(e, RemoteConflict) else 'rejected' if isinstance(e, ValueError) else 'unconfirmed',
                                         'error': str(e)[:300]})
                unavailable = isinstance(e, (OSError, RuntimeError))
        with FileLock(runtime / 'outbox.lock'):
            pending = [read_json(f) for f in (runtime / 'outbox').glob('*/*.json')]
        result['global_remaining'] = len(pending)
        result['scoped_remaining'] = sum(task_id is None or item['event']['task_id'] == task_id for item in pending)
        result['remaining'] = result['scoped_remaining']
    return result


def pending(runtime, config=None):
    with FileLock(runtime / 'outbox.lock'):
        captured = []
        for path in (runtime / 'outbox').glob('*/*.json'):
            raw = path.read_bytes()
            item = json.loads(raw)
            event = item['event']
            entry = {'order': item['order'], 'event_id': event['event_id'],
                     'task_id': event['task_id'], 'op': event['op'],
                     'queue_sha256': hashlib.sha256(raw).hexdigest()}
            captured.append((entry, event))
    entries = []
    for entry, event in captured:
        if config is not None:
            try:
                Protocol(config).validate(event)
                entry['local_validation'] = 'CURRENT_VALID'
            except ValueError as error:
                entry['local_validation'] = 'REJECTED'
                entry['local_rejection'] = str(error)[:180]
        entries.append(entry)
    entries.sort(key=lambda item: item['order'])
    return {'remaining': len(entries), 'events': entries}


def quarantine(runtime, config, event_id, expected_sha256, reason):
    ident(event_id)
    if len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256.lower()):
        raise ValueError('expected queue SHA-256 required')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 400 or any(ord(c) < 32 and c not in '\n\t' for c in reason):
        raise ValueError('nonempty bounded resolution reason required')
    with FileLock(runtime / 'flush.lock'):
        with FileLock(runtime / 'outbox.lock'):
            entries = []
            for path in (runtime / 'outbox').glob('*/*.json'):
                raw = path.read_bytes(); item = json.loads(raw)
                entries.append((item['order'], path, raw, item['event']))
        entries.sort(key=lambda item: item[0])
        matches = [item for item in entries if item[3]['event_id'] == event_id]
        if len(matches) != 1:
            raise ValueError('exact queued event not found')
        _, path, raw, event = matches[0]
        if next(item for item in entries if item[3]['task_id'] == event['task_id'])[3]['event_id'] != event_id:
            raise ValueError('only the first queued event for this task may be quarantined')
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_sha256.lower():
            raise ValueError('queued event changed; inspect pending again')
        queue_item = json.loads(raw)
        instance_info = health(runtime)
        instance = instance_info['instance_id']
        try:
            send(runtime, event, config, queue=False, expected_info=instance_info)
        except RemoteConflict as conflict:
            mode = 'confirmed_remote_409'
            verification = {'conflict': str(conflict)}
        else:
            raise RuntimeError('event was recorded; run flush instead of quarantining')
        destination = runtime / 'quarantine' / 'v2' / (storage_name('event', event_id) + '.json')
        encoded_raw = base64.b64encode(raw).decode('ascii')
        with FileLock(runtime / 'outbox.lock'):
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('queued event changed during resolution; inspect pending again')
            if destination.exists():
                previous = read_json(destination, max_bytes=262144)
                prior_mode = previous.get('resolution', {}).get('kind')
                # An interrupted quarantine resumes only with the identical record.
                if (previous.get('queue_sha256') != digest or previous.get('queue_item') != queue_item
                        or prior_mode != mode or previous.get('queue_raw_b64') != encoded_raw):
                    raise RuntimeError('different quarantine record exists; original queue unchanged')
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(destination, {'queue_item': queue_item, 'queue_sha256': digest,
                                          'queue_raw_b64': encoded_raw,
                                          'resolution': {'at': now(), 'instance_id': instance,
                                                         'reason': reason.strip(), 'kind': mode,
                                                         'queue_version': queue_item['queue_version'],
                                                         **verification}})
            preserved = read_json(destination, max_bytes=262144)
            if (preserved.get('queue_sha256') != digest or preserved.get('queue_item') != queue_item
                    or preserved.get('queue_raw_b64') != encoded_raw):
                raise RuntimeError('quarantine evidence not confirmed; original queue unchanged')
            path.unlink()
        return {'status': 'QUARANTINED', 'event_id': event_id, 'task_id': event['task_id'],
                'recorded': False, 'verification': mode,
                'preserved_at': str(destination), 'remaining': pending(runtime)['remaining']}


def check_registration(runtime, task_id, run_id=None):
    """Refuse events the service would permanently reject as an unknown task/run.

    An unregistered run's event at the head of its task's queue blocks every later
    report for that task. Accept only a registration queued ahead locally (it is
    delivered first), one this machine saw confirmed before, or one the service
    confirms now. Offline with none of these, raise: the event is held, not queued.
    """
    with FileLock(runtime / 'outbox.lock'):
        queued = [read_json(f)['event'] for f in (runtime / 'outbox').glob('*/*.json')]
    if run_id is None and any(e['task_id'] == task_id and e['op'] in {'task', 'ensure_task'} for e in queued):
        return
    if run_id is not None and any(e['op'] == 'run' and e['run_id'] == run_id and e['task_id'] == task_id for e in queued):
        return
    if known_registration(runtime, task_id, run_id):
        return
    from urllib.parse import quote
    try:
        value = request(runtime, '/api/task?id=' + quote(task_id))
    except RemoteNotFound:
        raise ValueError(f'TASK_NOT_REGISTERED: task {task_id} does not exist; register it first with board.py task '
                         '(or check the task_id). Not queued; no sequence number used') from None
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        subject = f'run {run_id} (task {task_id})' if run_id is not None else f'task {task_id}'
        raise RegistrationUnverified(f'UNVERIFIED_NOT_QUEUED: the service is unavailable ({str(error)[:120]}) and this machine '
                                     f'has no local registration record for {subject}. Not queued; no sequence number used. '
                                     'The input is kept in the held area and never replayed automatically; '
                                     'rerun this command unchanged once the service is back') from None
    if run_id is not None and run_id not in {r['id'] for r in value.get('runs', [])}:
        raise ValueError(f'RUN_NOT_REGISTERED: run {run_id} is not registered under task {task_id}; first run board.py run {task_id} {run_id} '
                         '--agent <tool> --client <client> [--role audit], then claim. Not queued; no sequence number used')
    try:
        remember_registration(runtime, task_id, run_id)
    except OSError:
        pass


def plan_in_force(runtime, task_id):
    """The task's plan as the service holds it now; None when unreachable."""
    from urllib.parse import quote
    try:
        return request(runtime, '/api/task-board?task_id=' + quote(task_id))
    except (OSError, RuntimeError, KeyError):
        return None


def check_item(task_id, item, plan):
    """Refuse a link the service would show as unknown. Offline it is queued and judged on read."""
    if plan is None:
        return
    if not plan.get('exists'):
        raise ValueError(f'NO_PLAN: task {task_id} has no execution plan, so no work item can be linked. Not queued; no sequence number used')
    if task_board.link_state(plan['board'], item) == 'unknown_item':
        name = item['mainline'] + ('/' + item['branch'] if 'branch' in item else '')
        raise ValueError(f'UNKNOWN_ITEM: the execution plan of task {task_id} (version {plan["version"]}) has no {name}, '
                         'or the branch does not belong to that mainline; check the IDs with task-board summary. Not queued; no sequence number used')


def hold(runtime, event, reason, command):
    """Keep refused input for the caller; never replayed and never counted as queued."""
    path = Path(runtime) / 'held' / (uuid.uuid4().hex + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {'held_at': now(), 'command': command, 'reason': reason, 'event': event})
    return {'status': 'UNVERIFIED_NOT_QUEUED', 'board_updated': False, 'warning': reason, 'held': str(path)}


def board_workstream(runtime, value):
    """Reuse the spelling of a board in use; a new board is accepted, not refused."""
    try:
        active = request(runtime, '/api/workstreams')['workstreams']
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        return value, f'Could not check existing workstreams ({str(error)[:120]}); registered as given: "{value}"'
    return workstream_choice(value, active)


def sequence(runtime, run_id, minimum=0):
    ident(run_id)
    path = runtime / 'sequences' / 'v2' / (storage_name('run', run_id) + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(runtime / 'sequence.lock'):
        saved = read_json(path) if path.exists() else {'run_id': run_id, 'seq': 0}
        if saved.get('run_id') != run_id:
            raise RuntimeError('sequence physical identity collision; counter unchanged')
        seq = max(saved['seq'], minimum) + 1
        atomic_json(path, {'run_id': run_id, 'seq': seq})
        return seq


def envelope(op, task, data, run=None, seq=None, source=None):
    return {'event_id': str(uuid.uuid4()), 'task_id': ident(task), 'run_id': run, 'seq': seq, 'op': op,
            'occurred_at': now(), 'source': source or {'kind': 'agent_report', 'label': 'CLI short report'}, 'data': data}


def start(args, config, runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    with FileLock(runtime / 'start.lock'):
        try:
            return dict(health(runtime), status='RUNNING')
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        port = args.port or config.get('port', 8765)
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                raise RuntimeError(f'PORT_IN_USE {port}: choose --port; no process was stopped')
        cmd = [sys.executable, '-B', str(Path(__file__).resolve()), '--config', str(Path(args.config).resolve()), '--project-root', config['project_root'], '--runtime', str(runtime), 'serve', '--port', str(port)]
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        launched = time.perf_counter()
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, start_new_session=os.name != 'nt', close_fds=True)
        for _ in range(40):
            time.sleep(.1)
            try:
                return dict(health(runtime), status='RUNNING', startup_ms=round((time.perf_counter()-launched)*1000, 1))
            except (OSError, RuntimeError, ValueError, KeyError):
                pass
        raise RuntimeError('START_UNCONFIRMED: inspect status; do not launch another instance blindly')


def exit_code(result):
    if isinstance(result, list):
        return max((exit_code(item) for item in result), default=0)
    if not isinstance(result, dict):
        return 1
    if result.get('status') == 'CONFLICT_NOT_RECORDED':
        return 3
    if result.get('status') == 'REJECTED_NOT_RECORDED':
        return 1
    if result.get('status') in {'ERROR', 'STOP_UNCONFIRMED'}:
        return 1
    if result.get('status') == 'UNVERIFIED_NOT_QUEUED':
        return 4
    if result.get('status') == 'QUEUED_NOT_RECORDED' or result.get('remaining', 0):
        return 2
    if 'outbox' in result:
        return exit_code(result['outbox'])
    return 0


def main():
    p = argparse.ArgumentParser(description='Local multi-agent progress board V1')
    p.add_argument('--config', help='board config; defaults to agent-board.json beside the project configuration named in STATE.md')
    p.add_argument('--runtime', help='isolated runtime override for tests')
    p.add_argument('--project-root', help='explicit project root for an isolated development checkout')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('start', 'serve'):
        s=sub.add_parser(name); s.add_argument('--port', type=int)
    sub.add_parser('status'); sub.add_parser('stop'); sub.add_parser('import-snapshots'); sub.add_parser('pending')
    s=sub.add_parser('flush'); s.add_argument('--task', help='only this task; absence explicitly flushes all queued tasks')
    s=sub.add_parser('quarantine', help='preserve one verified blocked queue head; does not auto-flush')
    s.add_argument('event_id'); s.add_argument('--expected-sha256', required=True); s.add_argument('--reason', required=True)
    s=sub.add_parser('task'); s.add_argument('task_id'); s.add_argument('--title', required=True); s.add_argument('--workstream', required=True)
    s=sub.add_parser('retag', help='correct the workstream/title of a registered task (audited; task_id, runs and history unchanged); needs user/maintainer approval')
    s.add_argument('task_id'); s.add_argument('--title'); s.add_argument('--workstream'); s.add_argument('--reason', required=True)
    s=sub.add_parser('run', help='register an execution/audit run; --agent is the tool name, not the model or effort')
    s.add_argument('task_id'); s.add_argument('run_id')
    s.add_argument('--agent', required=True, metavar='TOOL',
                   help='tool/client family, e.g. Claude, Codex, Gemini, OpenCode, Script; not the model, effort or task role; no fixed allowlist')
    s.add_argument('--client', required=True, metavar='CLIENT',
                   help='concrete client, e.g. Codex-Desktop, Claude-Code, internal-subagent; not the model or effort')
    s.add_argument('--role', choices=['execution','audit'], default='execution')
    s=sub.add_parser('claim'); s.add_argument('task_id'); s.add_argument('run_id'); s.add_argument('--expected-version', type=int, required=True)
    s=sub.add_parser('report'); s.add_argument('task_id'); s.add_argument('run_id'); s.add_argument('--data', help='UTF-8 JSON progress file, not a command')
    for field in ('status','phase','progress','next-step','blocker','decision','unanswered-policy','candidate-sha','worktree'):
        s.add_argument('--'+field)
    s.add_argument('--human-input', choices=('required','not_required','unknown'), help='self-declared need for user action on this run; ask is the per-question alternative')
    s.add_argument('--mainline', help='explicitly link the plan mainline this run is working on (audit run: reviewing)')
    s.add_argument('--branch', help='also link a branch under that mainline')
    s.add_argument('--unlink', action='store_true', help="remove this run's link")
    s=sub.add_parser('ask', help="ask the user one question; the receipt's question_id is used to resolve or withdraw it")
    s.add_argument('task_id'); s.add_argument('run_id'); s.add_argument('--question', required=True)
    s.add_argument('--option', action='append', help='answer option; repeatable, at most 8'); s.add_argument('--unanswered-policy')
    s.add_argument('--mainline'); s.add_argument('--branch')
    for name, field in (('resolve', 'answer'), ('withdraw', 'reason')):
        s=sub.add_parser(name, help="close a question: --question-id names an ask; --target-run closes that run's human_input declaration")
        s.add_argument('task_id'); s.add_argument('run_id'); s.add_argument('--question-id'); s.add_argument('--target-run')
        s.add_argument('--'+field, required=True)
    s=sub.add_parser('delivery'); s.add_argument('task_id'); s.add_argument('run_id'); s.add_argument('--data', required=True)
    s=sub.add_parser('submit'); s.add_argument('file', help='full stable event JSON; preserve event_id and seq on retry')
    s=sub.add_parser('show'); s.add_argument('task_id', nargs='?')
    s=sub.add_parser('draft'); s.add_argument('task_id')
    import task_board_cli
    task_board_cli.parser(sub)
    a = p.parse_args()
    if a.config is None:
        found = default_config()
        if found is None:
            p.error('STATE.md names no project_configuration; pass --config before the command')
        a.config = str(found)
    config, runtime = settings(a)
    if a.command == 'task-board':
        result = task_board_cli.run(a, config, runtime, request, health)
        print(packed(result) if a.plan_command in {'summary','history'} else json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code(result)
    if a.command == 'serve':
        serve(config, runtime, a.port or config.get('port',8765)); return 0
    if a.command == 'start':
        result = start(a, config, runtime)
        result['outbox'] = {'status': 'NOT_REPLAYED', 'note': 'start does not replay pending business events; use explicit scoped flush'}
    elif a.command == 'status':
        try:
            result = dict(health(runtime), status='RUNNING')
        except (OSError, RuntimeError, ValueError, KeyError):
            result = {'status': 'STOPPED_OR_UNVERIFIED'}
        result['pending_outbox_events'] = len(list((runtime/'outbox').glob('*/*.json')))
        result['held_unregistered_events'] = len(list((runtime/'held').glob('*.json')))
    elif a.command == 'pending':
        result = pending(runtime, config)
    elif a.command == 'quarantine':
        result = quarantine(runtime, config, a.event_id, a.expected_sha256, a.reason)
    elif a.command == 'stop':
        info = health(runtime)
        result = request(runtime, '/api/stop', {'instance_id': info['instance_id']})
        if not isinstance(result, dict) or result.get('instance_id') != info['instance_id'] or result.get('stopping') is not True:
            raise RuntimeError('STOP_UNCONFIRMED: service receipt identity mismatch')
        for _ in range(30):
            time.sleep(.1)
            try:
                health(runtime)
            except (OSError, RuntimeError, ValueError, KeyError):
                result['status'] = 'STOPPED'; break
        else:
            result['status'] = 'STOP_UNCONFIRMED'
    elif a.command == 'flush':
        result = flush(runtime, config, a.task)
    elif a.command in {'show', 'draft'}:
        from urllib.parse import quote
        result = request(runtime, '/api/task?id='+quote(a.task_id)) if a.task_id else request(runtime, '/api/state')
        if a.command == 'draft':
            print('# Stage handoff draft (for the owning agent to check; not an approval)\n' + packed(result)); return 0
    elif a.command == 'import-snapshots':
        result = []
        for s in config.get('snapshots', []):
            source_path = Path(Protocol(config).path_check(s['path']))
            if not source_path.is_relative_to(Path(config['project_root']).resolve()) or source_path.stat().st_size > 2097152:
                raise ValueError('snapshot source outside root or exceeds 2 MiB')
            raw = source_path.read_bytes(); content=raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
            start_at=content.index(s['heading']); end=content.find('\n## ',start_at+len(s['heading']))
            excerpt=content[start_at:end if end >= 0 else None].strip()
            if len(excerpt)>8000:
                raise ValueError('snapshot section exceeds 8000 chars')
            task=envelope('ensure_task',s['task_id'],{'title':s['title'],'workstream':s['workstream'],'project':config['project']})
            if s.get('source_at'):
                task['occurred_at'] = s['source_at']
                task['event_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'agent-board-ensure-task:' + s['task_id'] + ':'
                                                   + task['occurred_at'] + ':' + packed(task['data'])))
            result.append(send(runtime, task, config))
            event=envelope('snapshot',s['task_id'],{'fields':s['fields'],'excerpt':excerpt},source={'kind':'document_snapshot','path':str(source_path),'source_at':s.get('source_at'),'read_at':now(),'sha256':hashlib.sha256(raw).hexdigest(),'label':s['heading']})
            result.append(send(runtime,event,config))
    else:
        board_note = plan_note = None
        try:
            if a.command == 'submit':
                if Path(a.file).stat().st_size > 65536: raise ValueError('event too large')
                event=read_json(a.file)
                # Same guard as the short commands; a stable retry keeps its event_id and seq.
                if isinstance(event, dict) and event.get('op') in {'claim', 'progress', 'delivery', 'ask', 'resolve', 'withdraw'}:
                    check_registration(runtime, ident(event.get('task_id')), ident(event.get('run_id')))
                    item = event['data'].get('item') if isinstance(event.get('data'), dict) else None
                    if event['op'] in {'progress', 'ask'} and isinstance(item, dict):
                        check_item(event['task_id'], item, plan_in_force(runtime, event['task_id']))
                elif isinstance(event, dict) and event.get('op') in {'run', 'task_meta', 'snapshot', 'activity'}:
                    check_registration(runtime, ident(event.get('task_id')))
            elif a.command == 'task':
                name, board_note = board_workstream(runtime, a.workstream)
                event=envelope('task',a.task_id,{'title':a.title,'project':config['project'],'workstream':name})
            elif a.command == 'retag':
                data={'reason':a.reason}
                if a.title is not None: data['title']=a.title
                if a.workstream is not None: data['workstream'], board_note = board_workstream(runtime, a.workstream)
                event=envelope('task_meta',a.task_id,data)
                check_registration(runtime, a.task_id)
            else:
                if a.command == 'run': data={'role':a.role,'agent':a.agent,'client':a.client}
                elif a.command == 'claim': data={'expected_version':a.expected_version}
                elif a.command in {'report', 'ask'}:
                    if a.branch and not a.mainline:
                        raise ValueError('--branch requires --mainline')
                    item = {'mainline': a.mainline, **({'branch': a.branch} if a.branch else {})} if a.mainline else None
                    if a.command == 'ask':
                        data = {'question': a.question, **({'options': a.option} if a.option else {}),
                                **({'item': item} if item else {}),
                                **({'unanswered_policy': a.unanswered_policy} if a.unanswered_policy is not None else {})}
                    else:
                        options = {f:getattr(a,f) for f in ('status','phase','progress','next_step','blocker','decision','unanswered_policy','candidate_sha','worktree','human_input') if getattr(a,f) is not None}
                        if a.unlink and item:
                            raise ValueError('use either --mainline/--branch or --unlink')
                        if item or a.unlink:
                            options['item'] = item
                        if a.data and options:
                            raise ValueError('use either --data or short fields, not both')
                        data=read_json(a.data) if a.data else options
                        if not data: raise ValueError('at least one progress field required')
                elif a.command in {'resolve', 'withdraw'}:
                    if (a.question_id is None) == (a.target_run is None):
                        raise ValueError(f'{a.command} requires exactly one of --question-id or --target-run')
                    field = 'answer' if a.command == 'resolve' else 'reason'
                    data = {'question_id': a.question_id} if a.question_id is not None else {'target_run': a.target_run}
                    data[field] = getattr(a, field)
                else: data=read_json(a.data)
                event=envelope('progress' if a.command=='report' else a.command,a.task_id,data,a.run_id,1)
                Protocol(config).validate(event)  # before sequence files or any runtime directories
                check_registration(runtime, a.task_id, None if a.command == 'run' else a.run_id)
                linked = event['data'].get('item') if a.command in {'report', 'ask'} else None
                if linked or (a.command == 'report' and 'next_step' in event['data']):
                    plan = plan_in_force(runtime, a.task_id)
                    if linked:
                        check_item(a.task_id, linked, plan)
                    if plan and plan.get('exists') and 'next_step' in event['data'] and task_board.plan_next_step(plan['board']):
                        plan_note = 'The task next step comes from the plan focus; this field is only a note on this run; update the plan with task-board progress'
                event['seq'] = sequence(runtime,a.run_id)
        except RegistrationUnverified as error:
            # A short command has no sequence yet; a submitted event keeps its own.
            result = hold(runtime, event if a.command == 'submit' else dict(event, seq=None), str(error), a.command)
        else:
            result=send(runtime,event,config)
            if a.command == 'ask' and result.get('status') in {'RECORDED', 'QUEUED_NOT_RECORDED'}:
                result['question_id'] = event['event_id']  # Name it in resolve/withdraw, even while queued.
            if plan_note:
                result['note'] = plan_note
        if board_note:
            result['workstream'] = board_note
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if a.command in {'pending', 'quarantine'} else exit_code(result)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, TypeError, KeyError) as e:
        print(json.dumps({'status':'ERROR','error':str(e)[:600]}, ensure_ascii=False)); raise SystemExit(1)
