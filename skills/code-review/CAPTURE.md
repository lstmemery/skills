# Capture a fixed review version

Use [scripts/capture.py](scripts/capture.py) with Python 3 and Git. It captures
source and authority bytes so both reviewers can use the same version. It does
not run reviews, decide whether a spec exists, or supply a semantic verdict.

```sh
python3 <skill-dir>/scripts/capture.py capture --repo <repo-root> --mode branch --base main --out <outside-repo>/review --authority <spec> --authority <standards>
python3 <skill-dir>/scripts/capture.py capture --repo <repo-root> --mode since --base <commit-or-tag> --out <outside-repo>/review
python3 <skill-dir>/scripts/capture.py capture --repo <repo-root> --mode wip --out <outside-repo>/review
```

`branch` captures merge-base-to-HEAD changes and base-to-HEAD history. `since`
captures the exact selected endpoint through HEAD; it is not just that commit's
own patch. `wip` uses the net `git diff HEAD` and automatically includes every
non-ignored regular untracked file returned by Git. Index identity is recorded
so a staged-state change can invalidate freshness even when the net diff agrees.

`--preview` lists the selected refs, changed/untracked paths, authorities, and
destination without copying bodies or writing files. Capture writes a new
artifact directory; its parent must exist, and it must be outside the repo.
Existing destinations are never overwritten. No fetch, checkout, index update,
commit, external diff, or text-conversion program is run.

## Reading the capture

`manifest.json` records scope, resolved object IDs, exact Git argv, before/after
inventories, changed/untracked paths, hashes, authorities, gaps, and coverage.
Content lives at `blobs/<sha256>`; `diff.patch` and `commits.txt` are ordinary
files. `COMPLETE` identifies a fully written capture. A directory left without
that marker after interruption is incomplete; select a new destination.

```sh
python3 <skill-dir>/scripts/capture.py verify --capture <capture-dir>
python3 <skill-dir>/scripts/capture.py read --capture <capture-dir> --path src/example.py
python3 <skill-dir>/scripts/capture.py read --capture <capture-dir> --side before --path src/example.py
```

`read` returns JSON containing captured UTF-8 source. It can read unchanged
surrounding files as well as changed files. Give both axes the same capture ID;
provide each only its relevant authority references from the manifest. Additional
live-checkout reads do not inherit the capture's version guarantee. If needed
context is absent, report that gap or prepare a new capture with it included.

Missing authority files, symlinks, binary files, submodules, oversized files, and
unreadable content remain explicit gaps when in the changed scope. Symlink targets
are recorded without following them. Binary bytes may be retained but still
need a suitable reviewer. Unchanged context can also have individual gaps: a
reviewer who needs such an entry must report the limitation. A directory walk
also finds Git-untracked special files (FIFOs, sockets, devices) that Git's own
untracked-file listing omits; their content is not captured, but their path is
recorded as a visible gap rather than silently dropped.

`coverage: complete` means no known capture gaps for the selected change; it
never means review passed. With gaps, perform useful partial review and label the
findings partial. Full-scope review stays incomplete until gaps are covered or
the scope is explicitly revised. Keep the revised scope and exclusions visible.

## Secrets gate

Before any payload is written, every captured byte — source blobs from both
sides (including unchanged context files), authorities, `diff.patch`,
`commits.txt`, and `manifest.json` — is scanned for secrets. A capture with a
match fails closed with outcome `secret_detected` (exit 6) and nothing is
written to the destination. Findings name the payload, line, and rule; values
are never reported — built-in-rule findings carry only a length and a sha256
prefix (gitleaks reports stay redacted end to end).

Two layers run. When the gitleaks binary is installed it scans the payloads in
redacted directory mode; its own configuration and ignore files are deliberately
not consulted, and one false-positive class is suppressed: hex-shaped matches of
its generic rule (digests, UUIDs, commit SHAs). A built-in rule set always runs
alongside it: private-key PEM blocks, common token prefixes (`ghp_`,
`github_pat_`, `sk-`, `xox…`, `AKIA…`, `glpat-`, `tk_`), and high-entropy
assignments to names containing password/passwd/secret/token/api-key/access-key.
Hex-shaped values and placeholders are not flagged by the built-in rules. If
gitleaks is missing, the built-in rules still apply and the capture result says
`unavailable` under `secret_scan`. If gitleaks is present but cannot run, times
out, returns an unexpected status, or produces malformed output, capture fails
closed with `secret_detected` and `secrets scanner failed`; no payload is
written. Operators who deliberately accept the reduced coverage can set
`CODE_REVIEW_SECRETS_SCANNER=stdlib-only` to skip gitleaks and use only the
built-in rules. This opt-in is not a fallback after a scanner failure.

A private deployment can block known live values without publishing them: point
the environment variable `CODE_REVIEW_SECRET_FINGERPRINTS` at a file of sha256
prefixes (one per line, 8–64 hex characters, `#` comments). Any payload token of
8+ characters — or the value side of a `KEY=value` or `KEY:value` token — whose
sha256 starts with a listed prefix fails the capture. An unreadable or
malformed denylist file is an explicit input error.

A repository with a reviewed false positive may track `.code-review-secrets-allow`
with one full SHA-256 fingerprint and a tab-separated reason per line. Blank and
`#` comment lines are ignored; malformed entries fail capture. The fingerprint
must match the exact value reported by a built-in rule or the exact byte span
reported by gitleaks. Changing the matched bytes invalidates the exception. The
allowlist does not suppress the private fingerprint denylist. Never add a real
credential; replace it instead.

## Freshness and errors

```sh
python3 <skill-dir>/scripts/capture.py check --capture <capture-dir>
```

`check` verifies stored hashes and compares fresh source observations with the
capture. Drift exits 3 with `fresh_review_required: true`. Finish the old-version
review, but require a fresh review before claiming the new checkout was reviewed.
A committed review with dirty WIP explicitly flags that uncommitted checkout
content lies outside its reviewed version. Scope changes require a new capture;
never edit the existing manifest to turn partial coverage into complete coverage.

Exit 0 means capture/preview/read/verification succeeded; exit 2 means invalid
input, an unrecognized or malformed manifest, or a Git failure; exit 3 means
conflict/drift; exit 4 means a well-formed capture whose stored content no longer
matches its recorded digests or completion marker; exit 5 means
unsupported/unavailable input or I/O; exit 6 means the secrets gate refused the
capture. Missing refs, an unborn HEAD,
unmerged WIP, or multiple merge bases are explicit failures. The helper observes
twice and rejects changed inputs; it does not lock developers out of editing, and
cannot prove the absence of an external change-and-revert between observations.

This first version snapshots the full before/after tracked trees for context.
Bounds are 8 MiB per source file, 256 MiB unique source bytes, and 20,000 tree
entries. It favors reliable local evidence over minimal disk use; it claims correctness of
the recorded evidence only, not review speed. Authorities
are explicit local paths; spec discovery and missing-spec handling remain in the
calling skill.
