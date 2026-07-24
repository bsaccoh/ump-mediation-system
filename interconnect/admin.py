from django.contrib import admin

from .models import (
    InterconnectPartner, InterconnectRate, ExchangeRate, BillingCycle,
    Invoice, InvoiceLine, ReconciliationRecord, Settlement,
)


@admin.register(InterconnectPartner)
class InterconnectPartnerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'country', 'is_home', 'is_local',
                    'default_currency', 'is_active')
    list_filter = ('is_home', 'is_local', 'is_active', 'country')
    search_fields = ('code', 'name', 'country', 'mcc', 'mnc')


@admin.register(InterconnectRate)
class InterconnectRateAdmin(admin.ModelAdmin):
    list_display = ('partner', 'direction', 'service_type', 'destination_type',
                    'time_of_day', 'rate', 'unit', 'currency',
                    'effective_from', 'effective_to', 'is_active')
    list_filter = ('direction', 'service_type', 'destination_type',
                   'time_of_day', 'is_active', 'partner')
    search_fields = ('partner__code', 'partner__name', 'notes')


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('from_currency', 'to_currency', 'rate',
                    'effective_date', 'source')
    list_filter = ('from_currency', 'to_currency', 'source')
    date_hierarchy = 'effective_date'


@admin.register(BillingCycle)
class BillingCycleAdmin(admin.ModelAdmin):
    list_display = ('partner', 'period_start', 'period_end', 'status',
                    'our_voice_minutes', 'our_sms', 'our_data_mb',
                    'variance_pct')
    list_filter = ('status', 'partner')
    date_hierarchy = 'period_end'


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


class SettlementInline(admin.TabularInline):
    model = Settlement
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'partner', 'billing_cycle', 'direction',
                    'total', 'currency', 'status', 'issued_at', 'due_date')
    list_filter = ('status', 'direction', 'partner', 'currency')
    search_fields = ('invoice_number', 'partner__code', 'notes')
    inlines = [InvoiceLineInline, SettlementInline]
    date_hierarchy = 'created_at'


@admin.register(ReconciliationRecord)
class ReconciliationRecordAdmin(admin.ModelAdmin):
    list_display = ('partner', 'billing_cycle', 'service_type',
                    'destination_type', 'our_volume', 'partner_volume',
                    'variance_pct', 'status')
    list_filter = ('status', 'service_type', 'destination_type', 'partner')


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'currency', 'payment_date',
                    'payment_method', 'payment_reference')
    list_filter = ('payment_method', 'currency')
    date_hierarchy = 'payment_date'
    search_fields = ('payment_reference', 'invoice__invoice_number')
