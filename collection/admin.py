from django.contrib import admin
from .models import DataSource, CDRFile, DistributionPortal


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'source_type', 'decoder_type', 'vendor',
                    'network_element', 'enabled', 'files_collected', 'last_poll_time')
    list_filter = ('source_type', 'decoder_type', 'vendor', 'enabled')
    search_fields = ('name', 'description', 'network_element')
    readonly_fields = ('last_poll_time', 'last_poll_status', 'files_collected',
                       'created_at', 'updated_at')

    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'source_type', 'decoder_type',
                       'vendor', 'network_element', 'enabled')
        }),
        ('SFTP Configuration', {
            'classes': ('collapse',),
            'fields': ('sftp_host', 'sftp_port', 'sftp_username', 'sftp_password',
                       'sftp_key_path', 'sftp_remote_path', 'sftp_file_pattern'),
        }),
        ('Local Directory', {
            'classes': ('collapse',),
            'fields': ('local_path',),
        }),
        ('Polling', {
            'fields': ('poll_interval_seconds',),
        }),
        ('Statistics', {
            'fields': ('last_poll_time', 'last_poll_status', 'files_collected',
                       'created_at', 'updated_at'),
        }),
    )


@admin.register(CDRFile)
class CDRFileAdmin(admin.ModelAdmin):
    list_display = ('filename', 'source', 'decoder_type', 'status', 'records_total',
                    'records_valid', 'records_invalid', 'success_rate_display',
                    'processing_duration_display', 'created_at')
    list_filter = ('status', 'decoder_type', 'source')
    search_fields = ('filename',)
    readonly_fields = ('file_hash', 'file_size', 'records_total', 'records_valid',
                       'records_invalid', 'records_duplicate', 'processing_started',
                       'processing_completed', 'created_at', 'uploaded_by')
    date_hierarchy = 'created_at'

    @admin.display(description='Success %')
    def success_rate_display(self, obj):
        return f'{obj.success_rate}%'

    @admin.display(description='Duration')
    def processing_duration_display(self, obj):
        dur = obj.processing_duration
        if dur is not None:
            return f'{dur:.1f}s'
        return '-'


@admin.register(DistributionPortal)
class DistributionPortalAdmin(admin.ModelAdmin):
    list_display = ('name', 'label', 'vendor', 'decoder_type', 'enabled', 'created_at')
    list_filter = ('decoder_type', 'vendor', 'enabled')
    search_fields = ('name', 'label', 'description')
    fieldsets = (
        (None, {
            'fields': ('name', 'label', 'vendor', 'decoder_type', 'enabled', 'description')
        }),
    )
