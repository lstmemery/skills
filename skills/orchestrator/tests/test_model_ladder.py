import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil
import urllib.error
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.admission import AdmissionStore
from herdr_jobs.model_ladder import load_ladder, native_arguments, select
from herdr_jobs.records import JobError, policy_record, prepare
from herdr_jobs.transport import catalog_models, pi_catalog
import test_transport


EXPECTED = [
    (("gpt-6-luna", "low", "codex", "codex"), ("gpt-6-luna", "low", "codex", "opencode go")),
    (("gpt-6-luna", "medium", "codex", "codex"), ("gpt-6-luna", "medium", "codex", "opencode go")),
    (("gpt-6-luna", "xhigh", "codex", "codex"), ("gpt-6-luna", "xhigh", "codex", "opencode go")),
    (("gpt-6-luna", "max", "codex", "codex"), ("gpt-6-luna", "max", "codex", "opencode go")),
    (("mimo-v2.6-pro", "max", "pi", "opencode go"), ("gpt-6.1-sol", "low", "codex", "codex")),
    (("gpt-6.1-sol", "high", "codex", "codex"), ("claude-sonnet-5-5", "medium", "claude", "claude")),
    (("gpt-6.1-sol", "max", "codex", "codex"), ("claude-sonnet-5-5", "high", "claude", "claude")),
    (("claude-opus-5-5", "high", "claude", "claude"), ("gpt-6-astra", "high", "codex", "codex")),
]


class LadderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / "model-ladder.csv"
        self.csv.write_bytes((PACKAGE / "model-ladder.csv").read_bytes())
        self.policy = self.root / "policy.json"
        self.policy.write_bytes((PACKAGE / "launch-policy.json").read_bytes())
        (self.root / "task.md").write_text("Produce the assigned artifact.")
        self.manifest = self.root / "manifest.json"
        self.job = {"job_id": "route-test", "name": "Route test", "task_kind": "ordinary",
                    "task_file": "task.md", "cwd": str(self.root), "output_expectation": "Report",
                    "writes_repository": False}

    def prepared(self, **values):
        self.manifest.write_text(json.dumps({"schema_version": 1, "request_id": "routes",
                                            "jobs": [{**self.job, **values}]}))
        return prepare(self.manifest, self.policy)["jobs"][0]

    def fixture(self):
        fixture = test_transport.TransportTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.available = ["codex", "pi", "claude-code"]
        fixture.agent["agent_status"] = "working"
        fixture.adapter.binding["supported_kinds"] = ["codex", "pi", "claude"]
        return fixture

    def test_all_sixteen_tuples_and_native_start_arguments(self):
        levels = load_ladder(self.csv)
        for level, pair in enumerate(EXPECTED, 1):
            for index, expected in enumerate(pair):
                with self.subTest(level=level, choice=index):
                    fallback = {"reason": "unavailable", "evidence": "Pinned primary catalog lacks exact model"} if index else None
                    selection = select(levels, level, fallback)
                    self.assertEqual(tuple(selection["requested"].values()), expected)
                    model, effort, harness, provider = expected
                    runtime = "claude-code" if harness == "claude" else harness
                    native_provider = "opencode-go" if provider == "opencode go" else provider
                    self.assertEqual((selection["runtime"], selection["provider"]), (runtime, native_provider))
                    arguments = {"model_level": level}
                    if fallback:
                        arguments["model_fallback"] = fallback
                    spec = self.prepared(**arguments)
                    fixture = self.fixture()
                    fixture.job.update(spec=spec, resolved_model=model)
                    fake = test_transport.FakeHerdr()
                    with patch("herdr_jobs.transport.command", fake.command):
                        fixture.adapter.effect("start", fixture.job, "Managed prompt")
                    argv = fake.calls[0]
                    boundary = argv.index("--")
                    actual = argv[boundary + 1:]
                    required = native_arguments(selection)
                    for offset in range(0, len(required), 2):
                        self.assertIn(required[offset:offset + 2], [actual[i:i + 2] for i in range(len(actual) - 1)])
                    if provider == "opencode go" and harness == "codex":
                        self.assertEqual(spec["kind"], "codex")
                        self.assertIn('model_provider="opencode-go"', actual)

    def test_default_is_cheapest_and_explicit_requests_win_over_level(self):
        default = self.prepared()
        self.assertEqual((default["model_route"]["level"], default["model"], default["override"]["effort"]),
                         (1, "gpt-6-luna", "low"))
        explicit = self.prepared(model_level=8, override={"instruction": "User pinned", "runtime": "codex",
                                 "model": "gpt-6.1-sol", "effort": "high", "provider": "codex"})
        self.assertNotIn("model_route", explicit)
        self.assertEqual((explicit["route"]["runtime"], explicit["model"], explicit["override"]["effort"]),
                         ("codex", "gpt-6.1-sol", "high"))
        named = self.prepared(override={"instruction": "Named harness", "runtime": "omp"})
        self.assertEqual(named["route"]["runtime"], "omp")
        self.assertIsNone(named["model"])
        effort_only = self.prepared(override={"instruction": "Pinned effort", "effort": "max"})
        self.assertEqual(effort_only["override"]["effort"], "max")
        self.assertIsNone(effort_only["model"])
        instruction = self.prepared(override={"instruction": "Preserve the user's scope"})
        self.assertEqual(instruction["override"]["instruction"], "Preserve the user's scope")
        with self.assertRaises(JobError):
            self.prepared(override={"instruction": ""})

    def test_same_level_fallback_requires_concrete_reason_and_retains_evidence(self):
        for reason in ("unavailable", "rate_limit", "exhausted"):
            fallback = {"reason": reason, "evidence": "Recorded provider error at 2026-10-10T16:00Z"}
            job = self.prepared(model_level=7, model_fallback=fallback)
            self.assertEqual((job["model_route"]["level"], job["model"], job["override"]["effort"]),
                             (7, "claude-sonnet-5-5", "high"))
            self.assertEqual(job["model_route"]["fallback"], fallback)
        for fallback in ({"reason": "unknown", "evidence": "No quota snapshot"},
                         {"reason": "exhausted", "evidence": ""},
                         {"reason": "slow", "evidence": "A difficult task"},
                         {"reason": "rate_limit", "evidence": "429", "level": 8}):
            with self.subTest(fallback=fallback), self.assertRaises(JobError):
                self.prepared(model_level=7, model_fallback=fallback)
        with self.assertRaises(JobError):
            self.prepared(model_fallback={"reason": "unavailable", "evidence": "Missing model"})
        with self.assertRaises(JobError):
            self.prepared(model_level=7, model_fallback={"reason": "rate_limit", "evidence": "429"},
                          override={"instruction": "Pinned effort", "effort": "max"})

    def test_jail_and_shopping_boundaries_are_preserved(self):
        self.job["task_kind"] = "deep_research"
        job = self.prepared()
        self.assertEqual(job["route"], {"mode": "jail", "runtime": "codex"})
        self.assertIsNone(job["model"])
        self.assertNotIn("override", job)
        with self.assertRaises(JobError):
            self.prepared(model_level=1)
        self.job["task_kind"] = "shopping"
        with self.assertRaises(JobError) as error:
            self.prepared(model_level=5)
        self.assertEqual(error.exception.kind, "decision_needed")

    def test_legacy_policy_without_ladder_keeps_its_contract(self):
        policy = json.loads(self.policy.read_text())
        policy.pop("model_ladder")
        self.policy.write_text(json.dumps(policy))
        with self.assertRaises(JobError) as error:
            self.prepared()
        self.assertEqual(error.exception.kind, "decision_needed")
        job = self.prepared(override={"instruction": "Legacy request", "runtime": "pi", "provider": "llama-local",
                                     "model": "qwen3.8-27b-uncensored", "effort": "high"})
        self.assertEqual(job["provider"], "llama-local")
        policy["routes"]["ordinary"]["runtime"] = "omp"
        self.policy.write_text(json.dumps(policy))
        self.assertEqual(self.prepared()["route"]["runtime"], "omp")

    def test_malformed_ladders_fail_offline_without_effects(self):
        original = self.csv.read_text()
        rows = original.splitlines()
        malformed = [original.replace("primary_model", "model", 1), "\n".join(rows[:-1]),
                     original + rows[1] + "\n", original.replace("1,gpt", "9,gpt", 1),
                     original.replace("low,codex", "ultra,codex", 1),
                     original.replace("gpt-6-luna", "", 1), rows[0] + "\n" + rows[1] + ",extra\n",
                     original.replace("gpt-6-luna", " gpt-6-luna", 1)]
        for contents in malformed:
            with self.subTest(contents=contents[:70]):
                self.csv.write_text(contents)
                with self.assertRaises(JobError):
                    self.prepared()
        self.csv.write_text(original.replace("pi,opencode go", "pi,unsupported", 1))
        with self.assertRaises(JobError) as error:
            self.prepared(model_level=5)
        self.assertEqual(error.exception.kind, "unavailable_capability")
        for level in (0, 9, True, "1", None):
            self.csv.write_text(original)
            with self.subTest(level=level), self.assertRaises(JobError):
                self.prepared(model_level=level)

    def test_installed_catalog_shapes_and_provider_disambiguation(self):
        self.assertEqual(catalog_models({"models": [{"id": "old"}]}, "codex"), [{"id": "old"}])
        fixture = self.fixture()
        spec = self.prepared(model_level=6)
        fixture.catalog = {"runtimes": [{"id": "codex", "status": "available", "models": [{"id": "gpt-6.1-sol"}]}]}
        result = fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(result["models"], {spec["job_id"]: "gpt-6.1-sol"})
        table = "provider model context max-out thinking images\nopencode-go mimo-v2.6-pro 1M 128K yes yes\nopenrouter mimo-v2.6-pro 1M 128K yes yes\n"
        fixture.adapter.read_terminal = lambda argv: table
        fixture.adapter.pi_model_efforts = lambda provider, model: ["low", "high", "max"]
        spec = self.prepared(model_level=5)
        result = fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(result["models"], {spec["job_id"]: "opencode-go/mimo-v2.6-pro"})
        with self.assertRaises(JobError):
            pi_catalog("unexpected native help")

    def test_pi_silent_effort_clamping_is_rejected_before_effects(self):
        fixture = self.fixture()
        fixture.adapter.read_terminal = lambda argv: "provider model context max-out thinking images\nopencode-go mimo-v2.6-pro 1M 128K yes yes\n"
        fixture.adapter.pi_model_efforts = lambda provider, model: ["off", "minimal", "low", "medium", "high"]
        with self.assertRaisesRegex(JobError, "native Pi would clamp"):
            fixture.adapter.preflight({"jobs": [self.prepared(model_level=5)]})
        self.assertFalse(any(call[1:3] in (["pane", "split"], ["agent", "start"]) for call in fixture.calls))

    def test_partial_missing_models_and_provider_gaps_precede_effects(self):
        fixture = self.fixture()
        for catalog in ({"runtimes": [{"id": "claude-code", "status": "partial", "models": [{"id": "opus"}]}]},
                        {"models": [{"id": "opus"}]}):
            fixture.catalog = catalog
            with self.assertRaises(JobError):
                fixture.adapter.preflight({"jobs": [self.prepared(model_level=8)]})
        fixture.calls.clear()
        os_config = self.root / "codex"
        os_config.mkdir()
        (os_config / "config.toml").write_text('[model_providers.opencode-go]\nwire_api="responses"\nbase_url="https://opencode.ai/zen/go/v1"\nenv_key="ROUTING_TEST_ABSENT"\n')
        with patch.dict(os.environ, {"CODEX_HOME": str(os_config)}, clear=True):
            with self.assertRaises(JobError) as error:
                fixture.adapter.preflight({"jobs": [self.prepared(model_level=1,
                    model_fallback={"reason": "rate_limit", "evidence": "Codex usage limit banner"})]})
        self.assertIn("no Pi substitution", str(error.exception))
        self.assertFalse(any(call[1:3] in (["pane", "split"], ["agent", "start"]) for call in fixture.calls))

    def test_supported_codex_go_uses_authenticated_provider_catalog(self):
        fixture = self.fixture()
        config_dir = self.root / "codex"
        config_dir.mkdir()
        config = config_dir / "config.toml"
        configured = '[model_providers.opencode-go]\nwire_api="responses"\nbase_url="https://opencode.ai/zen/go/v1"\nenv_key="ROUTING_TEST_CREDENTIAL"\n'
        config.write_text(configured)
        spec = self.prepared(model_level=2, model_fallback={"reason": "rate_limit", "evidence": "Pinned 429"})
        with patch.dict(os.environ, {"CODEX_HOME": str(config_dir), "ROUTING_TEST_CREDENTIAL": "placeholder"}):
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"data":[{"id":"gpt-6-luna"}]}')) as request:
                result = fixture.adapter.preflight({"jobs": [spec]})
            self.assertEqual(result["models"][spec["job_id"]], "gpt-6-luna")
            self.assertEqual(request.call_args.args[0].full_url, "https://opencode.ai/zen/go/v1/models")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer placeholder")
            limited = urllib.error.HTTPError("https://opencode.ai/zen/go/v1/models", 429, "Limited",
                                             {"Retry-After": "30"}, None)
            with patch("urllib.request.urlopen", side_effect=limited):
                with self.assertRaisesRegex(JobError, 'HTTP 429.*Retry-After.*30'):
                    fixture.adapter.preflight({"jobs": [spec]})
            config.write_text(configured.replace('"responses"', '"chat"'))
            with self.assertRaisesRegex(JobError, "Responses adapter"):
                fixture.adapter.preflight({"jobs": [spec]})
        self.assertFalse(any(call[1:3] in (["pane", "split"], ["agent", "start"]) for call in fixture.calls))

    @unittest.skipUnless(shutil.which("node"), "Node is required for the native Pi metadata probe")
    def test_native_pi_probe_reads_metadata_without_creating_a_session(self):
        module = self.root / "fixture-sdk.mjs"
        module.write_text('export function getModel(provider,id){return {provider,id};}\n'
                          'export function getSupportedThinkingLevels(model){return ["off","low","medium","high"]; }\n')
        before = set(self.root.iterdir())
        result = subprocess.run(["node", str(PACKAGE / "scripts/pi-model-capabilities.mjs"),
                                 module.as_uri(), "opencode-go", "mimo-v2.6-pro"],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("max", json.loads(result.stdout)["efforts"])
        self.assertEqual(set(self.root.iterdir()), before)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the native Pi metadata probe")
    def test_native_pi_probe_uses_effective_configured_effort_metadata(self):
        module = self.root / "fixture-sdk.mjs"
        module.write_text('export function getModel(){return {efforts:["high"]};}\n'
                          'export function getSupportedThinkingLevels(model){return model.efforts;}\n')
        core = self.root / "dist/core"
        core.mkdir(parents=True)
        (self.root / "package.json").write_text('{"type":"module"}')
        (core / "model-config.js").write_text('import {readFile} from "node:fs/promises";\n'
            'export class ModelConfig{static async load(path){return JSON.parse(await readFile(path,"utf8"));}}\n')
        (core / "provider-composer.js").write_text('export function composeModelProvider(provider,base,config){\n'
            'return {getModels(){return [{id:"mimo-v2.6-pro",efforts:config.efforts}];}};}\n')
        providers = self.root / "providers"
        providers.mkdir()
        (providers / "all.js").write_text('export function builtinProviders(){return [{id:"opencode-go"}];}\n')
        models_path = self.root / "models.json"
        models_path.write_text('{"efforts":["high","max"]}')
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run(["node", str(PACKAGE / "scripts/pi-model-capabilities.mjs"), module.as_uri(),
                                 "opencode-go", "mimo-v2.6-pro", self.root.as_uri() + "/", str(models_path)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("max", json.loads(result.stdout)["efforts"])
        after = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_csv_change_is_pinned_and_preview_has_no_writes(self):
        first = self.prepared(model_level=1)
        self.csv.write_text(self.csv.read_text().replace("gpt-6-luna,low", "gpt-6-luna,medium", 1))
        second = self.prepared(model_level=1)
        self.assertNotEqual(first["model_route"]["ladder_sha256"], second["model_route"]["ladder_sha256"])
        run_dir = self.root / "never-created"
        result = subprocess.run([sys.executable, "-B", str(PACKAGE / "scripts/herdr-jobs.py"), "run",
                                 "--manifest", str(self.manifest), "--policy", str(self.policy),
                                 "--run-dir", str(run_dir), "--preview"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(run_dir.exists())
        preview = json.loads(result.stdout)["jobs"][0]
        self.assertEqual((preview["provider"], preview["effort"]), ("codex", "medium"))

    def test_billing_aliases_share_caps_and_backoff_and_migrate_old_state(self):
        store = AdmissionStore(self.root / "admission", clock=lambda: 1000.0)
        self.assertTrue(store.acquire("codex", "gpt-6-luna", "first", 1)["admitted"])
        self.assertFalse(store.acquire("openai-codex", "gpt-6-astra", "second", 1)["admitted"])
        self.assertTrue(store.acquire("opencode go", "gpt-6-luna", "third", 1)["admitted"])
        self.assertTrue(store.acquire("claude-code", "claude-opus-5-5", "fourth", 1)["admitted"])
        store.note_rate_limit("openai-codex", "gpt-6-luna", 60, "reset")
        self.assertFalse(store.acquire("codex", "gpt-6-luna", "blocked", 4)["admitted"])
        state = json.loads(store.path.read_text())
        state["leases"][0]["provider"] = "openai-codex"
        state["backoffs"].append({**state["backoffs"][0], "provider": "openai-codex", "until": 1100.0})
        store.path.write_text(json.dumps(state))
        self.assertEqual(store.status("codex")["active_count"], 1)
        self.assertEqual(store.status("codex")["backoffs"][0]["until"], 1100.0)
        policy = json.loads(self.policy.read_text())
        policy["provider_admission"]["provider_caps"].update({"openai-codex": 2, "codex": 8})
        self.assertEqual(policy_record(policy)["provider_admission"]["provider_caps"]["codex"], 2)

    def test_pool_reset_and_qualified_model_keys_share_backoff(self):
        current = [1000.0]
        store = AdmissionStore(self.root / "pool-reset", clock=lambda: current[0])
        store.note_rate_limit("opencode go", "opencode-go/gpt-6-luna", 10, "retry-after")
        self.assertFalse(store.acquire("opencode-go", "gpt-6-luna", "same", 4)["admitted"])
        store.note_rate_limit("openai-codex", "*", 60, "reset")
        self.assertFalse(store.acquire("codex", "gpt-6-astra", "other", 4)["admitted"])
        self.assertTrue(store.acquire("claude", "claude-opus-5-5", "independent", 4)["admitted"])
        self.assertEqual(store.status("codex", "gpt-6-astra")["backoffs"][0]["model"], "*")
        current[0] = 1060.0
        self.assertTrue(store.acquire("codex", "gpt-6-astra", "resumed", 4)["admitted"])


if __name__ == "__main__":
    unittest.main()
