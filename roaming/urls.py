"""Roaming URL patterns."""
from django.urls import path
from . import views

app_name = 'roaming'

urlpatterns = [
    path('', views.index, name='index'),

    # 1. Roaming Partners (filtered InterconnectPartner CRUD)
    path('partners/', views.partner_list, name='partner_list'),
    path('partners/api/', views.partner_api, name='partner_api'),
    path('partners/save/', views.partner_save, name='partner_save'),
    path('partners/<int:pk>/delete/', views.partner_delete, name='partner_delete'),

    # 2. Detection (scan CDRs for inbound roamers)
    path('detect/', views.detect_view, name='detect'),
    path('detect/api/', views.detect_api, name='detect_api'),

    # 3. Cycles (BillingCycle with is_roaming=True)
    path('cycles/', views.cycle_list, name='cycle_list'),
    path('cycles/api/', views.cycle_api, name='cycle_api'),
    path('cycles/save/', views.cycle_save, name='cycle_save'),
    path('cycles/<int:pk>/delete/', views.cycle_delete, name='cycle_delete'),

    # 4. File generation + browse
    path('files/', views.file_list, name='file_list'),
    path('files/api/', views.file_api, name='file_api'),
    path('files/generate/', views.file_generate, name='file_generate'),
    path('files/<int:pk>/', views.file_detail, name='file_detail'),
    path('files/<int:pk>/csv/', views.file_csv, name='file_csv'),
    path('files/<int:pk>/status/', views.file_set_status, name='file_set_status'),
    path('files/<int:pk>/delete/', views.file_delete, name='file_delete'),

    # 5. Disputes
    path('disputes/', views.dispute_list, name='dispute_list'),
    path('disputes/api/', views.dispute_api, name='dispute_api'),
    path('disputes/save/', views.dispute_save, name='dispute_save'),
    path('disputes/<int:pk>/delete/', views.dispute_delete, name='dispute_delete'),

    # 6. Reports
    path('reports/', views.reports_view, name='reports_view'),
    path('reports/api/', views.reports_api, name='reports_api'),
]
