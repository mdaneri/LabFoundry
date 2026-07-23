# Signed Appliance Releases and Photon Updates

Appliance Update is audited runtime maintenance, separate from desired-state
enforcement and `/appliance-apply`. The web process queues work, and
`labfoundry-worker.service` executes it as a durable `appliance-update` task.
The task retains the selected channel and release, verified key ID, checksums,
Python compatibility, service checks, rollback result, and bounded redacted
helper output.

## Update streams

LabFoundry has three update streams:

- **LabFoundry Release** installs one signed, self-contained application
  release. It replaces the former independent LabFoundry wheel and Python
  Libraries streams.
- **Photon OS** checks or installs packages only after validating the proposed
  system Python ABI against the active LabFoundry release.
- **PowerShell Modules** checks or installs the explicitly managed modules from
  their selected repositories.

The appliance never performs a broad runtime `pip --upgrade` and never contacts
PyPI during a LabFoundry release update. Application dependencies and bootstrap
tools are exact, hash-locked wheels inside the release bundle.

## Release sources and channels

GitHub is the default distribution origin:

- GitHub Releases stores immutable versioned manifests, signatures, bundles,
  and the legacy bridge wheel.
- GitHub Pages publishes signed `development`, `preview`, and `stable` channel
  pointers.
- A successful `main` CI run publishes the exact successful commit as
  `vX.Y.Z` and advances `development`.
- The manual promotion workflow advances `preview` or `stable` to an existing
  verified release. Promotion never rebuilds the artifact.

The default source is:

```text
https://mdaneri.github.io/LabFoundry/updates
```

For the `stable` channel, the appliance reads:

```text
https://mdaneri.github.io/LabFoundry/updates/channels/stable/manifest.json
https://mdaneri.github.io/LabFoundry/updates/channels/stable/manifest.json.sig
```

Operators may add HTTPS mirrors and failover sources. A mirror copies the
original channel pointers, release manifests, signatures, and bundles without
re-signing them. Every source must satisfy the same signed v2 contract.
Credentials remain encrypted in the database and move to the privileged helper
only in the existing mode-0600 transient file. They are not written to
manifests, tasks, audits, URLs, or helper output.

Photon and PowerShell source fields autosave as desired runtime-maintenance
configuration. **Synchronize repositories** explicitly writes only
LabFoundry-owned tdnf and PowerShell client configuration. Signed LabFoundry
sources are read directly and do not configure pip.

## Trust contract

Appliances contain named Ed25519 public keys under:

```text
/etc/labfoundry/update-trust.d/<key-id>.pem
```

Private signing keys exist only in the protected `appliance-release` GitHub
environment. The updater rejects missing, malformed, unknown-key, mismatched,
or invalid signatures. A release may add a future public key because its
contents are already signed by a currently trusted key. Old keys are retained
until an overlapping signed release has provisioned the replacement.

A signed channel pointer contains its channel, selected version and full
commit, immutable release-manifest URL, issue time, and signing-key ID. The
signed release manifest contains its version and commit, build time, updater
protocol, database schema version, supported Python ABIs, bundle URL/size/hash,
and a hash for every bundled file.

The Pages root `manifest.json` is the fixed legacy v1 bridge for older clients.
New clients accept only signed v2 channel and release documents. After the
bridge installs once, the appliance uses signed channels.

## Transactional installation

The helper verifies the channel and release signatures, URL contract, updater
protocol, current Python ABI, bundle size/hash, safe archive paths, exact
content set, and per-file hashes before it can switch the running release.

Each candidate is built under:

```text
/opt/labfoundry/releases/<version>
```

The helper creates the candidate virtualenv from the ABI-specific retained
wheelhouse with `PIP_CONFIG_FILE=/dev/null`, `--no-index`,
`--require-hashes`, and no network dependency resolution. It then runs
`pip check`, imports the web/worker/console modules, and validates the installed
entry points. `/opt/labfoundry/current` points at the active release, while
`/opt/labfoundry/.venv` remains a compatibility symlink through `current`.

Before switching, the helper enables the nginx maintenance response, pauses the
web and console services, closes the worker's database session, and creates a
consistent SQLite backup. It atomically changes `current`, installs the
matching privileged helper and systemd definitions, starts the application,
and probes internal `/openapi.json`.

Any failure restores the previous release link, helper/systemd files, and
database snapshot before maintenance mode is removed. A root-owned finalizer at
`/var/lib/labfoundry/apply/appliance-update/finalizer-status.json` records the
definitive transaction result so the worker can persist the durable task
outcome. Only the current and previous known-good releases are retained; the UI
does not expose arbitrary historical downgrades.

## Photon OS boundary

Manual and scheduled Photon checks/installations remain available. Before
mutation, the helper records an inspection of the proposed tdnf transaction and
queries its candidate `python3` minor ABI. It fails closed if that ABI is not
listed in the active signed LabFoundry bundle.

If Photon changes Python to another supported ABI, the helper reconstructs the
active virtualenv from the retained offline wheelhouse before restarting and
probing LabFoundry. It does not claim automatic RPM rollback and never reboots
automatically. If OS maintenance leaves the appliance unhealthy, the task
records the transaction evidence and manual recovery boundary.

## Persisted-state migration

On upgrade, LabFoundry:

- renames `labfoundry_wheel` schedule/job selections to
  `labfoundry_release`;
- removes `python_libraries` from mixed schedules;
- disables Python-only schedules with an explicit migration notice;
- deletes retired Python source rows and encrypted credentials;
- converts legacy HTTPS `.../manifest.json` release sources to v2 base URLs,
  disables invalid non-HTTPS release sources, and removes retired per-package
  LabFoundry/Python rows; and
- records one `migrate_signed_release_updates` audit event.

Appliances without a LabFoundry source receive the public GitHub Pages source.
Existing HTTPS mirrors remain in priority order.

## Signed lifecycle fixture

Hyper-V lifecycle coverage can exercise the complete release transaction with:

```powershell
scripts/windows/hyperv/invoke-lifecycle-test.ps1 `
  -SignedReleaseRepositoryUrl https://release-fixture.example.test/updates
```

The fixture must use the appliance's named test trust key. Its signed
`preview` channel must select a healthy release newer than the image baseline;
its signed `development` channel must select a candidate that reaches database
startup and then fails the service-health probe. The lifecycle runner proves
the preview upgrade, expects the development task to fail with
`rolled_back=true`, and compares the active release, compatibility virtualenv,
database schema hash, and user identities before and after rollback. It then
rechecks the web, worker, console, internal `/openapi.json`, and host-facing
API. Omitting the URL skips only this externally supplied fixture.

## Release operator workflow

The protected workflows use these checked-in inputs:

```text
requirements-appliance.lock
requirements-appliance-bootstrap.in
requirements-appliance-bootstrap.lock
requirements-release-tools.lock
image/common/update-trust/
```

The CI declaration fingerprint prevents dependency or Python-range changes
without a regenerated hash lock. Release CI installs only the hash-locked
bootstrap tools, verifies every downloaded wheel or source archive against the
checked-in lock, builds any missing pure wheel without build isolation, and
writes an ABI-specific `requirements-wheelhouse.lock` over the resulting wheel
bytes. It does this for CPython 3.12, 3.13, and 3.14 before running:

```bash
python scripts/build_release_bundle.py \
  --wheelhouses wheelhouses \
  --signing-key /protected/release-key.pem \
  --signing-key-id labfoundry-release-2026-01 \
  --commit <successful-main-sha>
```

Publication is idempotent. An existing tag or release must identify the same
commit and exact asset bytes or the workflow fails. To promote, run
**Promote appliance release**, choose `preview` or `stable`, and provide an
existing version without the `v` prefix.

Schedules for checks or installs live under **Operations → Automation**. See
[`automation.md`](automation.md).
