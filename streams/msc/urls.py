from django.urls import path
from . import views

app_name = 'msc'

urlpatterns = [
    path('search/', views.cdr_search, name='cdr_search'),
    path('api/search/', views.cdr_search_api, name='cdr_search_api'),
    path('api/export/', views.cdr_export, name='cdr_export'),
    path('subscriber/', views.subscriber_view, name='subscriber'),
    path('api/subscriber/', views.subscriber_api, name='subscriber_api'),
    path('<int:pk>/', views.cdr_detail, name='cdr_detail'),
]
