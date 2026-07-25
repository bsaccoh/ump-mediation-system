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

    # 6. Network Performance Monitoring (PM KPIs)
    path('network-performance/', views.network_performance_view, name='network_performance_view'),
    path('network-performance/api/', views.network_performance_api, name='network_performance_api'),
    path('network-performance/comparison/', views.network_performance_comparison_api, name='network_performance_comparison_api'),
    path('network-performance/import/', views.network_performance_import, name='network_performance_import'),
    path('network-performance/api/push/', views.network_performance_api_push, name='network_performance_api_push'),
    path('network-performance/save/', views.network_performance_save, name='network_performance_save'),
    path('network-performance/<int:pk>/delete/', views.network_performance_delete, name='network_performance_delete'),

    # 7. Drive Test Management
    path('drive-test/', views.drive_test_list, name='drive_test_list'),
    path('drive-test/api/', views.drive_test_api, name='drive_test_api'),
    path('drive-test/upload/', views.drive_test_upload, name='drive_test_upload'),
    path('drive-test/<int:pk>/', views.drive_test_detail, name='drive_test_detail'),
    path('drive-test/<int:pk>/samples/', views.drive_test_samples_api, name='drive_test_samples_api'),
    path('drive-test/<int:pk>/analyse/', views.drive_test_analyse, name='drive_test_analyse'),
    path('drive-test/<int:pk>/delete/', views.drive_test_delete, name='drive_test_delete'),

    # Regulatory Profile (admin)
    path('profile/save/', views.profile_save, name='profile_save'),
]
