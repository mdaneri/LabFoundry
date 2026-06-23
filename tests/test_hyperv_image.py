from pathlib import Path


def test_photon_provisioning_management_network_matches_eth0_only():
    script = Path("image/hyperv/scripts/provision-labfoundry.sh").read_text(encoding="utf-8")

    assert 'LABFOUNDRY_MGMT_INTERFACE="${LABFOUNDRY_MGMT_INTERFACE:-eth0}"' in script
    assert 'printf \'Name=%s\\n\\n\' "$LABFOUNDRY_MGMT_INTERFACE"' in script
    assert "Name=eth* en*" not in script
    assert "rm -f /etc/systemd/network/50-static-en.network /etc/systemd/network/99-dhcp-en.network" in script
