import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from herdr_jobs.prompt import (PromptNotStarted, has_active_turn_status, pane_text_submit,
                               submit_and_verify)


class PromptTest(unittest.TestCase):
    def test_active_turn_status_requires_the_visible_working_marker(self):
        self.assertTrue(has_active_turn_status("\x1b[32mWorking\x1b[0m\n"))
        self.assertFalse(has_active_turn_status("The prompt says Working here."))
        self.assertFalse(has_active_turn_status("Working\nFinished"))

    def test_submits_once_and_waits_for_verified_working(self):
        events = []
        observations = iter([
            {"state": "idle", "identity_verified": True, "prompt_verified": False},
            {"state": "working", "identity_verified": True, "prompt_verified": True},
        ])
        result = submit_and_verify(
            lambda: events.append("submit"), lambda: next(observations),
            deadline=type("Deadline", (), {"remaining": lambda self: 1})(),
            sleep=lambda _: None,
        )
        self.assertEqual(result["state"], "working")
        self.assertEqual(events, ["submit"])

    def test_blocked_trust_observation_stops_without_resubmitting(self):
        events = []
        with self.assertRaisesRegex(PromptNotStarted, "trust dialog"):
            submit_and_verify(
                lambda: events.append("submit"),
                lambda: {"state": "blocked", "launch_issue": "trust dialog"},
                deadline=type("Deadline", (), {"remaining": lambda self: 1})(),
                sleep=lambda _: None,
            )
        self.assertEqual(events, [])

    def test_blocked_dialog_after_submission_stops_without_retry(self):
        events = []
        observations = iter([
            {"state": "idle", "identity_verified": True},
            {"state": "blocked", "launch_issue": "trust dialog"},
        ])
        with self.assertRaisesRegex(PromptNotStarted, "trust dialog"):
            submit_and_verify(
                lambda: events.append("submit"), lambda: next(observations),
                deadline=type("Deadline", (), {"remaining": lambda self: 1})(),
                sleep=lambda _: None,
            )
        self.assertEqual(events, ["submit"])

    def test_unverified_working_state_is_not_enough(self):
        with self.assertRaisesRegex(PromptNotStarted, "not observed"):
            submit_and_verify(
                lambda: None, lambda: {"state": "idle", "identity_verified": True},
                deadline=type("Deadline", (), {"remaining": lambda self: 0})(),
                sleep=lambda _: None,
            )

    def test_unverified_or_non_idle_worker_is_never_submitted_to(self):
        for observation in (
            {"state": "idle", "identity_verified": False},
            {"state": "working", "identity_verified": True},
        ):
            events = []
            with self.subTest(observation=observation), self.assertRaisesRegex(PromptNotStarted, "not verified and idle"):
                submit_and_verify(
                    lambda: events.append("submit"), lambda: observation,
                    deadline=type("Deadline", (), {"remaining": lambda self: 1})(),
                    sleep=lambda _: None,
                )
            self.assertEqual(events, [])

    def test_pane_transport_sends_text_then_enter(self):
        calls = []

        def command(argv, _deadline):
            calls.append(argv)
            return 0, b"", b""

        pane_text_submit(command, "herdr", "w1:p2", "literal $(text)", None)
        self.assertEqual(calls, [
            ["herdr", "pane", "send-text", "w1:p2", "literal $(text)"],
            ["herdr", "pane", "send-keys", "w1:p2", "enter"],
        ])

    def test_pane_transport_runs_inside_submit_and_verify(self):
        calls = []

        def command(argv, _deadline):
            calls.append(argv)
            return 0, b"", b""

        observations = iter([
            {"state": "idle", "identity_verified": True},
            {"state": "working", "identity_verified": True, "prompt_verified": True},
        ])
        submit_and_verify(
            lambda: pane_text_submit(command, "herdr", "w1:p2", "task", None),
            lambda: next(observations),
            deadline=type("Deadline", (), {"remaining": lambda self: 1})(),
            sleep=lambda _: None,
        )
        self.assertEqual([call[2] for call in calls], ["send-text", "send-keys"])


if __name__ == "__main__":
    unittest.main()
