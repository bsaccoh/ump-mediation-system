from django.urls import path

from . import views

app_name = 'drive_test'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('sessions/', views.session_list, name='session_list'),
    path('sessions/upload/', views.session_upload, name='session_upload'),
    path('sessions/<str:session_ref>/', views.session_detail, name='session_detail'),
    path('sites/', views.site_list, name='site_list'),
    path('cells/', views.cell_list, name='cell_list'),
    path('findings/', views.finding_list, name='finding_list'),
    path('reference/import/', views.reference_import, name='reference_import'),
]
