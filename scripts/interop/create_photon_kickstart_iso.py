#!/usr/bin/env python3
"""Embed a LabFoundry Photon kickstart file into a bootable Photon ISO."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Photon ISO with photon-ks.json embedded.")
    parser.add_argument("--source-iso", required=True, help="Original Photon ISO path.")
    parser.add_argument("--kickstart", required=True, help="Rendered photon-ks.json path.")
    parser.add_argument("--output", required=True, help="Output ISO path.")
    return parser.parse_args()


def main() -> int:
    try:
        import pycdlib
    except ImportError:
        print("pycdlib is required. Install it with: python -m pip install pycdlib", file=sys.stderr)
        return 2

    args = parse_args()
    source_iso = Path(args.source_iso)
    kickstart = Path(args.kickstart)
    output = Path(args.output)

    if not source_iso.is_file():
        print(f"Source ISO not found: {source_iso}", file=sys.stderr)
        return 2
    if not kickstart.is_file():
        print(f"Kickstart file not found: {kickstart}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    iso = pycdlib.PyCdlib()
    iso.open(str(source_iso))
    try:
        iso.add_file(str(kickstart), iso_path="/PHOTONKS.JSON;1", rr_name="photon-ks.json")
        iso.write(str(output))
    finally:
        iso.close()

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
