"""Credential detection and redaction shared by local-lane launch paths."""

import re


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*)(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)")
AUTH_SCHEME_TOKEN = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
KNOWN_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]+|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16}|glpat-[A-Za-z0-9_-]{12,}|tk_[A-Za-z0-9_-]{12,})\b")
URL_PASSWORD = re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)")
PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]{0,8000}?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)


def contains_secret_material(value):
    """Detect common credential values before local-lane prompt submission."""
    return any(pattern.search(value) for pattern in (
        SENSITIVE_ASSIGNMENT, AUTH_SCHEME_TOKEN, KNOWN_TOKEN, URL_PASSWORD, PRIVATE_KEY_BLOCK,
    ))


def _redacted_assignment(match):
    value = match.group("value")
    quote = value[0] if value[0] in ("\"", "'") and value.endswith(value[0]) else ""
    return match.group("prefix") + quote + "<redacted>" + quote


def safe_cli_text(value, maximum=2048):
    """Return bounded terminal text with common credential material removed."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = ANSI_ESCAPE.sub("", str(value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    value = AUTH_SCHEME_TOKEN.sub(r"\1 <redacted>", value)
    value = URL_PASSWORD.sub(r"\1<redacted>\3", value)
    value = SENSITIVE_ASSIGNMENT.sub(_redacted_assignment, value)
    value = KNOWN_TOKEN.sub("<redacted>", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > maximum:
        return value[:maximum - 3] + "..."
    return value
