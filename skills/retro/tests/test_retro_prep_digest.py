"""Tests for the retro-prep digest checks (skills/retro/scripts/retro_prep_digest.py).

All fixtures are synthetic; no real transcripts are read.
"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[3]
SCRIPT = BASE / 'skills/retro/scripts/retro_prep_digest.py'
SPEC = importlib.util.spec_from_file_location('retro_prep_digest', SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def make_index(tmp, rows):
    """rows: list of (harness, session_id, start, end, role)."""
    lines = [
        '# index',
        '',
        '| Harness | Session ID | Start | End in window | Size | '
        'First user prompt (redacted) | Role |',
        '|---|---|---|---|---:|---|---|',
    ]
    for harness, sid, start, end, role in rows:
        lines.append(f'| {harness} | `{sid}` | {start} | {end} | 1,000 B | '
                     f'first prompt | {role} |')
    (tmp / 'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_digest(tmp, harness, sid, *, start, end, entries=(), user_turns=(),
                span_header=True, timeline=True):
    """entries: list of 'HH:MM:SS'; user_turns: list of body strings."""
    parts = [f'# {harness} — session', '',
             f'- Session ID: `{sid}`', '- Role: coordinator', '']
    if span_header:
        parts.append(f'- Session span: {start} to {end}')
    parts += ['', '## Timeline', '']
    if timeline:
        for i, stamp in enumerate(entries):
            parts += [f'### {stamp} PDT — User turn (verbatim, redacted)', '',
                      '```text', user_turns[i] if i < len(user_turns) else 'a turn',
                      '```', '']
    (tmp / 'sessions').mkdir(exist_ok=True)
    path = tmp / 'sessions' / f'{harness}-{sid}.md'
    path.write_text('\n'.join(parts), encoding='utf-8')
    return path


class DeriveRoleTests(unittest.TestCase):
    def test_orchestrator_invocation_is_coordinator(self):
        role, basis = MOD.derive_role(['<command-name>/clear</command-name>',
                                       'You are the /orchestrator. Land #1132'])
        self.assertEqual(('coordinator', 'orchestrator-invocation'), (role, basis))

    def test_plain_first_prompt_is_interactive(self):
        self.assertEqual(('interactive', 'no-invocation-in-user-turns'),
                         MOD.derive_role(['What time is it?']))

    def test_worker_dispatch(self):
        role, basis = MOD.derive_role(
            ['You are a worker. Read and execute your brief: /tmp/brief.md'])
        self.assertEqual(('worker', 'worker-dispatch'), (role, basis))

    def test_code_reviewer_is_worker(self):
        self.assertEqual('worker', MOD.derive_role(
            ['You are a code reviewer. Read and execute /tmp/review/brief.md.'])[0])

    def test_coordinator_wins_over_worker_dispatch(self):
        role, _ = MOD.derive_role([
            'You are a worker. Read and execute your brief: /tmp/b.md',
            'You are the /orchestrator. Read this handoff: ~/HANDOFF.md'])
        self.assertEqual('coordinator', role)

    def test_no_user_turns(self):
        self.assertEqual(('interactive', 'no-user-turns-in-digest'),
                         MOD.derive_role([]))


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sid_coord = '7866ceab-c401-443d-a9d1-8490b1915589'
        self.sid_inter = 'c577053b-c172-40c8-93ce-cf84c5554ad2'
        self.sid_worker = '01a0fb75-5524-7cc0-9950-ee0d6c5f7c95'

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = MOD.main(['verify', '--corpus', str(self.root), *extra])
        return code, out.getvalue()

    def build_index(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 14:27:30 PDT', '2026-10-02 15:07:55 PDT', 'coordinator'),
            ('claude-code', self.sid_inter,
             '2026-10-02 03:46:16 PDT', '2026-10-02 03:47:28 PDT', 'interactive'),
            ('codex', self.sid_worker,
             '2026-10-02 00:12:45 PDT', '2026-10-02 00:23:06 PDT', 'worker'),
        ])

    def test_complete_corpus_passes_and_reports_coverage(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['14:27:34', '15:07:10'],
                    user_turns=['You are the /orchestrator. Land #1132', 'go on'])
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:47:20'], user_turns=['Does it have cruise control?'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:00'],
                    user_turns=['You are a worker. Read and execute your brief: /b.md'])
        code, out = self.verify()
        self.assertEqual(0, code, out)
        self.assertIn('`7866ceab-c40`', out)
        self.assertIn('ok: 3', out)
        self.assertIn('failed: 0', out)

    def test_json_report_has_coverage_per_session(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['14:27:34', '15:07:10'],
                    user_turns=['You are the /orchestrator. Land #1132'])
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:47:20'], user_turns=['hello'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:00'], user_turns=['You are a worker. Execute /b.md'])
        report_path = self.root / 'coverage.json'
        code, out = self.verify('--report', str(report_path))
        self.assertEqual(0, code, out)
        payload = json.loads(report_path.read_text(encoding='utf-8'))
        by_id = {s['session_id']: s for s in payload['sessions']}
        self.assertEqual(2, by_id[self.sid_coord]['entries'])
        self.assertEqual(0.8, by_id[self.sid_coord]['coverage_gap_minutes'])
        self.assertEqual('OK', by_id[self.sid_coord]['status'])
        self.assertEqual('coordinator', by_id[self.sid_coord]['role_derived'])

    def test_truncated_digest_fails_loudly(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['14:27:34', '14:47:37'],
                    user_turns=['You are the /orchestrator. Land #1132'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('FAILED', out)
        self.assertIn('truncated', out)
        self.assertIn('20 min before', out)

    def test_span_clip_fails(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 14:47:55 PDT',
                    entries=['14:27:34', '14:47:50'],
                    user_turns=['You are the /orchestrator.'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('span clip', out)

    def test_empty_digest_fails_and_is_flagged_in_index(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=[], user_turns=[])
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:47:20'], user_turns=['hello'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:00'], user_turns=['You are a worker. Execute /b.md'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('EMPTY', out)
        self.assertIn('zero timeline entries', out)
        code, _ = self.verify('--write')
        self.assertEqual(2, code)
        index_text = (self.root / 'index.md').read_text(encoding='utf-8')
        row = [ln for ln in index_text.splitlines() if self.sid_coord in ln][0]
        self.assertIn('[empty-digest]', row)

    def test_coordinator_mislabeled_interactive_is_reported_and_written(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:00'],
                    user_turns=['<command-name>/clear</command-name>',
                                'You are the /orchestrator. Land #1132, #1131'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('does not match orchestrator-invocation', out)
        code, _ = self.verify('--write')
        self.assertEqual(2, code)  # empty-digest/role issues remain until rebuild
        index_text = (self.root / 'index.md').read_text(encoding='utf-8')
        row = [ln for ln in index_text.splitlines() if self.sid_coord in ln][0]
        self.assertIn('coordinator (was: interactive)', row)

    def test_write_is_idempotent_on_annotated_rows(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:00'],
                    user_turns=['You are the /orchestrator. Land #1132'])
        self.verify('--write')
        self.verify('--write')
        row = [ln for ln in (self.root / 'index.md').read_text(encoding='utf-8').splitlines()
               if self.sid_coord in ln][0]
        self.assertEqual(1, row.count('was:'))

    def test_missing_span_header_fails(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['15:07:00'], user_turns=['You are the /orchestrator.'],
                    span_header=False)
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn("no 'Session span", out)

    def test_missing_digest_ok_by_default_required_with_flag(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['15:07:00'], user_turns=['You are the /orchestrator.'])
        code, out = self.verify()
        self.assertEqual(0, code, out)
        self.assertIn('no digest: 2', out)
        code, out = self.verify('--require-digests')
        self.assertEqual(2, code, out)
        self.assertIn('no digest file found', out)

    def test_timeline_entry_past_end_within_grace_passes(self):
        # Session crossing midnight: last entry 00:03 belongs to the next day.
        make_index(self.root, [
            ('pi', self.sid_worker,
             '2026-10-02 23:00:00 PDT', '2026-10-03 00:05:00 PDT', 'interactive'),
        ])
        make_digest(self.root, 'pi', self.sid_worker,
                    start='2026-10-02 23:00:00 PDT', end='2026-10-03 00:05:00 PDT',
                    entries=['23:30:00', '00:03:00'], user_turns=['hello there'])
        code, out = self.verify()
        self.assertEqual(0, code, out)
        self.assertIn('ok: 1', out)

    def test_long_same_day_session_truncation_is_before_not_after(self):
        # A 12-hour session whose digest stops two minutes in must be
        # reported as truncated (before end), never rolled to the next day.
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'coordinator'),
        ])
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '02:19:00'],
                    user_turns=['You are the /orchestrator. Read this handoff'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('truncated', out)
        self.assertNotIn('after listed session end', out)

    def test_worker_mismatch_is_note_only(self):
        make_index(self.root, [
            ('codex', self.sid_worker,
             '2026-10-02 00:12:45 PDT', '2026-10-02 00:23:06 PDT', 'interactive'),
        ])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:00'],
                    user_turns=['You are a worker. Read and execute your brief: /b.md'])
        code, out = self.verify()
        self.assertEqual(0, code, out)
        self.assertIn('note: worker dispatch found', out)

    def test_grace_minutes_option(self):
        make_index(self.root, [
            ('claude-code', self.sid_inter,
             '2026-10-02 03:46:16 PDT', '2026-10-02 03:47:28 PDT', 'interactive'),
        ])
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:46:00'], user_turns=['hello'])
        code, _ = self.verify('--grace-minutes', '0')
        self.assertEqual(2, code)
        code, _ = self.verify('--grace-minutes', '5')
        self.assertEqual(0, code)

    def test_bad_index_fails_with_error(self):
        (self.root / 'index.md').write_text('# no table here\n', encoding='utf-8')
        code, out = self.verify()
        self.assertEqual(1, code)


class ParseTests(unittest.TestCase):
    def test_parse_index_row_with_pipes_in_prompt(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_index(root, [
            ('claude-code', 'abc-123',
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        # Prompt cell containing an escaped pipe must not break column mapping.
        text = (root / 'index.md').read_text(encoding='utf-8')
        text = text.replace('first prompt', 'choice a \\| choice b')
        (root / 'index.md').write_text(text, encoding='utf-8')
        rows = MOD.parse_index(root / 'index.md')
        self.assertEqual(1, len(rows))
        self.assertEqual('interactive', rows[0].role)
        self.assertEqual('choice a | choice b', rows[0].first_prompt)

    def test_parse_digest_extracts_user_turn_bodies(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = make_digest(
            root, 'claude-code', 'abc-123',
            start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
            entries=['14:27:34'], user_turns=['You are the /orchestrator.'])
        digest = MOD.parse_digest(path)
        self.assertEqual(1, len(digest.entries))
        self.assertEqual(14 * 3600 + 27 * 60 + 34, digest.entries[0][0])
        self.assertEqual(['You are the /orchestrator.'], digest.user_turn_bodies)
        self.assertEqual(15, digest.span_end.hour)


if __name__ == '__main__':
    unittest.main()
