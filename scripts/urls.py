from django.urls import path
from . import views

app_name = 'scripts'

urlpatterns = [
    path('',                  views.script_list,   name='script_list'),
    path('add/',              views.script_create, name='script_create'),
    path('<int:pk>/',         views.script_detail, name='script_detail'),
    path('<int:pk>/edit/',    views.script_edit,   name='script_edit'),
    path('<int:pk>/delete/',  views.script_delete, name='script_delete'),
    path('<int:pk>/run/',     views.script_run,    name='script_run'),
    path('ide/',              views.script_ide,    name='script_ide'),
    path('ide/<int:pk>/',     views.script_ide,    name='script_ide_edit'),
]
