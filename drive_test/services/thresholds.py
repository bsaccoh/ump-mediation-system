"""KPI threshold resolution and value classification.

Classification bands live in the database (``KpiThreshold``). Nothing here
hard-codes a threshold into view or template logic — ``DEFAULT_BANDS`` exists
only to seed the DB with sensible, editable starting values.

A band is ``{"label", "min", "max", "rank", "color"}`` where the interval is
inclusive-lower / exclusive-upper and ``None`` means unbounded. ``color`` is a
token (excellent/good/fair/poor/critical) the client maps to a CSS custom
property — so dark mode and rebrands need no code change.
"""
from __future__ import annotations

# Metrics the map/analysis surfaces can colour, with unit + human label.
METRIC_META = {
    'rsrp': ('RSRP', 'dBm'), 'rsrq': ('RSRQ', 'dB'), 'sinr': ('SINR', 'dB'),
    'rssi': ('RSSI', 'dBm'), 'cqi': ('CQI', ''),
    'ss_rsrp': ('SS-RSRP', 'dBm'), 'ss_rsrq': ('SS-RSRQ', 'dB'), 'ss_sinr': ('SS-SINR', 'dB'),
    'rscp': ('RSCP', 'dBm'), 'ecno': ('Ec/No', 'dB'),
    'rxlev': ('RxLev', 'dBm'), 'rxqual': ('RxQual', ''),
    'dl_throughput': ('DL Throughput', 'kbps'), 'ul_throughput': ('UL Throughput', 'kbps'),
    'latency_ms': ('Latency', 'ms'), 'packet_loss': ('Packet Loss', '%'),
}


def _bands(*rows):
    """Build a band list from (label, min, max, color) tuples, ranked high→low."""
    n = len(rows)
    return [
        {'label': lbl, 'min': lo, 'max': hi, 'rank': n - i, 'color': color}
        for i, (lbl, lo, hi, color) in enumerate(rows)
    ]


# Sensible defaults (editable in the DB). Higher-is-better metrics first.
DEFAULT_BANDS = {
    'rsrp': _bands(
        ('Excellent', -80, None, 'excellent'), ('Good', -90, -80, 'good'),
        ('Fair', -100, -90, 'fair'), ('Poor', -110, -100, 'poor'),
        ('Critical', None, -110, 'critical')),
    'ss_rsrp': _bands(
        ('Excellent', -80, None, 'excellent'), ('Good', -90, -80, 'good'),
        ('Fair', -100, -90, 'fair'), ('Poor', -110, -100, 'poor'),
        ('Critical', None, -110, 'critical')),
    'rsrq': _bands(
        ('Excellent', -10, None, 'excellent'), ('Good', -15, -10, 'good'),
        ('Fair', -18, -15, 'fair'), ('Poor', -20, -18, 'poor'),
        ('Critical', None, -20, 'critical')),
    'ss_rsrq': _bands(
        ('Excellent', -10, None, 'excellent'), ('Good', -15, -10, 'good'),
        ('Fair', -18, -15, 'fair'), ('Poor', -20, -18, 'poor'),
        ('Critical', None, -20, 'critical')),
    'sinr': _bands(
        ('Excellent', 20, None, 'excellent'), ('Good', 13, 20, 'good'),
        ('Fair', 0, 13, 'fair'), ('Poor', -5, 0, 'poor'),
        ('Critical', None, -5, 'critical')),
    'ss_sinr': _bands(
        ('Excellent', 20, None, 'excellent'), ('Good', 13, 20, 'good'),
        ('Fair', 0, 13, 'fair'), ('Poor', -5, 0, 'poor'),
        ('Critical', None, -5, 'critical')),
    'rscp': _bands(
        ('Excellent', -75, None, 'excellent'), ('Good', -85, -75, 'good'),
        ('Fair', -95, -85, 'fair'), ('Poor', -105, -95, 'poor'),
        ('Critical', None, -105, 'critical')),
    'ecno': _bands(
        ('Excellent', -6, None, 'excellent'), ('Good', -9, -6, 'good'),
        ('Fair', -12, -9, 'fair'), ('Poor', -15, -12, 'poor'),
        ('Critical', None, -15, 'critical')),
    'rxlev': _bands(
        ('Excellent', -70, None, 'excellent'), ('Good', -85, -70, 'good'),
        ('Fair', -95, -85, 'fair'), ('Poor', -105, -95, 'poor'),
        ('Critical', None, -105, 'critical')),
    'dl_throughput': _bands(
        ('Excellent', 50000, None, 'excellent'), ('Good', 20000, 50000, 'good'),
        ('Fair', 5000, 20000, 'fair'), ('Poor', 1000, 5000, 'poor'),
        ('Critical', None, 1000, 'critical')),
    'ul_throughput': _bands(
        ('Excellent', 20000, None, 'excellent'), ('Good', 10000, 20000, 'good'),
        ('Fair', 2000, 10000, 'fair'), ('Poor', 500, 2000, 'poor'),
        ('Critical', None, 500, 'critical')),
}


def classify(value, bands):
    """Return the matching band dict, or None when value is None / unclassified."""
    if value is None or not bands:
        return None
    for band in bands:
        lo, hi = band.get('min'), band.get('max')
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return band
    return None


def resolve_bands(metric, technology='', campaign=None):
    """Resolve the active band-set for a metric, honouring scope precedence:
    campaign > technology-specific > generic. Returns a list (possibly empty).
    """
    from drive_test.models import KpiThreshold

    qs = KpiThreshold.objects.filter(metric=metric, is_active=True)
    candidates = list(qs)
    if not candidates:
        return DEFAULT_BANDS.get(metric, [])

    def score(t):
        s = 0
        if campaign is not None and t.campaign_id == getattr(campaign, 'pk', None):
            s += 4
        if technology and t.technology == technology:
            s += 2
        if not t.technology:
            s += 1
        return s

    best = max(candidates, key=score)
    return best.bands or DEFAULT_BANDS.get(metric, [])
