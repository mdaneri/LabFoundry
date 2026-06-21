# LabFoundry PowerShell Module Roadmap

This folder is the scaffold for the future `LabFoundry` PowerShell module.

Goals:

- Connect to a LabFoundry appliance with bearer-token authentication.
- Generate friendly cmdlets from the OpenAPI contract where practical.
- Add hand-written wrappers for common workflows.
- Keep TLS validation enabled by default.
- Add `-SkipCertificateCheck` only for explicit lab testing.

Planned authentication commands:

```powershell
Connect-LabFoundry
Disconnect-LabFoundry
Get-LabFoundrySession
New-LabFoundryApiToken
Get-LabFoundryApiToken
Revoke-LabFoundryApiToken
```

Planned route and WAN commands:

```powershell
Get-LabFoundryRoute
New-LabFoundryRoute
Set-LabFoundryRoute
Remove-LabFoundryRoute
Get-LabFoundryWanPolicy
New-LabFoundryWanPolicy
Apply-LabFoundryWanPolicy
Clear-LabFoundryWanPolicy
Get-LabFoundryWanStatus
```
