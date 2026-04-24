from django.contrib import admin

from .models import InputPortal, OutputPortal, Plugin, Resource


@admin.register(InputPortal)
class InputPortalAdmin(admin.ModelAdmin):
    list_display = ('name', 'portal_type', 'stream_type', 'host', 'port', 'is_active', 'updated_at')
    list_filter = ('portal_type', 'stream_type', 'is_active')
    search_fields = ('name', 'host', 'directory')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(OutputPortal)
class OutputPortalAdmin(admin.ModelAdmin):
    list_display = ('name', 'portal_type', 'stream_type', 'output_format', 'host', 'is_active', 'updated_at')
    list_filter = ('portal_type', 'stream_type', 'output_format', 'is_active')
    search_fields = ('name', 'host', 'directory')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    list_display = ('name', 'plugin_type', 'version', 'author', 'is_active', 'updated_at')
    list_filter = ('plugin_type', 'is_active')
    search_fields = ('name', 'author', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'resource_type', 'status', 'host', 'is_monitored', 'updated_at')
    list_filter = ('resource_type', 'status', 'is_monitored')
    search_fields = ('name', 'host', 'description')
    readonly_fields = ('created_at', 'updated_at')
