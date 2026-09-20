"""Shared imports and constants for dashboard views."""
import csv
import io
import logging
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from core.decorators import staff_required, operator_required, analyst_required
from django.contrib import messages
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse, FileResponse, Http404
from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_POST
from django.db.models import Count, Sum, Avg, Q, F, Min, Max
from django.utils import timezone

from collection.models import CDRFile, DataSource, DistributionPortal, DistributionLog
from core.activity import log_activity
from core.models import Alert
from dashboard.services.system_health import get_system_health
from streams.msc.models import MSCRecord
from streams.ims.models import IMSRecord
from streams.pgw.models import PGWRecord
from streams.sgsn.models import SGSNRecord
from streams.sgw.models import SGWRecord

logger = logging.getLogger('dashboard.views')

RAT_TYPE_NAMES = {
    '1': 'UTRAN', '2': 'GERAN', '3': 'WLAN', '4': 'GAN',
    '5': 'HSPA_Evolution', '6': 'EUTRAN', '7': 'Virtual',
    '8': 'EUTRAN_NB_IoT', '9': 'LTE_M', '10': 'NR',
}
