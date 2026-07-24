"""Interconnect Billing URL patterns."""
from django.urls import path
from . import views

app_name = 'interconnect'

urlpatterns = [
    # Index → redirect to Partners
    path('', views.index, name='index'),

    # Traffic Matrix (proxy to dashboard view — same data, scoped under /interconnect/)
    path('traffic-matrix/', views.traffic_matrix, name='traffic_matrix'),

    # 1. Partners
    path('partners/', views.partner_list, name='partner_list'),
    path('partners/api/', views.partner_api, name='partner_api'),
    path('partners/save/', views.partner_save, name='partner_save'),
    path('partners/<int:pk>/delete/', views.partner_delete, name='partner_delete'),

    # 2. Rates
    path('rates/', views.rate_list, name='rate_list'),
    path('rates/api/', views.rate_api, name='rate_api'),
    path('rates/save/', views.rate_save, name='rate_save'),
    path('rates/<int:pk>/delete/', views.rate_delete, name='rate_delete'),

    # 3. Exchange Rates
    path('exchange-rates/', views.exchange_rate_list, name='exchange_rate_list'),
    path('exchange-rates/api/', views.exchange_rate_api, name='exchange_rate_api'),
    path('exchange-rates/save/', views.exchange_rate_save, name='exchange_rate_save'),
    path('exchange-rates/<int:pk>/delete/', views.exchange_rate_delete, name='exchange_rate_delete'),

    # 4. Billing Cycles
    path('cycles/', views.cycle_list, name='cycle_list'),
    path('cycles/api/', views.cycle_api, name='cycle_api'),
    path('cycles/save/', views.cycle_save, name='cycle_save'),
    path('cycles/<int:pk>/delete/', views.cycle_delete, name='cycle_delete'),

    # 5. Invoice Generation
    path('invoice-generate/', views.invoice_generate, name='invoice_generate'),

    # 6. Invoicing
    path('invoices/', views.invoice_list, name='invoice_list'),
    path('invoices/api/', views.invoice_api, name='invoice_api'),
    path('invoices/<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('invoices/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('invoices/<int:pk>/csv/', views.invoice_csv, name='invoice_csv'),
    path('invoices/<int:pk>/status/', views.invoice_set_status, name='invoice_set_status'),

    # 7. Reconciliation
    path('reconciliation/', views.reconciliation_list, name='reconciliation_list'),
    path('reconciliation/api/', views.reconciliation_api, name='reconciliation_api'),
    path('reconciliation/<int:cycle_pk>/upload/', views.reconciliation_upload, name='reconciliation_upload'),
    path('reconciliation/<int:pk>/save/', views.reconciliation_save, name='reconciliation_save'),

    # 8. Settlement
    path('settlement/', views.settlement_list, name='settlement_list'),
    path('settlement/api/', views.settlement_api, name='settlement_api'),
    path('settlement/save/', views.settlement_save, name='settlement_save'),
    path('settlement/<int:pk>/delete/', views.settlement_delete, name='settlement_delete'),

    # 9. Reports
    path('reports/', views.reports_view, name='reports_view'),
    path('reports/api/', views.reports_api, name='reports_api'),
    path('reports/export/', views.reports_export, name='reports_export'),
]
