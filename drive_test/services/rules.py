"""
Rules & Thresholds Management — a management interface for the RegulatoryRule /
RegulatoryThreshold configuration that drive_test.services.analysis.AnalysisEngine
already evaluates against every processed file. This module creates no new model,
no second rules engine, and no hard-coded regulatory value: it only queries,
filters and — via drive_test/forms.py's plain ModelForms — edits the two existing
tables.

Status (ACTIVE/INACTIVE/EXPIRED/SCHEDULED) is not a stored field. It is derived
here, once, from the same fields AnalysisEngine._load_rules() itself already
reads (is_active, effective_from, effective_to) — never invented, never a
separate "compliance" judgement.
"""
from __future__ import annotations

from datetime import date

from django.db.models import Count, Q

# Human labels for RegulatoryRule.condition's stored codes (the model itself defines no
# `choices=`, so this is the single place both the forms (drive_test/forms.py) and the
# list/detail views read from — never duplicated, never drifting from services.analysis._OPS.
CONDITION_LABELS = {
    'lt': 'is less than (<)', 'le': 'is less than or equal to (≤)',
    'gt': 'is greater than (>)', 'ge': 'is greater than or equal to (≥)',
    'eq': 'equals (=)', 'ne': 'does not equal (≠)',
}

ACTIVE, INACTIVE, EXPIRED, SCHEDULED = 'active', 'inactive', 'expired', 'scheduled'
STATUS_LABELS = {ACTIVE: 'Active', INACTIVE: 'Inactive', EXPIRED: 'Expired', SCHEDULED: 'Scheduled'}
# A RegulatoryRule has is_active; a RegulatoryThreshold does not, so "Inactive" is not a
# state a threshold can be in on its own (it always reflects its own effective window).
STATUS_CHOICES = list(STATUS_LABELS.items())
THRESHOLD_STATUS_CHOICES = [(k, v) for k, v in STATUS_CHOICES if k != INACTIVE]


def rule_status(rule, today=None):
    """Mirrors the real applicability window AnalysisEngine._load_rules() checks:
    is_active, effective_from <= today, and (no effective_to or effective_to >= today)."""
    today = today or date.today()
    if not rule.is_active:
        return INACTIVE
    if rule.effective_from and rule.effective_from > today:
        return SCHEDULED
    if rule.effective_to and rule.effective_to < today:
        return EXPIRED
    return ACTIVE


def threshold_status(threshold, today=None):
    """RegulatoryThreshold has no is_active field — only its own effective window,
    which is what this reflects. A threshold whose parent rule is INACTIVE is still
    shown as its own real state; the rule's status is displayed alongside it, not
    merged into one fabricated combined field."""
    today = today or date.today()
    if threshold.effective_from and threshold.effective_from > today:
        return SCHEDULED
    if threshold.effective_to and threshold.effective_to < today:
        return EXPIRED
    return ACTIVE


def _rule_status_q(status):
    """Q object matching `status` on RegulatoryRule, using the exact fields
    rule_status() reads, so filtering always agrees with what the row shows."""
    today = date.today()
    if status == INACTIVE:
        return Q(is_active=False)
    if status == SCHEDULED:
        return Q(is_active=True, effective_from__gt=today)
    if status == EXPIRED:
        return Q(is_active=True, effective_from__lte=today, effective_to__lt=today)
    if status == ACTIVE:
        return Q(is_active=True, effective_from__lte=today) & (
            Q(effective_to__isnull=True) | Q(effective_to__gte=today))
    return Q()


def _threshold_status_q(status):
    """Q object matching `status` on RegulatoryThreshold's own effective window
    (it has no is_active field)."""
    today = date.today()
    if status == SCHEDULED:
        return Q(effective_from__gt=today)
    if status == EXPIRED:
        return Q(effective_from__lte=today, effective_to__lt=today)
    if status == ACTIVE:
        return Q(effective_from__lte=today) & (Q(effective_to__isnull=True) | Q(effective_to__gte=today))
    return Q()


def filter_rules(GET):
    """Server-side filters for the Rules tab. Returns (queryset, filters, errors)."""
    from ..models import RegulatoryRule

    f = {k: GET.get(k, '').strip() for k in ('q', 'technology', 'metric', 'status')}
    qs = RegulatoryRule.objects.all()
    errors = []

    if f['q']:
        q = f['q']
        qs = qs.filter(Q(name__icontains=q) | Q(rule_code__icontains=q) | Q(description__icontains=q))
    if f['technology']:
        qs = qs.filter(technology=f['technology'])
    if f['metric']:
        qs = qs.filter(metric=f['metric'])
    if f['status']:
        if f['status'] not in STATUS_LABELS:
            errors.append('Unrecognised status.')
            qs = RegulatoryRule.objects.none()
        else:
            qs = qs.filter(_rule_status_q(f['status']))
    return qs, f, errors


def filter_thresholds(GET):
    """Server-side filters for the Thresholds tab. Returns (queryset, filters, errors)."""
    from ..models import RegulatoryThreshold

    f = {k: GET.get(k, '').strip() for k in ('q', 'operator', 'technology', 'metric', 'status')}
    qs = RegulatoryThreshold.objects.select_related('rule', 'operator')
    errors = []

    if f['q']:
        q = f['q']
        qs = qs.filter(Q(rule__name__icontains=q) | Q(rule__rule_code__icontains=q) | Q(unit__icontains=q))
    if f['operator']:
        qs = qs.filter(operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(rule__technology=f['technology'])
    if f['metric']:
        qs = qs.filter(rule__metric=f['metric'])
    if f['status']:
        if f['status'] not in dict(THRESHOLD_STATUS_CHOICES):
            errors.append('Unrecognised status.')
            qs = RegulatoryThreshold.objects.none()
        else:
            qs = qs.filter(_threshold_status_q(f['status']))
    return qs, f, errors


def annotate_rule_usage(qs):
    """Findings/sessions counts via the real Finding.threshold -> RegulatoryThreshold ->
    rule chain, aggregated in one query per page — never a per-row query."""
    return qs.annotate(
        findings_count=Count('thresholds__findings', distinct=True),
        sessions_evaluated=Count('thresholds__findings__session', distinct=True),
        threshold_count=Count('thresholds', distinct=True),
    )


def annotate_threshold_usage(qs):
    return qs.annotate(
        findings_count=Count('findings', distinct=True),
        sessions_evaluated=Count('findings__session', distinct=True),
    )


def configured_technologies():
    """Technologies actually used to scope a rule so far — real configured data,
    not measurement data and not a guess."""
    from ..models import RegulatoryRule
    return sorted(RegulatoryRule.objects.exclude(technology='')
                  .values_list('technology', flat=True).order_by().distinct())


def configured_metrics():
    from ..models import RegulatoryRule
    return sorted(RegulatoryRule.objects.exclude(metric='')
                  .values_list('metric', flat=True).order_by().distinct())


def has_any_configuration():
    from ..models import RegulatoryRule, RegulatoryThreshold
    return RegulatoryRule.objects.exists() or RegulatoryThreshold.objects.exists()


# ---------------------------------------------------------------------------
# Snapshots for the Audit Trail — a human-readable field:value map of the
# fields a user can actually change on this model, used only to build a
# before/after diff at the moment of a real save (services/audit.changes_dict).
# Never stored anywhere itself.
# ---------------------------------------------------------------------------

def rule_snapshot(rule) -> dict:
    return {
        'name': rule.name, 'description': rule.description, 'technology': rule.technology or 'All',
        'service_type': rule.service_type or 'All', 'metric': rule.metric,
        'condition': CONDITION_LABELS.get(rule.condition, rule.condition),
        'authority': rule.authority, 'regulatory_reference': rule.regulatory_reference,
        'is_active': rule.is_active, 'effective_from': rule.effective_from, 'effective_to': rule.effective_to,
    }


def threshold_snapshot(threshold) -> dict:
    return {
        'rule': threshold.rule.rule_code, 'operator': threshold.operator.name if threshold.operator_id else 'All Operators',
        'warning_value': threshold.warning_value, 'critical_value': threshold.critical_value,
        'unit': threshold.unit, 'effective_from': threshold.effective_from, 'effective_to': threshold.effective_to,
    }
