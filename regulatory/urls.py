"""Regulatory Service URL patterns."""
from django.urls import path
from . import views

app_name = 'regulatory'

urlpatterns = [
    # Index → redirect to NATCOM Reports
    path('', views.index, name='index'),

    # 1. NATCOM Reports
    path('reports/', views.report_list, name='report_list'),
    path('reports/api/', views.report_api, name='report_api'),
    path('reports/generate/', views.report_generate, name='report_generate'),
    path('reports/<int:pk>/pdf/', views.report_pdf, name='report_pdf'),
    path('reports/<int:pk>/xlsx/', views.report_xlsx, name='report_xlsx'),
    path('reports/<int:pk>/delete/', views.report_delete, name='report_delete'),
    path('reports/<int:pk>/status/', views.report_set_status, name='report_set_status'),

    # 2. Levy & USF
    path('levy/', views.levy_list, name='levy_list'),
    path('levy/api/', views.levy_api, name='levy_api'),
    path('levy/compute/', views.levy_compute, name='levy_compute'),
    path('levy/<int:pk>/pay/', views.levy_mark_paid, name='levy_mark_paid'),
    path('levy/<int:pk>/delete/', views.levy_delete, name='levy_delete'),

    # 3. Retail Revenue (manual entry)
    path('retail/', views.retail_list, name='retail_list'),
    path('retail/api/', views.retail_api, name='retail_api'),
    path('retail/save/', views.retail_save, name='retail_save'),
    path('retail/<int:pk>/delete/', views.retail_delete, name='retail_delete'),

    # 4. Lawful Intercept (gated)
    path('intercept/', views.intercept_list, name='intercept_list'),
    path('intercept/api/', views.intercept_api, name='intercept_api'),
    path('intercept/save/', views.intercept_save, name='intercept_save'),
    path('intercept/<int:pk>/', views.intercept_detail, name='intercept_detail'),
    path('intercept/<int:pk>/execute/', views.intercept_execute, name='intercept_execute'),
    path('intercept/<int:pk>/export/', views.intercept_export, name='intercept_export'),
    path('intercept/<int:pk>/delete/', views.intercept_delete, name='intercept_delete'),
    path('intercept/extraction/<int:pk>/download/', views.intercept_download_extraction,
         name='intercept_download_extraction'),

    # 5. QoS / KPIs
    path('qos/', views.qos_view, name='qos_view'),
    path('qos/api/', views.qos_api, name='qos_api'),
    path('qos/refresh/', views.qos_refresh, name='qos_refresh'),

    # Regulatory Profile (admin)
    path('profile/save/', views.profile_save, name='profile_save'),
]
