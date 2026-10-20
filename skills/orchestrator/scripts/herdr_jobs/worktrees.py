"""Local Git and legacy Treehouse lease parsing and worktree verification."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .records import JobError, encoded, is_commit_id, load_json, parse_json, repository_worktree_record


TREEHOUSE_GET_FIELDS = {"path", "lease_id", "lease_holder", "name", "leased_at",
                       "branch", "status", "flavor", "detached", "recovery_reason",
                       "processes"}
TREEHOUSE_STATUS_FIELDS = {"name", "path", "status", "flavor", "lease_id", "lease_holder",
                           "leased_at", "processes"}
TREEHOUSE_STATUS_OPTIONAL_FIELDS = {"branch", "detached", "recovery_reason"}
GIT_LEASE_PREFIX = "git-worktree-"
GIT_LEASE_MARKER = "orchestrator-lease.json"


def unavailable(message):
    raise JobError("unavailable_capability", message)


def _string(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
        unavailable(f"{label} is missing or invalid")
    return value


def _validate_slot_metadata(value, label):
    for field in ("branch", "recovery_reason"):
        if field in value and (not isinstance(value[field], str) or "\0" in value[field]
                               or len(value[field]) > 4096):
            unavailable(f"{label} {field} is invalid")
    if "detached" in value and not isinstance(value["detached"], bool):
        unavailable(f"{label} detached state is invalid")
    if "status" in value:
        _string(value["status"], f"{label} status")
    if "flavor" in value:
        _string(value["flavor"], f"{label} flavor")
    if "processes" in value and not isinstance(value["processes"], list):
        unavailable(f"{label} process list is invalid")


def parse_allocation(output, expected_holder):
    """Accept only Treehouse's lease JSON shape; plain paths are never leases."""
    if not output or not output.strip():
        unavailable("treehouse get returned empty lease output; no Git command or worker launch is allowed")
    try:
        value = parse_json(output)
    except JobError as error:
        unavailable(f"treehouse get returned unrecognized lease JSON: {error}")
    if (not isinstance(value, dict) or set(value) - TREEHOUSE_GET_FIELDS
            or not {"path", "lease_id", "lease_holder"} <= value.keys()):
        unavailable("treehouse get returned an unrecognized lease record")
    _validate_slot_metadata(value, "treehouse lease")
    path = _string(value["path"], "treehouse lease path")
    if not Path(path).is_absolute():
        unavailable("treehouse lease path must be absolute")
    lease_id = _string(value["lease_id"], "treehouse lease identity")
    holder = _string(value["lease_holder"], "treehouse lease holder")
    if holder != expected_holder:
        unavailable("treehouse lease holder does not match the requested task")
    if "name" in value:
        _string(value["name"], "treehouse slot name")
    if "leased_at" in value and value["leased_at"] is not None:
        _string(value["leased_at"], "treehouse lease timestamp")
    return {"path": path, "lease_id": lease_id, "lease_holder": holder}


def _decode(output, label):
    try:
        return output.decode("utf-8").strip()
    except UnicodeError as error:
        unavailable(f"{label} returned non-UTF-8 output: {error}")


def _call(run_command, argv, cwd, label):
    try:
        code, output, _ = run_command(argv, cwd)
    except JobError:
        raise
    except (OSError, subprocess.TimeoutExpired):
        unavailable(f"{label} could not be read")
    if code != 0:
        unavailable(f"{label} exited with status {code}")
    return output


def _canonical(value, label, strict=True):
    try:
        return Path(value).resolve(strict=strict)
    except (OSError, RuntimeError):
        unavailable(f"{label} does not resolve to an available path")


def _status_entry(output, path, lease_id, holder):
    try:
        entries = parse_json(output)
    except JobError as error:
        unavailable(f"treehouse status returned unrecognized JSON: {error}")
    if not isinstance(entries, list):
        unavailable("treehouse status returned an unrecognized pool response")
    matches = []
    for entry in entries:
        if (not isinstance(entry, dict)
                or set(entry) - (TREEHOUSE_STATUS_FIELDS | TREEHOUSE_STATUS_OPTIONAL_FIELDS)
                or not TREEHOUSE_STATUS_FIELDS <= entry.keys()):
            unavailable("treehouse status returned an unrecognized pool entry")
        _validate_slot_metadata(entry, "treehouse status")
        for key in ("name", "path", "status", "flavor"):
            _string(entry[key], f"treehouse status {key}")
        for key in ("lease_id", "lease_holder"):
            if not isinstance(entry[key], str) or "\0" in entry[key] or len(entry[key]) > 4096:
                unavailable(f"treehouse status {key} is invalid")
        if entry["leased_at"] is not None and not isinstance(entry["leased_at"], str):
            unavailable("treehouse status lease timestamp is invalid")
        if not isinstance(entry["processes"], list):
            unavailable("treehouse status process list is invalid")
        entry_path = _string(entry["path"], "treehouse status path")
        if _canonical(entry_path, "treehouse status path", strict=False) == path:
            matches.append(entry)
    if len(matches) != 1:
        unavailable("the leased worktree is not uniquely present in treehouse status")
    entry = matches[0]
    if (entry["status"] != "leased" or entry["lease_id"] != lease_id
            or entry["lease_holder"] != holder):
        unavailable("treehouse status does not confirm the recorded active lease")
    return entry


def _git_text(run_command, git, cwd, *args):
    output = _call(run_command, [git, "-C", str(cwd), *args], cwd, "Git verification")
    value = _decode(output, "Git verification")
    if not value:
        unavailable("Git verification returned an empty value")
    return value


def _worktree_paths(output):
    paths = []
    for field in output.split(b"\0"):
        if field.startswith(b"worktree "):
            try:
                paths.append(Path(field[len(b"worktree "):].decode("utf-8")).resolve(strict=True))
            except (UnicodeError, OSError, RuntimeError):
                unavailable("Git returned an invalid registered worktree path")
    return paths


def _git_identity(worktree, repo_hint, run_command, expected_common=None, expected_base=None):
    git = shutil.which("git")
    if not git:
        unavailable("git is unavailable for repository worktree verification")
    repo_root = _canonical(_git_text(run_command, git, repo_hint, "rev-parse", "--show-toplevel"),
                           "expected Git repository")
    if worktree == repo_root:
        unavailable("repository writers must use a linked worktree, not the main checkout")
    worktree_root = _canonical(_git_text(run_command, git, worktree, "rev-parse", "--show-toplevel"),
                               "writer Git root")
    if worktree_root != worktree:
        unavailable("writer cwd is not the root of its linked worktree")
    repo_common = _canonical(_git_text(run_command, git, repo_root, "rev-parse", "--path-format=absolute",
                                       "--git-common-dir"), "expected Git common directory")
    worktree_common = _canonical(_git_text(run_command, git, worktree, "rev-parse", "--path-format=absolute",
                                           "--git-common-dir"), "writer Git common directory")
    if repo_root != repo_hint or worktree_common != repo_common:
        unavailable("writer is not a linked worktree of the expected repository")
    if expected_common is not None and worktree_common != _canonical(expected_common, "recorded Git common directory"):
        unavailable("writer is not a linked worktree of the recorded repository")
    registered = _call(run_command, [git, "-C", str(repo_root), "worktree", "list", "--porcelain", "-z"],
                       repo_root, "Git worktree list")
    paths = _worktree_paths(registered)
    if not paths or paths[0] == worktree or worktree not in paths:
        unavailable("writer cwd is not a registered linked worktree of the expected repository")
    base = _git_text(run_command, git, worktree, "rev-parse", "HEAD").lower()
    if not is_commit_id(base):
        unavailable("Git returned an invalid worktree base commit")
    if expected_base is not None and base != expected_base.lower():
        unavailable("writer worktree HEAD no longer matches its recorded base commit")
    return repo_root, worktree_common, base


def _worktree_git_dir(worktree, run_command):
    git = shutil.which("git")
    if not git:
        unavailable("git is unavailable for repository worktree verification")
    return _canonical(_git_text(run_command, git, worktree, "rev-parse", "--absolute-git-dir"),
                      "writer Git metadata directory")


def _verify_git_lease_marker(record, worktree, run_command):
    marker = _worktree_git_dir(worktree, run_command) / GIT_LEASE_MARKER
    try:
        recorded = load_json(marker, 16384)
    except FileNotFoundError:
        unavailable("local Git worktree lease marker is missing; reacquire a lease for this worktree")
    except JobError as error:
        unavailable(f"local Git worktree lease marker is invalid: {error}")
    except OSError as error:
        unavailable(f"local Git worktree lease marker cannot be read: {error.strerror}")
    if recorded != record:
        unavailable("local Git worktree lease record does not match its metadata marker")


def _verify_treehouse_lease(record, worktree, run_command, treehouse):
    treehouse_status = _call(run_command, [treehouse, "status", "--json"], None, "treehouse status")
    _status_entry(treehouse_status, worktree, record["lease_id"], record["lease_holder"])


def _worktree_root(repo_root, home):
    root = home / ".local/share/orchestrator/worktrees"
    try:
        resolved = _canonical(root, "Git worktree lease directory", strict=False)
        if home not in resolved.parents:
            unavailable("Git worktree lease directory must remain below the home directory")
        if resolved == repo_root or repo_root in resolved.parents:
            unavailable("Git worktree lease directory must be outside the requested repository")
        resolved.mkdir(parents=True, exist_ok=True)
        return _canonical(resolved, "Git worktree lease directory")
    except OSError as error:
        unavailable(f"cannot create Git worktree lease directory {root}: {error.strerror}")


def _write_git_lease_marker(record, worktree, run_command):
    marker = _worktree_git_dir(worktree, run_command) / GIT_LEASE_MARKER
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        unavailable(f"cannot write local Git worktree lease marker {marker}: {error.strerror}")


def verify_repository_worktree(record, cwd, run_command, treehouse="treehouse"):
    """Refuse a writer cwd unless its lease and Git identity match the record."""
    record = repository_worktree_record(record)
    worktree = _canonical(record["path"], "recorded worktree")
    supplied_cwd = _canonical(cwd, "writer cwd")
    if worktree != supplied_cwd:
        unavailable("writer cwd does not match the canonical recorded lease path")
    home = _canonical(Path.home(), "home directory")
    repo_hint = _canonical(record["repo_root"], "expected repository")
    if worktree == home or worktree == repo_hint:
        unavailable("repository writers must use a linked worktree, not home or the expected checkout")

    _git_identity(worktree, repo_hint, run_command, record["git_common_dir"], record["base_commit"])
    if record["lease_id"].startswith(GIT_LEASE_PREFIX):
        _verify_git_lease_marker(record, worktree, run_command)
    else:
        _verify_treehouse_lease(record, worktree, run_command, treehouse)
    return record


def _run_command(argv, cwd):
    try:
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, check=False)
    except OSError as error:
        raise JobError("unavailable_capability", f"cannot execute {argv[0]}: {error.strerror}") from error
    except subprocess.TimeoutExpired as error:
        raise JobError("unavailable_capability", f"{argv[0]} exceeded the 30 second verification limit") from error
    return result.returncode, result.stdout, result.stderr


def acquire(repo, holder, expected_base=None, *, run_command=_run_command):
    """Create and verify an isolated linked worktree without a Treehouse service."""
    repo_hint = _canonical(repo, "requested repository")
    if not repo_hint.is_dir():
        unavailable("requested repository must be an existing directory")
    if not isinstance(holder, str) or not holder.strip() or "\0" in holder or len(holder) > 4096:
        raise JobError("invalid_input", "lease holder must be nonempty text")
    if expected_base is not None and not is_commit_id(expected_base):
        raise JobError("invalid_input", "expected base must be a full Git commit ID")
    git = shutil.which("git")
    if not git:
        unavailable("git is unavailable; install Git before acquiring a repository worktree lease")
    code, output, _ = run_command([git, "-C", str(repo_hint), "rev-parse", "--show-toplevel"], repo_hint)
    if code != 0:
        unavailable("requested repository is not a readable Git checkout")
    repo_root = _canonical(_decode(output, "Git repository check"), "requested Git repository")
    if repo_root != repo_hint:
        unavailable("--repo must name the root of the expected Git checkout")
    if expected_base is None:
        code, output, _ = run_command([git, "-C", str(repo_hint), "rev-parse", "HEAD"], repo_hint)
        if code != 0:
            unavailable("requested Git repository has no readable HEAD commit")
        base = _decode(output, "Git HEAD lookup").lower()
    else:
        base = expected_base.lower()
    if not is_commit_id(base):
        unavailable("resolved base is not a full Git commit ID")
    if expected_base is not None:
        code, _, _ = run_command([git, "-C", str(repo_hint), "cat-file", "-e", f"{base}^{{commit}}"],
                                 repo_hint)
        if code != 0:
            unavailable("expected base commit is unavailable in the requested repository; fetch or choose a present commit")

    home = _canonical(Path.home(), "home directory")
    root = _worktree_root(repo_hint, home)

    lease_id = GIT_LEASE_PREFIX + uuid.uuid4().hex
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_hint.name).strip("-.")[:48] or "repository"
    worktree = root / f"{safe_name}-{lease_id[len(GIT_LEASE_PREFIX):]}"
    add_argv = [git, "-C", str(repo_hint), "worktree", "add", "--detach", str(worktree), base]
    code, _, error = run_command(add_argv, repo_hint)
    if code != 0:
        detail = _decode(error, "Git worktree creation").strip()[:1000]
        suffix = f": {detail}" if detail else ""
        unavailable(f"Git could not create a linked worktree at {worktree}{suffix}; check repository access and base commit")

    try:
        worktree = _canonical(worktree, "created Git worktree")
        if not worktree.is_dir() or worktree == home or worktree == repo_hint:
            unavailable("Git did not create a separate linked worktree")
        repo_root, worktree_common, actual_base = _git_identity(
            worktree, repo_hint, run_command, expected_base=base)
        record = {"schema_version": 1, "path": str(worktree), "lease_id": lease_id,
                  "lease_holder": holder, "repo_root": str(repo_root),
                  "git_common_dir": str(worktree_common), "base_commit": actual_base}
        _write_git_lease_marker(record, worktree, run_command)
        _verify_git_lease_marker(record, worktree, run_command)
        return record
    except JobError as error:
        try:
            cleanup_code, _, cleanup_error = run_command(
                [git, "-C", str(repo_hint), "worktree", "remove", "--force", str(worktree)], repo_hint)
        except (JobError, OSError, subprocess.TimeoutExpired) as cleanup_error:
            unavailable(f"{error}; created worktree may remain at {worktree}; inspect and remove it with git worktree remove ({cleanup_error})")
        if cleanup_code != 0:
            detail = _decode(cleanup_error, "Git worktree cleanup").strip()[:500]
            suffix = f": {detail}" if detail else ""
            unavailable(f"{error}; created worktree could not be removed at {worktree}{suffix}; inspect and remove it with git worktree remove")
        raise
