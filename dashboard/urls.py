from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('queue/', views.processing_queue, name='processing_queue'),
    path('queue/api/', views.processing_queue_api, name='processing_queue_api'),

    # Unified CDR Search
    path('search/', views.cdr_search, name='cdr_search'),
    path('search/api/', views.cdr_search_api, name='cdr_search_api'),
    path('search/export/', views.cdr_export, name='cdr_export'),

    # Unified CDR Detail
    path('record/<str:stream>/<int:pk>/', views.cdr_detail, name='cdr_detail'),

    # Subscriber
    path('subscriber/', views.subscriber_view, name='subscriber'),
    path('subscriber/api/', views.subscriber_api, name='subscriber_api'),
]
