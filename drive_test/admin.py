from django.contrib import admin

from .models import (
    Project, Campaign, Route, Cell, DriveTestFile, Sample,
    NeighborMeasurement, Event, ProblemArea, KpiThreshold, KpiResult, Report,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'region', 'created_by', 'created_at')
    list_filter = ('status', 'region')
    search_fields = ('name', 'description')


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'operator', 'technology', 'created_at')
    list_filter = ('technology', 'operator')
    search_fields = ('name',)
    raw_id_fields = ('project',)


@admin.register(DriveTestFile)
class DriveTestFileAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'campaign', 'detected_format', 'status', 'uploaded_at')
    list_filter = ('status', 'detected_format')
    search_fields = ('original_name', 'sha256')
    raw_id_fields = ('campaign', 'job')


@admin.register(Cell)
class CellAdmin(admin.ModelAdmin):
    list_display = ('cell_id', 'technology', 'operator', 'pci', 'band')
    list_filter = ('technology',)
    search_fields = ('cell_id', 'site_name')


@admin.register(KpiThreshold)
class KpiThresholdAdmin(admin.ModelAdmin):
    list_display = ('metric', 'technology', 'operator', 'is_active')
    list_filter = ('metric', 'technology', 'is_active')


admin.site.register([Route, Sample, NeighborMeasurement, Event, ProblemArea, KpiResult, Report])
