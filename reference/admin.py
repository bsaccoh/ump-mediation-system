from django.contrib import admin
from .models import MccMnc, ImsiPrefix, NumberingPlan, TrunkGroup, NumberRange


@admin.register(MccMnc)
class MccMncAdmin(admin.ModelAdmin):
    list_display = ('mcc', 'mnc', 'country', 'operator', 'is_home', 'enabled')
    search_fields = ('mcc', 'mnc', 'country', 'operator')
    list_filter = ('country', 'is_home', 'enabled')


@admin.register(ImsiPrefix)
class ImsiPrefixAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'mcc', 'mnc', 'operator', 'country', 'is_home', 'enabled')
    search_fields = ('prefix', 'mcc', 'mnc', 'operator', 'country')
    list_filter = ('country', 'is_home', 'enabled')


@admin.register(NumberingPlan)
class NumberingPlanAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'country_code', 'operator', 'country', 'number_type', 'enabled')
    search_fields = ('prefix', 'operator', 'country')
    list_filter = ('number_type', 'country', 'enabled')


@admin.register(TrunkGroup)
class TrunkGroupAdmin(admin.ModelAdmin):
    list_display = ('trunk_id', 'name', 'trunk_type', 'direction', 'partner', 'country', 'enabled')
    list_filter = ('trunk_type', 'direction', 'enabled')
    search_fields = ('trunk_id', 'name', 'partner')


@admin.register(NumberRange)
class NumberRangeAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'operator', 'country', 'service_type')
    search_fields = ('prefix', 'operator')
    list_filter = ('operator', 'country')
