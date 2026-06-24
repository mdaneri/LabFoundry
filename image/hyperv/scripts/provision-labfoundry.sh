#!/bin/sh
set -eu

LABFOUNDRY_SRC="${LABFOUNDRY_SRC:-/tmp/labfoundry-src}"
LABFOUNDRY_HOME="${LABFOUNDRY_HOME:-/opt/labfoundry}"
LABFOUNDRY_STATE="${LABFOUNDRY_STATE:-/var/lib/labfoundry}"
LABFOUNDRY_LOG="${LABFOUNDRY_LOG:-/var/log/labfoundry}"
LABFOUNDRY_MGMT_ADDRESS="${LABFOUNDRY_MGMT_ADDRESS:-192.168.49.1/24}"
LABFOUNDRY_MGMT_GATEWAY="${LABFOUNDRY_MGMT_GATEWAY:-192.168.49.254}"
LABFOUNDRY_MGMT_DNS="${LABFOUNDRY_MGMT_DNS:-1.1.1.1 9.9.9.9}"
LABFOUNDRY_MGMT_INTERFACE="${LABFOUNDRY_MGMT_INTERFACE:-eth0}"
BOOTSTRAP_USERNAME="${LABFOUNDRY_BOOTSTRAP_ADMIN_USERNAME:-admin}"
BOOTSTRAP_PASSWORD="${LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD:-}"

log_step() {
  printf '\n==> LabFoundry appliance: %s\n' "$1"
}

if [ -z "$BOOTSTRAP_PASSWORD" ]; then
  echo "LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD is required for appliance provisioning" >&2
  exit 2
fi

log_step "refreshing Photon package metadata"
tdnf -y clean all || true
tdnf -y makecache

log_step "applying Photon OS updates"
tdnf -y update

log_step "installing Photon appliance packages"
tdnf -y install python3 python3-pip python3-devel python3-virtualenv sudo openssh-server curl rsync tar gzip shadow hyper-v nftables dnsmasq nginx

log_step "verifying Photon OS updates after package install"
tdnf -y update

log_step "disabling systemd SSH-over-vsock auto generator"
install -d -o root -g root -m 0755 /etc/systemd/system-generators
ln -sfn /dev/null /etc/systemd/system-generators/systemd-ssh-generator

if ! getent group labfoundry >/dev/null 2>&1; then
  groupadd --system labfoundry
fi

if ! id labfoundry >/dev/null 2>&1; then
  useradd --system --gid labfoundry --home-dir "$LABFOUNDRY_STATE" --shell /sbin/nologin labfoundry
fi

install -d -o root -g root -m 0755 "$LABFOUNDRY_HOME"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/apply/firewall"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/apply/dnsmasq"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/apply/local-users"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/apply/vcf-backups"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/apply/vcf-offline-depot"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_STATE/dnsmasq"
install -d -o root -g root -m 0755 "$LABFOUNDRY_STATE/users"
install -d -o labfoundry -g labfoundry -m 0750 "$LABFOUNDRY_LOG"
install -d -o root -g root -m 0755 /etc/labfoundry
install -d -o root -g root -m 0755 /etc/labfoundry/dnsmasq.d
install -d -o root -g root -m 0755 /etc/labfoundry/nginx/sites.d
install -d -o root -g root -m 0755 /etc/labfoundry/ssh/authorized_keys
install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
install -d -o root -g root -m 0755 /etc/systemd/network
install -d -o root -g root -m 0755 /usr/local/lib/labfoundry
install -d -o root -g root -m 0755 /mnt/labfoundry-vcf-backups
install -d -o root -g root -m 0755 /mnt/labfoundry-vcf-registry
install -d -o root -g root -m 0755 /mnt/labfoundry-vcf-offline-depot

if ! id "$BOOTSTRAP_USERNAME" >/dev/null 2>&1; then
  useradd --home-dir "$LABFOUNDRY_STATE/users/$BOOTSTRAP_USERNAME" --create-home --shell /sbin/nologin "$BOOTSTRAP_USERNAME"
fi
printf '%s:%s\n' "$BOOTSTRAP_USERNAME" "$BOOTSTRAP_PASSWORD" | chpasswd

cat >/etc/labfoundry/build-info <<EOF
build_time_utc=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
photon_release=$(cat /etc/photon-release 2>/dev/null || true)
kernel=$(uname -r)
python=$(python3 --version 2>&1)
package_update=tdnf -y update completed during image provisioning
final_mgmt_address=$LABFOUNDRY_MGMT_ADDRESS
final_mgmt_gateway=$LABFOUNDRY_MGMT_GATEWAY
final_mgmt_interface=$LABFOUNDRY_MGMT_INTERFACE
EOF
chmod 0644 /etc/labfoundry/build-info

rm -f /etc/sudoers.d/90-labfoundry-build

log_step "syncing LabFoundry application files"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  --exclude "test-results" \
  "$LABFOUNDRY_SRC"/ "$LABFOUNDRY_HOME"/

install -d -o root -g root -m 0755 "$LABFOUNDRY_HOME/bin"

log_step "installing LabFoundry Python environment"
python3 -m venv "$LABFOUNDRY_HOME/.venv"
"$LABFOUNDRY_HOME/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$LABFOUNDRY_HOME/.venv/bin/python" -m pip install "$LABFOUNDRY_HOME"

SECRET_KEY="$("$LABFOUNDRY_HOME/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
SECRETS_KEY="$("$LABFOUNDRY_HOME/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat >/etc/labfoundry/labfoundry.env <<EOF
LABFOUNDRY_ENVIRONMENT=appliance
LABFOUNDRY_DATABASE_URL=sqlite:////var/lib/labfoundry/labfoundry.db
LABFOUNDRY_SECRET_KEY=$SECRET_KEY
LABFOUNDRY_SECRETS_KEY=$SECRETS_KEY
LABFOUNDRY_BOOTSTRAP_ADMIN_USERNAME=$BOOTSTRAP_USERNAME
LABFOUNDRY_BOOTSTRAP_ADMIN_PASSWORD=$BOOTSTRAP_PASSWORD
LABFOUNDRY_DRY_RUN_SYSTEM_ADAPTERS=true
LABFOUNDRY_REPOSITORY_PATH=/mnt/labfoundry-vcf-offline-depot
LABFOUNDRY_VCF_BACKUP_PATH=/mnt/labfoundry-vcf-backups
EOF
chmod 0640 /etc/labfoundry/labfoundry.env
chown root:labfoundry /etc/labfoundry/labfoundry.env

install -o root -g root -m 0644 "$LABFOUNDRY_HOME/image/hyperv/systemd/labfoundry.service" /etc/systemd/system/labfoundry.service
install -o root -g root -m 0755 "$LABFOUNDRY_HOME/scripts/appliance/labfoundry-helper" "$LABFOUNDRY_HOME/bin/labfoundry-helper"
install -o root -g root -m 0640 "$LABFOUNDRY_HOME/image/hyperv/sudoers.d/labfoundry-helper" /etc/sudoers.d/labfoundry-helper
visudo -cf /etc/sudoers.d/labfoundry-helper

chown -R root:root "$LABFOUNDRY_HOME"
chmod 0755 /opt "$LABFOUNDRY_HOME"
find "$LABFOUNDRY_HOME/labfoundry" "$LABFOUNDRY_HOME/scripts" "$LABFOUNDRY_HOME/image" -type d -exec chmod 0755 {} +
find "$LABFOUNDRY_HOME/labfoundry" "$LABFOUNDRY_HOME/scripts" "$LABFOUNDRY_HOME/image" -type f -exec chmod 0644 {} +
find "$LABFOUNDRY_HOME/.venv" -type d -exec chmod 0755 {} +
find "$LABFOUNDRY_HOME/.venv" -type f -exec chmod u+rw,go+r {} +
find "$LABFOUNDRY_HOME/.venv/bin" -type f -exec chmod a+rx {} +
chmod 0755 "$LABFOUNDRY_HOME/bin" "$LABFOUNDRY_HOME/bin/labfoundry-helper"
chown -R labfoundry:labfoundry "$LABFOUNDRY_STATE" "$LABFOUNDRY_LOG"

log_step "configuring final appliance management network"
{
  printf '[Match]\n'
  printf 'Name=%s\n\n' "$LABFOUNDRY_MGMT_INTERFACE"
  printf '[Network]\n'
  printf 'Address=%s\n' "$LABFOUNDRY_MGMT_ADDRESS"
  printf 'Gateway=%s\n' "$LABFOUNDRY_MGMT_GATEWAY"
  for dns_server in $LABFOUNDRY_MGMT_DNS; do
    printf 'DNS=%s\n' "$dns_server"
  done
} >/etc/systemd/network/00-labfoundry-mgmt.network
chmod 0644 /etc/systemd/network/00-labfoundry-mgmt.network
rm -f /etc/systemd/network/50-static-en.network /etc/systemd/network/99-dhcp-en.network

{
  for dns_server in $LABFOUNDRY_MGMT_DNS; do
    printf 'nameserver %s\n' "$dns_server"
  done
} >/etc/resolv.conf
chmod 0644 /etc/resolv.conf

log_step "enabling appliance services"
systemctl daemon-reload
systemctl enable systemd-networkd
systemctl enable systemd-resolved || true
systemctl enable sshd
systemctl enable --now hv_kvp_daemon || true
systemctl enable --now hv_fcopy_daemon || true
systemctl enable --now hv_vss_daemon || true
systemctl enable labfoundry

log_step "configuring LabFoundry nftables firewall"
install -d -o root -g root -m 0755 /etc/labfoundry/nftables.d
cat >/etc/labfoundry/nftables.d/labfoundry.nft <<'EOF'
# Managed by LabFoundry. Local changes may be overwritten.
# nftables firewall state for Photon OS appliance images.
flush ruleset
table inet labfoundry {
  chain input {
    type filter hook input priority filter; policy drop;
    iifname "lo" accept comment "LabFoundry loopback"
    ct state established,related accept comment "LabFoundry established traffic"
    ip saddr 192.168.49.0/24 tcp dport { 22, 80, 443, 8000 } accept comment "LabFoundry management access"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept comment "LabFoundry established traffic"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }
  chain output {
    type filter hook output priority filter; policy accept;
    ct state established,related accept comment "LabFoundry established traffic"
    meta l4proto icmp accept comment "LabFoundry ICMP diagnostics"
    meta l4proto ipv6-icmp accept comment "LabFoundry IPv6 ICMP diagnostics"
  }
}
EOF
chmod 0644 /etc/labfoundry/nftables.d/labfoundry.nft
cat >/etc/systemd/system/labfoundry-firewall.service <<'EOF'
[Unit]
Description=LabFoundry nftables firewall
DefaultDependencies=no
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/labfoundry/nftables.d/labfoundry.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now labfoundry-firewall.service
if command -v iptables >/dev/null 2>&1; then
  iptables -P INPUT ACCEPT || true
  iptables -P FORWARD ACCEPT || true
  iptables -P OUTPUT ACCEPT || true
  iptables -F || true
  iptables -X || true
fi
systemctl disable --now iptables || true

log_step "running Photon compatibility check"
"$LABFOUNDRY_HOME/.venv/bin/python" "$LABFOUNDRY_HOME/scripts/check_photon_compatibility.py"
