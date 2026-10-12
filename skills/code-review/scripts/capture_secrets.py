"""Secret policy and the fail-closed redacted gitleaks adapter."""
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from capture_common import Failure, MAX_FILE, MAX_TOTAL, bounded_file, digest, invalid
from capture_payloads import PayloadInventory

FINGERPRINT_ENV = 'CODE_REVIEW_SECRET_FINGERPRINTS'
SECRETS_SCANNER_ENV = 'CODE_REVIEW_SECRETS_SCANNER'
GITLEAKS_EXIT = 7
GITLEAKS_TIMEOUT = 600
GITLEAKS_MAX_TARGET_MB = 512
MAX_FINDINGS = 20

# Prefix rules are written with character classes so this source file never
# contains a scannable secret shape of its own.
SECRET_TOKEN_RULES = tuple((name, re.compile(pattern)) for name, pattern in (
    ('github-token', r'(?<![A-Za-z0-9])gh[posur]_[A-Za-z0-9]{20,}'),
    ('github-fine-grained-token', r'(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}'),
    ('openai-token', r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}'),
    ('slack-token', r'(?<![A-Za-z0-9])xox[abprse]-[A-Za-z0-9-]{10,}'),
    ('aws-access-key', r'(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}'),
    ('gitlab-token', r'(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}'),
    ('ntfy-token', r'(?<![A-Za-z0-9])tk_[A-Za-z0-9]{15,}'),
))
# Assembled from fragments so neither literal alone forms a key block.
PRIVATE_KEY_BLOCK = re.compile('-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' + r'[\s\S]{0,8000}?' + '-----END [A-Z0-9 ]*PRIVATE KEY-----')
SECRET_ASSIGNMENT = re.compile(
    r'(?i)(?<![A-Za-z0-9_])'
    r'(?P<name>[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[A-Za-z0-9_.-]*)'
    r'["\']?\s*(?::=|[:=])\s*["\']?'
    r'(?P<value>[^\s"\'`<>{}()]{16,})'
    r'(?P<runtime_call>[ \t]*\()?')
PLACEHOLDER_VALUES = frozenset((
    'changeme', 'change_me', 'example', 'fixme', 'n/a', 'nil', 'none', 'not_set',
    'notset', 'null', 'passwd', 'password', 'placeholder', 'redacted', 'sample',
    'secret', 'setme', 'todo', 'token', 'unset', 'xxx', 'xxxx', 'xxxxx'))
PLACEHOLDER_PREFIXES = ('xxx', 'your', 'example', 'sample', 'dummy', 'fake',
                        'insert', 'replace', 'placeholder', 'test', 'changeme')
HEX_SHAPE = re.compile(r'[0-9a-fA-F]{16,}')


def entropy(text):
    total = len(text)
    return -sum((count / total) * math.log2(count / total) for count in Counter(text).values())


def character_classes(value):
    return sum(bool(re.search(pattern, value)) for pattern in (r'[a-z]', r'[A-Z]', r'[0-9]', r'[^A-Za-z0-9]'))


def finding(label, line, rule, value=None):
    row = {'file': label, 'line': line, 'rule': rule}
    if value is not None:
        row.update(length=len(value), sha256_prefix=digest(value.encode('latin-1'))[:16])
    return row


def hex_shaped(text, within=False):
    """Hex digest shape — digests, UUIDs, commit SHAs, the sweep's false-positive class.

    `within` asks whether the text contains a hex run; gitleaks match spans
    include the assignment's left-hand side, while the built-in rules see the
    exact value, so the two call sites legitimately ask different questions of
    the same shared shape definition.
    """
    normalized = text.replace('-', '').replace('_', '')
    match = HEX_SHAPE.search(normalized) if within else HEX_SHAPE.fullmatch(normalized)
    return bool(match)


def assignment_value_is_secret(value):
    lowered = value.lower()
    if lowered in PLACEHOLDER_VALUES or lowered.startswith(PLACEHOLDER_PREFIXES):
        return False
    if value[0] in '$%' or '${' in value or '{{' in value:
        return False
    if hex_shaped(value):
        return False
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+', value):
        return False  # attribute chains, not values
    if value.startswith(('/', './', '../')):
        return False  # filesystem paths, not values
    classes = character_classes(value)
    if classes < 2:
        return False
    return entropy(value) >= (3.5 if classes == 2 else 3.0)


def load_fingerprint_prefixes():
    location = os.environ.get(FINGERPRINT_ENV)
    if not location:
        return ()
    try:
        raw = bounded_file(Path(location)).decode('utf-8')
    except Failure as error:
        invalid('Fingerprint denylist cannot be read', path=location, detail=error.result['message'])
    except (OSError, UnicodeError) as error:
        invalid('Fingerprint denylist cannot be read', path=location, detail=str(error))
    prefixes = set()
    for number, line in enumerate(raw.splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith('#'):
            continue
        if not re.fullmatch(r'[0-9a-fA-F]{8,64}', entry):
            invalid('Invalid fingerprint denylist entry (want 8-64 hex characters per line)', path=location, line=number)
        prefixes.add(entry.lower())
    return tuple(sorted(prefixes))


def line_at(text, offset):
    return text.count('\n', 0, offset) + 1


def load_secret_allowlist(payloads):
    """Load exact-value SHA-256 exceptions from the captured repository tree."""
    entry = next((data for label, _, data in payloads
                  if label == 'after:.code-review-secrets-allow'), None)
    if entry is None:
        return frozenset()
    fingerprints = set()
    try:
        rows = entry.decode('utf-8').splitlines()
    except UnicodeError:
        invalid('Secret allowlist must be UTF-8 text', path='.code-review-secrets-allow')
    for number, row in enumerate(rows, 1):
        if not row.strip() or row.lstrip().startswith('#'):
            continue
        parts = row.split('\t', 1)
        if len(parts) != 2 or not re.fullmatch(r'[0-9a-fA-F]{64}', parts[0]) or not parts[1].strip():
            invalid('Invalid secret allowlist entry (want full SHA-256, tab, and reason)',
                    path='.code-review-secrets-allow', line=number)
        fingerprints.add(parts[0].lower())
    return frozenset(fingerprints)


def is_allowed_fingerprint(value, allowed_fingerprints):
    return digest(value) in allowed_fingerprints


def rule_findings(label, text, allowed_fingerprints=frozenset()):
    findings = []
    for name, pattern in SECRET_TOKEN_RULES:
        for match in pattern.finditer(text):
            value = match.group(0)
            if not is_allowed_fingerprint(value.encode('latin-1'), allowed_fingerprints):
                findings.append(finding(label, line_at(text, match.start()), name, value))
    for match in PRIVATE_KEY_BLOCK.finditer(text):
        value = match.group(0)
        if not is_allowed_fingerprint(value.encode('latin-1'), allowed_fingerprints):
            findings.append(finding(label, line_at(text, match.start()), 'private-key-block', value))
    for match in SECRET_ASSIGNMENT.finditer(text):
        value = match.group('value')
        # A callable name is source code describing how the value is fetched,
        # not the credential value being assigned.
        if match.group('runtime_call'):
            continue
        if assignment_value_is_secret(value) and not is_allowed_fingerprint(
                value.encode('latin-1'), allowed_fingerprints):
            findings.append(finding(label, line_at(text, match.start()), 'high-entropy-assignment', value))
    return findings


def denylist_findings(label, text, prefixes):
    if not prefixes:
        return []
    findings = []
    for match in re.finditer(r'\S{8,}', text):
        chunk = match.group(0)
        candidates = [chunk.strip('\'"()[]{},;:')]
        for separator in ('=', ':'):
            if separator in chunk:
                candidates.append(chunk.split(separator, 1)[1].strip('\'"()[]{},;:'))
        for candidate in candidates:
            if len(candidate) < 8:
                continue
            fingerprint = digest(candidate.encode('latin-1'))
            if any(fingerprint.startswith(prefix) for prefix in prefixes):
                findings.append(finding(label, line_at(text, match.start()), 'fingerprint-denylist', candidate))
                break
    return findings


def reported_match_span(staging, relative, report_row):
    """Return exact scanner-matched bytes, or None for invalid staged coordinates."""
    start_line = report_row['StartLine']
    end_line = report_row['EndLine']
    start_column = report_row['StartColumn']
    end_column = report_row['EndColumn']
    try:
        raw = (staging / relative).read_bytes()
    except OSError:
        return None
    lines = raw.splitlines(keepends=True)
    if not lines or start_line > len(lines) or end_line > len(lines):
        return None
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    start_bytes = lines[start_line - 1].rstrip(b'\r\n')
    end_bytes = lines[end_line - 1]
    if start_column > len(start_bytes) or end_column > len(end_bytes) + 1:
        return None
    if start_line == end_line and end_column < start_column:
        return None
    start = offsets[start_line - 1] + start_column - 1
    end = offsets[end_line - 1] + end_column
    return raw[start:end] if end > start else None


class GitleaksAdapter:
    def __init__(self, timeout=GITLEAKS_TIMEOUT, max_report=MAX_TOTAL):
        self.timeout = timeout
        self.max_report = max_report

    def scan(self, staging, labels, allowed_fingerprints=frozenset()):
        """Redacted directory-mode scan of staged payloads; report operational failures."""
        scanner_mode = os.environ.get(SECRETS_SCANNER_ENV)
        if scanner_mode not in (None, 'stdlib-only'):
            invalid(f'Unsupported {SECRETS_SCANNER_ENV} value')
        if scanner_mode == 'stdlib-only':
            return [], 'stdlib-only'
        binary = shutil.which('gitleaks')
        if not binary:
            return [], 'unavailable'
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GITLEAKS_')}
        argv = [binary, 'dir', str(staging), '--report-format', 'json', '--report-path', '-',
                '--redact', '--exit-code', str(GITLEAKS_EXIT), '--no-banner', '--no-color', '-l', 'error',
                '--max-target-megabytes', str(GITLEAKS_MAX_TARGET_MB), '--gitleaks-ignore-path', str(staging)]
        try:
            result = subprocess.run(argv, env=environment, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired):
            return [], 'failed'
        if result.returncode not in (0, GITLEAKS_EXIT) or len(result.stdout) > self.max_report:
            return [], 'failed'
        try:
            rows = json.loads(result.stdout.decode('utf-8'))
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('report is not a list of findings')
            if result.returncode == GITLEAKS_EXIT and not rows:
                raise ValueError('finding exit status has no findings')
            required_fields = ('File', 'RuleID', 'StartLine', 'EndLine', 'StartColumn', 'EndColumn')
            if any(any(field not in row for field in required_fields) or
                   not isinstance(row['File'], str) or not row['File'] or
                   not isinstance(row['RuleID'], str) or not row['RuleID'] or
                   any(type(row[field]) is not int or row[field] <= 0 or row[field] > MAX_FILE
                       for field in required_fields[2:]) or
                   row['EndLine'] < row['StartLine'] or
                   (row['EndLine'] == row['StartLine'] and row['EndColumn'] < row['StartColumn'])
                   for row in rows):
                raise ValueError('malformed finding')
        except (UnicodeError, ValueError, TypeError):
            return [], 'failed'
        findings = []
        for row in rows:
            reported = Path(row['File'])
            try:
                relative = reported.relative_to(staging)
            except ValueError:
                relative = reported if not reported.is_absolute() else Path(*reported.parts[-3:])
            key = '/'.join(relative.parts)
            span = reported_match_span(staging, relative, row)
            if span is None:
                return [], 'failed'
            if is_allowed_fingerprint(span, allowed_fingerprints):
                continue
            if row['RuleID'] == 'generic-api-key' and hex_shaped(span.decode('latin-1'), within=True):
                continue
            findings.append(finding(labels.get(key, key or str(reported)), row['StartLine'],
                                    f"gitleaks:{row['RuleID']}"))
        return findings, 'used'


class SecretGate:
    def __init__(self, scanner=None):
        self.scanner = scanner if scanner is not None else GitleaksAdapter()

    def check(self, inventory: PayloadInventory):
        """Fail closed, before any payload file is written, on secret-shaped content."""
        payloads = inventory.payloads
        prefixes = load_fingerprint_prefixes()
        allowed_fingerprints = load_secret_allowlist(payloads)
        findings = []
        for label, _, data in payloads:
            text = data.decode('latin-1')
            findings.extend(rule_findings(label, text, allowed_fingerprints))
            findings.extend(denylist_findings(label, text, prefixes))
        scan_state = 'unavailable'
        with tempfile.TemporaryDirectory(prefix='review-secret-scan-') as area:
            staging = Path(area)
            for _, relative, data in payloads:
                target = staging / relative
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)
            staged, scan_state = self.scanner.scan(
                staging, {relative: label for label, relative, _ in payloads}, allowed_fingerprints)
            findings.extend(staged)
        if scan_state == 'failed':
            raise Failure('secret_detected', 'Capture refused: secrets scanner failed',
                          secret_scan={'gitleaks': scan_state})
        if not findings:
            return {'gitleaks': scan_state}
        ordered = sorted(findings, key=lambda row: (row['file'], row['line'], row['rule']))
        raise Failure('secret_detected',
                      'Capture refused: a payload matches a secret rule; remove the value or replace it with a fake and capture again',
                      findings=ordered[:MAX_FINDINGS], total_findings=len(ordered),
                      secret_scan={'gitleaks': scan_state})
