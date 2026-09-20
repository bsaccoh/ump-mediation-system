from django import template
from django.urls import reverse

register = template.Library()

_RISK_SOURCE_VIEW = {
    'RECONCILIATION': 'regulatory:reconciliation_list',
    'OPERATOR_DECLARATIONS': 'regulatory:declaration_list',
    'TARIFF_COMPLIANCE': 'regulatory:tariff_compliance_list',
}


@register.simple_tag
def risk_source_url(alert):
    """Return a link to the alert's originating record, where a matching list page exists."""
    view_name = _RISK_SOURCE_VIEW.get(alert.source_type)
    if not view_name:
        return ''
    url = reverse(view_name)
    if alert.source_reference:
        url = f'{url}?search={alert.source_reference}'
    return url


@register.filter
def currency_fmt(value, currency='SLE'):
    """Format a decimal as currency: 1234.56 → SLE 1,234.56"""
    try:
        return f'{currency} {float(value):,.2f}'
    except (ValueError, TypeError):
        return value


@register.filter
def abs_val(value):
    """Return absolute value."""
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value


@register.filter(name='abs')
def abs_filter(value):
    """Return absolute value (used as |abs in templates)."""
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value


@register.filter
def sub(value, arg):
    """Subtract arg from value: {{ a|sub:b }} → a - b"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return ''


@register.filter
def pct_change(current, previous):
    """Calculate percentage change between two values."""
    try:
        current = float(current)
        previous = float(previous)
        if previous == 0:
            return '---'
        change = ((current - previous) / previous) * 100
        sign = '+' if change > 0 else ''
        return f'{sign}{change:.1f}%'
    except (ValueError, TypeError, ZeroDivisionError):
        return '---'
