from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from drive_test.access import dt_view_required
from drive_test.models import Campaign, Sample
from drive_test.services import analytics

SECTIONS = [
    ('rf', 'RF Analysis'), ('coverage', 'Coverage'), ('data', 'Data Performance'),
    ('cells', 'Cell Analysis'), ('voice', 'Voice'), ('handover', 'Handover'),
]
_VALID = {s for s, _ in SECTIONS}


@login_required
@dt_view_required
def analytics_index(request, section='rf'):
    if section not in _VALID:
        section = 'rf'
    campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(sample_count=Count('samples')).filter(sample_count__gt=0)
        .order_by('-created_at')
    )
    title = dict(SECTIONS)[section]
    return render(request, 'drive_test/analytics_index.html', {
        'campaigns': campaigns, 'section': section, 'title': title,
    })


@login_required
@dt_view_required
def campaign_analytics(request, pk, section='rf'):
    if section not in _VALID:
        section = 'rf'
    campaign = get_object_or_404(
        Campaign.objects.select_related('project', 'operator'), pk=pk)
    technology = request.GET.get('technology', '').strip()
    technologies = sorted(
        t for t in Sample.objects.filter(campaign=campaign)
        .exclude(technology='').values_list('technology', flat=True).distinct())

    ctx = {
        'campaign': campaign, 'section': section, 'sections': SECTIONS,
        'technology': technology, 'technologies': technologies,
        'sample_count': Sample.objects.filter(campaign=campaign).count(),
    }

    if section == 'rf':
        ctx['reports'] = analytics.rf_report(campaign, technology)
    elif section == 'coverage':
        ctx['coverage'] = analytics.coverage_report(campaign, technology)
    elif section == 'data':
        ctx['reports'] = analytics.data_report(campaign, technology)
    elif section == 'cells':
        ctx['cells'] = analytics.cell_report(campaign)
    elif section == 'voice':
        ctx['voice'] = analytics.voice_report(campaign, technology)
    # 'handover' renders an empty state until the event engine (Phase 5) runs.

    return render(request, 'drive_test/analytics.html', ctx)
