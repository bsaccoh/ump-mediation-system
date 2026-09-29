from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render

from drive_test.access import dt_view_required, is_regulator
from drive_test.models import Campaign, DriveTestFile, Event, Project, Sample
from drive_test.models.enums import Severity


@login_required
@dt_view_required
def dashboard(request):
    """Role-aware Drive Test Intelligence landing.

    Counts are real (they may legitimately be zero). Network KPIs that need
    measurement data render as "—" until samples exist — never as 0.
    """
    projects = Project.objects.count()
    campaigns = Campaign.objects.count()
    files = DriveTestFile.objects.count()
    samples = Sample.objects.count()
    operators = (
        Campaign.objects.exclude(operator__isnull=True)
        .values('operator').distinct().count()
    )
    critical_events = Event.objects.filter(severity=Severity.CRITICAL).count()

    summary = [
        {'label': 'Projects', 'value': projects, 'icon': 'bi-folder'},
        {'label': 'Campaigns', 'value': campaigns, 'icon': 'bi-flag'},
        {'label': 'Files', 'value': files, 'icon': 'bi-file-earmark-arrow-up'},
        {'label': 'Samples', 'value': samples, 'icon': 'bi-broadcast-pin'},
        {'label': 'Operators', 'value': operators, 'icon': 'bi-building'},
        {'label': 'Critical Events', 'value': critical_events, 'icon': 'bi-exclamation-triangle'},
    ]

    # Network KPIs require processed samples; none yet ⇒ "not available", not 0.
    network_kpis = [
        {'label': 'Avg RSRP', 'unit': 'dBm'},
        {'label': 'Avg RSRQ', 'unit': 'dB'},
        {'label': 'Avg SINR', 'unit': 'dB'},
        {'label': 'Avg DL', 'unit': 'Mbps'},
        {'label': 'Avg UL', 'unit': 'Mbps'},
        {'label': 'CSSR', 'unit': '%'},
        {'label': 'DCR', 'unit': '%'},
        {'label': 'HO Success', 'unit': '%'},
    ]

    recent_campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(file_count=Count('files'))
        .order_by('-created_at')[:8]
    )

    return render(request, 'drive_test/dashboard.html', {
        'summary': summary,
        'network_kpis': network_kpis,
        'recent_campaigns': recent_campaigns,
        'has_data': samples > 0,
        'regulator_view': is_regulator(request.user),
    })


@login_required
@dt_view_required
def placeholder(request, section):
    """Honest empty state for sections delivered in a later phase.

    Keeps the navigation complete without pretending a screen exists yet.
    """
    titles = {
        'map': 'Map Analysis', 'rf': 'RF Analysis', 'coverage': 'Coverage',
        'voice': 'Voice Analysis', 'data': 'Data Performance',
        'handover': 'Handover Analysis', 'cells': 'Cell Analysis',
        'events': 'Events', 'problems': 'Problem Areas',
        'comparison': 'Comparison', 'ai': 'AI Analyst', 'reports': 'Reports',
        'processing': 'Processing', 'config': 'Configuration',
    }
    phase = {
        'map': 3, 'rf': 4, 'coverage': 4, 'voice': 4, 'data': 4, 'handover': 6,
        'cells': 4, 'events': 5, 'problems': 5, 'comparison': 5, 'ai': 7,
        'reports': 6, 'processing': 2, 'config': 2,
    }
    return render(request, 'drive_test/placeholder.html', {
        'title': titles.get(section, 'Drive Test Intelligence'),
        'phase': phase.get(section),
    })
