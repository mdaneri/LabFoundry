from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest


def load_lifecycle_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "interop" / "lifecycle_test.py"
    spec = importlib.util.spec_from_file_location("lifecycle_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_baseline(path: Path, *, fingerprint: str = "abc123") -> None:
    path.write_text(
        """
{
  "steps": [
    {
      "name": "ca-client-certificate-check",
      "status": "passed",
      "evidence": {
        "common_name": "client-a.labfoundry.internal",
        "certificate": {
          "serial_number": "01",
          "sha256_fingerprint": "%s",
          "subject": "CN=client-a.labfoundry.internal",
          "issuer": "CN=LabFoundry Internal Root CA"
        }
      }
    }
  ]
}
"""
        % fingerprint,
        encoding="utf-8",
    )


def test_restored_certificate_baseline_check_matches_fingerprint(tmp_path):
    lifecycle = load_lifecycle_module()
    baseline = tmp_path / "result.json"
    write_baseline(baseline)
    args = argparse.Namespace(certificate_baseline_result=str(baseline))

    evidence = lifecycle.restored_certificate_baseline_check(
        args,
        {
            "common_name": "client-a.labfoundry.internal",
            "certificate": {
                "serial_number": "01",
                "sha256_fingerprint": "abc123",
                "subject": "CN=client-a.labfoundry.internal",
                "issuer": "CN=LabFoundry Internal Root CA",
            },
        },
    )

    assert evidence["sha256_fingerprint"] == "abc123"


def test_restored_certificate_baseline_check_rejects_changed_fingerprint(tmp_path):
    lifecycle = load_lifecycle_module()
    baseline = tmp_path / "result.json"
    write_baseline(baseline)
    args = argparse.Namespace(certificate_baseline_result=str(baseline))

    with pytest.raises(lifecycle.LifecycleError, match="does not match pre-restore certificate"):
        lifecycle.restored_certificate_baseline_check(
            args,
            {
                "common_name": "client-a.labfoundry.internal",
                "certificate": {
                    "serial_number": "01",
                    "sha256_fingerprint": "changed",
                    "subject": "CN=client-a.labfoundry.internal",
                    "issuer": "CN=LabFoundry Internal Root CA",
                },
            },
        )
