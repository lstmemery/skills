#!/usr/bin/env python3
"""Deterministic checks for the retro-prep digest step (finding R-09).

The retro-prep corpus is the retro's only source. The 2026-10-02 build
clipped a coordinator digest, shipped a digest with no timeline, and
labeled the day's main coordinator "interactive" because the role came
from the first prompt (`/clear`) instead of the /orchestrator invocation
found later in the session. This script makes those guarantees
mechanical, so the next corpus build cannot repeat them quietly:

- Digests must cover through the session end timestamp; truncation
  (timeline or span ending before the listed session end) fails loudly.
- Every digest must have a non-zero timeline; empty digests are flagged
  in the report and, with `--write`, in index.md itself.
- Role is inferred from the /orchestrator invocation in the session's
  user turns rather than the first prompt; index.md coordinator rows
  must match that inference (`--write` corrects them).

Inputs are the corpus artifacts themselves: `index.md` (the triage
table every build already writes) and the per-session digests under
`sessions/`. Raw transcripts are never read here; the builder reads
transcripts, this script verifies what it shipped.

Usage:
  retro_prep_digest.py verify --corpus DIR [--write] [--require-digests]
      [--grace-minutes N] [--report PATH]
  retro_prep_digest.py derive-role [--text TEXT]   # text on stdin

Exit codes: 0 all checks passed, 1 fatal usage/parse error, 2 check
failures found (truncated or empty digest, coordinator role mismatch,
span clip).
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

VERSION = 1

DEFAULT_GRACE_MINUTES = 2

# A digest without any timeline entry this many minutes past the listed
# session end is truncated. Symmetric tolerance for entries past the end
# (clock skew between builder reads).
DEFAULT_GRACE = timedelta(minutes=DEFAULT_GRACE_MINUTES)

# Role inference: user turns are scanned for orchestrator invocation and
# worker-dispatch patterns, not just the first prompt. Ordered; the
# first matching group wins. Coordinator is checked first because a
# coordinator session also dispatches workers.
ROLE_PATTERNS = (
    ("coordinator", "orchestrator-invocation", re.compile(
        r"you are (the|an) /?orchestrator\b", re.IGNORECASE)),
    ("worker", "worker-dispatch", re.compile(
        r"you are (?:a|an|the) (?:worker|code reviewer|read-only reviewer)\b"
        r"|parent-dispatched worker task"
        r"|read and execute (?:your brief|/)"
        r"|work autonomously to completion", re.IGNORECASE)),
)

TIMELINE_HEADER_RE = re.compile(r"^###\s+\d{1,2}:\d{2}:\d{2}", re.MULTILINE)
ENTRY_TIME_RE = re.compile(r"^###\s+(\d{1,2}):(\d{2}):(\d{2})")
DATETIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\s*([A-Za-z0-9+\-:/]+))?")
SPAN_LINE_RE = re.compile(r"^\s*-\s*Session span:\s*(.+)$", re.MULTILINE)
SPAN_TO_RE = re.compile(r"\s+to\s+")
USER_TURN_TITLE_RE = re.compile(r"^###\s+\S+\s+.*user turn", re.IGNORECASE)
FENCE_RE = re.compile(r"^```")


def norm_role(cell):
    """Reduce an index role cell to its bare role word, dropping earlier
    --write annotations like '(was: interactive)' or '[empty-digest]'."""
    cell = re.sub(r"\[[^\]]*\]", "", cell)
    cell = cell.split("(was:")[0]
    return cell.strip()


@dataclass
class IndexRow:
    line_no: int
    harness: str
    session_id: str
    start_text: str
    end_text: str
    end: datetime
    size: str
    first_prompt: str
    role: str
    line: str


@dataclass
class Digest:
    path: Path
    span_start: datetime = None
    span_end: datetime = None
    entries: list = field(default_factory=list)      # [(seconds, title)]
    user_turn_bodies: list = field(default_factory=list)


@dataclass
class SessionCheck:
    row: IndexRow
    digest_path: str = None
    entries: int = None
    last_entry: str = None
    coverage_gap_minutes: float = None
    span_end: str = None
    role_index: str = None
    role_derived: str = None
    role_basis: str = None
    status: str = "OK"
    problems: list = field(default_factory=list)


def parse_naive(text):
    """Parse 'YYYY-MM-DD HH:MM:SS [TZ]' as wall-clock; tz kept opaque."""
    match = DATETIME_RE.search(text)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def split_row(line):
    """Split a markdown table row on unescaped pipes outside backticks."""
    cells, current, in_ticks, escaped = [], [], False, False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            in_ticks = not in_ticks
            current.append(char)
        elif char == "|" and not in_ticks:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def parse_index(path):
    """Parse the triage table; returns (rows, headers) or raises ValueError."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_idx, headers = None, None
    for i, line in enumerate(lines):
        if line.startswith("|") and "Session ID" in line and "Role" in line:
            header_idx, headers = i, [c.strip() for c in split_row(line.strip("|"))]
            break
    if header_idx is None:
        raise ValueError(f"{path}: no index table header containing Session ID and Role")
    rows = []
    for i in range(header_idx + 2, len(lines)):  # skip separator row
        line = lines[i]
        if not line.startswith("|"):
            if rows:
                break  # table ended
            continue
        cells = split_row(line.strip().strip("|"))
        if len(cells) < len(headers):
            raise ValueError(f"{path}:{i + 1}: row has {len(cells)} cells, expected {len(headers)}")
        record = dict(zip(headers, cells))
        end = parse_naive(record.get("End in window", ""))
        if end is None:
            raise ValueError(f"{path}:{i + 1}: unparseable 'End in window': {record.get('End in window')!r}")
        session_id = record.get("Session ID", "").strip("`* ")
        if not session_id:
            raise ValueError(f"{path}:{i + 1}: empty Session ID cell")
        rows.append(IndexRow(
            line_no=i + 1,
            harness=record.get("Harness", "").strip(),
            session_id=session_id,
            start_text=record.get("Start", "").strip(),
            end_text=record.get("End in window", "").strip(),
            end=end,
            size=record.get("Size", "").strip(),
            first_prompt=record.get("First user prompt (redacted)", "").strip(),
            role=record.get("Role", "").strip(),
            line=line,
        ))
    if not rows:
        raise ValueError(f"{path}: index table has no session rows")
    return rows


def parse_digest(path):
    """Parse span header, timeline entries, and user-turn bodies."""
    text = path.read_text(encoding="utf-8")
    digest = Digest(path=path)
    span = SPAN_LINE_RE.search(text)
    if span:
        parts = SPAN_TO_RE.split(span.group(1).strip(), maxsplit=1)
        if len(parts) == 2:
            digest.span_start = parse_naive(parts[0])
            digest.span_end = parse_naive(parts[1])
    lines = text.splitlines()
    in_timeline, in_user_turn, in_user_body = False, False, False
    body = []
    for line in lines:
        if line.startswith("## "):
            in_timeline = line.strip().lower() == "## timeline"
            in_user_turn = in_user_body = False
            continue
        if line.startswith("### "):
            if in_timeline:
                time_match = ENTRY_TIME_RE.match(line)
                if time_match:
                    h, m, s = (int(time_match.group(i)) for i in range(1, 4))
                    digest.entries.append((h * 3600 + m * 60 + s, line.lstrip("# ").strip()))
            in_user_turn = bool(USER_TURN_TITLE_RE.match(line))
            if in_user_turn:
                body = []
            in_user_body = False
            continue
        if in_user_turn:
            if line.startswith("```"):
                if in_user_body:
                    digest.user_turn_bodies.append("\n".join(body))
                    in_user_turn = in_user_body = False
                else:
                    in_user_body = True
                continue
            if in_user_body:
                body.append(line)
    return digest


def derive_role(texts):
    """Infer role from any user-turn text; returns (role, basis)."""
    for role, basis, pattern in ROLE_PATTERNS:
        for text in texts:
            if pattern.search(text):
                return role, basis
    if texts:
        return "interactive", "no-invocation-in-user-turns"
    return "interactive", "no-user-turns-in-digest"


def find_digest(sessions_dir, row):
    """Locate a digest by the corpus naming convention, then by suffix glob."""
    direct = sessions_dir / f"{row.harness.lower().replace(' ', '-')}-{row.session_id}.md"
    if direct.exists():
        return direct
    matches = sorted(sessions_dir.glob(f"*{row.session_id}*.md"))
    return matches[0] if matches else None


def last_entry_datetime(digest, session_end):
    """Resolve the last timeline entry's date. Entries share the span-start
    date unless the session itself crosses midnight, in which case the
    interpretation closest to the session end wins (wall clock)."""
    if not digest.entries:
        return None
    seconds = digest.entries[-1][0]
    if digest.span_start and digest.span_start.date() != session_end.date():
        day = digest.span_start.date()
        base = datetime(day.year, day.month, day.day) + timedelta(seconds=seconds)
        return min((base, base + timedelta(days=1)),
                   key=lambda dt: abs((session_end - dt).total_seconds()))
    return datetime(session_end.year, session_end.month,
                    session_end.day) + timedelta(seconds=seconds)


def check_session(row, sessions_dir, grace, require_digests):
    check = SessionCheck(row=row)
    check.role_index = row.role
    path = find_digest(sessions_dir, row)
    if path is None:
        check.status = "NO-DIGEST"
        if require_digests:
            check.problems.append("no digest file found for listed session")
        return check
    check.digest_path = str(path)
    digest = parse_digest(path)
    check.entries = len(digest.entries)
    if digest.user_turn_bodies:
        check.role_derived, check.role_basis = derive_role(digest.user_turn_bodies)

    if digest.span_end is not None:
        check.span_end = digest.span_end.strftime("%Y-%m-%d %H:%M:%S")
        if digest.span_end + grace < row.end:
            gap = (row.end - digest.span_end).total_seconds() / 60
            check.problems.append(
                f"span header ends {gap:.0f} min before listed session end (span clip)")
    else:
        check.problems.append("digest has no 'Session span: X to Y' header line")

    if not digest.entries:
        check.status = "EMPTY"
        check.problems.append("digest has zero timeline entries")
        return check
    check.last_entry = f"{digest.entries[-1][0] // 3600:02d}:" \
                       f"{digest.entries[-1][0] % 3600 // 60:02d}:" \
                       f"{digest.entries[-1][0] % 60:02d}"
    resolved = last_entry_datetime(digest, row.end)
    gap = (row.end - resolved).total_seconds() / 60
    check.coverage_gap_minutes = round(gap, 1)
    if gap > grace.total_seconds() / 60:
        check.problems.append(
            f"last timeline entry {check.last_entry} is {gap:.0f} min before "
            f"listed session end (truncated)")
    elif gap < -grace.total_seconds() / 60:
        check.problems.append(
            f"last timeline entry {check.last_entry} is {-gap:.0f} min after "
            f"listed session end (beyond grace; clock or date mismatch)")

    if check.role_derived and \
            "coordinator" in (norm_role(check.role_index), check.role_derived) and \
            norm_role(check.role_index) != check.role_derived:
        check.problems.append(
            f"index role {check.role_index!r} does not match orchestrator-invocation "
            f"inference {check.role_derived!r} (basis: {check.role_basis})")
    elif check.role_derived == "worker" and \
            norm_role(check.role_index) not in ("worker", "coordinator"):
        check.problems.append(
            f"note: worker dispatch found but index role is {check.role_index!r}")

    if check.problems and check.status != "EMPTY":
        hard = [p for p in check.problems if not p.startswith("note:")]
        check.status = "FAILED" if hard else check.status
    return check


def annotate_index(path, checks):
    """Rewrite index.md: fix coordinator role rows, flag empty digests."""
    by_line = {c.row.line_no: c for c in checks}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, check in by_line.items():
        if check.status == "OK" and check.role_derived == check.role_index:
            continue
        cells = split_row(lines[line_no - 1].strip().strip("|"))
        role_cell = cells[-1]
        if check.status == "EMPTY":
            role_cell = f"{role_cell} [empty-digest]".strip()
        role_fixable = (check.role_derived
                        and norm_role(check.role_index) != check.role_derived
                        and check.entries not in (None, 0)
                        and "coordinator" in (norm_role(check.role_index),
                                              check.role_derived))
        if role_fixable:
            role_cell = f"{check.role_derived} (was: {check.role_index})"
        cells[-1] = role_cell
        lines[line_no - 1] = "| " + " | ".join(cells) + " |"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sum(1 for c in checks
               if c.status == "EMPTY" or
               ("coordinator" in (c.role_index or "", c.role_derived or "")
                and c.role_index != c.role_derived))


def render_report(checks):
    header = ("| Session | Status | Entries | Last entry | Gap to end (min) | "
              "Role (index) | Role (derived) | Problems |")
    lines = [header, "|---|---|---:|---|---:|---|---|---|"]
    for check in checks:
        lines.append("| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            check.row.session_id[:12],
            check.status,
            check.entries if check.entries is not None else "n/a",
            check.last_entry or "n/a",
            check.coverage_gap_minutes if check.coverage_gap_minutes is not None else "n/a",
            check.role_index or "n/a",
            check.role_derived or "n/a",
            "; ".join(check.problems) or "—",
        ))
    failed = sum(1 for c in checks if c.status in ("FAILED", "EMPTY"))
    no_digest = sum(1 for c in checks if c.status == "NO-DIGEST")
    lines.append("")
    lines.append(f"Sessions: {len(checks)}; ok: {len(checks) - failed - no_digest}; "
                 f"failed: {failed}; no digest: {no_digest}")
    return "\n".join(lines), failed


def cmd_verify(args):
    corpus = Path(args.corpus)
    index_path = corpus / args.index
    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else corpus / "sessions"
    rows = parse_index(index_path)
    grace = timedelta(minutes=args.grace_minutes)
    checks = [check_session(row, sessions_dir, grace, args.require_digests)
              for row in rows]
    report_text, failed = render_report(checks)
    missing = sum(1 for c in checks
                  if c.status == "NO-DIGEST" and c.problems) if args.require_digests else 0
    print(report_text)
    if args.report:
        payload = {
            "version": VERSION,
            "grace_minutes": args.grace_minutes,
            "require_digests": args.require_digests,
            "sessions": [{
                "session_id": c.row.session_id,
                "harness": c.row.harness,
                "listed_end": c.row.end_text,
                "digest": c.digest_path,
                "entries": c.entries,
                "last_entry": c.last_entry,
                "coverage_gap_minutes": c.coverage_gap_minutes,
                "span_end": c.span_end,
                "role_index": c.role_index,
                "role_derived": c.role_derived,
                "role_basis": c.role_basis,
                "status": c.status,
                "problems": c.problems,
            } for c in checks],
        }
        Path(args.report).write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.write:
        changed = annotate_index(index_path, checks)
        print(f"index.md updated: {changed} row(s) annotated")
    if failed or missing:
        print(f"VERIFY FAILED: {failed} session(s) with truncated or empty digests "
              f"or coordinator role mismatches"
              + (f"; {missing} listed session(s) without a digest" if missing else ""),
              file=sys.stderr)
        return 2
    return 0


def cmd_derive_role(args):
    text = args.text if args.text is not None else sys.stdin.read()
    role, basis = derive_role([text] if text.strip() else [])
    print(f"{role}\t{basis}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="check a built corpus against the digest guarantees")
    verify.add_argument("--corpus", required=True, help="corpus directory containing index.md and sessions/")
    verify.add_argument("--index", default="index.md", help="index file name inside --corpus")
    verify.add_argument("--sessions-dir", help="override digest directory (default CORPUS/sessions)")
    verify.add_argument("--grace-minutes", type=int, default=DEFAULT_GRACE_MINUTES,
                        help=f"tolerated gap between last timeline entry and session end "
                             f"(default {DEFAULT_GRACE_MINUTES})")
    verify.add_argument("--require-digests", action="store_true",
                        help="also fail for listed sessions without any digest")
    verify.add_argument("--report", help="write JSON coverage report to this path")
    verify.add_argument("--write", action="store_true",
                        help="annotate index.md: correct coordinator roles, flag empty digests")
    verify.set_defaults(func=cmd_verify)

    role = sub.add_parser("derive-role", help="classify one user prompt read from stdin or --text")
    role.add_argument("--text", help="prompt text (default: stdin)")
    role.set_defaults(func=cmd_derive_role)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
