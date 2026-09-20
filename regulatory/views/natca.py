"""
NatCA Dashboard Views
"""
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView

from core.decorators import regulator_required
from reference.models import Operator
from regulatory.services.natca_dashboard_service import NatCADashboardService


class NatCADashboardView(TemplateView):
    template_name = "regulatory/natca/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        operator = self.request.GET.get("operator")
        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")
        period = self.request.GET.get("period", "all")
        trend_granularity = self.request.GET.get("trend_granularity")  # None = auto-detect

        service = NatCADashboardService(
            operator=operator,
            start_date=start_date,
            end_date=end_date,
            period=period,
            trend_granularity=trend_granularity,
        )

        dashboard_data = service.get_dashboard_data()
        context.update(dashboard_data)

        context["traffic_trend_data"] = dashboard_data.get("traffic_trend", {})
        context["traffic_by_operator_data"] = dashboard_data.get("traffic_by_operator", [])
        context["service_contribution_data"] = dashboard_data.get("service_contribution", {})

        context["selected_operator"] = operator
        context["selected_period"] = period
        context["trend_granularity"] = service.trend_granularity
        context["start_date"] = start_date
        context["end_date"] = end_date
        context["date_range"] = dashboard_data.get("date_range", {})
        context["operators"] = Operator.objects.filter(enabled=True)
        context["page_title"] = "NatCA Dashboard"
        context["page_subtitle"] = "National telecom traffic, quality of service, tariffs, compliance and market oversight."

        return context


natca_dashboard = login_required(regulator_required(NatCADashboardView.as_view()))
