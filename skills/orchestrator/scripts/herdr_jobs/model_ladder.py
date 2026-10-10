"""CSV routing authority and native arguments, without launching workers."""

import csv
import io

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
        text(fallback["evidence"], "fallback evidence", 10000)
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
