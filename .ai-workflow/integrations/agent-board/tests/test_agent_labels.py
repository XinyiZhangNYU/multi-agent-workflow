"""Label guidance is only guidance: new tool families keep using the same protocol."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import board
from model import Protocol, Store


class AgentLabelTests(unittest.TestCase):
    def test_help_explains_tool_names_without_an_enum(self):
        result = subprocess.run([sys.executable, '-B', str(Path(board.__file__)), 'run', '--help'],
                                capture_output=True, encoding='utf-8', timeout=10,
                                env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        for phrase in ('Claude', 'Codex', 'Gemini', 'OpenCode', 'effort', 'allowlist'):
            self.assertIn(phrase, result.stdout)

    def test_cli_keeps_new_tools_and_script_labels_offline(self):
        with tempfile.TemporaryDirectory(prefix='board-label-guidance-') as tmp:
            root = Path(tmp)
            (root / '.ai-workflow').mkdir()
            (root / '.ai-workflow/WORKFLOW.md').write_text('synthetic', encoding='utf-8')
            config = root / 'config.json'
            config.write_text(json.dumps({'project_root': str(root), 'project': 'Test'}), encoding='utf-8')
            for index, label in enumerate(('Claude', 'Codex', 'Gemini', 'OpenCode', 'Script', 'FutureTool', '\u65b0\u5de5\u5177')):
                runtime = root / str(index)
                board.remember_registration(runtime, 'test')  # Offline needs a local basis for the task.
                result = subprocess.run([sys.executable, '-B', str(Path(board.__file__)),
                                         '--config', str(config), '--runtime', str(runtime),
                                         'run', 'test', 'r', '--agent', label, '--client', 'synthetic'],
                                        capture_output=True, encoding='utf-8', timeout=10,
                                        env=dict(os.environ, PYTHONUTF8='1'))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'QUEUED_NOT_RECORDED')
                queued = list((runtime / 'outbox').glob('*/*.json'))
                self.assertEqual(len(queued), 1)
                self.assertEqual(json.loads(queued[0].read_text(encoding='utf-8'))['event']['data']['agent'], label)

    def test_protocol_and_store_do_not_infer_or_reject_model_labels(self):
        # A policy violation is not a schema error: no hidden regex gate or codec.
        with tempfile.TemporaryDirectory(prefix='board-label-protocol-') as tmp:
            root = Path(tmp)
            config = {'project_root': str(root), 'project': 'Test'}
            store = Store(root / 'runtime', config)
            store.submit(board.envelope('task', 't', {'title': 'Test', 'project': 'Test', 'workstream': 'Test'}))
            for index, label in enumerate(('Gemini', 'FutureTool', 'ToolName-model-x-max')):
                event = board.envelope('run', 't', {'role': 'audit', 'agent': label, 'client': 'synthetic'}, f'r{index}', 1)
                self.assertEqual(Protocol(config).validate(event)['data']['agent'], label)
                store.submit(event)
            self.assertEqual([r['agent'] for r in store.view('t')['runs']],
                             ['Gemini', 'FutureTool', 'ToolName-model-x-max'])


if __name__ == '__main__':
    unittest.main()
