import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.admission import AdmissionStore
from herdr_jobs.model_ladder import load_ladder, native_arguments, provider_arguments, select
from herdr_jobs.records import JobError, policy_record, prepare
from herdr_jobs.transport import catalog_models, pi_catalog
import test_transport


EXPECTED = [
    (("gpt-6-luna", "low", "codex", "codex"), ("gpt-6-luna", "low", "pi", "opencode go")),
    (("gpt-6-luna", "medium", "codex", "codex"), ("gpt-6-luna", "medium", "pi", "opencode go")),
    (("gpt-6-luna", "xhigh", "codex", "codex"), ("gpt-6-luna", "xhigh", "pi", "opencode go")),
    (("gpt-6-luna", "max", "codex", "codex"), ("gpt-6-luna", "max", "pi", "opencode go")),
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

    @staticmethod
    def fallback_evidence(reason):
        if reason == "exhausted":
            return {"source": "quota-api", "observed_at": "2026-10-10T16:00:00Z",
                    "quota": {"remaining": 0}}
        return {"source": "provider", "observed_at": "2026-10-10T16:00:00Z",
                "error": {"kind": reason, "message": "observed provider response", "code": "fixture"}}

    @staticmethod
    def escalation(level):
        return {"previous_level": level - 1,
                "failure": {"kind": "failure", "source": "fixture-run",
                             "observed_at": "2026-10-10T15:59:00Z", "summary": "lower level failed"},
                "expected_improvement": "complete the assigned task"}

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
                    fallback = {"reason": "unavailable", "evidence": self.fallback_evidence("unavailable")} if index else None
                    selection = select(levels, level, fallback)
                    self.assertEqual(tuple(selection["requested"].values()), expected)
                    model, effort, harness, provider = expected
                    runtime = "claude-code" if harness == "claude" else harness
                    native_provider = "opencode-go" if provider == "opencode go" else provider
                    self.assertEqual((selection["runtime"], selection["provider"]), (runtime, native_provider))
                    arguments = {"model_level": level}
                    if level > 1:
                        arguments["escalation"] = self.escalation(level)
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
                    if provider == "opencode go":
                        self.assertEqual(spec["kind"], "pi")
                        self.assertIn(["--provider", "opencode-go"],
                                      [actual[i:i + 2] for i in range(len(actual) - 1)])

    def test_default_is_cheapest_and_explicit_requests_win_over_level(self):
        default = self.prepared()
        self.assertEqual((default["model_route"]["level"], default["model"], default["override"]["effort"]),
                         (1, "gpt-6-luna", "low"))
        explicit = self.prepared(model_level=8, override={"instruction": "User pinned", "runtime": "codex",
                                 "model": "gpt-6.1-sol", "effort": "high", "provider": "codex"})
        self.assertNotIn("model_route", explicit)
        self.assertEqual((explicit["route"]["runtime"], explicit["model"], explicit["override"]["effort"]),
                         ("codex", "gpt-6.1-sol", "high"))
        with self.assertRaises(JobError):
            self.prepared(override={"instruction": "Named harness", "runtime": "omp"})
        effort_only = self.prepared(override={"instruction": "Pinned effort", "effort": "max"})
        self.assertEqual((effort_only["model"], effort_only["provider"], effort_only["override"]["effort"]),
                         ("gpt-6-luna", "codex", "max"))
        instruction = self.prepared(override={"instruction": "Preserve the user's scope"})
        self.assertEqual(instruction["override"]["instruction"], "Preserve the user's scope")
        with self.assertRaises(JobError):
            self.prepared(override={"instruction": ""})

    def test_explicit_opencode_go_route_requires_pi_before_preflight(self):
        with self.assertRaisesRegex(JobError, "OpenCode Go requires the Pi harness"):
            self.prepared(override={"instruction": "Pinned route", "runtime": "codex",
                                   "model": "gpt-6-luna", "provider": "opencode go"})
        with self.assertRaisesRegex(JobError, "OpenCode Go requires the Pi harness"):
            provider_arguments("codex", "opencode-go")

    def test_preflight_rejects_opencode_go_codex_spec_before_host_discovery(self):
        fixture = self.fixture()
        spec = self.prepared(model_level=1, model_fallback={"reason": "rate_limit",
                          "evidence": self.fallback_evidence("rate_limit")})
        spec["route"]["runtime"] = "codex"
        spec["kind"] = "codex"
        with self.assertRaisesRegex(JobError, "OpenCode Go requires the Pi harness"):
            fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(fixture.calls, [])

    def test_unverified_codex_custom_provider_is_rejected_before_host_discovery(self):
        fixture = self.fixture()
        # P2: exercise custom-provider discovery with a complete explicit route.
        spec = self.prepared(override={"instruction": "Pinned provider", "runtime": "codex",
                                       "model": "gpt-6-luna", "provider": "custom-provider",
                                       "effort": "low"})
        with self.assertRaisesRegex(JobError, "Codex custom provider model discovery is not verified"):
            fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(fixture.calls, [])

    def test_effect_refuses_opencode_go_codex_route_before_any_action(self):
        fixture = self.fixture()
        spec = self.prepared(model_level=1, model_fallback={"reason": "rate_limit",
                          "evidence": self.fallback_evidence("rate_limit")})
        spec["route"]["runtime"] = "codex"
        job = {"spec": spec, "pane_id": "old:1", "agent_name": "route-test", "resolved_model": None}
        fake = test_transport.FakeHerdr()
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaisesRegex(JobError, "OpenCode Go requires the Pi harness"):
                fixture.adapter.effect("split", job, "")
        self.assertEqual(fake.calls, [])

    def test_same_level_fallback_requires_concrete_reason_and_retains_evidence(self):
        for reason in ("unavailable", "rate_limit", "exhausted"):
            fallback = {"reason": reason, "evidence": self.fallback_evidence(reason)}
            job = self.prepared(model_level=7, escalation=self.escalation(7), model_fallback=fallback)
            self.assertEqual((job["model_route"]["level"], job["model"], job["override"]["effort"]),
                             (7, "claude-sonnet-5-5", "high"))
            self.assertEqual(job["model_route"]["fallback"], fallback)
        for fallback in ({"reason": "unknown", "evidence": "No quota snapshot"},
                         {"reason": "exhausted", "evidence": ""},
                         {"reason": "slow", "evidence": "A difficult task"},
                         {"reason": "rate_limit", "evidence": "429", "level": 8}):
            with self.subTest(fallback=fallback), self.assertRaises(JobError):
                self.prepared(model_level=7, escalation=self.escalation(7), model_fallback=fallback)
        with self.assertRaises(JobError):
            self.prepared(model_fallback={"reason": "unavailable", "evidence": "Missing model"})
        with self.assertRaises(JobError):
            self.prepared(model_level=7, escalation=self.escalation(7),
                          model_fallback={"reason": "rate_limit", "evidence": {"source": "fixture",
                              "observed_at": "2026-10-10T16:00:00Z",
                              "error": {"kind": "rate_limit", "message": "429"}}},
                          override={"instruction": "Pinned effort", "effort": "max"})

    def test_jail_and_shopping_boundaries_are_preserved(self):
        self.job["task_kind"] = "deep_research"
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        self.job["required_context"] = [
            {"path": "acceptance_test.md", "kind": "test", "purpose": "Acceptance test."},
            {"path": "fixture-input.json", "kind": "fixture", "purpose": "Input fixture."},
        ]
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
                     original.replace("gpt-6-luna", " gpt-6-luna", 1),
                     original.replace("low,pi,opencode go", "low,codex,opencode go", 1)]
        for contents in malformed:
            with self.subTest(contents=contents[:70]):
                self.csv.write_text(contents)
                with self.assertRaises(JobError):
                    self.prepared()
        self.csv.write_text(original.replace("mimo-v2.6-pro,max,pi,opencode go",
                                             "mimo-v2.6-pro,max,pi,unsupported", 1))
        # P8: this level selection now records the required escalation evidence.
        with self.assertRaises(JobError) as error:
            self.prepared(model_level=5, escalation=self.escalation(5))
        self.assertEqual(error.exception.kind, "unavailable_capability")
        for level in (0, 9, True, "1", None):
            self.csv.write_text(original)
            with self.subTest(level=level), self.assertRaises(JobError):
                self.prepared(model_level=level)

    def test_installed_catalog_shapes_and_provider_disambiguation(self):
        self.assertEqual(catalog_models({"models": [{"id": "old"}]}, "codex"), [{"id": "old"}])
        fixture = self.fixture()
        spec = self.prepared(model_level=6, escalation=self.escalation(6))
        fixture.catalog = {"runtimes": [{"id": "codex", "status": "available", "models": [{"id": "gpt-6.1-sol"}]}]}
        result = fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(result["models"], {spec["job_id"]: "gpt-6.1-sol"})
        table = "provider model context max-out thinking images\nopencode-go mimo-v2.6-pro 1M 128K yes yes\nopenrouter mimo-v2.6-pro 1M 128K yes yes\n"
        fixture.adapter.read_terminal = lambda argv: table
        fixture.adapter.pi_model_efforts = lambda provider, model: ["low", "high", "max"]
        spec = self.prepared(model_level=5, escalation=self.escalation(5))
        result = fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(result["models"], {spec["job_id"]: "opencode-go/mimo-v2.6-pro"})
        with self.assertRaises(JobError):
            pi_catalog("unexpected native help")

    def test_pi_silent_effort_clamping_is_rejected_before_effects(self):
        fixture = self.fixture()
        fixture.adapter.read_terminal = lambda argv: "provider model context max-out thinking images\nopencode-go mimo-v2.6-pro 1M 128K yes yes\n"
        fixture.adapter.pi_model_efforts = lambda provider, model: ["off", "minimal", "low", "medium", "high"]
        with self.assertRaisesRegex(JobError, "native Pi would clamp"):
            fixture.adapter.preflight({"jobs": [self.prepared(model_level=5, escalation=self.escalation(5))]})
        self.assertFalse(any(call[1:3] in (["pane", "split"], ["agent", "start"]) for call in fixture.calls))

    def test_partial_catalogs_and_exact_model_gaps_precede_effects(self):
        fixture = self.fixture()
        for catalog in ({"runtimes": [{"id": "claude-code", "status": "partial", "models": [{"id": "opus"}]}]},
                        {"models": [{"id": "opus"}]}):
            fixture.catalog = catalog
            with self.assertRaises(JobError):
                        fixture.adapter.preflight({"jobs": [self.prepared(model_level=8, escalation=self.escalation(8))]})
        self.assertFalse(any(call[1:3] in (["pane", "split"], ["agent", "start"]) for call in fixture.calls))

    def test_opencode_go_fallback_uses_pi_offline_catalog_and_effort_metadata(self):
        fixture = self.fixture()
        table = ("provider model context max-out thinking images\n"
                 "opencode-go gpt-6-luna 1M 128K yes yes\n"
                 "openrouter gpt-6-luna 1M 128K yes yes\n")
        discovered = []
        fixture.adapter.read_terminal = lambda argv: discovered.append(argv) or table
        fixture.adapter.pi_model_efforts = lambda provider, model: ["off", "low", "medium", "high", "xhigh", "max"]
        spec = self.prepared(model_level=4, escalation=self.escalation(4),
                             model_fallback={"reason": "rate_limit", "evidence": self.fallback_evidence("rate_limit")})
        result = fixture.adapter.preflight({"jobs": [spec]})
        self.assertEqual(result["models"][spec["job_id"]], "opencode-go/gpt-6-luna")
        self.assertEqual(discovered, [["pi", "--offline", "--list-models"]])
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
