from django.urls import path
from . import views

app_name = 'sgsn'

urlpatterns = [
    path('search/', views.sgsn_search, name='sgsn_search'),
    path('api/search/', views.sgsn_search_api, name='sgsn_search_api'),
    path('api/export/', views.sgsn_export, name='sgsn_export'),
    path('<int:pk>/', views.sgsn_detail, name='sgsn_detail'),
]
