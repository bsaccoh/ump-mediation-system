from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('set-operator/', views.set_active_operator, name='set_operator'),
    path('summary/api/', views.processing_summary_api, name='summary_api'),
    path('pipeline/api/', views.pipeline_api, name='pipeline_api'),
    path('run-collection/', views.run_collection, name='run_collection'),
    path('kpis/api/', views.dashboard_kpis_api, name='kpis_api'),
    path('monitoring/api/system-health', views.system_health_api, name='system_health_api'),
    path('monitoring/api/chart-data', views.dashboard_chart_data_api, name='chart_data_api'),
    path('queue/', views.processing_queue, name='processing_queue'),
    path('queue/api/', views.processing_queue_api, name='processing_queue_api'),
    path('queue/stop-all/', views.stop_all_processing, name='stop_all_processing'),

    # Unified CDR Search
    path('search/', views.cdr_search, name='cdr_search'),
    path('search/api/', views.cdr_search_api, name='cdr_search_api'),
    path('search/export/', views.cdr_export, name='cdr_export'),

    # Unified CDR Detail
    path('record/<str:stream>/<int:pk>/', views.cdr_detail, name='cdr_detail'),

    # Subscriber
    path('subscriber/', views.subscriber_view, name='subscriber'),
    path('subscriber/api/', views.subscriber_api, name='subscriber_api'),

    # File Registry
    path('file-registry/', views.file_registry, name='file_registry'),
    path('file-registry/api/', views.file_registry_api, name='file_registry_api'),
    path('file-registry/reprocess/', views.file_registry_reprocess, name='file_registry_reprocess'),
    path('file-registry/export/', views.file_registry_export, name='file_registry_export'),
    path('file-registry/download/<int:pk>/', views.file_download, name='file_download'),

    # Analytics
    path('analytics/', views.analytics_view, name='analytics'),
    path('analytics/api/', views.analytics_api, name='analytics_api'),

    # Traffic Matrix (operator-to-operator)
    path('traffic-matrix/', views.traffic_matrix_view, name='traffic_matrix'),
    path('traffic-matrix/api/', views.traffic_matrix_api, name='traffic_matrix_api'),

    # Duplicate Management
    path('duplicates/', views.duplicates_view, name='duplicates'),
    path('duplicates/api/', views.duplicates_api, name='duplicates_api'),
    path('duplicates/delete/', views.duplicates_delete, name='duplicates_delete'),

    # Processing Errors
    path('errors/', views.errors_view, name='errors'),
    path('errors/api/', views.errors_api, name='errors_api'),

    # Activity Log
    path('activity-log/', views.activity_log_view, name='activity_log'),
    path('activity-log/api/', views.activity_log_api, name='activity_log_api'),
    path('activity-log/clear/', views.activity_log_clear, name='activity_log_clear'),

    # Reports
    path('reports/', views.reports_view, name='reports'),
    path('reports/api/', views.reports_api, name='reports_api'),
    path('reports/export/', views.reports_export, name='reports_export'),

    # Service Control
    path('services/', views.services_view, name='services'),
    path('services/api/', views.services_api, name='services_api'),
    path('services/action/', views.services_action, name='services_action'),
    path('services/intake-toggle/', views.intake_toggle, name='intake_toggle'),

    # System Monitoring (Master Reference)
    path('monitoring/', views.system_monitoring_view, name='system_monitoring'),
    path('monitoring/api/overview/', views.system_monitoring_overview_api, name='monitoring_overview_api'),
    path('monitoring/api/timeseries/', views.system_monitoring_timeseries_api, name='monitoring_timeseries_api'),
    path('monitoring/api/error-detail/<str:error_id>/', views.system_monitoring_error_detail_api, name='monitoring_error_detail_api'),

    # Mediation Flow Monitoring & Reconciliation
    path('monitoring/api/flow/summary/', views.flow_monitoring_summary_api, name='flow_monitoring_summary_api'),
    path('monitoring/api/flow/collection/', views.flow_collection_api, name='flow_collection_api'),
    path('monitoring/api/flow/processing/', views.flow_processing_api, name='flow_processing_api'),
    path('monitoring/api/flow/distribution/', views.flow_distribution_api, name='flow_distribution_api'),
    path('monitoring/api/flow/reconciliation/', views.flow_reconciliation_api, name='flow_reconciliation_api'),

    # Backlog, Storage, and Alarms
    path('monitoring/api/backlog/', views.backlog_api, name='backlog_api'),
    path('monitoring/api/storage/', views.storage_api, name='storage_api'),
    path('monitoring/api/alarms/', views.active_alarms_api, name='active_alarms_api'),
]

