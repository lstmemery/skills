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
input or a Git failure; exit 3 means conflict/drift; exit 4 means corrupt capture;
exit 5 means unsupported/unavailable input or I/O. Missing refs, an unborn HEAD,
unmerged WIP, or multiple merge bases are explicit failures. The helper observes
twice and rejects changed inputs; it does not lock developers out of editing, and
cannot prove the absence of an external change-and-revert between observations.

This first version snapshots the full before/after tracked trees for context.
Bounds are 8 MiB per source file, 256 MiB unique source bytes, and 20,000 tree
entries. It favors reliable local evidence over minimal disk use; no latency or
weaker-model reliability improvement is claimed without measurement. Authorities
are explicit local paths; spec discovery and missing-spec handling remain in the
calling skill.
