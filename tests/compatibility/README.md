# Durable format compatibility fixtures

Run from the combined repository tree:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/tests/compatibility -p 'test*.py' -v
```

These fixtures pin the behavior supplied for ticket #1240. Production code is
unchanged. The corpus is shared by tests of HN/ML runners, delivery/reverification,
capture, disposition synchronization/preflight, and calendar classification/plan
validation. The three orchestrator review suites also use `review_evidence` or
`review_bundle`; the existing recovery suite uses `pending_archive`.

| Fixture | Durable contract and public consumers |
| --- | --- |
| `daily.json` | Exact task-launched, task-succeeded, task-metadata and route-used text, malformed markers, and route CLI output for both real daily runners. Synthetic config is test input, not a deployment config. |
| `delivery.json` | Message-ID and emailed marker bytes, legacy markers, delivered/submitted/failed JSON and exit codes. Consumed by `deliver`, `deliver_report`, `reverify_pending`, and ML's receipt fast path. |
| `capture-v1.json` | Complete sealed manifest, blobs, diff, history, completion marker and verification result. Read through capture CLI verify/read; compared against CLI capture output. |
| `review.json` | Complete v1/v2 sidecars, both Markdown axes, response lines, identity roster, and invalid evidence cases. Passed through `sync_dispositions` and `review_records`, including the real capture verifier. |
| `calendar.json` | Fictional ICS, exact event JSON and seven-day grids. Passed through `event_from_ics`, `classify`, calendar-days CLI, and `check_plan.validate`. Pins UTC all-day dates, both DST transitions, exclusive ends, dinner boundaries, away precedence and evidence text. |
| `state-v1.json` | Current and legacy v1 checkpoints, expected public result, and refusal cases for unsupported versions and inconsistent identity. Intended for `Engine.load`, `result`, and `checkpoint`; see the verification limitation below. |

`builders.py` loads fresh copies and materializes isolated inputs. It does not
call a production parser to manufacture expectations. Tests compare complete
JSON objects and exact marker strings, including final newlines. Existing fake
SMTP/IMAP classes and the complete meal-plan builder are reused from their
bundled test modules.

The corpus needs the supplied combined tree (`skills/` plus `skills-local/`).
Within the skills repository, tests import it from `tests/compatibility`.
Keep that directory with consumer tests when running them in another checkout;
there is no new production import or deployment dependency.

Capture fixtures came from a fictional two-commit history with fixed author,
committer and dates. Only the repository location and command arguments naming
that location are normalized to `fixture/repository`. The sealed fixture has its
own valid capture ID. Writer comparison permits the location-dependent ID to
differ only after CLI verification; every other manifest field and payload byte
is compared. No content digest is masked. Receipt tests bind the wall-clock
timestamp; state tests allow only the checkpoint time and the legacy admission
lease identity to vary.

Calendar fixtures were recorded once from the supplied public APIs and reviewed
against the expected dates: Tuesday all-day storage stays Tuesday, DST trips
cover Saturday/Sunday, and a Wednesday-midnight retreat ends on Tuesday. The
historical 2025 transitions avoid assuming future timezone law. Review and
marker fixtures are literal examples of the supplied formats. State fixtures
were constructed from the supplied engine schema and have **not** been executed
against a complete engine: this input omits `herdr_jobs.admission` and other
engine dependencies. The two state tests explicitly skip for those missing
modules. A passing run with those skips does not establish full #1240 acceptance.

Do not regenerate goldens during tests. When an intentional protocol change is
approved, retain the old reader fixture, add a separately named version, update
the expected public behavior, and review the fixture diff. Do not add a schema
field to an unversioned format merely to make the fixture look versioned.
