from django.urls import path

from . import views

app_name = 'portals'

urlpatterns = [
    # Input Portal (IDE)
    path('', views.input_portal_ide, name='input_portal_ide'),
    path('config/', views.input_portal_ide, name='input_portal_ide_config'),
    path('<int:pk>/', views.input_portal_ide, name='input_portal_ide_edit'),
    path('<int:pk>/delete/', views.input_portal_delete, name='input_portal_delete'),
    # Legacy paths for compatibility
    path('input/', views.input_portal_ide, name='input_portal_list'),
    path('input-portal/', views.input_portal_ide, name='input_portal_ide_legacy'),
    # Output Portal
    path('output-portal/', views.output_portal_list, name='output_portal_list'),
    path('output-portal/add/', views.output_portal_create, name='output_portal_create'),
    path('output-portal/<int:pk>/edit/', views.output_portal_edit, name='output_portal_edit'),
    path('output-portal/<int:pk>/delete/', views.output_portal_delete, name='output_portal_delete'),
    # Plugins
    path('plugins/', views.plugin_list, name='plugin_list'),
    path('plugins/add/', views.plugin_create, name='plugin_create'),
    path('plugins/<int:pk>/edit/', views.plugin_edit, name='plugin_edit'),
    path('plugins/<int:pk>/delete/', views.plugin_delete, name='plugin_delete'),
    # Resources
    path('resources/', views.resource_list, name='resource_list'),
    path('resources/add/', views.resource_create, name='resource_create'),
    path('resources/<int:pk>/edit/', views.resource_edit, name='resource_edit'),
    path('resources/<int:pk>/delete/', views.resource_delete, name='resource_delete'),
]
