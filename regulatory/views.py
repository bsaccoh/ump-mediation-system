"""Regulatory Service — Views.

CRUD pattern mirrors ``reference/views.py``: HTML list + JSON API + POST
save + POST delete.  Engines live in ``regulatory/engines/``.  LEA views
are gated by :py:func:`regulatory.decorators.lawful_intercept_required`.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import lawful_intercept_required
from .models import (
    RegulatoryProfile, RetailRevenue, RegulatoryReport,
    LeviedPeriod, LEARequest, LEAExtractionLog, QoSMetric,
)


# =============================================================================
# Helpers
# =============================================================================

def _paginate(qs, page, per_page=25):
    total = qs.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    offset = (page - 1) * per_page
    return qs[offset:offset + per_page], total, page, pages


def _decimal(value, default='0'):
    if value in (None, ''):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _datetime(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return timezone.make_aware(datetime.strptime(value, fmt))
        except (ValueError, TypeError):
            continue
    return None


# =============================================================================
# Index
# =============================================================================

@login_required
def index(request):
    return redirect('regulatory:report_list')


# =============================================================================
# 1. NATCOM Reports
# =============================================================================

@login_required
def report_list(request):
    return render(request, 'regulatory/natcom_reports.html', {
        'title': 'NATCOM Reports',
        'total': RegulatoryReport.objects.count(),
        'report_types': RegulatoryReport.ReportType.choices,
        'status_choices': RegulatoryReport.Status.choices,
    })


@login_required
def report_api(request):
    rtype = request.GET.get('report_type', '').strip()
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    qs = RegulatoryReport.objects.all()
    if rtype:
        qs = qs.filter(report_type=rtype)
    if status:
        qs = qs.filter(status=status)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'report_type': r.report_type,
        'report_type_label': r.get_report_type_display(),
        'period_start': r.period_start.isoformat(),
        'period_end': r.period_end.isoformat(),
        'status': r.status,
        'generated_at': r.generated_at.isoformat() if r.generated_at else '',
        'pdf_url': r.pdf_file.url if r.pdf_file else '',
        'xlsx_url': r.xlsx_file.url if r.xlsx_file else '',
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def report_generate(request):
    rtype = request.POST.get('report_type', RegulatoryReport.ReportType.TRAFFIC)
    start = _date(request.POST.get('period_start'))
    end = _date(request.POST.get('period_end'))
    if not start or not end:
        return JsonResponse({'success': False, 'error': 'period_start + period_end required'})
    try:
        from core.tasks import enqueue_job
        from .tasks import task_generate_report
        job = enqueue_job(
            task=task_generate_report,
            job_type='regulatory.generate_report',
            label=f'NATCOM {rtype} report for {start}..{end}',
            user=request.user,
            params={'report_type': rtype, 'period_start': start.isoformat(),
                    'period_end': end.isoformat()},
            args=(rtype, start.isoformat(), end.isoformat(),
                   request.user.pk if request.user.is_authenticated else None),
        )
        return JsonResponse({
            'success': True, 'job_id': job.pk,
            'job_url': f'/jobs/{job.pk}/',
            'message': 'Report generation queued.',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def report_pdf(request, pk):
    r = get_object_or_404(RegulatoryReport, pk=pk)
    if r.pdf_file:
        try:
            data = r.pdf_file.read()
            r.pdf_file.close()
            resp = HttpResponse(data, content_type='application/pdf')
            resp['Content-Disposition'] = f'inline; filename="natcom_{r.report_type}_{r.period_start}_{r.period_end}.pdf"'
            return resp
        except Exception:
            pass
    raise Http404('PDF not available; regenerate the report.')


@login_required
def report_xlsx(request, pk):
    r = get_object_or_404(RegulatoryReport, pk=pk)
    if r.xlsx_file:
        try:
            data = r.xlsx_file.read()
            r.xlsx_file.close()
            resp = HttpResponse(
                data,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            resp['Content-Disposition'] = f'attachment; filename="natcom_{r.report_type}_{r.period_start}_{r.period_end}.xlsx"'
            return resp
        except Exception:
            pass
    raise Http404('Excel not available; regenerate the report.')


@login_required
@require_POST
def report_delete(request, pk):
    get_object_or_404(RegulatoryReport, pk=pk).delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def report_set_status(request, pk):
    r = get_object_or_404(RegulatoryReport, pk=pk)
    new_status = request.POST.get('status')
    if new_status not in dict(RegulatoryReport.Status.choices):
        return JsonResponse({'success': False, 'error': 'Invalid status'})
    r.status = new_status
    if new_status == RegulatoryReport.Status.SUBMITTED and not r.submitted_at:
        r.submitted_at = timezone.now()
    r.save()
    return JsonResponse({'success': True, 'status': r.status})


# =============================================================================
# 2. Levy & USF
# =============================================================================

@login_required
def levy_list(request):
    return render(request, 'regulatory/levy.html', {
        'title': 'Levy & USF',
        'total': LeviedPeriod.objects.count(),
        'profile': RegulatoryProfile.get_or_create_default(),
        'status_choices': LeviedPeriod.Status.choices,
    })


@login_required
def levy_api(request):
    page = request.GET.get('page', 1)
    qs = LeviedPeriod.objects.all()
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'period_year': r.period_year,
        'period_month': r.period_month,
        'interconnect_inbound': str(r.interconnect_inbound),
        'interconnect_outbound': str(r.interconnect_outbound),
        'retail_total': str(r.retail_total),
        'gross_revenue': str(r.gross_revenue),
        'levy_pct': str(r.levy_pct),
        'usf_pct': str(r.usf_pct),
        'levy_amount': str(r.levy_amount),
        'usf_amount': str(r.usf_amount),
        'total_payable': str(r.total_payable),
        'currency': r.currency,
        'status': r.status,
        'due_date': r.due_date.isoformat() if r.due_date else '',
        'paid_at': r.paid_at.isoformat() if r.paid_at else '',
        'payment_reference': r.payment_reference,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def levy_compute(request):
    year = _int(request.POST.get('period_year'))
    month = _int(request.POST.get('period_month'))
    if not (year and 1 <= month <= 12):
        return JsonResponse({'success': False, 'error': 'period_year + period_month required'})
    try:
        from .engines.levy import compute_levy
        levy = compute_levy(year, month, user=request.user if request.user.is_authenticated else None)
        return JsonResponse({
            'success': True, 'id': levy.pk,
            'gross_revenue': str(levy.gross_revenue),
            'levy_amount': str(levy.levy_amount),
            'usf_amount': str(levy.usf_amount),
            'total_payable': str(levy.total_payable),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def levy_mark_paid(request, pk):
    levy = get_object_or_404(LeviedPeriod, pk=pk)
    try:
        from .engines.levy import mark_levy_paid
        mark_levy_paid(
            levy,
            payment_date=_date(request.POST.get('payment_date')) or date.today(),
            reference=request.POST.get('payment_reference', '').strip(),
            user=request.user if request.user.is_authenticated else None,
        )
        return JsonResponse({'success': True, 'status': levy.status})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def levy_delete(request, pk):
    get_object_or_404(LeviedPeriod, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 3. Retail Revenue (manual entry)
# =============================================================================

@login_required
def retail_list(request):
    return render(request, 'regulatory/retail_revenue.html', {
        'title': 'Retail Revenue',
        'total': RetailRevenue.objects.count(),
    })


@login_required
def retail_api(request):
    q = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)
    qs = RetailRevenue.objects.all()
    if q:
        qs = qs.filter(notes__icontains=q)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'period_year': r.period_year,
        'period_month': r.period_month,
        'voice_revenue': str(r.voice_revenue),
        'sms_revenue': str(r.sms_revenue),
        'data_revenue': str(r.data_revenue),
        'other_revenue': str(r.other_revenue),
        'total': str(r.total),
        'currency': r.currency,
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def retail_save(request):
    pk = request.POST.get('id')
    try:
        if pk:
            obj = RetailRevenue.objects.get(pk=pk)
        else:
            obj = RetailRevenue()
            obj.created_by = request.user if request.user.is_authenticated else None
        obj.period_year = _int(request.POST.get('period_year'))
        obj.period_month = _int(request.POST.get('period_month'))
        obj.voice_revenue = _decimal(request.POST.get('voice_revenue'))
        obj.sms_revenue = _decimal(request.POST.get('sms_revenue'))
        obj.data_revenue = _decimal(request.POST.get('data_revenue'))
        obj.other_revenue = _decimal(request.POST.get('other_revenue'))
        obj.currency = request.POST.get('currency', 'SLE').strip().upper()
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk, 'total': str(obj.total)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def retail_delete(request, pk):
    get_object_or_404(RetailRevenue, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 4. Lawful Intercept (gated)
# =============================================================================

@lawful_intercept_required
def intercept_list(request):
    return render(request, 'regulatory/intercept.html', {
        'title': 'Lawful Intercept',
        'total': LEARequest.objects.count(),
        'status_choices': LEARequest.Status.choices,
    })


@lawful_intercept_required
def intercept_api(request):
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    qs = LEARequest.objects.all()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(case_number__icontains=q) | Q(requesting_agency__icontains=q) |
            Q(officer_name__icontains=q) | Q(filter_msisdn__icontains=q) |
            Q(filter_imsi__icontains=q) | Q(filter_imei__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'case_number': r.case_number,
        'requesting_agency': r.requesting_agency,
        'officer_name': r.officer_name,
        'officer_contact': r.officer_contact,
        'legal_basis': r.legal_basis,
        'filter_msisdn': r.filter_msisdn,
        'filter_imsi': r.filter_imsi,
        'filter_imei': r.filter_imei,
        'filter_cell_id': r.filter_cell_id,
        'filter_start': r.filter_start.isoformat() if r.filter_start else '',
        'filter_end': r.filter_end.isoformat() if r.filter_end else '',
        'status': r.status,
        'opened_at': r.opened_at.isoformat() if r.opened_at else '',
        'fulfilled_at': r.fulfilled_at.isoformat() if r.fulfilled_at else '',
        'extractions': r.extractions.count(),
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@lawful_intercept_required
@require_POST
def intercept_save(request):
    pk = request.POST.get('id')
    try:
        if pk:
            obj = LEARequest.objects.get(pk=pk)
        else:
            obj = LEARequest()
            obj.opened_by = request.user if request.user.is_authenticated else None
        obj.case_number = request.POST.get('case_number', '').strip()
        obj.requesting_agency = request.POST.get('requesting_agency', '').strip()
        obj.officer_name = request.POST.get('officer_name', '').strip()
        obj.officer_contact = request.POST.get('officer_contact', '').strip()
        obj.legal_basis = request.POST.get('legal_basis', '').strip()
        obj.filter_msisdn = request.POST.get('filter_msisdn', '').strip()
        obj.filter_imsi = request.POST.get('filter_imsi', '').strip()
        obj.filter_imei = request.POST.get('filter_imei', '').strip()
        obj.filter_cell_id = request.POST.get('filter_cell_id', '').strip()
        obj.filter_start = _datetime(request.POST.get('filter_start')) or obj.filter_start
        obj.filter_end = _datetime(request.POST.get('filter_end')) or obj.filter_end
        obj.status = request.POST.get('status', LEARequest.Status.OPEN)
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@lawful_intercept_required
def intercept_detail(request, pk):
    req = get_object_or_404(LEARequest.objects.prefetch_related('extractions'), pk=pk)
    return render(request, 'regulatory/intercept_detail.html', {
        'title': f'LEA Request {req.case_number}',
        'request_obj': req,
        'status_choices': LEARequest.Status.choices,
    })


@lawful_intercept_required
@require_POST
def intercept_execute(request, pk):
    req = get_object_or_404(LEARequest, pk=pk)
    try:
        from .engines.intercept import run_lea_query
        limit = _int(request.POST.get('limit'), 100)
        rows = run_lea_query(req, limit=limit)
        return JsonResponse({'success': True, 'rows': rows, 'count': len(rows)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@lawful_intercept_required
@require_POST
def intercept_export(request, pk):
    req = get_object_or_404(LEARequest, pk=pk)
    try:
        from core.tasks import enqueue_job
        from .tasks import task_intercept_export
        job = enqueue_job(
            task=task_intercept_export,
            job_type='regulatory.intercept_export',
            label=f'LEA evidentiary export: {req.case_number}',
            user=request.user,
            params={'case_number': req.case_number,
                    'filter_msisdn': req.filter_msisdn,
                    'filter_imsi': req.filter_imsi,
                    'filter_imei': req.filter_imei,
                    'filter_start': req.filter_start.isoformat() if req.filter_start else '',
                    'filter_end':   req.filter_end.isoformat()   if req.filter_end   else ''},
            args=(req.pk, request.user.pk if request.user.is_authenticated else None),
        )
        return JsonResponse({'success': True, 'job_id': job.pk,
                              'job_url': f'/jobs/{job.pk}/',
                              'message': 'Export queued.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@lawful_intercept_required
def intercept_download_extraction(request, pk):
    ext = get_object_or_404(LEAExtractionLog, pk=pk)
    if not ext.export_file:
        raise Http404('Export file missing')
    try:
        data = ext.export_file.read()
        ext.export_file.close()
    except Exception:
        raise Http404('Export file unreadable')
    resp = HttpResponse(data, content_type='text/csv')
    resp['Content-Disposition'] = (
        f'attachment; filename="LEA_{ext.request.case_number}_{ext.pk}.csv"'
    )
    return resp


@lawful_intercept_required
@require_POST
def intercept_delete(request, pk):
    get_object_or_404(LEARequest, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 5. QoS / KPIs
# =============================================================================

@login_required
def qos_view(request):
    return render(request, 'regulatory/qos.html', {
        'title': 'QoS / KPIs',
        'total': QoSMetric.objects.count(),
    })


@login_required
def qos_api(request):
    granularity = request.GET.get('granularity', 'DAILY')
    start = _date(request.GET.get('start'))
    end = _date(request.GET.get('end'))
    qs = QoSMetric.objects.filter(granularity=granularity).order_by('metric_date')
    if start:
        qs = qs.filter(metric_date__gte=start)
    if end:
        qs = qs.filter(metric_date__lte=end)
    data = [{
        'date': r.metric_date.isoformat(),
        'total_calls': r.total_calls,
        'successful_calls': r.successful_calls,
        'dropped_calls': r.dropped_calls,
        'failed_calls': r.failed_calls,
        'asr_pct': float(r.asr_pct),
        'acd_seconds': float(r.acd_seconds),
        'drop_rate_pct': float(r.drop_rate_pct),
        'availability_pct': float(r.availability_pct),
        'source': r.source,
    } for r in qs]
    return JsonResponse({'success': True, 'data': data})


@login_required
@require_POST
def qos_refresh(request):
    try:
        from .engines.qos import compute_daily_qos
        target_date = _date(request.POST.get('date')) or date.today()
        metric = compute_daily_qos(target_date)
        return JsonResponse({'success': True, 'date': metric.metric_date.isoformat(),
                              'asr_pct': float(metric.asr_pct),
                              'drop_rate_pct': float(metric.drop_rate_pct)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# =============================================================================
# Regulatory Profile (admin)
# =============================================================================

def _admin_required(u):
    return u.is_authenticated and u.is_superuser


@user_passes_test(_admin_required)
@require_POST
def profile_save(request):
    p = RegulatoryProfile.get_or_create_default()
    p.regulator_name = request.POST.get('regulator_name', p.regulator_name).strip() or 'NATCOM'
    p.contact_email = request.POST.get('contact_email', p.contact_email).strip()
    p.address = request.POST.get('address', p.address).strip()
    p.phone = request.POST.get('phone', p.phone).strip()
    p.levy_pct = _decimal(request.POST.get('levy_pct'), str(p.levy_pct))
    p.usf_pct = _decimal(request.POST.get('usf_pct'), str(p.usf_pct))
    p.home_currency = request.POST.get('home_currency', p.home_currency).strip() or 'SLE'
    p.updated_by = request.user
    p.save()
    return JsonResponse({'success': True})
