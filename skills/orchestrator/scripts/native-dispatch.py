#!/usr/bin/env python3
"""Native worker dispatch with the managed route, admission, and resume contract."""

from herdr_jobs.cli import main


if __name__ == "__main__":
    raise SystemExit(main(backend="native"))
