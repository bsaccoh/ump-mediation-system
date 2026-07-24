from django.urls import path
from . import views

app_name = 'ims'

urlpatterns = [
    path('search/',         views.cdr_search,     name='cdr_search'),
    path('api/search/',     views.cdr_search_api, name='cdr_search_api'),
    path('<int:pk>/',       views.cdr_detail,     name='cdr_detail'),
]
