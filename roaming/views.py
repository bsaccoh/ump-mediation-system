"""Roaming Views.

Pattern mirrors ``interconnect/views.py``: HTML list + JSON API + POST save
+ POST delete.  All engines lazy-imported so Day-1 still boots even if a
later engine module isn't built yet.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from interconnect.models import (
    InterconnectPartner, InterconnectRate, BillingCycle,
)

from .models import RoamingFile, RoamingDispute


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


def _resolve_partners_and_cycles(roaming_files):
    """Batch-load partner + billing_cycle for a list of RoamingFile rows.

    RoamingFile lives in the ``roaming`` DB; partner + billing_cycle live in
    ``interconnect``.  Cross-DB ``select_related`` doesn't work, so we
    materialise the FK IDs and fetch the targets in two extra queries.

    Returns ``(partners_by_id, cycles_by_id)`` — dicts keyed by PK.
    """
    partner_ids = {f.partner_id for f in roaming_files if f.partner_id}
    cycle_ids = {f.billing_cycle_id for f in roaming_files if f.billing_cycle_id}
    partners = {
        p.pk: p for p in InterconnectPartner.objects.filter(pk__in=partner_ids)
    } if partner_ids else {}
    cycles = {
        c.pk: c for c in BillingCycle.objects.filter(pk__in=cycle_ids)
    } if cycle_ids else {}
    return partners, cycles


# =============================================================================
# Index
# =============================================================================

@login_required
def index(request):
    return redirect('roaming:partner_list')


# =============================================================================
# 1. Roaming Partners — filtered view of InterconnectPartner
# =============================================================================

@login_required
def partner_list(request):
    return render(request, 'roaming/partners.html', {
        'title': 'Roaming Partners',
        'total': InterconnectPartner.objects.filter(is_roaming_partner=True).count(),
    })


@login_required
def partner_api(request):
    q = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)
    qs = InterconnectPartner.objects.filter(is_roaming_partner=True)
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(name__icontains=q) |
            Q(country__icontains=q) | Q(mcc__icontains=q) | Q(mnc__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'code': r.code, 'name': r.name,
        'country': r.country, 'country_code': r.country_code,
        'mcc': r.mcc, 'mnc': r.mnc,
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
        if pk:
            obj = InterconnectPartner.objects.get(pk=pk)
        else:
            obj = InterconnectPartner()
        obj.code = request.POST.get('code', '').strip().upper()
        obj.name = request.POST.get('name', '').strip()
        obj.country = request.POST.get('country', '').strip()
        obj.country_code = request.POST.get('country_code', '').strip()
        obj.mcc = request.POST.get('mcc', '').strip()
        obj.mnc = request.POST.get('mnc', '').strip()
        obj.default_currency = request.POST.get('default_currency', 'SLE').strip().upper()
        obj.billing_email = request.POST.get('billing_email', '').strip()
        obj.contact_name = request.POST.get('contact_name', '').strip()
        obj.phone = request.POST.get('phone', '').strip()
        obj.notes = request.POST.get('notes', '').strip()
        obj.is_active = _bool(request.POST.get('is_active'), True)
        # Roaming partners are by definition foreign and NOT home
        obj.is_local = False
        obj.is_home = False
        obj.is_roaming_partner = True
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def partner_delete(request, pk):
    get_object_or_404(InterconnectPartner, pk=pk, is_roaming_partner=True).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 2. Detection — scan CDRs for inbound roamers
# =============================================================================

@login_required
def detect_view(request):
    return render(request, 'roaming/detect.html', {
        'title': 'Roamer Detection',
    })


@login_required
def detect_api(request):
    """Return roamer detection summary for ``?start=&end=&group_by=mccmnc``."""
    start = _date(request.GET.get('start'))
    end = _date(request.GET.get('end'))
    if not start or not end:
        return JsonResponse({'success': False, 'error': 'start + end required'})
    try:
        from .engines.detect import detect_inbound_roamers
        rows = detect_inbound_roamers(start, end)
        return JsonResponse({'success': True, 'data': rows})
    except NotImplementedError as e:
        return JsonResponse({'success': False, 'error': str(e), 'data': []})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e), 'data': []})


# =============================================================================
# 3. Cycles — BillingCycle filtered to is_roaming=True
# =============================================================================

@login_required
def cycle_list(request):
    return render(request, 'roaming/cycles.html', {
        'title': 'Roaming Cycles',
        'total': BillingCycle.objects.filter(is_roaming=True).count(),
        'partners': InterconnectPartner.objects.filter(
            is_roaming_partner=True, is_active=True).order_by('name'),
        'status_choices': BillingCycle.Status.choices,
    })


@login_required
def cycle_api(request):
    page = request.GET.get('page', 1)
    qs = BillingCycle.objects.select_related('partner').filter(is_roaming=True)
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
        'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def cycle_save(request):
    pk = request.POST.get('id')
    try:
        if pk:
            obj = BillingCycle.objects.get(pk=pk)
        else:
            obj = BillingCycle()
        obj.partner_id = int(request.POST.get('partner_id'))
        obj.period_start = _date(request.POST.get('period_start')) or date.today()
        obj.period_end = _date(request.POST.get('period_end')) or date.today()
        obj.status = request.POST.get('status', BillingCycle.Status.OPEN)
        obj.notes = request.POST.get('notes', '').strip()
        obj.is_roaming = True
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def cycle_delete(request, pk):
    get_object_or_404(BillingCycle, pk=pk, is_roaming=True).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 4. Files
# =============================================================================

@login_required
def file_list(request):
    return render(request, 'roaming/files.html', {
        'title': 'Roaming Files',
        'total': RoamingFile.objects.count(),
        'status_choices': RoamingFile.Status.choices,
        'partners': InterconnectPartner.objects.filter(
            is_roaming_partner=True, is_active=True).order_by('name'),
        'cycles': BillingCycle.objects.filter(is_roaming=True)
                   .select_related('partner').order_by('-period_end')[:200],
    })


@login_required
def file_api(request):
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    # No select_related — partner + cycle live in a different DB.  Two
    # extra queries via _resolve_partners_and_cycles instead.
    qs = RoamingFile.objects.all()
    if status:
        qs = qs.filter(status=status)
    rows, total, page, pages = _paginate(qs, page)
    rows = list(rows)
    partners, cycles = _resolve_partners_and_cycles(rows)
    data = []
    for r in rows:
        p = partners.get(r.partner_id)
        c = cycles.get(r.billing_cycle_id)
        data.append({
            'id': r.pk,
            'file_number': r.file_number,
            'partner': p.code if p else '',
            'partner_name': p.name if p else '',
            'cycle': f'{c.period_start} – {c.period_end}' if c else '',
            'direction': r.direction,
            'record_count': r.record_count,
            'voice_minutes': str(r.voice_minutes),
            'sms_count': r.sms_count,
            'data_mb': str(r.data_mb),
            'total_amount': str(r.total_amount),
            'currency': r.currency,
            'status': r.status,
            'sha256': r.sha256[:24] + '…' if r.sha256 else '',
            'generated_at': r.generated_at.isoformat(),
            'csv_url': r.csv_file.url if r.csv_file else '',
        })
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def file_generate(request):
    cycle_id = request.POST.get('cycle_id')
    try:
        cycle = BillingCycle.objects.select_related('partner').get(
            pk=cycle_id, is_roaming=True)
        from core.tasks import enqueue_job
        from .tasks import task_generate_roaming_file
        job = enqueue_job(
            task=task_generate_roaming_file,
            job_type='roaming.generate_file',
            label=f'Roaming file: {cycle.partner.code} '
                   f'({cycle.period_start}..{cycle.period_end})',
            user=request.user,
            params={'cycle_id': cycle.pk, 'partner': cycle.partner.code},
            args=(cycle.pk, request.user.pk if request.user.is_authenticated else None),
        )
        return JsonResponse({'success': True, 'job_id': job.pk,
                              'job_url': f'/jobs/{job.pk}/',
                              'message': 'Roaming file generation queued.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def file_detail(request, pk):
    # partner + billing_cycle live in a different DB — drop select_related.
    # The FK descriptors lazy-load via the router (one extra query each),
    # which is fine for a single-record page.
    rfile = get_object_or_404(
        RoamingFile.objects.prefetch_related('disputes'),
        pk=pk,
    )
    return render(request, 'roaming/file_detail.html', {
        'title': f'Roaming File {rfile.file_number}',
        'rfile': rfile,
        'status_choices': RoamingFile.Status.choices,
    })


@login_required
def file_csv(request, pk):
    rfile = get_object_or_404(RoamingFile, pk=pk)
    if not rfile.csv_file:
        raise Http404('No CSV attached')
    try:
        data = rfile.csv_file.read()
        rfile.csv_file.close()
    except Exception:
        raise Http404('CSV unreadable')
    resp = HttpResponse(data, content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{rfile.file_number}.csv"'
    return resp


@login_required
@require_POST
def file_set_status(request, pk):
    rfile = get_object_or_404(RoamingFile, pk=pk)
    new_status = request.POST.get('status')
    if new_status not in dict(RoamingFile.Status.choices):
        return JsonResponse({'success': False, 'error': 'Invalid status'})
    rfile.status = new_status
    if new_status == RoamingFile.Status.SENT and not rfile.sent_at:
        rfile.sent_at = timezone.now()
    if new_status == RoamingFile.Status.SETTLED and not rfile.settled_at:
        rfile.settled_at = timezone.now()
    rfile.save()
    return JsonResponse({'success': True, 'status': rfile.status})


@login_required
@require_POST
def file_delete(request, pk):
    get_object_or_404(RoamingFile, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 5. Disputes
# =============================================================================

@login_required
def dispute_list(request):
    # Drop cross-DB select_related; the dropdown <option> labels resolve
    # partner lazily via the router for each of the ~200 files.
    return render(request, 'roaming/disputes.html', {
        'title': 'Roaming Disputes',
        'total': RoamingDispute.objects.count(),
        'files': RoamingFile.objects.order_by('-generated_at')[:200],
        'status_choices': RoamingDispute.Status.choices,
    })


@login_required
def dispute_api(request):
    status = request.GET.get('status', '').strip()
    page = request.GET.get('page', 1)
    # roaming_file is same-DB; partner is cross-DB.  Select only the
    # same-DB FK; batch-resolve partner via _resolve_partners_and_cycles.
    qs = RoamingDispute.objects.select_related('roaming_file')
    if status:
        qs = qs.filter(status=status)
    rows, total, page, pages = _paginate(qs, page)
    rows = list(rows)
    files = [r.roaming_file for r in rows if r.roaming_file_id]
    partners, _cycles = _resolve_partners_and_cycles(files)
    data = []
    for r in rows:
        p = partners.get(r.roaming_file.partner_id) if r.roaming_file_id else None
        data.append({
            'id': r.pk,
            'dispute_ref': r.dispute_ref,
            'file_number': r.roaming_file.file_number if r.roaming_file_id else '',
            'file_id': r.roaming_file_id,
            'partner': p.code if p else '',
            'raised_by': r.raised_by,
            'claimed_volume': str(r.claimed_volume),
            'claimed_amount': str(r.claimed_amount),
            'variance_amount': str(r.variance_amount),
            'description': r.description,
            'status': r.status,
            'opened_at': r.opened_at.isoformat(),
            'resolution_notes': r.resolution_notes,
        })
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def dispute_save(request):
    pk = request.POST.get('id')
    try:
        if pk:
            obj = RoamingDispute.objects.get(pk=pk)
        else:
            obj = RoamingDispute()
        obj.roaming_file_id = int(request.POST.get('roaming_file_id'))
        obj.dispute_ref = request.POST.get('dispute_ref', '').strip()
        obj.raised_by = request.POST.get('raised_by', '').strip()
        obj.claimed_volume = _decimal(request.POST.get('claimed_volume'))
        obj.claimed_amount = _decimal(request.POST.get('claimed_amount'))
        obj.description = request.POST.get('description', '').strip()
        obj.status = request.POST.get('status', RoamingDispute.Status.OPEN)
        obj.resolution_notes = request.POST.get('resolution_notes', '').strip()
        if obj.status == RoamingDispute.Status.RESOLVED and not obj.resolved_at:
            obj.resolved_at = timezone.now()
            if request.user.is_authenticated:
                obj.resolved_by = request.user
        # Auto-compute variance vs the file's total
        if obj.roaming_file_id:
            inv_total = RoamingFile.objects.get(pk=obj.roaming_file_id).total_amount
            obj.variance_amount = obj.claimed_amount - inv_total
        obj.save()
        # Promote the roaming file to DISPUTED if any dispute is open
        if obj.status in (RoamingDispute.Status.OPEN, RoamingDispute.Status.UNDER_REVIEW):
            f = RoamingFile.objects.get(pk=obj.roaming_file_id)
            if f.status not in (RoamingFile.Status.DISPUTED, RoamingFile.Status.SETTLED):
                f.status = RoamingFile.Status.DISPUTED
                f.save(update_fields=['status'])
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def dispute_delete(request, pk):
    get_object_or_404(RoamingDispute, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# 6. Reports
# =============================================================================

@login_required
def reports_view(request):
    return render(request, 'roaming/reports.html', {
        'title': 'Roaming Reports',
    })


@login_required
def reports_api(request):
    report = request.GET.get('report', 'top_partners')
    try:
        from .engines import reports as R
        if report == 'top_partners':
            data = R.top_partners(request.GET.get('start'), request.GET.get('end'))
        elif report == 'monthly_trend':
            data = R.monthly_trend(request.GET.get('start'), request.GET.get('end'))
        elif report == 'top_countries':
            data = R.top_countries(request.GET.get('start'), request.GET.get('end'))
        elif report == 'open_disputes':
            data = R.open_disputes()
        else:
            data = []
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e), 'data': []})
    return JsonResponse({'success': True, 'data': data})
