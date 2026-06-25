#!/usr/bin/env python3
"""Embed LabFoundry kickstart and auto-install GRUB config into Photon ISO."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

GRUB_BOOT_CONFIG = """set default=0
set timeout=1

menuentry 'Install LabFoundry Photon OS with kickstart' {
    linux /isolinux/vmlinuz root=/dev/ram0 loglevel=3 ks=cdrom:/photon-ks.json insecure_installation=1 photon.media=cdrom
    initrd /isolinux/initrd.img
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Photon ISO with photon-ks.json and an auto-install GRUB entry embedded."
    )
    parser.add_argument("--source-iso", required=True, help="Original Photon ISO path.")
    parser.add_argument("--kickstart", required=True, help="Rendered photon-ks.json path.")
    parser.add_argument("--output", required=True, help="Output ISO path.")
    return parser.parse_args()


def replace_or_add_text_file(iso, *, iso_path: str, rr_name: str, text: str) -> None:
    rr_path = f"{iso_path.rsplit('/', 1)[0]}/{rr_name}"
    for lookup in ({"rr_path": rr_path}, {"iso_path": iso_path}):
        try:
            iso.get_record(**lookup)
        except Exception:
            continue
        iso.rm_file(**lookup)
        break

    payload = text.encode("utf-8")
    iso.add_fp(io.BytesIO(payload), len(payload), iso_path=iso_path, rr_name=rr_name)


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
        replace_or_add_text_file(
            iso,
            iso_path="/EFI/BOOT/GRUB.CFG;1",
            rr_name="grub.cfg",
            text=GRUB_BOOT_CONFIG,
        )
        iso.write(str(output))
    finally:
        iso.close()

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
