from django.contrib import admin
from .models import MSCRecord


@admin.register(MSCRecord)
class MSCRecordAdmin(admin.ModelAdmin):
    list_display = ('record_type', 'service_type', 'calling_number', 'called_number',
                    'imsi', 'start_time', 'duration', 'msc_id', 'status')
    list_filter = ('service_type', 'record_type', 'status', 'rat_type')
    search_fields = ('calling_number', 'called_number', 'imsi', 'imei',
                     'charged_msisdn', 'network_record_id')
    readonly_fields = ('file', 'source', 'raw_data', 'created_at')
    date_hierarchy = 'start_time'
    list_per_page = 50

    fieldsets = (
        ('Record Identity', {
            'fields': ('file', 'source', 'record_type', 'service_type',
                       'network_record_id', 'call_reference', 'call_direction'),
        }),
        ('Parties', {
            'fields': ('calling_number', 'called_number', 'dialed_number',
                       'charged_msisdn', 'imsi', 'imei', 'imsi_b', 'imei_b'),
        }),
        ('Timing', {
            'fields': ('start_time', 'end_time', 'duration'),
        }),
        ('Location & Network', {
            'fields': ('cell_id', 'lac', 'msc_id', 'rat_type',
                       'originating_trunk', 'terminating_trunk'),
        }),
        ('Service & Result', {
            'fields': ('teleservice_code', 'bearer_service_code',
                       'result_code', 'roaming_indicator'),
        }),
        ('Raw Data', {
            'classes': ('collapse',),
            'fields': ('raw_data',),
        }),
        ('Status', {
            'fields': ('status', 'created_at'),
        }),
    )
