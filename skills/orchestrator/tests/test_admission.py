import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
CLI = PACKAGE / "scripts/herdr-admission.py"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from herdr_jobs.admission import AdmissionStore, backoff_delay, rate_limit_from_output
from herdr_jobs.records import JobError
from herdr_jobs.admission_service import AdmissionService
from support import isolate_admission_state


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        isolate_admission_state(self, self.root / "state")
        self.store = AdmissionStore(self.root / "state")

    def call_cli(self, *arguments):
        result = subprocess.run([sys.executable, "-B", str(CLI), *arguments],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_first_provider_lease_is_admitted(self):
        result = self.store.acquire("codex", "gpt-6-luna", "run-a-job-a", cap=4)

        self.assertTrue(result["admitted"])
        self.assertEqual(result["provider_active"], 1)
        self.assertEqual(result["model_active"], 1)

    def test_cli_acquire_and_separate_status_share_the_provider_count(self):
        acquired = self.call_cli("acquire", "--provider", "codex", "--model", "gpt-6-luna",
                                 "--lease-id", "direct-a")
        observed = self.call_cli("status", "--provider", "codex")

        self.assertTrue(acquired["admitted"])
        self.assertEqual(observed["active_count"], 1)
        self.assertEqual(observed["leases"][0]["lease_id"], "direct-a")
        released = self.call_cli("release", "--lease-id", "direct-a")
        self.assertTrue(released["released"])
        self.assertEqual(self.call_cli("status", "--provider", "codex")["active_count"], 0)

    def test_cli_release_requires_reconciliation_for_an_unknown_effect(self):
        acquired = self.store.acquire("llama-local", "qwen3.8-27b-uncensored", "direct-unknown", cap=1)
        effect = {
            "action": "start",
            "status": "unknown",
            "pane_id": "moved:1",
            "agent_name": "bounded-task",
            "observation": {"state": "unknown", "identity_verified": False,
                            "prompt_verified": False},
        }
        self.store.record_unknown_effect(acquired["lease_id"], effect)
        with self.assertRaisesRegex(JobError, "reconciliation evidence"):
            self.store.release(acquired["lease_id"])

        rejected = subprocess.run(
            [sys.executable, "-B", str(CLI), "release", "--lease-id", acquired["lease_id"]],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(rejected.returncode, 3)
        self.assertEqual(json.loads(rejected.stdout)["error"], "conflict")
        self.assertEqual(self.store.status("llama-local")["active_count"], 1)

        released = self.call_cli(
            "release", "--lease-id", acquired["lease_id"],
            "--reconciliation-evidence", "herdr reports no owned worker; pane shows shell prompt",
        )
        self.assertTrue(released["released"])
        self.assertEqual(self.store.status("llama-local")["active_count"], 0)

    def test_retry_after_blocks_only_that_provider_model_until_expiry(self):
        current_time = [1000.0]
        store = AdmissionStore(self.root / "clock-state", clock=lambda: current_time[0])
        store.note_rate_limit("codex", "gpt-6-luna", 12, source="retry-after")

        blocked = store.acquire("codex", "gpt-6-luna", "same-model", cap=4)
        other_model = store.acquire("codex", "another-model", "other-model", cap=4)
        current_time[0] = 1012.0
        resumed = store.acquire("codex", "gpt-6-luna", "after-wait", cap=4)

        self.assertEqual((blocked["admitted"], blocked["reason"], blocked["retry_at"]),
                         (False, "backoff", 1012.0))
        self.assertTrue(other_model["admitted"])
        self.assertTrue(resumed["admitted"])

    def test_provider_cap_is_shared_across_models_and_release_reopens_capacity(self):
        policy = json.loads((PACKAGE / "launch-policy.json").read_text())
        policy["provider_admission"]["provider_caps"]["codex"] = 2
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps(policy))
        policy_arg = ("--policy", str(policy_path))
        first = self.call_cli("acquire", "--provider", "codex", "--model", "gpt-6-luna",
                              "--lease-id", "codex-first", *policy_arg)
        second = self.call_cli("acquire", "--provider", "codex", "--model", "another-model",
                               "--lease-id", "codex-second", *policy_arg)
        denied = self.call_cli("acquire", "--provider", "codex", "--model", "other-model",
                               "--lease-id", "codex-third", *policy_arg)

        self.assertTrue(first["admitted"])
        self.assertTrue(second["admitted"])
        self.assertEqual(second["provider_active"], 2)
        self.assertFalse(denied["admitted"])
        self.assertEqual(denied["reason"], "capacity")
        self.call_cli("release", "--lease-id", "codex-first")
        self.assertTrue(self.call_cli("acquire", "--provider", "codex", "--model", "other-model",
                                      "--lease-id", "codex-after-release", *policy_arg)["admitted"])

    def test_concurrent_acquisitions_cannot_exceed_a_provider_cap(self):
        stores = [AdmissionStore(self.root / "race-state") for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda index: stores[index].acquire("codex", "gpt-6-luna", f"parallel-{index}", cap=4),
                range(8),
            ))

        self.assertEqual(sum(result["admitted"] for result in results), 4)
        self.assertEqual(stores[0].status("codex")["active_count"], 4)

    def test_rate_limit_metadata_and_bounded_default_are_parsed(self):
        retry = rate_limit_from_output(
            'Error: 429: {"code":"1302","message":"Rate limit reached"}\nRetry-After: 15',
            now=1000.0, runtime="pi")
        dated_retry = rate_limit_from_output(
            "HTTP/1.1 429 Too Many Requests\nRetry-After: Thu, 01 Jan 1970 00:16:45 GMT",
            now=1000.0, source="harness_error", exit_code=1)
        reset = rate_limit_from_output(
            'Error: 429: {"code":"1302"}\nX-RateLimit-Reset: 1025', now=1000.0, runtime="pi")
        expired_reset = rate_limit_from_output(
            'Error: 429: {"code":"1302","x-ratelimit-reset":1699999999}',
            now=1700000000.0, runtime="pi")
        go_usage_limit = rate_limit_from_output(
            'Error: 429: {"error":{"type":"GoUsageLimitError"}}', runtime="pi", now=1000.0)
        codex_banner = rate_limit_from_output("API Error: Rate limit reached", runtime="codex", now=1000.0)
        claude_banner = rate_limit_from_output(
            "You've hit your usage limit · resets at 10:00 UTC\nReset-At: 1030",
            runtime="claude-code", now=1000.0)
        claude_status = rate_limit_from_output(
            "Usage limit reached.\nRetry-After: 45", runtime="claude", now=1000.0)

        self.assertEqual(retry, {"retry_after_seconds": 15.0, "source": "retry-after"})
        self.assertEqual(dated_retry, {"retry_after_seconds": 5.0, "source": "retry-after"})
        self.assertEqual(reset, {"retry_after_seconds": 25.0, "source": "reset"})
        self.assertEqual(expired_reset, {"retry_after_seconds": 0.0, "source": "reset"})
        self.assertEqual(go_usage_limit, {"retry_after_seconds": None, "source": None})
        self.assertEqual(codex_banner, {"retry_after_seconds": None, "source": None})
        self.assertEqual(claude_banner, {"retry_after_seconds": 30.0, "source": "reset"})
        self.assertEqual(claude_status, {"retry_after_seconds": 45.0, "source": "retry-after"})
        self.assertEqual(backoff_delay(default_seconds=60, now=1000.0), (60.0, "default"))

    def test_natural_reset_banners_parse_relative_and_zoned_times(self):
        relative = rate_limit_from_output(
            "You've hit your usage limit · resets in 2h 13m",
            now=1000.0, runtime="claude-code")
        zoned = rate_limit_from_output(
            "You've hit your usage limit · try again at 10:00 AM America/Vancouver",
            now=1791734400.0, runtime="claude-code")

        self.assertEqual(relative, {"retry_after_seconds": 7980.0, "source": "reset"})
        self.assertEqual(zoned, {"retry_after_seconds": 3600.0, "source": "reset"})
        self.assertEqual(backoff_delay(reset_at="resets in 2h 13m", now=1000.0),
                         (7980.0, "reset"))

    def test_natural_reset_clock_without_zone_uses_host_timezone(self):
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/Vancouver"
            time.tzset()
            result = rate_limit_from_output(
                "You've hit your usage limit · try again at 10:00 AM",
                now=1791734400.0, runtime="claude-code")
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

        self.assertEqual(result, {"retry_after_seconds": 3600.0, "source": "reset"})

    def test_unparseable_banner_reset_keeps_the_bounded_default(self):
        signal = rate_limit_from_output(
            "You've hit your usage limit · resets at the next opportunity",
            now=1000.0, runtime="claude-code")

        self.assertEqual(signal, {"retry_after_seconds": None, "source": None})
        self.assertEqual(backoff_delay(signal["retry_after_seconds"], default_seconds=60, now=1000.0),
                         (60.0, "default"))

    def test_natural_reset_is_recorded_in_shared_admission_backoff(self):
        current_time = 1791734400.0
        store = AdmissionStore(self.root / "fake-host-admission", clock=lambda: current_time)
        signal = rate_limit_from_output(
            "You've hit your usage limit · resets at 10:00 AM America/Vancouver",
            now=current_time, runtime="claude-code")

        recorded = AdmissionService(store).rate_limit(
            {"provider": "codex", "admission_model": "gpt-6-luna"}, signal)
        denied = store.acquire("codex", "gpt-6-luna", "next-attempt", cap=4)

        self.assertEqual(recorded["retry_at"], 1791738000.0)
        self.assertEqual((denied["admitted"], denied["reason"], denied["retry_at"]),
                         (False, "backoff", 1791738000.0))

    def test_prompt_and_report_mentions_do_not_trigger_provider_backoff(self):
        samples = (
            ("pi", "Please explain HTTP 429 and rate limit responses in the prompt."),
            ("codex", "The report mentions Error: 429 and says rate limit twice."),
            ("pi", 'Example from a report: Error: 429: {"code":"1302"}'),
        )
        for runtime, transcript in samples:
            with self.subTest(runtime=runtime, transcript=transcript):
                self.assertIsNone(rate_limit_from_output(transcript, runtime=runtime))

        self.assertIsNone(rate_limit_from_output(
            "HTTP/1.1 429 Too Many Requests", source="harness_error", exit_code=0))
        self.assertIsNone(rate_limit_from_output(
            "The prompt discusses HTTP 429 and rate limit behavior.",
            source="harness_error", exit_code=1, runtime="pi"))

    def test_cli_uses_bounded_default_when_rate_limit_has_no_metadata(self):
        before = time.time()
        recorded = self.call_cli("rate-limit", "--provider", "codex", "--model", "gpt-6-luna")
        observed = self.call_cli("status", "--provider", "codex", "--model", "gpt-6-luna")
        denied = self.call_cli("acquire", "--provider", "codex", "--model", "gpt-6-luna",
                               "--lease-id", "retry-too-soon")

        delay = observed["backoffs"][0]["until"] - before
        self.assertEqual(recorded["source"], "default")
        self.assertGreaterEqual(delay, 59)
        self.assertLessEqual(delay, 61)
        self.assertFalse(denied["admitted"])
        self.assertEqual(denied["reason"], "backoff")

    def test_rate_limit_cli_writes_a_task_scoped_continuation_record(self):
        task_dir = self.root / "run" / "tasks" / "direct-task"
        task_dir.mkdir(parents=True)
        record_path = task_dir / "quota-continuation.json"
        before = time.time()

        self.call_cli("rate-limit", "--provider", "codex", "--model", "gpt-6-luna",
                      "--retry-after", "30", "--continuation-record", str(record_path),
                      "--task-id", "direct-task", "--pane-id", "pane-42", "--attempt-id", "attempt-7")

        record = json.loads(record_path.read_text())
        self.assertEqual(record["task"], "direct-task")
        self.assertEqual(record["pane"], "pane-42")
        self.assertEqual(record["attempt"], "attempt-7")
        self.assertGreaterEqual(record["reset_at"] - before, 29)
        self.assertLessEqual(record["reset_at"] - before, 31)


if __name__ == "__main__":
    unittest.main()
