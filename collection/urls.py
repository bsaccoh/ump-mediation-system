from django.urls import path
from . import views

app_name = 'collection'

urlpatterns = [
    path('', views.file_list, name='file_list'),
    path('upload/', views.upload_file, name='upload'),
    path('file/<int:pk>/', views.file_detail, name='file_detail'),
    path('file/<int:pk>/reprocess/', views.reprocess_file, name='reprocess'),
    path('sftp-poll/<int:source_id>/', views.poll_sftp_now, name='sftp_poll'),
]
