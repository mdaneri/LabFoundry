# Managed LDAP for VCF Automation 9.1

LabFoundry can operate a single OpenLDAP 2.6 service for VCF Automation while keeping each VCF organization in a separate LDAP naming context and LMDB database. This service is independent from LabFoundry operator authentication: LabFoundry sign-in remains local in v1.

## Security boundary

- External access is LDAPS on TCP 636 only. Plaintext LDAP on TCP 389 is not configured.
- Privileged reconciliation uses local `ldapi:///` with SASL EXTERNAL through `labfoundry-helper ldap`.
- The integrated LabFoundry CA issues the `ldap:ldaps` certificate for the configured hostname and listener addresses.
- Firewall apply owns TCP/636 rules for selected addressed management, access, or route interfaces and enabled VLANs.
- Each organization receives a separate suffix and database. Its generated VCF bind identity can read only that suffix.
- VCF bind secrets are encrypted with `LABFOUNDRY_SECRETS_KEY`. A generated or rotated secret is displayed once.
- User passwords are held only in process memory until global LDAP apply. Password plaintext and hashes are never stored in the application database, previews, tasks, or audit details.

The default organization layout is:

```text
dc=<organization>,dc=ldap,dc=labfoundry,dc=internal
├── ou=users
├── ou=groups
├── ou=service-accounts
└── ou=system
```

## Directory behavior

The helper generates one MDB database per enabled organization and configures the `ppolicy`, `memberof`, and referential-integrity overlays. Groups use DN-valued `member` attributes and may contain users or groups. LabFoundry rejects cross-organization members and direct or indirect group cycles before save and again during helper validation.

The default password policy requires 14 characters with uppercase, lowercase, number, special-character, and username checks. Five failures lock a user for 15 minutes, and five previous passwords are retained. Expiry is disabled by default because v1 has no end-user password-change portal. Administrators can stage password resets, enable or disable users, and request an unlock; enforcement occurs only through global LDAP apply.

## VCF Automation integration

Every organization can download a manual ZIP bundle containing the LDAPS endpoint, root CA PEM, search bases, bind DN, VCF Automation 9.1 JSON, and operator instructions. The bind password is intentionally separate.

The guided workflow pins the VCF Automation TLS SHA-256 fingerprint, reads current organization LDAP settings, requires explicit replacement approval, writes `settingsSource=DEFINED`, tests LDAP, and verifies that VCF can find at least one user and group. Administrator credentials are transient and are not stored.

The VCF 9.1 mapping includes:

```json
{
  "userAttributes": {
    "serviceAccount": "employeeType"
  }
}
```

LabFoundry does not import LDAP groups into VCF or assign VCF organization roles. Complete those steps in VCF Automation and retain local break-glass administrators.

## Apply and recovery

The `ldap` apply unit stages secret-bearing JSON at `/var/lib/labfoundry/apply/ldap/labfoundry-ldap.json` with mode `0600`. The file is excluded from previews and task payloads and removed after helper processing. When LDAP-related CA, DNS, or firewall desired state changes, global appliance apply submits the changed dependency units together.

Normal settings backup contains LDAP metadata but no bind secrets or password hashes. Use the separate passphrase-encrypted LDAP recovery export to preserve `slapcat` data. Recovery import decrypts and validates the archive in memory, then stages it for the next global LDAP apply. A restart before apply requires the archive and passphrase again.
