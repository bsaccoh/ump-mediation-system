from django.contrib import admin

from .models import RoamingFile, RoamingDispute


class RoamingDisputeInline(admin.TabularInline):
    model = RoamingDispute
    extra = 0
    readonly_fields = ('opened_at', 'resolved_at')


@admin.register(RoamingFile)
class RoamingFileAdmin(admin.ModelAdmin):
    list_display = ('file_number', 'partner', 'direction', 'record_count',
                    'voice_minutes', 'sms_count', 'data_mb',
                    'total_amount', 'currency', 'status', 'generated_at')
    list_filter = ('status', 'direction', 'partner', 'currency')
    search_fields = ('file_number', 'partner__code', 'partner__name')
    date_hierarchy = 'generated_at'
    inlines = [RoamingDisputeInline]


@admin.register(RoamingDispute)
class RoamingDisputeAdmin(admin.ModelAdmin):
    list_display = ('dispute_ref', 'roaming_file', 'status',
                    'claimed_amount', 'variance_amount', 'opened_at')
    list_filter = ('status',)
    search_fields = ('dispute_ref', 'raised_by',
                     'roaming_file__file_number',
                     'roaming_file__partner__code')
    date_hierarchy = 'opened_at'
