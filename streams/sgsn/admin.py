from django.contrib import admin
from .models import SGSNRecord


@admin.register(SGSNRecord)
class SGSNRecordAdmin(admin.ModelAdmin):
    list_display = (
        'record_type', 'service_type', 'calling_number', 'apn', 'imsi',
        'start_time', 'duration', 'data_volume_up', 'data_volume_down',
        'rat_type', 'status',
    )
    list_filter = ('record_type', 'service_type', 'status', 'rat_type', 'is_roaming')
    search_fields = ('calling_number', 'imsi', 'imei', 'apn', 'node_id', 'charging_id')
    readonly_fields = ('file', 'source', 'raw_data', 'created_at')
    date_hierarchy = 'start_time'
    list_per_page = 50

    fieldsets = (
        ('Record Identity', {
            'fields': ('file', 'source', 'record_type', 'service_type', 'charging_id'),
        }),
        ('Subscriber', {
            'fields': ('calling_number', 'called_number', 'imsi', 'imei'),
        }),
        ('Timing', {
            'fields': ('start_time', 'end_time', 'duration'),
        }),
        ('Data Volumes', {
            'fields': ('data_volume_up', 'data_volume_down'),
        }),
        ('Network', {
            'fields': ('apn', 'pdp_type', 'rat_type', 'sgsn_address', 'ggsn_address',
                       'node_id', 'serving_plmn', 'cause_for_closing', 'is_roaming'),
        }),
        ('Location', {
            'fields': ('cell_id', 'lac', 'rac'),
        }),
        ('Raw Data', {
            'classes': ('collapse',),
            'fields': ('raw_data',),
        }),
        ('Status', {
            'fields': ('status', 'created_at'),
        }),
    )
