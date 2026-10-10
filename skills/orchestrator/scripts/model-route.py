#!/usr/bin/env python3
"""Preview a CSV model route as native argv; performs no launches or writes."""

import argparse
from pathlib import Path

from herdr_jobs.model_ladder import load_ladder, native_arguments, select
from herdr_jobs.records import JobError, encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path(__file__).resolve().parents[1] / "model-ladder.csv")
    parser.add_argument("--level", type=int, required=True)
    parser.add_argument("--fallback-reason", choices=("unavailable", "rate_limit", "exhausted"))
    parser.add_argument("--evidence")
    args = parser.parse_args()
    try:
        if bool(args.fallback_reason) != (args.evidence is not None):
            raise JobError("invalid_input", "fallback reason and evidence must be supplied together")
        fallback = {"reason": args.fallback_reason, "evidence": args.evidence} if args.fallback_reason else None
        selection = select(load_ladder(args.csv), args.level, fallback)
        print(encoded({**selection, "native_argv": native_arguments(selection),
                       "capabilities": "unverified; validate installed runtime/model/provider before any effect"}).decode())
        return 0
    except JobError as error:
        print(encoded({"error": error.kind, "message": str(error)}).decode())
        return 4 if error.kind == "unavailable_capability" else 2
    except (OSError, UnicodeError) as error:
        print(encoded({"error": "io_error", "message": str(error)}).decode())
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
