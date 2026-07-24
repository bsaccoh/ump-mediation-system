from django.contrib import admin
from .models import CBSRecord


@admin.register(CBSRecord)
class CBSRecordAdmin(admin.ModelAdmin):
    list_display  = ('id', 'cbs_type', 'subscriber_id', 'msisdn', 'charge_amount', 'event_time', 'status', 'created_at')
    list_filter   = ('cbs_type', 'status')
    search_fields = ('subscriber_id', 'msisdn')
    readonly_fields = ('raw_data', 'created_at')
    ordering      = ('-created_at',)
