#!/usr/bin/env python3
"""Task-scoped loading profiles: deterministic generation from tracked sources.

Night decision N09 adopted bundle A as a task-scoped loading profile only.
The source documents keep every section; this helper renders a per-task copy
that omits only the anchors a declaration proves irrelevant, so the loaded
context shrinks without suppressing any mandatory requirement.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from herdr_jobs.records import (JobError, atomic_bytes, digest, fields, identifier,
                                invalid, load_json, read_regular, text, version)


# Anchor source paths are relative to the repository root this skill lives in
# (<repo>/skills/<skill-name>/scripts/loading-profile.py -> <repo>).
REPO_ROOT = Path(__file__).resolve().parents[3]

GUARD = (
    "Omission from this loading profile is not exemption. Assignment, admission, "
    "result-record, disposition/closeout, and testing requirements still apply "
    "from the governing contracts; only this task's loaded copy is reduced. "
    "If an omitted anchor becomes relevant to the task, load the full source file."
)


@dataclass(frozen=True)
class Anchor:
    source: str
    heading: str
    omit_only_when: str
    requires: dict


# Explicit selection: the only anchors a task-scoped profile may omit, where
# they live, and the declaration facts that must hold before omission.
ANCHORS = {
    "workers.assignment": Anchor(
        source="skills/orchestrator/references/workers.md",
        heading="## Assignment",
        omit_only_when=("the task will not prepare assignments or briefs for "
                        "further workers (no sub-delegation)"),
        requires={"subdelegation": ["none"]},
    ),
    "workers.terminal-disposition-closeout": Anchor(
        source="skills/orchestrator/references/workers.md",
        heading="## Terminal disposition and closeout",
        omit_only_when=("the worker will not record dispositions or run closeout "
                        "checks (coordinator-owned; the worker's result-record "
                        "contract still applies)"),
        requires={"disposition": ["coordinator-owned"]},
    ),
    "tdd.test-quality": Anchor(
        source="skills/tdd/SKILL.md",
        heading="## Test quality",
        omit_only_when=("the task writes no tests and runs no TDD cycles; any "
                        "test work loads the full tdd skill"),
        requires={"tests": ["none"]},
    ),
}

# Explicit profiles. task-a is the only profile (N09 adopted bundle A; no
# profile B exists).
PROFILES = {
    "task-a": {
        "description": ("Task-scoped Profile A: omit workers.assignment, "
                        "workers.terminal-disposition-closeout, and tdd.test-quality."),
        "anchors": sorted(ANCHORS),
    },
}


def profile_or_invalid(name):
    profile = PROFILES.get(name)
    if profile is None:
        invalid(f"unknown profile: {name!r}; known: {', '.join(sorted(PROFILES))}")
    return profile


def check_applicability(profile_name, applicability):
    profile = profile_or_invalid(profile_name)
    expected = set(profile["anchors"])
    if not isinstance(applicability, dict) or set(applicability) != expected:
        invalid(f"applicability must name exactly the profile anchors: {sorted(expected)}")
    for anchor_name in sorted(expected):
        anchor = ANCHORS[anchor_name]
        entry = applicability[anchor_name]
        required = ["reason", *anchor.requires]
        fields(entry, required, label=f"applicability.{anchor_name}")
        reason = text(entry["reason"], f"applicability.{anchor_name}.reason", 4096)
        # The reason is rendered inside the header's HTML comment; '-->' or a
        # line break in it could terminate or extend that comment and forge the
        # artifact's provenance metadata. Refuse every character str.splitlines()
        # would split a line on (LF, CR, VT, FF, NEL, the C0 FS/GS/RS separators,
        # U+2028, U+2029) and any other non-printable character, so the reason
        # is provably one printable line.
        if "-->" in reason or not reason.isprintable():
            invalid(f"applicability.{anchor_name}.reason must be a single printable "
                    f"line without '-->' so it cannot break the generated header comment")
        for fact, allowed in anchor.requires.items():
            if entry[fact] not in allowed:
                invalid(f"applicability.{anchor_name}.{fact} must be one of: {', '.join(allowed)}")
    return applicability


def load_declaration(path, profile_name):
    try:
        declaration = load_json(path)
    except FileNotFoundError:
        invalid(f"missing declaration file at {path}")
    except OSError as error:
        invalid(f"cannot read declaration at {path}: {error.strerror or error}")
    fields(declaration, ["schema_version", "task_id", "profile", "applicability"],
           label="declaration")
    version(declaration["schema_version"])
    identifier(declaration["task_id"], "declaration.task_id")
    if declaration["profile"] != profile_name:
        invalid(f"declaration profile {declaration['profile']!r} does not match "
                f"requested profile {profile_name!r}")
    check_applicability(profile_name, declaration["applicability"])
    return declaration, digest(read_regular(path))


def read_source(path):
    try:
        return read_regular(REPO_ROOT / path)
    except FileNotFoundError:
        invalid(f"anchor source file is missing: {path}; update the anchor selection")
    except OSError as error:
        invalid(f"cannot read anchor source file {path}: {error.strerror or error}")


def section_span(lines, anchor):
    """Return [start, end) line indexes of one heading's section, or invalid."""
    matches = [index for index, line in enumerate(lines) if line.rstrip("\n") == anchor.heading]
    if len(matches) != 1:
        invalid(f"{anchor.source}: expected exactly one {anchor.heading!r} heading, "
                f"found {len(matches)}; update the anchor selection")
    level = len(anchor.heading) - len(anchor.heading.lstrip("#"))
    heading_pattern = f"{'#' * level} "
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].rstrip("\n")
        if stripped.startswith(heading_pattern) or stripped == "#" * level:
            end = index
            break
    return start, end


def render_source(path, anchors):
    """Return the source text with each anchor's section removed, rest verbatim."""
    data = read_source(path)
    lines = data.decode("utf-8").splitlines(keepends=True)
    spans = sorted(section_span(lines, anchor) for anchor in anchors)
    for (_, previous_end), (next_start, _) in zip(spans, spans[1:]):
        if next_start < previous_end:
            invalid(f"{path}: overlapping anchor sections")
    kept = []
    cursor = 0
    for start, end in spans:
        kept.extend(lines[cursor:start])
        cursor = end
    kept.extend(lines[cursor:])
    return "".join(kept)


def render_body(profile_name, declaration):
    profile = profile_or_invalid(profile_name)
    by_source = {}
    for anchor_name in profile["anchors"]:
        anchor = ANCHORS[anchor_name]
        by_source.setdefault(anchor.source, []).append(anchor)
    parts = []
    for source in sorted(by_source):
        parts.append(f"<!-- source: {source} (anchors removed: "
                     f"{', '.join(sorted(a.heading for a in by_source[source]))}) -->\n")
        parts.append(render_source(source, by_source[source]))
    return "".join(parts)


def render_header(profile_name, declaration, declaration_digest):
    profile = profile_or_invalid(profile_name)
    lines = ["<!-- Task-scoped loading profile (generated; do not edit by hand)",
             f"     Profile: {profile_name} -- {profile['description']}",
             f"     Task: {declaration['task_id']}",
             "     Omitted anchors:",
             ]
    for anchor_name in profile["anchors"]:
        anchor = ANCHORS[anchor_name]
        entry = declaration["applicability"][anchor_name]
        lines.append(f"       - {anchor_name} ({anchor.source} {anchor.heading})")
        lines.append(f"         Applicable because: {entry['reason']}")
        lines.append(f"         Omit only when: {anchor.omit_only_when}")
    lines.append(f"     Guard: {GUARD}")
    for source in sorted({a.source for a in ANCHORS.values()}):
        lines.append(f"     Source sha256 {source}: {digest(read_source(source))}")
    lines.append(f"     Declaration sha256: {declaration_digest}")
    lines.append("-->")
    return "\n".join(lines) + "\n\n"


def render_profile(profile_name, declaration, declaration_digest):
    header = render_header(profile_name, declaration, declaration_digest)
    return header + render_body(profile_name, declaration)


def json_report(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)


def command_list(args):
    print(json_report({
        "schema_version": 1,
        "anchors": {name: {"source": anchor.source, "heading": anchor.heading,
                           "omit_only_when": anchor.omit_only_when,
                           "requires": anchor.requires}
                    for name, anchor in sorted(ANCHORS.items())},
        "profiles": {name: {"description": profile["description"],
                            "anchors": profile["anchors"]}
                     for name, profile in sorted(PROFILES.items())},
    }))
    return 0


def refuse_output_inside_repository(out):
    """Refuse an --out path that resolves into the repository, before any write.

    Sources are read, never modified (references/loading-profiles.md); the
    resolved path must stay outside REPO_ROOT so a mistyped or symlinked
    output path cannot overwrite a tracked source file.
    """
    resolved = Path(out).resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        invalid(f"--out must resolve outside the repository ({REPO_ROOT}): "
                f"{out} resolves to {resolved}")
    return resolved


def command_generate(args):
    refuse_output_inside_repository(args.out)
    declaration, declaration_digest = load_declaration(args.declaration, args.profile)
    data = render_profile(args.profile, declaration, declaration_digest).encode("utf-8")
    atomic_bytes(args.out, data)
    print(json_report({"schema_version": 1, "generated": True,
                       "profile": args.profile, "task_id": declaration["task_id"],
                       "out": str(args.out), "sha256": digest(data)}))
    return 0


def command_verify(args):
    declaration, declaration_digest = load_declaration(args.declaration, args.profile)
    expected = render_profile(args.profile, declaration, declaration_digest).encode("utf-8")
    candidate = read_regular(args.candidate)
    problems = []
    if candidate != expected:
        problems.append("candidate is not byte-identical to regeneration from source")
    try:
        decoded = candidate.decode("utf-8")
    except UnicodeDecodeError:
        decoded = ""
        problems.append("candidate is not valid UTF-8")
    for anchor_name in profile_or_invalid(args.profile)["anchors"]:
        heading = ANCHORS[anchor_name].heading
        if any(line.rstrip("\n") == heading for line in decoded.splitlines()):
            problems.append(f"omitted anchor heading still present: {heading!r}")
    if problems:
        for problem in problems:
            print(f"verify failed: {problem}", file=sys.stderr)
        raise JobError("conflict", "candidate does not match the generated profile")
    print(json_report({"schema_version": 1, "verified": True,
                       "profile": args.profile, "task_id": declaration["task_id"],
                       "candidate": str(args.candidate), "sha256": digest(candidate)}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="show explicit anchor and profile selection")

    generate = subparsers.add_parser("generate", help="generate a task-scoped profile")
    generate.add_argument("--profile", required=True, help="profile name (see list)")
    generate.add_argument("--declaration", required=True, type=Path,
                          help="applicability declaration JSON")
    generate.add_argument("--out", required=True, type=Path, help="output file")
    generate.set_defaults(function=command_generate)

    verify = subparsers.add_parser("verify", help="verify a candidate against source")
    verify.add_argument("--profile", required=True)
    verify.add_argument("--declaration", required=True, type=Path)
    verify.add_argument("--candidate", required=True, type=Path)
    verify.set_defaults(function=command_verify)

    args = parser.parse_args(argv)
    if args.command == "list":
        return command_list(args)
    return args.function(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except JobError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
