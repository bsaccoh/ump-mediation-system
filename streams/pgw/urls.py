from django.urls import path
from . import views

app_name = 'pgw'

urlpatterns = [
    path('search/', views.pgw_search, name='pgw_search'),
    path('api/search/', views.pgw_search_api, name='pgw_search_api'),
    path('api/export/', views.pgw_export, name='pgw_export'),
    path('<int:pk>/', views.pgw_detail, name='pgw_detail'),
]
