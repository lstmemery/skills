"""Shared, locked provider admission and rate-limit backoff state."""

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .records import (JobError, atomic_bytes, encoded, fields, identifier, integer,
                      load_json, policy_record, save, text, version)
from .model_ladder import billing_pool


STATE_NAME = "provider-admission.json"
LOCK_NAME = ".provider-admission.lock"
POLICY_PATH = Path(__file__).resolve().parents[2] / "launch-policy.json"


def normalize_provider(value):
    value = billing_pool(text(value, "provider", 100))
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value) is None:
        raise JobError("invalid_input", "provider must be a simple provider identifier")
    return value.casefold()


def normalize_model(value):
    value = text(value, "model", 200).strip().casefold()
    if "/" in value and billing_pool(value.split("/", 1)[0]) in ("codex", "claude", "opencode-go"):
        return text(value.split("/", 1)[1], "native model", 200)
    return value


def _retry_after(value, now):
    if value is None:
        return None
    value = str(value).strip().strip('"\'`')
    try:
        seconds = float(value.removesuffix("s"))
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return None


def _reset_delay(value, now):
    if value is None:
        return None
    value = str(value).strip().strip('"\'`')
    try:
        numeric = float(value.removesuffix("s"))
        if not math.isfinite(numeric) or numeric < 0:
            return None
        if numeric >= 1_000_000_000 or numeric > now:
            return max(0.0, numeric - now)
        return numeric
    except ValueError:
        pass
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return _retry_after(value, now)


def _duration_seconds(value):
    value = value.strip().rstrip(" .,!;")
    pattern = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)")
    matches = list(pattern.finditer(value))
    if not matches:
        return None
    cursor = 0
    seconds = 0.0
    for match in matches:
        if value[cursor:match.start()].strip(" ,"):
            return None
        amount = float(match.group(1))
        unit = match.group(2).casefold()[0]
        seconds += amount * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        cursor = match.end()
    if value[cursor:].strip(" ,") or not math.isfinite(seconds):
        return None
    return seconds


def _reset_timezone(label, now):
    if not label:
        configured = os.environ.get("TZ", "").removeprefix(":")
        if configured:
            try:
                return ZoneInfo(configured)
            except (ValueError, ZoneInfoNotFoundError):
                pass
        try:
            zoneinfo_root = Path("/usr/share/zoneinfo").resolve()
            localtime = Path("/etc/localtime").resolve()
            return ZoneInfo(localtime.relative_to(zoneinfo_root).as_posix())
        except (OSError, ValueError, ZoneInfoNotFoundError):
            return datetime.fromtimestamp(now).astimezone().tzinfo or timezone.utc
    normalized = label.strip().strip("()")
    aliases = {"UTC": timezone.utc, "GMT": timezone.utc, "Z": timezone.utc,
               "PST": timezone(timedelta(hours=-8)), "PDT": timezone(timedelta(hours=-7)),
               "MST": timezone(timedelta(hours=-7)), "MDT": timezone(timedelta(hours=-6)),
               "CST": timezone(timedelta(hours=-6)), "CDT": timezone(timedelta(hours=-5)),
               "EST": timezone(timedelta(hours=-5)), "EDT": timezone(timedelta(hours=-4))}
    if normalized.upper() in aliases:
        return aliases[normalized.upper()]
    offset = re.fullmatch(r"(?i)(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?", normalized)
    if offset:
        hours = int(offset.group(2))
        minutes = int(offset.group(3) or 0)
        if hours > 23 or minutes > 59:
            return None
        seconds = (hours * 60 + minutes) * 60 * (1 if offset.group(1) == "+" else -1)
        return timezone(timedelta(seconds=seconds))
    try:
        return ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def _clock_reset_delay(value, now):
    value = value.strip().rstrip(" .,!;")
    match = re.fullmatch(
        r"(?i)(\d{1,2}(?::\d{2}(?::\d{2})?)?\s*(?:am|pm)?)(?:\s+([^\s]+))?", value)
    if not match:
        return None
    clock_text, zone_text = match.groups()
    formats = ("%I:%M:%S %p", "%I:%M %p", "%I %p") if re.search(r"(?i)\b(?:am|pm)\b", clock_text) else (
        "%H:%M:%S", "%H:%M", "%H")
    reset_time = None
    for format_string in formats:
        try:
            reset_time = datetime.strptime(clock_text.upper(), format_string).time()
            break
        except ValueError:
            continue
    if reset_time is None:
        return None
    zone = _reset_timezone(zone_text, now)
    if zone is None:
        return None
    current = datetime.fromtimestamp(now, timezone.utc).astimezone(zone)
    for offset in (0, 1):
        reset_date = current.date() + timedelta(days=offset)
        local_time = datetime.combine(reset_date, reset_time)
        candidates = []
        for fold in (0, 1):
            candidate = local_time.replace(tzinfo=zone, fold=fold)
            if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local_time:
                continue
            timestamp = candidate.timestamp()
            if timestamp >= now:
                candidates.append(timestamp)
        if candidates:
            return min(candidates) - now
    return None


def _natural_reset_delay(output, now):
    match = re.search(r"(?i)\b(?:resets?|try again)\s+(at|in)\s+([^\r\n]+)", output)
    if match is None:
        return None
    mode, value = match.groups()
    if mode.casefold() == "in":
        return _duration_seconds(value)
    return _clock_reset_delay(value, now)


def backoff_delay(retry_after=None, reset_at=None, default_seconds=60, now=None):
    now = time.time() if now is None else now
    delay = _retry_after(retry_after, now)
    if delay is not None:
        return delay, "retry-after"
    delay = _reset_delay(reset_at, now)
    if delay is None and reset_at is not None:
        delay = _clock_reset_delay(str(reset_at), now)
    if delay is None and reset_at is not None:
        delay = _duration_seconds(str(reset_at))
    if delay is None and reset_at is not None:
        delay = _natural_reset_delay(str(reset_at), now)
    if delay is not None:
        return delay, "reset"
    integer(default_seconds, "default_backoff_seconds", 1, 3600)
    return float(default_seconds), "default"


def _pi_provider_error(output):
    marker = re.search(r"(?im)^\s*Error:\s*429\s*:\s*", output)
    if marker is None:
        return False
    body = output[marker.end():]
    start = body.find("{")
    if start < 0:
        return False
    try:
        payload, _ = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError:
        return False

    def has_marker(value):
        if isinstance(value, dict):
            if str(value.get("code", "")) == "1302":
                return True
            return any(has_marker(child) for child in value.values())
        if isinstance(value, list):
            return any(has_marker(child) for child in value)
        return isinstance(value, str) and value == "GoUsageLimitError"

    return has_marker(payload)


def _codex_rate_limit_banner(output):
    return bool(
        re.search(
            r"(?im)^\s*(?:⚠\s*)?API Error:\s*(?:Rate limit reached|429(?:\s+Too Many Requests)?|Too Many Requests)(?:\b|$)",
            output,
        )
    )


def _usage_limit_banner(output):
    """Recognize terminal quota banners from supported interactive runtimes."""
    return bool(re.search(
        r"(?im)^\s*(?:⚠\s*)?(?:you(?:'ve| have) hit your (?:chatgpt )?(?:usage )?limit|"
        r"usage limit reached|quota exceeded)(?:\b|[.!·])",
        output,
    ))


def _http_429_status_line(output):
    return re.search(r"(?im)^\s*HTTP(?:/[0-9]+(?:\.[0-9]+)?)?\s+429(?:\s|$)", output) is not None


def rate_limit_from_output(output, now=None, *, source="pane", runtime=None, exit_code=None):
    """Return metadata only for known provider errors, never for generic transcript prose."""
    output = str(output)
    if source == "harness_error":
        explicit_error = exit_code not in (None, 0) and (
            _http_429_status_line(output)
            or _pi_provider_error(output)
            or (runtime in ("codex", "claude", "claude-code") and
                (_codex_rate_limit_banner(output) or _usage_limit_banner(output)))
        )
    elif source == "pane" and runtime == "pi":
        explicit_error = _pi_provider_error(output)
    elif source == "pane" and runtime == "codex":
        explicit_error = _codex_rate_limit_banner(output) or _usage_limit_banner(output)
    elif source == "pane" and runtime in ("claude", "claude-code"):
        explicit_error = _usage_limit_banner(output)
    else:
        explicit_error = False
    if not explicit_error:
        return None

    current_time = time.time() if now is None else now
    retry_match = re.search(r"(?im)^\s*retry[-_ ]after\s*[:=]\s*([^\r\n]+)", output)
    if retry_match is None:
        retry_match = re.search(r"(?i)[\"']retry[-_ ]after[\"']\s*:\s*[\"']?([^\"',}\s]+)", output)
    reset_match = re.search(
        r"(?im)^\s*(?:(?:x-)?rate[-_ ]?limit[-_ ]?reset(?:[-_ ](?:requests|tokens))?|reset[-_ ]at)\s*[:=]\s*([^\r\n,;]+)",
        output,
    )
    if reset_match is None:
        reset_match = re.search(
            r"(?i)[\"'](?:reset_at|reset|x-ratelimit-reset)[\"']\s*:\s*[\"']?([^\"',}\s]+)",
            output,
        )
    retry_after = retry_match.group(1).strip() if retry_match else None
    reset_at = reset_match.group(1).strip() if reset_match else None
    delay = None
    source = None
    if retry_after is not None:
        delay = _retry_after(retry_after, current_time)
        if delay is not None:
            source = "retry-after"
    if delay is None and reset_at is not None:
        delay = _reset_delay(reset_at, current_time)
        if delay is None:
            delay = _clock_reset_delay(reset_at, current_time)
        if delay is not None:
            source = "reset"
    if delay is None:
        delay = _natural_reset_delay(output, current_time)
        if delay is not None:
            source = "reset"
    return {"retry_after_seconds": delay, "source": source}


def write_continuation_record(path, task_id, pane_id, reset_at, attempt_id):
    """Persist a direct-worker reset for the coordinator's existing watcher."""
    target = Path(text(str(path), "continuation record path", 4096)).absolute()
    if target.is_symlink():
        raise JobError("invalid_input", "continuation record must not be a symlink")
    if type(reset_at) not in (int, float) or not math.isfinite(reset_at) or reset_at < 0:
        raise JobError("invalid_input", "continuation reset_at must be a finite nonnegative timestamp")
    record = {"task": identifier(task_id, "continuation task id"),
              "pane": text(pane_id, "continuation pane id", 512),
              "reset_at": float(reset_at),
              "attempt": text(attempt_id, "continuation attempt id", 512)}
    if target.exists():
        try:
            if load_json(target, 65536) == record:
                return record
        except (JobError, OSError):
            pass
        raise JobError("conflict", "continuation record already exists with different contents")
    atomic_bytes(target, encoded(record) + b"\n")
    return record


class AdmissionStore:
    """An atomic host-level counter shared by every caller using this directory."""

    def __init__(self, state_dir=None, clock=None):
        configured_dir = state_dir or os.environ.get("ORCHESTRATOR_ADMISSION_DIR")
        self.state_dir = Path(configured_dir or (Path.home() / ".local/state/orchestrator"))
        self.clock = clock or time.time
        self.path = self.state_dir / STATE_NAME
        self.lock_path = self.state_dir / LOCK_NAME

    @contextlib.contextmanager
    def _locked(self):
        if self.state_dir.is_symlink():
            raise JobError("invalid_input", "admission state directory must not be a symlink")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _load(self):
        if self.path.is_symlink():
            raise JobError("invalid_input", "admission state file must not be a symlink")
        try:
            state = fields(load_json(self.path, 16777216),
                           ["schema_version", "leases", "backoffs", "updated_at"], label="admission state")
        except FileNotFoundError:
            return {"schema_version": 1, "leases": [], "backoffs": [], "updated_at": self.clock()}
        version(state["schema_version"])
        if not isinstance(state["leases"], list) or not isinstance(state["backoffs"], list):
            raise JobError("invalid_input", "admission state leases and backoffs must be arrays")
        for lease in state["leases"]:
            fields(lease, ["lease_id", "provider", "model", "run_id", "acquired_at", "cap"], label="admission lease")
            text(lease["lease_id"], "lease_id", 512)
            lease["provider"] = normalize_provider(lease["provider"])
            lease["model"] = normalize_model(lease["model"])
            text(lease["run_id"], "run_id", 1024)
            integer(lease["cap"], "cap", 1, 64)
            self._timestamp(lease["acquired_at"], "acquired_at")
        original_backoff_keys = set()
        for backoff in state["backoffs"]:
            fields(backoff, ["provider", "model", "until", "recorded_at", "source"], label="provider backoff")
            original_key = (text(backoff["provider"], "backoff provider").casefold(),
                            text(backoff["model"], "backoff model").strip().casefold())
            if original_key in original_backoff_keys:
                raise JobError("invalid_input", "admission state contains duplicate backoff keys")
            original_backoff_keys.add(original_key)
            backoff["provider"] = normalize_provider(backoff["provider"])
            backoff["model"] = normalize_model(backoff["model"])
            self._timestamp(backoff["until"], "backoff until")
            self._timestamp(backoff["recorded_at"], "backoff recorded_at")
            text(backoff["source"], "backoff source", 100)
        # Older alias-keyed backoffs can collide after pool migration; retain the later reset.
        merged = {}
        for item in state["backoffs"]:
            key = (item["provider"], item["model"].casefold())
            if key not in merged or item["until"] > merged[key]["until"]:
                merged[key] = item
        state["backoffs"] = list(merged.values())
        self._unique(state)
        return state

    @staticmethod
    def _timestamp(value, label):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise JobError("invalid_input", f"{label} must be a finite timestamp")

    @staticmethod
    def _unique(state):
        lease_ids = [lease["lease_id"] for lease in state["leases"]]
        keys = [(item["provider"].casefold(), item["model"].casefold()) for item in state["backoffs"]]
        if len(set(lease_ids)) != len(lease_ids) or len(set(keys)) != len(keys):
            raise JobError("invalid_input", "admission state contains duplicate lease or backoff keys")

    def _save(self, state):
        state["updated_at"] = self.clock()
        save(self.path, state)

    @staticmethod
    def _prune_backoffs(state, now):
        retained = [item for item in state["backoffs"] if item["until"] > now]
        changed = len(retained) != len(state["backoffs"])
        state["backoffs"] = retained
        return changed

    def acquire(self, provider, model, lease_id, cap, run_id=None, bypass_admission=False):
        provider_key = normalize_provider(provider)
        model_key = normalize_model(model)
        lease_id = text(lease_id, "lease_id", 512)
        cap = integer(cap, "cap", 1, 64)
        run_id = lease_id if run_id in (None, "") else text(run_id, "run_id", 1024)
        with self._locked():
            state = self._load()
            now = self.clock()
            pruned = self._prune_backoffs(state, now)
            existing = next((item for item in state["leases"] if item["lease_id"] == lease_id), None)
            if existing:
                if (existing["provider"].casefold(), existing["model"].casefold()) != (provider_key, model_key):
                    raise JobError("conflict", "lease_id already belongs to a different provider or model")
                backoff = max((item for item in state["backoffs"]
                                if item["provider"].casefold() == provider_key
                                and item["model"].casefold() in (model_key, "*") and item["until"] > now),
                              key=lambda item: item["until"], default=None)
                if backoff and not bypass_admission:
                    if pruned:
                        self._save(state)
                    return self._admission_result(state, provider_key, model_key, cap, admitted=False,
                                                  reason="backoff", retry_at=backoff["until"],
                                                  lease_id=lease_id, lease_held=True)
                if pruned:
                    self._save(state)
                return self._admission_result(state, provider_key, model_key, cap, admitted=True, lease_id=lease_id)

            backoff = max((item for item in state["backoffs"]
                            if item["provider"].casefold() == provider_key
                            and item["model"].casefold() in (model_key, "*") and item["until"] > now),
                              key=lambda item: item["until"], default=None)
            if backoff and not bypass_admission:
                result = self._admission_result(state, provider_key, model_key, cap, admitted=False,
                                                reason="backoff", retry_at=backoff["until"])
                if pruned:
                    self._save(state)
                return result

            active = sum(item["provider"].casefold() == provider_key for item in state["leases"])
            if active >= cap and not bypass_admission:
                result = self._admission_result(state, provider_key, model_key, cap, admitted=False,
                                                reason="capacity")
                if pruned:
                    self._save(state)
                return result

            state["leases"].append({"lease_id": lease_id, "provider": provider_key,
                                    "model": model_key, "run_id": run_id,
                                    "acquired_at": now, "cap": cap})
            self._save(state)
            return self._admission_result(state, provider_key, model_key, cap,
                                          admitted=True, lease_id=lease_id)

    @staticmethod
    def _admission_result(state, provider, model, cap, admitted, lease_id=None,
                          reason=None, retry_at=None, lease_held=None):
        provider_active = sum(item["provider"].casefold() == provider for item in state["leases"])
        model_active = sum(item["provider"].casefold() == provider and item["model"].casefold() == model
                           for item in state["leases"])
        if lease_held is None:
            lease_held = bool(lease_id)
        return {"admitted": admitted, "provider": provider, "model": model,
                "provider_active": provider_active, "model_active": model_active,
                "cap": cap, "lease_id": lease_id, "reason": reason, "retry_at": retry_at,
                "lease_held": lease_held}

    def release(self, lease_id):
        lease_id = text(lease_id, "lease_id", 512)
        with self._locked():
            state = self._load()
            self._prune_backoffs(state, self.clock())
            before = len(state["leases"])
            state["leases"] = [item for item in state["leases"] if item["lease_id"] != lease_id]
            self._save(state)
            return {"released": len(state["leases"]) != before, "lease_id": lease_id,
                    "active_leases": len(state["leases"])}

    def note_rate_limit(self, provider, model, delay_seconds, source="metadata"):
        provider_key = normalize_provider(provider)
        model_key = normalize_model(model)
        if type(delay_seconds) not in (int, float) or not math.isfinite(delay_seconds) or delay_seconds < 0:
            raise JobError("invalid_input", "backoff delay must be a finite nonnegative number")
        source = text(source, "backoff source", 100)
        with self._locked():
            state = self._load()
            now = self.clock()
            self._prune_backoffs(state, now)
            retry_at = now + delay_seconds
            existing = next((item for item in state["backoffs"]
                             if item["provider"].casefold() == provider_key
                             and item["model"].casefold() == model_key), None)
            if existing:
                retry_at = max(retry_at, existing["until"])
                existing.update(until=retry_at, recorded_at=now, source=source)
            else:
                state["backoffs"].append({"provider": provider_key, "model": model_key,
                                          "until": retry_at, "recorded_at": now,
                                          "source": source})
            self._save(state)
            return {"provider": provider_key, "model": model_key, "retry_at": retry_at,
                    "delay_seconds": max(0, retry_at - now), "source": source}

    def status(self, provider=None, model=None):
        provider_key = normalize_provider(provider) if provider is not None else None
        model_key = normalize_model(model) if model is not None else None
        if model_key is not None and provider_key is None:
            raise JobError("invalid_input", "status --model requires --provider")
        with self._locked():
            state = self._load()
            now = self.clock()
            pruned = self._prune_backoffs(state, now)
            active = [item for item in state["leases"]
                      if provider_key is None or item["provider"].casefold() == provider_key]
            if model_key is not None:
                active = [item for item in active if item["model"].casefold() == model_key]
            backoffs = [item for item in state["backoffs"]
                        if item["until"] > now
                        and (provider_key is None or item["provider"].casefold() == provider_key)
                        and (model_key is None or item["model"].casefold() in (model_key, "*"))]
            if pruned:
                self._save(state)
            return {"provider": provider_key, "model": model_key,
                    "active_count": len(active), "leases": active, "backoffs": backoffs,
                    "updated_at": state["updated_at"]}


def policy_admission(path=POLICY_PATH):
    policy = policy_record(load_json(path))
    return policy["provider_admission"]


def parser():
    result = argparse.ArgumentParser(description="Share provider launch admission and rate-limit backoff across runs.")
    commands = result.add_subparsers(dest="operation", required=True)
    acquire = commands.add_parser("acquire", help="reserve one provider slot before launching")
    acquire.add_argument("--provider", required=True)
    acquire.add_argument("--model", required=True)
    acquire.add_argument("--lease-id", required=True)
    acquire.add_argument("--run-id", default="")
    acquire.add_argument("--policy", default=str(POLICY_PATH))
    release = commands.add_parser("release", help="release a terminal or unused provider slot")
    release.add_argument("--lease-id", required=True)
    status = commands.add_parser("status", help="show provider counts and active backoffs")
    status.add_argument("--provider")
    status.add_argument("--model")
    rate_limit = commands.add_parser("rate-limit", help="record a provider/model rate-limit backoff")
    rate_limit.add_argument("--provider", required=True)
    rate_limit.add_argument("--model", required=True, help="exact model, or '*' for observed billing-pool exhaustion")
    choice = rate_limit.add_mutually_exclusive_group()
    choice.add_argument("--retry-after", help="Retry-After delta seconds or HTTP date")
    choice.add_argument("--reset-at", help="provider reset timestamp, clock time, or relative duration")
    rate_limit.add_argument("--policy", default=str(POLICY_PATH))
    rate_limit.add_argument("--continuation-record", help="task-scoped JSON record for a direct worker continuation")
    rate_limit.add_argument("--task-id", help="task identity for the continuation record")
    rate_limit.add_argument("--pane-id", help="exact existing worker pane for the continuation")
    rate_limit.add_argument("--attempt-id", help="current attempt identity for the continuation")
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        state_dir = os.environ.get("ORCHESTRATOR_ADMISSION_DIR") or Path.home() / ".local/state/orchestrator"
        store = AdmissionStore(state_dir)
        if args.operation == "acquire":
            config = policy_admission(args.policy)
            provider = normalize_provider(args.provider)
            cap = config["provider_caps"].get(provider, config["default_provider_cap"])
            result = store.acquire(provider, args.model, args.lease_id, cap, args.run_id)
        elif args.operation == "release":
            result = store.release(args.lease_id)
        elif args.operation == "status":
            result = store.status(args.provider, args.model)
        else:
            continuation_values = (args.continuation_record, args.task_id, args.pane_id, args.attempt_id)
            if any(value is not None for value in continuation_values) and not all(
                    value is not None for value in continuation_values):
                raise JobError("invalid_input", "continuation record requires --continuation-record, --task-id, --pane-id, and --attempt-id")
            config = policy_admission(args.policy)
            delay, source = backoff_delay(args.retry_after, args.reset_at,
                                          config["default_backoff_seconds"])
            result = store.note_rate_limit(args.provider, args.model, delay, source)
            if args.continuation_record is not None:
                write_continuation_record(args.continuation_record, args.task_id, args.pane_id,
                                          result["retry_at"], args.attempt_id)
                result["continuation_record"] = str(Path(args.continuation_record).absolute())
        print(encoded(result).decode())
        return 0
    except JobError as error:
        print(encoded({"error": error.kind, "message": str(error)}).decode())
        return {"invalid_input": 2, "conflict": 3, "io_error": 7}.get(error.kind, 2)
    except (OSError, UnicodeError) as error:
        print(encoded({"error": "io_error", "message": str(error)}).decode())
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
