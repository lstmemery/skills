"""Preflight jail brief artifact names and staged bundle inputs."""

import json
from pathlib import Path
import re
import stat
import unicodedata


INVENTORY_FIELDS = {
    "schema",
    "artifact_paths",
    "production_inputs",
    "test_inputs",
    "fixture_inputs",
    "prior_inputs",
}
REQUIRED_INPUT_GROUPS = ("production_inputs", "test_inputs", "fixture_inputs")
INVENTORY_BLOCK = re.compile(
    r"^```json jail-preflight[ \t]*\n(.*?)^```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_inventory(brief_path):
    try:
        content = Path(brief_path).read_text(encoding="utf-8")
    except OSError as error:
        return None, [f"cannot read brief at {brief_path}: {error.strerror or error}"]
    blocks = INVENTORY_BLOCK.findall(content)
    if len(blocks) != 1:
        return None, [
            "jail brief must contain exactly one ```json jail-preflight inventory block"
        ]
    try:
        inventory = json.loads(blocks[0], object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as error:
        return None, [f"jail pre-launch inventory is not valid JSON: {error}"]
    if not isinstance(inventory, dict) or set(inventory) != INVENTORY_FIELDS:
        expected = ", ".join(sorted(INVENTORY_FIELDS))
        return None, [f"jail pre-launch inventory must contain exactly: {expected}"]
    if type(inventory["schema"]) is not int or inventory["schema"] != 1:
        return None, ["jail pre-launch inventory schema must be 1"]
    return inventory, []


def _plain_relative_path(value):
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value:
        return False
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        return False
    return not any(
        unicodedata.category(char).startswith("C")
        or unicodedata.category(char) in {"Zl", "Zp"}
        for char in value
    )


def _validate_artifact_paths(value):
    issues = []
    if not isinstance(value, list) or not value:
        return ["artifact_paths must be a non-empty array of filenames"]
    seen = set()
    for name in value:
        if (not _plain_relative_path(name) or "/" in name or name in {".", "..", "result.json"}):
            issues.append(
                f"invalid declared artifact path {name!r}: import artifacts must be single "
                "filenames directly under the workspace root"
            )
        elif name in seen:
            issues.append(f"duplicate declared artifact path: {name}")
        else:
            seen.add(name)
    return issues


def _input_path_issue(root, relative, label):
    if not _plain_relative_path(relative):
        return f"invalid {label} input path {relative!r}: expected a bundle-relative file path"
    current = root
    components = relative.split("/")
    for index, component in enumerate(components):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except OSError:
            return f"missing {label} input in bundle: {relative}"
        if stat.S_ISLNK(mode):
            return f"{label} input is a symlink in bundle: {relative}"
        if index < len(components) - 1 and not stat.S_ISDIR(mode):
            return f"{label} input has a non-directory parent in bundle: {relative}"
        if index == len(components) - 1 and not stat.S_ISREG(mode):
            return f"{label} input is not a regular file in bundle: {relative}"
    try:
        with current.open("rb") as stream:
            stream.read(1)
    except OSError:
        return f"{label} input is unreadable in bundle: {relative}"
    return None


def validate_jail_brief(brief_path, bundle_root):
    """Return pre-launch inventory errors; output paths are validated, not opened."""
    inventory, issues = _read_inventory(brief_path)
    if issues:
        return issues
    issues.extend(_validate_artifact_paths(inventory["artifact_paths"]))

    root = Path(bundle_root)
    try:
        root_mode = root.lstat().st_mode
    except OSError:
        return issues + [f"bundle root is missing or unreadable: {root}"]
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return issues + [f"bundle root must be a plain directory: {root}"]

    for group in REQUIRED_INPUT_GROUPS:
        entries = inventory[group]
        label = group.removesuffix("_inputs").replace("_", " ")
        if not isinstance(entries, list) or not entries:
            issues.append(f"jail pre-launch inventory must declare at least one {label} input")
            continue
        for relative in entries:
            issue = _input_path_issue(root, relative, label)
            if issue:
                issues.append(issue)

    prior_inputs = inventory["prior_inputs"]
    if not isinstance(prior_inputs, list):
        issues.append("prior_inputs must be an array")
    else:
        for relative in prior_inputs:
            issue = _input_path_issue(root, relative, "prior")
            if issue:
                issues.append(issue)
    return issues
