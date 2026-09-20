import json
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView

from core.decorators import regulator_required
from regulatory.services.nra_dashboard_service import NRADashboardService


class NRADashboardView(TemplateView):
    template_name = "regulatory/nra/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        service = NRADashboardService(
            operator=self.request.GET.get("operator"),
            service_type=self.request.GET.get("service"),
            start_date=self.request.GET.get("start_date"),
            end_date=self.request.GET.get("end_date"),
        )

        context.update(service.get_dashboard_data())
        context["page_title"] = "NRA Dashboard"
        context["page_subtitle"] = (
            "Taxable revenue, GST monitoring, operator declarations, "
            "reconciliation, tax risk and audit oversight"
        )

        from reference.models import Operator
        context["operators"] = Operator.objects.filter(enabled=True)

        return context


nra_dashboard = login_required(
    regulator_required(NRADashboardView.as_view())
)
