from django.contrib import admin
from .models import Script, ScriptExecution


@admin.register(Script)
class ScriptAdmin(admin.ModelAdmin):
    list_display  = ['name', 'script_type', 'status', 'created_by', 'created_at', 'updated_at']
    list_filter   = ['script_type', 'status']
    search_fields = ['name', 'description', 'tags']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ScriptExecution)
class ScriptExecutionAdmin(admin.ModelAdmin):
    list_display  = ['script', 'run_by', 'run_status', 'started_at', 'duration_ms']
    list_filter   = ['run_status']
    readonly_fields = ['started_at', 'finished_at']
