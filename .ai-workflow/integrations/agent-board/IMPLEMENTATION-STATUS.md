# Implementation status

The agent board is a local, stdlib-only Python service with a read-mostly web page:

- `board.py` is the CLI (registration, reports, handoff, offline queue, start/stop/status).
- `model.py` holds the SQLite store, validation, display rules and the offline queue.
- `server.py` serves the local page and its JSON endpoints on a loopback port.
- `task_board.py` / `task_board_cli.py` implement the on-demand task board (plans, freezes, branches).
- `web/` holds the page (`index.html`, `app.js`, `views.js`, `task-board.js`, `style.css`).

Tests in `tests/` are author checks on isolated temporary data:

- `python -B -m unittest discover -s tests -v` runs the unit and process tests.
- `node tests/browser_*_checks.mjs <output-dir>` runs the browser checks. They need
  `AGENT_BOARD_PLAYWRIGHT_MODULE` (a file URL to a `playwright-core` `index.mjs`), and optionally
  `AGENT_BOARD_CHROME` (a browser executable) and `AGENT_BOARD_PYTHON` (a Python interpreter).
  Each check starts its own fixture on a free ephemeral port and stops it when done.
- `python tests/verify.py <output-dir>` records the author checks and their exit codes.

Author checks are not an independent audit or user acceptance. Project-specific settings live in the
project configuration (see `projects/example/agent-board.json`).
