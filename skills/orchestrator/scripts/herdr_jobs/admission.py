"""Shared, locked provider admission and rate-limit backoff state."""

import argparse
import contextlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time

from .records import JobError, encoded, fields, integer, load_json, policy_record, save, text, version


STATE_NAME = "provider-admission.json"
LOCK_NAME = ".provider-admission.lock"
POLICY_PATH = Path(__file__).resolve().parents[2] / "launch-policy.json"


def normalize_provider(value):
    value = text(value, "provider", 100).strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value) is None:
        raise JobError("invalid_input", "provider must be a simple provider identifier")
    return value.casefold()


def normalize_model(value):
    return text(value, "model", 200).strip().casefold()


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


def backoff_delay(retry_after=None, reset_at=None, default_seconds=60, now=None):
    now = time.time() if now is None else now
    delay = _retry_after(retry_after, now)
    if delay is not None:
        return delay, "retry-after"
    delay = _reset_delay(reset_at, now)
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
        or re.search(
            r"(?im)^\s*(?:⚠\s*)?You(?:'ve| have) hit your (?:ChatGPT )?usage limit(?:\b|[.!])",
            output,
        )
    )


def _http_429_status_line(output):
    return re.search(r"(?im)^\s*HTTP(?:/[0-9]+(?:\.[0-9]+)?)?\s+429(?:\s|$)", output) is not None


def rate_limit_from_output(output, now=None, *, source="pane", runtime=None, exit_code=None):
    """Return metadata only for known provider errors, never for generic transcript prose."""
    output = str(output)
    if source == "harness_error":
        explicit_error = exit_code not in (None, 0) and (
            _http_429_status_line(output)
            or _pi_provider_error(output)
            or (runtime == "codex" and _codex_rate_limit_banner(output))
        )
    elif source == "pane" and runtime == "pi":
        explicit_error = _pi_provider_error(output)
    elif source == "pane" and runtime == "codex":
        explicit_error = _codex_rate_limit_banner(output)
    else:
        explicit_error = False
    if not explicit_error:
        return None

    retry_match = re.search(r"(?im)^\s*retry[-_ ]after\s*[:=]\s*([^\r\n]+)", output)
    if retry_match is None:
        retry_match = re.search(r"(?i)[\"']retry[-_ ]after[\"']\s*:\s*[\"']?([^\"',}\s]+)", output)
    reset_match = re.search(
        r"(?im)^\s*(?:x-)?rate[-_ ]?limit[-_ ]?reset(?:[-_ ](?:requests|tokens))?\s*[:=]\s*([^\r\n,;]+)",
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
        delay = _retry_after(retry_after, time.time() if now is None else now)
        if delay is not None:
            source = "retry-after"
    if delay is None and reset_at is not None:
        delay = _reset_delay(reset_at, time.time() if now is None else now)
        if delay is not None:
            source = "reset"
    return {"retry_after_seconds": delay, "source": source}


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
            normalize_provider(lease["provider"])
            normalize_model(lease["model"])
            text(lease["run_id"], "run_id", 1024)
            integer(lease["cap"], "cap", 1, 64)
            self._timestamp(lease["acquired_at"], "acquired_at")
        for backoff in state["backoffs"]:
            fields(backoff, ["provider", "model", "until", "recorded_at", "source"], label="provider backoff")
            normalize_provider(backoff["provider"])
            normalize_model(backoff["model"])
            self._timestamp(backoff["until"], "backoff until")
            self._timestamp(backoff["recorded_at"], "backoff recorded_at")
            text(backoff["source"], "backoff source", 100)
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
                backoff = next((item for item in state["backoffs"]
                                if item["provider"].casefold() == provider_key
                                and item["model"].casefold() == model_key and item["until"] > now), None)
                if backoff and not bypass_admission:
                    if pruned:
                        self._save(state)
                    return self._admission_result(state, provider_key, model_key, cap, admitted=False,
                                                  reason="backoff", retry_at=backoff["until"],
                                                  lease_id=lease_id, lease_held=True)
                if pruned:
                    self._save(state)
                return self._admission_result(state, provider_key, model_key, cap, admitted=True, lease_id=lease_id)

            backoff = next((item for item in state["backoffs"]
                            if item["provider"].casefold() == provider_key
                            and item["model"].casefold() == model_key and item["until"] > now), None)
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
                        and (model_key is None or item["model"].casefold() == model_key)]
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
    rate_limit.add_argument("--model", required=True)
    choice = rate_limit.add_mutually_exclusive_group()
    choice.add_argument("--retry-after", help="Retry-After delta seconds or HTTP date")
    choice.add_argument("--reset-at", help="provider reset timestamp or delay in seconds")
    rate_limit.add_argument("--policy", default=str(POLICY_PATH))
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
            config = policy_admission(args.policy)
            delay, source = backoff_delay(args.retry_after, args.reset_at,
                                          config["default_backoff_seconds"])
            result = store.note_rate_limit(args.provider, args.model, delay, source)
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
