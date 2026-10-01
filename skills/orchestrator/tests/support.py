"""Shared test environment helpers."""

import os


def isolate_admission_state(test_case, state_dir):
    """Point subprocesses at a fixture directory and restore the caller's env."""
    previous = os.environ.get("ORCHESTRATOR_ADMISSION_DIR")
    os.environ["ORCHESTRATOR_ADMISSION_DIR"] = str(state_dir)
    test_case.addCleanup(_restore_admission_state, previous)


def _restore_admission_state(previous):
    if previous is None:
        os.environ.pop("ORCHESTRATOR_ADMISSION_DIR", None)
    else:
        os.environ["ORCHESTRATOR_ADMISSION_DIR"] = previous
