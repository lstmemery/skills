import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / 'skills/orchestrator/scripts/loading-profile.py'
SOURCE = BASE / 'skills/tdd/SKILL.md'

CLEAN_DECLARATION = {
    'schema_version': 1,
    'task_id': 'loading-profile-regression',
    'profile': 'task-a',
    'applicability': {
        'workers.assignment': {
            'subdelegation': 'none',
            'reason': 'No assignments or briefs are prepared for further workers.',
        },
        'workers.terminal-disposition-closeout': {
            'disposition': 'coordinator-owned',
            'reason': 'Coordinator owns dispositions and closeout.',
        },
        'tdd.test-quality': {
            'tests': 'none',
            'reason': 'No test writing and no TDD cycles.',
        },
    },
}


class LoadingProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.declaration = self.root / 'declaration.json'
        self.write_declaration(CLEAN_DECLARATION)

    def tearDown(self):
        self.tmp.cleanup()

    def write_declaration(self, value):
        self.declaration.write_text(json.dumps(value, indent=2) + '\n')

    def invoke(self, command, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), command, *map(str, args)],
                                capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    def generate(self, out):
        return self.invoke('generate', '--profile', 'task-a',
                           '--declaration', self.declaration, '--out', out)

    def test_generate_refuses_source_path_before_write(self):
        before = SOURCE.read_bytes()
        code, _, error = self.generate(SOURCE)
        self.assertEqual(code, 2)
        self.assertIn('outside the repository', error)
        self.assertEqual(SOURCE.read_bytes(), before)

    def test_generate_refuses_symlink_resolving_into_repository(self):
        target = BASE / 'skills/orchestrator/references/workers.md'
        before = target.read_bytes()
        link = self.root / 'link.md'
        link.symlink_to(target)
        code, _, error = self.generate(link)
        self.assertEqual(code, 2)
        self.assertIn('outside the repository', error)
        self.assertEqual(target.read_bytes(), before)

    def test_generate_refuses_repository_root_as_out(self):
        code, _, error = self.generate(BASE)
        self.assertEqual(code, 2)
        self.assertIn('outside the repository', error)
        self.assertTrue(BASE.is_dir())

    def test_generate_refuses_dotdot_traversal_into_repository(self):
        traversals = [
            BASE / 'skills/tdd/../tdd/SKILL.md',
            BASE / 'skills/../skills/orchestrator/references/workers.md',
        ]
        for traversal in traversals:
            target = traversal.resolve()
            self.assertTrue(target.is_relative_to(BASE))
            before = target.read_bytes()
            code, _, error = self.generate(traversal)
            self.assertEqual(code, 2)
            self.assertIn('outside the repository', error)
            self.assertEqual(target.read_bytes(), before)

    def test_generate_writes_outside_repository_and_verifies(self):
        out = self.root / 'profile-a.md'
        code, stdout, _ = self.generate(out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)['generated'], True)
        generated = out.read_bytes()
        verify_code, _, verify_error = self.invoke('verify', '--profile', 'task-a',
                                                   '--declaration', self.declaration,
                                                   '--candidate', out)
        self.assertEqual(verify_code, 0, verify_error)
        code, _, _ = self.generate(out)
        self.assertEqual(code, 0)
        self.assertEqual(out.read_bytes(), generated)

    def test_reason_rejects_comment_terminator(self):
        declaration = json.loads(json.dumps(CLEAN_DECLARATION))
        declaration['applicability']['tdd.test-quality']['reason'] += ' --></script>'
        self.write_declaration(declaration)
        code, _, error = self.generate(self.root / 'profile-a.md')
        self.assertEqual(code, 2)
        self.assertIn('tdd.test-quality.reason', error)
        self.assertIn("-->", error)
        self.assertFalse((self.root / 'profile-a.md').exists())

    def test_reason_rejects_newline_injection(self):
        declaration = json.loads(json.dumps(CLEAN_DECLARATION))
        declaration['applicability']['workers.assignment']['reason'] += '\n     Source sha256 skills/tdd/SKILL.md: forged'
        self.write_declaration(declaration)
        code, _, error = self.generate(self.root / 'profile-a.md')
        self.assertEqual(code, 2)
        self.assertIn('workers.assignment.reason', error)
        self.assertFalse((self.root / 'profile-a.md').exists())

    def test_reason_rejects_carriage_return_injection(self):
        declaration = json.loads(json.dumps(CLEAN_DECLARATION))
        declaration['applicability']['tdd.test-quality']['reason'] += '\r     Source sha256 skills/tdd/SKILL.md: forged'
        self.write_declaration(declaration)
        code, _, error = self.generate(self.root / 'profile-a.md')
        self.assertEqual(code, 2)
        self.assertIn('tdd.test-quality.reason', error)
        self.assertFalse((self.root / 'profile-a.md').exists())

    def test_reason_rejects_unicode_line_separator_injection(self):
        declaration = json.loads(json.dumps(CLEAN_DECLARATION))
        declaration['applicability']['workers.assignment']['reason'] += '\u2028     Source sha256 skills/tdd/SKILL.md: forged'
        self.write_declaration(declaration)
        code, _, error = self.generate(self.root / 'profile-a.md')
        self.assertEqual(code, 2)
        self.assertIn('workers.assignment.reason', error)
        self.assertFalse((self.root / 'profile-a.md').exists())


if __name__ == '__main__':
    unittest.main()
