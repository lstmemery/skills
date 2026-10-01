"""Run-scoped retry configuration for managed workers."""

import hashlib
import json
import os
from pathlib import Path

from .records import JobError, encoded, parse_json, read_regular, save


PI_RESOURCES = (
    "auth.json", "models.json", "keybindings.json", "AGENTS.md", "SYSTEM.md",
    "APPEND_SYSTEM.md", "extensions", "skills", "prompts", "themes", "npm", "git", "bin",
)
CODEX_BUILTIN_PROVIDERS = {"openai", "ollama", "lmstudio"}
MAX_SETTINGS_SIZE = 1048576
PI_OVERRIDE_TO_SETTINGS_KEY = {
    "max_retries": "maxRetries",
    "max_agent_delay_ms": "maxAgentDelayMs",
}


def pi_agent_dir():
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser().absolute() if configured else Path.home() / ".pi" / "agent"


def parse_jsonc(data):
    """Parse pi's JSONC settings while preserving JSON value semantics."""
    try:
        source = data.decode("utf-8")
    except UnicodeError as error:
        raise JobError("invalid_input", "pi settings are not valid UTF-8") from error
    def copy_string(source, start, output):
        index = start + 1
        escaped = False
        while index < len(source):
            char = source[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
        output.extend(source[start:index])
        return index

    clean = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == '"':
            index = copy_string(source, index, clean)
        elif char == "/" and source[index:index + 2] == "//":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
        elif char == "/" and source[index:index + 2] == "/*":
            index += 2
            while index < len(source) and source[index:index + 2] != "*/":
                if source[index] in "\r\n":
                    clean.append(source[index])
                else:
                    clean.append(" ")
                index += 1
            if index >= len(source):
                raise JobError("invalid_input", "unterminated comment in pi settings")
            index += 2
        else:
            clean.append(char)
            index += 1
    without_comments = "".join(clean)
    clean = []
    index = 0
    while index < len(without_comments):
        char = without_comments[index]
        if char == '"':
            index = copy_string(without_comments, index, clean)
        elif char == ",":
            following = index + 1
            while following < len(without_comments) and without_comments[following].isspace():
                following += 1
            if following < len(without_comments) and without_comments[following] in "}]":
                index += 1
            else:
                clean.append(char)
                index += 1
        else:
            clean.append(char)
            index += 1
    return parse_json("".join(clean).encode("utf-8"))


def load_pi_settings(path):
    value = parse_jsonc(read_regular(path, MAX_SETTINGS_SIZE))
    if not isinstance(value, dict):
        raise JobError("invalid_input", f"pi settings must be a JSON object: {path}")
    return value


def validate_pi_project_settings(cwd, override):
    settings_path = Path(cwd) / ".pi" / "settings.json"
    if not settings_path.exists() and not settings_path.is_symlink():
        return
    settings = load_pi_settings(settings_path)
    retry = settings.get("retry", {})
    if not isinstance(retry, dict):
        raise JobError("invalid_input", f"project pi retry settings must be an object: {settings_path}")
    if retry.get("enabled") is False:
        raise JobError("decision_needed", "project pi settings disable retries, so a retry override would have no effect")
    conflicts = [settings_key for key, settings_key in PI_OVERRIDE_TO_SETTINGS_KEY.items()
                 if key in override and settings_key in retry]
    if conflicts:
        raise JobError("decision_needed", f"project pi settings override run retry values: {', '.join(conflicts)}")


def _read_global_settings(agent_dir):
    path = agent_dir / "settings.json"
    try:
        data = load_pi_settings(path)
    except FileNotFoundError:
        return {}
    return data


def _ensure_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise JobError("conflict", f"run-scoped pi directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise JobError("conflict", f"run-scoped pi path is not a directory: {path}")
    return path


def prepare_pi_run_profile(agent_dir, session_dir, override, global_agent_dir=None):
    """Copy settings into a private run profile and link installed pi resources."""
    source_dir = (Path(global_agent_dir) if global_agent_dir is not None else pi_agent_dir()).absolute()
    target_dir = Path(agent_dir).absolute()
    target_sessions = Path(session_dir).absolute()
    if target_dir == source_dir:
        raise JobError("invalid_input", "run-scoped pi profile cannot replace the shared agent directory")
    if target_sessions == target_dir or target_dir in target_sessions.parents:
        raise JobError("invalid_input", "pi session directory must be outside the agent profile directory")
    global_settings = _read_global_settings(source_dir)
    retry = global_settings.setdefault("retry", {})
    if not isinstance(retry, dict):
        raise JobError("invalid_input", "global pi retry settings must be an object")
    if retry.get("enabled") is False:
        raise JobError("decision_needed", "global pi settings disable retries, so a retry override would have no effect")
    for field, value in override.items():
        retry[PI_OVERRIDE_TO_SETTINGS_KEY[field]] = value
    settings_bytes = encoded(global_settings) + b"\n"
    _ensure_directory(target_dir)
    settings_path = target_dir / "settings.json"
    if settings_path.exists() or settings_path.is_symlink():
        try:
            existing = load_pi_settings(settings_path)
        except (JobError, OSError, ValueError) as error:
            raise JobError("conflict", f"existing run pi settings cannot be read: {settings_path}") from error
        if existing != global_settings:
            raise JobError("conflict", "run-scoped pi settings changed since the worker profile was prepared")
    else:
        save(settings_path, global_settings)
    for name in PI_RESOURCES:
        source = source_dir / name
        if not source.exists() and not source.is_symlink():
            continue
        destination = target_dir / name
        if destination.is_symlink():
            if os.readlink(destination) != str(source):
                raise JobError("conflict", f"run-scoped pi resource changed: {destination}")
        elif destination.exists():
            raise JobError("conflict", f"unexpected run-scoped pi resource already exists: {destination}")
        else:
            os.symlink(source, destination, target_is_directory=source.is_dir())
    _ensure_directory(target_sessions)
    return {
        "agent_dir": str(target_dir),
        "session_dir": str(target_sessions),
        "settings_sha256": hashlib.sha256(settings_bytes).hexdigest(),
    }


def validate_codex_retry_provider(override, environ=None):
    """Require an already selected, user-defined Codex provider for CLI overrides."""
    try:
        import tomllib
    except ImportError as error:
        raise JobError("unavailable_capability", "Codex retry preflight requires Python 3.11 TOML support") from error
    environ = os.environ if environ is None else environ
    home = Path(environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().absolute()
    config_path = home / "config.toml"
    try:
        raw = read_regular(config_path, MAX_SETTINGS_SIZE)
        config = tomllib.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        config = {}
    except (JobError, OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise JobError("unavailable_capability", "Codex config.toml cannot be read for retry preflight") from error
    provider_id = override["provider_id"]
    active_provider = config.get("model_provider", "openai")
    providers = config.get("model_providers", {})
    if provider_id in CODEX_BUILTIN_PROVIDERS or active_provider in CODEX_BUILTIN_PROVIDERS:
        raise JobError("unavailable_capability", "Codex retry override cannot target the built-in provider")
    if active_provider != provider_id:
        raise JobError("unavailable_capability", "Codex retry override provider is not the selected provider")
    if not isinstance(providers, dict) or not isinstance(providers.get(provider_id), dict):
        raise JobError("unavailable_capability", "Codex retry override requires an existing custom provider")
    return provider_id


def codex_retry_cli_args(override):
    provider = override["provider_id"]
    arguments = []
    for key in ("request_max_retries", "stream_max_retries"):
        if key in override:
            arguments.extend(["-c", f"model_providers.{provider}.{key}={override[key]}"])
    return arguments
