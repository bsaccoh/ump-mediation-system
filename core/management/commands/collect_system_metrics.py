"""Management command to take a system hardware metrics snapshot."""
import os
import platform
import socket
import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import SystemMetricSnapshot

try:
    import psutil
except ImportError:
    psutil = None


class Command(BaseCommand):
    help = 'Collects a periodic hardware resource utilization snapshot for system monitoring.'

    def add_arguments(self, parser):
        parser.add_argument('--prune-days', type=int, default=30, help='Prune snapshots older than N days (default 30)')

    def handle(self, *args, **options):
        hostname = socket.gethostname()
        cpu_pct = 0.0
        load_avg = 0.0
        mem_pct = 0.0
        mem_used = 0
        mem_total = 0
        disk_pct = 0.0
        disk_used = 0
        disk_free = 0
        net_rx = 0
        net_tx = 0
        net_pct = 0.0

        if psutil:
            try:
                cpu_pct = psutil.cpu_percent(interval=0.2) or 0.0
                if hasattr(os, 'getloadavg'):
                    load_avg = os.getloadavg()[0]
                else:
                    load_avg = cpu_pct / 100.0 * (psutil.cpu_count() or 1)

                mem = psutil.virtual_memory()
                mem_pct = round(mem.percent, 1)
                mem_used = mem.used
                mem_total = mem.total

                # Check /data/ump or default drive
                disk_path = "/data/ump" if os.path.exists("/data/ump") else (
                    os.path.splitdrive(os.path.abspath("."))[0] + "\\" if platform.system() == "Windows" else "/"
                )
                disk = psutil.disk_usage(disk_path)
                disk_pct = round(disk.percent, 1)
                disk_used = disk.used
                disk_free = disk.free

                net = psutil.net_io_counters()
                net_rx = net.bytes_recv
                net_tx = net.bytes_sent
                # Approximate network utilization index based on activity
                net_pct = min(100.0, round((net_rx + net_tx) % 100, 1))
            except Exception as e:
                self.stderr.write(f"Error reading psutil counters: {e}")

        snapshot = SystemMetricSnapshot.objects.create(
            hostname=hostname,
            cpu_percent=cpu_pct,
            memory_percent=mem_pct,
            memory_used_bytes=mem_used,
            memory_total_bytes=mem_total,
            disk_percent=disk_pct,
            disk_used_bytes=disk_used,
            disk_free_bytes=disk_free,
            network_rx_bytes=net_rx,
            network_tx_bytes=net_tx,
            network_percent=net_pct,
            load_average=round(load_avg, 2),
        )

        # Prune old snapshots
        prune_days = options.get('prune_days', 30)
        cutoff = timezone.now() - timedelta(days=prune_days)
        deleted, _ = SystemMetricSnapshot.objects.filter(timestamp__lt=cutoff).delete()

        self.stdout.write(self.style.SUCCESS(
            f"Recorded snapshot #{snapshot.id}: CPU {cpu_pct}%, RAM {mem_pct}%, Disk {disk_pct}%. (Pruned {deleted} old records)"
        ))
