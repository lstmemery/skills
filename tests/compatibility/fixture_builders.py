"""Pure fixture builders from checked-in goldens, without paired-repo discovery."""

import copy
import json
from pathlib import Path


FIXTURES = Path(__file__).with_name("fixtures")


def golden(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_files(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def review_evidence(*, schema_version=1, task_id="task-1", capture_id="fixture-capture",
                    author="coder-1", reviewer="reviewer-1", standards=(), spec=()):
    evidence = golden("review")["empty"]
    evidence.update(schema_version=schema_version, task_id=task_id, capture_id=capture_id,
                    author_identity=author, reviewer_identity=reviewer)
    evidence["axes"]["standards"]["findings"] = copy.deepcopy(list(standards))
    evidence["axes"]["spec"]["findings"] = copy.deepcopy(list(spec))
    return evidence


def capture_bundle(directory):
    fixture = golden("capture-v1")
    write_files(directory, fixture["files"])
    write_json(directory / "manifest.json", fixture["manifest"])
    return copy.deepcopy(fixture["manifest"])


def review_bundle(directory, case="v1-bare"):
    """Materialize a complete bundle usable by both sync and preflight."""
    fixture = golden("review")
    selected = fixture["cases"][case]
    review = directory / "task-1/review"
    manifest = capture_bundle(review / "capture")
    evidence = review_evidence(
        schema_version=selected["schema_version"], capture_id=manifest["capture_id"],
        standards=selected["standards"], spec=selected["spec"],
    )
    write_json(review / "review-evidence.json", evidence)
    write_json(review / "done.json", {
        "task_id": "task-1", "capture_id": manifest["capture_id"],
        "standards_findings": len(selected["standards"]), "spec_findings": len(selected["spec"]),
    })
    write_files(review, {"review.md": selected["markdown"]})
    write_files(directory / "task-1", {"review-response.md": selected["response"]})
    write_files(directory, {"workers.txt": fixture["workers"]})
    return review, evidence
