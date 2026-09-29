from django.urls import path

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

    # Files
    path('files/', views.file_list, name='file_list'),
    path('files/<int:pk>/delete/', views.file_delete, name='file_delete'),

    # Sections delivered in later phases (honest placeholders keep nav complete).
    path('<str:section>/', views.placeholder, name='placeholder'),
]
