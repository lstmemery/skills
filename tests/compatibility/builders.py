"""Build isolated inputs from checked-in goldens, never from production helpers.

Each load returns fresh data. Expected outputs stay in the fixture files; these
builders only bind identities and materialize inputs for public consumers.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


FIXTURES = Path(__file__).with_name("fixtures")
ROOT = Path(__file__).resolve().parents[3]


def golden(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_files(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def calendar_block(grid):
    """The existing handoff copies days, not classifier metadata, into a plan."""
    days = copy.deepcopy(grid["days"])
    return {
        "status": "ok", "source": "fictional calendar compatibility fixture",
        "days": days,
        "skipped_days": [
            {"date": day["date"], "reason": "matt-away" if day["matt"] == "away" else "evening-booked",
             "evidence": day["evidence"][0]}
            for day in days if day["evening"] != "free"
        ],
    }


def pending_archive(root, day, message_id):
    """Reuse the pinned one-line Message-ID format with a caller-owned date."""
    fixture = golden("delivery")
    directory = root / day
    write_files(directory, {"brief.md": "# Archived brief\n", "email-message-id":
                           fixture["message_marker"].replace(fixture["message_id"], message_id)})
    return directory


def review_evidence(*, schema_version=1, task_id="task-1", capture_id="fixture-capture",
                    author="coder-1", reviewer="reviewer-1", standards=(), spec=()):
    evidence = golden("review")["empty"]
    evidence.update(schema_version=schema_version, task_id=task_id, capture_id=capture_id,
                    author_identity=author, reviewer_identity=reviewer)
    evidence["axes"]["standards"]["findings"] = copy.deepcopy(list(standards))
    evidence["axes"]["spec"]["findings"] = copy.deepcopy(list(spec))
    return evidence


def capture_bundle(directory):
    fixture = golden("capture-v1")
    write_files(directory, fixture["files"])
    write_json(directory / "manifest.json", fixture["manifest"])
    return copy.deepcopy(fixture["manifest"])


def review_bundle(directory, case="v1-bare"):
    """Materialize a complete bundle usable by both sync and preflight."""
    fixture = golden("review")
    selected = fixture["cases"][case]
    review = directory / "task-1/review"
    manifest = capture_bundle(review / "capture")
    evidence = review_evidence(
        schema_version=selected["schema_version"], capture_id=manifest["capture_id"],
        standards=selected["standards"], spec=selected["spec"],
    )
    write_json(review / "review-evidence.json", evidence)
    write_json(review / "done.json", {
        "task_id": "task-1", "capture_id": manifest["capture_id"],
        "standards_findings": len(selected["standards"]), "spec_findings": len(selected["spec"]),
    })
    write_files(review, {"review.md": selected["markdown"]})
    write_files(directory / "task-1", {"review-response.md": selected["response"]})
    write_files(directory, {"workers.txt": fixture["workers"]})
    return review, evidence


def git_history(directory):
    """Deterministic, fictional Git history for capture writer comparisons."""
    directory.mkdir()
    environment = os.environ | {
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-02T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-10-02T12:00:00+00:00",
    }

    def git(*args):
        return subprocess.run(["git", "-C", str(directory), *args], env=environment,
                              capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-b", "main")
    write_files(directory, {"file.txt": "first\n", "context.txt": "unchanged context\n"})
    git("add", ".")
    git("commit", "-m", "initial")
    base = git("rev-parse", "HEAD")
    write_files(directory, {"file.txt": "second\n"})
    git("commit", "-am", "candidate")
    return base, git("rev-parse", "HEAD")


class DailyArchive:
    """Run either real shell entry point with the same offline process adapters.

    The managed task adapter supplies completion artifacts, just as the existing
    runner tests do. It never executes the missing managed-task wrapper.
    """

    def __init__(self, directory, workflow):
        self.fixture = golden("daily")
        self.root = directory / workflow
        self.skill = self.root / "skills-local" / (workflow + "-daily-brief")
        self.bin = self.root / "bin"
        self.archive = self.skill / "briefs" / self.fixture["day"]
        self.archive.mkdir(parents=True)
        self.bin.mkdir()
        source = ROOT / "skills-local" / self.skill.name
        for name in ("daily-run.sh", workflow + "_daily_brief.py"):
            shutil.copy2(source / name, self.skill / name)
        common = self.root / "skills-local/ml-daily-brief/runner-common.sh"
        common.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skills-local/ml-daily-brief/runner-common.sh", common)
        write_json(self.skill / "runtime-config.json", self.fixture["config"])
        for name in ("codex", "orchestrator", "date"):
            shutil.copy2(FIXTURES / "daily-process.py", self.bin / name)
            (self.bin / name).chmod(0o755)
        for name in ("email-brief.sh", "notify-brief.sh"):
            path = self.skill / name
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$COMPAT_DELIVERY_LOG"\n')
            path.chmod(0o755)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self.environment = os.environ | {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "HOME": str(self.root),
            "COMPAT_DAY": self.fixture["day"],
            "COMPAT_TASK_ID": self.fixture["task_id"],
            "COMPAT_ARCHIVE": str(self.archive),
            "COMPAT_CALLS": str(self.root / "calls.jsonl"),
            "COMPAT_DELIVERY_LOG": str(self.root / "delivered.txt"),
            "COMPAT_FIXTURE": str(FIXTURES / "daily.json"),
            "HN_BRIEF_CONFIG": str(self.skill / "runtime-config.json"),
            "ML_BRIEF_CONFIG": str(self.skill / "runtime-config.json"),
        }

    def prepare_output(self):
        write_json(self.archive / "candidates.json", self.fixture["candidates"])
        write_json(self.archive / "selection.json", self.fixture["selection"])
        (self.archive / "brief.md").write_text("# Completed brief\n", encoding="utf-8")

    def install(self, name):
        write_files(self.archive, self.fixture["archives"][name])

    def run(self, primary="success", task="succeeded"):
        return subprocess.run(
            ["bash", "-c", 'export COMPAT_RUNNER_PID=$$; exec bash "$1"',
             "runner", str(self.skill / "daily-run.sh")], cwd=self.root,
            env=self.environment | {"COMPAT_PRIMARY": primary, "COMPAT_TASK": task},
            text=True, capture_output=True, timeout=15, check=False,
        )

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
