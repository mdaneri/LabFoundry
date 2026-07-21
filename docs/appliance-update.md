# Appliance Update and Software Sources

Appliance Update is audited runtime maintenance, separate from desired-state
enforcement and `/appliance-apply`. Update requests are queued as durable tasks
and executed by `labfoundry-worker.service`; restarting the web application does
not interrupt them. The page separates repository and managed-module editing in
the secondary **Update Sources** tab from check, run, result, and manifest
workflows in the primary **Update Streams** tab.

## Sources

The **Update Sources** section supports multiple named source definitions per
package ecosystem. Each ecosystem is a collapsible section with one tab per
repository and a final **+ Repository** tab. Sources can be added, disabled,
edited, and deleted. Managed PowerShell modules remain in a separate
collapsible editor with one tab per module; each module selects one of the
configured PowerShell repositories.

- **Photon** uses the repositories installed with Photon by default. An operator
  can instead enable a LabFoundry-managed HTTP(S) repository definition with
  RPM signature and TLS verification settings. The built-in source reads the
  enabled repository ids, names, and base/mirror locations from
  `/etc/yum.repos.d` and does not rewrite those files.
- **Python** leaves pip defaults intact when its URL is blank. Setting an
  HTTP(S) Simple API URL writes the LabFoundry virtualenv's owned `pip.conf`.
  The built-in source queries the LabFoundry virtualenv's pip configuration and
  displays its effective index URL with no LabFoundry override.
- **PowerShell** defaults to `PSGallery` at
  `https://www.powershellgallery.com/api/v2`. Source synchronization registers
  it with PowerShellGet. Managed modules have their own repository binding and
  either an exact pinned version or a latest-version policy, so modules from
  PSGallery and private galleries can coexist.
- **LabFoundry** takes a repository base URL and a `stable`, `preview`, or
  `development` channel. The UI derives the manifest URL, so operators no
  longer need to invent a manifest path.

Saving source fields updates control-plane configuration only. **Synchronize
repositories** queues a helper-backed synchronization task. Disabled or removed
LabFoundry-owned Photon, pip, and PowerShell definitions are cleaned up by that
task. In dry-run mode the task validates and records intent without modifying
host package clients.

Source editors autosave individual definitions, but synchronization remains an
explicit audited runtime-maintenance action because it can write package-client
configuration. The compact synchronization action stays in the **Update
Sources** header rather than interrupting the repository and managed-module
editors. The UI reports the effective built-in Photon repositories and
pip index discovered from the appliance rather than substituting explanatory
placeholder strings. Repository fields use aligned compact controls, and the
ecosystem/repository tabs preserve the active editing context after changes.

A source cannot be deleted while a managed package still references it. Rebind
or delete those packages first. Removing a managed PowerShell module stops
future checks and installs but does not uninstall an already installed module.
Use **Check selected** to perform the package-client repository lookups without
installing updates.

## Creating a LabFoundry release repository

Run this from a clean source checkout:

```bash
python scripts/build_update_wheel.py --channel stable
```

It creates this publishable tree under `dist/update`:

```text
dist/update/
  index.json
  manifest.json                         # direct-URL compatibility
  channels/stable/manifest.json         # channel contract
  packages/labfoundry-<version>.whl
```

Serve or copy the complete `dist/update` directory to an HTTP(S) static host.
If it is available as `https://updates.example/labfoundry`, enter that base URL
in LabFoundry and choose `stable`; the appliance requests:

```text
https://updates.example/labfoundry/channels/stable/manifest.json
```

The channel manifest contains the version, full git commit, build timestamp,
relative wheel path, Python floor, and SHA256. A direct HTTP(S) `.json` URL is
still accepted for migration from the original single-manifest design.
Subsequent builds preserve other channel entries and packages. Pass `--clean`
when intentionally creating a fresh single-channel repository tree.

For a temporary development server:

```bash
python -m http.server 18080 --directory dist
```

The repository base is then `http://<dev-host-ip>:18080/update`. From a Photon
VM, do not use `localhost` unless the server actually runs inside that VM.

## Streams

- **Photon OS** runs `tdnf makecache`, `tdnf check-update`, and `tdnf -y update`.
- **Python Libraries** checks and upgrades LabFoundry's direct virtualenv
  dependencies through the configured Python source.
- **PowerShell Modules** finds and installs enabled managed modules from the
  registered PowerShell repository.
- **LabFoundry Wheel** downloads the channel manifest and wheel, verifies the
  SHA256 and full commit metadata, installs with
  `pip install --force-reinstall --no-deps`, restores virtualenv permissions,
  and schedules a delayed `labfoundry.service` restart.

Every check, source synchronization, and install is an `appliance-update` task.
Real execution stages its redacted configuration at:

```text
/var/lib/labfoundry/apply/appliance-update/labfoundry-update.json
```

Task results retain bounded redacted command output and are mirrored under the
`labfoundry.appliance_update` logger. Photon updates may require a reboot;
LabFoundry records that recommendation and does not reboot automatically.

Schedules for update checks or installs are configured under **Operations →
Automation**. Their wizard first captures the schedule name and task type, then
offers the same update-stream selection used by this page. See
[`automation.md`](automation.md).
