"""ICS, event JSON, classifier grid, and plan validation share one corpus."""
import copy
from datetime import date, time
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import ROOT, calendar_block, golden, load_module


CALENDAR = load_module("compat_calendar", "skills-local/nextcloud-calendar/nextcloud_calendar.py")
CLASSIFIER = load_module("compat_classifier", "skills-local/meal-plan/scripts/calendar_days.py")
CHECKER = load_module("compat_checker", "skills-local/meal-plan/scripts/check_plan.py")
PLAN_HELPERS = load_module("compat_plan_helpers", "skills-local/meal-plan/tests/test_check_plan.py")


class CalendarCompatibilityTests(unittest.TestCase):
    def test_ics_events_and_grids_match_goldens_through_both_consumers(self):
        for case in golden("calendar")["cases"]:
            with self.subTest(case=case["name"]):
                events = [CALENDAR.event_from_ics(ics) for ics in case["ics"]]
                self.assertEqual(events, case["events"])
                grid = CLASSIFIER.classify(
                    date.fromisoformat(case["week_of"]), events,
                    case["away"], case["trip"], case["booked"],
                    "America/Vancouver", time(17), time(22),
                )
                self.assertEqual(grid, case["grid"])
                plan = PLAN_HELPERS.make_plan(case["week_of"])
                plan["calendar"] = calendar_block(grid)
                free_days = [day for day in grid["days"] if day["evening"] == "free"]
                plan["dinners"] = [dict(dinner, day=day["day"])
                                   for dinner, day in zip(plan["dinners"], free_days)]
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    (root / "groceries.md").write_text("# Groceries\n\n## Produce\n\n- [ ] Rice\n")
                    knobs, _knob_warnings = CHECKER.parse_knobs(PLAN_HELPERS.PREFERENCES)
                    violations, _warnings = CHECKER.validate(
                        plan, knobs,
                        {"rows": [], "rotation": [], "rotation_asof": None}, root,
                    )
                self.assertEqual(violations, [])

    def test_classifier_cli_accepts_event_list_and_calendar_response(self):
        script = ROOT / "skills-local/meal-plan/scripts/calendar_days.py"
        for case in golden("calendar")["cases"]:
            for envelope in (case["events"], {"ok": True, "events": case["events"]}):
                with self.subTest(case=case["name"], envelope=isinstance(envelope, dict)):
                    args = [sys.executable, str(script), "--week-of", case["week_of"], "--events", "-"]
                    for option, key in (("--away", "away"), ("--away-trip", "trip"), ("--booked", "booked")):
                        for pattern in case[key]:
                            args.extend([option, pattern])
                    result = subprocess.run(args, input=json.dumps(envelope), capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), case["grid"])
                    self.assertEqual(result.stderr, "".join("WARNING: " + warning + "\n" for warning in case["grid"]["warnings"]))

    def test_plan_rejects_misaligned_grid_or_missing_blocked_day_evidence(self):
        fixture = golden("calendar")["cases"][1]
        for mutation in ("order", "evidence", "count"):
            with self.subTest(case=mutation):
                grid = copy.deepcopy(fixture["grid"])
                block = calendar_block(grid)
                if mutation == "order":
                    block["days"][0], block["days"][1] = block["days"][1], block["days"][0]
                elif mutation == "evidence":
                    block["days"][1]["evidence"] = []
                else:
                    block["days"].pop()
                _info, violations, _warnings = CHECKER.validate_calendar(
                    {"calendar": block}, date.fromisoformat(fixture["week_of"]), {}, 0,
                )
                self.assertTrue(violations)


if __name__ == "__main__":
    unittest.main()
