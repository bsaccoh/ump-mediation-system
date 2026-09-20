"""UMP Mediation System - URL Configuration"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

admin.site.site_header = 'UMP Mediation System'
admin.site.site_title = 'UMP Mediation'
admin.site.index_title = 'Administration'

def health(request):
    from django.db import connection
    connection.ensure_connection()
    return JsonResponse({'status': 'ok'})

urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('core.urls', namespace='core')),
    path('api/v1/', include('api.urls')),
    path('collection/', include('collection.urls')),
    path('cdr/', include('streams.msc.urls')),
    path('ims/', include('streams.ims.urls')),
    path('pgw/', include('streams.pgw.urls')),
    path('sgsn/', include('streams.sgsn.urls')),
    path('sgw/', include('streams.sgw.urls')),
    path('reference/', include('reference.urls')),
    path('portals/', include('portals.urls', namespace='portals')),
    path('scripts/', include('scripts.urls', namespace='scripts')),
    path('business-logic/', include('businesslogic.urls', namespace='businesslogic')),
    path('regulatory/', include('regulatory.urls', namespace='regulatory')),
    path('', include('dashboard.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
