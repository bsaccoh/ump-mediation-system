from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('set-operator/', views.set_active_operator, name='set_operator'),
    path('summary/api/', views.processing_summary_api, name='summary_api'),
    path('run-collection/', views.run_collection, name='run_collection'),
    path('kpis/api/', views.dashboard_kpis_api, name='kpis_api'),
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

    # Analytics
    path('analytics/', views.analytics_view, name='analytics'),
    path('analytics/api/', views.analytics_api, name='analytics_api'),

    # Traffic Matrix (operator-to-operator)
    path('traffic-matrix/', views.traffic_matrix_view, name='traffic_matrix'),
    path('traffic-matrix/api/', views.traffic_matrix_api, name='traffic_matrix_api'),
]
