"""Run the public parser/engine against the offline test transport."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fake_host import FakeHost
from herdr_jobs.cli import main


if __name__ == "__main__":
    raise SystemExit(main(transport_factory=FakeHost))
