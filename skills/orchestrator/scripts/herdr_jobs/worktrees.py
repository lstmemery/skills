"""Treehouse lease parsing and linked-worktree verification."""

from pathlib import Path
import shutil
import subprocess

from .records import JobError, is_commit_id, parse_json, repository_worktree_record


TREEHOUSE_GET_FIELDS = {"path", "lease_id", "lease_holder", "name", "leased_at"}
TREEHOUSE_STATUS_FIELDS = {"name", "path", "status", "flavor", "lease_id", "lease_holder",
                           "leased_at", "processes"}


def unavailable(message):
    raise JobError("unavailable_capability", message)


def _string(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
        unavailable(f"{label} is missing or invalid")
    return value


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
    except (OSError, subprocess.TimeoutExpired) as error:
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
        if not isinstance(entry, dict) or set(entry) != TREEHOUSE_STATUS_FIELDS:
            unavailable("treehouse status returned an unrecognized pool entry")
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


def verify_repository_worktree(record, cwd, run_command, treehouse="treehouse"):
    """Refuse a writer cwd unless its current lease and Git identity match the record."""
    record = repository_worktree_record(record)
    worktree = _canonical(record["path"], "recorded worktree")
    supplied_cwd = _canonical(cwd, "writer cwd")
    if worktree != supplied_cwd:
        unavailable("writer cwd does not match the canonical recorded lease path")
    home = _canonical(Path.home(), "home directory")
    repo_hint = _canonical(record["repo_root"], "expected repository")
    if worktree == home or worktree == repo_hint:
        unavailable("repository writers must use a linked worktree, not home or the expected checkout")

    treehouse_status = _call(run_command, [treehouse, "status", "--json"], None, "treehouse status")
    _status_entry(treehouse_status, worktree, record["lease_id"], record["lease_holder"])
    _git_identity(worktree, repo_hint, run_command, record["git_common_dir"], record["base_commit"])
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


def acquire(repo, holder, expected_base=None, treehouse="treehouse", run_command=_run_command):
    repo_hint = _canonical(repo, "requested repository")
    if not repo_hint.is_dir():
        unavailable("requested repository must be an existing directory")
    if not holder.strip() or "\0" in holder:
        raise JobError("invalid_input", "lease holder must be nonempty text")
    if expected_base is not None and not is_commit_id(expected_base):
        raise JobError("invalid_input", "expected base must be a full Git commit ID")
    allocated = _call(run_command,
                      [treehouse, "get", "--lease", "--lease-holder", holder, "--json"],
                      repo_hint, "treehouse get")
    lease = parse_allocation(allocated, holder)
    worktree = _canonical(lease["path"], "treehouse lease path")
    if not worktree.is_dir():
        unavailable("treehouse lease path is not a directory")
    home = _canonical(Path.home(), "home directory")
    if worktree == home or worktree == repo_hint:
        unavailable("treehouse returned home or the requested checkout instead of a linked worktree")
    status = _call(run_command, [treehouse, "status", "--json"], repo_hint, "treehouse status")
    _status_entry(status, worktree, lease["lease_id"], lease["lease_holder"])

    repo_root, worktree_common, base = _git_identity(worktree, repo_hint, run_command,
                                                      expected_base=expected_base)
    return {"schema_version": 1, "path": str(worktree), "lease_id": lease["lease_id"],
            "lease_holder": lease["lease_holder"], "repo_root": str(repo_root),
            "git_common_dir": str(worktree_common), "base_commit": base}
