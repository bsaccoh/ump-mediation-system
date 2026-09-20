"""URL patterns for the platform-wide job-tracking pages."""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('jobs/',                    views.job_list,   name='job_list'),
    path('jobs/api/',                views.job_api,    name='job_api'),
    path('jobs/<int:pk>/',           views.job_detail, name='job_detail'),
    path('jobs/<int:pk>/status/',    views.job_status, name='job_status'),
    path('users/',                   views.user_list,  name='user_list'),
    path('users/api/',               views.users_api,  name='users_api'),
]
