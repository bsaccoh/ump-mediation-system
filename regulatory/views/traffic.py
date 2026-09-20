"""
Traffic Monitoring Views for NatCA

Detailed traffic analysis with filters and drill-down.
"""
from datetime import datetime, timedelta
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from core.decorators import regulator_required

from regulatory.services.natca_dashboard_service import NatCADashboardService


@login_required
@regulator_required
def traffic_overview(request):
    """
    Traffic Overview - Traffic volume by type, operator, technology, direction.
    """
    # Get filter parameters
    period = request.GET.get('period', '30')
    operator = request.GET.get('operator', '')
    service_type = request.GET.get('service_type', '')
    traffic_type = request.GET.get('traffic_type', '')
    network_technology = request.GET.get('network_technology', '')

    # Calculate date range
    period_days = int(period) if period.isdigit() else 30
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    # Get dashboard data
    service = NatCADashboardService(
        operator=operator or None,
        period=period
    )

    dashboard_data = service.get_dashboard_data()

    context = {
        'period': period,
        'operator': operator,
        'service_type': service_type,
        'traffic_type': traffic_type,
        'network_technology': network_technology,
        'start_date': start_date,
        'end_date': end_date,
        'dashboard_data': dashboard_data,
        'traffic_trend_data': dashboard_data.get('traffic_trend', {}),
        'traffic_by_operator_data': dashboard_data.get('traffic_by_operator', []),
        'title': 'Traffic Overview',
    }

    return render(request, 'regulatory/natca/traffic_overview.html', context)


@login_required
@regulator_required
def traffic_international(request):
    period   = request.GET.get('period', '30')
    operator = request.GET.get('operator', '')

    service = NatCADashboardService(operator=operator or None, period=period)
    data    = service.get_international_data()

    context = {
        'period': period, 'operator': operator, 'title': 'International Traffic',
        **data,
    }
    return render(request, 'regulatory/natca/traffic_international.html', context)


@login_required
@regulator_required
def traffic_interconnect(request):
    period   = request.GET.get('period', '30')
    operator = request.GET.get('operator', '')

    service = NatCADashboardService(operator=operator or None, period=period)
    data    = service.get_interconnect_data()

    context = {
        'period': period, 'operator': operator, 'title': 'Interconnect Traffic',
        **data,
    }
    return render(request, 'regulatory/natca/traffic_interconnect.html', context)


@login_required
@regulator_required
def traffic_roaming(request):
    period   = request.GET.get('period', '30')
    operator = request.GET.get('operator', '')

    service = NatCADashboardService(operator=operator or None, period=period)
    data    = service.get_roaming_data()

    context = {
        'period': period, 'operator': operator, 'title': 'Roaming Traffic',
        **data,
    }
    return render(request, 'regulatory/natca/traffic_roaming.html', context)
