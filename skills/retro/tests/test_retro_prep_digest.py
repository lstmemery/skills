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
                invocation=None, span_header=True, timeline=True):
    """entries: list of 'HH:MM:SS'; user_turns: list of body strings."""
    parts = [f'# {harness} — session', '',
             f'- Session ID: `{sid}`', '- Role: coordinator', '']
    if invocation is not None:
        parts.insert(-1, f'- Invocation evidence: {invocation}')
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
                    entries=['14:27:34', '15:07:55'],
                    user_turns=['You are the /orchestrator. Land #1132', 'go on'],
                    invocation='/orchestrator')
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:47:28'], user_turns=['Does it have cruise control?'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:06'],
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
                    entries=['14:27:34', '15:07:55'],
                    user_turns=['You are the /orchestrator. Land #1132'],
                    invocation='/orchestrator')
        make_digest(self.root, 'claude-code', self.sid_inter,
                    start='2026-10-02 03:46:16 PDT', end='2026-10-02 03:47:28 PDT',
                    entries=['03:47:28'], user_turns=['hello'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:06'], user_turns=['You are a worker. Execute /b.md'])
        report_path = self.root / 'coverage.json'
        code, out = self.verify('--report', str(report_path))
        self.assertEqual(0, code, out)
        payload = json.loads(report_path.read_text(encoding='utf-8'))
        by_id = {s['session_id']: s for s in payload['sessions']}
        self.assertEqual(4, payload['version'])
        self.assertEqual(2, by_id[self.sid_coord]['entries'])
        self.assertEqual(0.0, by_id[self.sid_coord]['coverage_gap_minutes'])
        self.assertEqual('OK', by_id[self.sid_coord]['status'])
        self.assertEqual('coordinator', by_id[self.sid_coord]['role_invocation'])

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

    def test_digest_reaches_the_final_transcript_record(self):
        # Redacted transcript fixture: the final record is at 00:23:06, but
        # the digest stops one second earlier. The final record must appear.
        make_index(self.root, [
            ('codex', self.sid_worker,
             '2026-10-02 00:12:45 PDT', '2026-10-02 00:23:06 PDT', 'worker'),
        ])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:12:45', '00:23:05'],
                    user_turns=['You are a worker. Read and execute your brief: /b.md'])

        code, out = self.verify()

        self.assertEqual(2, code, out)
        self.assertIn('truncated', out)
        self.assertIn('1 second before', out)
        self.assertIn('0.017', out)
        self.assertIn('00:23:05', out)

    def test_roles_come_from_explicit_invocation_evidence_not_index_or_content(self):
        # Invocation evidence is an explicit transcript field. Neither the
        # index nor unrelated prompt content can override or supply the role.
        rows = [
            ('claude-code', self.sid_coord, '2026-10-02 02:17:22 PDT',
             '2026-10-02 14:24:18 PDT', 'worker'),
            ('claude-code', self.sid_inter, '2026-10-02 02:17:22 PDT',
             '2026-10-02 14:24:18 PDT', 'coordinator'),
            ('codex', self.sid_worker, '2026-10-02 02:17:22 PDT',
             '2026-10-02 14:24:18 PDT', 'reviewer'),
            ('codex', 'unknown-session', '2026-10-02 02:17:22 PDT',
             '2026-10-02 14:24:18 PDT', 'coordinator'),
        ]
        make_index(self.root, rows)
        transcript = [
            ('/orchestrator', 'The prompt quotes: You are a worker.'),
            ('You are a worker. Read and execute your brief: /b.md',
             'The prompt quotes: You are the /orchestrator.'),
            ('You are a code reviewer. Read review brief: /review.md',
             'Please review this implementation.'),
            (None, 'You are the /orchestrator. This is ordinary prompt content.'),
        ]
        for (harness, sid, start, end, _), (invocation, body) in zip(rows, transcript):
            make_digest(self.root, harness, sid, start=start, end=end,
                        entries=['02:17:22', '14:24:18'], user_turns=[body],
                        invocation=invocation)

        report = self.root / 'roles.json'
        code, out = self.verify('--report', str(report))

        self.assertEqual(2, code, out)  # known invocation/index disagreements
        payload = json.loads(report.read_text(encoding='utf-8'))
        by_id = {session['session_id']: session for session in payload['sessions']}
        self.assertEqual('coordinator', by_id[self.sid_coord]['role_invocation'])
        self.assertEqual('worker', by_id[self.sid_inter]['role_invocation'])
        self.assertEqual('reviewer', by_id[self.sid_worker]['role_invocation'])
        self.assertEqual('unknown', by_id['unknown-session']['role_invocation'])
        self.assertIn('does not match invocation role', out)
        self.assertIn('invocation evidence is absent or unrecognized', out)

        code, out = self.verify('--write')
        self.assertEqual(2, code, out)  # reports the pre-write role mismatches
        code, out = self.verify()
        self.assertEqual(0, code, out)
        self.assertIn('unknown', out)

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
                    entries=['03:47:28'], user_turns=['hello'])
        make_digest(self.root, 'codex', self.sid_worker,
                    start='2026-10-02 00:12:45 PDT', end='2026-10-02 00:23:06 PDT',
                    entries=['00:23:06'], user_turns=['You are a worker. Execute /b.md'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('EMPTY', out)
        self.assertIn('zero timeline entries', out)
        code, _ = self.verify('--write')
        self.assertEqual(2, code)
        index_text = (self.root / 'index.md').read_text(encoding='utf-8')
        row = [ln for ln in index_text.splitlines() if self.sid_coord in ln][0]
        self.assertIn('[empty-digest]', row)

    def test_write_preserves_invocation_role_instead_of_guessing_from_digest(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:18'],
                    user_turns=['<command-name>/clear</command-name>',
                                'You are the /orchestrator. Land #1132, #1131'],
                    invocation='/orchestrator')
        code, out = self.verify('--write')
        self.assertEqual(2, code, out)
        index_text = (self.root / 'index.md').read_text(encoding='utf-8')
        row = [ln for ln in index_text.splitlines() if self.sid_coord in ln][0]
        self.assertIn('| coordinator |', row)

    def test_write_does_not_add_role_annotations(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:18'],
                    user_turns=['hello'], invocation='/orchestrator')
        code, _ = self.verify('--write')
        self.assertEqual(2, code)
        self.verify('--write')
        row = [ln for ln in (self.root / 'index.md').read_text(encoding='utf-8').splitlines()
               if self.sid_coord in ln][0]
        self.assertIn('| coordinator |', row)
        self.assertNotIn('was:', row)

    def test_escaped_pipe_survives_verify_write_role_correction(self):
        make_index(self.root, [
            ('claude-code', self.sid_coord,
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'coordinator'),
        ])
        index_path = self.root / 'index.md'
        before = index_path.read_text(encoding='utf-8').replace(
            'first prompt', 'choice a \\| choice b').replace(
                '| coordinator |', '| coordinator [empty-digest] |')
        index_path.write_text(before, encoding='utf-8')
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:18'], user_turns=['hello'],
                    invocation='/orchestrator')

        code, out = self.verify()
        self.assertEqual(0, code, out)
        code, out = self.verify('--write')
        self.assertEqual(0, code, out)
        after_write = index_path.read_text(encoding='utf-8')
        self.assertEqual(
            before.replace('| coordinator [empty-digest] |', '| coordinator |'),
            after_write)
        self.assertIn('choice a \\| choice b', after_write)
        self.assertEqual('choice a | choice b', MOD.parse_index(index_path)[0].first_prompt)
        code, out = self.verify()
        self.assertEqual(0, code, out)

    def test_missing_span_header_fails(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['15:07:55'], user_turns=['You are the /orchestrator.'],
                    span_header=False)
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn("no 'Session span", out)

    def test_missing_digest_fails_by_default_opt_out_with_flag(self):
        self.build_index()
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
                    entries=['15:07:55'], user_turns=['You are the /orchestrator.'])
        code, out = self.verify()
        self.assertEqual(2, code, out)
        self.assertIn('no digest file found', out)
        self.assertIn('no digest: 2', out)
        code, out = self.verify('--allow-missing-digests')
        self.assertEqual(0, code, out)
        self.assertIn('no digest: 2', out)

    def test_final_timeline_entry_crossing_midnight_matches_session_end(self):
        # Session crossing midnight: the final record is at 00:05 next day.
        make_index(self.root, [
            ('pi', self.sid_worker,
             '2026-10-02 23:00:00 PDT', '2026-10-03 00:05:00 PDT', 'interactive'),
        ])
        make_digest(self.root, 'pi', self.sid_worker,
                    start='2026-10-02 23:00:00 PDT', end='2026-10-03 00:05:00 PDT',
                    entries=['23:30:00', '00:05:00'], user_turns=['hello there'])
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

    def test_write_targets_role_column_when_table_has_trailing_column(self):
        lines = [
            '# index', '',
            '| Harness | Session ID | Start | End in window | Size | '
            'First user prompt (redacted) | Role | Notes |',
            '|---|---|---|---|---:|---|---|---|',
            f'| claude-code | `{self.sid_coord}` | 2026-10-02 02:17:22 PDT | '
            '2026-10-02 14:24:18 PDT | 1,000 B | first prompt | interactive | keep me |',
        ]
        (self.root / 'index.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        make_digest(self.root, 'claude-code', self.sid_coord,
                    start='2026-10-02 02:17:22 PDT', end='2026-10-02 14:24:18 PDT',
                    entries=['02:17:30', '14:24:18'],
                    user_turns=['You are the /orchestrator. Land #1132'],
                    invocation='/orchestrator')
        code, out = self.verify('--write')
        self.assertEqual(2, code, out)
        row = [ln for ln in (self.root / 'index.md').read_text(encoding='utf-8').splitlines()
               if self.sid_coord in ln][0]
        cells = MOD.split_row(row.strip().strip('|'))
        self.assertEqual('coordinator', cells[6])
        self.assertEqual('keep me', cells[7])
        code, out = self.verify()
        self.assertEqual(0, code, out)

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

    def test_overlong_index_row_fails(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        make_index(root, [
            ('claude-code', 'abc-123',
             '2026-10-02 02:17:22 PDT', '2026-10-02 14:24:18 PDT', 'interactive'),
        ])
        # An unescaped pipe in the prompt cell adds a column; parse must
        # refuse instead of silently shifting Role/End onto other cells.
        text = (root / 'index.md').read_text(encoding='utf-8')
        text = text.replace('first prompt', 'choice a | choice b')
        (root / 'index.md').write_text(text, encoding='utf-8')
        with self.assertRaises(ValueError) as ctx:
            MOD.parse_index(root / 'index.md')
        self.assertIn('cells, expected', str(ctx.exception))

    def test_parse_digest_reads_span_and_timeline_without_interpreting_content(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = make_digest(
            root, 'claude-code', 'abc-123',
            start='2026-10-02 14:27:30 PDT', end='2026-10-02 15:07:55 PDT',
            entries=['14:27:34'], user_turns=['You are the /orchestrator.'],
            invocation='/orchestrator')
        digest = MOD.parse_digest(path)
        self.assertEqual(1, len(digest.entries))
        self.assertEqual(14 * 3600 + 27 * 60 + 34, digest.entries[0][0])
        self.assertEqual(15, digest.span_end.hour)
        self.assertEqual('/orchestrator', digest.invocation_evidence)


if __name__ == '__main__':
    unittest.main()
