from django.urls import path
from . import views

app_name = 'reference'

urlpatterns = [
    # MCC/MNC
    path('mcc-mnc/', views.mcc_mnc_list, name='mcc_mnc'),
    path('mcc-mnc/api/', views.mcc_mnc_api, name='mcc_mnc_api'),
    path('mcc-mnc/save/', views.mcc_mnc_save, name='mcc_mnc_save'),
    path('mcc-mnc/delete/<int:pk>/', views.mcc_mnc_delete, name='mcc_mnc_delete'),

    # IMSI Prefix
    path('imsi-prefix/', views.imsi_prefix_list, name='imsi_prefix'),
    path('imsi-prefix/api/', views.imsi_prefix_api, name='imsi_prefix_api'),
    path('imsi-prefix/save/', views.imsi_prefix_save, name='imsi_prefix_save'),
    path('imsi-prefix/delete/<int:pk>/', views.imsi_prefix_delete, name='imsi_prefix_delete'),

    # Numbering Plan
    path('numbering-plan/', views.numbering_plan_list, name='numbering_plan'),
    path('numbering-plan/api/', views.numbering_plan_api, name='numbering_plan_api'),
    path('numbering-plan/save/', views.numbering_plan_save, name='numbering_plan_save'),
    path('numbering-plan/delete/<int:pk>/', views.numbering_plan_delete, name='numbering_plan_delete'),

    # Trunk Groups
    path('trunks/', views.trunk_list, name='trunks'),
    path('trunks/api/', views.trunk_api, name='trunk_api'),
    path('trunks/save/', views.trunk_save, name='trunk_save'),
    path('trunks/delete/<int:pk>/', views.trunk_delete, name='trunk_delete'),

    # CSV Import
    path('import/<str:table>/', views.csv_import, name='csv_import'),
]
