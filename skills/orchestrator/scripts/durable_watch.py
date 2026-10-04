#!/usr/bin/env python3
"""Reconcile worker results from a transient systemd user timer."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit


PREFIX = "orch-watch-"
CONFIG = Path(".durable-watch/config.json")
LOCK = Path(".durable-watch/reconcile.lock")
EVENTS = Path("flags/events.jsonl")
VERSION = 1


class WatchError(Exception):
    """A watcher error safe to show without leaking configuration."""


def unit_base(run_dir: Path) -> str:
    return PREFIX + hashlib.sha256(str(run_dir.resolve()).encode()).hexdigest()[:16]


def within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def task_names(run_dir: Path, values: list[str]) -> list[str]:
    names = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = run_dir / path
        try:
            path = path.resolve(strict=True)
        except OSError as error:
            raise WatchError(f"task directory does not exist: {value}") from error
        if not path.is_dir() or not within(path, run_dir) or path == run_dir:
            raise WatchError(f"task directory must be inside the run directory: {value}")
        relative = path.relative_to(run_dir).as_posix()
        if relative in names:
            raise WatchError(f"duplicate task directory: {relative}")
        names.append(relative)
    if not names:
        raise WatchError("list at least one task directory")
    return names


def write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary_path = file.name
            json.dump(config, file, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def read_config(run_dir: Path) -> dict:
    try:
        config = json.loads((run_dir / CONFIG).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WatchError("no durable watch is configured for this run") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WatchError("durable watch configuration cannot be read") from error
    tasks = config.get("tasks") if isinstance(config, dict) else None
    if not isinstance(config, dict) or config.get("schema_version") != VERSION:
        raise WatchError("durable watch configuration has an unsupported shape")
    if not isinstance(tasks, list) or not tasks:
        raise WatchError("durable watch configuration has an unsupported shape")
    if any(not isinstance(task, str) or Path(task).is_absolute() or ".." in Path(task).parts for task in tasks):
        raise WatchError("durable watch configuration contains an invalid task path")
    return config


def task_file(run_dir: Path, relative: str, name: str) -> Path | None:
    try:
        task = (run_dir / relative).resolve(strict=True)
        path = (task / name).resolve(strict=True)
    except FileNotFoundError:
        return None
    if within(task, run_dir) and within(path, run_dir) and task.is_dir() and path.is_file():
        return path
    return None


def recorded(events: Path) -> set[tuple[str, str]]:
    known = set()
    if not events.exists():
        return known
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("event") == "worker-result":
            if isinstance(item.get("task_dir"), str) and isinstance(item.get("result_sha256"), str):
                known.add((item["task_dir"], item["result_sha256"]))
    return known


def publish(event: dict, config: dict) -> None:
    """Send the shared publisher's title/body/priority payload, best-effort."""
    if not config.get("notify"):
        return
    publisher = Path(config.get("publisher", ""))
    if not os.environ.get("NTFY_URL") or not publisher.is_file() or not os.access(publisher, os.X_OK):
        print("durable-watch: ntfy route unavailable; event is recorded", file=sys.stderr)
        return
    payload = (
        "Orchestrator worker result\n"
        f"{event['task_dir']} wrote result.json in {event['run_dir']}\n3\n"
    )
    try:
        result = subprocess.run(
            [str(publisher)], input=payload, text=True, capture_output=True,
            check=False, timeout=45, env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0:
        print("durable-watch: ntfy publish failed; event is recorded", file=sys.stderr)


def reconcile_once(run_dir: Path) -> dict:
    run_dir = run_dir.resolve(strict=True)
    config = read_config(run_dir)
    lock_path = run_dir / LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        events = run_dir / EVENTS
        events.parent.mkdir(parents=True, exist_ok=True)
        seen = recorded(events)
        new_events = []
        for relative in config["tasks"]:
            result = task_file(run_dir, relative, "result.json")
            if result is None:
                continue
            try:
                digest = hashlib.sha256(result.read_bytes()).hexdigest()
            except OSError:
                continue
            key = (relative, digest)
            if key in seen:
                continue
            event = {
                "event": "worker-result",
                "task_dir": relative,
                "result_sha256": digest,
                "run_dir": run_dir.name,
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with events.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
                file.flush()
                os.fsync(file.fileno())
            seen.add(key)
            new_events.append(event)
            publish(event, config)
        settled = [task for task in config["tasks"] if task_file(run_dir, task, "disposition.json")]
        return {
            "new_events": new_events,
            "complete": len(settled) == len(config["tasks"]),
            "settled": settled,
            "tasks": config["tasks"],
        }
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def systemd_property(unit: str, name: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, f"--property={name}", "--value"],
            text=True, capture_output=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unavailable"


def stop_units(run_dir: Path, service: bool = False, nonblocking: bool = False) -> None:
    base = unit_base(run_dir)
    units = [base + ".timer"] + ([base + ".service"] if service else [])
    states = {unit: systemd_property(unit, "LoadState") for unit in units}
    if "unavailable" in states.values():
        raise WatchError("systemd user manager is unavailable")
    present = [unit for unit, state in states.items() if state != "not-found"]
    if not present:
        return
    command = ["systemctl", "--user", "stop"]
    if nonblocking:
        command.append("--no-block")
    try:
        result = subprocess.run(command + present, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WatchError("systemd stop request failed") from error
    if result.returncode != 0:
        raise WatchError("systemd refused the stop request")


def ntfy_url(args: argparse.Namespace) -> str | None:
    value = args.ntfy_url or os.environ.get("ORCH_WATCH_NTFY_URL") or os.environ.get("NTFY_URL")
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise WatchError("ntfy URL must be an http(s) URL without embedded credentials")
    return value


def command_arm(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve(strict=True)
    if not run_dir.is_dir() or args.interval_seconds < 1:
        raise WatchError("run directory must exist and interval must be positive")
    tasks = task_names(run_dir, args.tasks)
    url = ntfy_url(args)
    publisher_value = os.environ.get("ORCH_WATCH_NTFY_PUBLISHER")
    publisher = Path(publisher_value).expanduser() if publisher_value else (
        Path.home() / "agents" / "claude-settings" / "ntfy" / "publisher.sh"
    )
    config = {
        "schema_version": VERSION,
        "tasks": tasks,
        "interval_seconds": args.interval_seconds,
        "notify": url is not None,
        "ntfy_url_sha256": hashlib.sha256(url.encode()).hexdigest() if url else None,
        "publisher": str(publisher.resolve()),
    }
    timer = unit_base(run_dir) + ".timer"
    load_state = systemd_property(timer, "LoadState")
    active_state = systemd_property(timer, "ActiveState")
    config_path = run_dir / CONFIG
    if load_state == "unavailable":
        raise WatchError("systemd user manager is unavailable")
    if config_path.exists() and read_config(run_dir) != config:
        raise WatchError("this run already has a different watch configuration; disarm before changing it")
    if load_state != "not-found" and not config_path.exists():
        raise WatchError("an existing watcher unit has no run configuration; disarm it before re-arming")
    if active_state in ("active", "activating", "reloading"):
        print(f"already armed: {timer}")
        return 0
    if not config_path.exists():
        write_config(config_path, config)

    outcome = reconcile_once(run_dir)
    if outcome["complete"]:
        print(f"already settled: {len(outcome['settled'])}/{len(tasks)} task dispositions present")
        return 0
    if load_state != "not-found":
        command = ["systemctl", "--user", "start", timer]
    else:
        command = [
            "systemd-run", "--user", "--quiet", f"--unit={unit_base(run_dir)}.service",
            "--on-active=1s", f"--on-unit-active={args.interval_seconds}s",
            "--timer-property=AccuracySec=1s",
        ]
        if url:
            command.append(f"--setenv=NTFY_URL={url}")
        command.extend([
            sys.executable, "-B", str(Path(__file__).resolve()), "reconcile", str(run_dir),
        ])
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WatchError("could not arm the systemd watcher") from error
    if result.returncode != 0:
        raise WatchError("systemd refused to arm the watcher; inspect the user journal")
    print(f"armed: {timer}; interval={args.interval_seconds}s; tasks={len(tasks)}")
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve(strict=True)
    outcome = reconcile_once(run_dir)
    if outcome["complete"]:
        stop_units(run_dir, nonblocking=True)
    print(json.dumps(outcome, sort_keys=True, separators=(",", ":")))
    return 0


def command_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve(strict=True)
    base = unit_base(run_dir)
    try:
        tasks = read_config(run_dir)["tasks"]
    except WatchError:
        tasks = []
    print(json.dumps({
        "run_dir": str(run_dir),
        "timer_unit": base + ".timer",
        "timer_load": systemd_property(base + ".timer", "LoadState"),
        "timer_state": systemd_property(base + ".timer", "ActiveState"),
        "service_state": systemd_property(base + ".service", "ActiveState"),
        "tasks": [{
            "task_dir": task,
            "result": task_file(run_dir, task, "result.json") is not None,
            "disposition": task_file(run_dir, task, "disposition.json") is not None,
        } for task in tasks],
    }, sort_keys=True, separators=(",", ":")))
    return 0


def command_disarm(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve(strict=True)
    stop_units(run_dir, service=True)
    print(f"disarmed: {unit_base(run_dir)}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    arm = commands.add_parser("arm", help="arm a finite systemd user timer")
    arm.add_argument("run_dir")
    arm.add_argument("tasks", nargs="+", help="task directories relative to the run directory")
    arm.add_argument("--interval-seconds", type=int, default=30)
    arm.add_argument("--ntfy-url", help="ntfy URL; defaults to ORCH_WATCH_NTFY_URL or NTFY_URL")
    arm.set_defaults(handler=command_arm)
    for name, handler in (("reconcile", command_reconcile), ("status", command_status), ("disarm", command_disarm)):
        command = commands.add_parser(name)
        command.add_argument("run_dir")
        command.set_defaults(handler=handler)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except WatchError as error:
        print(f"durable-watch: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"durable-watch: filesystem operation failed ({error.strerror or 'I/O error'})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
