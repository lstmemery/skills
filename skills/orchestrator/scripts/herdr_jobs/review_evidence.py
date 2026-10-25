"""Review bundle API v1: schema, consistency, and disposition rules.

review_dispositions.py (sync) and integration-preflight.py (gate) share finding
validation here. Sync can update staged sidecars; integration additionally
requires the complete independent review bundle. Amend the contract only here, and update
skills/orchestrator/references/review.md — the prose owner — in the same change.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Protocol

from . import records


class ReviewEvidenceError(ValueError):
    """Review evidence cannot establish a complete independent review."""


class LocalReviewFiles:
    """Filesystem boundary; sync retains its bounded, no-symlink reads."""

    def read(self, path):
        return path.read_bytes()

    def read_regular(self, path):
        return records.read_regular(path)

    def is_file(self, path):
        return path.is_file()

    def review_directories(self, task_dir):
        return sorted(
            path for path in task_dir.iterdir()
            if path.is_dir() and (path.name == "review" or path.name.startswith("review-"))
        )


@dataclass(frozen=True)
class VerifiedCapture:
    base: str
    head: str
    capture_id: str
    manifest_sha256: str
    complete_sha256: str


class CaptureVerifier(Protocol):
    def verify(self, review_dir: Path, capture_id: str) -> VerifiedCapture:
        """Return verified complete coverage, or raise ReviewEvidenceError."""
        ...


# The caller must establish the exact historical precedent. There is no
# permissive Markdown mode: only a failed strict parse can consult this policy.
LegacyPrecedent = Callable[[dict], str | None]


AXES = ("standards", "spec")
EVIDENCE_NAME = "review-evidence.json"
IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
MARKDOWN_FINDING = re.compile(r"^\s*-\s+\[(?P<finding>[A-Za-z0-9._-]+)\]\s+\S.*$")
MARKDOWN_LIST_ENTRY = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S.*$")
MARKDOWN_SUMMARY = re.compile(
    r"^Summary: findings=(?P<count>0|[1-9][0-9]*); worst=(?P<worst>.+)\.$"
)
NO_FINDINGS_LINE = "No findings."


FINDING_LINE = re.compile(
    r"^\s*-\s*`(?P<review>[^`]+)/(?P<finding>[^`]+)`\s*:\s*"
    r"(?P<disposition>fixed|rejected)\s+—\s+(?P<detail>\S.*)$"
)
FINDING_REFERENCE_LINE = re.compile(r"^\s*-\s*`[^`]+/[^`]+`")


def parse_review_response(text, *, reject_malformed=False):
    """Parse response dispositions, optionally rejecting malformed references.

    Preflight historically ignores malformed response lines; disposition sync
    rejects lines that look like finding references but do not match the grammar.
    """
    dispositions = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = FINDING_LINE.match(line)
        if match is None:
            if reject_malformed and FINDING_REFERENCE_LINE.match(line):
                raise ReviewEvidenceError(f"malformed review disposition at review-response.md:{line_number}")
            continue
        key = (match.group("review"), match.group("finding"))
        if key in dispositions:
            raise ReviewEvidenceError(f"duplicate disposition for {key[0]}/{key[1]}")
        dispositions[key] = (match.group("disposition"), match.group("detail").strip())
    return dispositions


FINDING_EVIDENCE_FIELDS = frozenset(
    {"confidence", "reproducer", "evidence", "unresolved_assumption"}
)
FINDING_CONFIDENCE = frozenset({"high", "medium", "low"})
REVIEW_EVIDENCE_SCHEMA_VERSIONS = frozenset({1, 2})


def finding_evidence_error(finding, review_name, schema_version):
    """Return the contract violation for a finding as a message, or None."""
    has_metadata = bool(finding.keys() & FINDING_EVIDENCE_FIELDS)
    finding_id = finding["id"]
    if schema_version == 2 and not has_metadata:
        return f"{review_name}/{finding_id} schema version 2 finding requires evidence metadata"
    if not has_metadata:
        return None

    confidence = finding.get("confidence")
    reproducer = finding.get("reproducer")
    evidence = finding.get("evidence")
    assumption = finding.get("unresolved_assumption")
    if type(confidence) is not str or confidence not in FINDING_CONFIDENCE:
        return f"{review_name}/{finding_id} has invalid finding confidence"
    if reproducer is not None and (
        not isinstance(reproducer, str) or not reproducer.strip()
    ):
        return f"{review_name}/{finding_id} has an invalid reproducer"
    if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
        return f"{review_name}/{finding_id} has invalid finding evidence"
    if assumption is not None and (
        not isinstance(assumption, str) or not assumption.strip()
    ):
        return f"{review_name}/{finding_id} has an invalid unresolved assumption"
    has_reproducer = isinstance(reproducer, str) and bool(reproducer.strip())
    has_unresolved_evidence = (
        isinstance(evidence, str)
        and bool(evidence.strip())
        and isinstance(assumption, str)
        and bool(assumption.strip())
    )
    if not (has_reproducer or has_unresolved_evidence):
        return f"{review_name}/{finding_id} needs a reproducer or evidence and unresolved assumption"
    return None


def _read_markdown(review_dir, filesystem):
    try:
        raw = filesystem.read(review_dir / "review.md")
        return raw, raw.decode("utf-8")
    except FileNotFoundError as error:
        raise ReviewEvidenceError(f"missing review.md in {review_dir.name}") from error
    except (OSError, UnicodeError) as error:
        raise ReviewEvidenceError(f"cannot read review.md in {review_dir.name}") from error


def parse_review_markdown(review_dir, *, filesystem=None):
    """Compatibility entry point for the strict Markdown contract."""
    filesystem = LocalReviewFiles() if filesystem is None else filesystem
    _raw, text = _read_markdown(review_dir, filesystem)
    return review_dir / "review.md", parse_review_markdown_text(text, review_dir.name)


def parse_review_markdown_text(text, review_name):
    """Parse strict review prose; historical exceptions never enter this parser."""
    findings = {axis: set() for axis in AXES}
    found_axes = set()
    no_findings = set()
    summaries = {}
    current_axis = None
    title_seen = False
    for line in text.splitlines():
        content = line.strip()
        if not content:
            continue
        heading = re.match(r"^##\s+(.+?)\s*#*\s*$", content)
        if heading:
            title = heading.group(1).strip().lower()
            if title not in AXES:
                raise ReviewEvidenceError(
                    f"{review_name}/review.md has an unsupported level-two heading; "
                    "only Standards and Spec are allowed"
                )
            current_axis = title
            if current_axis in found_axes:
                raise ReviewEvidenceError(
                    f"{review_name}/review.md repeats the {current_axis.title()} section"
                )
            found_axes.add(current_axis)
            continue
        if current_axis is None:
            if not found_axes and not title_seen and re.fullmatch(r"#\s+\S.*", content):
                title_seen = True
                continue
            raise ReviewEvidenceError(
                f"{review_name}/review.md has unrecognized content outside its axis sections"
            )
        if current_axis in summaries:
            raise ReviewEvidenceError(
                f"{review_name}/review.md summary must be the last nonblank line in "
                f"the {current_axis.title()} section"
            )
        finding = MARKDOWN_FINDING.fullmatch(content)
        if finding is None:
            if content == NO_FINDINGS_LINE:
                if current_axis in no_findings or findings[current_axis]:
                    raise ReviewEvidenceError(
                        f"{review_name}/review.md has an inconsistent No findings. line"
                    )
                no_findings.add(current_axis)
                continue
            summary = MARKDOWN_SUMMARY.fullmatch(content)
            if summary is not None:
                count = int(summary.group("count"))
                worst = summary.group("worst").strip()
                actual_count = len(findings[current_axis])
                if not worst:
                    raise ReviewEvidenceError(
                        f"{review_name}/review.md {current_axis.title()} summary has no worst-issue value"
                    )
                if count != actual_count:
                    raise ReviewEvidenceError(
                        f"{review_name}/review.md {current_axis.title()} summary count "
                        "does not match Markdown findings"
                    )
                if count == 0 and (current_axis not in no_findings or worst != "none"):
                    raise ReviewEvidenceError(
                        f"{review_name}/review.md empty {current_axis.title()} summary "
                        "requires No findings. and worst=none"
                    )
                if count > 0 and (current_axis in no_findings or worst.lower() == "none"):
                    raise ReviewEvidenceError(
                        f"{review_name}/review.md nonempty {current_axis.title()} summary "
                        "requires a worst-issue description"
                    )
                summaries[current_axis] = count
                continue
            if MARKDOWN_LIST_ENTRY.fullmatch(content):
                raise ReviewEvidenceError(
                    f"{review_name}/review.md finding entry must use the new "
                    "'- [ID] <finding>' format"
                )
            raise ReviewEvidenceError(
                f"{review_name}/review.md has an unrecognized nonblank line in "
                f"the {current_axis.title()} section"
            )
        finding_id = finding.group("finding")
        if current_axis in no_findings:
            raise ReviewEvidenceError(
                f"{review_name}/review.md has a finding after No findings."
            )
        if any(finding_id in ids for ids in findings.values()):
            raise ReviewEvidenceError(f"{review_name}/review.md repeats finding ID {finding_id}")
        findings[current_axis].add(finding_id)
    for axis in AXES:
        if axis not in found_axes:
            raise ReviewEvidenceError(f"{review_name}/review.md lacks a {axis.title()} section")
        if axis not in summaries:
            raise ReviewEvidenceError(f"{review_name}/review.md lacks a {axis.title()} summary line")
        if not findings[axis] and axis not in no_findings:
            raise ReviewEvidenceError(f"{review_name}/review.md lacks a {axis.title()} No findings. line")
    return findings


def _read_json(filesystem, path, description):
    try:
        raw = filesystem.read(path)
        value = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as error:
        raise ReviewEvidenceError(f"missing {description}: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewEvidenceError(f"cannot read {description}: {path}") from error
    if not isinstance(value, dict):
        raise ReviewEvidenceError(f"{description} must be a JSON object: {path}")
    return raw, value


def _read_review_evidence(filesystem, path, *, regular=False):
    """Read review evidence with the same strict JSON rules for every consumer."""
    try:
        raw = filesystem.read_regular(path) if regular else filesystem.read(path)
    except FileNotFoundError as error:
        raise ReviewEvidenceError(f"missing {EVIDENCE_NAME} at {path}") from error
    except OSError as error:
        raise ReviewEvidenceError(f"cannot read {EVIDENCE_NAME} at {path}: {error}") from error
    try:
        evidence = records.parse_json(raw)
    except records.JobError as error:
        raise ReviewEvidenceError(f"invalid {EVIDENCE_NAME} at {path}: {error}") from error
    if not isinstance(evidence, dict):
        raise ReviewEvidenceError(f"{EVIDENCE_NAME} must be a JSON object: {path}")
    return raw, evidence


_DISPOSITION_STAGE = object()


def _validate_evidence(evidence, review_name, *, expected_task=_DISPOSITION_STAGE):
    """One sidecar contract, with the existing sync staging boundary explicit.

    Sync historically accepts incomplete envelope fields and axis extensions;
    integration requires the full envelope. Both use identical finding schema
    and metadata rules. Pending dispositions are replaced before integration.
    """
    complete_bundle = expected_task is not _DISPOSITION_STAGE
    required = {"schema_version", "task_id", "author_identity", "reviewer_identity", "capture_id", "axes"}
    if complete_bundle and (not isinstance(evidence, dict)
                            or not required <= evidence.keys()
                            or evidence.keys() - required - {"axis_reviewers"}):
        raise ReviewEvidenceError(f"{review_name} review evidence has unexpected fields")
    if (
        not isinstance(evidence, dict)
        or type(evidence.get("schema_version")) is not int
        or evidence["schema_version"] not in REVIEW_EVIDENCE_SCHEMA_VERSIONS
    ):
        if complete_bundle:
            raise ReviewEvidenceError(f"{review_name} review evidence has the wrong schema or task ID")
        raise ReviewEvidenceError(f"invalid {EVIDENCE_NAME} schema in {review_name}")
    if complete_bundle and str(evidence["task_id"]) != str(expected_task):
        raise ReviewEvidenceError(f"{review_name} review evidence has the wrong schema or task ID")
    if "axis_reviewers" in evidence:
        reviewers = evidence["axis_reviewers"]
        if (not isinstance(reviewers, dict) or set(reviewers) != set(AXES)
                or any(not isinstance(value, str) or not value.strip() or len(value) > 512
                       for value in reviewers.values())):
            raise ReviewEvidenceError(f"{review_name} axis_reviewers must name both axis reviewers")
        if evidence.get("author_identity") in reviewers.values():
            raise ReviewEvidenceError(f"{review_name} axis reviewer is the candidate author")
    axes = evidence.get("axes")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        raise ReviewEvidenceError(f"{review_name} must record both Standards and Spec axes")
    findings = {}
    axis_ids = {}
    for axis in AXES:
        axis_record = axes[axis]
        if complete_bundle and (
            not isinstance(axis_record, dict) or set(axis_record) != {"status", "findings"}
        ):
            raise ReviewEvidenceError(f"{review_name} {axis} record is malformed")
        if (
            not isinstance(axis_record, dict)
            or axis_record.get("status") != "complete"
            or not isinstance(axis_record.get("findings"), list)
        ):
            raise ReviewEvidenceError(f"{review_name} {axis} review is incomplete")
        axis_ids[axis] = set()
        for finding in axis_record["findings"]:
            if (
                not isinstance(finding, dict)
                or not {"id", "disposition"} <= finding.keys()
                or finding.keys() - {"id", "disposition", "reason", *FINDING_EVIDENCE_FIELDS}
            ):
                raise ReviewEvidenceError(f"{review_name} has a malformed finding")
            finding_id = finding["id"]
            if not isinstance(finding_id, str) or not IDENTIFIER.fullmatch(finding_id):
                raise ReviewEvidenceError(f"{review_name} has an invalid finding ID")
            error = finding_evidence_error(finding, review_name, evidence["schema_version"])
            if error is not None:
                raise ReviewEvidenceError(error)
            key = (review_name, finding_id)
            if key in findings:
                raise ReviewEvidenceError(f"{review_name} repeats finding ID {finding_id}")
            findings[key] = finding
            axis_ids[axis].add(finding_id)
    return findings, axis_ids


def _validate_identities(evidence, review_name, worker_roster):
    author = evidence["author_identity"]
    reviewer = evidence["reviewer_identity"]
    if not isinstance(author, str) or not author or not isinstance(reviewer, str) or not reviewer:
        raise ReviewEvidenceError(f"{review_name} identities must be non-empty strings")
    if author == reviewer:
        raise ReviewEvidenceError(f"{review_name} reviewer is the candidate author")
    if author not in worker_roster or reviewer not in worker_roster:
        raise ReviewEvidenceError(f"{review_name} identities are not both listed in workers.txt")
    if author != worker_roster[0]:
        raise ReviewEvidenceError(
            f"{review_name} author identity does not match the first task worker in workers.txt"
        )


def _validate_terminal_dispositions(findings, response):
    for (review_name, finding_id), finding in findings.items():
        disposition = finding["disposition"]
        if not isinstance(disposition, str) or disposition not in {"fixed", "rejected"}:
            raise ReviewEvidenceError(f"{review_name}/{finding_id} has no terminal disposition")
        reason = finding.get("reason", "")
        if not isinstance(reason, str) or (disposition == "rejected" and not reason.strip()):
            raise ReviewEvidenceError(f"{review_name}/{finding_id} needs a rejection reason")
        recorded = response.get((review_name, finding_id))
        if recorded is None or recorded[0] != disposition:
            raise ReviewEvidenceError(f"missing recorded disposition for {review_name}/{finding_id}")


def _validate_done(done, review_name, task_id, capture_id, axis_ids):
    if str(done.get("task_id")) != str(task_id) or done.get("capture_id") != capture_id:
        raise ReviewEvidenceError(f"{review_name}/done.json does not match its task and capture")
    if review_name == "review":
        for axis in AXES:
            count = done[f"{axis}_findings"]
            if type(count) is not int or count != len(axis_ids[axis]):
                raise ReviewEvidenceError(f"{review_name} {axis.title()} count disagrees with done.json")
        return
    count = done["new_findings"]
    unfixed = done["unfixed"]
    if type(count) is not int or count != sum(map(len, axis_ids.values())):
        raise ReviewEvidenceError(f"{review_name} finding count disagrees with done.json")
    if type(unfixed) is not int or unfixed != 0:
        raise ReviewEvidenceError(f"{review_name} reports unfixed review findings")


@dataclass(frozen=True)
class ReviewSummary:
    review_id: str
    author_identity: str
    reviewer_identity: str
    base: str
    head: str
    capture_id: str
    review_path: str
    review_evidence_sha256: str
    done_sha256: str
    review_markdown_sha256: str
    capture_manifest_sha256: str
    capture_complete_sha256: str
    finding_count: int
    markdown_compatibility: str
    legacy_precedent_sha256: str | None = None
    axis_reviewers: tuple[tuple[str, str], ...] | None = None

    def to_record(self):
        record = asdict(self)
        if self.legacy_precedent_sha256 is None:
            del record["legacy_precedent_sha256"]
        if self.axis_reviewers is None:
            del record["axis_reviewers"]
        else:
            record["axis_reviewers"] = dict(self.axis_reviewers)
        return record


@dataclass(frozen=True)
class ReviewBundle:
    """A validated bundle; only filesystem and capture ports perform I/O."""

    _summary: ReviewSummary

    @classmethod
    def read(
        cls, directory, expected_task, worker_roster, *, response,
        capture_verifier: CaptureVerifier,
        legacy_precedent: LegacyPrecedent | None = None,
        filesystem=None,
    ):
        """Validate a full review without resolving Git refs or writing files.

        ``response`` comes from parse_review_response. ``worker_roster`` keeps
        the candidate author first. CaptureVerifier guarantees complete verified
        coverage; legacy_precedent can authorize only exact historical bytes.
        """
        filesystem = LocalReviewFiles() if filesystem is None else filesystem
        directory = Path(directory)
        name = directory.name
        evidence_raw, evidence = _read_review_evidence(filesystem, directory / EVIDENCE_NAME)
        findings, axis_ids = _validate_evidence(evidence, name, expected_task=expected_task)
        _validate_identities(evidence, name, worker_roster)
        capture_id = evidence["capture_id"]
        if not isinstance(capture_id, str) or not capture_id:
            raise ReviewEvidenceError(f"{name} has no capture ID")
        capture = capture_verifier.verify(directory, capture_id)
        if capture.capture_id != capture_id:
            raise ReviewEvidenceError(f"{name} verified capture does not match its evidence")
        done_raw, done = _read_json(filesystem, directory / "done.json", "review done record")
        try:
            _validate_done(done, name, expected_task, capture_id, axis_ids)
        except KeyError as error:
            raise ReviewEvidenceError(str(error)) from error
        _validate_terminal_dispositions(findings, response)
        markdown_raw, markdown = _read_markdown(directory, filesystem)
        digests = {
            "review_evidence_sha256": hashlib.sha256(evidence_raw).hexdigest(),
            "done_sha256": hashlib.sha256(done_raw).hexdigest(),
            "review_markdown_sha256": hashlib.sha256(markdown_raw).hexdigest(),
            "capture_manifest_sha256": capture.manifest_sha256,
            "capture_complete_sha256": capture.complete_sha256,
        }
        legacy_digest = None
        compatibility = "strict"
        try:
            markdown_ids = parse_review_markdown_text(markdown, name)
        except ReviewEvidenceError:
            if legacy_precedent is not None:
                legacy_digest = legacy_precedent({
                    "review_id": name,
                    "base": capture.base,
                    "head": capture.head,
                    "capture_id": capture_id,
                    "author_identity": evidence["author_identity"],
                    "reviewer_identity": evidence["reviewer_identity"],
                    **digests,
                    "finding_ids": {axis: sorted(axis_ids[axis]) for axis in AXES},
                })
            if legacy_digest is None:
                raise
            compatibility = "allowlisted-pre-change"
        else:
            if markdown_ids != axis_ids:
                raise ReviewEvidenceError(
                    f"{name}/review.md Markdown finding IDs disagree with review-evidence.json"
                )
        return cls(ReviewSummary(
            review_id=name,
            author_identity=evidence["author_identity"],
            reviewer_identity=evidence["reviewer_identity"],
            base=capture.base,
            head=capture.head,
            capture_id=capture_id,
            review_path=name,
            **digests,
            finding_count=len(findings),
            markdown_compatibility=compatibility,
            legacy_precedent_sha256=legacy_digest,
            axis_reviewers=(tuple(evidence["axis_reviewers"].items())
                            if "axis_reviewers" in evidence else None),
        ))

    def summary(self):
        return self._summary


@dataclass(frozen=True)
class DispositionEvidence:
    """Staged sidecar bytes, not a claim that a review bundle is complete."""

    path: Path
    original: bytes


@dataclass(frozen=True)
class EvidenceUpdate:
    path: Path
    original: bytes
    replacement: bytes


def read_disposition_evidence(review_dir, *, filesystem=None):
    filesystem = LocalReviewFiles() if filesystem is None else filesystem
    evidence_path = review_dir / EVIDENCE_NAME
    raw, evidence = _read_review_evidence(filesystem, evidence_path, regular=True)
    _validate_evidence(evidence, review_dir.name)
    return DispositionEvidence(evidence_path, raw)


def apply_dispositions(documents, response):
    """Validate the entire response before producing any sidecar replacements.

    The input documents and original bytes remain unchanged. The write adapter
    decides how to back up and persist the returned updates.
    """
    updated = []
    all_findings = {}
    for document in documents:
        # Parse the saved bytes so applying responses cannot change the original.
        evidence = records.parse_json(document.original)
        findings, _axis_ids = _validate_evidence(evidence, document.path.parent.name)
        all_findings.update(findings)
        updated.append((document, evidence))
    unknown = sorted(set(response) - set(all_findings))
    if unknown:
        names = ", ".join(f"{review}/{finding}" for review, finding in unknown)
        raise ReviewEvidenceError(f"review-response.md has unknown finding(s): {names}")
    missing = sorted(set(all_findings) - set(response))
    if missing:
        names = ", ".join(f"{review}/{finding}" for review, finding in missing)
        raise ReviewEvidenceError(f"review-response.md is missing disposition(s) for: {names}")
    for key, (disposition, reason) in response.items():
        all_findings[key].update(disposition=disposition, reason=reason)
    _validate_terminal_dispositions(all_findings, response)
    changes = []
    for document, evidence in updated:
        replacement = (
            json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
            + b"\n"
        )
        if replacement != document.original:
            changes.append(EvidenceUpdate(document.path, document.original, replacement))
    return tuple(changes)


def parse_worker_identities(path, task_id, *, filesystem=None):
    filesystem = LocalReviewFiles() if filesystem is None else filesystem
    try:
        lines = filesystem.read(path).decode("utf-8").splitlines()
    except FileNotFoundError as error:
        raise ReviewEvidenceError(f"missing workers.txt: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ReviewEvidenceError(f"cannot read workers.txt: {path}") from error
    identities = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[0] == str(task_id) and fields[1] not in identities:
            identities.append(fields[1])
    if not identities:
        raise ReviewEvidenceError(f"workers.txt has no roster entry for task {task_id}")
    return identities


def read_review_records(
    task_dir, task_id, workers_path, *, capture_verifier,
    legacy_precedent=None, filesystem=None,
):
    """Collect valid bundles plus diagnostics, preserving preflight's selection policy."""
    filesystem = LocalReviewFiles() if filesystem is None else filesystem
    response_path = task_dir / "review-response.md"
    try:
        response_text = filesystem.read(response_path).decode("utf-8")
    except FileNotFoundError:
        response_text = ""
    except (OSError, UnicodeError) as error:
        raise ReviewEvidenceError(f"cannot read review-response.md: {response_path}") from error
    response = parse_review_response(response_text)
    roster = parse_worker_identities(workers_path, task_id, filesystem=filesystem)
    summaries = []
    diagnostics = []
    for directory in filesystem.review_directories(task_dir):
        if not filesystem.is_file(directory / EVIDENCE_NAME):
            if filesystem.is_file(directory / "review.md") or filesystem.is_file(directory / "done.json"):
                diagnostics.append(f"{directory.name} lacks {EVIDENCE_NAME}")
            continue
        try:
            bundle = ReviewBundle.read(
                directory, task_id, roster, response=response, capture_verifier=capture_verifier,
                legacy_precedent=legacy_precedent, filesystem=filesystem,
            )
            summaries.append(bundle.summary().to_record())
        except (OSError, ReviewEvidenceError) as error:
            diagnostics.append(f"{directory.name}: {error}")
    return summaries, diagnostics


def validate_chain_authors(reviews):
    """All selected reviews must establish the same candidate author."""
    if len({review["author_identity"] for review in reviews}) != 1:
        raise ReviewEvidenceError("review records disagree about the candidate author identity")
