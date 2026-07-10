# VCF Certificate Trust

VCF Certificate Trust deploys the active LabFoundry root CA to one VCF 9
Installer or SDDC Manager appliance. Open it from the `VCF Certificate Trust`
button on `/vcf-helper`; the CA identity, target history, latest status, and run
form remain inside that modal. It is a remote maintenance task, not DNS desired
state and not an Appliance Apply unit.

Before starting, create a current VM snapshot as required by [Broadcom KB
316056](https://knowledge.broadcom.com/external/article/316056/how-to-adddelete-custom-ca-certificates.html).
The task requires the target IP/FQDN and SSH port, one-time VCF API credentials,
the `vcf` SSH password or private key, and the separate root password for
`su -`.

LabFoundry never persists these credentials. It stores only the target,
detected role/version, pinned SSH fingerprint, last deployed CA fingerprint,
and sanitized task results. First contact confirms the SSH fingerprint inside
the task modal before a real job is queued.

The task authenticates through `POST /v1/tokens`, detects the appliance using
`GET /v1/system/appliance-info`, checks
`GET /v1/sddc-manager/trusted-certificates`, and imports missing outbound trust
through `POST /v1/sddc-manager/trusted-certificates`. An identical certificate
is a successful no-op.

VCF Installer imports complete after API verification. SDDC Manager imports
also run the following command through SSH as `vcf` plus `su -`, then wait for
API recovery and verify the certificate again:

```text
/opt/vmware/vcf/operationsmanager/scripts/cli/sddcmanager_restart_services.sh
```

A restart/recovery failure is recorded as `partial-failure`, meaning the CA was
installed but manual service recovery may be required. VCF releases before 9.x
and certificate deletion are outside this release.

Routes:

- `GET /vcf-trust` redirects compatibility links to
  `/vcf-helper?vcf_trust=1`, which opens the modal.
- `POST /vcf-trust/root-ca` discovers/confirms SSH identity and queues the task.
- `POST /vcf-helper/trust-root-ca` remains a compatibility alias for cached
  clients and returns successful work to the modal.
