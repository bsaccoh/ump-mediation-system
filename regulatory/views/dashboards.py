from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.shortcuts import render

from core.decorators import regulator_required
from regulatory.models import (
    Tariff, TrafficSummary, RatedAggregate, RevenueSnapshot,
    OperatorDeclaration, ReconciliationRun, RiskAlert, AuditCase,
)


@login_required
@regulator_required
def executive_dashboard(request):
    from django.conf import settings
    operators = getattr(settings, 'OPERATORS', [])

    open_alerts = RiskAlert.objects.filter(status='OPEN').count()
    open_cases = AuditCase.objects.filter(
        status__in=['OPEN', 'INVESTIGATING', 'FINDINGS'],
    ).count()
    active_tariffs = Tariff.objects.filter(status='ACTIVE').count()

    return render(request, 'regulatory/dashboard_executive.html', {
        'operators': operators,
        'open_alerts': open_alerts,
        'open_cases': open_cases,
        'active_tariffs': active_tariffs,
    })
