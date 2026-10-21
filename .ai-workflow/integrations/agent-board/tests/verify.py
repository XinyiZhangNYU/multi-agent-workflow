"""Record bounded author checks and their exact exit statuses.

Usage: python verify.py <output-dir> [startup-targets.json]
The startup-targets file defaults to the one beside the project configuration named in STATE.md.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

workflow = Path(__file__).resolve().parents[3]
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(workflow / 'integrations/agent-board'))
from board import default_config
targets = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else default_config(workflow).with_name('startup-targets.json')
powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
node = shutil.which('node')
web = workflow / 'integrations/agent-board/web'
checks = {'unittest': [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(workflow / 'integrations/agent-board/tests'), '-v'],
          'diff-check': ['git', '-C', str(workflow), 'diff', '--check']}
if powershell:
    checks['startup-check'] = [powershell, '-NoProfile', '-NonInteractive', '-File', str(workflow / 'bin/sync-startup-contract.ps1'),
                               '-Check', '-ConfigPath', str(targets)]
    for script in sorted((workflow / 'tests').glob('*startup-contract*.ps1')):
        checks['startup-regression-' + script.stem] = [powershell, '-NoProfile', '-NonInteractive', '-File', str(script)]
if node:
    for script in sorted(web.glob('*.js')):
        checks['javascript-syntax-' + script.stem] = [node, '--check', str(script)]
results = []
for name, command in checks.items():
    env = os.environ.copy()
    if name.startswith('startup-') and Path(powershell).name.lower() == 'powershell.exe':
        # A PowerShell 7 host's module paths otherwise shadow Windows PowerShell 5.1
        # cmdlets in child processes. Scoped to these checks; no global change.
        env['PSModulePath'] = os.path.expandvars(r'%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules')
    p = subprocess.run(command, capture_output=True, timeout=600 if name == 'unittest' else 90, cwd=workflow, env=env)
    (out / (name + '.stdout.txt')).write_bytes(p.stdout); (out / (name + '.stderr.txt')).write_bytes(p.stderr)
    results.append({'name': name, 'command': command, 'exit_code': p.returncode})
for f in (workflow / 'integrations/agent-board').rglob('*.py'):
    ast.parse(f.read_text(encoding='utf-8-sig'), filename=str(f))
for f in ['runbooks/AGENT-BOARD.md', 'runbooks/TASK-BOARD.md', 'projects/example/agent-board.json',
          'integrations/agent-board/web/index.html', 'integrations/agent-board/web/app.js', 'integrations/agent-board/web/style.css']:
    assert (workflow / f).is_file(), f
skipped = [name for name, tool in (('powershell checks', powershell), ('javascript syntax', node)) if not tool]
result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': results, 'skipped': skipped, 'python_ast': 'PASS',
          'required_references': 'PASS', 'scope': 'author focused checks only; not independent acceptance'}
(out / 'checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(any(r['exit_code'] for r in results))
