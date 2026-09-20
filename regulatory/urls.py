from django.urls import path

from .views import audit, compliance, tax_rates, tariffs, dashboards, natca, nra, traffic, declarations, reconciliation, risk, reports

app_name = 'regulatory'

urlpatterns = [
    path('reports/', reports.report_hub, name='report_hub'),
    path('reports/generate/', reports.report_generate, name='report_generate'),
    path('reports/generate/<str:report_type>/', reports.report_download, name='report_download'),
    # Dashboard
    path('', dashboards.executive_dashboard, name='dashboard'),

    # NatCA Dashboard
    path('natca/dashboard/', natca.natca_dashboard, name='natca_dashboard'),

    # NatCA Traffic Monitoring
    path('natca/traffic/', traffic.traffic_overview, name='traffic_overview'),
    path('natca/traffic/international/', traffic.traffic_international, name='traffic_international'),
    path('natca/traffic/interconnect/', traffic.traffic_interconnect, name='traffic_interconnect'),
    path('natca/traffic/roaming/', traffic.traffic_roaming, name='traffic_roaming'),
    path('natca/tariff-compliance/', compliance.tariff_compliance_list, name='tariff_compliance_list'),
    path('natca/tariff-compliance/run/', compliance.tariff_compliance_run, name='tariff_compliance_run'),
    path('natca/tariff-compliance/export/', compliance.tariff_compliance_export, name='tariff_compliance_export'),
    path('natca/tariff-compliance/<int:pk>/', compliance.tariff_compliance_detail, name='tariff_compliance_detail'),
    path('natca/tariff-compliance/<int:pk>/compare/', compliance.tariff_compliance_compare, name='tariff_compliance_compare'),
    path('natca/tariff-compliance/<int:pk>/create-finding/', compliance.tariff_compliance_create_finding, name='tariff_compliance_create_finding'),

    # NRA Dashboard
    path('nra/dashboard/', nra.nra_dashboard, name='nra_dashboard'),

    # NRA Declarations
    path('nra/declarations/', declarations.declaration_list, name='declaration_list'),
    path('nra/declarations/create/', declarations.declaration_create, name='declaration_create'),
    path('nra/declarations/<int:pk>/', declarations.declaration_detail, name='declaration_detail'),
    path('nra/declarations/<int:pk>/edit/', declarations.declaration_edit, name='declaration_edit'),
    path('nra/declarations/<int:pk>/submit/', declarations.declaration_submit, name='declaration_submit'),
    path('nra/declarations/<int:pk>/review/', declarations.declaration_review, name='declaration_review'),
    path('nra/declarations/<int:pk>/amend/', declarations.declaration_amend, name='declaration_amend'),
    path('nra/declarations/import/', declarations.declaration_import, name='declaration_import'),
    path('nra/declarations/import/template/', declarations.declaration_import_template, name='declaration_import_template'),

    # NRA Reconciliation
    path('nra/reconciliation/', reconciliation.reconciliation_list, name='reconciliation_list'),
    path('nra/reconciliation/run/', reconciliation.reconciliation_create, name='reconciliation_create'),
    path('nra/reconciliation/export/', reconciliation.reconciliation_export, name='reconciliation_export'),
    path('nra/reconciliation/<int:pk>/', reconciliation.reconciliation_detail, name='reconciliation_detail'),
    path('nra/reconciliation/discrepancy/<int:pk>/', reconciliation.discrepancy_detail, name='discrepancy_detail'),

    # NRA Risk Management
    path('nra/risk/rules/', risk.risk_rule_list, name='risk_rule_list'),
    path('nra/risk/rules/create/', risk.risk_rule_create, name='risk_rule_create'),
    path('nra/risk/rules/<int:pk>/edit/', risk.risk_rule_edit, name='risk_rule_edit'),
    path('nra/risk/alerts/', risk.risk_alert_list, name='risk_alert_list'),
    path('nra/risk/alerts/export/', risk.risk_alert_export, name='risk_alert_export'),
    path('nra/risk/alerts/<str:identifier>/', risk.risk_alert_detail, name='risk_alert_detail'),
    path('nra/risk/alerts/<str:identifier>/evidence/', risk.risk_alert_evidence_export, name='risk_alert_evidence_export'),

    # NRA Audit Case Management
    path('nra/audit/', audit.audit_case_list, name='audit_case_list'),
    path('nra/audit/export/', audit.audit_case_export, name='audit_case_export'),
    path('nra/audit/create/', audit.audit_case_create, name='audit_case_create'),
    path('nra/audit/<int:pk>/', audit.audit_case_detail, name='audit_case_detail'),
    path('nra/audit/<int:pk>/report/', audit.audit_case_report, name='audit_case_report'),

    # Tariff management
    path('natca/tariffs/', tariffs.tariff_list, name='tariff_list'),
    path('natca/tariffs/create/', tariffs.tariff_create, name='tariff_create'),
    path('natca/tariffs/<int:pk>/edit/', tariffs.tariff_edit, name='tariff_edit'),
    path('natca/tariffs/<int:pk>/', tariffs.tariff_detail, name='tariff_detail'),
    path('natca/tariffs/<int:pk>/workflow/<str:action>/', tariffs.tariff_workflow, name='tariff_workflow'),
    path('natca/tariffs/<int:pk>/deactivate/', tariffs.tariff_deactivate, name='tariff_deactivate'),
    path('natca/tariffs/<int:pk>/new-version/', tariffs.tariff_new_version, name='tariff_new_version'),
    path('tariffs/', tariffs.tariff_list, name='tariff_list_legacy'),

    # NRA tax rates
    path('nra/tax-rates/', tax_rates.tax_rate_list, name='tax_rate_list'),
    path('nra/tax-rates/create/', tax_rates.tax_type_create, name='tax_type_create'),
    path('nra/tax-rates/<int:pk>/', tax_rates.tax_type_detail, name='tax_type_detail'),
    path('nra/tax-rates/<int:pk>/edit/', tax_rates.tax_type_edit, name='tax_type_edit'),
    path('nra/tax-rates/<int:pk>/deactivate/', tax_rates.tax_type_deactivate, name='tax_type_deactivate'),
    path('nra/tax-rates/<int:tax_type_id>/versions/create/', tax_rates.tax_rate_create, name='tax_rate_create'),
    path('tax-rates/', tax_rates.tax_rate_list, name='tax_rate_list_legacy'),
]
