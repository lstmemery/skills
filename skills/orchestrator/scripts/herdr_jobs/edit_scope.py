"""Advisory local symlink checks; the coordinator owns the edit inventory."""

import os
from pathlib import Path
import re


# Backticks support paths with spaces; unquoted absolute/home paths are common
# in briefs. This is deliberately not an interpretation of edit instructions.
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
