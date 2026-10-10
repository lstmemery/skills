"""CSV routing authority and native arguments, without launching workers."""

import csv
import io
import re

from .records import (JobError, fields, integer, invalid, read_regular, text,
                      validate_effort, effort_arguments)


HARNESS_ALIASES = {"claude": "claude-code"}
PROVIDER_ALIASES = {"opencode go": "opencode-go", "openai-codex": "codex",
                    "claude-code": "claude"}
TUPLE_FIELDS = ("model", "effort", "harness", "provider")
CSV_FIELDS = ["level", *[f"{choice}_{key}" for choice in ("primary", "fallback")
                         for key in TUPLE_FIELDS]]
SUPPORTED_PAIRS = {("codex", "codex"), ("pi", "opencode-go"), ("pi", "codex"),
                   ("claude-code", "claude")}


def billing_pool(provider):
    return PROVIDER_ALIASES.get(provider.strip().casefold(), provider.strip().casefold())


def validate_provider_harness(runtime, provider):
    if provider is not None and billing_pool(provider) == "opencode-go" and runtime != "pi":
        raise JobError("unavailable_capability", "OpenCode Go requires the Pi harness")


def _iso_timestamp(value, label):
    value = text(value, label, 128)
    # Evidence timestamps are deliberately kept as a syntactic contract.  The
    # coordinator records the source and timestamp; it does not infer either
    # from free-form prose.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[^\s]+(?:Z|[+-]\d{2}:?\d{2})", value) is None:
        invalid(f"{label}: expected an ISO-8601 timestamp")
    return value


def validate_fallback_evidence(value, reason):
    """Validate an observed provider failure before permitting a CSV fallback.

    A fallback is an operational transition, so a manifest must carry a
    structured observation rather than an assertion such as "quota unknown".
    The shape intentionally accepts only a source/time plus an error or a
    zero-remaining quota/reset observation.
    """
    fields(value, ["source", "observed_at"], ["error", "quota", "reset_at"],
           "fallback evidence")
    text(value["source"], "fallback evidence source", 512)
    _iso_timestamp(value["observed_at"], "fallback evidence observed_at")
    has_error = "error" in value
    has_quota = "quota" in value
    has_reset = "reset_at" in value
    if not (has_error or has_quota or has_reset):
        invalid("fallback evidence must include an observed error or quota/reset data")
    if has_error:
        error = fields(value["error"], ["kind", "message"], ["code", "retry_after_seconds"],
                       "fallback evidence error")
        if error["kind"] != reason:
            invalid(f"fallback evidence error kind must match {reason}")
        text(error["message"], "fallback evidence error message", 2000)
        if "code" in error:
            text(error["code"], "fallback evidence error code", 128)
        if "retry_after_seconds" in error:
            if type(error["retry_after_seconds"]) not in (int, float) or error["retry_after_seconds"] < 0:
                invalid("fallback evidence retry_after_seconds must be nonnegative")
        lower_message = error["message"].casefold()
        if any(marker in lower_message for marker in ("timeout", "timed out", "generic", "unknown", "slow")):
            invalid("fallback evidence cannot use a generic timeout or unknown error")
        if reason == "rate_limit" and "code" not in error and "retry_after_seconds" not in error and not has_reset:
            invalid("rate_limit fallback evidence requires a provider code, retry-after, or reset timestamp")
        if reason == "exhausted" and "code" not in error and not has_quota:
            invalid("exhausted fallback evidence requires a quota record or provider code")
    if has_quota:
        quota = fields(value["quota"], ["remaining"], ["reset_at"], "fallback evidence quota")
        if type(quota["remaining"]) is not int or quota["remaining"] < 0:
            invalid("fallback evidence quota.remaining must be a nonnegative integer")
        if reason == "exhausted" and quota["remaining"] != 0:
            invalid("exhausted fallback evidence requires quota.remaining=0")
        if "reset_at" in quota:
            _iso_timestamp(quota["reset_at"], "fallback evidence quota.reset_at")
    if has_reset:
        _iso_timestamp(value["reset_at"], "fallback evidence reset_at")
    if reason == "unavailable" and not has_error:
        invalid("unavailable fallback evidence requires a concrete provider error")
    if reason == "rate_limit" and not has_error and not has_reset:
        invalid("rate_limit fallback evidence requires an error or reset timestamp")
    if reason == "exhausted" and not has_error and not (has_quota and value["quota"]["remaining"] == 0):
        invalid("exhausted fallback evidence requires a zero quota or concrete error")
    return value


def validate_escalation(value, next_level):
    """Validate a recorded escalation and its authorization boundary."""
    fields(value, ["previous_level", "failure", "expected_improvement"],
           ["authorization"], label="escalation")
    previous_level = integer(value["previous_level"], "escalation.previous_level", 1, 7)
    if next_level <= previous_level:
        invalid("escalation target must be above the previous model level")
    if next_level != previous_level + 1 and value.get("authorization") != "user":
        invalid("multi-level escalation requires authorization=user")
    if "authorization" in value and value["authorization"] != "user":
        invalid("escalation.authorization must be user when present")
    failure = fields(value["failure"], ["kind", "source", "observed_at", "summary"],
                     label="escalation.failure")
    if failure["kind"] not in ("failure", "check"):
        invalid("escalation.failure.kind must be failure or check")
    text(failure["source"], "escalation.failure.source", 512)
    _iso_timestamp(failure["observed_at"], "escalation.failure.observed_at")
    text(failure["summary"], "escalation.failure.summary", 2000)
    text(value["expected_improvement"], "escalation.expected_improvement", 2000)
    return value


def complete_tuple(levels, level, pins, policy_runtime=None):
    """Complete a partial explicit route from the cheapest compatible primary tuple."""
    if level is not None:
        integer(level, "model_level", 1, 8)
    normalized = dict(pins)
    if "runtime" in normalized:
        normalized["runtime"] = HARNESS_ALIASES.get(normalized["runtime"], normalized["runtime"])
    if "provider" in normalized:
        normalized["provider"] = billing_pool(normalized["provider"])
    if "runtime" in normalized and "provider" in normalized:
        validate_provider_harness(normalized["runtime"], normalized["provider"])
    if policy_runtime is not None and "runtime" not in normalized:
        normalized["runtime"] = policy_runtime
    candidates = []
    levels_to_check = ((str(level),) if level is not None
                       else tuple(str(i) for i in range(1, 9)))
    for candidate_level in levels_to_check:
        requested = levels[candidate_level]["primary"]
        runtime = HARNESS_ALIASES.get(requested["harness"], requested["harness"])
        provider = billing_pool(requested["provider"])
        candidate = {"model": requested["model"], "effort": requested["effort"],
                     "runtime": runtime, "provider": provider}
        if all(candidate[key] == value for key, value in normalized.items()):
            validate_provider_harness(runtime, provider)
            if (runtime, provider) in SUPPORTED_PAIRS:
                candidates.append((int(candidate_level), candidate, requested))
    if not candidates:
        raise JobError("unavailable_capability",
                       "partial explicit route has no compatible validated model ladder tuple")
    chosen_level, candidate, requested = candidates[0]
    return {"level": chosen_level, "choice": "primary", "requested": dict(requested),
            **candidate, "fallback": None}


def load_ladder(path, data=None):
    try:
        data = read_regular(path, 65536) if data is None else data
        reader = csv.DictReader(io.StringIO(data.decode("utf-8")), strict=True)
        if reader.fieldnames != CSV_FIELDS:
            invalid("model ladder CSV must have the exact ordered primary/fallback header")
        levels = {}
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                invalid("model ladder CSV has an extra or missing column")
            if row["level"] not in tuple(str(i) for i in range(1, 9)):
                invalid("model ladder level must be 1–8")
            level = int(row["level"])
            if str(level) in levels:
                invalid(f"duplicate model ladder level: {level}")
            choices = {}
            for choice in ("primary", "fallback"):
                requested = {key: text(row[f"{choice}_{key}"], key, 200)
                             for key in TUPLE_FIELDS}
                if any(value != value.strip() for value in requested.values()):
                    invalid("model ladder values must not have surrounding whitespace")
                runtime = HARNESS_ALIASES.get(requested["harness"], requested["harness"])
                validate_provider_harness(runtime, requested["provider"])
                validate_effort(runtime, requested["effort"])
                choices[choice] = requested
            levels[str(level)] = choices
        if set(levels) != set(str(i) for i in range(1, 9)):
            invalid("model ladder must contain exactly levels 1–8")
        return levels
    except (csv.Error, UnicodeError) as error:
        invalid(f"malformed model ladder CSV: {error}")


def select(levels, level, fallback=None):
    integer(level, "model_level", 1, 8)
    choice = "primary"
    if fallback is not None:
        fields(fallback, ["reason", "evidence"], label="model_fallback")
        if fallback["reason"] not in ("unavailable", "rate_limit", "exhausted"):
            invalid("fallback requires concrete unavailable, rate_limit or exhausted evidence")
        validate_fallback_evidence(fallback["evidence"], fallback["reason"])
        choice = "fallback"
    requested = levels[str(level)][choice]
    runtime = HARNESS_ALIASES.get(requested["harness"], requested["harness"])
    provider = billing_pool(requested["provider"])
    validate_provider_harness(runtime, provider)
    if (runtime, provider) not in SUPPORTED_PAIRS:
        raise JobError("unavailable_capability",
                       f"unsupported {runtime}/{provider} tuple; retain the CSV, restore the exact "
                       "provider adapter or request an explicit route change; no harness substitution")
    return {"level": level, "choice": choice, "requested": dict(requested),
            "runtime": runtime, "provider": provider, "model": requested["model"],
            "effort": requested["effort"], "fallback": fallback}


def provider_arguments(runtime, provider):
    validate_provider_harness(runtime, provider)
    if runtime == "codex" and provider is not None:
        native = "openai" if billing_pool(provider) == "codex" else billing_pool(provider)
        return ["-c", f'model_provider="{native}"']
    if runtime == "pi" and provider is not None:
        native = "openai-codex" if billing_pool(provider) == "codex" else billing_pool(provider)
        return ["--provider", native]
    return []


def native_arguments(selection):
    runtime = selection["runtime"]
    return [*provider_arguments(runtime, selection["provider"]),
            "-m" if runtime == "codex" else "--model", selection["model"],
            *effort_arguments(runtime, selection["effort"])]
