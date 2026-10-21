"""Focused checks of the current entry points, not a client probe or an independent audit."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def read(relative):
    return (ROOT / relative).read_text(encoding='utf-8-sig')


class WorkflowEntrypoints(unittest.TestCase):
    def test_root_startup_copies_match_canonical(self):
        config = json.loads(read('projects/example/startup-targets.json'))
        self.assertEqual(config['canonical'], '../../startup-contract/STARTUP-CONTRACT.md')
        self.assertEqual({t['path'] for t in config['targets']}, {'AGENTS.md', 'CLAUDE.md'})
        canonical = (ROOT / 'startup-contract/STARTUP-CONTRACT.md').read_bytes()
        project_root = (ROOT / 'projects/example' / config['project_root']).resolve()
        self.assertEqual(project_root, REPO.resolve())
        for target in config['targets']:
            with self.subTest(path=target['path']):
                self.assertEqual((project_root / target['path']).read_bytes(), canonical)

    def test_startup_chain_routes_to_existing_files(self):
        canonical = read('startup-contract/STARTUP-CONTRACT.md')
        for relative in ('.ai-workflow/WORKFLOW.md', '.ai-workflow/README.md', '.ai-workflow/STATE.md',
                         '.ai-workflow/runbooks/AGENT-BOARD.md', '.ai-workflow/runbooks/TASK-BOARD.md'):
            with self.subTest(path=relative):
                self.assertIn(relative, canonical)
                self.assertTrue((REPO / relative).exists())
        for section in ('"Optional local progress board"', '"On-demand task board"',
                        '"Single home for each kind of content"'):
            with self.subTest(section=section):
                self.assertIn(section, ' '.join(canonical.split()))
                self.assertIn('### ' + section.strip('"') + '\n', read('WORKFLOW.md'))
        state = read('STATE.md')
        configured = re.search(r'(?m)^project_configuration: (\S+)$', state).group(1)
        self.assertTrue((ROOT / configured).exists(), configured)

    def test_current_rule_and_state_versions_match(self):
        workflow = read('WORKFLOW.md')
        state = read('STATE.md')
        self.assertEqual(re.search(r'(?m)^version: (v\d+)$', workflow).group(1),
                         re.search(r'(?m)^workflow_version: (v\d+)$', state).group(1))

    def test_observation_and_audit_contracts_remain(self):
        workflow = read('WORKFLOW.md')
        for text in ('### Optional local progress board',
                     '### On-demand task board', '## 6. Independent audit and evidence',
                     'never reads replies automatically and never sends reception or re-audit',
                     '`AUDIT_REQUEST`', '`AUDIT_RESULT`', '`AUDIT_RECEPTION`'):
            with self.subTest(text=text):
                self.assertIn(text, workflow)

    def test_relative_markdown_links_resolve(self):
        for path in sorted([*ROOT.rglob('*.md'), REPO / 'README.md']):
            if 'runtime' in path.relative_to(REPO).parts:
                continue
            for target in re.findall(r'\]\(([^)\s]+)\)', path.read_text(encoding='utf-8-sig')):
                if '://' in target or target.startswith(('#', 'mailto:')):
                    continue
                with self.subTest(source=str(path.relative_to(REPO)), target=target):
                    self.assertTrue((path.parent / target.split('#', 1)[0]).exists())

    def test_progress_board_has_no_readonly_refresh_relay(self):
        server = read('integrations/agent-board/server.py')
        self.assertNotIn('/api/progress', server)
        self.assertFalse((ROOT / 'integrations/agent-board/progress_refresh.py').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
