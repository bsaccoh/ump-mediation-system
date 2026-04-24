"""Celery configuration for UMP Mediation System."""
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('ump_mediation')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
