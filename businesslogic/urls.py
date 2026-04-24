from django.urls import path
from . import views

app_name = 'businesslogic'

urlpatterns = [
    path('',                views.rule_list,   name='rule_list'),
    path('add/',            views.rule_create, name='rule_create'),
    path('<int:pk>/',       views.rule_detail, name='rule_detail'),
    path('<int:pk>/edit/',  views.rule_edit,   name='rule_edit'),
    path('<int:pk>/delete/',views.rule_delete, name='rule_delete'),
    path('<int:pk>/toggle/',views.rule_toggle, name='rule_toggle'),
]
