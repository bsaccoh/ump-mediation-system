from django.urls import path
from . import views

app_name = 'sgw'

urlpatterns = [
    path('search/', views.sgw_search, name='sgw_search'),
    path('api/search/', views.sgw_search_api, name='sgw_search_api'),
    path('api/export/', views.sgw_export, name='sgw_export'),
    path('<int:pk>/', views.sgw_detail, name='sgw_detail'),
]
