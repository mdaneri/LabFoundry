SERVICE_STATE_DEFAULTS = [
    {"service": "routing", "display_name": "Routing", "running": True, "enabled": True, "health": "healthy"},
    {"service": "firewall", "display_name": "Firewall", "running": True, "enabled": True, "health": "healthy"},
    {"service": "dns", "display_name": "DNS", "running": False, "enabled": False, "health": "disabled"},
    {"service": "dhcp", "display_name": "DHCP", "running": False, "enabled": False, "health": "disabled"},
    {
        "service": "chronyd",
        "display_name": "Chrony",
        "running": False,
        "enabled": False,
        "health": "disabled",
        "detail": "chronyd.service / UDP 123",
    },
    {
        "service": "kms",
        "display_name": "KMS / KMIP",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "PyKMIP lab backend",
    },
    {
        "service": "repository",
        "display_name": "VCF Offline Depot",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "/mnt/labfoundry-vcf-offline-depot",
    },
    {
        "service": "esxi-pxe",
        "display_name": "ESXi PXE",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "/var/lib/labfoundry/pxe/http/esxi/ks",
    },
    {
        "service": "vcf-private-registry",
        "display_name": "VCF Private Registry",
        "running": False,
        "enabled": False,
        "health": "planned",
        "detail": "Harbor / vcf-supervisor-services",
    },
    {
        "service": "vcf-backups",
        "display_name": "VCF Backup SFTP",
        "running": False,
        "enabled": False,
        "health": "disabled",
        "detail": "/mnt/labfoundry-vcf-backups",
    },
    {"service": "ca", "display_name": "Certificate Authority", "running": False, "enabled": False, "health": "planned"},
    {"service": "ldap", "display_name": "LDAP", "running": False, "enabled": False, "health": "planned"},
    {"service": "auth", "display_name": "Authentication", "running": True, "enabled": True, "health": "healthy"},
]

SERVICE_STATE_IDS = frozenset(row["service"] for row in SERVICE_STATE_DEFAULTS)
RETIRED_SERVICE_IDS = frozenset({"ntpd"})
SERVICE_SYSTEMD_UNITS = {
    "chronyd": "chronyd.service",
}
