from labfoundry.app.models import VcfDepotDownloadProfile, VcfOfflineDepotSettings
from labfoundry.app.services.vcf_offline_depot import (
    VCF_DEPOT_COMPONENTS,
    VCF_DEPOT_ESX_DISABLED_PLATFORMS,
    render_vcfdt_command_preview,
    validate_vcf_depot_state,
)


def test_vcf_depot_validation_requires_correct_credential_kind(tmp_path):
    archive = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    archive.write_bytes(b"not-a-real-archive")
    settings = VcfOfflineDepotSettings(
        enabled=True,
        hostname="depot.labfoundry.internal",
        listen_interface="eth2",
        listen_address="192.168.50.1",
        port=443,
        server_certificate="depot.labfoundry.internal",
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
        tool_archive_path=str(archive),
        tool_version="9.1.0",
        telemetry_choice="DISABLE",
        config_path="/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf",
    )
    profiles = [
        VcfDepotDownloadProfile(name="install", profile_type="binaries", sku="VCF", vcf_version="9.1.0", binary_type="INSTALL", enabled=True),
        VcfDepotDownloadProfile(name="metadata", profile_type="metadata", enabled=True),
        VcfDepotDownloadProfile(name="esx", profile_type="esx", enabled=True),
    ]

    errors, warnings = validate_vcf_depot_state(settings, profiles, {"eth2"})
    assert any("install requires an uploaded download token" in error for error in errors)
    assert any("metadata requires an uploaded download token" in error for error in errors)
    assert any("ESX profile esx requires an uploaded activation-code file" in error for error in errors)
    assert warnings == []

    errors, _warnings = validate_vcf_depot_state(
        settings,
        profiles,
        {"eth2"},
        download_token_present=True,
        activation_code_present=True,
    )
    assert errors == []


def test_vcf_depot_validation_uses_documented_component_catalog(tmp_path):
    archive = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    archive.write_bytes(b"not-a-real-archive")
    settings = VcfOfflineDepotSettings(
        enabled=True,
        hostname="depot.labfoundry.internal",
        listen_interface="eth2",
        listen_address="192.168.50.1",
        port=443,
        server_certificate="depot.labfoundry.internal",
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
        tool_archive_path=str(archive),
        tool_version="9.1.0",
        telemetry_choice="DISABLE",
        config_path="/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf",
    )
    assert VCF_DEPOT_COMPONENTS["VRA"] == "VCF Automation"
    assert VCF_DEPOT_COMPONENTS["VCF_OBSERVABILITY_DATA_PLATFORM"] == "Observability Data Platform"
    assert len(VCF_DEPOT_COMPONENTS) == 32

    errors, _warnings = validate_vcf_depot_state(
        settings,
        [
            VcfDepotDownloadProfile(
                name="invalid-component",
                profile_type="binaries",
                sku="VCF",
                vcf_version="9.1.0",
                binary_type="INSTALL",
                component="NOT_A_COMPONENT",
                enabled=True,
            )
        ],
        {"eth2"},
        download_token_present=True,
    )

    assert any("unsupported component NOT_A_COMPONENT" in error for error in errors)


def test_vcf_depot_validation_uses_esx_disabled_platform_catalog(tmp_path):
    archive = tmp_path / "vcf-download-tool-9.1.0.test.tar.gz"
    archive.write_bytes(b"not-a-real-archive")
    settings = VcfOfflineDepotSettings(
        enabled=True,
        hostname="depot.labfoundry.internal",
        listen_interface="eth2",
        listen_address="192.168.50.1",
        port=443,
        server_certificate="depot.labfoundry.internal",
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
        tool_archive_path=str(archive),
        tool_version="9.1.0",
        telemetry_choice="DISABLE",
        config_path="/etc/labfoundry/nginx/sites.d/vcf-offline-depot.conf",
    )
    assert VCF_DEPOT_ESX_DISABLED_PLATFORMS == (
        "esxio-9.1-INTL",
        "armEsx-9.1-INTL",
        "embeddedEsx-8.0-INTL",
        "embeddedEsx-7.0-INTL",
        "embeddedEsx-9.0-INTL",
        "embeddedEsx-9.1-INTL",
        "esxio-8.0-INTL",
        "esxio-9.0-INTL",
        "embeddedEsx-6.7-INT",
    )

    errors, _warnings = validate_vcf_depot_state(
        settings,
        [
            VcfDepotDownloadProfile(
                name="esx",
                profile_type="esx",
                disabled_platforms="\n".join(VCF_DEPOT_ESX_DISABLED_PLATFORMS),
                enabled=True,
            )
        ],
        {"eth2"},
        activation_code_present=True,
    )
    assert errors == []

    errors, _warnings = validate_vcf_depot_state(
        settings,
        [
            VcfDepotDownloadProfile(
                name="esx",
                profile_type="esx",
                disabled_platforms="embeddedEsx-5.5-INTL",
                enabled=True,
            )
        ],
        {"eth2"},
        activation_code_present=True,
    )
    assert any("unsupported disabled platform embeddedEsx-5.5-INTL" in error for error in errors)


def test_vcf_depot_command_preview_uses_staged_secret_paths():
    settings = VcfOfflineDepotSettings(
        hostname="depot.labfoundry.internal",
        depot_store_path="/mnt/labfoundry-vcf-offline-depot",
        tool_archive_path="vcfDownloadTool/vcf-download-tool-9.1.0.test.tar.gz",
        tool_version="9.1.0",
    )
    profiles = [
        VcfDepotDownloadProfile(
            name="upgrade",
            profile_type="binaries",
            sku="VCF",
            vcf_version="9.1.0",
            binary_type="UPGRADE",
            upgrades_only=True,
            component="VRA",
            component_version="9.1.0.0100",
            enabled=True,
        ),
        VcfDepotDownloadProfile(
            name="esx",
            profile_type="esx",
            disabled_platforms="esxio-9.1-INTL\narmEsx-9.1-INTL",
            enabled=True,
        ),
    ]

    preview = render_vcfdt_command_preview(settings, profiles)

    assert "vcf-download-tool binaries list" in preview
    assert "--depot-store=/mnt/labfoundry-vcf-offline-depot" in preview
    assert "--depot-download-token-file=/etc/labfoundry/vcf-offline-depot/secrets/download-token.txt" in preview
    assert "--component=VRA" in preview
    assert "--component-version=9.1.0.0100" in preview
    assert "vcf-download-tool esx configuration -D esxio-9.1-INTL -D armEsx-9.1-INTL" in preview
    assert "--depot-download-activation-code-file=/etc/labfoundry/vcf-offline-depot/secrets/activation-code.txt" in preview
