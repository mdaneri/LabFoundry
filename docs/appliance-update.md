# Appliance Update

Appliance Update is runtime maintenance, not desired-state enforcement. It uses
the same job, audit, adapter, and constrained `labfoundry-helper` safety model
as global appliance apply, but it does not create pending `/appliance-apply`
drift.

The v1 workflow has three selectable streams:

- Photon OS: runs `tdnf makecache`, `tdnf check-update`, and `tdnf -y update`
  against the appliance's configured Photon repositories.
- Python Libraries: updates the LabFoundry virtual environment's direct Python
  dependencies with pip. Leave the Python index URL blank for standard pip
  behavior, or set an internal HTTP(S) index when the lab requires one.
- LabFoundry Wheel: downloads a LabFoundry update manifest, verifies the wheel
  SHA256, installs it with `pip install --force-reinstall --no-deps`, restores
  virtualenv read/execute permissions, and schedules a short delayed
  `labfoundry.service` restart.

The LabFoundry update source defaults to:

```text
http://localhost:18080/update/manifest.json
```

That default is useful when the update server runs on the appliance itself. For
Photon VM development, `localhost` means the Photon appliance, not the Windows
host, so change the source to a reachable host URL such as:

```text
http://<dev-host-ip>:18080/update/manifest.json
```

Build a local update wheel and manifest with:

```bash
python scripts/build_update_wheel.py
```

The script writes `dist/update/manifest.json` and a matching LabFoundry wheel.
The generated package version uses the project version plus git provenance, for
example `0.1.0+gabcdef123456`. The manifest also records the full git commit,
build time, wheel filename, required Python floor, and wheel SHA256.

Real update execution stages:

```text
/var/lib/labfoundry/apply/appliance-update/labfoundry-update.json
```

Each check or run records an `appliance-update` job. Helper command return
codes and redacted stdout/stderr excerpts are stored in the job result and
mirrored to the LabFoundry app log under the `labfoundry.appliance_update`
logger, including failures that happen while staging the update manifest before
the helper runs. Inspect `/var/log/labfoundry/labfoundry.log` when the UI says
an update failed but the rendered helper output is not enough.

After a successful update, the appliance also writes:

```text
/etc/labfoundry/update-info
```

Photon OS updates may require a reboot. V1 records that guidance but does not
auto-reboot the appliance.
