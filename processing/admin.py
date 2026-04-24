from django.contrib import admin
from .models import ValidationRule, EnrichmentRule


@admin.register(ValidationRule)
class ValidationRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'stream', 'field_name', 'rule_type', 'rule_value', 'enabled')
    list_filter = ('stream', 'rule_type', 'enabled')
    search_fields = ('name', 'field_name')


@admin.register(EnrichmentRule)
class EnrichmentRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'stream', 'target_field', 'source_field', 'transform', 'condition', 'enabled')
    list_filter = ('stream', 'transform', 'condition', 'enabled')
    search_fields = ('name', 'target_field')
