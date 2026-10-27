"""Check declared effects strictly and undeclared prose paths as advisory warnings."""

import os
from pathlib import Path, PurePosixPath
import re
import stat


# Backticks support paths with spaces; unquoted absolute/home paths are common
# in briefs. This is deliberately not an interpretation of edit instructions.
BRIEF_CONTRACT_FIELDS = ("dependencies", "mutation_targets", "remote_authority")
PATH_REFERENCES = re.compile(r"`([^`\n]+)`|(?<![\w:/])((?:/|~/)[^\s`\"'<>]+)")


def _absolute(value, cwd):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.absolute()


def edit_scope_warnings(job):
    """Flag local symlink escapes without reading targets or granting scope."""
    if job["route"]["mode"] != "agent":
        return [{"reason": "namespace_unverified",
                 "message": "Check edit targets and editable_paths in the worker namespace."}]
    cwd = job["cwd"]
    editable = []
    warnings = []
    for value in job["editable_paths"]:
        try:
            editable.append(Path(os.path.abspath(_absolute(value, cwd))))
        except (OSError, RuntimeError, ValueError):
            warnings.append({"path": value, "reason": "resolution_unverified",
                             "message": "Cannot expand this editable path; verify scope before dispatch."})
    candidates = list(job["editable_paths"])
    for match in PATH_REFERENCES.finditer(job["task"]):
        value = match[1] or match[2].rstrip(".,;:)]}")
        if (("/" in value or (match[1] and not any(char.isspace() for char in value)))
                and not any(char in value for char in "\n*?${}")):
            candidates.append(value)
    seen = set()
    for value in candidates:
        try:
            path = _absolute(value, cwd)
        except (OSError, RuntimeError, ValueError):
            if not any(item.get("path") == value for item in warnings):
                warnings.append({"path": value, "reason": "resolution_unverified",
                                 "message": "Cannot expand this path; verify edit scope before dispatch."})
            continue
        if path in seen:
            continue
        seen.add(path)
        try:
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError:
                resolved = path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            warnings.append({"path": str(path), "reason": "resolution_unverified",
                             "message": "Cannot resolve this path; verify edit scope before dispatch."})
            continue
        # Keep the allowlist lexical: resolving it would silently authorize a
        # symlink's excluded destination, the conflict this check must expose.
        if resolved != Path(os.path.abspath(path)) and not any(resolved.is_relative_to(root) for root in editable):
            warnings.append({"path": str(path), "resolved_path": str(resolved),
                             "reason": "symlink_outside_editable_paths",
                             "message": "If this path must be edited, reconcile its destination with editable_paths before dispatch."})
    return warnings


def declared_brief_contract(job):
    return {key: job[key] for key in BRIEF_CONTRACT_FIELDS if key in job}


def brief_contract_errors(job):
    """Find hard contradictions in structured intent; never infer authority."""
    errors = []
    if not declared_brief_contract(job):
        return errors
    if job["route"]["mode"] != "agent" and any(
            job.get(key) for key in BRIEF_CONTRACT_FIELDS):
        return ["structured brief contract requires the desktop agent namespace; jail is unverified"]
    try:
        editable = [Path(os.path.abspath(_absolute(value, job["cwd"])))
                    for value in job["editable_paths"]]
        protected = [_absolute(value, job["cwd"]) for value in job["protected_paths"]]
    except (OSError, RuntimeError, ValueError) as error:
        return [f"local scope resolution unverified: {error}"]
    for operation, key in (("read", "dependencies"), ("write", "mutation_targets")):
        for target in job.get(key, []):
            if target.get("lookup_host", target["host"]) != target["host"]:
                errors.append(f"{key}: lookup host disagrees with source host for {target['path']}")
            if "source" in target:
                source = target["source"]
                root = PurePosixPath(source["run_dir"])
                outputs = (root / f"out-{source['job_id']}",
                           root / "run" / "workers" / source["job_id"])
                if (root.name != source["request_id"]
                        or not all(any(PurePosixPath(target[field]).is_relative_to(output) for output in outputs)
                                   for field in ("path", "canonical_path"))):
                    errors.append(f"{key}: source identity disagrees with path for {target['path']}")
            if target["host"] == "desktop":
                path = Path(target["path"])
                try:
                    resolved = path.resolve(strict=operation == "read")
                    if resolved != Path(target["canonical_path"]):
                        errors.append(f"{key}: canonical_path disagrees with resolved destination for {path}")
                    if operation == "read" and (not resolved.is_file() or not os.access(resolved, os.R_OK)):
                        errors.append(f"{key}: dependency is not a readable file: {path}")
                    if operation == "write":
                        ancestor = resolved.parent
                        while True:
                            try:
                                ancestor_mode = ancestor.stat().st_mode
                                break
                            except (FileNotFoundError, NotADirectoryError):
                                ancestor = ancestor.parent
                        if not stat.S_ISDIR(ancestor_mode):
                            errors.append(f"{key}: mutation target ancestor is not a directory: {ancestor}")
                        if not all(any(candidate.is_relative_to(root) for root in editable)
                                   for candidate in (Path(os.path.abspath(path)), resolved)):
                            errors.append(f"{key}: target is outside editable_paths: {path} -> {resolved}")
                        if any(candidate.is_relative_to(root) or candidate.is_relative_to(root.resolve())
                               for candidate in (Path(os.path.abspath(path)), resolved)
                               for root in protected):
                            errors.append(f"{key}: mutation target is protected: {path}")
                except (OSError, RuntimeError, ValueError) as error:
                    errors.append(f"{key}: local path resolution unverified for {path}: {error}")
                continue
            paths = (PurePosixPath(target["path"]), PurePosixPath(target["canonical_path"]))
            if not any(grant["host"] == target["host"]
                       and operation in grant["operations"]
                       and all(any(path.is_relative_to(PurePosixPath(root)) for root in grant["paths"])
                               for path in paths)
                       for grant in job.get("remote_authority", [])):
                errors.append(f"{key}: {target['path']} requires explicit remote authority for {operation}")
    return errors
