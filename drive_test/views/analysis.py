from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from drive_test.access import dt_view_required
from drive_test.models import Campaign, Sample
from drive_test.services.geo import GeoQueryService
from drive_test.services.thresholds import METRIC_META
from drive_test.services.timeseries import ALL_METRICS


@login_required
@dt_view_required
def map_index(request):
    """Chooser: campaigns that have samples to analyse on the map."""
    campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(sample_count=Count('samples'))
        .filter(sample_count__gt=0)
        .order_by('-created_at')
    )
    if campaigns.count() == 1:
        return redirect('drive_test:map_analysis', pk=campaigns.first().pk)
    return render(request, 'drive_test/map_index.html', {'campaigns': campaigns})


@login_required
@dt_view_required
def map_analysis(request, pk):
    campaign = get_object_or_404(
        Campaign.objects.select_related('project', 'operator'), pk=pk,
    )
    geo = GeoQueryService(campaign)
    avail_counts = geo.available_metrics(ALL_METRICS)
    available = [m for m in ALL_METRICS if avail_counts.get(m, 0) > 0]
    absent = [m for m in ALL_METRICS if avail_counts.get(m, 0) == 0]

    technologies = sorted(
        t for t in Sample.objects.filter(campaign=campaign)
        .exclude(technology='').values_list('technology', flat=True).distinct()
    )
    metric_options = [
        {'key': m, 'label': METRIC_META[m][0], 'unit': METRIC_META[m][1]}
        for m in available
    ]
    default_metric = next(
        (m for m in ('rsrp', 'ss_rsrp', 'rscp', 'rxlev', 'sinr') if m in available),
        available[0] if available else '',
    )

    ns = 'drive_test:drive_test_api'
    config = {
        'campaignId': campaign.pk,
        'mapUrl': reverse(f'{ns}:campaign_map', args=[campaign.pk]),
        'timeseriesUrl': reverse(f'{ns}:campaign_timeseries', args=[campaign.pk]),
        'sampleUrlTemplate': reverse(f'{ns}:sample_detail', args=[999999]),
        'defaultMetric': default_metric,
    }

    return render(request, 'drive_test/map_analysis.html', {
        'campaign': campaign,
        'metric_options': metric_options,
        'absent_metrics': [METRIC_META[m][0] for m in absent],
        'technologies': technologies,
        'default_metric': default_metric,
        'sample_count': Sample.objects.filter(campaign=campaign).count(),
        'config': config,
    })
