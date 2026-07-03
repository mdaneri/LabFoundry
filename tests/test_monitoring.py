from datetime import timedelta

from labfoundry.app.models import utcnow
from labfoundry.app.services.monitoring import (
    CpuCounters,
    DiskUsage,
    MonitorSnapshot,
    NetworkCounters,
    cpu_percent,
    monitor_payload,
    parse_diskstats,
    parse_meminfo,
    parse_net_dev,
    parse_proc_stat_cpu,
    record_monitor_sample,
)


class FakeMonitorCollector:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def collect(self, sampled_at=None):
        if self.snapshots:
            return self.snapshots.pop(0)
        raise AssertionError("Unexpected monitor collection")

    def collect_virtualization(self):
        return {
            "detected": "vmware",
            "sys_vendor": "VMware, Inc.",
            "product_name": "VMware Virtual Platform",
            "vmtools_version": "VMware Tools 13.0",
            "hostname": "labfoundry-test",
            "platform": "Linux-test",
        }


def test_monitor_parsers_handle_linux_proc_shapes():
    cpu = parse_proc_stat_cpu("cpu  100 0 50 850 25 0 0 0\ncpu0 10 0 5 85 0 0 0 0\n")
    assert cpu == CpuCounters(total=1025, idle=875)
    assert cpu_percent(CpuCounters(total=1000, idle=900), CpuCounters(total=1100, idle=950)) == 50.0

    memory = parse_meminfo("MemTotal:       2048 kB\nMemAvailable:   512 kB\nSwapTotal:      128 kB\nSwapFree:        64 kB\n")
    assert memory["MemTotal"] == 2_097_152
    assert memory["MemAvailable"] == 524_288

    networks = parse_net_dev("  eth0: 1000 10 1 2 0 0 0 0 2000 20 3 4 0 0 0 0\n")
    assert networks[0].name == "eth0"
    assert networks[0].rx_bytes == 1000
    assert networks[0].tx_dropped == 4

    disks = parse_diskstats("   8       0 sda 1 0 8 0 2 0 16 0 0 0 0 0 0 0 0 0\n")
    assert disks["sda"].read_bytes == 4096
    assert disks["sda"].write_bytes == 8192


def test_monitor_samples_persist_rates_and_payload(client):
    from labfoundry.app.database import SessionLocal

    now = utcnow()
    snapshots = [
        MonitorSnapshot(
            sampled_at=now - timedelta(seconds=30),
            cpu=CpuCounters(total=1000, idle=900),
            cpu_count=4,
            load=(0.25, 0.2, 0.1),
            memory_total_bytes=8_000,
            memory_available_bytes=4_000,
            memory_used_percent=50.0,
            swap_total_bytes=1_000,
            swap_used_bytes=100,
            networks=[NetworkCounters(name="eth0", rx_bytes=1_000, tx_bytes=2_000, rx_packets=10, tx_packets=20, rx_errors=0, tx_errors=0, rx_dropped=0, tx_dropped=0, oper_state="up")],
            disks=[DiskUsage(mount_point="/", device="/dev/sda1", filesystem="ext4", total_bytes=10_000, used_bytes=4_000, free_bytes=6_000, used_percent=40.0, read_bytes=1_000, write_bytes=2_000)],
        ),
        MonitorSnapshot(
            sampled_at=now,
            cpu=CpuCounters(total=1100, idle=950),
            cpu_count=4,
            load=(0.5, 0.3, 0.2),
            memory_total_bytes=8_000,
            memory_available_bytes=3_000,
            memory_used_percent=62.5,
            swap_total_bytes=1_000,
            swap_used_bytes=125,
            networks=[NetworkCounters(name="eth0", rx_bytes=1_900, tx_bytes=2_600, rx_packets=19, tx_packets=26, rx_errors=1, tx_errors=0, rx_dropped=0, tx_dropped=1, oper_state="up")],
            disks=[DiskUsage(mount_point="/", device="/dev/sda1", filesystem="ext4", total_bytes=10_000, used_bytes=5_000, free_bytes=5_000, used_percent=50.0, read_bytes=1_600, write_bytes=3_200)],
        ),
    ]

    with SessionLocal() as db:
        collector = FakeMonitorCollector(snapshots)
        record_monitor_sample(db, collector=collector)
        second = record_monitor_sample(db, collector=collector)
        assert second.cpu_percent == 50.0
        assert second.network_samples[0].rx_bytes_per_sec == 30.0
        assert second.disk_samples[0].write_bytes_per_sec == 40.0

        payload = monitor_payload(db, hours=6, collector=FakeMonitorCollector([]))

    assert payload["summary"]["cpu"]["current_percent"] == 50.0
    assert payload["summary"]["memory"]["current_percent"] == 62.5
    assert payload["summary"]["network"]["rx_bytes_per_sec"] == 30.0
    assert payload["summary"]["disk"]["highest_used_mount"] == "/"
    assert payload["virtualization"]["detected"] == "vmware"
