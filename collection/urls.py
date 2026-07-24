from django.urls import path
from . import views

app_name = 'collection'

urlpatterns = [
    path('', views.file_list, name='file_list'),
    path('upload/', views.upload_file, name='upload'),
    path('file/<int:pk>/', views.file_detail, name='file_detail'),
    path('file/<int:pk>/reprocess/', views.reprocess_file, name='reprocess'),
    path('sftp-poll/<int:source_id>/', views.poll_sftp_now, name='sftp_poll'),
    path('distribution/', views.distribution_dashboard, name='distribution_dashboard'),
    path('distribution/<int:log_id>/view/', views.distribution_log_view, name='distribution_log_view'),
    path('distribution/<int:log_id>/download/', views.distribution_log_download, name='distribution_log_download'),
    path('distribution/<int:log_id>/retry/', views.distribution_log_retry, name='distribution_log_retry'),
    path('distribution/bulk-retry/', views.distribution_log_bulk_retry, name='distribution_log_bulk_retry'),
]
