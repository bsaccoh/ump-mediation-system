from django.contrib import admin

from .models import (
    Tariff, TaxType, TaxRate, TrafficSummary, RatedAggregate,
    RevenueSnapshot, OperatorDeclaration, DeclarationLineItem,
    ReconciliationRun, ReconciliationResult, Discrepancy,
    RiskRule, RiskRuleVersion, RiskAlert, RiskAlertOperatorResponse, RiskAlertComment,
    AuditCase, AuditFinding, AuditEvidence, AuditOperatorResponse, AuditCaseComment, AuditCaseReport,
    TariffComplianceResult,
)


class TariffAdmin(admin.ModelAdmin):
    list_display = ['name', 'operator_code', 'service_type', 'traffic_type', 'rate',
                    'charging_unit', 'status', 'effective_from', 'effective_to', 'version']
    list_filter = ['status', 'is_regulatory_reference', 'operator_code', 'service_type', 'traffic_type']
    search_fields = ['name', 'operator_code']
    readonly_fields = ['created_at', 'updated_at']


class TaxRateInline(admin.TabularInline):
    model = TaxRate
    extra = 1


class TaxTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active']
    inlines = [TaxRateInline]


class DeclarationLineItemInline(admin.TabularInline):
    model = DeclarationLineItem
    extra = 1


class OperatorDeclarationAdmin(admin.ModelAdmin):
    list_display = ['operator_code', 'period_start', 'period_end', 'status',
                    'total_declared_revenue', 'total_declared_tax']
    list_filter = ['status', 'operator_code']
    inlines = [DeclarationLineItemInline]


class ReconciliationResultInline(admin.TabularInline):
    model = ReconciliationResult
    extra = 0
    readonly_fields = ['operator_code', 'service_type', 'mediated_revenue',
                       'declared_revenue', 'variance_revenue', 'variance_pct', 'match_status']


class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ['operator_code', 'level', 'period_start', 'period_end', 'status']
    list_filter = ['status', 'level']
    inlines = [ReconciliationResultInline]


class AuditFindingInline(admin.TabularInline):
    model = AuditFinding
    extra = 0


class AuditEvidenceInline(admin.TabularInline):
    model = AuditEvidence
    extra = 0


class AuditOperatorResponseInline(admin.TabularInline):
    model = AuditOperatorResponse
    extra = 0


class AuditCaseReportInline(admin.TabularInline):
    model = AuditCaseReport
    extra = 0
    readonly_fields = ['version', 'file_format', 'checksum', 'case_status_at_generation', 'generated_by', 'generated_at']


class AuditCaseAdmin(admin.ModelAdmin):
    list_display = ['case_number', 'title', 'operator_code', 'case_type', 'status',
                    'risk_level', 'priority', 'potential_exposure', 'assigned_to']
    list_filter = ['status', 'case_type', 'risk_level', 'priority', 'operator_code']
    search_fields = ['case_number', 'title', 'operator_code']
    inlines = [AuditFindingInline, AuditEvidenceInline, AuditOperatorResponseInline, AuditCaseReportInline]


admin.site.register(Tariff, TariffAdmin)
admin.site.register(TaxType, TaxTypeAdmin)
admin.site.register(TaxRate)
admin.site.register(TrafficSummary)
admin.site.register(RatedAggregate)
admin.site.register(RevenueSnapshot)
admin.site.register(OperatorDeclaration, OperatorDeclarationAdmin)
admin.site.register(ReconciliationRun, ReconciliationRunAdmin)
class RiskRuleVersionInline(admin.TabularInline):
    model = RiskRuleVersion
    extra = 0


class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'alert_type', 'status', 'severity', 'comparison_operator', 'threshold_value', 'threshold_unit', 'version']
    list_filter = ['status', 'severity', 'alert_type']
    inlines = [RiskRuleVersionInline]


class RiskAlertCommentInline(admin.TabularInline):
    model = RiskAlertComment
    extra = 0


class RiskAlertOperatorResponseInline(admin.TabularInline):
    model = RiskAlertOperatorResponse
    extra = 0


class RiskAlertAdmin(admin.ModelAdmin):
    list_display = ['reference', 'operator_code', 'alert_type', 'severity', 'status', 'source_type', 'triggered_at']
    list_filter = ['status', 'severity', 'alert_type', 'source_type']
    search_fields = ['reference', 'operator_code', 'description']
    inlines = [RiskAlertCommentInline, RiskAlertOperatorResponseInline]


admin.site.register(RiskRule, RiskRuleAdmin)
admin.site.register(RiskAlert, RiskAlertAdmin)
admin.site.register(AuditCase, AuditCaseAdmin)
admin.site.register(AuditCaseComment)
admin.site.register(TariffComplianceResult)
