from django.contrib import admin

from .models import (
    RegulatoryProfile, RetailRevenue, RegulatoryReport,
    LeviedPeriod, LEARequest, LEAExtractionLog, QoSMetric,
)


@admin.register(RegulatoryProfile)
class RegulatoryProfileAdmin(admin.ModelAdmin):
    list_display = ('regulator_name', 'levy_pct', 'usf_pct',
                    'home_currency', 'updated_at')


@admin.register(RetailRevenue)
class RetailRevenueAdmin(admin.ModelAdmin):
    list_display = ('period_year', 'period_month', 'voice_revenue',
                    'sms_revenue', 'data_revenue', 'other_revenue', 'currency')
    list_filter = ('period_year', 'currency')


@admin.register(RegulatoryReport)
class RegulatoryReportAdmin(admin.ModelAdmin):
    list_display = ('report_type', 'period_start', 'period_end',
                    'status', 'generated_at', 'generated_by')
    list_filter = ('report_type', 'status')
    date_hierarchy = 'generated_at'


@admin.register(LeviedPeriod)
class LeviedPeriodAdmin(admin.ModelAdmin):
    list_display = ('period_year', 'period_month', 'gross_revenue',
                    'levy_amount', 'usf_amount', 'total_payable',
                    'currency', 'status')
    list_filter = ('status', 'period_year')


class LEAExtractionLogInline(admin.TabularInline):
    model = LEAExtractionLog
    extra = 0
    readonly_fields = ('executed_at', 'executed_by', 'record_count',
                       'sha256', 'criteria_json')


@admin.register(LEARequest)
class LEARequestAdmin(admin.ModelAdmin):
    list_display = ('case_number', 'requesting_agency', 'officer_name',
                    'filter_msisdn', 'filter_imsi', 'status', 'opened_at')
    list_filter = ('status', 'requesting_agency')
    search_fields = ('case_number', 'requesting_agency', 'officer_name',
                     'filter_msisdn', 'filter_imsi', 'filter_imei')
    inlines = [LEAExtractionLogInline]
    date_hierarchy = 'opened_at'


@admin.register(LEAExtractionLog)
class LEAExtractionLogAdmin(admin.ModelAdmin):
    list_display = ('request', 'executed_at', 'executed_by',
                    'record_count', 'sha256')
    readonly_fields = ('request', 'executed_at', 'executed_by',
                       'record_count', 'sha256', 'criteria_json', 'export_file')
    date_hierarchy = 'executed_at'


@admin.register(QoSMetric)
class QoSMetricAdmin(admin.ModelAdmin):
    list_display = ('metric_date', 'granularity', 'asr_pct', 'acd_seconds',
                    'drop_rate_pct', 'availability_pct', 'source')
    list_filter = ('granularity', 'source')
    date_hierarchy = 'metric_date'
