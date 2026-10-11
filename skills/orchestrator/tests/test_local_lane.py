import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from herdr_jobs.local_lane import check_preconditions
from herdr_jobs.records import JobError


class LocalLaneTest(unittest.TestCase):
    def run_fixture(self, *, service=(0, "active", ""), gpu=(0, "3072\n", "")):
        calls = []

        def run(argv):
            calls.append(argv)
            return service if argv[0] == "systemctl" else gpu

        config = {"service_unit": "llama-server.service",
                  "minimum_free_gpu_memory_mib": 2048}
        return check_preconditions(config, run), calls

    def test_requires_active_model_server_and_reserved_gpu_headroom(self):
        result, calls = self.run_fixture()
        self.assertEqual(result["free_gpu_memory_mib"], 3072)
        self.assertEqual(result["service_state"], "active")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-2:], ["is-active", "llama-server.service"])
        self.assertIn("memory.free", calls[1][1])

    def test_blocks_when_server_is_not_active(self):
        with self.assertRaisesRegex(JobError, "requires active user service"):
            self.run_fixture(service=(3, "inactive", ""))

    def test_blocks_when_gpu_headroom_is_insufficient_or_ambiguous(self):
        for gpu in ((0, "2047\n", ""), (0, "2048\n4096\n", ""), (1, "", "missing")):
            with self.subTest(gpu=gpu), self.assertRaises(JobError):
                self.run_fixture(gpu=gpu)


if __name__ == "__main__":
    unittest.main()
