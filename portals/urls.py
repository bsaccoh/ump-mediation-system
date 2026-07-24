from django.urls import path

from . import views

app_name = 'portals'

urlpatterns = [
    # Input Portal (IDE)
    path('', views.input_portal_ide, name='input_portal_ide'),
    path('config/', views.input_portal_ide, name='input_portal_ide_config'),
    path('<int:pk>/', views.input_portal_ide, name='input_portal_ide_edit'),
    path('<int:pk>/delete/', views.input_portal_delete, name='input_portal_delete'),
    path('<int:pk>/toggle/', views.input_portal_toggle, name='input_portal_toggle'),
    # Legacy paths for compatibility
    path('input/', views.input_portal_ide, name='input_portal_list'),
    path('input-portal/', views.input_portal_ide, name='input_portal_ide_legacy'),
    # Output Portal (IDE)
    path('output/', views.output_portal_ide, name='output_portal_ide'),
    path('output/<int:pk>/', views.output_portal_ide, name='output_portal_ide_edit'),
    path('output/<int:pk>/toggle/', views.output_portal_toggle, name='output_portal_toggle'),
    path('output/<int:pk>/delete/', views.output_portal_delete, name='output_portal_delete'),
    path('api/stream-fields/', views.stream_fields_api, name='stream_fields_api'),
    path('output/schema/add/', views.output_schema_create, name='output_schema_create'),
    path('output/schema/<int:pk>/edit/', views.output_schema_edit, name='output_schema_edit'),
    path('output/schema/<int:pk>/delete/', views.output_schema_delete, name='output_schema_delete'),
    path('output/rule/add/', views.distribution_rule_create, name='distribution_rule_create'),
    path('output/rule/<int:pk>/edit/', views.distribution_rule_edit, name='distribution_rule_edit'),
    path('output/rule/<int:pk>/delete/', views.distribution_rule_delete, name='distribution_rule_delete'),
    # Legacy output paths
    path('output-portal/', views.output_portal_ide, name='output_portal_list'),
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
