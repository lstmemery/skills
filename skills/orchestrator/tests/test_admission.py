import json
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
        result = self.store.acquire("zai", "glm-4.5", "run-a-job-a", cap=4)

        self.assertTrue(result["admitted"])
        self.assertEqual(result["provider_active"], 1)
        self.assertEqual(result["model_active"], 1)

    def test_cli_acquire_and_separate_status_share_the_provider_count(self):
        acquired = self.call_cli("acquire", "--provider", "zai", "--model", "glm-4.5",
                                 "--lease-id", "direct-a")
        observed = self.call_cli("status", "--provider", "zai")

        self.assertTrue(acquired["admitted"])
        self.assertEqual(observed["active_count"], 1)
        self.assertEqual(observed["leases"][0]["lease_id"], "direct-a")
        released = self.call_cli("release", "--lease-id", "direct-a")
        self.assertTrue(released["released"])
        self.assertEqual(self.call_cli("status", "--provider", "zai")["active_count"], 0)

    def test_retry_after_blocks_only_that_provider_model_until_expiry(self):
        current_time = [1000.0]
        store = AdmissionStore(self.root / "clock-state", clock=lambda: current_time[0])
        store.note_rate_limit("zai", "glm-4.5", 12, source="retry-after")

        blocked = store.acquire("zai", "glm-4.5", "same-model", cap=4)
        other_model = store.acquire("zai", "glm-4.7", "other-model", cap=4)
        current_time[0] = 1012.0
        resumed = store.acquire("zai", "glm-4.5", "after-wait", cap=4)

        self.assertEqual((blocked["admitted"], blocked["reason"], blocked["retry_at"]),
                         (False, "backoff", 1012.0))
        self.assertTrue(other_model["admitted"])
        self.assertTrue(resumed["admitted"])

    def test_provider_cap_is_shared_across_models_and_release_reopens_capacity(self):
        first = self.call_cli("acquire", "--provider", "zai", "--model", "glm-4.5",
                              "--lease-id", "zai-first")
        second = self.call_cli("acquire", "--provider", "zai", "--model", "glm-4.7",
                               "--lease-id", "zai-second")
        denied = self.call_cli("acquire", "--provider", "zai", "--model", "other-model",
                               "--lease-id", "zai-third")

        self.assertTrue(first["admitted"])
        self.assertTrue(second["admitted"])
        self.assertEqual(second["provider_active"], 2)
        self.assertFalse(denied["admitted"])
        self.assertEqual(denied["reason"], "capacity")
        self.call_cli("release", "--lease-id", "zai-first")
        self.assertTrue(self.call_cli("acquire", "--provider", "zai", "--model", "other-model",
                                      "--lease-id", "zai-after-release")["admitted"])

    def test_concurrent_acquisitions_cannot_exceed_a_provider_cap(self):
        stores = [AdmissionStore(self.root / "race-state") for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(
                lambda index: stores[index].acquire("zai", "glm-4.5", f"parallel-{index}", cap=4),
                range(8),
            ))

        self.assertEqual(sum(result["admitted"] for result in results), 4)
        self.assertEqual(stores[0].status("zai")["active_count"], 4)

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

        self.assertEqual(retry, {"retry_after_seconds": 15.0, "source": "retry-after"})
        self.assertEqual(dated_retry, {"retry_after_seconds": 5.0, "source": "retry-after"})
        self.assertEqual(reset, {"retry_after_seconds": 25.0, "source": "reset"})
        self.assertEqual(expired_reset, {"retry_after_seconds": 0.0, "source": "reset"})
        self.assertEqual(go_usage_limit, {"retry_after_seconds": None, "source": None})
        self.assertEqual(codex_banner, {"retry_after_seconds": None, "source": None})
        self.assertEqual(backoff_delay(default_seconds=60, now=1000.0), (60.0, "default"))

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
        recorded = self.call_cli("rate-limit", "--provider", "zai", "--model", "glm-4.5")
        observed = self.call_cli("status", "--provider", "zai", "--model", "glm-4.5")
        denied = self.call_cli("acquire", "--provider", "zai", "--model", "glm-4.5",
                               "--lease-id", "retry-too-soon")

        delay = observed["backoffs"][0]["until"] - before
        self.assertEqual(recorded["source"], "default")
        self.assertGreaterEqual(delay, 59)
        self.assertLessEqual(delay, 61)
        self.assertFalse(denied["admitted"])
        self.assertEqual(denied["reason"], "backoff")


if __name__ == "__main__":
    unittest.main()
