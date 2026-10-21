"""Loopback-only HTTP adapter; no command or file browsing endpoints."""
import hmac
import json
import logging
import logging.handlers
import os
import secrets
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from model import Conflict, NotFound, Store, now, packed
import task_board


class FileLock:
    def __init__(self, path, timeout=2):
        self.path = Path(path)
        self.timeout = timeout

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, 'a+b')
        # Locking byte zero also works for an empty Windows file; never append on each use.
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.f.close()
                    raise RuntimeError('another local board operation holds the lock; retry original event')
                time.sleep(.025)
        return self

    def __exit__(self, *args):
        self.f.close()


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    temp.write_text(packed(data), encoding='utf-8')
    os.replace(temp, path)


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, address, store, token):
        self.store, self.token = store, token
        task_board.initialize(store)
        self.instance = secrets.token_hex(16)
        # Embedded in the served page only: proves a close came from this page
        # (anti-CSRF), not a write credential. Rotates with every start.
        self.page_token = secrets.token_urlsafe(24)
        self.started = now()
        self.outbox_cache = (None, None)
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(address, Handler)

    def outbox_summary(self):
        """Unconfirmed local events may not be on the board; say so on every read.

        A file that cannot be read or is not a queue envelope is counted as
        unreadable: never fatal to the page, never removed, never cached.
        """
        runtime = self.store.runtime
        files = []
        for f in sorted((runtime / 'outbox').glob('*/*.json')) + sorted((runtime / 'task-board-outbox').glob('*.json')):
            try:
                stat = f.stat()
            except FileNotFoundError:
                continue  # Flushed meanwhile.
            files.append((f, stat.st_mtime_ns, stat.st_size))
        key = tuple((str(f), mtime, size) for f, mtime, size in files)
        if self.outbox_cache[0] == key:
            return self.outbox_cache[1]
        by_task, plans, unreadable = {}, 0, 0
        for f, _, _ in files:
            try:
                item = json.loads(f.read_text(encoding='utf-8'))
                if isinstance(item, dict) and 'request' in item:
                    plans += 1
                    continue
                e = item['event']
                if (type(item['order']) is not int or not isinstance(e, dict)
                        or not all(isinstance(e.get(k), str) for k in ('task_id', 'op', 'occurred_at'))):
                    raise ValueError('not a queue envelope')
            except FileNotFoundError:
                continue  # Deleted by a concurrent flush.
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
                continue
            entry = by_task.setdefault(e['task_id'], {'count': 0, 'head_order': None, 'head_op': None, 'head_run_id': None, 'oldest_at': None})
            entry['count'] += 1
            if entry['head_order'] is None or item['order'] < entry['head_order']:
                entry.update(head_order=item['order'], head_op=e['op'], head_run_id=e.get('run_id'))
            if entry['oldest_at'] is None or e['occurred_at'] < entry['oldest_at']:
                entry['oldest_at'] = e['occurred_at']
        value = {'pending': sum(v['count'] for v in by_task.values()), 'by_task': by_task,
                 'task_board_pending': plans, 'unreadable': unreadable}
        if not unreadable:
            self.outbox_cache = (key, value)  # A failed read is retried next time.
        return value

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'AgentBoard/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(4)

    def log_message(self, fmt, *args):
        # Do not log raw URLs, headers, payload, source text or credentials.
        pass

    def reply(self, code, obj, mime='application/json; charset=utf-8'):
        payload = obj if isinstance(obj, bytes) else packed(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def guard(self, write=False):
        port = self.server.server_port
        if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            self.reply(403, {'error': 'Host rejected'}); return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers['Host']:
            self.reply(403, {'error': 'Origin rejected'}); return False
        if write and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.token):
            self.reply(401, {'error': 'local write credential required'}); return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        uri = urlsplit(self.path)
        try:
            expected = self.headers.get('X-Agent-Board-Instance')
            if uri.path in {'/api/health', '/api/state', '/api/feed', '/api/task', '/api/task-boards', '/api/task-board', '/api/workstreams'} and expected is not None and expected != self.server.instance:
                self.reply(409, {'error': 'instance identity mismatch; verify runtime',
                                 'service': 'agent-board-v1', 'instance_id': self.server.instance})
                return
            if uri.path == '/api/health':
                self.reply(200, {'service': 'agent-board-v1', 'instance_id': self.server.instance,
                                 'pid': os.getpid(), 'started_at': self.server.started, 'observed_at': now()})
            elif uri.path == '/api/workstreams':
                if uri.query:
                    raise ValueError('no list query fields')
                self.reply(200, {'service': 'agent-board-v1', 'instance_id': self.server.instance,
                                 'workstreams': self.server.store.active_workstreams()})
            elif uri.path == '/api/task-boards':
                if uri.query:
                    raise ValueError('no list query fields')
                self.reply(200, {'service': 'agent-board-v1', 'instance_id': self.server.instance,
                                 'boards': task_board.listing(self.server.store)})
            elif uri.path == '/api/task-board':
                query = parse_qs(uri.query, keep_blank_values=True)
                if set(query) - {'task_id', 'summary', 'mainline', 'section', 'offset', 'version'} or any(len(v) != 1 for v in query.values()) or query.get('summary', ['0'])[0] not in {'0', '1'}:
                    raise ValueError('invalid task-board query')
                value = task_board.read(self.server.store, query.get('task_id', [''])[0])
                if 'version' in query and str(value['version']) != query['version'][0]:
                    self.reply(409, {'error': 'plan version changed; reread summary', 'service': 'agent-board-v1', 'instance_id': self.server.instance}); return
                budget = task_board.limit(self.server.store.config, 'max_task_board_summary_bytes', task_board.DEFAULT_SUMMARY_BYTES)
                offset = int(query.get('offset', ['0'])[0])
                if query.get('section', [''])[0] == 'history':
                    value = task_board.history_value(value, query.get('mainline', [None])[0], offset, budget)
                elif 'section' in query:
                    raise ValueError('invalid task-board section')
                elif query.get('summary', ['0'])[0] == '1':
                    value = task_board.focus_summary(value, query.get('mainline', [None])[0], offset, budget)
                elif 'mainline' in query:
                    raise ValueError('mainline requires summary=1')
                elif len(packed(value).encode()) > task_board.limit(self.server.store.config, 'max_task_board_bytes', task_board.DEFAULT_PLAN_BYTES) + task_board.CLOSE_RESERVE_BYTES + 8192:
                    raise ValueError('full read exceeds configured plan limit')
                self.reply(200, dict(value, service='agent-board-v1', instance_id=self.server.instance))
            elif uri.path == '/api/state':
                self.reply(200, dict(task_board.attach_overviews(self.server.store, self.server.store.view()), service='agent-board-v1',
                                     instance_id=self.server.instance, outbox=self.server.outbox_summary()))
            elif uri.path == '/api/feed':
                query = parse_qs(uri.query)
                if set(query) - {'limit'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError('invalid feed query')
                limit = int(query.get('limit', ['200'])[0])
                if not 1 <= limit <= 500:
                    raise ValueError('feed limit must be 1..500')
                self.reply(200, {'service': 'agent-board-v1', 'instance_id': self.server.instance,
                                 'events': self.server.store.feed(limit)})
            elif uri.path == '/api/task':
                task = parse_qs(uri.query).get('id', [''])[0]
                if not task:
                    raise ValueError('task id required')
                self.reply(200, dict(task_board.attach_overviews(self.server.store, self.server.store.view(task)), service='agent-board-v1',
                                     instance_id=self.server.instance))
            elif uri.path in {'/', '/app.js', '/views.js', '/style.css', '/task-board.js'}:
                names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/views.js': ('views.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'), '/task-board.js': ('task-board.js', 'text/javascript')}
                name, mime = names[uri.path]
                content = (Path(__file__).parent / 'web' / name).read_bytes()
                if name == 'index.html':
                    content = content.replace(b'__PAGE_TOKEN__', self.server.page_token.encode())
                self.reply(200, content, mime + '; charset=utf-8')
            else:
                self.reply(404, {'error': 'not found'})
        except NotFound as e:
            self.reply(404, {'error': str(e), 'service': 'agent-board-v1', 'instance_id': self.server.instance})
        except ValueError as e:
            self.reply(400, {'error': str(e)})
        except Exception:
            logging.getLogger('board').error('read_failed')
            self.reply(500, {'error': 'local read failed'})

    def user_close(self):
        """The user's one web write: mark an open request handled. Never answers or approves."""
        host = self.headers.get('Host')
        if (self.headers.get('Origin') != 'http://' + host
                or not hmac.compare_digest(self.headers.get('X-Board-Page-Token', ''), self.server.page_token)
                or self.headers.get('X-Agent-Board-Instance') != self.server.instance):
            self.reply(403, {'error': 'page expired or not this board; reload the page'}); return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise ValueError('JSON Content-Length required')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise ValueError('body limit 4096 bytes')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or set(data) - {'task_id', 'question_id', 'target_run'}:
                raise ValueError('task_id plus question_id or target_run')
            target = {k: data[k] for k in ('question_id', 'target_run') if k in data}
            event = {'event_id': 'user-close-' + uuid.uuid4().hex, 'task_id': data.get('task_id'), 'op': 'user_close',
                     'occurred_at': now(), 'source': {'kind': 'user_web', 'label': 'Board page'}, 'data': target}
            result = self.server.store.submit(event, user=True)
            self.reply(200, dict(result, service='agent-board-v1', instance_id=self.server.instance))
        except Conflict as e:
            self.reply(409, {'error': str(e), 'service': 'agent-board-v1', 'instance_id': self.server.instance})
        except (ValueError, TypeError, KeyError, UnicodeError, socket.timeout):
            self.reply(400, {'error': 'invalid close request', 'service': 'agent-board-v1', 'instance_id': self.server.instance})

    def do_POST(self):
        if self.path == '/api/user-close':
            if self.guard():
                self.user_close()
            return
        if not self.guard(True):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise ValueError('JSON Content-Length required')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('body limit 65536 bytes')
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('incomplete body')
            def reject_constant(v):
                raise ValueError('nonfinite JSON')
            data = json.loads(body, parse_constant=reject_constant)
            if self.path == '/api/task-board-events':
                if self.headers.get('X-Agent-Board-Instance') != self.server.instance:
                    raise Conflict('instance identity mismatch; retain task-board request')
                result = task_board.apply(self.server.store, data)
                self.reply(200, dict(result, service='agent-board-v1', instance_id=self.server.instance))
            elif self.path == '/api/events':
                if self.headers.get('X-Agent-Board-Instance') != self.server.instance:
                    raise Conflict('instance identity mismatch; retain original event')
                result = self.server.store.submit(data)
                self.reply(200, dict(result, service='agent-board-v1', instance_id=self.server.instance,
                                     recorded=True))
            elif self.path == '/api/stop':
                if data != {'instance_id': self.server.instance}:
                    raise Conflict('instance identity mismatch; refusing stop')
                self.reply(200, {'stopping': True, 'instance_id': self.server.instance})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.reply(404, {'error': 'not found'})
        except Conflict as e:
            self.reply(409, {'error': str(e), 'service': 'agent-board-v1',
                             'instance_id': self.server.instance,
                             'event_id': data.get('event_id') if self.path in {'/api/events', '/api/task-board-events'} and isinstance(data, dict) else None})
        except (ValueError, TypeError, KeyError, UnicodeError, socket.timeout):
            self.reply(400, {'error': 'invalid event or request; verify schema/limits', 'service': 'agent-board-v1',
                             'instance_id': self.server.instance,
                             'event_id': data.get('event_id') if isinstance(locals().get('data'), dict) else None})
        except Exception:
            logging.getLogger('board').error('write_failed')
            self.reply(503, {'error': 'write unavailable; retain original event for retry'})


def serve(config, runtime, port):
    runtime = Path(runtime); runtime.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('board'); logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(runtime / 'service.log', maxBytes=262144, backupCount=2, encoding='utf-8')
    logger.addHandler(handler)
    with FileLock(runtime / 'service.lock'):
        token_path = runtime / 'write-token'
        if not token_path.exists():
            with open(token_path, 'x', encoding='ascii') as f:
                f.write(secrets.token_urlsafe(32))
            os.chmod(token_path, 0o600)
        token = token_path.read_text(encoding='ascii').strip()
        if len(token) < 32:
            raise ValueError('invalid local credential file')
        store = Store(runtime, config)
        server = BoardServer(('127.0.0.1', port), store, token)
        info = {'service': 'agent-board-v1', 'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid(),
                'instance_id': server.instance, 'started_at': server.started, 'source_path': str(Path(__file__).resolve())}
        atomic_json(runtime / 'instance.json', info)
        logger.info('service_started %s', server.started)
        try:
            server.serve_forever(poll_interval=0.3)
        finally:
            server.server_close()
            logger.info('service_stopped %s', now())
