"""Reference Data Views — CRUD for all lookup tables."""
import json
import csv
import io
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST, require_http_methods
from django.db.models import Q

from .models import MccMnc, ImsiPrefix, NumberingPlan, TrunkGroup


# =============================================================================
# Helper
# =============================================================================

def _paginate(qs, page, per_page=25):
    total = qs.count()
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    offset = (page - 1) * per_page
    return qs[offset:offset + per_page], total, page, pages


# =============================================================================
# MCC / MNC
# =============================================================================

@login_required
def mcc_mnc_list(request):
    return render(request, 'reference/mcc_mnc.html', {
        'title': 'MCC / MNC',
        'total': MccMnc.objects.count(),
    })


@login_required
def mcc_mnc_api(request):
    q = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    qs = MccMnc.objects.all()
    if q:
        qs = qs.filter(
            Q(mcc__icontains=q) | Q(mnc__icontains=q) |
            Q(country__icontains=q) | Q(operator__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'mcc': r.mcc, 'mnc': r.mnc,
        'country': r.country, 'iso': r.iso, 'dial_code': r.dial_code,
        'operator': r.operator, 'is_home': r.is_home,
        'enabled': r.enabled, 'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def mcc_mnc_save(request):
    pk = request.POST.get('id')
    try:
        obj = MccMnc.objects.get(pk=pk) if pk else MccMnc()
        obj.mcc = request.POST.get('mcc', '').strip()
        obj.mnc = request.POST.get('mnc', '').strip()
        obj.country = request.POST.get('country', '').strip()
        obj.iso = request.POST.get('iso', '').strip().upper()
        obj.dial_code = request.POST.get('dial_code', '').strip()
        obj.operator = request.POST.get('operator', '').strip()
        obj.is_home = request.POST.get('is_home') == 'true'
        obj.enabled = request.POST.get('enabled', 'true') != 'false'
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def mcc_mnc_delete(request, pk):
    get_object_or_404(MccMnc, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# IMSI Prefix
# =============================================================================

@login_required
def imsi_prefix_list(request):
    return render(request, 'reference/imsi_prefix.html', {
        'title': 'IMSI Prefixes',
        'total': ImsiPrefix.objects.count(),
    })


@login_required
def imsi_prefix_api(request):
    q = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))
    qs = ImsiPrefix.objects.all()
    if q:
        qs = qs.filter(
            Q(prefix__icontains=q) | Q(mcc__icontains=q) |
            Q(mnc__icontains=q) | Q(operator__icontains=q) | Q(country__icontains=q)
        )
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'prefix': r.prefix, 'mcc': r.mcc, 'mnc': r.mnc,
        'operator': r.operator, 'country': r.country,
        'is_home': r.is_home, 'enabled': r.enabled, 'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def imsi_prefix_save(request):
    pk = request.POST.get('id')
    try:
        obj = ImsiPrefix.objects.get(pk=pk) if pk else ImsiPrefix()
        obj.prefix = request.POST.get('prefix', '').strip()
        obj.mcc = request.POST.get('mcc', '').strip()
        obj.mnc = request.POST.get('mnc', '').strip()
        obj.operator = request.POST.get('operator', '').strip()
        obj.country = request.POST.get('country', '').strip()
        obj.is_home = request.POST.get('is_home') == 'true'
        obj.enabled = request.POST.get('enabled', 'true') != 'false'
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def imsi_prefix_delete(request, pk):
    get_object_or_404(ImsiPrefix, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# Numbering Plan
# =============================================================================

@login_required
def numbering_plan_list(request):
    return render(request, 'reference/numbering_plan.html', {
        'title': 'Numbering Plan',
        'total': NumberingPlan.objects.count(),
        'number_types': NumberingPlan.NUMBER_TYPE_CHOICES,
    })


@login_required
def numbering_plan_api(request):
    q = request.GET.get('q', '').strip()
    number_type = request.GET.get('number_type', '').strip()
    page = int(request.GET.get('page', 1))
    qs = NumberingPlan.objects.all()
    if q:
        qs = qs.filter(
            Q(prefix__icontains=q) | Q(operator__icontains=q) |
            Q(country__icontains=q) | Q(country_code__icontains=q)
        )
    if number_type:
        qs = qs.filter(number_type=number_type)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'prefix': r.prefix, 'country': r.country,
        'country_code': r.country_code, 'operator': r.operator,
        'number_type': r.number_type, 'min_length': r.min_length,
        'max_length': r.max_length, 'enabled': r.enabled, 'notes': r.notes,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def numbering_plan_save(request):
    pk = request.POST.get('id')
    try:
        obj = NumberingPlan.objects.get(pk=pk) if pk else NumberingPlan()
        obj.prefix = request.POST.get('prefix', '').strip()
        obj.country = request.POST.get('country', 'Sierra Leone').strip()
        obj.country_code = request.POST.get('country_code', '').strip()
        obj.operator = request.POST.get('operator', '').strip()
        obj.number_type = request.POST.get('number_type', 'MOBILE').strip()
        obj.min_length = int(request.POST.get('min_length', 8))
        obj.max_length = int(request.POST.get('max_length', 12))
        obj.enabled = request.POST.get('enabled', 'true') != 'false'
        obj.notes = request.POST.get('notes', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def numbering_plan_delete(request, pk):
    get_object_or_404(NumberingPlan, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# Trunk Groups
# =============================================================================

@login_required
def trunk_list(request):
    from django.db.models import Count
    switch_counts = (
        TrunkGroup.objects.values('switch_id')
        .annotate(n=Count('id'))
        .order_by('switch_id')
    )
    return render(request, 'reference/trunks.html', {
        'title': 'Trunk Groups',
        'total': TrunkGroup.objects.count(),
        'trunk_types': TrunkGroup.TRUNK_TYPE_CHOICES,
        'directions': TrunkGroup.DIRECTION_CHOICES,
        'switch_ids': [r['switch_id'] for r in switch_counts],
        'switch_counts': {r['switch_id']: r['n'] for r in switch_counts},
    })


@login_required
def trunk_api(request):
    q = request.GET.get('q', '').strip()
    direction = request.GET.get('direction', '').strip()
    trunk_type = request.GET.get('trunk_type', '').strip()
    switch_id = request.GET.get('switch_id', '').strip()
    page = int(request.GET.get('page', 1))
    qs = TrunkGroup.objects.all()
    if q:
        qs = qs.filter(
            Q(trunk_id__icontains=q) | Q(name__icontains=q) |
            Q(partner__icontains=q) | Q(switch_id__icontains=q)
        )
    if direction:
        qs = qs.filter(direction=direction)
    if trunk_type:
        qs = qs.filter(trunk_type=trunk_type)
    if switch_id:
        qs = qs.filter(switch_id=switch_id)
    rows, total, page, pages = _paginate(qs, page)
    data = [{
        'id': r.pk, 'switch_id': r.switch_id,
        'trunk_id': r.trunk_id, 'name': r.name,
        'trunk_type': r.trunk_type, 'direction': r.direction,
        'partner': r.partner, 'country': r.country,
        'prefix': r.prefix, 'enabled': r.enabled, 'description': r.description,
    } for r in rows]
    return JsonResponse({'records': data, 'total': total, 'page': page, 'pages': pages})


@login_required
@require_POST
def trunk_save(request):
    pk = request.POST.get('id')
    try:
        obj = TrunkGroup.objects.get(pk=pk) if pk else TrunkGroup()
        obj.switch_id = request.POST.get('switch_id', '').strip().upper()
        obj.trunk_id = request.POST.get('trunk_id', '').strip().upper()
        obj.name = request.POST.get('name', '').strip() or obj.trunk_id
        obj.trunk_type = request.POST.get('trunk_type', 'INTERCONNECT').strip()
        obj.direction = request.POST.get('direction', 'BOTH').strip()
        obj.partner = request.POST.get('partner', '').strip()
        obj.country = request.POST.get('country', '').strip()
        obj.prefix = request.POST.get('prefix', '').strip()
        obj.enabled = request.POST.get('enabled', 'true') != 'false'
        obj.description = request.POST.get('description', '').strip()
        obj.save()
        return JsonResponse({'success': True, 'id': obj.pk})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def trunk_delete(request, pk):
    get_object_or_404(TrunkGroup, pk=pk).delete()
    return JsonResponse({'success': True})


# =============================================================================
# CSV Import (shared)
# =============================================================================

@login_required
@require_POST
def csv_import(request, table):
    """Generic CSV import for any reference table."""
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'success': False, 'error': 'No file uploaded'})

    IMPORTERS = {
        'mcc_mnc': _import_mcc_mnc,
        'imsi_prefix': _import_imsi_prefix,
        'numbering_plan': _import_numbering_plan,
        'trunks': _import_trunks,
    }
    importer = IMPORTERS.get(table)
    if not importer:
        return JsonResponse({'success': False, 'error': f'Unknown table: {table}'})

    try:
        text = f.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(text))
        created, updated, errors = importer(reader)
        return JsonResponse({
            'success': True,
            'message': f'Imported: {created} created, {updated} updated, {errors} errors'
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


def _import_mcc_mnc(reader):
    created = updated = errors = 0
    for row in reader:
        try:
            obj, is_new = MccMnc.objects.update_or_create(
                mcc=row.get('mcc', '').strip(),
                mnc=row.get('mnc', '').strip(),
                defaults={
                    'country': row.get('country', '').strip(),
                    'operator': row.get('operator', '').strip(),
                    'is_home': row.get('is_home', '').lower() in ('1', 'true', 'yes'),
                    'notes': row.get('notes', '').strip(),
                }
            )
            if is_new:
                created += 1
            else:
                updated += 1
        except Exception:
            errors += 1
    return created, updated, errors


def _import_imsi_prefix(reader):
    created = updated = errors = 0
    for row in reader:
        try:
            obj, is_new = ImsiPrefix.objects.update_or_create(
                prefix=row.get('prefix', '').strip(),
                defaults={
                    'mcc': row.get('mcc', '').strip(),
                    'mnc': row.get('mnc', '').strip(),
                    'operator': row.get('operator', '').strip(),
                    'country': row.get('country', '').strip(),
                    'is_home': row.get('is_home', '').lower() in ('1', 'true', 'yes'),
                    'notes': row.get('notes', '').strip(),
                }
            )
            if is_new:
                created += 1
            else:
                updated += 1
        except Exception:
            errors += 1
    return created, updated, errors


def _import_numbering_plan(reader):
    created = updated = errors = 0
    for row in reader:
        try:
            obj, is_new = NumberingPlan.objects.update_or_create(
                prefix=row.get('prefix', '').strip(),
                defaults={
                    'country': row.get('country', 'Sierra Leone').strip(),
                    'country_code': row.get('country_code', '').strip(),
                    'operator': row.get('operator', '').strip(),
                    'number_type': row.get('number_type', 'MOBILE').strip(),
                    'min_length': int(row.get('min_length', 8) or 8),
                    'max_length': int(row.get('max_length', 12) or 12),
                    'notes': row.get('notes', '').strip(),
                }
            )
            if is_new:
                created += 1
            else:
                updated += 1
        except Exception:
            errors += 1
    return created, updated, errors


def _import_trunks(reader):
    """Import trunks from CSV. Supports SWITCH_ID,TRUNK_ID,TRUNK_TYPE,OPERATOR format."""
    created = updated = errors = 0
    TRUNK_TYPE_MAP = {'0': 'INTERNAL', '1': 'INTERCONNECT', '3': 'ROAMING'}

    def _direction(switch_id, trunk_id):
        if switch_id.startswith('SLFTMSC'):
            last = trunk_id[-1].upper() if trunk_id else ''
            if last == 'I': return 'INCOMING'
            if last == 'O': return 'OUTGOING'
        return 'BOTH'

    for row in reader:
        try:
            # Support both SWITCH_ID.txt format and generic CSV format
            switch_id = (row.get('SWITCH_ID') or row.get('switch_id') or '').strip().upper()
            trunk_id  = (row.get('TRUNK_ID')  or row.get('trunk_id')  or '').strip().upper()
            if not trunk_id:
                continue

            raw_type = (row.get('TRUNK_TYPE') or row.get('trunk_type') or '').strip()
            trunk_type = TRUNK_TYPE_MAP.get(raw_type, raw_type if raw_type in dict(TrunkGroup.TRUNK_TYPE_CHOICES) else 'OTHER')
            operator = (row.get('OPERATOR') or row.get('partner') or '').strip()
            direction = (row.get('direction') or _direction(switch_id, trunk_id))
            future1 = (row.get('FUTURE1') or '').strip()
            future2 = (row.get('FUTURE2') or '').strip()
            desc = ' | '.join(p for p in [future1, future2, row.get('description', '').strip()] if p)

            obj, is_new = TrunkGroup.objects.update_or_create(
                switch_id=switch_id, trunk_id=trunk_id,
                defaults={
                    'name': trunk_id,
                    'trunk_type': trunk_type,
                    'direction': direction,
                    'partner': operator,
                    'country': row.get('country', '').strip(),
                    'prefix': row.get('prefix', '').strip(),
                    'description': desc,
                    'enabled': True,
                }
            )
            if is_new: created += 1
            else: updated += 1
        except Exception:
            errors += 1
    return created, updated, errors


