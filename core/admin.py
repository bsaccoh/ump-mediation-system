from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, AuditLog, Alert


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_operator', 'is_analyst', 'is_staff')
    list_filter = BaseUserAdmin.list_filter + ('is_operator', 'is_analyst')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Mediation Roles', {
            'fields': ('phone', 'department', 'is_operator', 'is_analyst'),
        }),
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'entity_type', 'entity_id', 'description')
    list_filter = ('action', 'entity_type')
    search_fields = ('description', 'entity_id')
    readonly_fields = ('timestamp', 'user', 'action', 'entity_type', 'entity_id',
                       'description', 'ip_address', 'extra_data')
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'severity', 'category', 'source', 'message_short', 'acknowledged')
    list_filter = ('severity', 'category', 'acknowledged')
    search_fields = ('message', 'source')
    date_hierarchy = 'timestamp'

    @admin.display(description='Message')
    def message_short(self, obj):
        return obj.message[:100]
