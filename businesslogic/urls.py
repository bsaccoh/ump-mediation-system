from django.urls import path
from . import views

app_name = 'businesslogic'

urlpatterns = [
    path('',                views.rule_list,   name='rule_list'),
    path('business/',       views.business_rule_list,   name='business_rules'),
    path('correlation/',    views.correlation_rule_list, name='correlation_rules'),
    path('detection/',      views.detection_rule_list,   name='detection_rules'),
    path('error-handling/', views.error_handling_list,   name='error_handling'),
    path('add/',            views.rule_create, name='rule_create'),
    path('<int:pk>/',       views.rule_detail, name='rule_detail'),
    path('<int:pk>/edit/',  views.rule_edit,   name='rule_edit'),
    path('<int:pk>/delete/',views.rule_delete, name='rule_delete'),
    path('<int:pk>/toggle/',views.rule_toggle, name='rule_toggle'),
]
