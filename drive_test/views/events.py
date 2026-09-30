from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.models import Campaign, Event, ProblemArea
from drive_test.models.enums import EventStatus, Severity
from drive_test.services.audit import log_action


@login_required
@dt_view_required
def events_index(request):
    campaigns = (
        Campaign.objects.select_related('project')
        .annotate(event_count=Count('events')).filter(event_count__gt=0)
        .order_by('-created_at')
    )
    return render(request, 'drive_test/events_index.html', {'campaigns': campaigns})


@login_required
@dt_view_required
def event_list(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    events = Event.objects.filter(campaign=campaign).select_related('sample')
    etype = request.GET.get('type', '').strip()
    severity = request.GET.get('severity', '').strip()
    status = request.GET.get('status', '').strip()
    if etype:
        events = events.filter(event_type=etype)
    if severity:
        events = events.filter(severity=severity)
    if status:
        events = events.filter(status=status)
    events = events.order_by('-severity', '-timestamp')[:1000]

    types = (Event.objects.filter(campaign=campaign)
             .values_list('event_type', flat=True).distinct().order_by('event_type'))
    return render(request, 'drive_test/event_list.html', {
        'campaign': campaign, 'events': events, 'types': types,
        'severities': Severity.choices, 'statuses': EventStatus.choices,
        'filters': {'type': etype, 'severity': severity, 'status': status},
        'total': Event.objects.filter(campaign=campaign).count(),
    })


@login_required
@dt_view_required
def event_detail(request, pk):
    event = get_object_or_404(
        Event.objects.select_related('campaign', 'sample', 'operator', 'cell', 'problem_area'), pk=pk)
    return render(request, 'drive_test/event_detail.html', {
        'event': event, 'statuses': EventStatus.choices,
    })


@login_required
@dt_manage_required
def event_set_status(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status', '').strip()
        if new_status in EventStatus.values:
            old = event.status
            event.status = new_status
            event.save(update_fields=['status'])
            log_action(request.user, 'UPDATE', 'drive_test.Event', event.pk,
                       f'Event status {old} → {new_status}', request=request)
            messages.success(request, 'Event status updated.')
    return redirect('drive_test:event_detail', pk=event.pk)


@login_required
@dt_view_required
def problem_area_list(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    areas = ProblemArea.objects.filter(campaign=campaign).order_by('-severity', '-sample_count')
    return render(request, 'drive_test/problem_areas.html', {
        'campaign': campaign, 'areas': areas,
    })


@login_required
@dt_manage_required
def campaign_detect(request, pk):
    """Enqueue event detection + problem-area clustering for a campaign."""
    campaign = get_object_or_404(Campaign, pk=pk)
    if request.method != 'POST':
        return redirect('drive_test:event_list', pk=campaign.pk)

    from drive_test.services.jobs import run_tracked
    from drive_test.tasks import detect_events_and_areas
    run_tracked(
        task=detect_events_and_areas, job_type='drive_test.detect_events',
        label=f'Detect events for {campaign.name}', user=request.user,
        params={'campaign_id': campaign.pk}, args=(campaign.pk,),
    )
    log_action(request.user, 'PROCESS', 'drive_test.Campaign', campaign.pk,
               'Ran event detection', request=request)
    messages.success(request, 'Event detection complete.')
    return redirect('drive_test:event_list', pk=campaign.pk)
