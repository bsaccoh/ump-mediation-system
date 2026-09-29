"""Operator / technology / campaign comparison.

Each comparison summarises the same metrics the analytics pages use, via the
same ``stats.describe`` and threshold classification, so numbers are consistent
across the module. A dimension with no data for a metric shows "—", not 0.
"""
from __future__ import annotations

from django.db.models import Count

from drive_test.models import Event, Sample
from drive_test.services.stats import describe
from drive_test.services.thresholds import METRIC_META

# Compact, cross-RAT comparison set.
COMPARE_METRICS = ['rsrp', 'rsrq', 'sinr', 'ss_rsrp', 'ss_sinr', 'rscp', 'rxlev',
                   'dl_throughput', 'ul_throughput', 'latency_ms']


def _summary(sample_filter):
    """Return {metric: {'mean','median','count'}} for metrics present in the set."""
    out = {}
    for m in COMPARE_METRICS:
        vals = list(Sample.objects.filter(**sample_filter, **{f'{m}__isnull': False})
                    .values_list(m, flat=True))
        stats = describe(vals)
        if stats:
            out[m] = {'mean': stats['mean'], 'median': stats['median'], 'count': stats['count']}
    return out


def _build(dimensions):
    """dimensions: list of (label, sample_filter). Returns a comparison table."""
    summaries = {label: _summary(filt) for label, filt in dimensions}
    events = {
        label: Event.objects.filter(**filt_events(filt)).count()
        for label, filt in dimensions
    }
    # Which metrics have data in at least one dimension.
    present = [m for m in COMPARE_METRICS if any(m in s for s in summaries.values())]
    metric_rows = []
    for m in present:
        row = {'key': m, 'label': METRIC_META[m][0], 'unit': METRIC_META[m][1], 'values': {}}
        for label in summaries:
            row['values'][label] = summaries[label].get(m, {}).get('mean')
        metric_rows.append(row)
    return {
        'dimensions': [label for label, _ in dimensions],
        'metrics': metric_rows,
        'events': events,
    }


def filt_events(sample_filter):
    """Translate a Sample filter into the equivalent Event filter keys."""
    out = {}
    for k, v in sample_filter.items():
        # Sample filters use campaign_id/operator_id/technology — all valid on Event.
        out[k] = v
    return out


def operator_comparison(campaign_ids):
    ops = (Sample.objects.filter(campaign_id__in=campaign_ids, operator__isnull=False)
           .values('operator_id', 'operator__name').distinct())
    dims = [(o['operator__name'], {'campaign_id__in': campaign_ids, 'operator_id': o['operator_id']})
            for o in ops]
    return _build(dims)


def technology_comparison(campaign_ids):
    techs = (Sample.objects.filter(campaign_id__in=campaign_ids).exclude(technology='')
             .values_list('technology', flat=True).distinct())
    dims = [(t, {'campaign_id__in': campaign_ids, 'technology': t}) for t in sorted(techs)]
    return _build(dims)


def campaign_comparison(campaign_ids):
    from drive_test.models import Campaign
    campaigns = Campaign.objects.filter(id__in=campaign_ids)
    dims = [(c.name, {'campaign_id': c.id}) for c in campaigns]
    return _build(dims)
