from django.urls import path

from . import views

app_name = 'drive_test_api'

urlpatterns = [
    path('campaigns/<int:pk>/map/', views.campaign_map, name='campaign_map'),
    path('campaigns/<int:pk>/timeseries/', views.campaign_timeseries, name='campaign_timeseries'),
    path('campaigns/<int:pk>/thresholds/', views.campaign_thresholds, name='campaign_thresholds'),
    path('samples/<int:pk>/', views.sample_detail, name='sample_detail'),
]
