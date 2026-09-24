from django.contrib import admin

from .models import (
    Cell, CellHistory, Chiefdom, DataQualityResult,
    DeviceManufacturer, DeviceModel, District, DriveTestFile, DriveTestSession,
    Finding, FrequencyBand, NetworkVendor, ParserProfile,
    Region, RegulatoryRule, RegulatoryThreshold, Sector, Site, TestDevice,
)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'country']
    search_fields = ['code', 'name']


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'region']
    list_filter = ['region']
    search_fields = ['code', 'name']


@admin.register(Chiefdom)
class ChiefdomAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'district']
    list_filter = ['district__region']
    search_fields = ['code', 'name']


@admin.register(NetworkVendor)
class NetworkVendorAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']


@admin.register(FrequencyBand)
class FrequencyBandAdmin(admin.ModelAdmin):
    list_display = ['band_number', 'technology', 'frequency_mhz', 'bandwidth_mhz', 'duplex_mode']
    list_filter = ['technology']


class SectorInline(admin.TabularInline):
    model = Sector
    extra = 0
    fields = ['sector_id', 'azimuth_deg', 'height_m', 'is_active']


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['site_id', 'operator', 'name', 'site_type', 'chiefdom', 'is_active']
    list_filter = ['operator', 'site_type', 'is_active']
    search_fields = ['site_id', 'name']
    inlines = [SectorInline]


class CellInline(admin.TabularInline):
    model = Cell
    extra = 0
    fields = ['cell_id', 'technology', 'mcc', 'mnc', 'lac', 'tac', 'ci', 'eci', 'pci', 'is_active']


@admin.register(Cell)
class CellAdmin(admin.ModelAdmin):
    list_display = ['cell_id', 'operator', 'technology', 'cgi', 'ecgi', 'pci', 'is_active']
    list_filter = ['operator', 'technology', 'is_active']
    search_fields = ['cell_id', 'cgi', 'ecgi']
    readonly_fields = ['cgi', 'ecgi', 'created_at', 'updated_at']


@admin.register(DeviceManufacturer)
class DeviceManufacturerAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(DeviceModel)
class DeviceModelAdmin(admin.ModelAdmin):
    list_display = ['manufacturer', 'model_name']
    list_filter = ['manufacturer']
    search_fields = ['model_name']


@admin.register(TestDevice)
class TestDeviceAdmin(admin.ModelAdmin):
    list_display = ['serial_number', 'label', 'device_model', 'operator', 'is_active']
    list_filter = ['operator', 'is_active']
    search_fields = ['serial_number', 'imei', 'label']


@admin.register(ParserProfile)
class ParserProfileAdmin(admin.ModelAdmin):
    list_display = ['name', 'vendor', 'file_extensions', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name', 'vendor']


class DriveTestFileInline(admin.TabularInline):
    model = DriveTestFile
    extra = 0
    fields = ['original_filename', 'status', 'measurement_count', 'uploaded_at']
    readonly_fields = ['sha256', 'uploaded_at']


@admin.register(DriveTestSession)
class DriveTestSessionAdmin(admin.ModelAdmin):
    list_display = [
        'session_ref', 'operator', 'test_date', 'test_type', 'status',
        'total_measurements', 'finding_count',
    ]
    list_filter = ['operator', 'status', 'test_type']
    search_fields = ['session_ref', 'title']
    readonly_fields = ['session_ref', 'created_at', 'updated_at']
    inlines = [DriveTestFileInline]


@admin.register(DriveTestFile)
class DriveTestFileAdmin(admin.ModelAdmin):
    list_display = [
        'original_filename', 'session', 'status', 'measurement_count',
        'detected_format', 'uploaded_at',
    ]
    list_filter = ['status', 'detected_format']
    search_fields = ['original_filename', 'sha256']
    readonly_fields = ['sha256', 'uploaded_at', 'updated_at']


@admin.register(RegulatoryRule)
class RegulatoryRuleAdmin(admin.ModelAdmin):
    list_display = ['rule_code', 'name', 'technology', 'metric', 'condition', 'is_active']
    list_filter = ['technology', 'service_type', 'is_active', 'authority']
    search_fields = ['rule_code', 'name']


@admin.register(RegulatoryThreshold)
class RegulatoryThresholdAdmin(admin.ModelAdmin):
    list_display = ['rule', 'operator', 'critical_value', 'warning_value', 'unit', 'effective_from']
    list_filter = ['rule', 'operator']


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'finding_type', 'severity', 'is_resolved', 'created_at']
    list_filter = ['severity', 'finding_type', 'is_resolved']
    search_fields = ['description', 'session__session_ref']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(DataQualityResult)
class DataQualityResultAdmin(admin.ModelAdmin):
    list_display = [
        'drive_file', 'total_records', 'valid_records', 'match_rate_pct',
        'overall_score', 'created_at',
    ]
    readonly_fields = ['created_at']
