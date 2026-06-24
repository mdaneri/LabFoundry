# Certificate Authority Roadmap

LabFoundry CA starts as a local lab trust anchor for VCF integration. It is
designed to make HTTPS, KMS/KMIP, Offline Depot, and Private Registry
certificates usable without turning routine service pages into certificate
management tools.

## v1: Local CA Foundation

- Generate and persist one LabFoundry root CA.
- Store root and leaf private keys encrypted in the database with
  `LABFOUNDRY_SECRETS_KEY`.
- Auto-ensure certificates for LabFoundry HTTPS, KMS server TLS, KMS client
  certificates, VCF Offline Depot HTTPS, and VCF Private Registry HTTPS.
- Keep manual certificate requests and CSR intake for additional VCF
  integration certificates.
- Stage `/var/lib/labfoundry/apply/ca/labfoundry-ca.json` and let
  `labfoundry-helper ca validate|apply` write public CA bundles and service
  certificate/key files under `/etc/labfoundry`.
- Include encrypted CA material in settings backups; restore requires the same
  `LABFOUNDRY_SECRETS_KEY` to keep private material usable.

## v1.1: Rotation And Renewal

- Show expiry windows and renewal recommendations.
- Add explicit renew/reissue actions for managed and manual certificates.
- Coordinate service restarts or follow-up apply units after certificate
  replacement.

## v1.2: Intermediate CA Support

- Add optional root/intermediate hierarchy.
- Support chain downloads with path-length and usage constraints.
- Allow an offline-root posture while keeping appliance-issued leaf
  certificates practical for lab workflows.

## v1.3: Revocation

- Track revoked certificate state.
- Generate and publish CRLs through the CA apply unit.
- Refresh service trust bundles after revocation changes.

## v1.4: External CA Workflows

- Improve CSR export/import flows.
- Import externally signed certificate chains.
- Support enterprise PKI handoff while keeping local CA defaults simple.

## Later

- OCSP responder support.
- Certificate inventory APIs.
- Guided VCF import bundles for SDDC Manager and KMIP clients.
