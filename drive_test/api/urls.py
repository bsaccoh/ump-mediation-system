from django.urls import path

from . import views

urlpatterns = [
    path('stats/', views.stats, name='drive-test-stats'),
    path('regions/', views.RegionListView.as_view(), name='drive-test-regions'),
    path('districts/', views.DistrictListView.as_view(), name='drive-test-districts'),
    path('chiefdoms/', views.ChiefdomListView.as_view(), name='drive-test-chiefdoms'),
    path('sites/', views.SiteListView.as_view(), name='drive-test-sites'),
    path('cells/', views.CellListView.as_view(), name='drive-test-cells'),
    path('sessions/', views.SessionListView.as_view(), name='drive-test-sessions'),
    path('sessions/<str:session_ref>/', views.SessionDetailView.as_view(),
         name='drive-test-session-detail'),
    path('files/', views.FileListView.as_view(), name='drive-test-files'),
    path('findings/', views.FindingListView.as_view(), name='drive-test-findings'),
]
