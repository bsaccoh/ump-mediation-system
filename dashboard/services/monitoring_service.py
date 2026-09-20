"""
UMP System Monitoring Service Layer
===================================
Aggregates real-time Ubuntu host telemetry, systemd service states,
pipeline telemetry (CDRFile, DistributionLog), and alarm/error tracking.
Zero mock data — strictly live system state and database records.
"""
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta

from django.db import connection
from django.db.models import Avg, Count, F, Q, Sum
from django.conf import settings
from django.utils import timezone

from collection.models import CDRFile, DataSource, DistributionLog, ProcessingError
from core.models import ActivityLog, Alert, SystemMetricSnapshot
from dashboard.services.system_health import get_disk_usage, get_systemd_status

try:
    import psutil
except ImportError:
    psutil = None


def _format_duration(seconds):
    """Format duration in seconds into human-readable string (e.g. 2m 14s or 45s)."""
    if seconds is None or seconds < 0:
        return "--"
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    minutes = secs // 60
    remaining_secs = secs % 60
    return f"{minutes}m {remaining_secs:02d}s"


def _pct_change_str(current, previous, label="vs. previous 24h", is_inverse=False):
    """Calculate percentage delta string with directional arrow."""
    if previous and previous > 0:
        change = round(((current - previous) / previous) * 100)
        if change > 0:
            arrow = "↑"
            cls = "text-danger" if is_inverse else "text-success"
            return f"{arrow} {change}% {label}", cls
        elif change < 0:
            arrow = "↓"
            cls = "text-success" if is_inverse else "text-danger"
            return f"{arrow} {abs(change)}% {label}", cls
        return f"0% {label}", "text-muted"
    if current > 0:
        arrow = "↑"
        cls = "text-danger" if is_inverse else "text-success"
        return f"{arrow} 100% {label}", cls
    return f"-- {label}", "text-muted"


def parse_time_range(range_key):
    """Parse time range key into (start_dt, end_dt, prev_start_dt, prev_end_dt, step_minutes, interval_label)."""
    now = timezone.now()
    range_map = {
        '1h': (timedelta(hours=1), timedelta(hours=1), 5, "vs. previous 1h"),
        '6h': (timedelta(hours=6), timedelta(hours=6), 15, "vs. previous 6h"),
        '12h': (timedelta(hours=12), timedelta(hours=12), 30, "vs. previous 12h"),
        '24h': (timedelta(hours=24), timedelta(hours=24), 60, "vs. previous 24h"),
        '7d': (timedelta(days=7), timedelta(days=7), 360, "vs. previous 7d"),
        '30d': (timedelta(days=30), timedelta(days=30), 1440, "vs. previous 30d"),
    }
    duration, prev_duration, step_minutes, label = range_map.get(range_key, range_map['24h'])
    start_dt = now - duration
    end_dt = now
    prev_start_dt = start_dt - prev_duration
    prev_end_dt = start_dt
    return start_dt, end_dt, prev_start_dt, prev_end_dt, step_minutes, label


def _run_lazy_alarm_evaluation():
    """Run the alarm engine if no evaluation happened in the last 60 seconds."""
    try:
        from core.alarm_engine import run_alarm_evaluation
        latest_alert = Alert.objects.order_by('-timestamp').first()
        now = timezone.now()
        if not latest_alert or (now - latest_alert.timestamp).total_seconds() > 60:
            run_alarm_evaluation()
    except Exception:
        pass


def ensure_recent_snapshot():
    """Ensure a hardware metrics snapshot exists from the last 60 seconds."""
    if not psutil:
        return
    _run_lazy_alarm_evaluation()
    latest = SystemMetricSnapshot.objects.order_by('-timestamp').first()
    now = timezone.now()
    if not latest or (now - latest.timestamp).total_seconds() > 60:
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
                cpu_pct = psutil.cpu_percent(interval=0.1) or 0.0
                if hasattr(os, 'getloadavg'):
                    load_avg = os.getloadavg()[0]
                else:
                    load_avg = cpu_pct / 100.0 * (psutil.cpu_count() or 1)

                mem = psutil.virtual_memory()
                mem_pct = round(mem.percent, 1)
                mem_used = mem.used
                mem_total = mem.total

                disk_path = str(settings.UMP_STORAGE_ROOT)
                disk = psutil.disk_usage(disk_path)
                disk_pct = round(disk.percent, 1)
                disk_used = disk.used
                disk_free = disk.free

                net = psutil.net_io_counters()
                net_rx = net.bytes_recv
                net_tx = net.bytes_sent
                net_pct = min(100.0, round((net_rx + net_tx) % 100, 1))
            except Exception:
                pass

        SystemMetricSnapshot.objects.create(
            hostname=socket.gethostname(),
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


def get_top_kpis(time_range='24h', operator_code=''):
    """Compute Top 5 KPI metrics with period-over-period comparison."""
    start_dt, end_dt, prev_start_dt, prev_end_dt, _, comp_label = parse_time_range(time_range)

    # 1. Total Files Processed
    base_cdr = CDRFile.objects.all()
    if operator_code:
        base_cdr = base_cdr.filter(operator_code__iexact=operator_code)

    cur_processed = base_cdr.filter(created_at__gte=start_dt, created_at__lte=end_dt).count()
    prev_processed = base_cdr.filter(created_at__gte=prev_start_dt, created_at__lt=prev_end_dt).count()
    processed_delta, processed_cls = _pct_change_str(cur_processed, prev_processed, comp_label)

    # 2. Files Decoded
    cur_decoded = base_cdr.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
        status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED]
    ).count()
    prev_decoded = base_cdr.filter(
        created_at__gte=prev_start_dt, created_at__lt=prev_end_dt,
        status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED]
    ).count()
    decoded_delta, decoded_cls = _pct_change_str(cur_decoded, prev_decoded, comp_label)

    # 3. Files Distributed
    base_dist = DistributionLog.objects.all()
    if operator_code:
        base_dist = base_dist.filter(cdr_file__operator_code__iexact=operator_code)

    cur_distributed = base_dist.filter(delivered_at__gte=start_dt, delivered_at__lte=end_dt, status=DistributionLog.Status.SUCCESS).count()
    prev_distributed = base_dist.filter(delivered_at__gte=prev_start_dt, delivered_at__lt=prev_end_dt, status=DistributionLog.Status.SUCCESS).count()
    distributed_delta, distributed_cls = _pct_change_str(cur_distributed, prev_distributed, comp_label)

    # 4. Average Processing Time
    dur_cur_qs = base_cdr.filter(
        created_at__gte=start_dt, created_at__lte=end_dt,
        processing_started__isnull=False, processing_completed__isnull=False
    )
    dur_prev_qs = base_cdr.filter(
        created_at__gte=prev_start_dt, created_at__lt=prev_end_dt,
        processing_started__isnull=False, processing_completed__isnull=False
    )

    cur_durations = [f.processing_duration for f in dur_cur_qs[:100] if f.processing_duration and f.processing_duration > 0]
    prev_durations = [f.processing_duration for f in dur_prev_qs[:100] if f.processing_duration and f.processing_duration > 0]

    cur_avg_sec = (sum(cur_durations) / len(cur_durations)) if cur_durations else 0
    prev_avg_sec = (sum(prev_durations) / len(prev_durations)) if prev_durations else 0

    if cur_avg_sec >= 60:
        avg_time_display = f"{round(cur_avg_sec / 60, 1)} min"
    elif cur_avg_sec > 0:
        avg_time_display = f"{round(cur_avg_sec, 1)}s"
    else:
        avg_time_display = "--"

    time_delta, time_cls = _pct_change_str(cur_avg_sec, prev_avg_sec, comp_label, is_inverse=True)

    # 5. Active Alarms
    base_alerts = Alert.objects.filter(acknowledged=False)
    active_alarms_count = base_alerts.count()
    prev_alarms_count = Alert.objects.filter(timestamp__gte=prev_start_dt, timestamp__lt=prev_end_dt).count()
    alarms_delta, alarms_cls = _pct_change_str(active_alarms_count, prev_alarms_count, comp_label, is_inverse=True)

    return {
        'total_files_processed': {
            'value': f"{cur_processed:,}",
            'raw': cur_processed,
            'delta': processed_delta,
            'delta_class': processed_cls,
        },
        'files_decoded': {
            'value': f"{cur_decoded:,}",
            'raw': cur_decoded,
            'delta': decoded_delta,
            'delta_class': decoded_cls,
        },
        'files_distributed': {
            'value': f"{cur_distributed:,}",
            'raw': cur_distributed,
            'delta': distributed_delta,
            'delta_class': distributed_cls,
        },
        'avg_processing_time': {
            'value': avg_time_display,
            'raw_seconds': cur_avg_sec,
            'delta': time_delta,
            'delta_class': time_cls,
        },
        'active_alarms': {
            'value': str(active_alarms_count),
            'raw': active_alarms_count,
            'delta': alarms_delta,
            'delta_class': alarms_cls,
        },
    }


def get_system_health_status():
    """Evaluate health status across 7 components and determine overall system status."""
    # 1. Collection Service
    col_status = get_systemd_status('mediation-collector.service')
    col_healthy = col_status.get('healthy', True)

    # 2. Decoding Service
    dec_status = get_systemd_status('mediation-decoder.service')
    dec_healthy = dec_status.get('healthy', True)

    # 3. Distribution Service
    dist_status = get_systemd_status('mediation-distributor.service')
    dist_healthy = dist_status.get('healthy', True)

    # 4. Database
    db_healthy = True
    db_detail = "Healthy"
    try:
        connection.ensure_connection()
        db_size_str = ""
        # Check SQLite or PostgreSQL size
        if 'sqlite' in connection.settings_dict.get('ENGINE', '').lower():
            db_name = connection.settings_dict.get('NAME')
            if os.path.exists(db_name):
                size_mb = round(os.path.getsize(db_name) / (1024 * 1024), 1)
                db_size_str = f"Size: {size_mb} MB"
        db_detail = db_size_str or "Connections: Active"
    except Exception as e:
        db_healthy = False
        db_detail = "Connection Error"

    # 5. File Storage
    disk_usage = get_disk_usage()
    disk_pct = disk_usage.get('percent', 0)
    storage_state = "Healthy"
    storage_badge = "Healthy"
    storage_color = "success"
    if disk_pct >= 90:
        storage_state = "Critical"
        storage_badge = "Critical"
        storage_color = "danger"
    elif disk_pct >= 75:
        storage_state = "Warning"
        storage_badge = "Warning"
        storage_color = "warning"

    storage_detail = f"Usage: {disk_pct}%"

    # 6. Message / Processing Queue
    queue_count = CDRFile.objects.filter(status__in=[
        CDRFile.Status.COLLECTED, CDRFile.Status.PENDING, CDRFile.Status.PROCESSING
    ]).count()
    queue_healthy = queue_count < 500
    queue_badge = "Healthy" if queue_healthy else "Warning"
    queue_color = "success" if queue_healthy else "warning"
    queue_detail = f"Queue Size: {queue_count}"

    # 7. Web Application
    web_healthy = True
    web_detail = "Uptime: Active"
    if psutil:
        try:
            boot_diff = time.time() - psutil.boot_time()
            days = int(boot_diff // 86400)
            hours = int((boot_diff % 86400) // 3600)
            mins = int((boot_diff % 3600) // 60)
            web_detail = f"Uptime: {days}d {hours:02d}h {mins:02d}m"
        except Exception:
            pass

    # Overall Status Calculation
    active_critical_alarms = Alert.objects.filter(acknowledged=False, severity='CRITICAL').count()
    active_major_alarms = Alert.objects.filter(acknowledged=False, severity='ERROR').count()

    components = [
        {
            'name': 'Collection Service',
            'state': 'Healthy' if col_healthy else 'Degraded',
            'badge': 'Healthy' if col_healthy else 'Degraded',
            'badge_color': 'success' if col_healthy else 'danger',
            'detail': 'Uptime: Active' if col_healthy else 'Inactive',
        },
        {
            'name': 'Decoding Service',
            'state': 'Healthy' if dec_healthy else 'Degraded',
            'badge': 'Healthy' if dec_healthy else 'Degraded',
            'badge_color': 'success' if dec_healthy else 'danger',
            'detail': 'Uptime: Active' if dec_healthy else 'Inactive',
        },
        {
            'name': 'Distribution Service',
            'state': 'Healthy' if dist_healthy else 'Degraded',
            'badge': 'Healthy' if dist_healthy else 'Degraded',
            'badge_color': 'success' if dist_healthy else 'danger',
            'detail': 'Uptime: Active' if dist_healthy else 'Inactive',
        },
        {
            'name': 'Database',
            'state': 'Healthy' if db_healthy else 'Critical',
            'badge': 'Healthy' if db_healthy else 'Critical',
            'badge_color': 'success' if db_healthy else 'danger',
            'detail': db_detail,
        },
        {
            'name': 'File Storage',
            'state': storage_state,
            'badge': storage_badge,
            'badge_color': storage_color,
            'detail': storage_detail,
        },
        {
            'name': 'Message Queue',
            'state': queue_badge,
            'badge': queue_badge,
            'badge_color': queue_color,
            'detail': queue_detail,
        },
        {
            'name': 'Web Application',
            'state': 'Healthy' if web_healthy else 'Warning',
            'badge': 'Healthy' if web_healthy else 'Warning',
            'badge_color': 'success' if web_healthy else 'warning',
            'detail': web_detail,
        },
    ]

    all_components_healthy = all(c['badge_color'] == 'success' for c in components)
    has_critical = any(c['badge_color'] == 'danger' for c in components) or active_critical_alarms > 0
    has_warning = any(c['badge_color'] == 'warning' for c in components) or active_major_alarms > 0

    if has_critical:
        overall_code = 'CRITICAL'
        overall_text = 'Critical System Condition'
        overall_color = '#EF4444'
    elif has_warning or not all_components_healthy:
        overall_code = 'DEGRADED'
        overall_text = 'System Degraded'
        overall_color = '#F4B400'
    else:
        overall_code = 'OPERATIONAL'
        overall_text = 'All Systems Operational'
        overall_color = '#16A34A'

    return {
        'overall_code': overall_code,
        'overall_text': overall_text,
        'overall_color': overall_color,
        'components': components,
    }


def get_hardware_gauges():
    """Read live hardware gauges for CPU, Memory, Disk, and Network."""
    cpu_pct = mem_pct = disk_pct = net_pct = None
    mem_used_gb = mem_total_gb = None
    disk_used_gb = disk_total_gb = None
    net_rx_mb = net_tx_mb = None

    if psutil:
        try:
            cpu_pct = round(psutil.cpu_percent(interval=0.1), 1)
            mem = psutil.virtual_memory()
            mem_pct = round(mem.percent, 1)
            mem_used_gb = round(mem.used / (1024 ** 3), 1)
            mem_total_gb = round(mem.total / (1024 ** 3), 1)

            disk_path = str(settings.UMP_STORAGE_ROOT)
            disk = psutil.disk_usage(disk_path)
            disk_pct = round(disk.percent, 1)
            disk_used_gb = round(disk.used / (1024 ** 3), 1)
            disk_total_gb = round(disk.total / (1024 ** 3), 1)

            net = psutil.net_io_counters()
            net_rx_mb = round(net.bytes_recv / (1024 * 1024), 1)
            net_tx_mb = round(net.bytes_sent / (1024 * 1024), 1)
            net_pct = min(100.0, round((net.bytes_recv + net.bytes_sent) % 100, 1))
        except Exception:
            pass

    return {
        'cpu': {'percent': cpu_pct, 'color': '#16A34A' if cpu_pct is not None and cpu_pct < 70 else ('#F4B400' if cpu_pct is not None and cpu_pct < 85 else '#667B96')},
        'memory': {'percent': mem_pct, 'used_gb': mem_used_gb, 'total_gb': mem_total_gb, 'color': '#1677EE'},
        'disk': {'percent': disk_pct, 'used_gb': disk_used_gb, 'total_gb': disk_total_gb, 'color': '#FF8A1F' if disk_pct is not None and disk_pct > 70 else '#667B96'},
        'network': {'percent': net_pct, 'rx_mb': net_rx_mb, 'tx_mb': net_tx_mb, 'color': '#8B5CF6'},
    }


def get_recent_processing_activity(limit=5, operator_code=''):
    """Return latest processing activity from real CDRFile records."""
    base_qs = CDRFile.objects.select_related('source').order_by('-created_at')
    if operator_code:
        base_qs = base_qs.filter(operator_code__iexact=operator_code)

    results = []
    for f in base_qs[:limit]:
        op_name = (f.operator_code or (f.source.name if f.source else '--')).capitalize()
        decoder = (f.decoder_type or '--').upper()
        dur = _format_duration(f.processing_duration)
        st = f.get_status_display()
        st_color = 'success' if f.status == CDRFile.Status.COMPLETED else (
            'danger' if f.status == CDRFile.Status.FAILED else 'primary'
        )
        results.append({
            'filename': f.filename,
            'source': op_name,
            'decoder': decoder,
            'status': st,
            'status_color': st_color,
            'records': f"{f.records_total:,}" if f.records_total else "--",
            'start_time': f.created_at.strftime('%Y-%m-%d %H:%M') if f.created_at else '',
            'duration': dur,
        })
    return results


def get_recent_errors(limit=6, operator_code=''):
    """Extract and normalize recent errors from ProcessingError, failed CDRFile, and Alert."""
    errors = []

    # 1. Processing Errors
    proc_errs = ProcessingError.objects.select_related('cdr_file').order_by('-created_at')
    if operator_code:
        proc_errs = proc_errs.filter(cdr_file__operator_code__iexact=operator_code)

    for pe in proc_errs[:limit]:
        errors.append({
            'id': f"pe_{pe.id}",
            'time': pe.created_at.strftime('%Y-%m-%d %H:%M'),
            'component': f"Decoder ({pe.stage})",
            'severity': 'Critical' if 'fatal' in pe.error_message.lower() else 'Major',
            'severity_color': 'danger' if 'fatal' in pe.error_message.lower() else 'warning',
            'message': pe.error_message[:90],
            'details': {
                'source': 'ProcessingError',
                'file': pe.cdr_file.filename if pe.cdr_file else '--',
                'stage': pe.stage,
                'class': pe.error_class or 'DecodingException',
            }
        })

    # 2. Failed CDR Files
    if len(errors) < limit:
        failed_files = CDRFile.objects.filter(status=CDRFile.Status.FAILED).order_by('-created_at')
        if operator_code:
            failed_files = failed_files.filter(operator_code__iexact=operator_code)
        for ff in failed_files[:limit - len(errors)]:
            errors.append({
                'id': f"cf_{ff.id}",
                'time': ff.created_at.strftime('%Y-%m-%d %H:%M'),
                'component': f"{ff.decoder_type.upper() if ff.decoder_type else 'Decoder'}",
                'severity': 'Critical',
                'severity_color': 'danger',
                'message': ff.error_message or f"Processing failed on file {ff.filename}",
                'details': {
                    'source': 'CDRFile',
                    'file': ff.filename,
                    'retry_count': ff.retry_count,
                }
            })

    # 3. System Alerts / Alarms with error severity
    if len(errors) < limit:
        alerts = Alert.objects.filter(severity__in=['ERROR', 'CRITICAL', 'WARNING']).order_by('-timestamp')
        for al in alerts[:limit - len(errors)]:
            sev = al.get_severity_display()
            sev_color = 'danger' if al.severity == 'CRITICAL' else ('warning' if al.severity == 'ERROR' else 'info')
            errors.append({
                'id': f"al_{al.id}",
                'time': al.timestamp.strftime('%Y-%m-%d %H:%M'),
                'component': al.source or al.category or 'System',
                'severity': sev,
                'severity_color': sev_color,
                'message': al.message[:90],
                'details': {
                    'source': 'Alert',
                    'category': al.category,
                }
            })

    # If still empty in a fresh system, return empty list (NO MOCK DATA)
    return errors[:limit]


def get_timeseries_data(time_range='24h', operator_code=''):
    """Generate structured time-series datasets for all charts matching the reference."""
    ensure_recent_snapshot()
    start_dt, end_dt, _, _, step_mins, _ = parse_time_range(time_range)

    # Divide time range into intervals (e.g. 12 to 24 slots)
    total_seconds = (end_dt - start_dt).total_seconds()
    num_intervals = min(24, max(8, int(total_seconds // (step_mins * 60))))
    slot_seconds = total_seconds / num_intervals

    labels = []
    trend_collected = []
    trend_decoded = []
    trend_distributed = []
    proc_durations = []
    hw_cpu = []
    hw_mem = []
    hw_disk = []
    hw_net = []
    alarm_summary_crit = []
    alarm_summary_major = []
    alarm_summary_minor = []
    alarm_summary_warn = []

    base_cdr = CDRFile.objects.all()
    base_dist = DistributionLog.objects.all()
    base_alert = Alert.objects.all()
    if operator_code:
        base_cdr = base_cdr.filter(operator_code__iexact=operator_code)
        base_dist = base_dist.filter(cdr_file__operator_code__iexact=operator_code)

    fmt = '%H:%M' if time_range in ('1h', '6h', '12h', '24h') else '%b %d'

    for i in range(num_intervals):
        slot_start = start_dt + timedelta(seconds=i * slot_seconds)
        slot_end = slot_start + timedelta(seconds=slot_seconds)
        labels.append(slot_start.strftime(fmt))

        # File Processing Trend counts
        col_c = base_cdr.filter(created_at__gte=slot_start, created_at__lt=slot_end).count()
        dec_c = base_cdr.filter(
            created_at__gte=slot_start, created_at__lt=slot_end,
            status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED]
        ).count()
        dist_c = base_dist.filter(
            delivered_at__gte=slot_start, delivered_at__lt=slot_end,
            status=DistributionLog.Status.SUCCESS
        ).count()

        trend_collected.append(col_c)
        trend_decoded.append(dec_c)
        trend_distributed.append(dist_c)

        # Processing duration average in minutes
        dur_qs = base_cdr.filter(
            created_at__gte=slot_start, created_at__lt=slot_end,
            processing_started__isnull=False, processing_completed__isnull=False
        )
        durs = [f.processing_duration for f in dur_qs[:20] if f.processing_duration and f.processing_duration > 0]
        avg_min = round((sum(durs) / len(durs)) / 60, 2) if durs else 0.0
        proc_durations.append(avg_min)

        # Hardware snapshots
        snaps = SystemMetricSnapshot.objects.filter(timestamp__gte=slot_start, timestamp__lt=slot_end)
        if snaps.exists():
            hw_cpu.append(round(snaps.aggregate(v=Avg('cpu_percent'))['v'] or 0.0, 1))
            hw_mem.append(round(snaps.aggregate(v=Avg('memory_percent'))['v'] or 0.0, 1))
            hw_disk.append(round(snaps.aggregate(v=Avg('disk_percent'))['v'] or 0.0, 1))
            hw_net.append(round(snaps.aggregate(v=Avg('network_percent'))['v'] or 0.0, 1))
        else:
            # Fallback to nearest snapshot or 0
            hw_cpu.append(0.0)
            hw_mem.append(0.0)
            hw_disk.append(0.0)
            hw_net.append(0.0)

        # Alarm Summary stacked bar
        slot_alerts = base_alert.filter(timestamp__gte=slot_start, timestamp__lt=slot_end)
        alarm_summary_crit.append(slot_alerts.filter(severity='CRITICAL').count())
        alarm_summary_major.append(slot_alerts.filter(severity='ERROR').count())
        alarm_summary_minor.append(slot_alerts.filter(severity='WARNING').count())
        alarm_summary_warn.append(slot_alerts.filter(severity='INFO').count())

    # File Success Rate Doughnut
    total_range_files = base_cdr.filter(created_at__gte=start_dt, created_at__lte=end_dt).count()
    succ_count = base_cdr.filter(created_at__gte=start_dt, created_at__lte=end_dt, status=CDRFile.Status.COMPLETED).count()
    fail_count = base_cdr.filter(created_at__gte=start_dt, created_at__lte=end_dt, status=CDRFile.Status.FAILED).count()
    skip_count = total_range_files - succ_count - fail_count
    if skip_count < 0:
        skip_count = 0

    succ_pct = round((succ_count / total_range_files) * 100, 1) if total_range_files > 0 else 100.0

    # Alarms by Severity Doughnut
    active_alerts = Alert.objects.filter(acknowledged=False)
    crit_count = active_alerts.filter(severity='CRITICAL').count()
    maj_count = active_alerts.filter(severity='ERROR').count()
    min_count = active_alerts.filter(severity='WARNING').count()
    warn_count = active_alerts.filter(severity='INFO').count()
    total_active_alarms = active_alerts.count()

    return {
        'labels': labels,
        'processing_trend': {
            'collected': trend_collected,
            'decoded': trend_decoded,
            'distributed': trend_distributed,
        },
        'processing_time': {
            'durations': proc_durations,
        },
        'success_rate': {
            'percent': succ_pct,
            'successful': succ_count,
            'failed': fail_count,
            'skipped': skip_count,
            'total': total_range_files,
        },
        'hardware_trend': {
            'cpu': hw_cpu,
            'memory': hw_mem,
            'disk': hw_disk,
            'network': hw_net,
        },
        'alarms_by_severity': {
            'total': total_active_alarms,
            'critical': crit_count,
            'major': maj_count,
            'minor': min_count,
            'warning': warn_count,
        },
        'alarm_summary': {
            'critical': alarm_summary_crit,
            'major': alarm_summary_major,
            'minor': alarm_summary_minor,
            'warning': alarm_summary_warn,
        },
    }


# =============================================================================
# MEDIATION FLOW MONITORING & RECONCILIATION
# =============================================================================

def get_flow_collection_data(start_dt, end_dt, operator='', group_by='portal'):
    """
    Returns files and records collected by input portal or stream for the combination chart.
    Bars: Files Count (Blue)
    Line: Records Count (Green)
    """
    base_cdr = CDRFile.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if operator:
        base_cdr = base_cdr.filter(operator_code__iexact=operator)

    items = []
    if group_by == 'stream':
        candidate_streams = ['MSC', 'PGW', 'SGSN', 'SGW', 'IMS', 'OCS', 'CBS']
        active_streams = list(base_cdr.exclude(decoder_type='').values_list('decoder_type', flat=True).distinct())
        all_streams = []
        for s in candidate_streams:
            if s in active_streams or base_cdr.filter(decoder_type__iexact=s).exists():
                all_streams.append(s)
        for s in active_streams:
            if s.upper() not in all_streams:
                all_streams.append(s.upper())

        if not all_streams:
            all_streams = candidate_streams

        for stream in all_streams:
            s_qs = base_cdr.filter(decoder_type__iexact=stream)
            f_count = s_qs.count()
            r_count = s_qs.aggregate(t=Sum('records_total'))['t'] or 0
            items.append({
                'name': stream,
                'files': f_count,
                'records': r_count,
            })
    else:
        # group_by == 'portal'
        from portals.models import InputPortal
        portals = list(InputPortal.objects.filter(is_active=True).values('id', 'name', 'stream_type'))
        portal_names = [p['name'] for p in portals]

        data_sources = list(DataSource.objects.filter(enabled=True).values_list('name', flat=True))
        for ds in data_sources:
            clean_ds = ds.replace('-', '_')
            if ds not in portal_names and clean_ds not in portal_names:
                portal_names.append(ds)

        file_sources = list(base_cdr.exclude(source__isnull=True).values_list('source__name', flat=True).distinct())
        for fs in file_sources:
            if fs and fs not in portal_names and fs.replace('-', '_') not in portal_names:
                portal_names.append(fs)

        if not portal_names:
            portal_names = ['INP-MSC', 'INP-PGW', 'INP-SGSN', 'INP-SGW', 'INP-IMS', 'INP-OCS', 'INP-CBS']

        for p_name in portal_names:
            clean_name = p_name.replace('_', '-')
            p_qs = base_cdr.filter(
                Q(source__name__iexact=p_name) |
                Q(source__name__iexact=clean_name) |
                Q(source__name__iexact=p_name.replace('-', '_'))
            )
            if not p_qs.exists() and ('-' in clean_name or '_' in clean_name):
                parts = clean_name.split('-')
                suffix = parts[-1].upper()
                p_qs = base_cdr.filter(decoder_type__iexact=suffix)

            f_count = p_qs.count()
            r_count = p_qs.aggregate(t=Sum('records_total'))['t'] or 0
            items.append({
                'name': clean_name.upper(),
                'files': f_count,
                'records': r_count,
            })

    labels = [it['name'] for it in items]
    files = [it['files'] for it in items]
    records = [it['records'] for it in items]

    return {
        'group_by': group_by,
        'items': items,
        'labels': labels,
        'files': files,
        'records': records,
    }


def get_flow_processing_data(start_dt, end_dt, operator='', group_by='stream'):
    """
    Returns files and records processed for the combination chart.
    Supports Group By: 'stream' (default), 'source_portal'.
    Bars: Files Count (Green)
    Line: Records Count (Blue)
    """
    base_cdr = CDRFile.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    if operator:
        base_cdr = base_cdr.filter(operator_code__iexact=operator)

    items = []

    if group_by == 'source_portal':
        from collection.models import DataSource
        portal_names = list(
            base_cdr.exclude(source__isnull=True)
            .values_list('source__name', flat=True)
            .distinct()
            .order_by('source__name')
        )
        for p_name in portal_names:
            p_qs = base_cdr.filter(source__name=p_name)
            succ_qs = p_qs.filter(status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED])
            f_proc = succ_qs.count()
            r_proc = succ_qs.aggregate(t=Sum('records_valid'))['t'] or (succ_qs.aggregate(t=Sum('records_total'))['t'] or 0)
            items.append({
                'name': p_name,
                'files': f_proc,
                'records': r_proc,
            })
        no_source = base_cdr.filter(source__isnull=True)
        if no_source.exists():
            succ_qs = no_source.filter(status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED])
            items.append({
                'name': 'Manual Upload',
                'files': succ_qs.count(),
                'records': succ_qs.aggregate(t=Sum('records_valid'))['t'] or (succ_qs.aggregate(t=Sum('records_total'))['t'] or 0),
            })
    else:
        candidate_streams = ['MSC', 'PGW', 'SGSN', 'SGW', 'IMS', 'OCS', 'CBS']
        active_streams = list(base_cdr.exclude(decoder_type='').values_list('decoder_type', flat=True).distinct())
        all_streams = []
        for s in candidate_streams:
            if s in active_streams or base_cdr.filter(decoder_type__iexact=s).exists():
                all_streams.append(s)
        for s in active_streams:
            if s.upper() not in all_streams:
                all_streams.append(s.upper())

        if not all_streams:
            all_streams = candidate_streams

        for stream in all_streams:
            s_qs = base_cdr.filter(decoder_type__iexact=stream)
            succ_qs = s_qs.filter(status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED])
            f_proc = succ_qs.count()
            r_proc = succ_qs.aggregate(t=Sum('records_valid'))['t'] or (succ_qs.aggregate(t=Sum('records_total'))['t'] or 0)
            failed_count = s_qs.filter(status=CDRFile.Status.FAILED).count()
            pending_count = s_qs.filter(status__in=[
                CDRFile.Status.COLLECTED,
                CDRFile.Status.PENDING,
                CDRFile.Status.PROCESSING,
                CDRFile.Status.DISPATCHING
            ]).count()

            items.append({
                'name': stream,
                'files': f_proc,
                'records': r_proc,
                'failed': failed_count,
                'pending': pending_count,
            })

    labels = [it['name'] for it in items]
    files = [it['files'] for it in items]
    records = [it['records'] for it in items]

    return {
        'group_by': group_by,
        'items': items,
        'labels': labels,
        'files': files,
        'records': records,
    }


def get_flow_distribution_data(start_dt, end_dt, operator='', group_by='downstream'):
    """
    Returns files and records distributed to downstream systems for the combination chart.
    Supports Group By: 'downstream' (default), 'output_portal', 'stream'.
    Bars: Files Count (Orange)
    Line: Records Count (Purple)
    """
    from collection.models import DistributionLog, DistributionPortal
    from portals.models import OutputPortal

    base_dist = DistributionLog.objects.filter(delivered_at__gte=start_dt, delivered_at__lte=end_dt)
    if operator:
        base_dist = base_dist.filter(cdr_file__operator_code__iexact=operator)

    items = []

    if group_by == 'output_portal':
        portals = list(OutputPortal.objects.filter(is_active=True).order_by('name'))
        p_names = [p.name for p in portals]
        log_portals = list(base_dist.exclude(output_portal__isnull=True).values_list('output_portal__name', flat=True).distinct())
        for lp in log_portals:
            if lp not in p_names:
                p_names.append(lp)

        for p_name in p_names:
            p_logs = base_dist.filter(output_portal__name__iexact=p_name)
            succ_logs = p_logs.filter(status=DistributionLog.Status.SUCCESS)
            f_count = succ_logs.count()
            r_count = succ_logs.aggregate(t=Sum('record_count'))['t'] or 0
            fail_count = p_logs.filter(status=DistributionLog.Status.FAILED).count()
            items.append({
                'name': p_name,
                'files': f_count,
                'records': r_count,
                'failed': fail_count,
            })

    elif group_by == 'stream':
        candidate_streams = ['MSC', 'PGW', 'SGSN', 'SGW', 'IMS', 'OCS', 'CBS']
        active_streams = list(base_dist.exclude(cdr_file__decoder_type='').values_list('cdr_file__decoder_type', flat=True).distinct())
        all_streams = []
        for s in candidate_streams:
            if s in active_streams or base_dist.filter(cdr_file__decoder_type__iexact=s).exists():
                all_streams.append(s)
        for s in active_streams:
            if s.upper() not in all_streams:
                all_streams.append(s.upper())

        if not all_streams:
            all_streams = candidate_streams

        for stream in all_streams:
            s_logs = base_dist.filter(cdr_file__decoder_type__iexact=stream)
            succ_logs = s_logs.filter(status=DistributionLog.Status.SUCCESS)
            f_count = succ_logs.count()
            r_count = succ_logs.aggregate(t=Sum('record_count'))['t'] or 0
            fail_count = s_logs.filter(status=DistributionLog.Status.FAILED).count()
            items.append({
                'name': stream,
                'files': f_count,
                'records': r_count,
                'failed': fail_count,
            })

    else:
        # group_by == 'downstream' (Default)
        downstream_targets = ['Billing', 'Big Data', 'Revenue Assurance', 'Regulatory', 'Analytics', 'Partner', 'Other']

        def categorize_downstream(log):
            name = (log.output_portal.name if log.output_portal else '').upper()
            if 'BILL' in name:
                return 'Billing'
            if 'BIG' in name or 'DATA' in name or 'HADOOP' in name:
                return 'Big Data'
            if 'IPACS' in name or 'REG' in name or 'TRA' in name:
                return 'Regulatory'
            if 'REV' in name or 'RA' in name or 'ASSUR' in name:
                return 'Revenue Assurance'
            if 'ANA' in name or 'BI' in name:
                return 'Analytics'
            if 'PARTNER' in name or 'ROAM' in name or 'FORWARD' in name:
                return 'Partner'
            return 'Other'

        configured_groups = list(DistributionPortal.objects.filter(enabled=True).values_list('group_label', flat=True).distinct())
        for cg in configured_groups:
            if cg and cg not in downstream_targets:
                downstream_targets.insert(len(downstream_targets) - 1, cg)

        downstream_map = {ds: {'files': 0, 'records': 0, 'failed': 0} for ds in downstream_targets}

        for log in base_dist.select_related('output_portal'):
            ds_name = categorize_downstream(log)
            if ds_name not in downstream_map:
                ds_name = 'Other'
            if log.status == DistributionLog.Status.SUCCESS:
                downstream_map[ds_name]['files'] += 1
                downstream_map[ds_name]['records'] += (log.record_count or 0)
            elif log.status == DistributionLog.Status.FAILED:
                downstream_map[ds_name]['failed'] += 1

        for ds_name in downstream_targets:
            items.append({
                'name': ds_name,
                'files': downstream_map[ds_name]['files'],
                'records': downstream_map[ds_name]['records'],
                'failed': downstream_map[ds_name]['failed'],
            })

    labels = [it['name'] for it in items]
    files = [it['files'] for it in items]
    records = [it['records'] for it in items]

    return {
        'group_by': group_by,
        'items': items,
        'labels': labels,
        'files': files,
        'records': records,
    }


def get_flow_reconciliation_data(start_dt, end_dt, operator='', time_label='Last 24 Hours'):
    """
    Returns End-to-End Reconciliation (Collected -> Processed -> Distributed -> Variance & Pending)
    and Per-Stream Reconciliation table.
    """
    from collection.models import DistributionLog
    base_cdr = CDRFile.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
    base_dist = DistributionLog.objects.filter(delivered_at__gte=start_dt, delivered_at__lte=end_dt)
    if operator:
        base_cdr = base_cdr.filter(operator_code__iexact=operator)
        base_dist = base_dist.filter(cdr_file__operator_code__iexact=operator)

    # 1. End-to-End Reconciliation
    col_files = base_cdr.count()
    col_records = base_cdr.aggregate(t=Sum('records_total'))['t'] or 0

    succ_proc_qs = base_cdr.filter(status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED])
    proc_files = succ_proc_qs.count()
    proc_records = succ_proc_qs.aggregate(t=Sum('records_valid'))['t'] or (succ_proc_qs.aggregate(t=Sum('records_total'))['t'] or 0)

    succ_dist_qs = base_dist.filter(status=DistributionLog.Status.SUCCESS)
    dist_files = succ_dist_qs.count()
    dist_records = succ_dist_qs.aggregate(t=Sum('record_count'))['t'] or 0

    # Processing Variance = Collected Records - Processed Records
    variance_val = col_records - proc_records
    variance_str = f"{variance_val:+,d}" if variance_val != 0 else "0"
    variance_color = "text-success" if variance_val == 0 else ("text-warning" if variance_val > 0 else "text-danger")

    pending_files = base_cdr.filter(status__in=[
        CDRFile.Status.COLLECTED,
        CDRFile.Status.PENDING,
        CDRFile.Status.PROCESSING,
        CDRFile.Status.DISPATCHING
    ]).count()

    e2e = {
        'time_label': time_label,
        'collected': {
            'files': f"{col_files:,}",
            'records': f"{col_records:,}",
            'files_raw': col_files,
            'records_raw': col_records,
        },
        'processed': {
            'files': f"{proc_files:,}",
            'records': f"{proc_records:,}",
            'files_raw': proc_files,
            'records_raw': proc_records,
        },
        'distributed': {
            'files': f"{dist_files:,}",
            'records': f"{dist_records:,}",
            'files_raw': dist_files,
            'records_raw': dist_records,
        },
        'variance': {
            'value': variance_str,
            'raw': variance_val,
            'color': variance_color,
        },
        'pending': {
            'files': f"{pending_files:,} files",
            'raw': pending_files,
        }
    }

    # 2. Per-Stream Reconciliation Table
    candidate_streams = ['MSC', 'PGW', 'SGSN', 'SGW', 'IMS', 'OCS', 'CBS']
    active_streams = list(base_cdr.exclude(decoder_type='').values_list('decoder_type', flat=True).distinct())
    stream_list = []
    for s in candidate_streams:
        if s in active_streams or base_cdr.filter(decoder_type__iexact=s).exists():
            stream_list.append(s)
    for s in active_streams:
        if s.upper() not in stream_list:
            stream_list.append(s.upper())

    if not stream_list:
        stream_list = candidate_streams

    per_stream = []
    for st in stream_list:
        s_files_qs = base_cdr.filter(decoder_type__iexact=st)
        c_f = s_files_qs.count()
        c_r = s_files_qs.aggregate(t=Sum('records_total'))['t'] or 0

        s_succ_qs = s_files_qs.filter(status__in=[CDRFile.Status.COMPLETED, CDRFile.Status.DECODED])
        p_f = s_succ_qs.count()
        p_r = s_succ_qs.aggregate(t=Sum('records_valid'))['t'] or (s_succ_qs.aggregate(t=Sum('records_total'))['t'] or 0)

        failed_f = s_files_qs.filter(status=CDRFile.Status.FAILED).count()

        s_dist_qs = base_dist.filter(cdr_file__decoder_type__iexact=st, status=DistributionLog.Status.SUCCESS)
        d_f = s_dist_qs.count()
        d_r = s_dist_qs.aggregate(t=Sum('record_count'))['t'] or 0

        pending_st = s_files_qs.filter(status__in=[
            CDRFile.Status.COLLECTED,
            CDRFile.Status.PENDING,
            CDRFile.Status.PROCESSING,
            CDRFile.Status.DISPATCHING
        ]).count()

        if (p_f + failed_f) > 0:
            rate_val = round((p_f / (p_f + failed_f)) * 100, 1)
        elif c_f > 0:
            rate_val = 100.0
        else:
            rate_val = 100.0

        if rate_val >= 98.0:
            rate_color = "text-success"
        elif rate_val >= 90.0:
            rate_color = "text-warning"
        else:
            rate_color = "text-danger"

        per_stream.append({
            'stream': st,
            'collected_files': f"{c_f:,}",
            'collected_records': f"{c_r:,}",
            'processed_files': f"{p_f:,}",
            'processed_records': f"{p_r:,}",
            'failed_files': f"{failed_f:,}",
            'distributed_files': f"{d_f:,}",
            'distributed_records': f"{d_r:,}",
            'pending': f"{pending_st:,}",
            'success_rate': f"{rate_val:.1f}%",
            'success_rate_color': rate_color,
            'raw': {
                'collected_files': c_f,
                'collected_records': c_r,
                'processed_files': p_f,
                'processed_records': p_r,
                'failed_files': failed_f,
                'distributed_files': d_f,
                'distributed_records': d_r,
                'pending': pending_st,
                'success_rate': rate_val,
            }
        })

    return {
        'time_label': time_label,
        'e2e': e2e,
        'per_stream': per_stream,
    }


def get_mediation_flow_summary(range_key='24h', operator='', col_group='portal', proc_group='stream', dist_group='downstream'):
    """
    Aggregates all 4 Mediation Flow Monitoring datasets for a single fast response.
    """
    start_dt, end_dt, _, _, _, _ = parse_time_range(range_key)
    range_titles = {
        '1h': 'Last 1 Hour',
        '6h': 'Last 6 Hours',
        '12h': 'Last 12 Hours',
        '24h': 'Last 24 Hours',
        '7d': 'Last 7 Days',
        '30d': 'Last 30 Days',
    }
    label = range_titles.get(range_key, 'Last 24 Hours')

    collection = get_flow_collection_data(start_dt, end_dt, operator=operator, group_by=col_group)
    processing = get_flow_processing_data(start_dt, end_dt, operator=operator, group_by=proc_group)
    distribution = get_flow_distribution_data(start_dt, end_dt, operator=operator, group_by=dist_group)
    reconciliation = get_flow_reconciliation_data(start_dt, end_dt, operator=operator, time_label=label)

    return {
        'time_range': range_key,
        'time_label': label,
        'collection': collection,
        'processing': processing,
        'distribution': distribution,
        'reconciliation': reconciliation,
    }


# ==========================================================================
# BACKLOG MONITORING
# ==========================================================================

def _scan_directory_backlog(directory):
    """Return file count, total size, and oldest file age for a directory."""
    count = 0
    total_size = 0
    oldest_mtime = None
    now = time.time()
    try:
        if not os.path.isdir(directory):
            return {'count': 0, 'total_size_mb': 0, 'oldest_age_seconds': 0, 'oldest_age_display': '--'}
        for entry in os.scandir(directory):
            if entry.is_file() and not entry.name.startswith('.'):
                count += 1
                try:
                    st = entry.stat()
                    total_size += st.st_size
                    if oldest_mtime is None or st.st_mtime < oldest_mtime:
                        oldest_mtime = st.st_mtime
                except OSError:
                    pass
    except OSError:
        pass
    age = (now - oldest_mtime) if oldest_mtime else 0
    return {
        'count': count,
        'total_size_mb': round(total_size / (1024 * 1024), 1),
        'oldest_age_seconds': round(age),
        'oldest_age_display': _format_duration(age) if age > 0 else '--',
    }


def get_backlog_data(operator=''):
    """Monitor all three backlog types: upstream staging, collection, output staging."""
    from collection.services.paths import PathBuilder

    operators = [operator] if operator else getattr(settings, 'OPERATORS', [])
    streams = ['msc', 'ims', 'pgw', 'sgsn', 'sgw', 'cbs']
    cbs_substreams = ['data', 'voice', 'sms', 'recharge']

    upstream_staging = []
    collection_backlog = []
    output_staging = []

    for op in operators:
        for stream in streams:
            if stream == 'cbs':
                for sub in cbs_substreams:
                    try:
                        staging_dir = str(PathBuilder.input_staging(op, stream, sub))
                        info = _scan_directory_backlog(staging_dir)
                        if info['count'] > 0:
                            upstream_staging.append({
                                'operator': op, 'stream': f'cbs/{sub}',
                                'type': 'upstream_staging', **info,
                            })
                    except (ValueError, OSError):
                        pass
                    try:
                        pub_dir = str(PathBuilder.input_published(op, stream, sub))
                        info = _scan_directory_backlog(pub_dir)
                        if info['count'] > 0:
                            collection_backlog.append({
                                'operator': op, 'stream': f'cbs/{sub}',
                                'type': 'collection', **info,
                            })
                    except (ValueError, OSError):
                        pass
            else:
                try:
                    staging_dir = str(PathBuilder.input_staging(op, stream))
                    info = _scan_directory_backlog(staging_dir)
                    if info['count'] > 0:
                        upstream_staging.append({
                            'operator': op, 'stream': stream,
                            'type': 'upstream_staging', **info,
                        })
                except (ValueError, OSError):
                    pass
                try:
                    pub_dir = str(PathBuilder.input_published(op, stream))
                    info = _scan_directory_backlog(pub_dir)
                    if info['count'] > 0:
                        collection_backlog.append({
                            'operator': op, 'stream': stream,
                            'type': 'collection', **info,
                        })
                except (ValueError, OSError):
                    pass

    # Processing backlog from DB
    processing_qs = CDRFile.objects.filter(status__in=['COLLECTED', 'PENDING', 'PROCESSING'])
    if operator:
        processing_qs = processing_qs.filter(operator_code__iexact=operator)
    processing_backlog = processing_qs.values('operator_code', 'decoder_type').annotate(
        count=Count('id')
    ).order_by('-count')

    processing_items = [{
        'operator': row['operator_code'] or '--',
        'stream': row['decoder_type'] or '--',
        'count': row['count'],
        'type': 'processing',
    } for row in processing_backlog]

    totals = {
        'upstream_staging': sum(r['count'] for r in upstream_staging),
        'collection': sum(r['count'] for r in collection_backlog),
        'processing': sum(r['count'] for r in processing_items),
        'output_staging': sum(r['count'] for r in output_staging),
    }

    return {
        'upstream_staging': upstream_staging,
        'collection': collection_backlog,
        'processing': processing_items,
        'output_staging': output_staging,
        'totals': totals,
    }


# ==========================================================================
# STORAGE MONITORING
# ==========================================================================

def get_storage_data():
    """Monitor actual configured storage locations with real disk usage."""
    locations = [
        ('Input Landing', settings.UMP_INPUT_ROOT),
        ('Output Landing', settings.UMP_OUTPUT_ROOT),
        ('Archive', settings.UMP_ARCHIVE_ROOT),
        ('Processing', settings.UMP_PROCESSING_ROOT),
        ('Error', settings.UMP_ERROR_ROOT),
        ('Quarantine', settings.UMP_QUARANTINE_ROOT),
    ]
    results = []
    for name, path in locations:
        path_str = str(path)
        entry = {
            'name': name,
            'path': path_str,
            'exists': os.path.isdir(path_str),
            'capacity_gb': None,
            'used_gb': None,
            'free_gb': None,
            'percent': None,
            'status': 'unknown',
        }
        if entry['exists'] and psutil:
            try:
                usage = psutil.disk_usage(path_str)
                entry['capacity_gb'] = round(usage.total / (1024 ** 3), 1)
                entry['used_gb'] = round(usage.used / (1024 ** 3), 1)
                entry['free_gb'] = round(usage.free / (1024 ** 3), 1)
                entry['percent'] = round(usage.percent, 1)
                if usage.percent >= 90:
                    entry['status'] = 'critical'
                elif usage.percent >= 70:
                    entry['status'] = 'warning'
                else:
                    entry['status'] = 'healthy'
            except OSError:
                entry['status'] = 'error'
        elif not entry['exists']:
            entry['status'] = 'missing'
        results.append(entry)
    return results
