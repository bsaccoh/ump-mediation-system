"""UMP Mediation System - URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

admin.site.site_header = 'UMP Mediation System'
admin.site.site_title = 'UMP Mediation'
admin.site.index_title = 'Administration'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/v1/', include('api.urls')),
    path('collection/', include('collection.urls')),
    path('cdr/', include('streams.msc.urls')),
    path('pgw/', include('streams.pgw.urls')),
    path('sgsn/', include('streams.sgsn.urls')),
    path('sgw/', include('streams.sgw.urls')),
    path('reference/', include('reference.urls')),
    path('portals/', include('portals.urls', namespace='portals')),
    path('scripts/', include('scripts.urls', namespace='scripts')),
    path('business-logic/', include('businesslogic.urls', namespace='businesslogic')),
    path('', include('dashboard.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
