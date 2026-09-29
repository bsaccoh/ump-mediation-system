"""Distribution statistics — pure Python (no numpy dependency).

Reports distributions, not just means: min/mean/median/p10/p50/p90/max and
standard deviation, plus a threshold-band histogram. An all-missing column
returns None throughout (never a misleading 0), and a metric with too few
samples is flagged so a caller can say "insufficient samples" instead of
publishing a number.
"""
from __future__ import annotations

import math

from .thresholds import classify

MIN_SAMPLES = 20  # below this, treat a distribution as not publishable


def percentile(sorted_values, p):
    """Linear-interpolated percentile of an already-sorted list (p in 0..100)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[int(rank)]
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def describe(values):
    """Summarise a list of numeric values (Nones should be pre-filtered)."""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return None
    s = sorted(vals)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / n if n > 1 else 0.0
    return {
        'count': n,
        'min': round(s[0], 2),
        'max': round(s[-1], 2),
        'mean': round(mean, 2),
        'median': round(percentile(s, 50), 2),
        'p10': round(percentile(s, 10), 2),
        'p50': round(percentile(s, 50), 2),
        'p90': round(percentile(s, 90), 2),
        'std': round(math.sqrt(var), 2),
        'sufficient': n >= MIN_SAMPLES,
    }


def band_histogram(values, bands):
    """Return [{label, color, count, pct}] over the given classification bands.

    Values that classify into no band are grouped under 'Unclassified'.
    """
    if not bands:
        return []
    counts = {b['label']: 0 for b in bands}
    color = {b['label']: b['color'] for b in bands}
    unclassified = 0
    total = 0
    for v in values:
        if v is None:
            continue
        total += 1
        band = classify(v, bands)
        if band:
            counts[band['label']] += 1
        else:
            unclassified += 1
    out = []
    for b in bands:
        c = counts[b['label']]
        out.append({'label': b['label'], 'color': b['color'], 'count': c,
                    'pct': round(100.0 * c / total, 1) if total else 0.0})
    if unclassified:
        out.append({'label': 'Unclassified', 'color': 'none', 'count': unclassified,
                    'pct': round(100.0 * unclassified / total, 1) if total else 0.0})
    return out
