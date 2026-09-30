from django.urls import include, path

from . import views

app_name = 'drive_test'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Projects
    path('projects/', views.project_list, name='project_list'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:pk>/', views.project_detail, name='project_detail'),
    path('projects/<int:pk>/edit/', views.project_edit, name='project_edit'),
    path('projects/<int:project_pk>/campaigns/new/', views.campaign_create, name='campaign_create'),

    # Campaigns
    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:pk>/edit/', views.campaign_edit, name='campaign_edit'),
    path('campaigns/<int:campaign_pk>/upload/', views.file_upload, name='file_upload'),
    path('campaigns/<int:pk>/analysis/', views.map_analysis, name='map_analysis'),
    path('campaigns/<int:pk>/analytics/', views.campaign_analytics, name='campaign_analytics'),
    path('campaigns/<int:pk>/analytics/<str:section>/', views.campaign_analytics, name='campaign_analytics_section'),
    path('campaigns/<int:pk>/events/', views.event_list, name='event_list'),
    path('campaigns/<int:pk>/events/detect/', views.campaign_detect, name='campaign_detect'),
    path('campaigns/<int:pk>/problem-areas/', views.problem_area_list, name='problem_area_list'),

    # Events
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/<int:pk>/status/', views.event_set_status, name='event_set_status'),

    # Reports
    path('reports/', views.report_list, name='report_list'),
    path('reports/generate/', views.report_generate, name='report_generate'),
    path('reports/<str:ref>/', views.report_detail, name='report_detail'),
    path('reports/<str:ref>/download/', views.report_download, name='report_download'),

    # Choosers + comparison + JSON APIs
    path('map/', views.map_index, name='map_index'),
    path('events/', views.events_index, name='events_index'),
    path('comparison/', views.comparison_view, name='comparison'),
    path('ai-analyst/', views.ai_analyst, name='ai_analyst'),
    path('analytics/<str:section>/', views.analytics_index, name='analytics_index'),
    path('api/', include('drive_test.api.urls')),

    # Files
    path('files/', views.file_list, name='file_list'),
    path('files/<int:pk>/process/', views.file_process, name='file_process'),
    path('files/<int:pk>/status/', views.file_status, name='file_status'),
    path('files/<int:pk>/delete/', views.file_delete, name='file_delete'),

    # Processing monitor
    path('processing/', views.processing_monitor, name='processing_monitor'),

    # Sections delivered in later phases (honest placeholders keep nav complete).
    path('<str:section>/', views.placeholder, name='placeholder'),
]
