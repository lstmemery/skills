"""Run git/worktree-sensitive tests in a fresh primary checkout (card #1264).

A test that shells out to git or inspects worktree layout can pass in a linked
worktree (where `.git` is a file and the git dir differs from the common dir)
and fail in a primary checkout. Integration therefore runs the tests listed in
the target tree's manifest in a freshly cloned primary checkout of the merged
result, and records the checkout context for each test module.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MANIFEST = "skills/orchestrator/tests/checkout-sensitive.json"
CONTEXT_KIND = "fresh-primary-checkout"
MODULE = re.compile(r"^test_[A-Za-z0-9_]+$")
SUMMARY_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
SUMMARY_FAILED = re.compile(r"^FAILED \((.*)\)$", re.MULTILINE)
SUMMARY_SKIPPED = re.compile(r"\bskipped=(\d+)")
SUMMARY_STATUS = re.compile(r"^(?:OK|FAILED) \((.*)\)$", re.MULTILINE)
MODULE_TIMEOUT_SECONDS = 1800


class CheckoutError(Exception):
    """The fresh primary checkout could not be established."""


def _git(cwd, *args, check=True):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
        env=environment, check=False,
    )
    if check and result.returncode != 0:
        raise CheckoutError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def load_manifest(tree):
    """Return (cwd, modules) from the manifest in `tree`, or None if absent."""
    path = Path(tree) / MANIFEST
    if not path.is_file():
        return None
    try:
        return parse_manifest(path.read_text(), tree)
    except OSError as error:
        raise CheckoutError(f"invalid checkout-sensitive manifest: {error}") from error


def parse_manifest(text, tree):
    try:
        data = json.loads(text)
        cwd = data["cwd"]
        modules = [entry["module"] for entry in data["tests"]]
    except (ValueError, KeyError, TypeError) as error:
        raise CheckoutError(f"invalid checkout-sensitive manifest: {error}") from error
    if not isinstance(data, dict):
        raise CheckoutError("invalid checkout-sensitive manifest: not an object")
    if data.get("version") != 1 or not isinstance(cwd, str) or not modules:
        raise CheckoutError("invalid checkout-sensitive manifest: version 1 with tests required")
    if len(set(modules)) != len(modules) or not all(
        isinstance(m, str) and MODULE.match(m) for m in modules
    ):
        raise CheckoutError("invalid checkout-sensitive manifest: bad or duplicate module names")
    root = Path(tree).resolve()
    if not (root / cwd / "tests").is_dir() or not (root / cwd).resolve().is_relative_to(root):
        raise CheckoutError("invalid checkout-sensitive manifest: cwd has no tests directory")
    return cwd, modules


def target_manifest(checkout, target_head):
    """Manifest declared at the target head itself, or None if it has none."""
    shown = _git(checkout, "show", f"{target_head}:{MANIFEST}", check=False)
    if shown.returncode != 0:
        return None
    return parse_manifest(shown.stdout, checkout)


def describe_context(checkout):
    """Observe, not assume, that `checkout` is a primary (non-linked) checkout."""
    git_dir = _git(checkout, "rev-parse", "--absolute-git-dir").stdout.strip()
    common = _git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    primary = (
        Path(git_dir).resolve() == Path(common).resolve()
        and (Path(checkout) / ".git").is_dir()
    )
    return {
        "kind": CONTEXT_KIND,
        "path": str(checkout),
        "git_dir": git_dir,
        "git_common_dir": common,
        "dot_git_is_directory": (Path(checkout) / ".git").is_dir(),
        "primary": primary,
        "head": _git(checkout, "rev-parse", "HEAD").stdout.strip(),
    }


def build_checkout(repo, target_head, candidate_heads, parent):
    """Clone `repo` fresh, check out target_head and merge each candidate head."""
    checkout = Path(parent) / "primary"
    result = subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(repo), str(checkout)],
        capture_output=True, text=True, check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    if result.returncode != 0:
        raise CheckoutError(f"fresh clone failed: {result.stderr.strip()}")
    # Candidate branches may be unpublished refs; fetch the pinned commits explicitly.
    for sha in [target_head, *candidate_heads]:
        _git(checkout, "fetch", "--quiet", str(repo), sha)
    _git(checkout, "checkout", "--quiet", "--detach", target_head)
    for sha in candidate_heads:
        merged = _git(
            checkout, "-c", "user.name=preflight", "-c", "user.email=preflight@invalid",
            "merge", "--no-edit", "--no-ff", sha, check=False,
        )
        if merged.returncode != 0:
            raise CheckoutError(f"candidate {sha} does not merge cleanly into the target: "
                                f"{(merged.stdout + merged.stderr).strip()[:300]}")
    return checkout


def run_module(checkout, cwd, module):
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", f"{module}.py"]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run(
            command, cwd=Path(checkout) / cwd, capture_output=True, text=True,
            env=environment, timeout=MODULE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"module": module, "status": "failed", "returncode": None, "tests_run": 0,
                "detail": f"timed out after {MODULE_TIMEOUT_SECONDS}s"}
    output = result.stdout + result.stderr
    ran = SUMMARY_RAN.search(output)
    tests_run = int(ran.group(1)) if ran else 0
    skipped = 0
    status_line = SUMMARY_STATUS.search(output)
    if status_line:
        match = SUMMARY_SKIPPED.search(status_line.group(1))
        skipped = int(match.group(1)) if match else 0
    executed = tests_run - skipped
    failed = SUMMARY_FAILED.search(output)
    # A module that discovers or executes no tests proves nothing (a fully
    # skipped module included), so it cannot pass.
    passed = result.returncode == 0 and executed > 0
    record = {"module": module, "status": "passed" if passed else "failed",
              "returncode": result.returncode, "tests_run": tests_run,
              "tests_skipped": skipped}
    if failed:
        record["summary"] = failed.group(1)
    if not passed:
        record["detail"] = "\n".join(output.strip().splitlines()[-25:])
    return record


def run_checkout_preflight(repo, target_head, candidate_heads):
    """Return a report dict; `status` is ready, blocked or not_applicable."""
    parent = tempfile.mkdtemp(prefix="preflight-primary-")
    try:
        try:
            checkout = build_checkout(repo, target_head, candidate_heads, parent)
            manifest = load_manifest(checkout)
            required = target_manifest(checkout, target_head)
            if required is not None:
                # The target already opted in: a candidate must not switch the
                # stage off or drop declared modules.
                if manifest is None:
                    raise CheckoutError(f"candidate removes {MANIFEST} declared at the target head")
                dropped = [m for m in required[1] if m not in manifest[1]]
                if dropped or required[0] != manifest[0]:
                    raise CheckoutError("candidate drops checkout-sensitive modules or changes "
                                        f"cwd declared at the target head: {', '.join(dropped)}")
            if manifest is None:
                return {"status": "not_applicable",
                        "reason": f"no {MANIFEST} in the merged tree", "tests": []}
            cwd, modules = manifest
            context = describe_context(checkout)
            if not context["primary"]:
                raise CheckoutError("fresh checkout is not a primary checkout")
        except CheckoutError as error:
            return {"status": "blocked", "issues": [str(error)], "tests": []}
        tests = []
        for module in modules:
            record = run_module(checkout, cwd, module)
            record["checkout_context"] = {k: context[k] for k in ("kind", "git_dir", "primary", "head")}
            tests.append(record)
        failed = [t["module"] for t in tests if t["status"] != "passed"]
        report = {"status": "blocked" if failed else "ready", "context": context,
                  "merged_candidates": list(candidate_heads), "tests": tests}
        if failed:
            report["issues"] = [f"git/worktree-sensitive tests failed in a fresh primary "
                                f"checkout: {', '.join(failed)}"]
        return report
    finally:
        shutil.rmtree(parent, ignore_errors=True)
