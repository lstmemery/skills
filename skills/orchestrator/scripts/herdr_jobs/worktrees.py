"""Local Git and legacy Treehouse lease parsing and worktree verification."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
import fcntl

from .records import (JobError, atomic_bytes, encoded, is_commit_id, load_json, parse_json,
                      repository_worktree_record)


TREEHOUSE_GET_FIELDS = {"path", "lease_id", "lease_holder", "name", "leased_at",
                       "branch", "status", "flavor", "detached", "recovery_reason",
                       "processes"}
TREEHOUSE_STATUS_FIELDS = {"name", "path", "status", "flavor", "lease_id", "lease_holder",
                           "leased_at", "processes"}
TREEHOUSE_STATUS_OPTIONAL_FIELDS = {"branch", "detached", "recovery_reason"}
GIT_LEASE_PREFIX = "git-worktree-"
# Reserve the allocator's exact UUID form; arbitrary legacy IDs keep the Treehouse verifier.
GIT_LEASE_ID = re.compile(re.escape(GIT_LEASE_PREFIX) + r"[0-9a-f]{32}")
GIT_LEASE_MARKER = "orchestrator-lease.json"
GIT_LEASE_HISTORY = "lease-history.json"


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


def _lease_root():
    home = _canonical(Path.home(), "home directory")
    root = _canonical(home / ".local/share/orchestrator/worktrees",
                      "Git worktree lease directory", strict=False)
    if home not in root.parents:
        unavailable("Git worktree lease directory must remain below the home directory")
    return root


def _read_local_lease(marker, worktree, run_command):
    try:
        record = repository_worktree_record(load_json(marker, 16384))
    except FileNotFoundError:
        return None
    except (OSError, JobError) as error:
        unavailable(f"local Git worktree lease marker is invalid: {error}")
    if not GIT_LEASE_ID.fullmatch(record["lease_id"]):
        unavailable("local Git worktree lease marker has an invalid lease identity")
    canonical_worktree = _canonical(worktree, "leased worktree")
    recorded_path = _canonical(record["path"], "recorded worktree")
    if canonical_worktree != recorded_path:
        unavailable("local Git worktree lease marker names a different worktree")
    repo_root, common_dir, _ = _git_identity(
        canonical_worktree, _canonical(record["repo_root"], "expected repository"),
        run_command, expected_common=record["git_common_dir"])
    if (repo_root != _canonical(record["repo_root"], "expected repository")
            or common_dir != _canonical(record["git_common_dir"], "recorded Git common directory")):
        unavailable("local Git worktree lease marker does not match its repository")
    _verify_git_lease_marker(record, canonical_worktree, run_command)
    return record


def _lease_metadata(record, marker, run_command):
    worktree = _canonical(record["path"], "recorded worktree")
    git = shutil.which("git")
    if not git:
        unavailable("git is unavailable for repository worktree status")
    branch_code, branch_output, _ = run_command(
        [git, "-C", str(worktree), "symbolic-ref", "--quiet", "--short", "HEAD"], worktree)
    if branch_code not in (0, 1):
        unavailable("Git could not read the leased worktree branch")
    branch = _decode(branch_output, "Git branch lookup") if branch_code == 0 else None
    head = _git_text(run_command, git, worktree, "rev-parse", "HEAD").lower()
    status_output = _call(run_command, [git, "-C", str(worktree), "status", "--porcelain",
                                       "--untracked-files=all"], worktree,
                          "Git worktree status")
    dirty = bool(status_output.strip())
    main_code, _, _ = run_command(
        [git, "-C", record["repo_root"], "show-ref", "--verify", "--quiet", "refs/heads/main"],
        Path(record["repo_root"]))
    if main_code == 0:
        merge_code, _, _ = run_command(
            [git, "-C", str(worktree), "merge-base", "--is-ancestor", "HEAD", "refs/heads/main"],
            worktree)
        if merge_code not in (0, 1):
            unavailable("Git could not determine whether the worktree is merged into main")
        merged = merge_code == 0
    elif main_code == 1:
        merged = None
    else:
        unavailable("Git could not inspect the repository main branch")
    created = datetime.fromtimestamp(marker.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    return {"lease_id": record["lease_id"], "lease_holder": record["lease_holder"],
            "path": str(worktree), "created": created, "branch": branch, "head": head,
            "merged_into_main": merged, "dirty": dirty}


def _active_local_lease_records(root, run_command):
    if not root.exists():
        return []
    try:
        worktrees = sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink())
    except OSError as error:
        unavailable(f"cannot list Git worktree leases: {error.strerror}")
    records = []
    for worktree in worktrees:
        if not (worktree / ".git").exists():
            continue
        git_dir = _worktree_git_dir(worktree, run_command)
        marker = git_dir / GIT_LEASE_MARKER
        if not marker.exists():
            continue
        record = _read_local_lease(marker, worktree, run_command)
        if record is not None:
            records.append((record, marker))
    return records


def _load_lease_history(path):
    try:
        history = load_json(path, 16777216)
    except FileNotFoundError:
        return {"schema_version": 1, "leases": {}}
    except (OSError, JobError) as error:
        unavailable(f"local worktree lease history is invalid: {error}")
    if (not isinstance(history, dict) or set(history) != {"schema_version", "leases"}
            or history["schema_version"] != 1 or not isinstance(history["leases"], dict)):
        unavailable("local worktree lease history has an unrecognized format")
    return history


def _save_lease_history(path, history):
    try:
        atomic_bytes(path, encoded(history) + b"\n")
    except OSError as error:
        unavailable(f"cannot record local worktree lease release: {error.strerror}")


def list_local_leases(*, run_command=None):
    """List verified active local Git worktree leases under the current home."""
    run_command = run_command or _run_command
    root = _lease_root()
    return [_lease_metadata(record, marker, run_command)
            for record, marker in _active_local_lease_records(root, run_command)]


def status_local_lease(lease_id, *, run_command=None):
    """Return status for one verified active local Git worktree lease."""
    run_command = run_command or _run_command
    if not isinstance(lease_id, str) or not GIT_LEASE_ID.fullmatch(lease_id):
        raise JobError("invalid_input", "lease ID must be a local Git worktree lease ID")
    root = _lease_root()
    for record, marker in _active_local_lease_records(root, run_command):
        if record["lease_id"] == lease_id:
            return _lease_metadata(record, marker, run_command)
    history = _load_lease_history(root / GIT_LEASE_HISTORY)
    released = history["leases"].get(lease_id)
    if released is not None:
        return {**released, "released": True}
    raise JobError("invalid_input", f"active local Git worktree lease was not found: {lease_id}")


def release_local_lease(lease_id, who, why, *, force=False, run_command=None):
    """Remove one local worktree after safety checks and retain its release audit."""
    run_command = run_command or _run_command
    if not isinstance(lease_id, str) or not GIT_LEASE_ID.fullmatch(lease_id):
        raise JobError("invalid_input", "lease ID must be a local Git worktree lease ID")
    who = _string(who, "release actor")
    why = _string(why, "release reason")
    if type(force) is not bool:
        raise JobError("invalid_input", "force must be a boolean")

    root = _lease_root()
    if not root.is_dir():
        raise JobError("invalid_input", f"active local Git worktree lease was not found: {lease_id}")
    lock_path = root / ".lease-history.lock"
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        unavailable(f"cannot lock local worktree lease history: {error.strerror}")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        history_path = root / GIT_LEASE_HISTORY
        history = _load_lease_history(history_path)
        prior_release = history["leases"].get(lease_id)
        if prior_release is not None:
            return {"lease_id": lease_id, "released": False, "already_released": True,
                    "release": prior_release}

        found = next(((record, marker) for record, marker
                      in _active_local_lease_records(root, run_command)
                      if record["lease_id"] == lease_id), None)
        if found is None:
            raise JobError("invalid_input", f"active local Git worktree lease was not found: {lease_id}")
        record, marker = found
        status = _lease_metadata(record, marker, run_command)
        if not force and status["dirty"]:
            raise JobError("conflict", "cannot release a dirty worktree without --force")
        if not force and status["merged_into_main"] is not True:
            raise JobError("conflict", "cannot release an unmerged worktree without --force")

        git = shutil.which("git")
        if not git:
            unavailable("git is unavailable for repository worktree release")
        argv = [git, "-C", record["repo_root"], "worktree", "remove"]
        if force:
            argv.append("--force")
        argv.append(record["path"])
        _call(run_command, argv, Path(record["repo_root"]), "Git worktree release")
        released_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        release_record = {**status, "repo_root": record["repo_root"], "released_at": released_at,
                          "who": who, "why": why, "forced": force}
        history["leases"][lease_id] = release_record
        _save_lease_history(history_path, history)
        return {"lease_id": lease_id, "released": True, "already_released": False,
                "release": release_record}
    finally:
        os.close(descriptor)


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
    if GIT_LEASE_ID.fullmatch(record["lease_id"]):
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
