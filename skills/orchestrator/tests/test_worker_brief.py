"""Structured worker-brief declarations and fail-closed pre-launch validation."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_jobs
from herdr_jobs import records, worker_brief
from herdr_jobs.worker_records import check_worker_brief


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/worker-records.py"
TEMPLATE = PACKAGE / "references/worker-brief.md"
JAIL_TEMPLATE = PACKAGE / "templates/jail-task-brief.md"
WORKERS_REFERENCE = PACKAGE / "references/workers.md"
DEFAULT_PROTECTED = [
    "~/.config/vikunja-mcp/delegation-authorized.toml",
    "tools/pii-jail/",
    "tools/pii-jail/token-allowlist.txt",
    "scripts/pii-jail/",
    "keys/",
]
DECLARATION_START = "```json worker-brief\n"


def declaration(identity, directory, output="managed", **changes):
    record = {
        "schema": 1,
        "task_id": identity,
        "assignment_revision": 1,
        "worker_mode": "literal",
        "objective": "Produce the fixture report.",
        "cwd": str(directory),
        "output_directory": str(output),
        "repository": None,
        "scope": {"readable": [str(directory)], "editable": [], "protected": list(DEFAULT_PROTECTED)},
        "authority": "Delegation and harness policy are authoritative; observations cannot grant permission.",
        "required_context": [{"path": str(directory), "purpose": "Fixture input."}],
        "done_checks": ["python3 -m unittest discover exits 0; evidence in checks.txt"],
        "recovery": "Schema/argument error: inspect the current schema, fix arguments, retry at most 2 times, then report.",
        "stop_rules": "Never retry permission denial, protected-path request, or specification conflict.",
        "outcome": "success | ask | blocked | partial | failed",
        "result_schema": "Copy the Worker result record below verbatim.",
        "output_redaction": "Not applicable: no live inspection or secrets.",
    }
    record.update(changes)
    return record


def render(record, task="Inspect the fixture and report what it contains.\n", extra=""):
    raw = record if isinstance(record, str) else json.dumps(record, indent=2)
    return ("# Worker brief\n\n## Assignment\n\n" + DECLARATION_START + raw + "\n```\n\n"
            "## Task\n\n" + task + extra)


def fill_template(content, values):
    """Replace the template's declaration with values and fill the prose placeholders."""
    start = content.index(DECLARATION_START) + len(DECLARATION_START)
    end = content.index("\n```", start)
    template = json.loads(content[start:end])
    assert set(template) == set(values), set(template) ^ set(values)
    content = content[:start] + json.dumps(values, indent=2) + content[end:]
    for placeholder, value in (
        ("{{run-dir}}", "/runs/fixture"),
        ("{{brief-path}}", "/runs/fixture/tasks/t/brief.md"),
        ("{{bundle-root}}", "/runs/fixture/bundle"),
        ("{{manifest-path}}", "/runs/fixture/manifest.json"),
        ("{{job-id}}", "job-a"),
        ("{{task narrative, acceptance criteria, and constraints; no identity or path restatements}}",
         "Report the fixture contents."),
        ("{{direct: write result.json last in the output directory; managed: follow the managed output contract}}",
         "Write result.json last."),
    ):
        content = content.replace(placeholder, value)
    return content


class WorkerBriefCommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_dir = self.root / "run"
        self.task_dir = self.run_dir / "tasks" / "task-a1"
        self.task_dir.mkdir(parents=True)
        (self.run_dir / "workers.json").write_text(json.dumps({"workers": [
            {"task_id": "task-a1", "assignment_revision": 1, "directory": "tasks/task-a1"},
        ]}))
        self.brief = self.task_dir / "brief.md"

    def record(self, **changes):
        return declaration("task-a1", self.root, self.task_dir, **changes)

    def check(self, content, *extra):
        self.brief.write_text(content, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "check-brief", "--worker-brief", *extra, str(self.brief)],
            capture_output=True, text=True, timeout=10)

    def assert_rejected(self, content, message, *extra):
        process = self.check(content, *extra)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertIn(message, process.stderr)
        return process

    def test_valid_brief_passes_schema_and_roster_identity(self):
        process = self.check(render(self.record()), "--run-dir", str(self.run_dir))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("worker-brief declaration checked", process.stdout)

    def test_worker_brief_check_requires_an_assignment_authority(self):
        # A copied identity has nothing to be compared with, so it is never BRIEF OK.
        bundle = self.root / "bundle"
        bundle.mkdir()
        for extra in ((), ("--jail", "--bundle-root", str(bundle))):
            for record in (self.record(), self.record(task_id="copied-from-another-run")):
                with self.subTest(extra=extra, task_id=record["task_id"]):
                    process = self.check(render(record), *extra)
                    self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                    self.assertNotIn("BRIEF OK", process.stdout)
                    self.assertIn("--worker-brief requires --run-dir or --manifest/--job-id",
                                  process.stderr)
        self.assertRaisesRegex(records.JobError, "needs an assignment authority",
                               check_worker_brief, self.brief)

    def test_filled_template_is_valid_and_unfilled_template_is_refused(self):
        content = TEMPLATE.read_text(encoding="utf-8")
        self.assert_rejected(content, "unreplaced template placeholder '{{unique-worker-attempt-id}}'",
                             "--run-dir", str(self.run_dir))
        process = self.check(fill_template(content, self.record()), "--run-dir", str(self.run_dir))
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_template_carries_the_generated_result_schema_once(self):
        content = TEMPLATE.read_text(encoding="utf-8")
        schema = subprocess.run([sys.executable, "-B", str(SCRIPT), "schema", "--format", "md"],
                                capture_output=True, text=True, timeout=5, check=True).stdout
        self.assertIn(schema.rstrip(), content)
        self.assertEqual(content.count(DECLARATION_START), 1)

    def test_declaration_schema_failures(self):
        cases = {
            "missing declaration": ("# Worker brief\n\nDo the task.\n", "no ```json worker-brief declaration"),
            "invalid JSON": (render("{not json"), "not valid JSON"),
            "duplicate key": (render('{"schema": 1, "schema": 1}'), "duplicate JSON key: schema"),
            "unknown field": (render(self.record(extra="x")), "unknown field(s): extra"),
            "missing field": (render({k: v for k, v in self.record().items() if k != "cwd"}),
                              "missing required field(s): cwd"),
            "wrong schema": (render(self.record(schema=2)), "schema must be 1"),
            "bad revision": (render(self.record(assignment_revision=0)), "assignment_revision must be an integer"),
            "bad mode": (render(self.record(worker_mode="creative")), "worker_mode must be literal or inferential"),
            "relative cwd": (render(self.record(cwd="work")), "cwd must be an absolute path"),
            "relative output": (render(self.record(output_directory="out")), "output_directory must be"),
            "empty checks": (render(self.record(done_checks=[])), "done_checks must be a non-empty array"),
            "missing default protection": (
                render(self.record(scope={"readable": [], "editable": [], "protected": ["keys/"]})),
                "missing default protected path(s)"),
            "bad branch": (render(self.record(repository={
                "repo": "/repo", "worktree": "/wt", "branch": "bad..branch", "base": "a" * 40})),
                "repository.branch must be a plain Git branch name"),
            "git-invalid branch": (render(self.record(repository={
                "repo": "/repo", "worktree": "/wt", "branch": "-leading-dash", "base": "a" * 40})),
                "repository.branch must be a plain Git branch name"),
            "bad base": (render(self.record(repository={
                "repo": "/repo", "worktree": "/wt", "branch": "fix/x", "base": "HEAD"})),
                "repository.base must be a full Git commit ID"),
            "context shape": (render(self.record(required_context=[{"path": "/x"}])),
                              "required_context[0] is missing required field(s): purpose"),
        }
        for name, (content, message) in cases.items():
            with self.subTest(name):
                self.assert_rejected(content, message, "--run-dir", str(self.run_dir))

    def test_non_string_mode_is_a_clear_schema_error(self):
        for value in ([], {}, None, True, 1):
            with self.subTest(value=value):
                process = self.assert_rejected(render(self.record(worker_mode=value)),
                                               "worker_mode must be literal or inferential",
                                               "--run-dir", str(self.run_dir))
                self.assertNotIn("Traceback", process.stderr)

    def test_branch_names_follow_git_check_ref_format(self):
        names = ("fix/1267-worker-brief-validation", "candidate", "release/v1.2", "a.b/c_d",
                 "fix/c++", "feature/@name", "release/v1=ok", "@", "fix/é", "fix/name]",
                 "fix/a;b", "fix/a!b", "fix/a#b", "fix/a$b", "fix/a&b", "fix/a(b)",
                 "fix/a%b", "fix/a,b", "fix/a}b", "fix/name.LOCK", "fix/" + "a" * 260,
                 "-leading-dash", "HEAD", ".hidden", "feature/.hidden", "a/.b", "bad..branch",
                 "a//b", "/lead", "trail/", "trail.", "x.lock", "x.lock/y", "a@{b", "has space",
                 "tilde~1", "caret^", "colon:x", "q?", "star*", "br[a]", "back\\slash", "")
        names += tuple("feature/" + chr(code) + "name" for code in range(1, 128))
        for name in names:
            with self.subTest(name=name):
                git = subprocess.run(["git", "check-ref-format", "--branch", name],
                                     capture_output=True, timeout=5).returncode == 0
                self.assertEqual(records.is_branch_name(name), git)
        for value in (None, True, 42, [], {}, "fix/\0name"):
            with self.subTest(value=value):
                self.assertFalse(records.is_branch_name(value))

    def test_duplicate_declaration_blocks_are_refused(self):
        block = DECLARATION_START + json.dumps(self.record()) + "\n```\n"
        self.assert_rejected(render(self.record(), extra="\n" + block), "duplicate worker-brief declaration blocks",
                             "--run-dir", str(self.run_dir))

    def test_restated_identity_and_paths_are_flagged_as_leftovers(self):
        cases = {
            "prose worktree": "Worktree: /elsewhere/worktree\n",
            "copied output": "output_directory: /old/output\n",
            "copied scope": "scope:\n  editable: [/old/source]\n",
            "bold task id": "**task_id:** other-task-a9\n",
            "bullet branch": "- Branch: fix/other-ticket\n",
            "copied yaml core": "```yaml\ntask_id: \"old-task\"\nassignment_revision: 2\n```\n",
            "same value restated": "Base: " + "a" * 40 + "\n",
        }
        cases.update({f"key {key}": f"{key}: leftover-value\n" for key in worker_brief.DECLARED_KEYS})
        for name, extra in cases.items():
            with self.subTest(name):
                self.assert_rejected(render(self.record(), extra="\n" + extra),
                                     "outside the worker-brief declaration", "--run-dir", str(self.run_dir))

    def test_reference_documents_every_restated_key(self):
        reference = " ".join(WORKERS_REFERENCE.read_text(encoding="utf-8").split())
        start = reference.index("**Leftovers and duplicates (both paths).**")
        rule = reference[start:reference.index("repeated section headings", start)]
        for key in worker_brief.DECLARED_KEYS:
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", rule)

    def test_duplicate_instructions_and_sections_are_flagged(self):
        line = "Run the full unittest suite and record every exit code in checks.txt.\n"
        roster = ("--run-dir", str(self.run_dir))
        self.assert_rejected(render(self.record(), task=line + "\n" + line), "duplicate instruction at lines",
                             *roster)
        self.assert_rejected(render(self.record(), extra="\n## Task\n\nMore leftover work.\n"),
                             "duplicate section heading 'Task'", *roster)

    def test_roster_identity_failures(self):
        cases = {
            "unregistered task": (self.record(task_id="task-copied"), "is not registered"),
            "revision": (self.record(assignment_revision=2), "does not match roster revision 1"),
            "output directory": (self.record(output_directory=str(self.root / "elsewhere")),
                                 "does not match rostered directory"),
            "managed output on a direct roster": (self.record(output_directory="managed"),
                                                  "does not match rostered directory"),
        }
        for name, (record, message) in cases.items():
            with self.subTest(name):
                self.assert_rejected(render(record), message, "--run-dir", str(self.run_dir))

    def test_brief_outside_rostered_directory_is_refused(self):
        self.brief = self.run_dir / "tasks" / "brief.md"
        self.assert_rejected(render(self.record()), "not in the rostered directory",
                             "--run-dir", str(self.run_dir))

    def test_run_dir_requires_worker_brief_mode(self):
        self.brief.write_text(render(self.record()))
        process = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "check-brief", "--run-dir", str(self.run_dir), str(self.brief)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertIn("--run-dir and --manifest require --worker-brief", process.stderr)

    def write_manifest(self, **job_changes):
        job = {"job_id": "job-a", "name": "Direct", "task_kind": "ordinary", "task_file": str(self.brief),
               "cwd": str(self.root), "output_expectation": "Report.", "writes_repository": False,
               "worker_result": {"task_id": "task-a1", "assignment_revision": 1}}
        job.update(job_changes)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "direct", "jobs": [job]}))
        return manifest

    def repository_record(self, **repository_changes):
        lease = {"schema_version": 1, "path": str(self.root), "lease_id": "lease-1",
                 "lease_holder": "direct", "repo_root": str(self.root / "repo"),
                 "git_common_dir": str(self.root / "repo/.git"), "base_commit": "b" * 40}
        repository = {"repo": lease["repo_root"], "worktree": lease["path"],
                      "branch": "fix/1267-brief", "base": lease["base_commit"], **repository_changes}
        editable = str(self.root / "src")
        record = self.record(repository=repository, scope={
            "readable": [], "editable": [editable], "protected": DEFAULT_PROTECTED})
        job = {"writes_repository": True, "repository_worktree": lease, "editable_paths": [editable],
               "candidate_branch": "fix/1267-brief"}
        return record, job

    def test_roster_alone_cannot_vouch_for_repository_or_edit_scope(self):
        record, _ = self.repository_record()
        self.assert_rejected(render(record), "cannot be verified from workers.json",
                             "--run-dir", str(self.run_dir))

    def test_direct_brief_matches_its_manifest_assignment(self):
        record, job = self.repository_record()
        manifest = self.write_manifest(**job)
        process = self.check(render(record), "--run-dir", str(self.run_dir),
                             "--manifest", str(manifest), "--job-id", "job-a")
        self.assertEqual(process.returncode, 0, process.stderr)
        cases = {
            "copied branch": (self.repository_record(branch="fix/other")[0], job, "repository.branch"),
            "unrecorded branch": (record, {k: v for k, v in job.items() if k != "candidate_branch"},
                                  "does not record candidate_branch"),
            "copied identity": (dict(record, task_id="task-a1"), dict(
                job, worker_result={"task_id": "other-a1", "assignment_revision": 1}), "task_id 'task-a1'"),
            "editable scope": (record, dict(job, editable_paths=[str(self.root / "other")]), "scope.editable"),
        }
        for name, (case_record, case_job, message) in cases.items():
            with self.subTest(name):
                manifest = self.write_manifest(**case_job)
                self.assert_rejected(render(case_record), message, "--run-dir", str(self.run_dir),
                                     "--manifest", str(manifest), "--job-id", "job-a")

    def test_direct_check_refuses_every_manifest_error_the_launch_boundary_refuses(self):
        record, job = self.repository_record()
        cases = {
            "candidate branch on a non-writer": (
                self.record(), {"candidate_branch": "fix/x"},
                "candidate_branch requires writes_repository=true"),
            "git-invalid candidate branch": (
                self.repository_record(branch="HEAD")[0], dict(job, candidate_branch="HEAD"),
                "candidate_branch must be a plain Git branch name"),
            "editable path is protected": (
                self.record(scope={"readable": [], "editable": ["keys/"], "protected": DEFAULT_PROTECTED}),
                {"editable_paths": ["keys/"]}, "editable path is protected"),
            "cwd outside the leased worktree": (
                record, dict(job, cwd=str(self.root / "elsewhere")),
                "cwd must equal repository_worktree.path"),
            "unknown job field": (self.record(), {"surprise": True}, "surprise"),
            "structured_brief not boolean": (self.record(), {"structured_brief": "yes"},
                                             "structured_brief must be true or false"),
            "legacy flag with candidate branch": (
                record, dict(job, structured_brief=False),
                "structured_brief cannot be false"),
        }
        for name, (case_record, case_job, message) in cases.items():
            with self.subTest(name):
                manifest = self.write_manifest(**case_job)
                process = self.check(render(case_record), "--run-dir", str(self.run_dir),
                                     "--manifest", str(manifest), "--job-id", "job-a")
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertNotIn("BRIEF OK", process.stdout)
                self.assertIn(message, process.stderr)
                # The launch boundary refuses the same job with the same message.
                with self.assertRaisesRegex(records.JobError, re.escape(message)):
                    records.prepare(manifest, test_jobs.POLICY)

    def test_manifest_errors_are_reported_even_when_the_brief_is_invalid(self):
        manifest = self.write_manifest(candidate_branch="fix/x")
        process = self.check("# Not a structured brief\n", "--manifest", str(manifest), "--job-id", "job-a")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn("candidate_branch requires writes_repository=true", process.stderr)

    def test_manifest_without_run_dir_checks_a_managed_brief(self):
        manifest = self.write_manifest()
        process = self.check(render(self.record(output_directory="managed")),
                             "--manifest", str(manifest), "--job-id", "job-a")
        self.assertEqual(process.returncode, 0, process.stderr)
        process = self.check(render(self.record(task_id="copied-from-another-run", output_directory="managed")),
                             "--manifest", str(manifest), "--job-id", "job-a")
        self.assertEqual(process.returncode, 1)
        self.assertIn("task_id 'copied-from-another-run' does not match", process.stderr)

    def test_manifest_requires_a_unique_job_and_paired_options(self):
        manifest = self.write_manifest()
        process = self.check(render(self.record()), "--manifest", str(manifest), "--job-id", "missing")
        self.assertEqual(process.returncode, 2)
        self.assertIn("must contain exactly one job 'missing'", process.stderr)
        process = self.check(render(self.record()), "--manifest", str(manifest))
        self.assertEqual(process.returncode, 2)
        self.assertIn("--manifest and --job-id must be given together", process.stderr)


class WorkerBriefLaunchTest(unittest.TestCase):
    setUp = test_jobs.JobsTest.setUp
    write_jobs = test_jobs.JobsTest.write_jobs
    configure = test_jobs.JobsTest.configure
    argv = test_jobs.JobsTest.argv
    call = test_jobs.JobsTest.call
    events = test_jobs.JobsTest.events
    configure_repository_writer = test_jobs.JobsTest.configure_repository_writer

    def use_brief(self, content, **job_changes):
        self.task.write_text(content)
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0].update(job_changes)
        self.manifest.write_text(json.dumps(manifest))

    def record(self, **changes):
        return declaration("brief-task", self.root, **changes)

    def identity(self):
        return {"worker_result": {"task_id": "brief-task", "assignment_revision": 1}}

    def assert_refused(self, content, message, **job_changes):
        shutil.rmtree(self.run, ignore_errors=True)
        (self.root / "events.json").unlink(missing_ok=True)
        self.write_jobs(1)
        self.use_brief(content, **{**self.identity(), **job_changes})
        for operation in ("run", "add"):
            for preview in (True, False):
                with self.subTest(operation=operation, preview=preview):
                    code, result = self.call(operation, **({"preview": True} if preview else {}))
                    self.assertNotEqual(code, 0)
                    self.assertEqual(result["error"], "invalid_input")
                    self.assertIn("must be corrected before launch", result["message"])
                    self.assertIn(message, result["message"])
                    self.assertFalse(self.run.exists())
                    self.assertFalse((self.root / "events.json").exists())

    def test_valid_declared_brief_launches(self):
        self.use_brief(render(self.record()), **self.identity())
        for operation in ("run", "add"):
            code, result = self.call(operation, preview=True)
            self.assertEqual(code, 0, result)
        code, result = self.call()
        self.assertEqual(code, 0, result)
        prompt = (self.root / "prompt-job0.txt").read_text()
        self.assertIn(DECLARATION_START, prompt)

    def test_rendered_prompt_keeps_one_brief_core_and_result_contract(self):
        schema = subprocess.run([sys.executable, "-B", str(SCRIPT), "schema", "--format", "md"],
                                capture_output=True, text=True, timeout=5, check=True).stdout
        self.use_brief(render(self.record(), extra="\n" + schema),
                       **self.identity(), structured_brief=True)
        code, result = self.call()
        self.assertEqual(code, 0, result)
        prompt = (self.root / "prompt-job0.txt").read_text()
        _, issues = worker_brief.parse(prompt)
        self.assertEqual(issues, [])
        self.assertIn("evidence_gate:", prompt)
        self.assertIn("mode_rules:", prompt)

    def test_job_id_is_the_identity_without_worker_result(self):
        self.use_brief(render(declaration("job0", self.root)))
        code, result = self.call(preview=True)
        self.assertEqual(code, 0, result)

    def test_plain_task_without_brief_core_keeps_legacy_launch(self):
        code, result = self.call(preview=True)
        self.assertEqual(code, 0, result)

    def test_copied_brief_core_without_declaration_is_refused(self):
        copied = ("task_id: \"other-task\"\nassignment_revision: 1\nworker_mode: literal\n"
                  "objective: \"Copied from another assignment.\"\n")
        self.assert_refused(copied, "no ```json worker-brief declaration")

    def test_markdown_formatting_cannot_hide_leftovers_or_duplicates(self):
        instruction = "Run the fixture checks and record their exit codes in checks.txt."
        cases = {
            "code key": ("\n`task_id`: copied-task\n", "restates 'task_id'"),
            "numbered key": ("\n1. task_id: copied-task\n", "restates 'task_id'"),
            "numbered parenthesis key": ("\n2) **`task_id`**: copied-task\n", "restates 'task_id'"),
            "bulleted key": ("\n+ `repository`: /old/fixture-repo\n", "restates 'repository'"),
            "bullet styles": (f"\n- {instruction}\n* {instruction}\n", "duplicate instruction"),
            "number and code styling": (
                f"\n1. {instruction}\n2) **Run** the `fixture` checks and record their exit codes in checks.txt.\n",
                "duplicate instruction"),
            "heading levels": ("\n## Recovery\n\n### Recovery\n", "duplicate section heading 'Recovery'"),
            "heading styling": ("\n## `Recovery`\n\n### **Recovery**\n", "duplicate section heading 'Recovery'"),
        }
        for name, (extra, message) in cases.items():
            with self.subTest(name=name):
                self.assert_refused(render(self.record(), extra=extra), message, structured_brief=True)

    def test_paired_emphasis_cannot_hide_leftovers_or_duplicates(self):
        instruction = "Run the fixture checks and record their exit codes in checks.txt."
        for delimiter in ("***", "___", "**_", "*__"):
            closing = delimiter[::-1]
            with self.subTest(delimiter=delimiter, scenario="leftover"):
                self.assert_refused(
                    render(self.record(), extra=f"\n{delimiter}task_id{closing}: copied-task\n"),
                    "restates 'task_id'", structured_brief=True)
            with self.subTest(delimiter=delimiter, scenario="duplicate"):
                styled = instruction.replace("Run", delimiter + "Run" + closing, 1)
                self.assert_refused(render(self.record(), task=instruction + "\n" + styled + "\n"),
                                    "duplicate instruction", structured_brief=True)

    def test_distinct_formatted_instructions_and_inline_identifiers_launch(self):
        task = ("1. Read `fixture_one.json` and record the expected fixture fields.\n"
                "2) Read `fixture_two.json` and record the expected fixture fields.\n"
                "- Report `fixture_one.json` in the evidence inventory.\n"
                "* Report `fixture_two.json` in the evidence inventory.\n"
                "Use `task_id` when describing the fixture identity.\n"
                "## **Inputs**\n\n### `Outputs`\n")
        self.use_brief(render(self.record(), task=task), **self.identity(), structured_brief=True)
        code, result = self.call()
        self.assertEqual(code, 0, result)

    def test_formatted_copied_identity_cannot_downgrade_to_legacy_prose(self):
        for prefix in ("`task_id`", "1. task_id", "- **task_id**"):
            with self.subTest(prefix=prefix):
                self.assert_refused(prefix + ": copied-task\n", "no ```json worker-brief declaration")

    def test_unsupported_declaration_fences_cannot_downgrade_to_legacy_prose(self):
        for opening in ("```JSON worker-brief", "```json worker-brief extra",
                        "~~~json WORKER-BRIEF", "```worker-brief json",
                        "```json worker-brief`extra"):
            with self.subTest(opening=opening):
                content = render(self.record(task_id="copied-task")).replace(
                    "```json worker-brief", opening, 1)
                self.assert_refused(content, "unsupported worker-brief declaration fence")

    def test_quoted_declarations_cannot_downgrade_to_legacy_prose(self):
        for prefix in ("> ", "> > ", "1. > - "):
            for malformed in (False, True):
                with self.subTest(prefix=prefix, malformed=malformed):
                    content = render(self.record(task_id="copied-task"))
                    if malformed:
                        content = content.replace("```json worker-brief", "```JSON worker-brief extra", 1)
                    quoted = "\n".join(prefix + line for line in content.splitlines())
                    self.assert_refused(quoted, "unsupported worker-brief declaration fence" if malformed
                                        else "task_id 'copied-task' does not match")

    def test_matching_quoted_declaration_preserves_literal_json_text(self):
        objective = "Preserve ***literal*** markers and CaseSensitivePath in fixture text."
        content = render(self.record(objective=objective))
        quoted = "\n".join("> " + line for line in content.splitlines())
        parsed, issues = worker_brief.parse(quoted)
        self.assertEqual(issues, [])
        self.assertEqual(parsed["objective"], objective)
        self.use_brief(quoted, **self.identity(), structured_brief=True)
        code, result = self.call()
        self.assertEqual(code, 0, result)

    def test_case_distinct_commands_and_paths_launch(self):
        for name, value in (("source_one", "lower directory"), ("Source_One", "mixed directory")):
            directory = self.root / name
            directory.mkdir()
            (directory / "input.txt").write_text(value)
        for name, label in (("VerifyScopedOutput.py", "mixed script"),
                            ("verifyscopedoutput.py", "lower script")):
            program = ("from pathlib import Path\nimport sys\n"
                       f"print({label!r}, (Path(sys.argv[-1]) / 'input.txt').read_text())\n")
            (self.root / name).write_text(program)
            process = subprocess.run([sys.executable, str(self.root / name), "--directory", "source_one"],
                                     cwd=self.root, capture_output=True, text=True, check=True)
            self.assertEqual(process.stdout.strip(), label + " lower directory")
        pairs = (
            ("python3 VerifyScopedOutput.py --directory source_one",
             "python3 verifyscopedoutput.py --directory source_one"),
            ("python3 VerifyScopedOutput.py --directory source_one",
             "python3 VerifyScopedOutput.py --directory Source_One"),
        )
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                self.use_brief(render(self.record(), task=first + "\n" + second + "\n"),
                               **self.identity(), structured_brief=True)
                code, result = self.call(preview=True)
                self.assertEqual(code, 0, result)
        code, result = self.call()
        self.assertEqual(code, 0, result)

    def test_ordinary_fences_and_nested_examples_keep_legacy_prose(self):
        for example in (
                '```json\n{"task_id": "example-value"}\n```\n',
                '```JSON worker-briefing\n{"task_id": "example-value"}\n```\n',
                '````text\n```JSON worker-brief\n{"task_id": "example-value"}\n```\n````\n',
                '~~~python\nprint("worker-brief example")\n~~~\n'):
            with self.subTest(example=example):
                self.use_brief("Inspect the fixture. These are illustrative examples.\n" + example,
                               structured_brief=False)
                code, result = self.call(preview=True)
                self.assertEqual(code, 0, result)
                self.assertFalse(self.run.exists())

    def test_identity_mismatches_are_refused(self):
        cases = {
            "copied task id": (self.record(task_id="other-task"), "task_id 'other-task' does not match"),
            "revision": (self.record(assignment_revision=2), "assignment_revision 2 does not match"),
            "mode": (self.record(worker_mode="inferential"), "worker_mode 'inferential' does not match"),
        }
        for name, (record, message) in cases.items():
            with self.subTest(name):
                self.assert_refused(render(record), message)

    def test_path_mismatches_are_refused(self):
        editable = self.root / "out"
        cases = {
            "cwd": (self.record(cwd=str(self.root / "elsewhere")), {}, "cwd"),
            "output directory": (self.record(output_directory=str(self.root / "out")), {},
                                 "output_directory"),
            "editable widened": (self.record(scope={"readable": [], "editable": [str(editable)],
                                                    "protected": DEFAULT_PROTECTED}), {},
                                 "scope.editable"),
            "editable omitted": (self.record(), {"editable_paths": [str(editable)]}, "scope.editable"),
            "acceptance test unprotected": (self.record(), {"acceptance_tests": [str(self.context_test)]},
                                            "scope.protected omits assigned protected path"),
            "repository on a non-writer": (self.record(repository={
                "repo": str(self.root), "worktree": str(self.root), "branch": "fix/x", "base": "a" * 40}),
                {}, "assignment does not write a repository"),
        }
        for name, (record, changes, message) in cases.items():
            with self.subTest(name):
                self.assert_refused(render(record), message, **changes)

    def test_leftover_and_duplicate_instructions_are_refused(self):
        self.assert_refused(render(self.record(), extra="\nWorktree: /old/worktree\n"),
                            "restates 'Worktree'")
        line = "Run the full unittest suite and record every exit code in checks.txt.\n"
        self.assert_refused(render(self.record(), task=line + line), "duplicate instruction")
        self.assert_refused(render(self.record(objective="{{one observable outcome}}")),
                            "unreplaced template placeholder")

    def repository_job(self, **repository_changes):
        worktree = self.root / "worktree"
        worktree.mkdir(exist_ok=True)
        lease = {"schema_version": 1, "path": str(worktree), "lease_id": "lease-1",
                 "lease_holder": "test-request", "repo_root": str(self.root / "repo"),
                 "git_common_dir": str(self.root / "repo/.git"), "base_commit": "b" * 40}
        repository = {"repo": lease["repo_root"], "worktree": lease["path"],
                      "branch": "fix/1267-brief", "base": lease["base_commit"], **repository_changes}
        editable = str(worktree / "src")
        record = declaration("brief-task", worktree, repository=repository, scope={
            "readable": [], "editable": [editable], "protected": DEFAULT_PROTECTED})
        return record, {"writes_repository": True, "repository_worktree": lease, "cwd": str(worktree),
                        "editable_paths": [editable]}

    def test_repository_binding_must_match_assignment(self):
        record, changes = self.repository_job()
        self.use_brief(render(record), **self.identity(), **changes, candidate_branch="fix/1267-brief")
        code, result = self.call(preview=True)
        self.assertEqual(code, 0, result)
        cases = {
            "branch": ({"branch": "fix/other"}, {"candidate_branch": "fix/1267-brief"},
                       "repository.branch 'fix/other' does not match"),
            "unrecorded branch": ({}, {}, "does not record candidate_branch"),
            "base": ({"base": "c" * 40}, {"candidate_branch": "fix/1267-brief"}, "repository.base"),
            "worktree": ({"worktree": str(self.root / "other-worktree")},
                         {"candidate_branch": "fix/1267-brief"}, "repository.worktree"),
            "repo": ({"repo": str(self.root / "other-repo")}, {"candidate_branch": "fix/1267-brief"},
                     "repository.repo"),
        }
        for name, (repository_changes, job_changes, message) in cases.items():
            with self.subTest(name):
                record, changes = self.repository_job(**repository_changes)
                self.assert_refused(render(record), message, **changes, **job_changes)
        record, changes = self.repository_job()
        self.assert_refused(render({**record, "repository": None}), "repository must be declared",
                            **changes, candidate_branch="fix/1267-brief")

    def test_declared_brief_launches_a_real_repository_writer(self):
        lease, _ = self.configure_repository_writer(merge_candidate=False)
        repository = {"repo": lease["repo_root"], "worktree": lease["path"],
                      "branch": "candidate", "base": lease["base_commit"]}
        editable = json.loads(self.manifest.read_text())["jobs"][0]["editable_paths"]
        record = declaration("task-finish", lease["path"], repository=repository,
                             scope={"readable": [], "editable": editable, "protected": DEFAULT_PROTECTED})
        self.task.write_text(render(record))
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["candidate_branch"] = "candidate"
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call()
        self.assertEqual(code, 0, result)
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)

    def test_assignment_requiring_a_declaration_refuses_a_prose_brief(self):
        # The 2026-10-11 shape: a copied coordinator brief in prose with a foreign
        # identity and leftover repository paths, and no `task_id:` line.
        copied = ("Task: other-task-from-last-run; assignment revision 1.\n"
                  "Review ONE candidate: /old/repo branch fix/old abc1234 (base def5678), "
                  "worktree /old/worktree (read-only).\n"
                  "Write only /old/run/tasks/other-task-from-last-run.\n")
        _, changes = self.repository_job()
        self.assert_refused(copied, "the assignment requires a structured brief",
                            **changes, candidate_branch="fix/1267-brief")
        self.assert_refused(copied, "no ```json worker-brief declaration", structured_brief=True)
        self.assert_refused("Implement the fixture change.\n", "requires a structured brief",
                            structured_brief=True)

    def test_structured_brief_flag_admits_a_matching_declaration(self):
        self.use_brief(render(self.record()), **self.identity(), structured_brief=True)
        for operation in ("run", "add"):
            code, result = self.call(operation, preview=True)
            self.assertEqual(code, 0, result)

    def test_explicit_legacy_flag_keeps_prose_launch_but_not_with_a_candidate_branch(self):
        self.use_brief("Implement the fixture change.\n", **self.identity(), structured_brief=False)
        code, result = self.call(preview=True)
        self.assertEqual(code, 0, result)
        record, changes = self.repository_job()
        cases = {
            "legacy with candidate branch": ({**changes, "candidate_branch": "fix/1267-brief",
                                              "structured_brief": False}, "structured_brief cannot be false"),
            "not boolean": ({"structured_brief": "yes"}, "structured_brief must be true or false"),
        }
        for name, (job_changes, message) in cases.items():
            with self.subTest(name):
                self.use_brief(render(record), **self.identity(), **job_changes)
                code, result = self.call(preview=True)
                self.assertNotEqual(code, 0)
                self.assertEqual(result["error"], "invalid_input")
                self.assertIn(message, result["message"])

    def test_git_valid_punctuation_branches_match_the_manifest(self):
        for branch in ("fix/c++", "feature/@name", "release/v1=ok", "feature/é"):
            with self.subTest(branch=branch):
                record, changes = self.repository_job(branch=branch)
                self.use_brief(render(record), **self.identity(), **changes, candidate_branch=branch)
                for operation in ("run", "add"):
                    code, result = self.call(operation, preview=True)
                    self.assertEqual(code, 0, result)

    def test_git_invalid_candidate_branch_is_refused(self):
        for branch in ("-leading-dash", "HEAD", ".hidden", "feature/.hidden"):
            with self.subTest(branch=branch):
                record, changes = self.repository_job(branch=branch)
                self.use_brief(render(record), **self.identity(), **changes, candidate_branch=branch)
                code, result = self.call(preview=True)
                self.assertNotEqual(code, 0)
                self.assertIn("candidate_branch must be a plain Git branch name", result["message"])

    def test_candidate_branch_requires_a_repository_writer(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["candidate_branch"] = "fix/x"
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call(preview=True)
        self.assertNotEqual(code, 0)
        self.assertIn("candidate_branch requires writes_repository=true", result["message"])


if __name__ == "__main__":
    unittest.main()
