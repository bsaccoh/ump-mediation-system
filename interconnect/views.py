"""Interconnect Billing — Views.

CRUD for Partners / Rates / Exchange Rates / Billing Cycles (Day 2 polish),
plus action views for Invoice Generation, Invoicing, Reconciliation,
Settlement and Reports.

Pattern mirrors ``reference/views.py``: HTML list page + JSON paginated
API + POST save + POST delete.  Engines live in ``interconnect/engines/``.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    InterconnectPartner, InterconnectRate, ExchangeRate, BillingCycle,
    Invoice, InvoiceLine, ReconciliationRecord, Settlement,
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


def _date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _bool(value, default=False):
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ('true', '1', 'yes', 'on'):
        return True
    if s in ('false', '0', 'no', 'off', ''):
        return False
    return default


# =============================================================================
# Index
# =============================================================================

@login_required
def index(request):
    return redirect('interconnect:partner_list')


@login_required
def traffic_matrix(request):
    """Proxy to the dashboard Traffic Matrix view — shown under /interconnect/
    so finance/wholesale teams have it in their dropdown without leaving the
    Interconnect section."""
    from dashboard.views import traffic_matrix_view
    return traffic_matrix_view(request)


# =============================================================================
# 1. Partners
# =============================================================================

@login_required
def partner_list(request):
    return render(request, 'interconnect/partners.html', {
        'title': 'Interconnect Partners',
        'total': InterconnectPartner.objects.count(),
    })


@login_required
def partner_api(request):
    q = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)
    qs = InterconnectPartner.objects.all()
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(name__icontains=q) |
            Q(country__icontains=q) | Q(country_code__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'code': r.code, 'name': r.name,
        'country': r.country, 'country_code': r.country_code,
        'mcc': r.mcc, 'mnc': r.mnc,
        'is_local': r.is_local, 'is_home': r.is_home,
        'is_primary_for_country': r.is_primary_for_country,
        'default_currency': r.default_currency,
        'billing_email': r.billing_email,
        'contact_name': r.contact_name, 'phone': r.phone,
        'is_active': r.is_active, 'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def partner_save(request):
    pk = request.POST.get('id')
    try:
        obj = InterconnectPartner.objects.get(pk=pk) if pk else InterconnectPartner()
        obj.code = request.POST.get('code', '').strip().upper()
        obj.name = request.POST.get('name', '').strip()
        obj.country = request.POST.get('country', 'Sierra Leone').strip()
        obj.country_code = request.POST.get('country_code', '').strip()
        obj.mcc = request.POST.get('mcc', '').strip()
        obj.mnc = request.POST.get('mnc', '').strip()
        obj.is_local = _bool(request.POST.get('is_local'))
        obj.is_home = _bool(request.POST.get('is_home'))
        obj.is_primary_for_country = _bool(request.POST.get('is_primary_for_country'))
        obj.default_currency = request.POST.get('default_currency', 'SLE').strip().upper()
        obj.billing_email = request.POST.get('billing_email', '').strip()
        obj.billing_address = request.POST.get('billing_address', '').strip()
        obj.contact_name = request.POST.get('contact_name', '').strip()
        obj.phone = request.POST.get('phone', '').strip()
        obj.is_active = _bool(request.POST.get('is_active'), True)
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def partner_delete(request, pk):
    get_object_or_404(InterconnectPartner, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 2. Rates
# =============================================================================

@login_required
def rate_list(request):
    return render(request, 'interconnect/rates.html', {
        'title': 'Rates & Charging',
        'total': InterconnectRate.objects.count(),
        'partners': InterconnectPartner.objects.filter(is_active=True).order_by('name'),
        'direction_choices': InterconnectRate.Direction.choices,
        'service_choices': InterconnectRate.ServiceType.choices,
        'destination_choices': InterconnectRate.DestinationType.choices,
        'tod_choices': InterconnectRate.TimeOfDay.choices,
        'unit_choices': InterconnectRate.Unit.choices,
    })


@login_required
def rate_api(request):
    q = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)
    partner = request.GET.get('partner', '').strip()
    service = request.GET.get('service', '').strip()
    direction = request.GET.get('direction', '').strip()

    qs = InterconnectRate.objects.select_related('partner').all()
    if partner:
        qs = qs.filter(partner_id=partner)
    if service:
        qs = qs.filter(service_type=service)
    if direction:
        qs = qs.filter(direction=direction)
    if q:
        qs = qs.filter(
            Q(partner__code__icontains=q) | Q(partner__name__icontains=q) |
            Q(notes__icontains=q)
        )

    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'partner_id': r.partner_id,
        'partner': f'{r.partner.code} — {r.partner.name}',
        'direction': r.direction,
        'service_type': r.service_type,
        'destination_type': r.destination_type,
        'rat_filter': r.rat_filter,
        'time_of_day': r.time_of_day,
        'unit': r.unit,
        'rate': str(r.rate),
        'min_charge': str(r.min_charge),
        'setup_fee': str(r.setup_fee),
        'currency': r.currency,
        'effective_from': r.effective_from.isoformat() if r.effective_from else '',
        'effective_to': r.effective_to.isoformat() if r.effective_to else '',
        'is_active': r.is_active,
        'call_type': r.call_type or '',
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def rate_save(request):
    pk = request.POST.get('id')
    try:
        obj = InterconnectRate.objects.get(pk=pk) if pk else InterconnectRate()
        obj.partner_id = int(request.POST.get('partner_id'))
        obj.direction = request.POST.get('direction', InterconnectRate.Direction.INBOUND)
        obj.service_type = request.POST.get('service_type', InterconnectRate.ServiceType.VOICE)
        obj.destination_type = request.POST.get('destination_type',
                                                InterconnectRate.DestinationType.NATIONAL)
        obj.rat_filter = request.POST.get('rat_filter', '').strip()
        obj.time_of_day = request.POST.get('time_of_day', InterconnectRate.TimeOfDay.ANY)
        obj.unit = request.POST.get('unit', InterconnectRate.Unit.PER_MINUTE)
        obj.rate = _decimal(request.POST.get('rate'))
        obj.min_charge = _decimal(request.POST.get('min_charge'))
        obj.setup_fee = _decimal(request.POST.get('setup_fee'))
        obj.currency = request.POST.get('currency', 'SLE').strip().upper()
        obj.effective_from = _date(request.POST.get('effective_from')) or date.today()
        obj.effective_to = _date(request.POST.get('effective_to'))
        obj.is_active = _bool(request.POST.get('is_active'), True)
        obj.call_type = request.POST.get('call_type', '').strip() or None
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def rate_delete(request, pk):
    get_object_or_404(InterconnectRate, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 3. Exchange Rates
# =============================================================================

@login_required
def exchange_rate_list(request):
    return render(request, 'interconnect/exchange_rates.html', {
        'title': 'Exchange Rates',
        'total': ExchangeRate.objects.count(),
        'source_choices': ExchangeRate.Source.choices,
    })


@login_required
def exchange_rate_api(request):
    q = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)
    qs = ExchangeRate.objects.all()
    if q:
        qs = qs.filter(
            Q(from_currency__icontains=q) | Q(to_currency__icontains=q) |
            Q(notes__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'from_currency': r.from_currency,
        'to_currency': r.to_currency,
        'rate': str(r.rate),
        'effective_date': r.effective_date.isoformat(),
        'source': r.source,
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def exchange_rate_save(request):
    pk = request.POST.get('id')
    try:
        obj = ExchangeRate.objects.get(pk=pk) if pk else ExchangeRate()
        obj.from_currency = request.POST.get('from_currency', '').strip().upper()
        obj.to_currency = request.POST.get('to_currency', '').strip().upper()
        obj.rate = _decimal(request.POST.get('rate'))
        obj.effective_date = _date(request.POST.get('effective_date')) or date.today()
        obj.source = request.POST.get('source', ExchangeRate.Source.MANUAL)
        obj.notes = request.POST.get('notes', '').strip()
        if request.user.is_authenticated:
            obj.updated_by = request.user
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def exchange_rate_delete(request, pk):
    get_object_or_404(ExchangeRate, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 4. Billing Cycles
# =============================================================================

@login_required
def cycle_list(request):
    return render(request, 'interconnect/cycles.html', {
        'title': 'Billing Cycles',
        'total': BillingCycle.objects.count(),
        'partners': InterconnectPartner.objects.filter(is_active=True, is_home=False).order_by('name'),
        'status_choices': BillingCycle.Status.choices,
    })


@login_required
def cycle_api(request):
    page = request.GET.get('page', 1)
    qs = BillingCycle.objects.select_related('partner').all()
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'partner_id': r.partner_id,
        'partner': f'{r.partner.code} — {r.partner.name}',
        'period_start': r.period_start.isoformat(),
        'period_end': r.period_end.isoformat(),
        'status': r.status,
        'our_voice_minutes': str(r.our_voice_minutes),
        'our_voice_calls': r.our_voice_calls,
        'our_sms': r.our_sms,
        'our_data_mb': str(r.our_data_mb),
        'variance_pct': str(r.variance_pct),
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def cycle_save(request):
    pk = request.POST.get('id')
    try:
        obj = BillingCycle.objects.get(pk=pk) if pk else BillingCycle()
        obj.partner_id = int(request.POST.get('partner_id'))
        obj.period_start = _date(request.POST.get('period_start')) or date.today()
        obj.period_end = _date(request.POST.get('period_end')) or date.today()
        obj.status = request.POST.get('status', BillingCycle.Status.OPEN)
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def cycle_delete(request, pk):
    get_object_or_404(BillingCycle, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 5. Invoice Generation
# =============================================================================

@login_required
def invoice_generate(request):
    if request.method == 'POST':
        partner_id = request.POST.get('partner_id')
        cycle_id = request.POST.get('cycle_id')
        direction = request.POST.get('direction', Invoice.Direction.INBOUND)
        try:
            cycle = BillingCycle.objects.select_related('partner').get(pk=cycle_id)
            # Enqueue async; user gets redirected to a job-status page.
            from core.tasks import enqueue_job
            from .tasks import task_generate_invoice
            job = enqueue_job(
                task=task_generate_invoice,
                job_type='interconnect.generate_invoice',
                label=f'Generate {direction} invoice for {cycle.partner.code} '
                       f'({cycle.period_start}..{cycle.period_end})',
                user=request.user,
                params={'cycle_id': cycle.pk, 'direction': direction,
                        'partner': cycle.partner.code},
                args=(cycle.pk, direction,
                       request.user.pk if request.user.is_authenticated else None),
            )
            messages.info(request,
                f'Invoice generation queued (job #{job.pk}). '
                f'You will be redirected when ready.')
            return redirect('core:job_detail', pk=job.pk)
        except Exception as e:
            messages.error(request, f'Invoice generation failed: {e}')

    return render(request, 'interconnect/invoice_generate.html', {
        'title': 'Generate Invoice',
        'partners': InterconnectPartner.objects.filter(is_active=True, is_home=False).order_by('name'),
        'cycles': BillingCycle.objects.select_related('partner').order_by('-period_end')[:200],
        'direction_choices': Invoice.Direction.choices,
    })


# =============================================================================
# 6. Invoicing
# =============================================================================

@login_required
def invoice_list(request):
    return render(request, 'interconnect/invoices.html', {
        'title': 'Invoices',
        'total': Invoice.objects.count(),
        'status_choices': Invoice.Status.choices,
    })


@login_required
def invoice_api(request):
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    qs = Invoice.objects.select_related('partner', 'billing_cycle').all()
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q) | Q(partner__code__icontains=q) |
            Q(partner__name__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'invoice_number': r.invoice_number,
        'partner': r.partner.code,
        'partner_name': r.partner.name,
        'cycle': f'{r.billing_cycle.period_start} – {r.billing_cycle.period_end}',
        'direction': r.direction,
        'total': str(r.total),
        'currency': r.currency,
        'status': r.status,
        'issued_at': r.issued_at.isoformat() if r.issued_at else '',
        'due_date': r.due_date.isoformat() if r.due_date else '',
        'amount_paid': str(r.amount_paid),
        'amount_outstanding': str(r.amount_outstanding),
        'pdf_url': r.pdf_file.url if r.pdf_file else '',
        'csv_url': r.csv_file.url if r.csv_file else '',
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
def invoice_detail(request, pk):
    inv = get_object_or_404(
        Invoice.objects.select_related('partner', 'billing_cycle')
        .prefetch_related('lines', 'settlements'),
        pk=pk,
    )
    return render(request, 'interconnect/invoice_detail.html', {
        'title': f'Invoice {inv.invoice_number}',
        'invoice': inv,
        'status_choices': Invoice.Status.choices,
        'payment_method_choices': Settlement.PaymentMethod.choices,
    })


@login_required
def invoice_pdf(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    if inv.pdf_file:
        try:
            data = inv.pdf_file.read()
            inv.pdf_file.close()
        except Exception:
            data = None
        if data:
            resp = HttpResponse(data, content_type='application/pdf')
            resp['Content-Disposition'] = f'inline; filename="{inv.invoice_number}.pdf"'
            return resp
    try:
        from .engines.invoicing import render_invoice_pdf
        data = render_invoice_pdf(inv)
    except Exception as e:
        raise Http404(f'PDF not available: {e}')
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{inv.invoice_number}.pdf"'
    return resp


@login_required
def invoice_csv(request, pk):
    inv = get_object_or_404(Invoice.objects.prefetch_related('lines'), pk=pk)
    if inv.csv_file:
        try:
            data = inv.csv_file.read()
            inv.csv_file.close()
        except Exception:
            data = None
        if data:
            resp = HttpResponse(data, content_type='text/csv')
            resp['Content-Disposition'] = f'attachment; filename="{inv.invoice_number}.csv"'
            return resp
    # Fallback: render on the fly
    from .engines.invoicing import render_invoice_csv
    resp = HttpResponse(render_invoice_csv(inv), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{inv.invoice_number}.csv"'
    return resp


@login_required
@require_POST
def invoice_set_status(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    new_status = request.POST.get('status')
    if new_status not in dict(Invoice.Status.choices):
        return JsonResponse({'success': False, 'error': 'Invalid status'})
    inv.status = new_status
    if new_status == Invoice.Status.ISSUED and not inv.issued_at:
        inv.issued_at = timezone.now()
    inv.save()
    return JsonResponse({'success': True, 'status': inv.status})


# =============================================================================
# 7. Reconciliation
# =============================================================================

@login_required
def reconciliation_list(request):
    return render(request, 'interconnect/reconciliation.html', {
        'title': 'Reconciliation',
        'total': ReconciliationRecord.objects.count(),
        'cycles': BillingCycle.objects.select_related('partner').order_by('-period_end')[:200],
        'status_choices': ReconciliationRecord.Status.choices,
    })


@login_required
def reconciliation_api(request):
    cycle_id = request.GET.get('cycle', '').strip()
    page = request.GET.get('page', 1)
    qs = ReconciliationRecord.objects.select_related('partner', 'billing_cycle').all()
    if cycle_id:
        qs = qs.filter(billing_cycle_id=cycle_id)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'partner': r.partner.code,
        'cycle': f'{r.billing_cycle.period_start} – {r.billing_cycle.period_end}',
        'service_type': r.service_type,
        'destination_type': r.destination_type,
        'our_volume': str(r.our_volume),
        'our_amount': str(r.our_amount),
        'partner_volume': str(r.partner_volume),
        'partner_amount': str(r.partner_amount),
        'variance_volume': str(r.variance_volume),
        'variance_amount': str(r.variance_amount),
        'variance_pct': str(r.variance_pct),
        'status': r.status,
        'resolution_notes': r.resolution_notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def reconciliation_upload(request, cycle_pk):
    cycle = get_object_or_404(BillingCycle, pk=cycle_pk)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'success': False, 'error': 'No file uploaded'})
    try:
        from .engines.reconciliation import import_partner_cdr
        summary = import_partner_cdr(cycle, f)
        return JsonResponse({'success': True, **summary})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def reconciliation_save(request, pk):
    obj = get_object_or_404(ReconciliationRecord, pk=pk)
    obj.status = request.POST.get('status', obj.status)
    obj.resolution_notes = request.POST.get('resolution_notes', obj.resolution_notes)
    if obj.status == ReconciliationRecord.Status.RESOLVED and not obj.resolved_at:
        obj.resolved_at = timezone.now()
        if request.user.is_authenticated:
            obj.resolved_by = request.user
    obj.save()
    return JsonResponse({'success': True})


# =============================================================================
# 8. Settlement
# =============================================================================

@login_required
def settlement_list(request):
    return render(request, 'interconnect/settlement.html', {
        'title': 'Settlement',
        'total': Settlement.objects.count(),
        'invoices': Invoice.objects.select_related('partner').exclude(
            status=Invoice.Status.VOID).order_by('-created_at')[:300],
        'payment_method_choices': Settlement.PaymentMethod.choices,
    })


@login_required
def settlement_api(request):
    invoice_id = request.GET.get('invoice', '').strip()
    page = request.GET.get('page', 1)
    qs = Settlement.objects.select_related('invoice', 'invoice__partner').all()
    if invoice_id:
        qs = qs.filter(invoice_id=invoice_id)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk,
        'invoice_id': r.invoice_id,
        'invoice_number': r.invoice.invoice_number,
        'partner': r.invoice.partner.code,
        'amount': str(r.amount),
        'currency': r.currency,
        'amount_local': str(r.amount_local),
        'fx_rate_to_local': str(r.fx_rate_to_local),
        'payment_date': r.payment_date.isoformat(),
        'payment_method': r.payment_method,
        'payment_reference': r.payment_reference,
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def settlement_save(request):
    pk = request.POST.get('id')
    try:
        obj = Settlement.objects.get(pk=pk) if pk else Settlement()
        obj.invoice_id = int(request.POST.get('invoice_id'))
        obj.amount = _decimal(request.POST.get('amount'))
        obj.currency = request.POST.get('currency', 'SLE').strip().upper()
        obj.fx_rate_to_local = _decimal(request.POST.get('fx_rate_to_local'), '1')
        obj.amount_local = _decimal(request.POST.get('amount_local')) or (
            obj.amount * obj.fx_rate_to_local
        )
        obj.payment_date = _date(request.POST.get('payment_date')) or date.today()
        obj.payment_method = request.POST.get('payment_method', Settlement.PaymentMethod.WIRE)
        obj.payment_reference = request.POST.get('payment_reference', '').strip()
        obj.notes = request.POST.get('notes', '').strip()
        if request.user.is_authenticated and not obj.pk:
            obj.recorded_by = request.user
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def settlement_delete(request, pk):
    get_object_or_404(Settlement, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 9. Reports
# =============================================================================

@login_required
def reports_view(request):
    return render(request, 'interconnect/reports.html', {
        'title': 'Interconnect Reports',
    })


@login_required
def reports_api(request):
    report = request.GET.get('report', 'traffic_by_partner')
    try:
        from .engines import reports as R
        if report == 'traffic_by_partner':
            data = R.traffic_by_partner(request.GET.get('start'), request.GET.get('end'))
        elif report == 'revenue_trend':
            data = R.revenue_trend(request.GET.get('start'), request.GET.get('end'),
                                    request.GET.get('granularity', 'month'))
        elif report == 'top_destinations':
            data = R.top_destinations(request.GET.get('partner'),
                                       request.GET.get('start'),
                                       request.GET.get('end'),
                                       int(request.GET.get('limit', 20)))
        elif report == 'ageing':
            data = R.ageing(request.GET.get('as_of'))
        else:
            data = []
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e), 'data': []})
    return JsonResponse({'success': True, 'data': data})


@login_required
def reports_export(request):
    """CSV export of any report."""
    report = request.GET.get('report', 'traffic_by_partner')
    try:
        from .engines import reports as R
        if report == 'traffic_by_partner':
            rows = R.traffic_by_partner(request.GET.get('start'), request.GET.get('end'))
        elif report == 'revenue_trend':
            rows = R.revenue_trend(request.GET.get('start'), request.GET.get('end'),
                                    request.GET.get('granularity', 'month'))
        elif report == 'top_destinations':
            rows = R.top_destinations(request.GET.get('partner'),
                                       request.GET.get('start'),
                                       request.GET.get('end'),
                                       int(request.GET.get('limit', 20)))
        elif report == 'ageing':
            rows = R.ageing(request.GET.get('as_of'))
        else:
            rows = []
    except Exception:
        rows = []

    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{report}.csv"'
    return resp
