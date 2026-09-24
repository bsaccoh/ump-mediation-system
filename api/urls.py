from django.urls import include, path

app_name = 'api'

urlpatterns = [
    path('drive-test/', include('drive_test.api.urls')),
]
