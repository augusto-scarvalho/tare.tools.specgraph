"""Standalone entry for the same benchmark exposed by specgraph bench."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from specgraph.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
