# LabFoundry

![LabFoundry appliance graphic](labfoundry/app/static/brand/labfoundry-appliance-graphic.svg)

LabFoundry is a Linux-based, web-managed infrastructure appliance for homelabs, VMware Cloud Foundation labs, POCs, training environments, isolated network labs, and WAN simulation testing.

The MVP is a safe runnable scaffold. It provides the FastAPI control plane, appliance-style web UI, local authentication, JWT bearer API tokens, audit logging, OpenAPI 3.1, dry-run system adapters, and Windows/Hyper-V script scaffolding. It does not apply real host networking, firewall, service, SFTP, registry, repository, DNS, DHCP, CA, or KMS changes by default.

## Development

Primary workflow:

1. Develop inside WSL2 on Windows 11.
2. Run unit and API tests in WSL2.
3. Build or prepare a Hyper-V-compatible appliance image later.
4. Test the appliance in Hyper-V with PowerShell automation.

Install and run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn labfoundry.app.main:app --reload --host 127.0.0.1 --port 8000
```

Development URL:

```text
http://127.0.0.1:8000
```

Bootstrap local login:

```text
username: admin
password: labfoundry-admin
```

For a real appliance, set `LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD` before the first startup.

Default local VCF backup SFTP user:

```text
username: vcf-backup
password: labfoundry-vcf-backup
```

Reset this account from `Users` before exposing the SFTP endpoint beyond a development lab.

## Brand Assets

Reusable SVG assets live in `labfoundry/app/static/brand/` and are documented in `docs/branding.md`.

## Safety Boundary

Python is the control plane only. LabFoundry should not reimplement routing, firewalling, DNS, DHCP, SFTP, HTTPS serving, or CA behavior in Python.

The MVP follows these boundaries:

- App package: `labfoundry`
- Service user: `labfoundry`
- Default database: `data/labfoundry.db`
- Repository target: `/srv/repository`
- VCF private registry volume mount: `/mnt/labfoundry-vcf-registry`
- VCF backup volume mount: `/mnt/labfoundry-vcf-backups`
- VCF backup SFTP remote directory: `/backups`
- System adapters default to dry-run mode.
- Future privileged changes must use reviewed helper scripts and sudo allowlists.
- Subprocess calls must use argument arrays, not arbitrary shell strings.

## REST API

API prefix:

```text
/api/v1
```

OpenAPI and docs:

```text
http://127.0.0.1:8000/openapi.json
http://127.0.0.1:8000/api/docs
```

The OpenAPI document uses OpenAPI 3.1 and includes a JWT bearer security scheme.

Initial resource areas:

- Auth
- API Tokens
- Dashboard
- Interfaces
- VLANs
- Routes
- WAN
- Services
- Logs
- Audit
- Jobs
- Settings

Several future appliance resources are intentionally scaffolded as dry-run or status-only surfaces until their native Linux adapters are implemented.

## API Token Example

Create a bearer token from the bootstrap admin account:

```bash
curl -s \
  -X POST \
  "http://127.0.0.1:8000/api/v1/auth/login?username=admin&password=labfoundry-admin" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "development token",
    "scopes": [
      "read:dashboard",
      "read:routes",
      "read:wan",
      "write:wan",
      "read:services",
      "read:audit"
    ]
  }'
```

Call the dashboard API:

```bash
curl -s \
  -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8000/api/v1/dashboard
```

Create a WAN policy:

```bash
curl -s \
  -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8000/api/v1/wan/policies \
  -d '{
    "name": "Slow WAN",
    "latency_ms": 100,
    "jitter_ms": 10,
    "packet_loss_percent": 0.5,
    "bandwidth_mbit": 100
  }'
```

Problem-details errors use this shape:

```json
{
  "type": "https://labfoundry.internal/errors/validation-error",
  "title": "Validation error",
  "status": 422,
  "detail": "Invalid request payload",
  "instance": "/api/v1/wan/policies",
  "error_code": "VALIDATION_ERROR",
  "request_id": "req_123"
}
```

## API Scopes

Supported initial scopes:

```text
read:dashboard
read:interfaces
write:interfaces
read:vlans
write:vlans
read:routes
write:routes
read:wan
write:wan
read:firewall
write:firewall
read:dns
write:dns
read:dhcp
write:dhcp
read:ca
write:ca
read:kms
write:kms
read:repository
write:repository
read:vcf-registry
write:vcf-registry
read:vcf-backups
write:vcf-backups
read:services
write:services
read:logs
read:audit
write:backup
admin:all
```

Role checks and scope checks are both enforced. A viewer cannot mint admin scopes, and a network-admin cannot mint CA or repository administration scopes.

## Hyper-V Workflow

Windows-side automation lives in `scripts/windows/`.

From WSL2:

```bash
powershell.exe -ExecutionPolicy Bypass -File scripts/windows/create-hyperv-switches.ps1
```

The scaffold uses these switch names:

- `LabFoundry-Mgmt`
- `LabFoundry-SiteA`
- `LabFoundry-SiteB`
- `LabFoundry-Trunk`

The primary appliance image target is Hyper-V VHDX. ESXi/vSphere OVA and KVM/Proxmox QCOW2 are future packaging targets.

## PowerShell Roadmap

The future PowerShell module scaffold lives in:

```text
clients/powershell/LabFoundry/
```

The first generated or hand-wrapped cmdlets should map cleanly to the OpenAPI operation IDs. Token authentication should be preferred for automation. `-SkipCertificateCheck` may be added for lab testing only and must not be the default.

## Tests

Run:

```bash
pytest
```

The MVP test suite covers auth, token revocation, scope enforcement, audit records, UI smoke rendering, and OpenAPI contract checks.
