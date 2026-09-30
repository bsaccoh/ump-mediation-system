"""AI Analyst — evidence-first, deterministic reasoning.

The analyst never invents measurements. It reads the same analytics/events/
problem-area data the rest of the module computes, then organises it into four
clearly separated layers so a reader always knows what is measured versus
inferred:

    MEASURED FACT          — a number from the database
    OBSERVED PATTERN       — a plain-language reading of those numbers
    POSSIBLE CAUSE         — a hypothesis, with the evidence it rests on + confidence
    RECOMMENDED INVESTIGATION — what an engineer should check next

An optional LLM (services.ai_llm, behind a flag) may only *phrase* this
evidence into prose — it is given the facts and forbidden to add numbers.
"""
from __future__ import annotations

from django.db.models import Count

from drive_test.services import analytics
from drive_test.services.stats import MIN_SAMPLES


def _confidence(sample_count, strong):
    if sample_count < MIN_SAMPLES:
        return 'Low'
    return 'High' if strong else 'Medium'


def analyze(campaign, question=''):
    from drive_test.models import Event, ProblemArea, Sample

    total = Sample.objects.filter(campaign=campaign).count()
    facts = []
    patterns = []
    causes = []
    recommendations = []

    if total == 0:
        return {
            'campaign': campaign.name, 'question': question, 'has_data': False,
            'facts': [], 'patterns': [], 'causes': [], 'recommendations': [],
            'note': 'No processed samples for this campaign — nothing to analyse.',
        }

    # --- Measured facts (straight from the analytics services) --------------
    cov = analytics.coverage_report(campaign)
    rf = {r['metric']: r for r in analytics.rf_report(campaign)}
    data = {r['metric']: r for r in analytics.data_report(campaign)}

    facts.append({'label': 'Samples', 'value': total, 'unit': ''})
    if cov.get('metric'):
        facts.append({'label': f"Mean {cov['label']}", 'value': cov['stats']['mean'], 'unit': cov['unit']})
        facts.append({'label': 'Route distance', 'value': cov['total_distance_km'], 'unit': 'km'})
    for m in ('sinr', 'ss_sinr'):
        if m in rf:
            facts.append({'label': f"Mean {rf[m]['label']}", 'value': rf[m]['stats']['mean'], 'unit': rf[m]['unit']})
    if 'dl_throughput' in data and data['dl_throughput']['stats']:
        facts.append({'label': 'Mean DL throughput',
                      'value': data['dl_throughput']['stats']['mean'], 'unit': 'kbps'})

    event_counts = dict(
        Event.objects.filter(campaign=campaign).values_list('event_type')
        .annotate(n=Count('id')).values_list('event_type', 'n')
    )
    total_events = sum(event_counts.values())
    area_count = ProblemArea.objects.filter(campaign=campaign).count()
    facts.append({'label': 'Detected events', 'value': total_events, 'unit': ''})
    facts.append({'label': 'Problem areas', 'value': area_count, 'unit': ''})

    # --- Patterns + causes (rule-driven, each tied to its evidence) ---------
    def poor_share(metric):
        r = rf.get(metric)
        if not r:
            return 0.0
        return sum(h['pct'] for h in r['histogram'] if h['color'] in ('poor', 'critical'))

    # Coverage
    if cov.get('metric'):
        cov_poor = sum(h['pct'] for h in cov['histogram'] if h['color'] in ('poor', 'critical'))
        if cov_poor >= 20:
            patterns.append(f"{cov_poor:.0f}% of {cov['label']} samples fall in the poor/critical range.")
            causes.append({
                'cause': 'Weak serving-cell coverage over part of the route',
                'confidence': _confidence(cov['stats']['count'], cov_poor >= 40),
                'evidence': [f"{cov_poor:.0f}% poor/critical {cov['label']}",
                             f"mean {cov['label']} {cov['stats']['mean']} {cov['unit']}"],
            })
            recommendations += ['Review serving-cell coverage and antenna configuration along the affected segment',
                                'Check for coverage holes and physical obstructions']

    # Quality / interference
    for m in ('sinr', 'ss_sinr'):
        if m in rf:
            sh = poor_share(m)
            if sh >= 20 and rf[m]['stats']['mean'] is not None:
                patterns.append(f"{sh:.0f}% of {rf[m]['label']} samples are poor/critical (mean {rf[m]['stats']['mean']} {rf[m]['unit']}).")
                causes.append({
                    'cause': 'Poor radio quality — interference or pilot pollution likely',
                    'confidence': _confidence(rf[m]['stats']['count'], sh >= 40),
                    'evidence': [f"{sh:.0f}% poor/critical {rf[m]['label']}"],
                })
                recommendations += ['Review neighbour configuration and check for missing neighbours',
                                    'Investigate overlapping cells / PCI or scrambling-code confusion']

    # Throughput with adequate coverage → capacity/backhaul
    if 'dl_throughput' in data and data['dl_throughput']['stats']:
        dl = data['dl_throughput']['stats']['mean']
        good_cov = cov.get('metric') and (cov['stats']['mean'] or -999) >= -95
        if dl is not None and dl < 5000 and good_cov:
            patterns.append(f"Mean downlink throughput is low ({dl} kbps) despite adequate coverage.")
            causes.append({
                'cause': 'Capacity or backhaul limitation (RF looks adequate)',
                'confidence': _confidence(data['dl_throughput']['stats']['count'], dl < 2000),
                'evidence': [f"mean DL {dl} kbps", f"mean {cov['label']} {cov['stats']['mean']} {cov['unit']}"],
            })
            recommendations.append('Check cell load, scheduler and transport/backhaul capacity for the serving cells')

    # Mobility
    ho_fail = event_counts.get('HO_FAILURE', 0)
    if ho_fail:
        patterns.append(f"{ho_fail} handover-failure event(s) detected.")
        causes.append({'cause': 'Handover/mobility parameter issues',
                       'confidence': 'Medium',
                       'evidence': [f"{ho_fail} handover failures"]})
        recommendations.append('Review handover thresholds and neighbour relations for the involved cell pairs')

    if not patterns:
        patterns.append('No sustained coverage, quality or throughput problems stand out in the aggregate statistics.')

    # De-duplicate recommendations, keep order.
    seen = set()
    recommendations = [r for r in recommendations if not (r in seen or seen.add(r))]

    return {
        'campaign': campaign.name, 'question': question, 'has_data': True,
        'facts': facts, 'patterns': patterns, 'causes': causes,
        'recommendations': recommendations,
        'note': 'All figures are measured from this campaign\'s samples. Causes are '
                'hypotheses to investigate, not confirmed conclusions.',
    }
