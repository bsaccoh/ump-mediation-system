"""Service control."""
from django.conf import settings

from dashboard.views._common import *


# =============================================================================
# SERVICE CONTROL
# =============================================================================

MANAGED_SERVICES = [
    {
        'id': 'mediation-collector',
        'unit': 'mediation-collector.service',
        'display_unit': 'mediation-collector',
        'name': 'Collection',
        'type_code': 'collector',
        'description': 'Scans input directories and registers new CDR files for processing.',
        'icon': 'bi-folder-symlink-fill',
        'accent': '#1677ee',
        'config_title': 'Collection Configuration',
        'config_link': '/collection/',
        'config_link_text': 'View Data Sources',
    },
    {
        'id': 'mediation-decoder',
        'unit': 'mediation-decoder.service',
        'display_unit': 'mediation-decoder',
        'name': 'Decoding',
        'type_code': 'decoder',
        'description': 'Picks up collected files, decodes CDR records, and renders output.',
        'icon': 'bi-cpu-fill',
        'accent': '#16a34a',
        'config_title': 'Decoding Configuration',
        'config_link': '/reference/source-patterns/',
        'config_link_text': 'View Source Patterns',
    },
    {
        'id': 'mediation-distributor',
        'unit': 'mediation-distributor.service',
        'display_unit': 'mediation-distributor',
        'name': 'Distribution',
        'type_code': 'distributor',
        'description': 'Dispatches decoded files to configured output portals.',
        'icon': 'bi-send-fill',
        'accent': '#ff9b28',
        'config_title': 'Distribution Configuration',
        'config_link': '/portals/output-portal/',
        'config_link_text': 'View Output Portals',
    },
]


def _format_time_ago(dt):
    """Return a human-readable time-ago string matching reference design."""
    if not dt:
        return 'No recent activity'
    diff = (timezone.now() - dt).total_seconds()
    if diff < 0 or diff < 60:
        return 'just now'
    minutes = int(diff // 60)
    if minutes == 1:
        return '1 minute ago'
    if minutes < 60:
        return f'{minutes} minutes ago'
    hours = int(diff // 3600)
    if hours == 1:
        return '1 hour ago'
    if hours < 24:
        return f'{hours} hours ago'
    days = int(diff // 86400)
    if days == 1:
        return '1 day ago'
    return f'{days} days ago'


def _format_uptime(seconds):
    """Format duration in seconds into human-readable uptime string."""
    if not seconds or seconds < 0:
        return 'Recently started'
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _service_status(service_id):
    """Query systemd for a service's active and enabled state, PID and uptime."""
    import os
    import shutil
    import subprocess
    import time
    from datetime import datetime

    result = {
        'id': service_id,
        'active': 'unknown',
        'enabled': 'unknown',
        'uptime': 'Active',
        'pid': 'N/A',
        'status_label': 'Unknown',
        'status_color': 'unknown',
    }

    # If on Linux with systemctl
    if shutil.which('systemctl'):
        try:
            r = subprocess.run(
                ['sudo', 'systemctl', 'is-active', service_id],
                capture_output=True, text=True, timeout=3,
            )
            result['active'] = r.stdout.strip().lower() or 'unknown'
        except Exception:
            logger.debug("systemctl is-active check failed for %s", service_id, exc_info=True)

        try:
            r = subprocess.run(
                ['sudo', 'systemctl', 'is-enabled', service_id],
                capture_output=True, text=True, timeout=3,
            )
            result['enabled'] = r.stdout.strip().lower() or 'unknown'
        except Exception:
            logger.debug("systemctl is-enabled check failed for %s", service_id, exc_info=True)

        if result['active'] == 'active':
            try:
                r = subprocess.run(
                    ['systemctl', 'show', service_id, '--property=ActiveEnterTimestamp,MainPID'],
                    capture_output=True, text=True, timeout=3,
                )
                props = {}
                for line in r.stdout.splitlines():
                    if '=' in line:
                        k, v = line.split('=', 1)
                        props[k.strip()] = v.strip()
                if 'MainPID' in props and props['MainPID'] != '0':
                    result['pid'] = props['MainPID']
                if 'ActiveEnterTimestamp' in props and props['ActiveEnterTimestamp']:
                    try:
                        from dateutil import parser
                        dt = parser.parse(props['ActiveEnterTimestamp'])
                        diff = (timezone.now() - dt).total_seconds()
                        if diff >= 0:
                            result['uptime'] = _format_uptime(diff)
                    except Exception:
                        logger.debug("Could not parse uptime timestamp", exc_info=True)
            except Exception:
                logger.debug("systemctl show failed for %s", service_id, exc_info=True)
    else:
        result['active'] = 'unavailable'
        result['uptime'] = '—'

    status_raw = result['active']
    if status_raw in ('active', 'running'):
        result['status_label'] = 'Running'
        result['status_color'] = 'running'
    elif status_raw in ('inactive', 'stopped'):
        result['status_label'] = 'Stopped'
        result['status_color'] = 'stopped'
    elif status_raw == 'failed':
        result['status_label'] = 'Failed'
        result['status_color'] = 'failed'
    elif status_raw == 'degraded':
        result['status_label'] = 'Degraded'
        result['status_color'] = 'warning'
    elif status_raw in ('activating', 'starting'):
        result['status_label'] = 'Starting'
        result['status_color'] = 'warning'
    elif status_raw in ('deactivating', 'stopping'):
        result['status_label'] = 'Stopping'
        result['status_color'] = 'warning'
    elif status_raw == 'unavailable':
        result['status_label'] = 'Unavailable'
        result['status_color'] = 'unknown'
    else:
        result['status_label'] = 'Unknown'
        result['status_color'] = 'unknown'

    return result


def _get_service_operational_data(service_id):
    """Retrieve operational database metrics for a mediation service."""
    from django.db.models import Sum
    today = timezone.now().date()
    if service_id == 'mediation-collector':
        today_files = CDRFile.objects.filter(created_at__date=today)
        processed_total = today_files.count()
        last_file = CDRFile.objects.order_by('-created_at').first()
        last_act = _format_time_ago(last_file.created_at) if last_file else 'No activity'
        return {
            'metric_label': 'Processed Files',
            'metric_value': f"{processed_total:,}",
            'last_activity': last_act,
            'metrics': [
                {'label': 'Files Detected', 'value': f'{processed_total:,}', 'icon': 'bi-file-earmark-text', 'tone': 'blue'},
                {'label': 'Files Processed', 'value': f"{today_files.exclude(status=CDRFile.Status.FAILED).count():,}", 'icon': 'bi-check-circle-fill', 'tone': 'green'},
                {'label': 'Files Failed', 'value': f"{today_files.filter(status=CDRFile.Status.FAILED).count():,}", 'icon': 'bi-exclamation-circle-fill', 'tone': 'red'},
            ], 'context_label': 'Input Directory', 'context_value': DataSource.objects.filter(enabled=True).values_list('local_path', flat=True).exclude(local_path='').first() or 'Not configured',
        }
    elif service_id == 'mediation-decoder':
        processed_total = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED).count()
        last_file = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED).order_by('-processing_completed').first()
        last_act = _format_time_ago(last_file.processing_completed) if last_file else 'No activity'
        return {
            'metric_label': 'Files Decoded',
            'metric_value': f"{processed_total:,}",
            'last_activity': last_act,
            'metrics': [
                {'label': 'Files Decoded', 'value': f'{processed_total:,}', 'icon': 'bi-file-earmark-text', 'tone': 'blue'},
                {'label': 'CDR Records', 'value': f"{CDRFile.objects.filter(status=CDRFile.Status.COMPLETED, processing_completed__date=today).aggregate(total=Sum('records_valid'))['total'] or 0:,}", 'icon': 'bi-database-fill', 'tone': 'green'},
                {'label': 'Decode Errors', 'value': f"{CDRFile.objects.filter(status=CDRFile.Status.FAILED, created_at__date=today).count():,}", 'icon': 'bi-exclamation-triangle-fill', 'tone': 'red'},
            ], 'context_label': 'Processing Queue', 'context_value': f"{CDRFile.objects.filter(status__in=[CDRFile.Status.COLLECTED, CDRFile.Status.PENDING]).count():,} files",
        }
    elif service_id == 'mediation-distributor':
        processed_total = DistributionLog.objects.filter(status=DistributionLog.Status.SUCCESS).count()
        if processed_total == 0:
            processed_total = DistributionLog.objects.count()
        last_log = DistributionLog.objects.order_by('-delivered_at').first()
        last_act = _format_time_ago(last_log.delivered_at) if last_log else 'No activity'
        return {
            'metric_label': 'Files Dispatched',
            'metric_value': f"{processed_total:,}",
            'last_activity': last_act,
            'metrics': [
                {'label': 'Files Dispatched', 'value': f'{processed_total:,}', 'icon': 'bi-file-earmark-text', 'tone': 'blue'},
                {'label': 'Pending', 'value': f"{CDRFile.objects.filter(status=CDRFile.Status.DISPATCHING).count():,}", 'icon': 'bi-clock-fill', 'tone': 'orange'},
                {'label': 'Delivery Failed', 'value': f"{DistributionLog.objects.filter(status=DistributionLog.Status.FAILED, delivered_at__date=today).count():,}", 'icon': 'bi-exclamation-triangle-fill', 'tone': 'red'},
            ], 'context_label': 'Output Targets', 'context_value': f"{DistributionPortal.objects.filter(enabled=True).count():,} configured",
        }
    return {
        'metric_label': 'Processed Files',
        'metric_value': '0',
        'last_activity': 'No activity',
    }


def _service_action(service_id, action):
    """Run a systemctl action on a service. Returns (success, message)."""
    import subprocess
    import shutil

    if action not in ('start', 'stop', 'restart', 'enable', 'disable'):
        return False, f'Invalid action: {action}'
    if service_id not in [s['id'] for s in MANAGED_SERVICES]:
        return False, f'Unknown service: {service_id}'

    if not shutil.which('systemctl'):
        return False, 'Service control is unavailable on this host because systemd is not installed.'

    try:
        r = subprocess.run(
            ['sudo', 'systemctl', action, service_id],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return True, f'{action.capitalize()} {service_id} successful'
        return False, r.stderr.strip() or f'{action} failed (exit {r.returncode})'
    except subprocess.TimeoutExpired:
        return False, 'Command timed out'
    except Exception as e:
        return False, str(e)


@login_required
def services_view(request):
    """Service Control view displaying real-time metrics and systemd status for mediation services."""
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Superuser access required')

    services_data = []
    all_healthy = True

    for svc in MANAGED_SERVICES:
        status = _service_status(svc['id'])
        metrics = _get_service_operational_data(svc['id'])
        is_running = status.get('active') in ('active', 'running')
        if not is_running:
            all_healthy = False
        services_data.append({
            **svc,
            **status,
            **metrics,
            'version': getattr(settings, 'UMP_VERSION', None) or getattr(settings, 'VERSION', None) or '—',
            'is_running': is_running,
            'is_controllable': status.get('active') != 'unavailable',
        })

    last_updated = timezone.now().strftime('%b %d, %Y %I:%M:%S %p')

    from core.models import SystemControl
    sys_ctrl = SystemControl.get()

    service_map = {s['type_code']: s for s in services_data}
    context = {
        'services': services_data,
        'collection': service_map.get('collector', {}),
        'decoding': service_map.get('decoder', {}),
        'distribution': service_map.get('distributor', {}),
        'all_healthy': all_healthy,
        'last_updated': last_updated,
        'intake_paused': sys_ctrl.intake_paused,
        'intake_paused_reason': sys_ctrl.intake_paused_reason,
        'intake_paused_by': sys_ctrl.intake_paused_by,
        'intake_paused_at': sys_ctrl.intake_paused_at,
    }
    return render(request, 'dashboard/services.html', context)


@login_required
def services_api(request):
    """API endpoint returning live status and operational metrics for mediation services."""
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Superuser access required'}, status=403)

    services_data = []
    all_healthy = True

    for svc in MANAGED_SERVICES:
        status = _service_status(svc['id'])
        metrics = _get_service_operational_data(svc['id'])
        is_running = status.get('active') in ('active', 'running')
        if not is_running:
            all_healthy = False
        services_data.append({
            **svc,
            **status,
            **metrics,
            'version': getattr(settings, 'UMP_VERSION', None) or getattr(settings, 'VERSION', None) or '—',
            'is_running': is_running,
            'is_controllable': status.get('active') != 'unavailable',
        })

    service_map = {s['type_code']: s for s in services_data}
    return JsonResponse({
        'services': services_data,
        'collection': service_map.get('collector', {}),
        'decoding': service_map.get('decoder', {}),
        'distribution': service_map.get('distributor', {}),
        'all_healthy': all_healthy,
        'last_updated': timezone.now().strftime('%b %d, %Y %I:%M:%S %p'),
    })


@staff_required
@require_POST
def services_action(request):
    """Trigger start, stop, restart, or bulk action on mediation daemon services."""
    import json
    if not request.user.is_superuser:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.content_type:
            return JsonResponse({'error': 'Superuser access required'}, status=403)
        messages.error(request, 'Superuser access required')
        return redirect('dashboard:services')

    service_id = None
    action = None

    if 'application/json' in request.content_type:
        try:
            body = json.loads(request.body)
            service_id = body.get('service')
            action = body.get('action')
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        service_id = request.POST.get('service')
        action = request.POST.get('action')

    if not action:
        return JsonResponse({'error': 'Action required'}, status=400)

    # Bulk actions handling
    if service_id in ('all', 'ALL') or action.endswith('_all'):
        clean_action = action.replace('_all', '')
        results = []
        all_ok = True
        for svc in MANAGED_SERVICES:
            ok, msg = _service_action(svc['id'], clean_action)
            results.append({'service': svc['id'], 'success': ok, 'message': msg})
            if not ok:
                all_ok = False
        log_activity(
            f'SERVICE_BULK_{clean_action.upper()}',
            'SYSTEM',
            level='INFO' if all_ok else 'WARNING',
            message=f'Bulk {clean_action} executed on all services.',
        )
        if 'application/json' in request.content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': all_ok, 'results': results})
        messages.success(request, f'Bulk {clean_action} executed across all services.')
        return redirect('dashboard:services')

    if not service_id:
        return JsonResponse({'error': 'service required'}, status=400)

    success, message = _service_action(service_id, action)
    log_activity(
        f'SERVICE_{action.upper()}',
        'SYSTEM',
        level='INFO' if success else 'ERROR',
        message=f'{action.capitalize()} {service_id}: {message}',
    )

    status = _service_status(service_id)
    if 'application/json' in request.content_type or request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'success': success,
            'message': message,
            'service': status,
        })

    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect('dashboard:services')


@login_required
@require_POST
def intake_toggle(request):
    """Toggle the Emergency Intake Pause flag on or off."""
    import json
    from django.utils import timezone as tz
    from core.models import SystemControl

    if not request.user.is_superuser:
        if 'application/json' in request.content_type:
            return JsonResponse({'error': 'Superuser access required'}, status=403)
        messages.error(request, 'Superuser access required')
        return redirect('dashboard:services')

    if 'application/json' in request.content_type:
        try:
            body = json.loads(request.body)
            # Support both {pause: bool} and {action: "pause"/"resume"}
            if 'action' in body:
                pause = body['action'] == 'pause'
            else:
                pause = body.get('pause')
            reason = body.get('reason', '').strip()
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        pause = request.POST.get('pause') in ('1', 'true', 'True', 'on')
        reason = request.POST.get('reason', '').strip()

    ctrl = SystemControl.get()
    ctrl.intake_paused = bool(pause)
    ctrl.intake_paused_reason = reason if pause else ''
    ctrl.intake_paused_by = request.user if pause else None
    ctrl.intake_paused_at = tz.now() if pause else None
    ctrl.save()

    action_label = 'INTAKE_PAUSED' if pause else 'INTAKE_RESUMED'
    log_activity(
        action_label, 'SYSTEM',
        level='WARNING' if pause else 'INFO',
        message=f'Emergency intake {"paused" if pause else "resumed"} by {request.user}: {reason}',
    )

    if 'application/json' in request.content_type:
        return JsonResponse({
            'success': True,
            'intake_paused': ctrl.intake_paused,
            'message': f'Intake {"paused" if pause else "resumed"} successfully.',
        })

    if pause:
        messages.warning(request, f'Emergency Intake Pause activated. All file collection is suspended.')
    else:
        messages.success(request, 'Emergency Intake Pause lifted. File collection will resume on next cycle.')
    return redirect('dashboard:services')


