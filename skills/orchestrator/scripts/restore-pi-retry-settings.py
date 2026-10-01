#!/usr/bin/env python3
"""Stage or apply restoration of the two pi retry settings from a backup."""

import argparse
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


RETRY_KEYS = ("maxRetries", "maxAgentDelayMs")
MAX_FILE_SIZE = 1048576


class RestoreError(Exception):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RestoreError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def reject_constant(constant):
    raise RestoreError(f"invalid JSON number: {constant}")


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_SIZE:
            raise RestoreError(f"expected a regular file of at most {MAX_FILE_SIZE} bytes: {path}")
        data = stream.read(MAX_FILE_SIZE + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_FILE_SIZE or (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise RestoreError(f"file changed while reading: {path}")
    return data, after


def load_object(path):
    raw, info = read_regular(path)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as error:
        raise RestoreError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise RestoreError(f"expected a JSON object: {path}")
    retry = value.get("retry", {})
    if not isinstance(retry, dict):
        raise RestoreError(f"retry must be an object in {path}")
    for key in RETRY_KEYS:
        if key in retry and (type(retry[key]) is not int or retry[key] < 0):
            raise RestoreError(f"retry.{key} must be a non-negative integer in {path}")
    return raw, value, info


def selected(data):
    retry = data.get("retry", {})
    return {key: retry[key] for key in RETRY_KEYS if key in retry}


def restore_keys(current, baseline):
    result = copy.deepcopy(current)
    retry = result.setdefault("retry", {})
    if not isinstance(retry, dict):
        raise RestoreError("retry must be an object in the current settings")
    baseline_retry = baseline.get("retry", {})
    if not isinstance(baseline_retry, dict):
        raise RestoreError("retry must be an object in the baseline settings")
    for key in RETRY_KEYS:
        if key in baseline_retry:
            retry[key] = copy.deepcopy(baseline_retry[key])
        else:
            retry.pop(key, None)
    return result


def display_value(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False) if value is not None else "<absent>"


def show_diff(current, desired, header):
    print(header)
    before = selected(current)
    after = selected(desired)
    for key in RETRY_KEYS:
        old = before.get(key)
        new = after.get(key)
        if (key in before) != (key in after) or old != new:
            print(f"retry.{key}: {display_value(old)} -> {display_value(new)}")
    if before == after:
        print("retry keys already match")


def write_durably(descriptor, data):
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def fsync_parent(path):
    directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_write(path, data, mode, expected_raw, expected_info):
    path = Path(path)
    current, info = read_regular(path)
    if (info.st_ino, info.st_size, info.st_mtime_ns) != (
            expected_info.st_ino, expected_info.st_size, expected_info.st_mtime_ns) or current != expected_raw:
        raise RestoreError(f"target changed since it was read: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".retry-restore-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        write_durably(descriptor, data)
        os.replace(temporary, path)
        fsync_parent(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_snapshot(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        write_durably(descriptor, raw)
        fsync_parent(path)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="apply the selected-key restoration")
    action.add_argument("--rollback", action="store_true", help="restore the selected keys from the saved pre-apply file")
    default_settings = Path.home() / ".pi" / "agent" / "settings.json"
    parser.add_argument("--settings", type=Path, default=default_settings)
    parser.add_argument("--baseline", type=Path,
                        default=default_settings.with_name("settings.json.bak-orch-retry-20260930"))
    parser.add_argument("--rollback-file", type=Path,
                        help="private snapshot path; defaults beside settings.json")
    args = parser.parse_args(argv)
    settings = args.settings.expanduser().absolute()
    baseline_path = args.baseline.expanduser().absolute()
    rollback_path = (args.rollback_file.expanduser().absolute() if args.rollback_file else
                     settings.with_name(settings.name + ".bak-orch-retry-restore"))
    try:
        original, current, info = load_object(settings)
        _, baseline, _ = load_object(rollback_path if args.rollback else baseline_path)
        desired = restore_keys(current, baseline)
        action_name = "rollback" if args.rollback else "apply" if args.apply else "dry-run"
        show_diff(current, desired, f"{action_name}: {settings}")
        if args.apply:
            if not selected(current) == selected(restore_keys(current, baseline)):
                write_snapshot(rollback_path, original)
                atomic_write(settings, encoded(desired), stat.S_IMODE(info.st_mode), original, info)
                print(f"applied; rollback snapshot: {rollback_path}")
            else:
                print("no write needed")
        elif args.rollback:
            expected = restore_keys(current, load_object(baseline_path)[1])
            if selected(current) != selected(expected):
                raise RestoreError("current retry keys differ from the staged baseline; refusing rollback")
            if selected(current) != selected(desired):
                atomic_write(settings, encoded(desired), stat.S_IMODE(info.st_mode), original, info)
                print("rollback applied")
            else:
                print("no rollback write needed")
        return 0
    except (OSError, RestoreError, ValueError) as error:
        print(f"restore-pi-retry-settings: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
