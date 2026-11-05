from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.records import JobError
from herdr_jobs.transport import Deadline, NativeTransport, claude_catalog
import test_transport


ATTESTATION = {"verified": True, "models": [
    {"id": "claude-sonnet-5-5", "efforts": ["low", "medium", "high", "xhigh"]},
    {"id": "claude-opus-5-5", "efforts": ["medium", "high"]},
]}


class ClaudeCatalogTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_transport.TransportTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.adapter = self.fixture.adapter
        self.adapter.binding["supported_kinds"].append("claude")
        self.fixture.available.append("claude-code")
        self.fixture.spec.update(kind="claude", model="claude-sonnet-5-5",
                                 route={"mode": "agent", "runtime": "claude-code"},
                                 override={"effort": "medium"})
        self.fixture.catalog = {"runtimes": [{"id": "claude-code", "status": "partial", "models": [
            {"id": "sonnet"}, {"id": "opus"}, {"id": "haiku"}]}]}
        self.adapter.binding["claude_exact_catalog"] = deepcopy(ATTESTATION)

    def preflight(self):
        return self.adapter.preflight({"jobs": [self.fixture.spec], "policy": {}})

    def test_attestation_enables_exact_model_despite_partial_alias_discovery(self):
        result = self.preflight()
        self.assertEqual(result["models"], {"j1": "claude-sonnet-5-5"})
        self.assertFalse(any(call[1] == "models" for call in self.fixture.calls))
        self.assertFalse(any(call[1] in ("pane", "agent") and call[2] != "--help"
                             for call in self.fixture.calls))

    def test_missing_attestation_does_not_weaken_partial_catalog_gate(self):
        del self.adapter.binding["claude_exact_catalog"]
        with self.assertRaisesRegex(JobError, "discovery incomplete"):
            self.preflight()

    def test_attested_model_without_requested_effort_refuses_new_and_pending_jobs(self):
        self.fixture.spec.pop("override")
        self.adapter.binding["claude_exact_catalog"] = {"verified": True, "models": [
            {"id": "claude-sonnet-5-5", "efforts": ["medium"]}]}
        pending = {"jobs": [{"spec": self.fixture.spec, "resolved_model": "claude-sonnet-5-5",
                             "phase": "pending", "pending_effect": None}]}
        for existing in (None, pending):
            with self.subTest(resuming=existing is not None):
                with self.assertRaises(JobError) as raised:
                    self.adapter.preflight({"jobs": [self.fixture.spec], "policy": {}}, existing)
                self.assertEqual(raised.exception.kind, "unavailable_capability")

    def test_malformed_or_unverified_attestation_never_falls_back_to_discovery(self):
        self.fixture.catalog = {"models": [{"id": "claude-sonnet-5-5", "efforts": ["medium"]}]}
        for attestation in (None, [], {}, {**ATTESTATION, "verified": False},
                            {**ATTESTATION, "verified": 1}, {**ATTESTATION, "models": []},
                            {**ATTESTATION, "extra": True}):
            with self.subTest(attestation=attestation):
                self.adapter.binding["claude_exact_catalog"] = attestation
                with self.assertRaises(JobError):
                    self.preflight()

    def test_unlisted_model_or_effort_refuses_without_silent_substitution(self):
        for model, effort in (("claude-haiku-4-5-20251001", "medium"),
                              ("sonnet", "medium"), ("claude-sonnet-5-5", "max"),
                              ("claude-opus-5-5", "xhigh")):
            with self.subTest(model=model, effort=effort):
                self.fixture.spec.update(model=model, override={"effort": effort})
                with self.assertRaises(JobError):
                    self.preflight()

    def test_whole_catalog_rejects_aliases_duplicate_ids_and_invalid_efforts(self):
        for item in ({"id": "sonnet", "efforts": ["medium"]},
                     {"id": "claude-sonnet-latest", "efforts": ["medium"]},
                     {"id": "claude-sonnet-5-5", "efforts": "medium"},
                     {"id": "claude-sonnet-5-5", "efforts": []},
                     {"id": "claude-sonnet-5-5", "efforts": ["medium", "medium"]},
                     {"id": "claude-sonnet-5-5", "efforts": [True]},
                     {"id": "claude-sonnet-5-5", "efforts": ["max"]},
                     {"id": "claude-sonnet-5-5"}):
            with self.subTest(item=item):
                with self.assertRaises(JobError):
                    claude_catalog({"verified": True, "models": [item]})
        with self.assertRaisesRegex(JobError, "unique exact"):
            claude_catalog({"verified": True, "models": [ATTESTATION["models"][0]] * 2})
        self.adapter.binding["claude_exact_catalog"]["models"].append({"id": "opus", "efforts": ["high"]})
        with self.assertRaises(JobError):
            self.preflight()

    def test_resume_of_pending_job_revalidates_attested_catalog(self):
        existing = {"jobs": [{"spec": self.fixture.spec, "resolved_model": "claude-sonnet-5-5",
                               "phase": "pending", "pending_effect": None}]}
        self.adapter.binding["claude_exact_catalog"]["verified"] = False
        with self.assertRaises(JobError):
            self.adapter.preflight({"jobs": [self.fixture.spec], "policy": {}}, existing)

    def test_claude_attestation_cannot_authorize_codex(self):
        self.fixture.spec.update(kind="codex", route={"mode": "agent", "runtime": "codex"}, override={})
        with self.assertRaises(JobError):
            self.preflight()

    def test_host_contract_loader_accepts_optional_attestation(self):
        contract = self.fixture.root / "host-contract.json"
        contract.write_text(json.dumps({"schema_version": 1, "verified": True, **self.adapter.binding}))
        with patch.dict(os.environ, {"HERDR_ENV": "1"}), \
                patch("herdr_jobs.transport.shutil.which", side_effect=lambda value: value):
            adapter = NativeTransport(contract, Deadline(1))
        self.assertEqual(adapter.binding["claude_exact_catalog"], ATTESTATION)

    def test_exact_cwd_trust_precedes_start_and_never_sends_the_task_in_start_argv(self):
        fixture = self.fixture
        fixture.job["resolved_model"] = "claude-sonnet-5-5"
        fake = test_transport.FakeHerdr()
        config = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"

        def start(argv, deadline, cwd=None, env=None):
            self.assertTrue(json.loads(config.read_bytes())["projects"][str(fixture.root.resolve())]["hasTrustDialogAccepted"])
            return fake.command(argv, deadline, cwd, env)

        with patch("herdr_jobs.transport.command", start):
            self.adapter.effect("start", fixture.job, "Assigned work")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][-5:], ["--", "--model", "claude-sonnet-5-5", "--effort", "medium"])
        self.assertNotIn("Assigned work", fake.calls[0])

    def test_malformed_trust_config_prevents_every_host_start_effect(self):
        fixture = self.fixture
        fixture.job["resolved_model"] = "claude-sonnet-5-5"
        config = Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
        config.parent.mkdir()
        config.write_bytes(b"{malformed-fixture")
        with patch("herdr_jobs.transport.command") as command:
            with self.assertRaises(JobError):
                self.adapter.effect("start", fixture.job, "Assigned work")
        command.assert_not_called()
