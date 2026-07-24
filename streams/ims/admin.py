from django.contrib import admin
from .models import IMSRecord


@admin.register(IMSRecord)
class IMSRecordAdmin(admin.ModelAdmin):
    list_display = (
        'record_type', 'service_type', 'sip_method', 'role_of_node',
        'calling_number', 'called_number', 'imsi', 'start_time',
        'duration', 'call_category', 'status',
    )
    list_filter = (
        'service_type', 'record_type', 'status', 'role_of_node',
        'sip_method', 'call_category', 'call_property',
    )
    search_fields = (
        'calling_number', 'called_number', 'imsi', 'msisdn',
        'session_id', 'icid', 'charged_party',
    )
    readonly_fields = ('file', 'source', 'raw_data', 'created_at')
    date_hierarchy = 'start_time'
    list_per_page = 50

    fieldsets = (
        ('Record Identity', {
            'fields': ('file', 'source', 'record_type', 'service_type',
                       'role_of_node', 'sip_method', 'session_id', 'icid'),
        }),
        ('Parties', {
            'fields': ('calling_number', 'called_number', 'dialed_number',
                       'charged_party', 'calling_sip_uri', 'called_sip_uri'),
        }),
        ('Subscriber', {
            'fields': ('imsi', 'msisdn', 'prepaid_flag'),
        }),
        ('Timing', {
            'fields': ('request_time', 'start_time', 'end_time',
                       'duration', 'ringing_duration'),
        }),
        ('Network & Routing', {
            'fields': ('node_address', 'msc_number', 'vlr_number',
                       'originating_ioi', 'terminating_ioi',
                       'np_routing_number', 'carrier_code',
                       'call_property', 'roaming_indicator', 'call_category'),
        }),
        ('Charging & Service', {
            'fields': ('charging_category', 'service_context_id',
                       'sequence_number', 'cause_for_closing',
                       'service_reason_code', 'online_charging_flag',
                       'supplementary_service', 'diversion_reason', 'diversion_count'),
        }),
        ('Raw Data', {
            'classes': ('collapse',),
            'fields': ('raw_data',),
        }),
        ('Status', {
            'fields': ('status', 'created_at'),
        }),
    )
