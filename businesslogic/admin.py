from django.contrib import admin
from .models import BusinessRule, RuleExecutionLog


@admin.register(BusinessRule)
class BusinessRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'rule_type', 'stream', 'status', 'priority', 'updated_at')
    list_filter = ('rule_type', 'stream', 'status', 'priority')
    search_fields = ('name', 'description', 'tags')


@admin.register(RuleExecutionLog)
class RuleExecutionLogAdmin(admin.ModelAdmin):
    list_display = ('rule', 'result', 'records_in', 'records_out', 'executed_at', 'duration_ms')
    list_filter = ('result',)
    raw_id_fields = ('rule',)
