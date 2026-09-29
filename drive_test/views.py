"""
Drive Test dashboard views.
All views require login; role gates use existing RBAC decorators.
"""

import logging
import re
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import analyst_required, regulator_required, regulatory_admin_required

from .models import (
    BenchmarkCampaign, BenchmarkScore,
    Cell, DataQualityResult, District, DriveTestFile, DriveTestSession,
    Finding, Measurement, Region, RegulatoryRule, RegulatoryThreshold, Sector, Site,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard / index
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    import json
    from django.db.models import Sum

    today = date.today()
    thirty_days_ago = today - timedelta(days=30)

    # ── Basic session stats ──────────────────────────────────────────────────
    stats = DriveTestSession.objects.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
        pending=Count('id', filter=Q(status='PENDING') | Q(status='PROCESSING')),
        total_meas=Sum('total_measurements'),
    )
    total_sessions      = stats['total'] or 0
    completed_sessions  = stats['completed'] or 0
    processing_sessions = stats['pending'] or 0
    total_measurements  = stats['total_meas'] or 0
    completion_rate     = round(completed_sessions / total_sessions * 100) if total_sessions else 0
    operator_count      = DriveTestSession.objects.values('operator').distinct().count()

    # ── Recent sessions ──────────────────────────────────────────────────────
    # finding_count is already a model field (denormalized cache) — use it directly
    recent_sessions = (
        DriveTestSession.objects
        .select_related('operator', 'uploaded_by')
        .order_by('-created_at')[:10]
    )

    # ── Findings ─────────────────────────────────────────────────────────────
    open_findings_count = Finding.objects.filter(is_resolved=False).count()
    critical_findings   = Finding.objects.filter(is_resolved=False, severity='CRITICAL').count()
    open_findings_list  = (
        Finding.objects
        .filter(is_resolved=False)
        .select_related('session__operator', 'cell')
        .order_by('-created_at')[:8]
    )
    finding_summary = (
        Finding.objects.filter(is_resolved=False)
        .values('finding_type')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    # ── Operator summary (processed to plain dicts for template) ─────────────
    operator_summary_qs = (
        DriveTestSession.objects
        .values('operator__name', 'operator__code')
        .annotate(sessions=Count('id'), measurements=Sum('total_measurements'))
        .order_by('-sessions')[:8]
    )
    operator_summary = [
        {
            'name': row['operator__name'] or 'Unknown',
            'code': (row['operator__code'] or '').upper(),
            'sessions': row['sessions'],
            'measurements': row['measurements'] or 0,
        }
        for row in operator_summary_qs
    ]

    # ── Technology summary from RadioMeasurement ──────────────────────────────
    try:
        from .models import RadioMeasurement
        technology_summary_qs = (
            RadioMeasurement.objects
            .filter(measurement__is_valid=True)
            .exclude(technology='')
            .values('technology')
            .annotate(measurements=Count('pk'))  # pk = measurement OneToOne PK
            .order_by('-measurements')[:6]
        )
        technology_summary = list(technology_summary_qs)
    except Exception:
        logger.exception('Drive Test: technology summary query failed')
        technology_summary = []

    technology_chart_data = [
        {'technology': r['technology'], 'measurements': r['measurements']}
        for r in technology_summary
    ]

    # ── Measurement trend — last 30 days by session test_date ────────────────
    session_trend = (
        DriveTestSession.objects
        .filter(test_date__gte=thirty_days_ago)
        .values('test_date')
        .annotate(measurements=Sum('total_measurements'))
        .order_by('test_date')
    )
    measurement_trend_data = [
        {
            'label': row['test_date'].strftime('%d %b'),
            'measurements': row['measurements'] or 0,
        }
        for row in session_trend
    ]

    from django.conf import settings as django_settings
    return render(request, 'drive_test/dashboard.html', {
        # scalar KPIs
        'total_sessions':      total_sessions,
        'completed_sessions':  completed_sessions,
        'processing_sessions': processing_sessions,
        'completion_rate':     completion_rate,
        'total_measurements':  total_measurements,
        'operator_count':      operator_count,
        'open_findings':       open_findings_count,
        'critical_findings':   critical_findings,
        # lists
        'recent_sessions':     recent_sessions,
        'operator_summary':    operator_summary,
        'technology_summary':  technology_summary,
        'open_findings_list':  open_findings_list,
        'finding_summary':     finding_summary,
        # chart data — passed as Python objects; json_script in template handles serialization
        'measurement_trend_json': measurement_trend_data,
        'technology_chart_json':  technology_chart_data,
        # map configuration
        'drive_test_map_tile_url':    getattr(django_settings, 'DRIVE_TEST_MAP_TILE_URL', ''),
        'drive_test_map_attribution': getattr(django_settings, 'DRIVE_TEST_MAP_ATTRIBUTION', '&copy; OpenStreetMap contributors'),
        'page_title': 'Drive Test Dashboard',
    })


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_PAGE_SIZES = (10, 25, 50)


@login_required
def session_list(request):
    from django.db.models import Exists, OuterRef
    from reference.models import Operator
    from .models import DriveTestFile, RadioMeasurement

    GET = request.GET
    q = GET.get('q', '').strip()
    operator = GET.get('operator', '').strip()
    technology = GET.get('technology', '').strip()
    status = GET.get('status', '').strip()
    date_from = GET.get('date_from', '').strip()
    date_to = GET.get('date_to', '').strip()
    try:
        per_page = int(GET.get('per_page', SESSION_PAGE_SIZES[0]))
    except ValueError:
        per_page = SESSION_PAGE_SIZES[0]
    if per_page not in SESSION_PAGE_SIZES:
        per_page = SESSION_PAGE_SIZES[0]

    base = DriveTestSession.objects.all()
    if q:
        base = base.filter(
            Q(session_ref__icontains=q) | Q(title__icontains=q)
            | Q(operator__name__icontains=q)
            | Exists(DriveTestFile.objects.filter(
                session=OuterRef('pk'), original_filename__icontains=q))
        )
    if operator:
        op_q = Q(operator__code=operator)
        if operator.isdigit():
            op_q |= Q(operator_id=int(operator))
        base = base.filter(op_q)
    if technology:
        base = base.filter(Exists(RadioMeasurement.objects.filter(
            technology=technology, measurement__drive_file__session=OuterRef('pk'))))
    try:
        if date_from:
            base = base.filter(test_date__gte=date_from)
        if date_to:
            base = base.filter(test_date__lte=date_to)
    except (ValueError, ValidationError):
        date_from = date_to = ''
        base = DriveTestSession.objects.none()

    # Summary reflects every filter except status, so the cards stay comparable.
    summary = base.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        processing=Count('id', filter=Q(status='PROCESSING')),
        failed=Count('id', filter=Q(status='FAILED')),
    )

    qs = base.filter(status=status) if status else base
    qs = (qs.select_related('operator')
            .annotate(n_findings=Count('findings'))
            .order_by('-test_date', '-created_at', '-pk'))
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(GET.get('page', 1))
    sessions = list(page_obj.object_list)

    # Technologies detected from measurements, one grouped query for the page only.
    techs = {}
    if sessions:
        rows = (RadioMeasurement.objects
                .filter(measurement__drive_file__session_id__in=[s.pk for s in sessions])
                .exclude(technology='')
                .values_list('measurement__drive_file__session_id', 'technology')
                .distinct())
        for sid, t in rows:
            techs.setdefault(sid, []).append(t)
    for s in sessions:
        s.technologies = sorted(techs.get(s.pk, []))

    keep = GET.copy()
    keep.pop('page', None)
    keep_qs = keep.urlencode()

    filters_active = bool(q or operator or technology or status or date_from or date_to)
    return render(request, 'drive_test/session_list.html', {
        'sessions': sessions,
        'page_obj': page_obj,
        'page_title': 'Sessions',
        'summary': summary,
        'filters': {'q': q, 'operator': operator, 'technology': technology,
                    'status': status, 'date_from': date_from, 'date_to': date_to},
        'filters_active': filters_active,
        'has_any_sessions': DriveTestSession.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': ['2G', '3G', '4G', '5G'],
        'status_choices': DriveTestSession.Status.choices,
        'per_page': per_page,
        'page_sizes': SESSION_PAGE_SIZES,
        'keep_qs': keep_qs,
        'keep_qs_no_per_page': _without(GET, 'page', 'per_page'),
        'first_item': page_obj.start_index(),
        'last_item': page_obj.end_index(),
    })


def _without(querydict, *keys):
    qd = querydict.copy()
    for k in keys:
        qd.pop(k, None)
    return qd.urlencode()


def _format_duration(delta):
    if delta is None:
        return ''
    secs = int(delta.total_seconds())
    h, rem = divmod(secs, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f'{h}h {m:02d}m {sec:02d}s'
    return f'{m}m {sec:02d}s' if m else f'{sec}s'


@login_required
def session_detail(request, session_ref):
    from django.db.models import Max, Min
    from .models import Measurement, RadioMeasurement

    session = get_object_or_404(
        DriveTestSession.objects.select_related('operator', 'region', 'uploaded_by'),
        session_ref=session_ref,
    )
    files = list(session.files.select_related('parser_profile').order_by('-uploaded_at'))

    meas = Measurement.objects.filter(drive_file__session=session)
    span = meas.aggregate(first=Min('captured_at'), last=Max('captured_at'))
    duration = (span['last'] - span['first']) if span['first'] and span['last'] else None
    gps_points = meas.filter(is_valid=True).exclude(latitude=0.0, longitude=0.0).count()
    matched = meas.filter(matched_cell__isnull=False).count()
    total = session.total_measurements
    technologies = sorted(
        RadioMeasurement.objects.filter(measurement__drive_file__session=session)
        .exclude(technology='').values_list('technology', flat=True).distinct())
    device = (meas.filter(test_device__isnull=False)
              .values_list(
                  'test_device__device_model__manufacturer__name',
                  'test_device__device_model__model_name',
                  'test_device__label',
              ).first())

    if device:
        manufacturer_name, model_name, label = device
        parts = []
        # Capitalise manufacturer (e.g. "samsung" → "Samsung")
        if manufacturer_name and manufacturer_name.lower() != 'unknown':
            parts.append(manufacturer_name.capitalize())
        if model_name and model_name.lower() != 'unknown':
            parts.append(model_name)
        if label and label not in parts:
            parts.append(label)
        device_display = ' '.join(parts)
    else:
        device_display = ''

    in_progress = [f for f in files if f.status in ('RECEIVED', 'PARSING', 'PARSED', 'MATCHING', 'NORMALIZING')]
    failed_errors = [_safe_error(f.error_message) for f in files if f.status == 'FAILED']

    back_qs = request.GET.get('back', '')
    back_url = reverse('drive_test:session_list') + (f'?{back_qs}' if back_qs else '')

    return render(request, 'drive_test/session_detail.html', {
        'session': session,
        'files': files,
        'page_title': f'Session {session.session_ref}',
        'back_url': back_url,
        'technologies': technologies,
        'duration_label': _format_duration(duration),
        'gps_points': gps_points,
        'matched_count': matched,
        'unmatched_count': max(total - matched, 0) if total else 0,
        'device': device_display,
        'finding_total': session.findings.count(),
        'in_progress_files': in_progress,
        'failed_errors': failed_errors,
        'is_processing': session.status in ('UPLOADING', 'PENDING', 'PROCESSING') and bool(in_progress),
        'is_failed': session.status == 'FAILED',
        'has_data': total > 0,
        'can_report': total > 0 and session.status in ('COMPLETED', 'PARTIAL'),
        'measurement_page_sizes': (50, 100, 250),
    })


def _format_bytes(n):
    return ('%g GB' % (n / 1024 ** 3)) if n >= 1024 ** 3 else ('%g MB' % (n / 1024 ** 2))


def _upload_context(**extra):
    from reference.models import Operator
    from .models import ParserProfile
    profiles = ParserProfile.objects.filter(is_active=True).order_by('name')
    exts = sorted({e.lower() for p in profiles for e in (p.file_extensions or [])})
    ctx = {
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'test_types': DriveTestSession.TestType.choices,
        'supported_extensions': exts,
        'supported_parsers': list(profiles.values_list('name', flat=True)),
        'page_title': 'Upload Drive Test',
        'max_upload_bytes': settings.DRIVE_TEST_MAX_UPLOAD_BYTES,
        'max_upload_label': _format_bytes(settings.DRIVE_TEST_MAX_UPLOAD_BYTES),
    }
    ctx.update(extra)
    return ctx


@login_required
def session_upload(request):
    """Upload a new drive test file and create/attach it to a session."""
    if request.method == 'POST':
        return _handle_upload(request)
    return render(request, 'drive_test/session_upload.html', _upload_context())


def _upload_error(request, wants_json, code, message, status=400, **extra):
    if wants_json:
        return JsonResponse({'ok': False, 'code': code, 'message': message, **extra}, status=status)
    return render(request, 'drive_test/session_upload.html', _upload_context(errors=[message]))


def _peek_file(parser_profile, path):
    """Read the first measurement with the file's real parser (no DB writes).

    Returns dict with readable, has_data, has_gps, technology, mcc, mnc,
    device_info, start_date, tags. A parser failure means unreadable.
    """
    import importlib
    info = {
        'readable': False, 'has_data': False, 'has_gps': False,
        'technology': '', 'mcc': '', 'mnc': '',
        'device_info': {}, 'start_date': '', 'tags': [],
    }
    try:
        mod, cls = parser_profile.parser_class.rsplit('.', 1)
        parser = getattr(importlib.import_module(mod), cls)(config=parser_profile.default_config)
        first = next(iter(parser.parse(path)), None)
    except Exception:
        logger.exception('Drive Test upload: parser peek failed')
        return info
    info['readable'] = True
    if first is not None:
        info['has_data'] = True
        info['has_gps'] = first.latitude is not None and first.longitude is not None
        info['technology'] = first.technology or ''
        info['mcc'], info['mnc'] = first.obs_mcc or '', first.obs_mnc or ''
        info['device_info'] = first.raw_data.get('__device__', {})
        if first.captured_at:
            try:
                info['start_date'] = first.captured_at.date().isoformat()
            except Exception:
                pass
        trp_tags = first.raw_data.get('trp_tags', '')
        if trp_tags:
            info['tags'] = [t.strip() for t in trp_tags.split(';') if t.strip()]
    return info


def _detect_operator(peek, path):
    """Operator for an uploaded file, or None. Tries the file's MCC/MNC, then the SIM IMSI prefix.

    The IMSI is used only for this lookup; it is never stored or returned to the browser.
    """
    from reference.models import Operator
    if peek.get('mcc') and peek.get('mnc'):
        op = Operator.objects.filter(home_mcc=peek['mcc'], home_mnc=peek['mnc'].zfill(2), enabled=True).first()
        if op:
            return op
    from .parsers.trp_parser import read_sim_imsi
    imsi = read_sim_imsi(path)
    if imsi:
        ops = [o for o in Operator.objects.filter(enabled=True).exclude(home_plmn='') if imsi.startswith(o.home_plmn)]
        ops.sort(key=lambda o: len(o.home_plmn), reverse=True)   # most specific PLMN wins
        if ops:
            return ops[0]
    return None


@login_required
def session_upload_preview(request):
    """
    POST: peek a file and return detected metadata as JSON. No DB writes.
    Called client-side when a file is selected, before the real upload.
    """
    import os
    import tempfile
    from pathlib import Path
    from reference.models import Operator
    from .services.file_handler import detect_parser

    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'ok': False, 'error': 'No file'}, status=400)

    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    try:
        parser_profile = detect_parser(tmp_path)
        if parser_profile is None:
            return JsonResponse({'ok': True, 'detected': False})

        peek = _peek_file(parser_profile, tmp_path)

        # Build device display string
        d = peek.get('device_info', {})
        parts = []
        mfr = (d.get('manufacturer') or '').strip()
        mdl = (d.get('model_name') or '').strip()
        lbl = (d.get('label') or '').strip()
        if mfr and mfr.lower() != 'unknown':
            parts.append(mfr.capitalize())
        if mdl and mdl.lower() != 'unknown':
            parts.append(mdl)
        if lbl and lbl not in parts:
            parts.append(lbl)
        device_display = ' '.join(parts)

        # Operator lookup from MCC/MNC, falling back to the SIM's IMSI prefix
        operator_id = None
        operator_name = ''
        operator_source = ''
        op = _detect_operator(peek, tmp_path)
        if op:
            operator_id = op.pk
            operator_name = op.name
            operator_source = 'detected'

        # Test type hint from TRP tags
        test_type = ''
        for tag in peek.get('tags', []):
            t = tag.upper()
            if 'INDOOR' in t or 'WALK' in t:
                test_type = 'indoor'
                break
            if any(x in t for x in ('_OS', 'OUTDOOR', 'DRIVE')):
                test_type = 'outdoor'
                break

        return JsonResponse({
            'ok': True,
            'detected': True,
            'parser': parser_profile.name,
            'technology': peek['technology'],
            'mcc': peek['mcc'],
            'mnc': peek['mnc'],
            'operator_id': operator_id,
            'operator_name': operator_name,
            'operator_source': operator_source,
            'device': device_display,
            'test_date': peek.get('start_date', ''),
            'test_type': test_type,
            'tags': peek.get('tags', []),
            'has_gps': peek['has_gps'],
        })
    except Exception:
        logger.exception('Drive Test upload preview failed')
        return JsonResponse({'ok': False, 'error': 'Preview failed'}, status=500)
    finally:
        try:
            if tmp_path.exists():
                os.unlink(tmp_path)
        except Exception:
            pass


def _handle_upload(request):
    """Process the upload POST. JSON for the upload page's fetch(); redirect otherwise."""
    import os
    import tempfile
    from pathlib import Path
    from django.utils.text import get_valid_filename
    from reference.models import Operator
    from .services.file_handler import (
        check_duplicate, detect_parser, dispatch_processing, sha256_of_file, stage_file,
    )

    wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    uploaded_file = request.FILES.get('drive_test_file') or request.FILES.get('file')
    if not uploaded_file:
        return _upload_error(request, wants_json, 'no_file', 'No file selected.')
    if uploaded_file.size > settings.DRIVE_TEST_MAX_UPLOAD_BYTES:
        return _upload_error(
            request, wants_json, 'too_large',
            'The file exceeds the maximum upload size of %s.' % _format_bytes(settings.DRIVE_TEST_MAX_UPLOAD_BYTES), 413)

    test_type = request.POST.get('test_type', 'outdoor')
    if test_type not in DriveTestSession.TestType.values:
        test_type = DriveTestSession.TestType.OUTDOOR
    title = request.POST.get('title', '').strip()[:200]
    notes = request.POST.get('description', '').strip()
    test_date = request.POST.get('test_date') or date.today()
    region_id = request.POST.get('region') or None
    operator_id = request.POST.get('operator', '').strip()

    operator, operator_source = None, ''
    if operator_id:
        try:
            operator = Operator.objects.get(pk=int(operator_id), enabled=True)
            operator_source = 'user'
        except (Operator.DoesNotExist, ValueError):
            return _upload_error(request, wants_json, 'bad_operator', 'Invalid operator.')

    display_name = os.path.basename(uploaded_file.name)
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(display_name).suffix) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    try:
        # Parser detection is authoritative — each parser scores the file by
        # content and the highest confidence wins. Never content_type.
        parser_profile = detect_parser(tmp_path)
        if parser_profile is None:
            from .services.file_handler import explain_detection_failure
            return _upload_error(
                request, wants_json, 'unsupported_format',
                explain_detection_failure(tmp_path), 422)

        sha256 = sha256_of_file(tmp_path)
        if check_duplicate(sha256):
            existing = (DriveTestFile.objects.select_related('session')
                        .filter(sha256=sha256).first())
            return _upload_error(
                request, wants_json, 'duplicate', 'This file has already been uploaded.', 409,
                existing_session=existing.session.session_ref if existing else '',
                existing_url=reverse('drive_test:session_detail', args=[existing.session.session_ref])
                if existing else '')

        peek = _peek_file(parser_profile, tmp_path)
        if not peek['readable'] or not peek['has_data']:
            return _upload_error(
                request, wants_json, 'validation_failed',
                'The file could not be read, or contains no measurement data with GPS positions.',
                422, parser=parser_profile.name, checks=peek)

        if operator is None:
            operator = _detect_operator(peek, tmp_path)
            if operator is not None:
                operator_source = 'detected'
            if operator is None:
                return _upload_error(
                    request, wants_json, 'operator_required',
                    'The operator could not be detected from the file. Please select one.',
                    422, parser=parser_profile.name, checks=peek)

        target = None
        session_ref = request.POST.get('session_ref', '').strip()
        if session_ref:
            target = DriveTestSession.objects.filter(session_ref=session_ref, uploaded_by=request.user).first()
            if target is None:
                return _upload_error(request, wants_json, 'bad_session', 'The batch session was not found.', 404)
            if target.operator_id != operator.pk:
                return _upload_error(
                    request, wants_json, 'operator_mismatch',
                    'This file belongs to %s, but the batch session is for %s. Upload it separately.'
                    % (operator.name, target.operator.name), 422)

        stored_path, sha256 = stage_file(
            tmp_path, get_valid_filename(display_name) or 'upload', operator.code)
    except FileExistsError:
        return _upload_error(request, wants_json, 'duplicate',
                             'This file has already been uploaded.', 409)
    finally:
        if tmp_path.exists():
            os.unlink(tmp_path)

    if target is not None:
        session = target
        if session.status in ('COMPLETED', 'PARTIAL', 'FAILED'):
            session.status = 'PROCESSING'   # a new file is pending; aggregates re-run when it finishes
            session.save(update_fields=['status', 'updated_at'])
    else:
        session = DriveTestSession.objects.create(
            operator=operator, title=title, notes=notes, test_date=test_date,
            test_type=test_type, region_id=region_id, uploaded_by=request.user,
            metadata={'operator_source': operator_source},
        )
    drive_file = DriveTestFile.objects.create(
        session=session,
        original_filename=display_name,
        file_path=str(stored_path),
        file_size=uploaded_file.size,
        sha256=sha256,
        mime_type=uploaded_file.content_type or '',
        parser_profile=parser_profile,
        detected_format=parser_profile.name,
    )

    dispatch_processing(drive_file.id)

    from .services import audit as al
    al.log(request=request, action='UPLOAD', obj=drive_file,
           description=f'File uploaded: {display_name} ({session.session_ref})',
           operator=operator.code, session_ref=session.session_ref, file_size=uploaded_file.size)

    if not wants_json:
        return redirect('drive_test:session_detail', session_ref=session.session_ref)
    return JsonResponse({
        'ok': True,
        'file_id': drive_file.id,
        'session_ref': session.session_ref,
        'file_name': display_name,
        'file_size': uploaded_file.size,
        'parser': parser_profile.name,
        'operator': operator.name,
        'operator_source': operator_source,
        'technology': peek['technology'],
        'checks': peek,
        'status_url': reverse('drive_test:session_upload_status', args=[drive_file.id]),
        'session_url': reverse('drive_test:session_detail', args=[session.session_ref]),
    })


_PATH_RE = re.compile(r'[A-Za-z]:\\[^\s]+|/[\w.\-]+(?:/[\w.\-]+)+')


def _safe_error(text):
    """Strip filesystem paths / tracebacks from a stored error before showing it."""
    first = (text or '').strip().splitlines()[0] if (text or '').strip() else ''
    return _PATH_RE.sub('[path]', first)[:200] or 'The drive-test file could not be processed.'


@login_required
def session_upload_status(request, file_id):
    """Polled by the upload page for real pipeline state."""
    f = get_object_or_404(DriveTestFile.objects.select_related('session'), pk=file_id)
    s = f.session
    return JsonResponse({
        'file_status': f.status,
        'session_status': s.status,
        'session_ref': s.session_ref,
        'measurements': s.total_measurements if f.status == 'COMPLETED' else f.measurement_count,
        'file_measurements': f.measurement_count,
        'findings': s.finding_count,
        'error': _safe_error(f.error_message) if f.status == 'FAILED' else '',
        'session_url': reverse('drive_test:session_detail', args=[s.session_ref]),
    })


# ---------------------------------------------------------------------------
# KPI endpoint
# ---------------------------------------------------------------------------

@login_required
def session_workspace(request, session_ref):
    """The synchronised analysis workspace: map, charts, events and detail.

    Ships only the session shell; the panes fetch their data from
    session_timeseries so the page renders before the series arrives.
    """
    from .services.timeseries import ALL_METRICS

    session = get_object_or_404(
        DriveTestSession.objects.select_related('operator', 'region'),
        session_ref=session_ref,
    )
    return render(request, 'drive_test/session_workspace.html', {
        'session': session,
        'page_title': f'Workspace · {session.session_ref}',
        'config': {
            'sessionRef': session.session_ref,
            'timeseriesUrl': reverse('drive_test:session_timeseries',
                                     args=[session.session_ref]),
            'detailUrlBase': '/drive-test/measurements/',
            'tileUrl': getattr(settings, 'DRIVE_TEST_MAP_TILE_URL', ''),
            'tileAttribution': getattr(settings, 'DRIVE_TEST_MAP_ATTRIBUTION', ''),
            'metrics': ALL_METRICS,
        },
    })


@login_required
def session_timeseries(request, session_ref):
    """Column-oriented, server-decimated time series for the analysis workspace.

    Query params:
        metrics=rsrp,sinr   restrict the returned series (default: all available)
        primary=rsrp        the metric decimation preserves the extremes of
        max_points=4000     cap on returned samples
    """
    from .services.timeseries import DEFAULT_MAX_POINTS, session_timeseries as build

    session = get_object_or_404(DriveTestSession, session_ref=session_ref)

    raw_metrics = (request.GET.get('metrics') or '').strip()
    metrics = [m.strip() for m in raw_metrics.split(',') if m.strip()] or None

    try:
        max_points = int(request.GET.get('max_points') or DEFAULT_MAX_POINTS)
    except (TypeError, ValueError):
        max_points = DEFAULT_MAX_POINTS
    max_points = max(100, min(max_points, 50000))

    return JsonResponse(build(
        session,
        metrics=metrics,
        primary=(request.GET.get('primary') or 'rsrp').strip(),
        max_points=max_points,
    ))


@login_required
def session_kpis(request, session_ref):
    """Return computed KPIs for a session as JSON."""
    from .services.kpi import DriveTestKpiService

    session = get_object_or_404(
        DriveTestSession.objects.select_related('operator'),
        session_ref=session_ref,
    )
    data = DriveTestKpiService(session).compute()
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Excel report
# ---------------------------------------------------------------------------

@login_required
def session_report_excel(request, session_ref):
    """Generate and return a .xlsx report for a drive test session."""
    import io
    from django.http import HttpResponse
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, numbers as xl_numbers
        from openpyxl.utils import get_column_letter
    except ImportError:
        return HttpResponse('openpyxl is not installed.', status=500)

    from .models import Measurement, RadioMeasurement, ServiceMeasurement
    from .services.kpi import DriveTestKpiService

    session = get_object_or_404(
        DriveTestSession.objects.select_related('operator', 'region', 'uploaded_by'),
        session_ref=session_ref,
    )

    kpis = DriveTestKpiService(session).compute()

    wb = openpyxl.Workbook()

    # ── helpers ──────────────────────────────────────────────────────────
    HDR_FILL  = PatternFill('solid', fgColor='1A237E')
    HDR_FONT  = Font(color='FFFFFF', bold=True, size=10)
    HDR_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)

    def _header_row(ws, cols):
        ws.append(cols)
        for cell in ws[ws.max_row]:
            cell.fill  = HDR_FILL
            cell.font  = HDR_FONT
            cell.alignment = HDR_ALIGN

    def _auto_width(ws, extra=2):
        for col in ws.columns:
            max_len = max((len(str(c.value or '')) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + extra, 40)

    def _freeze(ws, cell='A2'):
        ws.freeze_panes = cell

    def _v(val, fallback='—'):
        return val if val is not None else fallback

    # ── Sheet 1 — Session Summary ─────────────────────────────────────
    ws1 = wb.active
    ws1.title = 'Session Summary'

    signal  = kpis.get('signal', {})
    voice   = kpis.get('voice', {})
    mob     = kpis.get('mobility', {})
    net     = kpis.get('network', {})
    meas_s  = kpis.get('measurements', {})

    rows = [
        ('Session Reference',   session.session_ref),
        ('Title',               session.title or '—'),
        ('Operator',            session.operator.name),
        ('Test Date',           str(session.test_date)),
        ('Test Type',           session.get_test_type_display()),
        ('Region',              session.region.name if session.region_id else '—'),
        ('Uploaded By',         session.uploaded_by.get_full_name() or session.uploaded_by.username),
        ('Status',              session.status),
        ('', ''),
        ('── MEASUREMENTS ──', ''),
        ('Total Measurements',  meas_s.get('total', 0)),
        ('Measurements with RSSI', meas_s.get('with_rssi', 0)),
        ('Measurements with GPS',  meas_s.get('with_gps', 0)),
        ('', ''),
        ('── SIGNAL ──', ''),
        ('Mean RSSI (dBm)',     _v(signal.get('mean_rssi'))),
        ('Min RSSI (dBm)',      _v(signal.get('min_rssi'))),
        ('Max RSSI (dBm)',      _v(signal.get('max_rssi'))),
        ('Coverage %',         _v(signal.get('coverage', {}).get('coverage_percent'))),
        ('Good RSSI (≥−85 dBm) %',      _v(signal.get('rssi_distribution', {}).get('good', {}).get('percentage'))),
        ('Fair RSSI (≥−95 dBm) %',      _v(signal.get('rssi_distribution', {}).get('fair', {}).get('percentage'))),
        ('Poor RSSI (≥−105 dBm) %',     _v(signal.get('rssi_distribution', {}).get('poor', {}).get('percentage'))),
        ('Very Poor RSSI (<−105 dBm) %', _v(signal.get('rssi_distribution', {}).get('very_poor', {}).get('percentage'))),
        ('No Signal %',        _v(signal.get('rssi_distribution', {}).get('no_signal', {}).get('percentage'))),
        ('', ''),
        ('── VOICE ──', ''),
        ('Total Calls',        voice.get('total_calls', 0)),
        ('Connected Calls',    voice.get('connected', 0)),
        ('Dropped Calls',      voice.get('dropped', 0)),
        ('CSSR (%)',           _v(voice.get('cssr_percent'))),
        ('DCR (%)',            _v(voice.get('dcr_percent'))),
        ('Mean Call Duration (s)', _v(voice.get('mean_call_duration_s'))),
        ('Mean MOS',           _v(voice.get('mean_mos'))),
        ('', ''),
        ('── MOBILITY ──', ''),
        ('Mean Speed (km/h)',  _v(mob.get('mean_speed_kmh'))),
        ('Max Speed (km/h)',   _v(mob.get('max_speed_kmh'))),
        ('Total Distance (km)', _v(mob.get('total_distance_km'))),
        ('', ''),
        ('── NETWORK ──', ''),
        ('Unique Cells',       net.get('unique_cells', 0)),
    ]

    _header_row(ws1, ['Parameter', 'Value'])
    for label, val in rows:
        ws1.append([label, val])

    ws1.column_dimensions['A'].width = 36
    ws1.column_dimensions['B'].width = 22
    _freeze(ws1)

    # ── Sheet 2 — Route Summary ───────────────────────────────────────
    ws2 = wb.create_sheet('Route Summary')
    _header_row(ws2, [
        'Sequence', 'Timestamp', 'Latitude', 'Longitude', 'Altitude (m)',
        'Speed (km/h)', 'Technology', 'Cell ID', 'Site',
    ])

    meas_qs = (
        Measurement.objects
        .filter(drive_file__session=session, is_valid=True)
        .select_related('radio', 'matched_cell__sector__site')
        .order_by('drive_file', 'sequence_num')
    )
    for m in meas_qs:
        radio = getattr(m, 'radio', None)
        cell  = m.matched_cell
        site  = (cell.sector.site if cell and cell.sector_id and cell.sector.site_id else None) if cell else None
        ws2.append([
            m.sequence_num,
            m.captured_at.strftime('%Y-%m-%d %H:%M:%S') if m.captured_at else '',
            m.latitude,
            m.longitude,
            m.altitude_m,
            m.speed_kmh,
            radio.technology if radio else '',
            cell.cell_id if cell else (f'CI:{m.obs_ci}' if m.obs_ci else ''),
            site.name if site else '',
        ])

    _auto_width(ws2)
    _freeze(ws2)
    ws2.auto_filter.ref = ws2.dimensions

    # ── Sheet 3 — Call Log ────────────────────────────────────────────
    ws3 = wb.create_sheet('Call Log')
    _header_row(ws3, [
        'Measurement Seq', 'Timestamp', 'Service Type', 'Outcome',
        'Duration (s)', 'MOS', 'Setup Time (ms)',
    ])

    svc_qs = (
        ServiceMeasurement.objects
        .filter(
            measurement__drive_file__session=session,
            service_type=ServiceMeasurement.ServiceType.VOICE,
        )
        .select_related('measurement')
        .order_by('measurement__sequence_num')
    )
    for svc in svc_qs:
        m = svc.measurement
        ws3.append([
            m.sequence_num,
            m.captured_at.strftime('%Y-%m-%d %H:%M:%S') if m.captured_at else '',
            svc.service_type,
            svc.outcome,
            svc.call_duration_s,
            svc.mos,
            svc.call_setup_time_ms,
        ])

    _auto_width(ws3)
    _freeze(ws3)

    # ── Sheet 4 — Measurements ────────────────────────────────────────
    ws4 = wb.create_sheet('Measurements')
    _header_row(ws4, [
        'Sequence', 'Timestamp', 'Latitude', 'Longitude', 'Altitude (m)',
        'Speed (km/h)', 'Technology', 'RSSI (dBm)', 'RSRP (dBm)', 'RSRQ (dB)',
        'SINR (dB)', 'ARFCN', 'LAC', 'CI', 'Cell', 'Site', 'Valid',
    ])

    for m in meas_qs:  # already fetched above
        radio = getattr(m, 'radio', None)
        cell  = m.matched_cell
        site  = (cell.sector.site if cell and cell.sector_id and cell.sector.site_id else None) if cell else None
        ws4.append([
            m.sequence_num,
            m.captured_at.strftime('%Y-%m-%d %H:%M:%S') if m.captured_at else '',
            m.latitude,
            m.longitude,
            m.altitude_m,
            m.speed_kmh,
            radio.technology if radio else '',
            radio.rssi  if radio else None,
            radio.rsrp  if radio else None,
            radio.rsrq  if radio else None,
            radio.sinr  if radio else None,
            m.obs_earfcn,
            m.obs_lac,
            m.obs_ci,
            cell.cell_id if cell else '',
            site.name if site else '',
            'Yes' if m.is_valid else 'No',
        ])

    _auto_width(ws4)
    _freeze(ws4)
    ws4.auto_filter.ref = ws4.dimensions

    # ── Stream response ────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f'drive_test_{session.session_ref}_{session.test_date}.xlsx'
    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Reference data views
# ---------------------------------------------------------------------------

SITE_PAGE_SIZES = (25, 50, 100)
_SITE_GPS = (Q(latitude__isnull=False, longitude__isnull=False) & ~Q(latitude=0.0, longitude=0.0))


def _can_edit_reference(user):
    return user.is_active and (user.is_superuser or getattr(user, 'is_regulatory_admin', False))


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _filtered_sites(GET):
    """Apply the Sites page filters server-side. Returns (queryset, filters dict)."""
    from django.db.models import Exists, OuterRef

    f = {k: GET.get(k, '').strip() for k in ('q', 'operator', 'region', 'district', 'technology', 'status')}
    qs = Site.objects.all()
    if f['q']:
        qs = qs.filter(Q(site_id__icontains=f['q']) | Q(name__icontains=f['q'])
                       | Q(operator__name__icontains=f['q']) | Q(address__icontains=f['q']))
    if f['operator']:
        qs = qs.filter(operator__code=f['operator'])
    if _to_int(f['region']) is not None:
        qs = qs.filter(chiefdom__district__region_id=int(f['region']))
    if _to_int(f['district']) is not None:
        qs = qs.filter(chiefdom__district_id=int(f['district']))
    if f['technology']:
        qs = qs.filter(Exists(Cell.objects.filter(sector__site=OuterRef('pk'), technology=f['technology'])))
    if f['status'] == 'active':
        qs = qs.filter(is_active=True)
    elif f['status'] == 'inactive':
        qs = qs.filter(is_active=False)
    return qs, f


def _site_technologies(site_ids):
    techs = {}
    rows = (Cell.objects.filter(sector__site_id__in=site_ids).exclude(technology='')
            .values_list('sector__site_id', 'technology').order_by().distinct())
    for sid, t in rows:
        techs.setdefault(sid, []).append(t)
    return {k: sorted(v) for k, v in techs.items()}


@login_required
def site_list(request):
    from reference.models import Operator

    base, filters = _filtered_sites(request.GET)
    summary = base.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        operators=Count('operator', distinct=True),
        gps=Count('id', filter=_SITE_GPS),
    )
    try:
        per_page = int(request.GET.get('per_page', SITE_PAGE_SIZES[0]))
    except ValueError:
        per_page = SITE_PAGE_SIZES[0]
    if per_page not in SITE_PAGE_SIZES:
        per_page = SITE_PAGE_SIZES[0]

    qs = (base.select_related('operator', 'chiefdom__district__region')
          .annotate(n_sectors=Count('sectors', distinct=True),
                    n_cells=Count('sectors__cells', distinct=True))
          .order_by('operator__name', 'site_id'))
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    sites = list(page_obj.object_list)
    techs = _site_technologies([s.pk for s in sites]) if sites else {}
    for s in sites:
        s.technologies = techs.get(s.pk, [])
        s.has_gps = s.latitude is not None and s.longitude is not None and not (s.latitude == 0 and s.longitude == 0)

    keep = request.GET.copy()
    keep.pop('page', None)
    districts = District.objects.select_related('region').order_by('region__name', 'name')
    if _to_int(filters['region']) is not None:
        districts = districts.filter(region_id=int(filters['region']))
    return render(request, 'drive_test/site_list.html', {
        'sites': sites,
        'page_obj': page_obj,
        'page_title': 'Sites',
        'summary': summary,
        'missing_gps': summary['total'] - summary['gps'],
        'filters': filters,
        'filters_active': any(filters.values()),
        'has_any_sites': Site.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'regions': Region.objects.order_by('name'),
        'districts': districts,
        'technology_choices': sorted(Cell.objects.exclude(technology='')
                                     .values_list('technology', flat=True).order_by().distinct()),
        'per_page': per_page,
        'page_sizes': SITE_PAGE_SIZES,
        'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index(),
        'last_item': page_obj.end_index(),
        'can_edit': _can_edit_reference(request.user),
        'map_url': reverse('drive_test:site_map_data'),
    })


@login_required
def site_map_data(request):
    """GeoJSON of the sites matching the current filters (real coordinates only)."""
    MAX_POINTS = 5000
    base, _ = _filtered_sites(request.GET)
    qs = list(base.filter(_SITE_GPS).select_related('operator')
              .order_by('operator__name', 'site_id')[:MAX_POINTS + 1])
    truncated = len(qs) > MAX_POINTS
    qs = qs[:MAX_POINTS]
    techs = _site_technologies([s.pk for s in qs]) if qs else {}
    return JsonResponse({
        'type': 'FeatureCollection',
        'truncated': truncated,
        'features': [{
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [s.longitude, s.latitude]},
            'properties': {
                'id': s.pk, 'site_id': s.site_id, 'name': s.name, 'operator': s.operator.name,
                'lat': round(s.latitude, 5), 'lon': round(s.longitude, 5),
                'technology': ' / '.join(techs.get(s.pk, [])), 'active': s.is_active,
                'url': reverse('drive_test:site_detail', args=[s.pk]),
            },
        } for s in qs],
    })


@login_required
def site_detail(request, pk):
    from django.db.models import Prefetch

    site = get_object_or_404(
        Site.objects.select_related('operator', 'vendor', 'chiefdom__district__region'), pk=pk)
    sectors = list(site.sectors.prefetch_related(
        Prefetch('cells', queryset=Cell.objects.order_by('technology', 'cell_id'))).order_by('sector_id'))
    cells = [c for s in sectors for c in s.cells.all()]
    matched = Measurement.objects.filter(matched_cell__sector__site=site)
    recent = (matched.values('drive_file__session__session_ref', 'drive_file__session__test_date')
              .annotate(n=Count('id')).order_by('-drive_file__session__test_date')[:5])
    has_gps = site.latitude is not None and site.longitude is not None and not (
        site.latitude == 0 and site.longitude == 0)
    return render(request, 'drive_test/site_detail.html', {
        'site': site,
        'sectors': sectors,
        'cell_count': len(cells),
        'technologies': sorted({c.technology for c in cells if c.technology}),
        'matched_count': matched.count(),
        'finding_count': Finding.objects.filter(cell__sector__site=site).count(),
        'recent_sessions': recent,
        'has_gps': has_gps,
        'can_edit': _can_edit_reference(request.user),
        'page_title': f'Site {site.site_id}',
    })


def _site_snapshot(site):
    return {
        'name': site.name, 'operator': site.operator.name if site.operator_id else None,
        'site_type': site.get_site_type_display(), 'address': site.address,
        'latitude': site.latitude, 'longitude': site.longitude, 'altitude_m': site.altitude_m,
        'chiefdom': str(site.chiefdom) if site.chiefdom_id else None, 'is_active': site.is_active,
    }


def _site_form_view(request, site=None):
    from .forms import SiteForm
    from .services import audit as al

    before = _site_snapshot(site) if site else None
    form = SiteForm(request.POST or None, instance=site)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        if before is None:
            al.log(request=request, action='CREATE', obj=saved,
                   description=f'Site created: {saved.site_id}', fields=_site_snapshot(saved))
        else:
            changes = al.changes_dict(before, _site_snapshot(saved))
            if changes:
                al.log(request=request, action='UPDATE', obj=saved,
                       description=f'Site updated: {saved.site_id}', changes=changes)
        messages.success(request, f'Site {saved.site_id} {"updated" if site else "created"}.')
        return redirect('drive_test:site_detail', pk=saved.pk)
    return render(request, 'drive_test/site_form.html', {
        'form': form, 'site': site,
        'page_title': f'Edit {site.site_id}' if site else 'Add Site',
    })


@regulatory_admin_required
def site_create(request):
    return _site_form_view(request)


@regulatory_admin_required
def site_edit(request, pk):
    return _site_form_view(request, get_object_or_404(Site, pk=pk))


CELL_PAGE_SIZES = (25, 50, 100)
_ID_SEARCH_FIELDS = ('ci', 'lac', 'tac', 'eci', 'nci', 'pci')


def _filtered_cells(GET):
    """Apply the Cells page filters server-side. Returns (queryset, filters dict)."""
    from django.db.models import Exists, OuterRef  # noqa: F401  (kept local like the other views)
    from .services import cell_reference as ref

    f = {k: GET.get(k, '').strip() for k in
         ('q', 'operator', 'technology', 'site', 'sector', 'sector_pk', 'band', 'status', 'matching')}
    qs = Cell.objects.all()
    if f['q']:
        q = Q(cell_id__icontains=f['q']) | Q(cgi__icontains=f['q']) | Q(ecgi__icontains=f['q']) \
            | Q(sector__site__name__icontains=f['q'])
        if f['q'].isdigit():
            for name in _ID_SEARCH_FIELDS:
                q |= Q(**{name: int(f['q'])})
        qs = qs.filter(q)
    if f['operator']:
        qs = qs.filter(operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(technology=f['technology'])
    if f['site']:
        qs = qs.filter(Q(sector__site__site_id__icontains=f['site']) | Q(sector__site__name__icontains=f['site']))
    if f['sector']:
        qs = qs.filter(sector__sector_id__icontains=f['sector'])
    if _to_int(f['sector_pk']) is not None:
        qs = qs.filter(sector_id=int(f['sector_pk']))
    if _to_int(f['band']) is not None:
        qs = qs.filter(band_id=int(f['band']))
    if f['status'] == 'active':
        qs = qs.filter(is_active=True)
    elif f['status'] == 'inactive':
        qs = qs.filter(is_active=False)
    if f['matching'] in ref.STATE_Q:
        qs = qs.filter(ref.STATE_Q[f['matching']])
    return qs, f


@login_required
def cell_list(request):
    from django.db.models import Exists, OuterRef
    from reference.models import Operator
    from .models import FrequencyBand
    from .services import cell_reference as ref

    base, filters = _filtered_cells(request.GET)
    summary = base.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        matchable=Count('id', filter=ref.Q_EXACT),
        coords=Count('id', filter=ref.Q_OWN_GPS),
    )
    try:
        per_page = int(request.GET.get('per_page', CELL_PAGE_SIZES[0]))
    except ValueError:
        per_page = CELL_PAGE_SIZES[0]
    if per_page not in CELL_PAGE_SIZES:
        per_page = CELL_PAGE_SIZES[0]

    twin = Cell.objects.filter(operator=OuterRef('operator')).exclude(pk=OuterRef('pk'))
    qs = (base.select_related('operator', 'sector__site', 'band')
          .annotate(dup_cgi=Exists(twin.filter(cgi=OuterRef('cgi'))),
                    dup_ecgi=Exists(twin.filter(ecgi=OuterRef('ecgi'))))
          .order_by('operator__name', 'cell_id'))
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    cells = list(page_obj.object_list)
    for c in cells:
        c.state = ref.cell_state(c)
        c.state_label = ref.STATE_LABELS[c.state]
        c.state_help = ref.STATE_HELP[c.state]
        c.warnings = ref.cell_warnings(c, dup_ids=bool((c.cgi and c.dup_cgi) or (c.ecgi and c.dup_ecgi)))
        c.identifier = ref.primary_identifier(c)
        c.band_label, c.channel_label = ref.frequency_label(c)
        c.own_gps = ref.has_valid_coords(c.latitude, c.longitude)
        site = c.sector.site
        c.site_gps = ref.has_valid_coords(site.latitude, site.longitude)

    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/cell_list.html', {
        'cells': cells,
        'page_obj': page_obj,
        'page_title': 'Cells',
        'summary': summary,
        'filters': filters,
        'has_any_cells': Cell.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': sorted(Cell.objects.exclude(technology='')
                                     .values_list('technology', flat=True).order_by().distinct()),
        'bands': FrequencyBand.objects.filter(cells__isnull=False).distinct().order_by('technology', 'band_number'),
        'match_states': [(k, ref.STATE_LABELS[k].title()) for k in (ref.MATCHABLE, ref.LIMITED, ref.INCOMPLETE)],
        'per_page': per_page,
        'page_sizes': CELL_PAGE_SIZES,
        'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index(),
        'last_item': page_obj.end_index(),
        'can_edit': _can_edit_reference(request.user),
        'map_url': reverse('drive_test:cell_map_data'),
    })


@login_required
def cell_map_data(request):
    """GeoJSON of cells (own coordinates only) and their parent sites for the current filters."""
    from .services import cell_reference as ref

    MAX_POINTS = 5000
    base, _ = _filtered_cells(request.GET)
    cells = list(base.filter(ref.Q_OWN_GPS).select_related('operator', 'sector__site', 'band')
                 .order_by('operator__name', 'cell_id')[:MAX_POINTS + 1])
    truncated = len(cells) > MAX_POINTS
    cells = cells[:MAX_POINTS]
    features, sites = [], {}
    for c in cells:
        band, chan = ref.frequency_label(c)
        ident = ref.primary_identifier(c)
        features.append({
            'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [c.longitude, c.latitude]},
            'properties': {
                'ftype': 'cell', 'id': c.pk, 'cell_id': c.cell_id, 'operator': c.operator.name,
                'technology': c.technology, 'site': c.sector.site.name, 'sector': c.sector.sector_id,
                'identifier': f'{ident[0]} {ident[1]}' if ident else '', 'band': band or chan,
                'active': c.is_active, 'url': reverse('drive_test:cell_detail', args=[c.pk]),
            },
        })
        site = c.sector.site
        if site.pk not in sites and ref.has_valid_coords(site.latitude, site.longitude):
            sites[site.pk] = {
                'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [site.longitude, site.latitude]},
                'properties': {'ftype': 'site', 'id': site.pk, 'site_id': site.site_id, 'name': site.name,
                               'operator': site.operator.name, 'url': reverse('drive_test:site_detail', args=[site.pk])},
            }
    return JsonResponse({'type': 'FeatureCollection', 'truncated': truncated,
                         'features': features + list(sites.values())})


@login_required
def cell_detail(request, pk):
    from .models import CellHistory
    from .services import cell_reference as ref

    cell = get_object_or_404(
        Cell.objects.select_related('operator', 'band', 'sector__site__chiefdom__district__region'), pk=pk)
    site = cell.sector.site
    twin = Cell.objects.filter(operator=cell.operator).exclude(pk=cell.pk)
    dup = bool((cell.cgi and twin.filter(cgi=cell.cgi).exists()) or (cell.ecgi and twin.filter(ecgi=cell.ecgi).exists()))
    matched = Measurement.objects.filter(matched_cell=cell)
    last = matched.order_by('-captured_at').values_list('captured_at', flat=True).first()
    recent = (matched.values('drive_file__session__session_ref', 'drive_file__session__test_date')
              .annotate(n=Count('id')).order_by('-drive_file__session__test_date')[:5])
    state = ref.cell_state(cell)
    band, chan = ref.frequency_label(cell)
    identity = [(label, val) for label, val in (
        ('MCC', cell.mcc), ('MNC', cell.mnc), ('LAC', cell.lac), ('RAC', cell.rac), ('TAC', cell.tac),
        ('CI', cell.ci), ('ECI', cell.eci), ('NCI', cell.nci), ('CGI', cell.cgi), ('ECGI', cell.ecgi))
        if val not in (None, '')]
    radio = [(label, val) for label, val in (
        ('PCI', cell.pci), ('EARFCN', cell.earfcn), ('NR-ARFCN', cell.nrarfcn)) if val is not None]
    return render(request, 'drive_test/cell_detail.html', {
        'cell': cell, 'site': site,
        'state': state, 'state_label': ref.STATE_LABELS[state], 'state_help': ref.STATE_HELP[state],
        'warnings': ref.cell_warnings(cell, dup_ids=dup),
        'identity': identity, 'radio': radio, 'band_label': band, 'channel_label': chan,
        'own_gps': ref.has_valid_coords(cell.latitude, cell.longitude),
        'site_gps': ref.has_valid_coords(site.latitude, site.longitude),
        'matched_count': matched.count(), 'last_observed': last, 'recent_sessions': recent,
        'finding_count': Finding.objects.filter(cell=cell).count(),
        'history': CellHistory.objects.filter(cell=cell).select_related('changed_by').order_by('-changed_at')[:10],
        'can_edit': _can_edit_reference(request.user),
        'page_title': f'Cell {cell.cell_id}',
    })


def _cell_form_view(request, cell=None):
    from django.db import transaction
    from .forms import CellForm
    from .models import CellHistory
    from .services.cell_reference import snapshot

    was_active = cell.is_active if cell else None     # read before validation mutates the instance
    form = CellForm(request.POST or None, instance=cell)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            change = 'created' if cell is None else (
                'deactivated' if was_active and not saved.is_active else 'updated')
            CellHistory.objects.create(cell=saved, changed_by=request.user, change_type=change,
                                       snapshot=snapshot(saved))
        messages.success(request, f'Cell {saved.cell_id} {"updated" if cell else "created"}.')
        return redirect('drive_test:cell_detail', pk=saved.pk)
    return render(request, 'drive_test/cell_form.html', {
        'form': form, 'cell': cell,
        'basic_fields': [form[n] for n in ('cell_id', 'operator', 'technology', 'site_code', 'sector_code')],
        'page_title': f'Edit {cell.cell_id}' if cell else 'Add Cell',
    })


@regulatory_admin_required
def cell_create(request):
    return _cell_form_view(request)


@regulatory_admin_required
def cell_edit(request, pk):
    return _cell_form_view(request, get_object_or_404(Cell.objects.select_related('sector__site'), pk=pk))


# ---------------------------------------------------------------------------
# Sectors (Network Reference)
# ---------------------------------------------------------------------------

SECTOR_PAGE_SIZES = (25, 50, 100)


def _filtered_sectors(GET):
    """Apply the Sectors page filters server-side. Returns (queryset, filters dict)."""
    from django.db.models import Exists, OuterRef

    f = {k: GET.get(k, '').strip() for k in
         ('q', 'operator', 'site', 'technology', 'region', 'district', 'status')}
    qs = Sector.objects.all()
    if f['q']:
        qs = qs.filter(Q(sector_id__icontains=f['q']) | Q(site__site_id__icontains=f['q'])
                       | Q(site__name__icontains=f['q']))
    if f['operator']:
        qs = qs.filter(site__operator__code=f['operator'])
    if f['site']:
        qs = qs.filter(Q(site__site_id__icontains=f['site']) | Q(site__name__icontains=f['site']))
    if f['technology']:
        qs = qs.filter(Exists(Cell.objects.filter(sector=OuterRef('pk'), technology=f['technology'])))
    if _to_int(f['region']) is not None:
        qs = qs.filter(site__chiefdom__district__region_id=int(f['region']))
    if _to_int(f['district']) is not None:
        qs = qs.filter(site__chiefdom__district_id=int(f['district']))
    if f['status'] == 'active':
        qs = qs.filter(is_active=True)
    elif f['status'] == 'inactive':
        qs = qs.filter(is_active=False)
    return qs, f


def _valid_azimuth(v):
    return v is None or 0 <= v < 360


def _sector_warnings(sector, n_cells, n_foreign_cells=0):
    """Reference problems the database can actually verify for one sector."""
    w = []
    if not _valid_azimuth(sector.azimuth_deg):
        w.append('Invalid azimuth')
    if not n_cells:
        w.append('No cells')
    if n_foreign_cells:
        w.append(f'{n_foreign_cells} cell(s) belong to a different operator than the site')
    return w


@login_required
def sector_list(request):
    from django.db.models import F
    from reference.models import Operator
    from .services import cell_reference as ref

    base, filters = _filtered_sectors(request.GET)
    summary = base.aggregate(total=Count('id'), active=Count('id', filter=Q(is_active=True)),
                             sites=Count('site', distinct=True))
    summary['cells'] = Cell.objects.filter(sector__in=base.values('pk')).count()
    try:
        per_page = int(request.GET.get('per_page', SECTOR_PAGE_SIZES[0]))
    except ValueError:
        per_page = SECTOR_PAGE_SIZES[0]
    if per_page not in SECTOR_PAGE_SIZES:
        per_page = SECTOR_PAGE_SIZES[0]

    qs = (base.select_related('site__operator', 'site__chiefdom__district__region')
          .annotate(n_cells=Count('cells', distinct=True),
                    n_foreign=Count('cells', filter=~Q(cells__operator=F('site__operator')), distinct=True))
          .order_by('site__operator__name', 'site__site_id', 'sector_id'))
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    sectors = list(page_obj.object_list)
    techs = {}
    if sectors:
        rows = (Cell.objects.filter(sector_id__in=[s.pk for s in sectors]).exclude(technology='')
                .values_list('sector_id', 'technology').order_by().distinct())
        for sid, t in rows:
            techs.setdefault(sid, []).append(t)
    for s in sectors:
        s.technologies = sorted(techs.get(s.pk, []))
        s.warnings = _sector_warnings(s, s.n_cells, s.n_foreign)
        s.site_gps = ref.has_valid_coords(s.site.latitude, s.site.longitude)

    districts = District.objects.select_related('region').order_by('region__name', 'name')
    if _to_int(filters['region']) is not None:
        districts = districts.filter(region_id=int(filters['region']))
    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/sector_list.html', {
        'sectors': sectors, 'page_obj': page_obj, 'page_title': 'Sectors', 'summary': summary,
        'filters': filters, 'has_any_sectors': Sector.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'regions': Region.objects.order_by('name'), 'districts': districts,
        'technology_choices': sorted(Cell.objects.exclude(technology='')
                                     .values_list('technology', flat=True).order_by().distinct()),
        'per_page': per_page, 'page_sizes': SECTOR_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index(), 'last_item': page_obj.end_index(),
        'can_edit': _can_edit_reference(request.user),
        'map_url': reverse('drive_test:sector_map_data'),
    })


@login_required
def sector_map_data(request):
    """Parent-site locations of the filtered sectors. Sectors have no coordinates of their own,
    so every point is labelled as a site location."""
    from .services import cell_reference as ref

    base, _ = _filtered_sectors(request.GET)
    MAX_POINTS = 5000
    sites = (Site.objects.filter(sectors__in=base.values('pk')).select_related('operator')
             .annotate(n_sectors=Count('sectors', filter=Q(sectors__in=base.values('pk')), distinct=True))
             .order_by('operator__name', 'site_id').distinct())
    sites = [s for s in sites[:MAX_POINTS + 1]]
    truncated = len(sites) > MAX_POINTS
    features = [{
        'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [s.longitude, s.latitude]},
        'properties': {'id': s.pk, 'site_id': s.site_id, 'name': s.name, 'operator': s.operator.name,
                       'sectors': s.n_sectors, 'url': reverse('drive_test:site_detail', args=[s.pk])},
    } for s in sites[:MAX_POINTS] if ref.has_valid_coords(s.latitude, s.longitude)]
    return JsonResponse({'type': 'FeatureCollection', 'truncated': truncated, 'features': features})


@login_required
def sector_detail(request, pk):
    from django.db.models import F
    from .services import cell_reference as ref

    sector = get_object_or_404(
        Sector.objects.select_related('site__operator', 'site__chiefdom__district__region'), pk=pk)
    site = sector.site
    cells = list(sector.cells.select_related('band').order_by('technology', 'cell_id'))
    foreign = sum(1 for c in cells if c.operator_id != site.operator_id)
    for c in cells:
        c.identifier = ref.primary_identifier(c)
        c.band_label, c.channel_label = ref.frequency_label(c)
        c.state_label = ref.STATE_LABELS[ref.cell_state(c)]
        c.state = ref.cell_state(c)
    matched = Measurement.objects.filter(matched_cell__sector=sector)
    return render(request, 'drive_test/sector_detail.html', {
        'sector': sector, 'site': site, 'cells': cells,
        'technologies': sorted({c.technology for c in cells if c.technology}),
        'warnings': _sector_warnings(sector, len(cells), foreign),
        'site_gps': ref.has_valid_coords(site.latitude, site.longitude),
        'matched_count': matched.count(),
        'last_observed': matched.order_by('-captured_at').values_list('captured_at', flat=True).first(),
        'finding_count': Finding.objects.filter(cell__sector=sector).count(),
        'recent_sessions': (matched.values('drive_file__session__session_ref', 'drive_file__session__test_date')
                            .annotate(n=Count('id')).order_by('-drive_file__session__test_date')[:5]),
        'can_edit': _can_edit_reference(request.user),
        'page_title': f'Sector {sector.sector_id}',
    })


def _sector_snapshot(sector):
    return {
        'site': sector.site.site_id if sector.site_id else None, 'azimuth_deg': sector.azimuth_deg,
        'height_m': sector.height_m, 'tilt_deg': sector.tilt_deg,
        'electrical_tilt_deg': sector.electrical_tilt_deg, 'is_active': sector.is_active,
    }


def _sector_form_view(request, sector=None):
    from .forms import SectorForm
    from .services import audit as al

    before = _sector_snapshot(sector) if sector else None
    form = SectorForm(request.POST or None, instance=sector)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        if before is None:
            al.log(request=request, action='CREATE', obj=saved,
                   description=f'Sector created: {saved.sector_id} on {saved.site.site_id}',
                   fields=_sector_snapshot(saved))
        else:
            changes = al.changes_dict(before, _sector_snapshot(saved))
            if changes:
                al.log(request=request, action='UPDATE', obj=saved,
                       description=f'Sector updated: {saved.sector_id} on {saved.site.site_id}', changes=changes)
        messages.success(request, f'Sector {saved.sector_id} {"updated" if sector else "created"}.')
        return redirect('drive_test:sector_detail', pk=saved.pk)
    return render(request, 'drive_test/sector_form.html', {
        'form': form, 'sector': sector,
        'page_title': f'Edit {sector.sector_id}' if sector else 'Add Sector',
    })


@regulatory_admin_required
def sector_create(request):
    return _sector_form_view(request)


@regulatory_admin_required
def sector_edit(request, pk):
    return _sector_form_view(request, get_object_or_404(Sector.objects.select_related('site__operator'), pk=pk))


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

FINDING_PAGE_SIZES = (25, 50, 100)
_FINDING_KIND_DATA_QUALITY = {'ANOMALY'}          # the model itself labels this type "Data Anomaly"


def _filtered_findings(GET):
    """Server-side Findings filters. Returns (queryset, filters dict)."""
    from django.core.exceptions import ValidationError

    f = {k: GET.get(k, '').strip() for k in
         ('q', 'operator', 'technology', 'severity', 'category', 'status', 'session', 'date_from', 'date_to')}
    qs = Finding.objects.all()
    if f['q']:
        q = (Q(description__icontains=f['q']) | Q(session__session_ref__icontains=f['q'])
             | Q(measurement__drive_file__original_filename__icontains=f['q'])
             | Q(cell__cell_id__icontains=f['q']) | Q(cell__sector__sector_id__icontains=f['q'])
             | Q(cell__sector__site__site_id__icontains=f['q']) | Q(cell__sector__site__name__icontains=f['q'])
             | Q(measurement__matched_cell__cell_id__icontains=f['q']))
        types = [k for k, label in Finding.FindingType.choices if f['q'].lower() in label.lower()]
        if types:
            q |= Q(finding_type__in=types)
        qs = qs.filter(q)
    if f['operator']:
        qs = qs.filter(session__operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(Q(measurement__radio__technology=f['technology']) | Q(cell__technology=f['technology']))
    if f['severity'] in Finding.Severity.values:
        qs = qs.filter(severity=f['severity'])
    if f['category'] in Finding.FindingType.values:
        qs = qs.filter(finding_type=f['category'])
    if f['status'] == 'open':
        qs = qs.filter(is_resolved=False)
    elif f['status'] == 'resolved':
        qs = qs.filter(is_resolved=True)
    if f['session']:
        qs = qs.filter(session__session_ref__icontains=f['session'])
    try:
        if f['date_from']:
            qs = qs.filter(session__test_date__gte=f['date_from'])
        if f['date_to']:
            qs = qs.filter(session__test_date__lte=f['date_to'])
    except (ValueError, ValidationError):
        f['date_from'] = f['date_to'] = ''
        qs = Finding.objects.none()
    return qs, f


def _finding_point(f):
    """The finding's own coordinates, else its measurement's. Never (0, 0), never guessed."""
    from .services.cell_reference import has_valid_coords
    m = f.measurement
    for lat, lon in ((f.latitude, f.longitude), (m.latitude, m.longitude) if m else (None, None)):
        if has_valid_coords(lat, lon):
            return lat, lon
    return None, None


def _finding_cell(f):
    """The cell recorded on the finding, else the one its measurement was matched to."""
    if f.cell_id:
        return f.cell
    return f.measurement.matched_cell if f.measurement and f.measurement.matched_cell_id else None


_FINDING_SELECT = ('session__operator', 'cell__sector__site', 'measurement__radio', 'measurement__drive_file',
                   'measurement__matched_cell__sector__site')


@login_required
def finding_list(request):
    from reference.models import Operator

    base, filters = _filtered_findings(request.GET)
    summary = base.aggregate(
        total=Count('id'),
        critical=Count('id', filter=Q(severity='CRITICAL')),
        high=Count('id', filter=Q(severity='HIGH')),
        open=Count('id', filter=Q(is_resolved=False)),
    )
    try:
        per_page = int(request.GET.get('per_page', FINDING_PAGE_SIZES[0]))
    except ValueError:
        per_page = FINDING_PAGE_SIZES[0]
    if per_page not in FINDING_PAGE_SIZES:
        per_page = FINDING_PAGE_SIZES[0]

    qs = base.select_related(*_FINDING_SELECT).order_by('-created_at', '-pk')
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    findings = list(page_obj.object_list)
    for f in findings:
        m = f.measurement
        radio = getattr(m, 'radio', None) if m else None
        f.tech = (radio.technology if radio and radio.technology else '') or (f.cell.technology if f.cell_id else '')
        f.point = _finding_point(f)
        f.eff_cell = _finding_cell(f)
        f.site = f.eff_cell.sector.site if f.eff_cell else None
        f.is_quality = f.finding_type in _FINDING_KIND_DATA_QUALITY

    keep = request.GET.copy()
    keep.pop('page', None)
    pending = DriveTestSession.objects.filter(status__in=('UPLOADING', 'PENDING', 'PROCESSING')).count()
    active_rules = RegulatoryRule.objects.filter(is_active=True).count()
    techs = set(Cell.objects.exclude(technology='').values_list('technology', flat=True).order_by().distinct())
    from .models import RadioMeasurement
    techs |= set(RadioMeasurement.objects.filter(measurement__findings__isnull=False).exclude(technology='')
                 .values_list('technology', flat=True).order_by().distinct())
    present_types = set(Finding.objects.values_list('finding_type', flat=True).order_by().distinct())
    return render(request, 'drive_test/finding_list.html', {
        'findings': findings, 'page_obj': page_obj, 'page_title': 'Findings', 'summary': summary,
        'filters': filters, 'filters_active': any(filters.values()),
        'has_any_findings': Finding.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': sorted(techs),
        'severities': Finding.Severity.choices,
        'categories': [(k, label) for k, label in Finding.FindingType.choices if k in present_types],
        'pending_sessions': pending, 'active_rules': active_rules,
        'per_page': per_page, 'page_sizes': FINDING_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index(), 'last_item': page_obj.end_index(),
        'map_url': reverse('drive_test:finding_map_data'),
        'export_url': reverse('drive_test:finding_export'),
        'export_qs': keep.urlencode(),
    })


@login_required
def finding_map_data(request):
    """GeoJSON of the filtered findings that have a real location."""
    MAX_POINTS = 5000
    base, _ = _filtered_findings(request.GET)
    rows = list(base.select_related('measurement', 'session').order_by('-created_at', '-pk')[:MAX_POINTS * 2])
    features = []
    for f in rows:
        lat, lon = _finding_point(f)
        if lat is None:
            continue
        features.append({
            'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {'id': f.pk, 'severity': f.severity, 'category': f.get_finding_type_display(),
                           'session': f.session.session_ref, 'resolved': f.is_resolved,
                           'description': f.description[:160],
                           'url': reverse('drive_test:finding_detail', args=[f.pk])},
        })
        if len(features) >= MAX_POINTS:
            break
    return JsonResponse({'type': 'FeatureCollection', 'truncated': len(features) >= MAX_POINTS, 'features': features})


@login_required
def finding_export(request):
    """Excel export of the currently filtered findings (same conventions as the session report)."""
    import io
    from django.http import HttpResponse
    try:
        import openpyxl
    except ImportError:
        return HttpResponse('openpyxl is not installed.', status=500)
    LIMIT = 20000
    base, _ = _filtered_findings(request.GET)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Findings'
    ws.append(['ID', 'Category', 'Severity', 'Status', 'Description', 'Operator', 'Technology', 'Session',
               'Cell', 'Sector', 'Site', 'Latitude', 'Longitude', 'Measured Value', 'Threshold Value',
               'Rule', 'Created'])
    for f in base.select_related(*_FINDING_SELECT, 'threshold__rule').order_by('-created_at', '-pk')[:LIMIT]:
        m = f.measurement
        radio = getattr(m, 'radio', None) if m else None
        cell = _finding_cell(f)
        lat, lon = _finding_point(f)
        ws.append([f.pk, f.get_finding_type_display(), f.severity, 'Resolved' if f.is_resolved else 'Open',
                   f.description, f.session.operator.name,
                   (radio.technology if radio and radio.technology else '') or (f.cell.technology if f.cell_id else ''),
                   f.session.session_ref, cell.cell_id if cell else '',
                   cell.sector.sector_id if cell else '', cell.sector.site.name if cell else '',
                   lat, lon, f.measured_value, f.threshold_value,
                   f.threshold.rule.rule_code if f.threshold_id else '', f.created_at.strftime('%Y-%m-%d %H:%M')])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="drive_test_findings.xlsx"'
    return resp


@login_required
def finding_detail(request, pk):
    from django.utils import timezone as tz
    from .services.cell_reference import has_valid_coords

    f = get_object_or_404(
        Finding.objects.select_related(*_FINDING_SELECT, 'threshold__rule', 'threshold__operator', 'resolved_by'), pk=pk)
    m = f.measurement
    radio = getattr(m, 'radio', None) if m else None
    cell = _finding_cell(f)
    sector = cell.sector if cell else None
    site = sector.site if sector else None
    lat, lon = _finding_point(f)
    radio_values = []
    if radio:
        for name, label, unit in (('rssi', 'RSSI', 'dBm'), ('rscp', 'RSCP', 'dBm'), ('ecio', 'Ec/Io', 'dB'),
                                  ('rsrp', 'RSRP', 'dBm'), ('rsrq', 'RSRQ', 'dB'), ('sinr', 'SINR', 'dB'),
                                  ('cqi', 'CQI', ''), ('ss_rsrp', 'SS-RSRP', 'dBm'), ('ss_rsrq', 'SS-RSRQ', 'dB'),
                                  ('ss_sinr', 'SS-SINR', 'dB'), ('dl_throughput_kbps', 'DL throughput', 'kbps'),
                                  ('ul_throughput_kbps', 'UL throughput', 'kbps')):
            v = getattr(radio, name)
            if v is not None:
                radio_values.append((label, v, unit))
    services = list(m.services.all().order_by('pk')[:20]) if m else []
    th = f.threshold
    today = tz.now().date()
    rule_current = None
    if th:
        rule_current = (th.effective_from <= today and (th.effective_to is None or th.effective_to >= today)
                        and f.threshold.rule.is_active)
    return render(request, 'drive_test/finding_detail.html', {
        'f': f, 'm': m, 'radio': radio, 'radio_values': radio_values, 'services': services,
        'cell': cell, 'sector': sector, 'site': site, 'operator': f.session.operator,
        'site_gps': bool(site) and has_valid_coords(site.latitude, site.longitude),
        'lat': lat, 'lon': lon, 'technology': (radio.technology if radio and radio.technology else '')
        or (f.cell.technology if f.cell_id else ''),
        'source_file': m.drive_file if m else None,
        'threshold': th, 'rule': th.rule if th else None, 'rule_current': rule_current,
        'is_quality': f.finding_type in _FINDING_KIND_DATA_QUALITY,
        'can_manage': _can_manage_findings(request.user),
        'page_title': f'Finding #{f.pk}',
    })


def _can_manage_findings(user):
    return user.is_active and (user.is_superuser or user.is_staff or getattr(user, 'is_analyst', False)
                               or getattr(user, 'is_regulatory_admin', False))


@login_required
@require_POST
def finding_set_status(request, pk):
    """Resolve / reopen a finding. Only the resolution state and notes can change; severity,
    category, evidence and lineage are never editable."""
    if not _can_manage_findings(request.user):
        return redirect('drive_test:finding_detail', pk=pk)
    f = get_object_or_404(Finding, pk=pk)
    action = request.POST.get('action')
    note = request.POST.get('note', '').strip()[:2000]
    if action == 'resolve' and not f.is_resolved:
        f.is_resolved, f.resolved_at, f.resolved_by = True, timezone.now(), request.user
        stamp = f'[{timezone.now():%Y-%m-%d %H:%M} {request.user.username}] Resolved'
    elif action == 'reopen' and f.is_resolved:
        f.is_resolved, f.resolved_at, f.resolved_by = False, None, None
        stamp = f'[{timezone.now():%Y-%m-%d %H:%M} {request.user.username}] Reopened'
    else:
        messages.error(request, 'That status change is not valid for this finding.')
        return redirect('drive_test:finding_detail', pk=pk)
    entry = stamp + (f': {note}' if note else '')
    f.notes = f'{f.notes}\n{entry}'.strip()
    f.save(update_fields=['is_resolved', 'resolved_at', 'resolved_by', 'notes', 'updated_at'])
    from .services import audit as al
    al.log(request=request, action='UPDATE', obj=f,
           description=f'Finding #{f.pk} {"resolved" if f.is_resolved else "reopened"}: {f.get_finding_type_display()}',
           changes={'is_resolved': {'from': not f.is_resolved, 'to': f.is_resolved}}, note=note)
    messages.success(request, f'Finding #{f.pk} {"resolved" if f.is_resolved else "reopened"}.')
    return redirect('drive_test:finding_detail', pk=pk)


# ---------------------------------------------------------------------------
# Measurements JSON endpoint (paginated, for Measurements tab)
# ---------------------------------------------------------------------------

@login_required
def session_measurements(request, session_ref):
    """Return paginated measurements for a session as JSON."""
    from .models import Measurement

    session = get_object_or_404(DriveTestSession, session_ref=session_ref)

    qs = (
        Measurement.objects
        .filter(drive_file__session=session, is_valid=True)
        .select_related('radio', 'matched_cell__sector__site')
        .order_by('drive_file', 'sequence_num')
    )

    tech = request.GET.get('tech', '')
    if tech:
        qs = qs.filter(radio__technology=tech)
    cell = request.GET.get('cell', '').strip()
    if cell:
        qs = qs.filter(matched_cell__cell_id__icontains=cell)
    for key, lookup in (('time_from', 'captured_at__time__gte'), ('time_to', 'captured_at__time__lte')):
        raw = request.GET.get(key, '').strip()
        if raw:
            try:
                fmt = '%H:%M:%S' if raw.count(':') == 2 else '%H:%M'
                qs = qs.filter(**{lookup: datetime.strptime(raw, fmt).time()})
            except ValueError:
                pass

    try:
        per_page = int(request.GET.get('per_page', 50))
    except ValueError:
        per_page = 50
    if per_page not in (50, 100, 250):
        per_page = 50
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get('page', 1))

    rows = []
    for m in page:
        radio = getattr(m, 'radio', None)
        mcell = m.matched_cell
        site = (mcell.sector.site
                if mcell and mcell.sector_id and mcell.sector and mcell.sector.site_id
                else None) if mcell else None
        rows.append({
            'seq': m.sequence_num,
            'ts': m.captured_at.strftime('%H:%M:%S') if m.captured_at else '',
            'lat': round(m.latitude, 6) if m.latitude else None,
            'lon': round(m.longitude, 6) if m.longitude else None,
            'tech': radio.technology if radio else '',
            'rssi': radio.rssi if radio else None,
            'rsrp': radio.rsrp if radio else None,
            'rsrq': radio.rsrq if radio else None,
            'sinr': radio.sinr if radio else None,
            'arfcn': m.obs_earfcn,
            'lac': m.obs_lac,
            'ci': m.obs_ci,
            'cell_id': mcell.cell_id if mcell else '',
            'site': site.name if site else '',
            'match': 'matched' if mcell else 'unmatched',
            'speed': round(m.speed_kmh, 1) if m.speed_kmh is not None else None,
        })

    return JsonResponse({
        'rows': rows,
        'page': page.number,
        'num_pages': paginator.num_pages,
        'total': paginator.count,
        'has_next': page.has_next(),
        'has_prev': page.has_previous(),
        'per_page': per_page,
        'start': page.start_index() if paginator.count else 0,
        'end': page.end_index() if paginator.count else 0,
    })


# ---------------------------------------------------------------------------
# Findings endpoint (evidence + lineage per finding)
# ---------------------------------------------------------------------------

@login_required
def session_findings(request, session_ref):
    """Return paginated findings for a session with the evidence needed to trace each one."""
    session = get_object_or_404(DriveTestSession.objects.select_related('operator', 'region'),
                                session_ref=session_ref)
    qs = (session.findings
          .select_related('measurement__radio', 'cell__sector__site')
          .order_by('-created_at', '-pk'))
    try:
        per_page = min(max(int(request.GET.get('per_page', 25)), 5), 100)
    except ValueError:
        per_page = 25
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get('page', 1))

    rows = []
    for f in page:
        m = f.measurement
        radio = getattr(m, 'radio', None) if m else None
        cell = f.cell
        sector = cell.sector if cell and cell.sector_id else None
        site = sector.site if sector and sector.site_id else None
        lat = f.latitude if f.latitude is not None else (m.latitude if m else None)
        lon = f.longitude if f.longitude is not None else (m.longitude if m else None)
        rows.append({
            'id': f.pk,
            'severity': f.severity,
            'severity_label': f.get_severity_display(),
            'category': f.get_finding_type_display(),
            'description': f.description,
            'technology': (radio.technology if radio and radio.technology else '') or (cell.technology if cell else ''),
            'lat': round(lat, 6) if lat is not None else None,
            'lon': round(lon, 6) if lon is not None else None,
            'region': session.region.name if session.region_id else '',
            'resolved': f.is_resolved,
            'created': f.created_at.strftime('%d %b %Y'),
            'created_full': f.created_at.strftime('%d %b %Y %H:%M'),
            'measured_value': f.measured_value,
            'threshold_value': f.threshold_value,
            'notes': f.notes,
            'operator': session.operator.name,
            'measurement_seq': m.sequence_num if m else None,
            'measurement_time': m.captured_at.strftime('%d %b %Y %H:%M:%S') if m and m.captured_at else '',
            'cell': cell.cell_id if cell else '',
            'sector': sector.sector_id if sector else '',
            'site': site.name if site else '',
            'site_code': site.site_id if site else '',
        })
    return JsonResponse({
        'rows': rows, 'page': page.number, 'num_pages': paginator.num_pages,
        'total': paginator.count, 'has_next': page.has_next(), 'has_prev': page.has_previous(),
        'start': page.start_index() if paginator.count else 0,
        'end': page.end_index() if paginator.count else 0,
    })


# ---------------------------------------------------------------------------
# Map data endpoint
# ---------------------------------------------------------------------------

@login_required
def session_map_data(request, session_ref):
    """
    Return GeoJSON FeatureCollection for a session's drive route.

    Feature types:
      - 'measurement'  — one per GPS-valid measurement; signal-coded
      - 'cell_tower'   — one per unique matched cell with known coordinates
      - 'finding'      — regulatory findings with location
      - 'handover'     — handover events between cells

    Also includes a top-level 'route_summary' with signal statistics.
    """
    from .models import Measurement

    session = get_object_or_404(DriveTestSession, session_ref=session_ref)

    qs = (
        Measurement.objects
        .filter(
            drive_file__session=session,
            is_valid=True,
            latitude__isnull=False,
            longitude__isnull=False,
        )
        .exclude(latitude=0.0, longitude=0.0)
        .select_related('radio', 'matched_cell__sector__site')
        .order_by('captured_at', 'drive_file', 'sequence_num')
    )

    features = []
    seen_cells = {}
    signal_values = {'rsrp': [], 'rsrq': [], 'sinr': [], 'rssi': []}
    tech_counts = {}
    prev_cell_id = None
    prev_cell_str = ''

    for m in qs:
        radio = getattr(m, 'radio', None)
        rsrp = radio.rsrp if radio else None
        rsrq = radio.rsrq if radio else None
        sinr = radio.sinr if radio else None
        rssi = radio.rssi if radio else None
        tech = radio.technology if radio else ''
        dl_tp = radio.dl_throughput_kbps if radio else None
        ul_tp = radio.ul_throughput_kbps if radio else None

        if rsrp is not None:
            signal_values['rsrp'].append(rsrp)
        if rsrq is not None:
            signal_values['rsrq'].append(rsrq)
        if sinr is not None:
            signal_values['sinr'].append(sinr)
        if rssi is not None:
            signal_values['rssi'].append(rssi)
        if tech:
            tech_counts[tech] = tech_counts.get(tech, 0) + 1

        signal_val = rsrp if rsrp is not None else rssi
        if signal_val is None:
            colour = 'grey'
            level = 0
        elif signal_val >= -85:
            colour = '#22c55e'
            level = 4
        elif signal_val >= -95:
            colour = '#eab308'
            level = 3
        elif signal_val >= -105:
            colour = '#f97316'
            level = 2
        elif signal_val >= -115:
            colour = '#ef4444'
            level = 1
        else:
            colour = '#991b1b'
            level = 0

        cell = m.matched_cell
        site = None
        if cell and cell.sector_id:
            try:
                site = cell.sector.site
            except Exception:
                pass

        cell_id_str = cell.cell_id if cell else ''
        ts_epoch = int(m.captured_at.timestamp()) if m.captured_at else 0

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [
                round(m.longitude, 6), round(m.latitude, 6)]},
            'properties': {
                'ftype': 'measurement',
                'seq': m.sequence_num,
                'ts': m.captured_at.strftime('%d %b %Y %H:%M:%S') if m.captured_at else '',
                'epoch': ts_epoch,
                'tech': tech,
                'rssi': rssi,
                'rsrp': rsrp,
                'rsrq': rsrq,
                'sinr': sinr,
                'dl_tp': round(dl_tp, 1) if dl_tp is not None else None,
                'ul_tp': round(ul_tp, 1) if ul_tp is not None else None,
                'colour': colour,
                'level': level,
                'cell_id': cell_id_str,
                'site_name': site.name if site else '',
                'speed': round(m.speed_kmh) if m.speed_kmh is not None else None,
            },
        })

        if cell and cell.pk not in seen_cells:
            tower_site = cell.sector.site if (cell.sector_id and cell.sector.site_id) else None
            clat = getattr(tower_site, 'latitude', None) or cell.latitude
            clon = getattr(tower_site, 'longitude', None) or cell.longitude
            if clat and clon:
                seen_cells[cell.pk] = True
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [clon, clat]},
                    'properties': {
                        'ftype': 'cell_tower',
                        'cell_id': cell.cell_id,
                        'technology': cell.technology,
                        'site_name': tower_site.name if tower_site else '',
                        'azimuth': cell.sector.azimuth_deg if cell.sector_id else None,
                    },
                })

        curr_cell = cell.pk if cell else None
        if prev_cell_id and curr_cell and curr_cell != prev_cell_id:
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [
                    round(m.longitude, 6), round(m.latitude, 6)]},
                'properties': {
                    'ftype': 'handover',
                    'ts': m.captured_at.strftime('%H:%M:%S') if m.captured_at else '',
                    'epoch': ts_epoch,
                    'from_cell': prev_cell_str,
                    'to_cell': cell_id_str,
                    'tech': tech,
                },
            })
        prev_cell_id = curr_cell
        prev_cell_str = cell_id_str

    for f in session.findings.select_related('measurement').order_by('-created_at')[:500]:
        lat = f.latitude if f.latitude is not None else (f.measurement.latitude if f.measurement else None)
        lon = f.longitude if f.longitude is not None else (f.measurement.longitude if f.measurement else None)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'ftype': 'finding', 'id': f.pk, 'severity': f.severity,
                'category': f.get_finding_type_display(),
                'description': f.description[:200], 'resolved': f.is_resolved,
            },
        })

    def _stats(vals):
        if not vals:
            return None
        s = sorted(vals)
        n = len(s)
        # Downsample sorted values to max 1000 for CDF rendering
        if n > 1000:
            step = n / 1000
            sampled = [round(s[int(i * step)], 1) for i in range(1000)]
            sampled.append(round(s[-1], 1))
        else:
            sampled = [round(v, 1) for v in s]
        return {
            'count': n,
            'min': round(s[0], 1),
            'max': round(s[-1], 1),
            'mean': round(sum(s) / n, 1),
            'p5': round(s[int(n * 0.05)], 1),
            'p10': round(s[int(n * 0.10)], 1),
            'p50': round(s[int(n * 0.50)], 1),
            'p90': round(s[int(n * 0.90)], 1),
            'p95': round(s[int(n * 0.95)], 1),
            'values': sampled,
        }

    return JsonResponse({
        'type': 'FeatureCollection',
        'features': features,
        'route_summary': {
            'total_points': len([f for f in features if f['properties']['ftype'] == 'measurement']),
            'cell_towers': len(seen_cells),
            'technologies': tech_counts,
            'rsrp': _stats(signal_values['rsrp']),
            'rsrq': _stats(signal_values['rsrq']),
            'sinr': _stats(signal_values['sinr']),
            'rssi': _stats(signal_values['rssi']),
        },
    })


# ---------------------------------------------------------------------------
# Dashboard trend AJAX endpoint (measurement activity for period filter)
# ---------------------------------------------------------------------------

@login_required
def dashboard_trend(request):
    """Return measurement trend JSON for the selected period (days=7|30|90)."""
    import json as _json
    try:
        days = int(request.GET.get('days', 30))
        days = min(max(days, 7), 90)
    except (ValueError, TypeError):
        days = 30

    since = date.today() - timedelta(days=days)
    from django.db.models import Sum as _Sum
    trend_qs = (
        DriveTestSession.objects
        .filter(test_date__gte=since)
        .values('test_date')
        .annotate(measurements=_Sum('total_measurements'))
        .order_by('test_date')
    )
    data = [
        {'label': row['test_date'].strftime('%d %b'), 'measurements': row['measurements'] or 0}
        for row in trend_qs
    ]
    return JsonResponse({'trend': data})


# ---------------------------------------------------------------------------
# Dashboard map data endpoint (aggregated across recent sessions)
# ---------------------------------------------------------------------------

@login_required
def dashboard_map_data(request):
    """
    Return GeoJSON of measurement points from the 5 most recent completed sessions.
    Capped at 800 points per session to keep response fast.
    """
    from .models import Measurement

    recent_sessions = (
        DriveTestSession.objects
        .filter(status='COMPLETED')
        .order_by('-test_date')[:5]
    )
    session_ids = list(recent_sessions.values_list('id', flat=True))

    qs = (
        Measurement.objects
        .filter(
            drive_file__session_id__in=session_ids,
            is_valid=True,
            latitude__isnull=False,
            longitude__isnull=False,
        )
        .exclude(latitude=0.0, longitude=0.0)
        .select_related('radio', 'drive_file__session')
        .order_by('drive_file__session', 'sequence_num')[:4000]
    )

    features = []
    for m in qs:
        radio = getattr(m, 'radio', None)
        rssi = radio.rssi if radio else None
        rsrp = radio.rsrp if radio else None
        signal_val = rsrp if rsrp is not None else rssi
        if signal_val is None:
            colour = 'grey'
        elif signal_val >= -85:
            colour = 'green'
        elif signal_val >= -95:
            colour = 'yellow'
        elif signal_val >= -105:
            colour = 'orange'
        else:
            colour = 'red'

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [m.longitude, m.latitude]},
            'properties': {
                'ftype': 'measurement',
                'tech': radio.technology if radio else '',
                'rssi': rssi,
                'colour': colour,
            },
        })

    return JsonResponse({'type': 'FeatureCollection', 'features': features})


# ---------------------------------------------------------------------------
# Reference import (regulatory admin)
# ---------------------------------------------------------------------------

@regulatory_admin_required
def reference_import(request):
    """UI for importing sites/cells from CSV."""
    from reference.models import Operator
    if request.method == 'POST':
        return _handle_reference_import(request)
    return render(request, 'drive_test/reference_import.html', {
        'page_title': 'Import Reference Data',
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'entity': request.GET.get('entity', ''),
    })


def _handle_reference_import(request):
    from .services.importer import ReferenceImporter, parse_csv
    from reference.models import Operator

    all_operators = Operator.objects.filter(enabled=True).order_by('name')
    operator_id = request.POST.get('operator')
    entity = request.POST.get('entity', 'cells')
    update_coords = request.POST.get('update_coordinates') == 'on'
    csv_file = request.FILES.get('csv_file')

    results = []
    errors = []

    if not csv_file:
        errors.append('No CSV file selected.')
    else:
        try:
            operator = Operator.objects.get(pk=operator_id) if operator_id else None
            importer = ReferenceImporter(
                operator_id=operator.id if operator else 0,
                update_coordinates=update_coords,
            )
            rows = parse_csv(csv_file.read())
            if entity == 'sites':
                result = importer.import_sites(rows)
            elif entity == 'cells':
                result = importer.import_cells(rows)
            elif entity == 'regions':
                result = importer.import_regions(rows)
            elif entity == 'districts':
                result = importer.import_districts(rows)
            else:
                errors.append(f'Unknown entity: {entity}')
                result = None
            if result:
                results.append(result)
                from .services import audit as al
                al.log(request=request, action='UPLOAD', entity_type='ReferenceImport',
                       description=str(result), entity=result.entity, source=result.source,
                       created=result.created, updated=result.updated, skipped=result.skipped,
                       error_count=len(result.errors), operator=operator.code if operator else None)
        except Exception as exc:
            logger.exception('Reference import failed')
            errors.append(str(exc))

    return render(request, 'drive_test/reference_import.html', {
        'page_title': 'Import Reference Data',
        'operators': all_operators,
        'results': results,
        'errors': errors,
    })


# ---------------------------------------------------------------------------
# Measurement Explorer
# ---------------------------------------------------------------------------

MEASUREMENT_PAGE_SIZES = (50, 100, 250)

# Radio fields the explorer can filter on: (GET key, label, RadioMeasurement field, unit).
_SIGNAL_METRICS = (
    ('rssi', 'RSSI', 'rssi', 'dBm'), ('rscp', 'RSCP', 'rscp', 'dBm'), ('ecio', 'Ec/No', 'ecio', 'dB'),
    ('rsrp', 'RSRP', 'rsrp', 'dBm'), ('rsrq', 'RSRQ', 'rsrq', 'dB'), ('sinr', 'SINR', 'sinr', 'dB'),
    ('ss_rsrp', 'SS-RSRP', 'ss_rsrp', 'dBm'), ('ss_rsrq', 'SS-RSRQ', 'ss_rsrq', 'dB'),
    ('ss_sinr', 'SS-SINR', 'ss_sinr', 'dB'),
)
_SIGNAL_BY_KEY = {k: (label, field, unit) for k, label, field, unit in _SIGNAL_METRICS}
# Per technology: the main "signal" and "quality" metrics, in preference order.
_MAIN_SIGNAL = {'2G': ('rssi',), '3G': ('rscp', 'rssi'), '4G': ('rsrp', 'rssi'), '5G': ('ss_rsrp', 'rsrp')}
_MAIN_QUALITY = {'3G': ('ecio',), '4G': ('sinr', 'rsrq'), '5G': ('ss_sinr', 'ss_rsrq', 'sinr')}


def _first_metric(radio, keys):
    """('RSRP', -95.0, 'dBm') for the first stored metric in `keys`, else None."""
    if radio is None:
        return None
    for k in keys:
        label, field, unit = _SIGNAL_BY_KEY[k]
        v = getattr(radio, field)
        if v is not None:
            return label, v, unit
    return None


def _parse_when(raw, end=False):
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM'; a bare date as an upper bound means end of that day."""
    raw = raw.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            d = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == '%Y-%m-%d' and end:
            d = d + timedelta(days=1) - timedelta(seconds=1)
        return d
    return None


def _match_q(state):
    """Cell-matching states as the pipeline actually records them.

    MATCHED: a reference cell is linked. UNMATCHED: matching has run (the file completed) and found none.
    UNKNOWN: no cell and the file has not completed matching, so nothing can be concluded yet.
    """
    if state == 'matched':
        return Q(matched_cell__isnull=False)
    if state == 'unmatched':
        return Q(matched_cell__isnull=True, drive_file__status='COMPLETED')
    if state == 'unknown':
        return Q(matched_cell__isnull=True) & ~Q(drive_file__status='COMPLETED')
    return None


# Observed identifiers the matcher can use (strategies 1-3 in cell_matcher._match_single). GPS proximity is separate.
_MATCH_IDENT_Q = (
    (~Q(obs_mcc='') & ~Q(obs_mnc='') & Q(obs_lac__isnull=False, obs_ci__isnull=False))
    | (~Q(obs_mcc='') & ~Q(obs_mnc='') & Q(obs_eci__isnull=False))
    | Q(obs_pci__isnull=False, obs_earfcn__isnull=False)
)


def _filtered_measurements(GET):
    """Return (queryset, filters, errors). Invalid values are reported, never silently applied."""
    from .models import Measurement
    qs = Measurement.objects.all()
    errors = []
    f = {k: GET.get(k, '').strip() for k in (
        'q', 'session', 'operator', 'technology', 'cell', 'site', 'region', 'district',
        'date_from', 'date_to', 'match', 'metric', 'min', 'max', 'ident')}

    if f['q']:
        q = f['q']
        qs = qs.filter(
            Q(drive_file__session__session_ref__icontains=q) | Q(drive_file__original_filename__icontains=q)
            | Q(matched_cell__cell_id__icontains=q) | Q(matched_cell__sector__site__name__icontains=q)
            | Q(matched_cell__sector__site__site_id__icontains=q)
            | Q(drive_file__session__operator__name__icontains=q) | Q(radio__technology__iexact=q)
            | Q(match_method__icontains=q))
    if f['session']:
        qs = qs.filter(drive_file__session__session_ref__icontains=f['session'])
    if f['operator']:
        qs = qs.filter(drive_file__session__operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(radio__technology=f['technology'])
    if f['cell']:
        qs = qs.filter(matched_cell__cell_id__icontains=f['cell'])
    if f['site']:
        qs = qs.filter(Q(matched_cell__sector__site__site_id__icontains=f['site'])
                       | Q(matched_cell__sector__site__name__icontains=f['site']))
    for key, path in (('region', 'matched_cell__sector__site__chiefdom__district__region_id'),
                      ('district', 'matched_cell__sector__site__chiefdom__district_id')):
        if f[key]:
            if f[key].isdigit():
                qs = qs.filter(**{path: int(f[key])})
            else:
                errors.append('Unrecognised %s.' % key)
                qs = qs.none()
    for key, lookup, end in (('date_from', 'captured_at__gte', False), ('date_to', 'captured_at__lte', True)):
        if f[key]:
            d = _parse_when(f[key], end)
            if d is None:
                errors.append('Date/time must look like 2026-09-24 or 2026-09-24T12:30.')
                qs = qs.none()
            else:
                qs = qs.filter(**{lookup: d})
    if f['match']:
        mq = _match_q(f['match'])
        if mq is None:
            errors.append('Unrecognised cell-matching status.')
            qs = qs.none()
        else:
            qs = qs.filter(mq)
    if f['ident']:
        if f['ident'] in ('yes', 'no'):
            qs = qs.filter(_MATCH_IDENT_Q) if f['ident'] == 'yes' else qs.exclude(_MATCH_IDENT_Q)
        else:
            errors.append('Unrecognised identifier filter.')
            qs = qs.none()
    if f['metric'] or f['min'] or f['max']:
        spec = _SIGNAL_BY_KEY.get(f['metric'])
        if spec is None:
            errors.append('Choose a signal metric to apply a range.')
            qs = qs.none()
        else:
            field = 'radio__' + spec[1]
            qs = qs.filter(**{field + '__isnull': False})
            for key, lk in (('min', 'gte'), ('max', 'lte')):
                if f[key]:
                    try:
                        qs = qs.filter(**{field + '__' + lk: float(f[key])})
                    except ValueError:
                        errors.append('Signal range must be numeric.')
                        qs = qs.none()
    return qs, f, errors


def _observed_id(m):
    """What the file itself reported, kept apart from the reference cell."""
    parts = []
    if m.obs_eci is not None:
        parts.append('ECI %s' % m.obs_eci)
    elif m.obs_lac is not None and m.obs_ci is not None:
        parts.append('LAC %s / CI %s' % (m.obs_lac, m.obs_ci))
    elif m.obs_ci is not None:
        parts.append('CI %s' % m.obs_ci)
    if m.obs_pci is not None:
        parts.append('PCI %s' % m.obs_pci)
    return ' · '.join(parts)


def _match_state(m):
    if m.matched_cell_id and m.matched_cell is not None:
        return 'matched'
    return 'unmatched' if m.drive_file.status == 'COMPLETED' else 'unknown'


_MATCH_LABEL = {'matched': 'MATCHED', 'unmatched': 'UNMATCHED', 'unknown': 'UNKNOWN'}


@login_required
def measurement_list(request):
    from reference.models import Operator
    from .models import Measurement, RadioMeasurement, Region, District
    from .services.cell_reference import has_valid_coords

    base, filters, errors = _filtered_measurements(request.GET)
    try:
        per_page = int(request.GET.get('per_page', MEASUREMENT_PAGE_SIZES[0]))
    except ValueError:
        per_page = MEASUREMENT_PAGE_SIZES[0]
    if per_page not in MEASUREMENT_PAGE_SIZES:
        per_page = MEASUREMENT_PAGE_SIZES[0]

    load_error = False
    rows, page_obj = [], None
    summary = {'total': 0, 'matched': 0, 'unmatched': 0, 'unknown': 0, 'invalid': 0}
    has_signal = has_quality = has_point = False
    try:
        valid = Q(is_valid=True)
        summary = base.aggregate(
            total=Count('id', filter=valid),
            matched=Count('id', filter=valid & Q(matched_cell__isnull=False)),
            unmatched=Count('id', filter=valid & Q(matched_cell__isnull=True, drive_file__status='COMPLETED')),
            unknown=Count('id', filter=valid & Q(matched_cell__isnull=True) & ~Q(drive_file__status='COMPLETED')),
            invalid=Count('id', filter=Q(is_valid=False)),
        )
        qs = (base.filter(is_valid=True)
              .select_related('radio', 'drive_file__session__operator', 'matched_cell__sector__site')
              .order_by('captured_at', 'pk'))
        page_obj = Paginator(qs, per_page).get_page(request.GET.get('page', 1))
        rows = list(page_obj.object_list)
        for m in rows:
            radio = getattr(m, 'radio', None)
            m.tech = radio.technology if radio and radio.technology else ''
            m.signal = _first_metric(radio, _MAIN_SIGNAL.get(m.tech, tuple(_SIGNAL_BY_KEY)))
            m.quality = _first_metric(radio, _MAIN_QUALITY.get(m.tech, ()))
            m.observed = _observed_id(m)
            m.state = _match_state(m)
            m.state_label = _MATCH_LABEL[m.state]
            m.ref_site = m.matched_cell.sector.site if m.state == 'matched' else None
            m.has_point = has_valid_coords(m.latitude, m.longitude)
        has_signal = any(m.signal for m in rows)
        has_quality = any(m.quality for m in rows)
        has_point = any(m.has_point for m in rows)
    except Exception:
        logger.exception('Measurement Explorer query failed')
        load_error = True
        rows, page_obj = [], None

    metrics_present, techs = [], []
    try:
        counts = RadioMeasurement.objects.aggregate(**{k: Count(field) for k, _l, field, _u in _SIGNAL_METRICS})
        metrics_present = [(k, label) for k, label, _f, _u in _SIGNAL_METRICS if counts[k]]
        techs = sorted(RadioMeasurement.objects.exclude(technology='')
                       .values_list('technology', flat=True).order_by().distinct())
    except Exception:
        logger.exception('Measurement Explorer option lookup failed')

    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/measurement_list.html', {
        'page_title': 'Measurements', 'rows': rows, 'page_obj': page_obj, 'summary': summary,
        'filters': filters, 'errors': errors, 'filters_active': any(filters.values()),
        'load_error': load_error, 'has_any': True if load_error else Measurement.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'regions': Region.objects.order_by('name'),
        'districts': District.objects.select_related('region').order_by('name'),
        'technology_choices': techs, 'metrics': metrics_present,
        'match_choices': [('matched', 'Matched'), ('unmatched', 'Unmatched'), ('unknown', 'Unknown (not yet matched)')],
        'has_signal': has_signal, 'has_quality': has_quality, 'has_point': has_point,
        'per_page': per_page, 'page_sizes': MEASUREMENT_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


# (label, RadioMeasurement field, unit)
_RADIO_GROUPS = (
    ('Signal', (('RSSI', 'rssi', 'dBm'), ('RSCP', 'rscp', 'dBm'), ('Ec/No', 'ecio', 'dB'),
                ('RSRP', 'rsrp', 'dBm'), ('RSRQ', 'rsrq', 'dB'), ('SINR', 'sinr', 'dB'), ('CQI', 'cqi', ''),
                ('SS-RSRP', 'ss_rsrp', 'dBm'), ('SS-RSRQ', 'ss_rsrq', 'dB'), ('SS-SINR', 'ss_sinr', 'dB'))),
    ('Throughput', (('Downlink', 'dl_throughput_kbps', 'kbps'), ('Uplink', 'ul_throughput_kbps', 'kbps'))),
)
_OBSERVED_IDS = (('MCC', 'obs_mcc'), ('MNC', 'obs_mnc'), ('LAC', 'obs_lac'), ('CI', 'obs_ci'), ('TAC', 'obs_tac'),
                 ('ECI', 'obs_eci'), ('PCI', 'obs_pci'), ('EARFCN', 'obs_earfcn'), ('NR-ARFCN', 'obs_nrarfcn'))


@login_required
def measurement_detail(request, pk):
    from .models import Measurement
    from .services.cell_reference import frequency_label, has_valid_coords, primary_identifier

    m = get_object_or_404(
        Measurement.objects.select_related(
            'radio', 'drive_file__session__operator', 'drive_file__parser_profile', 'test_device',
            'matched_cell__sector__site__chiefdom__district__region', 'matched_cell__band'), pk=pk)
    f, session = m.drive_file, m.drive_file.session
    radio = getattr(m, 'radio', None)

    radio_groups = []
    if radio:
        for title, fields in _RADIO_GROUPS:
            vals = [(label, getattr(radio, fld), unit) for label, fld, unit in fields if getattr(radio, fld) is not None]
            if vals:
                radio_groups.append((title, vals))
        # Handover counters default to 0, so a zero is only meaningful when something was counted.
        if radio.handover_count:
            radio_groups.append(('Handover', [('Attempts', radio.handover_count, ''), ('Success', radio.ho_success_count, ''),
                                              ('Failure', radio.ho_failure_count, '')]))
    extra = []
    if radio and isinstance(radio.raw_data, dict):
        extra = [(str(k), v) for k, v in list(radio.raw_data.items())[:30] if isinstance(v, (str, int, float)) and v != '']
    observed = [(label, getattr(m, fld)) for label, fld in _OBSERVED_IDS if getattr(m, fld) not in (None, '')]

    cell = m.matched_cell if m.matched_cell_id else None
    site = cell.sector.site if cell else None
    band, chan = frequency_label(cell) if cell else ('', '')
    ident = primary_identifier(cell) if cell else None
    geo = []
    if site and site.chiefdom_id:
        c = site.chiefdom
        geo = [c.district.region.name, c.district.name, c.name]

    siblings = Measurement.objects.filter(drive_file=f, is_valid=True).order_by()
    prev_m = siblings.filter(sequence_num__lt=m.sequence_num).order_by('-sequence_num').only('pk', 'sequence_num').first()
    next_m = siblings.filter(sequence_num__gt=m.sequence_num).order_by('sequence_num').only('pk', 'sequence_num').first()

    state = _match_state(m)
    cell_pt = (cell.latitude, cell.longitude) if cell and has_valid_coords(cell.latitude, cell.longitude) else None
    site_pt = (site.latitude, site.longitude) if site and has_valid_coords(site.latitude, site.longitude) else None
    return render(request, 'drive_test/measurement_detail.html', {
        'page_title': 'Measurement', 'm': m, 'f': f, 'session': session, 'operator': session.operator, 'radio': radio,
        'technology': radio.technology if radio and radio.technology else '',
        'radio_groups': radio_groups, 'extra': extra, 'observed': observed,
        'services': list(m.services.all().order_by('pk')[:50]),
        'state': state, 'state_label': _MATCH_LABEL[state],
        'cell': cell, 'site': site, 'sector': cell.sector if cell else None, 'band': band, 'chan': chan,
        'ident': ident, 'geo': geo, 'prev_m': prev_m, 'next_m': next_m,
        'has_point': has_valid_coords(m.latitude, m.longitude), 'cell_pt': cell_pt, 'site_pt': site_pt,
        'flags': m.quality_flags if isinstance(m.quality_flags, list) else [],
    })


# ---------------------------------------------------------------------------
# GIS / Coverage
# ---------------------------------------------------------------------------

GIS_MAX_MEASUREMENTS = 20000     # points sent to the browser per request; more are evenly thinned
GIS_MAX_REFERENCE = 5000         # sites / cells / sectors / findings per request
GIS_ROUTE_GAP_SECONDS = 300      # a longer pause between fixes starts a new route segment

_GIS_LOCATED = (Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180)
                & ~Q(latitude=0.0, longitude=0.0))


def _gis_bbox(GET):
    """bbox=minLon,minLat,maxLon,maxLat -> tuple of floats, or None when absent/invalid."""
    raw = GET.get('bbox', '').strip()
    if not raw:
        return None
    try:
        a = [float(x) for x in raw.split(',')]
    except ValueError:
        return None
    if len(a) != 4 or a[0] >= a[2] or a[1] >= a[3]:
        return None
    return tuple(a)


def _gis_in_bbox(qs, bbox, lat='latitude', lon='longitude'):
    if not bbox:
        return qs
    return qs.filter(**{lon + '__gte': bbox[0], lat + '__gte': bbox[1], lon + '__lte': bbox[2], lat + '__lte': bbox[3]})


def _gis_reference_filters(f):
    """Operator/region/district/technology for reference layers. A selected session implies its operator."""
    op = f['operator']
    if not op and f['session']:
        s = DriveTestSession.objects.filter(session_ref=f['session']).select_related('operator').first()
        op = s.operator.code if s else ''
    return {'operator': op, 'technology': f['technology'],
            'region': int(f['region']) if f['region'].isdigit() else None,
            'district': int(f['district']) if f['district'].isdigit() else None}


def _gis_sites(f, bbox):
    from .models import Site
    r = _gis_reference_filters(f)
    qs = Site.objects.filter(_SITE_GPS)
    if r['operator']:
        qs = qs.filter(operator__code=r['operator'])
    if r['region']:
        qs = qs.filter(chiefdom__district__region_id=r['region'])
    if r['district']:
        qs = qs.filter(chiefdom__district_id=r['district'])
    if r['technology']:
        qs = qs.filter(sectors__cells__technology=r['technology']).distinct()
    return _gis_in_bbox(qs, bbox)


def _gis_cells(f, bbox):
    from .services import cell_reference as ref
    r = _gis_reference_filters(f)
    qs = Cell.objects.filter(ref.Q_OWN_GPS)
    if r['operator']:
        qs = qs.filter(operator__code=r['operator'])
    if r['technology']:
        qs = qs.filter(technology=r['technology'])
    if r['region']:
        qs = qs.filter(sector__site__chiefdom__district__region_id=r['region'])
    if r['district']:
        qs = qs.filter(sector__site__chiefdom__district_id=r['district'])
    return _gis_in_bbox(qs, bbox)


def _gis_sectors(f, bbox):
    r = _gis_reference_filters(f)
    qs = Sector.objects.filter(azimuth_deg__isnull=False, site__latitude__isnull=False, site__longitude__isnull=False)
    qs = qs.exclude(site__latitude=0.0, site__longitude=0.0)
    if r['operator']:
        qs = qs.filter(site__operator__code=r['operator'])
    if r['region']:
        qs = qs.filter(site__chiefdom__district__region_id=r['region'])
    if r['district']:
        qs = qs.filter(site__chiefdom__district_id=r['district'])
    if r['technology']:
        qs = qs.filter(cells__technology=r['technology']).distinct()
    return _gis_in_bbox(qs, bbox, 'site__latitude', 'site__longitude')


def _gis_findings(f, GET):
    """Findings within the same session/operator/technology/geography/time filters as the measurements."""
    qs = Finding.objects.all()
    if f['session']:
        qs = qs.filter(session__session_ref__icontains=f['session'])
    if f['operator']:
        qs = qs.filter(session__operator__code=f['operator'])
    if f['technology']:
        qs = qs.filter(Q(measurement__radio__technology=f['technology']) | Q(cell__technology=f['technology']))
    for key, path in (('region', 'sector__site__chiefdom__district__region_id'),
                      ('district', 'sector__site__chiefdom__district_id')):
        if f[key].isdigit():
            v = int(f[key])
            qs = qs.filter(Q(**{'cell__' + path: v}) | Q(**{'measurement__matched_cell__' + path: v}))
    for key, lookup, end in (('date_from', 'measurement__captured_at__gte', False), ('date_to', 'measurement__captured_at__lte', True)):
        if f[key]:
            d = _parse_when(f[key], end)
            if d:
                qs = qs.filter(**{lookup: d})
    return qs


def _gis_route(points):
    """points: iterable of (file_id, epoch_seconds, lon, lat) in chronological order per file.
    Segments are split by file and by pauses, so unrelated fixes are never joined."""
    segs, cur, last = [], [], None
    for file_id, ts, lon, lat in points:
        if last is not None and (file_id != last[0] or ts - last[1] > GIS_ROUTE_GAP_SECONDS):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        cur.append([round(lon, 6), round(lat, 6)])
        last = (file_id, ts)
    if len(cur) > 1:
        segs.append(cur)
    return segs


@login_required
def gis(request):
    from reference.models import Operator
    from .models import RadioMeasurement, Region, District

    _base, filters, errors = _filtered_measurements(request.GET)
    sessions = list(DriveTestSession.objects.filter(total_measurements__gt=0).select_related('operator')
                    .order_by('-test_date', '-created_at')[:100])
    if filters['session'] and not any(s.session_ref == filters['session'] for s in sessions):
        extra = DriveTestSession.objects.filter(session_ref=filters['session']).select_related('operator').first()
        if extra:
            sessions.insert(0, extra)
    counts = RadioMeasurement.objects.aggregate(**{k: Count(field) for k, _l, field, _u in _SIGNAL_METRICS})
    techs = sorted(RadioMeasurement.objects.exclude(technology='').values_list('technology', flat=True).order_by().distinct())
    config = {
        'dataUrl': reverse('drive_test:gis_data'),
        'tileUrl': getattr(settings, 'DRIVE_TEST_MAP_TILE_URL', ''),
        'attribution': getattr(settings, 'DRIVE_TEST_MAP_ATTRIBUTION', ''),
        'measurementUrl': reverse('drive_test:measurement_detail', args=[0]),
        'sessionUrl': reverse('drive_test:session_detail', args=['__REF__']),
        'metrics': {k: {'label': label, 'unit': unit} for k, label, _f, unit in _SIGNAL_METRICS},
        'defaultView': [8.46, -13.23, 8],
        'maxMeasurements': GIS_MAX_MEASUREMENTS,
    }
    selected = next((s for s in sessions if s.session_ref == filters['session']), None)
    return render(request, 'drive_test/gis.html', {
        'page_title': 'GIS / Coverage', 'filters': filters, 'errors': errors, 'config': config,
        'sessions': sessions, 'selected_session': selected,
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': techs,
        'metrics': [(k, label) for k, label, _f, _u in _SIGNAL_METRICS if counts[k]],
        'regions': Region.objects.order_by('name'),
        'districts': District.objects.select_related('region').order_by('name'),
        'filters_active': any(filters.values()),
    })


@login_required
def gis_data(request):
    """One GeoJSON layer per call: ?layer=measurements|sites|cells|sectors|findings|summary plus the page filters.

    Reference layers and measurements accept bbox=minLon,minLat,maxLon,maxLat so the browser only loads what is in view.
    """
    from .services import cell_reference as ref

    layer = request.GET.get('layer', '')
    base, f, errors = _filtered_measurements(request.GET)
    bbox = _gis_bbox(request.GET)
    try:
        if layer == 'measurements':
            valid = base.filter(is_valid=True).filter(_GIS_LOCATED)
            located = _gis_in_bbox(valid, bbox)
            total = located.count()
            step = max(1, -(-total // GIS_MAX_MEASUREMENTS))
            metric_fields = [fld for _k, _l, fld, _u in _SIGNAL_METRICS]
            rows = located.order_by('drive_file_id', 'captured_at', 'pk').values_list(
                'pk', 'drive_file_id', 'captured_at', 'latitude', 'longitude', 'matched_cell_id', 'matched_cell__cell_id',
                'match_method', 'match_confidence', 'drive_file__status', 'drive_file__session__session_ref',
                'drive_file__session__operator__name', 'radio__technology', *['radio__' + m for m in metric_fields])
            features, route_pts = [], []
            for i, r in enumerate(rows.iterator(chunk_size=5000)):
                if i % step:
                    continue
                (pk, fid, ts, lat, lon, cell_pk, cell_id, method, conf, fstatus, sess, opn, tech) = r[:13]
                vals = {k: v for k, v in zip([k for k, *_ in _SIGNAL_METRICS], r[13:]) if v is not None}
                state = 'MATCHED' if cell_pk else ('UNMATCHED' if fstatus == 'COMPLETED' else 'UNKNOWN')
                p = {'id': pk, 't': ts.strftime('%Y-%m-%d %H:%M:%S'), 'op': opn, 'sess': sess, 'match': state}
                if tech:
                    p['tech'] = tech
                if cell_pk:
                    p['cell'] = cell_id
                    p['cell_pk'] = cell_pk
                    if method:
                        p['method'] = method
                    if conf is not None:
                        p['conf'] = round(conf, 2)
                if vals:
                    p['m'] = vals
                features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]},
                                 'properties': p})
                route_pts.append((fid, ts.timestamp(), lon, lat))
            return JsonResponse({'type': 'FeatureCollection', 'features': features, 'route': _gis_route(route_pts),
                                 'total_located': total, 'shown': len(features), 'thinned': step > 1})

        if layer == 'sites':
            qs = _gis_sites(f, bbox).select_related('operator', 'chiefdom__district__region').order_by('operator__name', 'site_id')
            items = list(qs[:GIS_MAX_REFERENCE + 1])
            feats = []
            for s in items[:GIS_MAX_REFERENCE]:
                c = s.chiefdom
                feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [s.longitude, s.latitude]},
                              'properties': {'id': s.pk, 'site_id': s.site_id, 'name': s.name, 'op': s.operator.name,
                                             'region': c.district.region.name if c else '', 'district': c.district.name if c else '',
                                             'url': reverse('drive_test:site_detail', args=[s.pk])}})
            return JsonResponse({'type': 'FeatureCollection', 'features': feats, 'truncated': len(items) > GIS_MAX_REFERENCE})

        if layer == 'cells':
            items = list(_gis_cells(f, bbox).select_related('operator', 'sector__site', 'band')
                         .order_by('operator__name', 'cell_id')[:GIS_MAX_REFERENCE + 1])
            feats = []
            for c in items[:GIS_MAX_REFERENCE]:
                band, chan = ref.frequency_label(c)
                feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [c.longitude, c.latitude]},
                              'properties': {'id': c.pk, 'cell_id': c.cell_id, 'tech': c.technology, 'op': c.operator.name,
                                             'site': c.sector.site.name, 'sector': c.sector.sector_id, 'band': band or chan,
                                             'active': c.is_active, 'url': reverse('drive_test:cell_detail', args=[c.pk])}})
            return JsonResponse({'type': 'FeatureCollection', 'features': feats, 'truncated': len(items) > GIS_MAX_REFERENCE})

        if layer == 'sectors':
            items = list(_gis_sectors(f, bbox).select_related('site__operator').order_by('site__site_id', 'sector_id')
                         [:GIS_MAX_REFERENCE + 1])
            feats = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [s.site.longitude, s.site.latitude]},
                      'properties': {'id': s.pk, 'sector_id': s.sector_id, 'site': s.site.name, 'op': s.site.operator.name,
                                     'azimuth': s.azimuth_deg, 'height': s.height_m, 'active': s.is_active,
                                     'url': reverse('drive_test:sector_detail', args=[s.pk])}}
                     for s in items[:GIS_MAX_REFERENCE]]
            return JsonResponse({'type': 'FeatureCollection', 'features': feats, 'truncated': len(items) > GIS_MAX_REFERENCE})

        if layer == 'findings':
            rows = list(_gis_findings(f, request.GET).select_related('session__operator', 'measurement__radio', 'cell',
                                                                       'measurement__matched_cell__sector__site')
                        .order_by('-created_at', '-pk')[:GIS_MAX_REFERENCE * 2])
            feats = []
            for fd in rows:
                lat, lon = _finding_point(fd)
                if lat is None or (bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3])):
                    continue
                cell = _finding_cell(fd)
                radio = getattr(fd.measurement, 'radio', None) if fd.measurement else None
                feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                              'properties': {'id': fd.pk, 'severity': fd.severity, 'category': fd.get_finding_type_display(),
                                             'tech': (radio.technology if radio and radio.technology else '') or (fd.cell.technology if fd.cell_id else ''),
                                             'cell': cell.cell_id if cell else '', 'site': cell.sector.site.name if cell else '',
                                             'sess': fd.session.session_ref, 'resolved': fd.is_resolved,
                                             'url': reverse('drive_test:finding_detail', args=[fd.pk])}})
                if len(feats) >= GIS_MAX_REFERENCE:
                    break
            return JsonResponse({'type': 'FeatureCollection', 'features': feats, 'truncated': len(feats) >= GIS_MAX_REFERENCE})

        if layer == 'summary':
            valid = Q(is_valid=True)
            agg = base.aggregate(
                total=Count('id', filter=valid),
                mapped=Count('id', filter=valid & _GIS_LOCATED),
                matched=Count('id', filter=valid & Q(matched_cell__isnull=False)),
                unmatched=Count('id', filter=valid & Q(matched_cell__isnull=True, drive_file__status='COMPLETED')),
                unknown=Count('id', filter=valid & Q(matched_cell__isnull=True) & ~Q(drive_file__status='COMPLETED')),
                invalid=Count('id', filter=Q(is_valid=False)),
            )
            agg['without_location'] = agg['total'] - agg['mapped']
            agg['sites'] = _gis_sites(f, None).count()
            agg['cells'] = _gis_cells(f, None).count()
            agg['sectors'] = _gis_sectors(f, None).count()
            fq = _gis_findings(f, request.GET)
            agg['findings_total'] = fq.count()
            agg['findings_located'] = fq.filter(
                Q(latitude__isnull=False, longitude__isnull=False) & ~Q(latitude=0.0, longitude=0.0)
                | Q(measurement__isnull=False) & Q(measurement__latitude__isnull=False) & ~Q(measurement__latitude=0.0, measurement__longitude=0.0)
            ).count()
            agg['errors'] = errors
            return JsonResponse(agg)
    except Exception:
        logger.exception('GIS data request failed (layer=%s)', layer)
        return JsonResponse({'error': 'Unable to load map data. Please try again.'}, status=500)
    return JsonResponse({'error': 'Unknown layer.'}, status=400)


# ---------------------------------------------------------------------------
# Cell Matching workspace (read-only view of the existing matcher's results)
# ---------------------------------------------------------------------------

def _observed_identifiers(m):
    """Every stored observed identifier that has a value. Nothing is derived or inferred."""
    parts = []
    if m.obs_mcc and m.obs_mnc:
        parts.append('MCC-MNC %s-%s' % (m.obs_mcc, m.obs_mnc))
    for label, v in (('LAC', m.obs_lac), ('CI', m.obs_ci), ('TAC', m.obs_tac), ('ECI', m.obs_eci),
                     ('PCI', m.obs_pci), ('EARFCN', m.obs_earfcn), ('NR-ARFCN', m.obs_nrarfcn)):
        if v is not None:
            parts.append('%s %s' % (label, v))
    return parts


def _has_match_identifiers(m):
    """Same inputs as cell_matcher strategies 1-3 (cell identifiers, not GPS)."""
    sim = bool(m.obs_mcc and m.obs_mnc)
    return ((sim and m.obs_lac is not None and m.obs_ci is not None)
            or (sim and m.obs_eci is not None)
            or (m.obs_pci is not None and m.obs_earfcn is not None))


@login_required
def cell_matching(request):
    from reference.models import Operator
    from .models import Region, District, RadioMeasurement
    from .services.cell_reference import has_valid_coords

    get = request.GET.copy()
    status = get.get('status', '').strip().lower()          # ?status=UNMATCHED is the documented form
    if status:
        get['match'] = status
    base, filters, errors = _filtered_measurements(get)
    filters['status'] = filters['match']
    try:
        per_page = int(request.GET.get('per_page', MEASUREMENT_PAGE_SIZES[0]))
    except ValueError:
        per_page = MEASUREMENT_PAGE_SIZES[0]
    if per_page not in MEASUREMENT_PAGE_SIZES:
        per_page = MEASUREMENT_PAGE_SIZES[0]

    load_error = False
    rows, page_obj, methods = [], None, []
    summary = {'total': 0, 'matched': 0, 'unmatched': 0, 'unknown': 0, 'invalid': 0}
    try:
        valid = Q(is_valid=True)
        summary = base.aggregate(
            total=Count('id', filter=valid),
            matched=Count('id', filter=valid & Q(matched_cell__isnull=False)),
            unmatched=Count('id', filter=valid & Q(matched_cell__isnull=True, drive_file__status='COMPLETED')),
            unknown=Count('id', filter=valid & Q(matched_cell__isnull=True) & ~Q(drive_file__status='COMPLETED')),
            invalid=Count('id', filter=Q(is_valid=False)),
        )
        methods = list(base.filter(is_valid=True, matched_cell__isnull=False).order_by()
                       .values('match_method').annotate(n=Count('id')).order_by('-n'))
        qs = (base.filter(is_valid=True)
              .select_related('radio', 'drive_file__session__operator', 'matched_cell__operator',
                              'matched_cell__sector__site__chiefdom__district__region')
              .order_by('captured_at', 'pk'))
        page_obj = Paginator(qs, per_page).get_page(request.GET.get('page', 1))
        rows = list(page_obj.object_list)
        for m in rows:
            m.state = _match_state(m)
            m.state_label = _MATCH_LABEL[m.state]
            m.identifiers = _observed_identifiers(m)
            m.has_idents = _has_match_identifiers(m)
            m.has_point = has_valid_coords(m.latitude, m.longitude)
            c = m.matched_cell if m.state == 'matched' else None
            m.ref_cell, m.ref_sector = c, (c.sector if c else None)
            m.ref_site = c.sector.site if c else None
            ch = m.ref_site.chiefdom if m.ref_site and m.ref_site.chiefdom_id else None
            m.ref_area = ('%s › %s' % (ch.district.region.name, ch.district.name)) if ch else ''
    except Exception:
        logger.exception('Cell Matching query failed')
        load_error, rows, page_obj = True, [], None

    # Denominator: only measurements whose file has finished matching. UNKNOWN (pending) and invalid records are excluded.
    eligible = summary['matched'] + summary['unmatched']
    match_rate = round(summary['matched'] * 100.0 / eligible, 1) if eligible else None

    techs = sorted(RadioMeasurement.objects.exclude(technology='').values_list('technology', flat=True).order_by().distinct())
    sessions = list(DriveTestSession.objects.filter(total_measurements__gt=0).select_related('operator')
                    .order_by('-test_date', '-created_at')[:100])
    if filters['session'] and not any(s.session_ref == filters['session'] for s in sessions):
        extra = DriveTestSession.objects.filter(session_ref=filters['session']).select_related('operator').first()
        if extra:
            sessions.insert(0, extra)
    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/cell_matching.html', {
        'page_title': 'Cell Matching', 'rows': rows, 'page_obj': page_obj, 'summary': summary, 'methods': methods,
        'match_rate': match_rate, 'eligible': eligible, 'filters': filters, 'errors': errors,
        'filters_active': any(v for k, v in filters.items() if k != 'status'),
        'load_error': load_error, 'has_any': True if load_error else Measurement.objects.exists(),
        'sessions': sessions, 'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': techs, 'regions': Region.objects.order_by('name'),
        'districts': District.objects.select_related('region').order_by('name'),
        'status_choices': [('matched', 'MATCHED'), ('unmatched', 'UNMATCHED'), ('unknown', 'UNKNOWN')],
        'per_page': per_page, 'page_sizes': MEASUREMENT_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


# ---------------------------------------------------------------------------
# Data Quality workspace
# ---------------------------------------------------------------------------

DATA_QUALITY_PAGE_SIZES = (25, 50, 100)


@login_required
def data_quality(request):
    """Assess dataset completeness/reliability from the existing DataQualityResult and
    Measurement data. This page never runs new checks; it only rolls up and displays
    what services.analysis.QualityAssessor and the cell matcher already computed."""
    from reference.models import Operator
    from .services import data_quality as dq

    base, filters, errors = dq.filter_sessions(request.GET)
    try:
        per_page = int(request.GET.get('per_page', DATA_QUALITY_PAGE_SIZES[0]))
    except ValueError:
        per_page = DATA_QUALITY_PAGE_SIZES[0]
    if per_page not in DATA_QUALITY_PAGE_SIZES:
        per_page = DATA_QUALITY_PAGE_SIZES[0]

    load_error = False
    rows, page_obj = [], None
    summary = {'sessions_assessed': None, 'measurements_assessed': None, 'quality_issues': None,
               'invalid_records': None, 'unmatched_measurements': None}
    chart = {'valid': 0, 'invalid': 0, 'without_gps': 0, 'unmatched': 0}
    issues = []
    category = None
    try:
        annotated = dq.annotate_rollup(base).order_by('-test_date', '-created_at', '-pk')
        all_rows = dq.filter_rows_by_quality(dq.build_rows(annotated), filters['quality'])
        session_ids = [s.pk for s in all_rows]
        page_obj = Paginator(all_rows, per_page).get_page(request.GET.get('page', 1))
        rows = list(page_obj.object_list)
        if session_ids:
            summary = dq.workspace_summary(session_ids)
            chart = dq.measurement_quality_chart(session_ids)
            issues = dq.issue_rows(session_ids)
            category = dq.category_breakdown(session_ids)
    except Exception:
        logger.exception('Data Quality query failed')
        load_error = True

    from .models import RadioMeasurement
    techs = sorted(RadioMeasurement.objects.exclude(technology='').values_list('technology', flat=True).order_by().distinct())
    keep = request.GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/data_quality.html', {
        'page_title': 'Data Quality', 'rows': rows, 'page_obj': page_obj, 'summary': summary,
        'chart': chart, 'issues': issues, 'category': category, 'filters': filters, 'errors': errors,
        'filters_active': any(v for k, v in filters.items() if k != 'quality') or bool(filters['quality']),
        'load_error': load_error, 'has_any': True if load_error else DriveTestSession.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': techs,
        'quality_choices': [('good', 'Good'), ('warning', 'Warning'), ('poor', 'Poor'), ('pending', 'Pending')],
        'per_page': per_page, 'page_sizes': DATA_QUALITY_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


@login_required
def data_quality_session_detail(request, session_ref):
    """JSON detail for the Data Quality drawer: one session's category breakdown, issues and files."""
    from .services import data_quality as dq

    session = get_object_or_404(DriveTestSession.objects.select_related('operator'), session_ref=session_ref)
    detail = dq.session_detail(session)
    files = [{
        'filename': f.original_filename, 'status': f.status, 'status_label': f.get_status_display(),
        'parser': f.parser_profile.name if f.parser_profile_id else f.detected_format,
        'measurements': f.measurement_count,
        'quality': ({
            'total': f.quality_result.total_records, 'valid': f.quality_result.valid_records,
            'invalid': f.quality_result.invalid_records, 'missing_gps': f.quality_result.missing_gps,
            'missing_cell_id': f.quality_result.missing_cell_id, 'matched': f.quality_result.matched_cells,
            'unmatched': f.quality_result.unmatched_cells, 'match_rate_pct': f.quality_result.match_rate_pct,
            'overall_score': f.quality_result.overall_score, 'issues': f.quality_result.issues,
        } if hasattr(f, 'quality_result') else None),
    } for f in detail['files']]
    return JsonResponse({
        'session_ref': session.session_ref, 'operator': session.operator.name,
        'status': session.status, 'test_date': str(session.test_date),
        'files': files, 'category': detail['category'], 'issues': detail['issues'], 'chart': detail['chart'],
        'session_url': reverse('drive_test:session_detail', args=[session.session_ref]),
        'measurements_url': reverse('drive_test:measurement_list') + f'?session={session.session_ref}',
        'cell_matching_url': reverse('drive_test:cell_matching') + f'?session={session.session_ref}',
        'gis_url': reverse('drive_test:gis') + f'?session={session.session_ref}',
    })


# ---------------------------------------------------------------------------
# KPI Analysis workspace
# ---------------------------------------------------------------------------

@login_required
def kpi_analysis(request):
    """Session-scoped KPI analysis workspace.

    Every KPI value on this page comes from DriveTestKpiService (compute() for the
    session-wide numbers, compute_by_technology()/compute_by_cell() — same formulas,
    narrower querysets — for the breakdown tables). The data-quality indicator reuses
    the existing Data Quality roll-up. Nothing here recalculates a KPI independently,
    and no regulatory pass/fail is applied.
    """
    from django.db.models import Exists, OuterRef
    from reference.models import Operator
    from .models import RadioMeasurement
    from .services import data_quality as dq
    from .services.kpi import DriveTestKpiService

    GET = request.GET
    operator = GET.get('operator', '').strip()
    technology = GET.get('technology', '').strip()
    session_ref = GET.get('session', '').strip()

    # All sessions are listed (not just ones with measurements yet) so a processing or
    # failed session is still visible and gets its own honest state below, matching the
    # Data Quality workspace's approach.
    sessions_qs = DriveTestSession.objects.select_related('operator')
    if operator:
        sessions_qs = sessions_qs.filter(operator__code=operator)
    if technology:
        sessions_qs = sessions_qs.filter(Exists(RadioMeasurement.objects.filter(
            technology=technology, measurement__drive_file__session=OuterRef('pk'))))
    sessions = list(sessions_qs.order_by('-test_date', '-created_at')[:200])

    session = next((s for s in sessions if s.session_ref == session_ref), None)
    if session is None and session_ref:
        session = DriveTestSession.objects.filter(session_ref=session_ref).select_related('operator').first()
        if session is not None:
            sessions.insert(0, session)

    techs = sorted(RadioMeasurement.objects.exclude(technology='')
                   .values_list('technology', flat=True).order_by().distinct())

    kpi = by_tech = by_cell = quality = None
    voice_failed = None
    tech_dist = []
    load_error = False
    has_data = is_processing = is_failed = False
    TECH_COLOURS = {'2G': '#1769e0', '3G': '#16805b', '4G': '#b76c00', '5G': '#5c4aa1'}

    if session is not None:
        has_data = session.total_measurements > 0
        is_processing = session.status in ('UPLOADING', 'PENDING', 'PROCESSING')
        is_failed = session.status == 'FAILED'
        if has_data:
            try:
                svc = DriveTestKpiService(session)
                kpi = svc.compute()
                by_tech = svc.compute_by_technology()
                by_cell = svc.compute_by_cell()
                qr_rows = dq.build_rows(dq.annotate_rollup(
                    DriveTestSession.objects.filter(pk=session.pk)))
                quality = qr_rows[0] if qr_rows else None

                voice = kpi['voice']
                if voice['total_calls']:
                    voice_failed = max(voice['attempted'] - voice['connected'] - voice['dropped'], 0)

                tech_total = sum(kpi['network']['technology_breakdown'].values())
                if tech_total:
                    order = {'2G': 0, '3G': 1, '4G': 2, '5G': 3}
                    for tech, count in sorted(kpi['network']['technology_breakdown'].items(),
                                              key=lambda kv: order.get(kv[0], 99)):
                        tech_dist.append({
                            'technology': tech, 'count': count,
                            'pct': round(count / tech_total * 100, 1),
                            'color': TECH_COLOURS.get(tech, '#64748b'),
                        })
            except Exception:
                logger.exception('KPI Analysis query failed for session %s', session.session_ref)
                load_error = True

    keep = GET.copy()
    keep.pop('session', None)
    return render(request, 'drive_test/kpi_analysis.html', {
        'page_title': 'KPI Analysis',
        'sessions': sessions, 'session': session, 'session_ref': session_ref,
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': techs, 'filters': {'operator': operator, 'technology': technology},
        'kpi': kpi, 'by_tech': by_tech, 'by_cell': by_cell, 'quality': quality,
        'voice_failed': voice_failed, 'tech_dist': tech_dist,
        'load_error': load_error, 'has_data': has_data,
        'is_processing': is_processing, 'is_failed': is_failed,
        'has_any_sessions': DriveTestSession.objects.exists(),
        'keep_qs': keep.urlencode(),
    })


# ---------------------------------------------------------------------------
# Technology Analysis & Operator Comparison
# ---------------------------------------------------------------------------

@login_required
def operator_analysis(request):
    """Descriptive comparison of measured performance across operators and technologies.

    Every KPI value comes from DriveTestKpiService's own (class/static)methods, called
    through services.comparison — never recalculated here. This page never ranks, scores
    or judges an operator/technology, and applies no regulatory pass/fail.
    """
    from reference.models import Operator
    from .models import District, Region
    from .services import comparison as cmp

    GET = request.GET
    meas_qs, filters, errors = cmp.filter_measurements(GET)
    metric = GET.get('metric', 'measurements').strip()
    if metric not in cmp.METRIC_LABELS:
        metric = 'measurements'
    active_tab = GET.get('tab', 'technology').strip()
    if active_tab not in ('technology', 'operator'):
        active_tab = 'technology'

    scope = op_summary = tech_summary = op_kpis = tech_kpis = matrix = geo = quality_ctx = None
    op_kpis_chart = []
    load_error = False
    has_any = False
    try:
        has_any = meas_qs.exists()
        if has_any:
            scope = cmp.scope_summary(meas_qs)
            op_summary = cmp.operator_summary(meas_qs)
            tech_summary = cmp.technology_summary(meas_qs)
            op_kpis = cmp.operator_kpis(meas_qs)
            tech_kpis = cmp.technology_kpis(meas_qs)
            matrix = cmp.operator_technology_matrix(meas_qs, metric)
            geo = cmp.geographic_breakdown(meas_qs)
            quality_ctx = cmp.data_quality_context(meas_qs)
            op_kpis_chart = [
                {'label': row['operator'].name, 'value': cmp.extract_metric(row, metric)}
                for row in op_kpis
            ]
    except Exception:
        logger.exception('Technology Analysis / Operator Comparison query failed')
        load_error = True

    from .models import RadioMeasurement
    techs = sorted(RadioMeasurement.objects.exclude(technology='')
                   .values_list('technology', flat=True).order_by().distinct())
    keep = GET.copy()
    for k in ('metric', 'tab'):
        keep.pop(k, None)
    return render(request, 'drive_test/operator_analysis.html', {
        'page_title': 'Technology Analysis & Operator Comparison',
        'filters': filters, 'errors': errors, 'metric': metric, 'metrics': cmp.METRICS,
        'metric_label': cmp.METRIC_LABELS[metric], 'active_tab': active_tab,
        'scope': scope, 'operator_summary': op_summary, 'technology_summary': tech_summary,
        'operator_kpis': op_kpis, 'operator_kpis_chart': op_kpis_chart,
        'technology_kpis': tech_kpis, 'matrix': matrix,
        'geographic': geo, 'quality_context': quality_ctx,
        'load_error': load_error, 'has_any': has_any,
        'has_any_sessions': DriveTestSession.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': techs,
        'regions': Region.objects.order_by('name'),
        'districts': District.objects.select_related('region').order_by('name'),
        'keep_qs': keep.urlencode(),
    })


# ---------------------------------------------------------------------------
# Processing Monitor & Job Operations
# ---------------------------------------------------------------------------

def _job_duration(f):
    """Elapsed processing time for one DriveTestFile — real timestamps only.
    A still-running file gets a live 'so far' duration, not a fake one."""
    if f.processing_started_at and f.processing_completed_at:
        return f.processing_completed_at - f.processing_started_at
    if f.processing_started_at and f.status in ('RECEIVED', 'PARSING', 'PARSED', 'MATCHING', 'NORMALIZING'):
        return timezone.now() - f.processing_started_at
    return None


@login_required
def processing_monitor(request):
    """Operational visibility into the existing ingestion pipeline.

    Every row is a real DriveTestFile; every count and stage comes from that
    file's own stored status and results (DataQualityResult, Measurement).
    This page runs no processing itself, retries nothing automatically, and
    creates no new job-tracking model — DriveTestFile.status (written by
    drive_test.tasks) plus core.JobRecord already carry everything shown here.
    """
    from reference.models import Operator
    from .models import ParserProfile
    from .services import processing as pm

    GET = request.GET
    qs, filters, errors = pm.filter_jobs(GET)
    try:
        per_page = int(GET.get('per_page', pm.PAGE_SIZES[0]))
    except ValueError:
        per_page = pm.PAGE_SIZES[0]
    if per_page not in pm.PAGE_SIZES:
        per_page = pm.PAGE_SIZES[0]

    load_error = False
    files, page_obj = [], None
    summary = {'total': 0, 'processing': 0, 'completed': 0, 'failed': 0, 'queued': 0}
    try:
        summary = pm.summary_counts(qs)
        ordered = (qs.select_related('session__operator', 'parser_profile', 'quality_result')
                     .order_by('-uploaded_at'))
        ordered = pm.annotate_device(ordered)
        page_obj = Paginator(ordered, per_page).get_page(GET.get('page', 1))
        files = list(page_obj.object_list)
        for f in files:
            f.stage_label = pm.STAGE_LABEL.get(f.status, f.get_status_display())
            f.badge_class = pm.BADGE_CLASS.get(f.status, '')
            f.safe_error = _safe_error(f.error_message) if f.status == 'FAILED' else ''
            duration = _job_duration(f)
            f.duration_label = _format_duration(duration)
            f.duration_seconds = duration.total_seconds() if duration else None
            f.invalid_count = f.quality_result.invalid_records if hasattr(f, 'quality_result') else None
    except Exception:
        logger.exception('Processing Monitor query failed')
        load_error = True

    # Real durations for completed jobs on this page only — never a fabricated one.
    duration_chart = [
        {'label': f.session.session_ref, 'seconds': round(f.duration_seconds, 1)}
        for f in files if f.status == 'COMPLETED' and f.duration_seconds is not None
    ]

    keep = GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/processing.html', {
        'page_title': 'Processing Monitor',
        'files': files, 'page_obj': page_obj, 'summary': summary,
        'duration_chart_json': duration_chart,
        'filters': filters, 'errors': errors,
        'filters_active': any(filters.values()),
        'load_error': load_error,
        'has_any': True if load_error else DriveTestFile.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'status_choices': DriveTestFile.Status.choices,
        'format_choices': list(ParserProfile.objects.filter(is_active=True)
                                .order_by('name').values_list('name', flat=True)),
        'has_active_jobs': summary.get('processing', 0) > 0,
        'per_page': per_page, 'page_sizes': pm.PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


@login_required
def processing_detail(request, file_id):
    """Full processing history for one DriveTestFile — the job/session unit."""
    from .services import processing as pm

    drive_file = get_object_or_404(
        DriveTestFile.objects.select_related('session__operator', 'parser_profile', 'job'),
        pk=file_id,
    )
    session = drive_file.session
    stats = pm.job_stats(drive_file)
    timeline = pm.stage_timeline(drive_file, stats)
    safe_error = _safe_error(drive_file.error_message) if drive_file.status == 'FAILED' else ''

    device = (Measurement.objects.filter(drive_file=drive_file, test_device__isnull=False)
              .values_list('test_device__label', flat=True).first())

    return render(request, 'drive_test/processing_detail.html', {
        'page_title': f'Processing — {drive_file.original_filename}',
        'file': drive_file, 'session': session,
        'stage_label': pm.STAGE_LABEL.get(drive_file.status, drive_file.get_status_display()),
        'badge_class': pm.BADGE_CLASS.get(drive_file.status, ''),
        'timeline': timeline, 'stats': stats, 'safe_error': safe_error,
        'device': device or '',
        'duration_label': _format_duration(_job_duration(drive_file)),
        'is_failed': drive_file.status == 'FAILED',
        'is_processing': drive_file.status in ('RECEIVED', 'PARSING', 'PARSED', 'MATCHING', 'NORMALIZING'),
        'can_view_measurements': drive_file.measurement_count > 0,
        'can_view_quality': stats['has_quality_result'],
        'can_view_kpis': session.total_measurements > 0,
    })


# ---------------------------------------------------------------------------
# Rules & Thresholds Management
# ---------------------------------------------------------------------------

RULES_PAGE_SIZES = (25, 50, 100)


@login_required
def rules_thresholds(request):
    """Management interface for the existing RegulatoryRule / RegulatoryThreshold
    configuration that services.analysis.AnalysisEngine already evaluates against
    every processed file. No second rules engine and no hard-coded regulatory
    value lives here — this page only lists, filters, and (via forms.py's plain
    ModelForms) edits those two tables.
    """
    from reference.models import Operator
    from .services import rules as rl

    GET = request.GET
    active_tab = GET.get('tab', 'rules')
    if active_tab not in ('rules', 'thresholds'):
        active_tab = 'rules'

    rule_qs, rule_filters, rule_errors = rl.filter_rules(GET)
    thr_qs, thr_filters, thr_errors = rl.filter_thresholds(GET)

    try:
        rule_per_page = int(GET.get('rule_per_page', RULES_PAGE_SIZES[0]))
    except ValueError:
        rule_per_page = RULES_PAGE_SIZES[0]
    if rule_per_page not in RULES_PAGE_SIZES:
        rule_per_page = RULES_PAGE_SIZES[0]
    try:
        thr_per_page = int(GET.get('thr_per_page', RULES_PAGE_SIZES[0]))
    except ValueError:
        thr_per_page = RULES_PAGE_SIZES[0]
    if thr_per_page not in RULES_PAGE_SIZES:
        thr_per_page = RULES_PAGE_SIZES[0]

    load_error = False
    rules, rule_page = [], None
    thresholds, thr_page = [], None
    try:
        ordered_rules = rl.annotate_rule_usage(rule_qs).order_by('name')
        rule_page = Paginator(ordered_rules, rule_per_page).get_page(GET.get('rule_page', 1))
        rules = list(rule_page.object_list)
        for r in rules:
            r.status = rl.rule_status(r)
            r.status_label = rl.STATUS_LABELS[r.status]
            r.condition_label = rl.CONDITION_LABELS.get(r.condition, r.condition)

        ordered_thr = rl.annotate_threshold_usage(thr_qs).order_by('rule__name', '-effective_from')
        thr_page = Paginator(ordered_thr, thr_per_page).get_page(GET.get('thr_page', 1))
        thresholds = list(thr_page.object_list)
        for t in thresholds:
            t.status = rl.threshold_status(t)
            t.status_label = rl.STATUS_LABELS[t.status]
            t.rule_status_label = rl.STATUS_LABELS[rl.rule_status(t.rule)]
    except Exception:
        logger.exception('Rules & Thresholds query failed')
        load_error = True

    keep = GET.copy()
    for k in ('rule_page', 'thr_page', 'tab'):
        keep.pop(k, None)

    return render(request, 'drive_test/rules.html', {
        'page_title': 'Rules & Thresholds',
        'active_tab': active_tab,
        'rules': rules, 'rule_page': rule_page,
        'rule_filters': rule_filters, 'rule_errors': rule_errors,
        'rule_filters_active': any(rule_filters.values()),
        'thresholds': thresholds, 'thr_page': thr_page,
        'thr_filters': thr_filters, 'thr_errors': thr_errors,
        'thr_filters_active': any(thr_filters.values()),
        'load_error': load_error,
        'has_any_config': True if load_error else rl.has_any_configuration(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'status_choices': rl.STATUS_CHOICES,
        'threshold_status_choices': rl.THRESHOLD_STATUS_CHOICES,
        'technology_choices': rl.configured_technologies(),
        'metric_choices': rl.configured_metrics(),
        'can_edit': _can_edit_reference(request.user),
        'keep_qs': keep.urlencode(),
        'rule_page_sizes': RULES_PAGE_SIZES, 'thr_page_sizes': RULES_PAGE_SIZES,
        'rule_per_page': rule_per_page, 'thr_per_page': thr_per_page,
    })


@login_required
def rule_detail(request, pk):
    from .services import rules as rl
    from .services.analysis import _finding_type_for_metric

    rule = get_object_or_404(RegulatoryRule, pk=pk)
    thresholds = list(rule.thresholds.select_related('operator').order_by('-effective_from'))
    for t in thresholds:
        t.status = rl.threshold_status(t)
        t.status_label = rl.STATUS_LABELS[t.status]

    finding_qs = Finding.objects.filter(threshold__rule=rule)
    findings = list(finding_qs.select_related('session', 'cell').order_by('-created_at')[:10])
    findings_count = finding_qs.count()
    sessions_evaluated = finding_qs.values('session').distinct().count()

    status = rl.rule_status(rule)
    return render(request, 'drive_test/rule_detail.html', {
        'page_title': f'Rule: {rule.name}',
        'rule': rule, 'status': status, 'status_label': rl.STATUS_LABELS[status],
        'condition_label': rl.CONDITION_LABELS.get(rule.condition, rule.condition),
        'thresholds': thresholds,
        'findings': findings, 'findings_count': findings_count, 'sessions_evaluated': sessions_evaluated,
        # The finding_type this rule's metric maps to in the real engine — imported, not
        # re-derived, so this can never drift from what AnalysisEngine actually does.
        'finding_type_label': dict(Finding.FindingType.choices).get(_finding_type_for_metric(rule.metric)),
        'can_edit': _can_edit_reference(request.user),
    })


def _rule_form_view(request, rule=None):
    from .forms import RegulatoryRuleForm
    from .services import audit as al
    from .services import rules as rl

    before = rl.rule_snapshot(rule) if rule else None
    form = RegulatoryRuleForm(request.POST or None, instance=rule)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        if before is None:
            al.log(request=request, action='CREATE', obj=saved,
                   description=f'Regulatory rule created: {saved.rule_code}', fields=rl.rule_snapshot(saved))
        else:
            changes = al.changes_dict(before, rl.rule_snapshot(saved))
            if changes:
                al.log(request=request, action='UPDATE', obj=saved,
                       description=f'Regulatory rule updated: {saved.rule_code}', changes=changes)
        messages.success(request, f'Rule {saved.rule_code} {"updated" if rule else "created"}.')
        return redirect('drive_test:rule_detail', pk=saved.pk)
    return render(request, 'drive_test/rule_form.html', {
        'form': form, 'rule': rule,
        'page_title': f'Edit {rule.rule_code}' if rule else 'Add Rule',
    })


@regulatory_admin_required
def rule_create(request):
    return _rule_form_view(request)


@regulatory_admin_required
def rule_edit(request, pk):
    return _rule_form_view(request, get_object_or_404(RegulatoryRule, pk=pk))


@regulatory_admin_required
@require_POST
def rule_toggle_active(request, pk):
    """The safe alternative to deleting a rule: is_active already exists on the model
    and is exactly what AnalysisEngine._load_rules() checks — no new field, no delete."""
    from .services import audit as al

    rule = get_object_or_404(RegulatoryRule, pk=pk)
    was_active = rule.is_active
    rule.is_active = not rule.is_active
    rule.save(update_fields=['is_active', 'updated_at'])
    al.log(request=request, action='UPDATE', obj=rule,
           description=f'Regulatory rule {"enabled" if rule.is_active else "disabled"}: {rule.rule_code}',
           changes={'is_active': {'from': was_active, 'to': rule.is_active}})
    messages.success(request, f'Rule {rule.rule_code} {"enabled" if rule.is_active else "disabled"}.')
    return redirect('drive_test:rule_detail', pk=rule.pk)


@login_required
def threshold_detail(request, pk):
    from .services import rules as rl

    threshold = get_object_or_404(
        RegulatoryThreshold.objects.select_related('rule', 'operator'), pk=pk)
    finding_qs = Finding.objects.filter(threshold=threshold)
    findings = list(finding_qs.select_related('session', 'cell').order_by('-created_at')[:10])
    findings_count = finding_qs.count()
    sessions_evaluated = finding_qs.values('session').distinct().count()

    status = rl.threshold_status(threshold)
    rule_status = rl.rule_status(threshold.rule)
    return render(request, 'drive_test/threshold_detail.html', {
        'page_title': f'Threshold: {threshold.rule.name}',
        'threshold': threshold, 'status': status, 'status_label': rl.STATUS_LABELS[status],
        'rule_status': rule_status, 'rule_status_label': rl.STATUS_LABELS[rule_status],
        'findings': findings, 'findings_count': findings_count, 'sessions_evaluated': sessions_evaluated,
        'can_edit': _can_edit_reference(request.user),
    })


def _threshold_form_view(request, threshold=None):
    from .forms import RegulatoryThresholdForm
    from .services import audit as al
    from .services import rules as rl

    before = rl.threshold_snapshot(threshold) if threshold else None
    form = RegulatoryThresholdForm(request.POST or None, instance=threshold)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        if before is None:
            al.log(request=request, action='CREATE', obj=saved,
                   description=f'Regulatory threshold created for {saved.rule.rule_code}',
                   fields=rl.threshold_snapshot(saved))
        else:
            changes = al.changes_dict(before, rl.threshold_snapshot(saved))
            if changes:
                al.log(request=request, action='UPDATE', obj=saved,
                       description=f'Regulatory threshold updated for {saved.rule.rule_code}', changes=changes)
        messages.success(request, f'Threshold for {saved.rule.rule_code} {"updated" if threshold else "created"}.')
        return redirect('drive_test:threshold_detail', pk=saved.pk)
    return render(request, 'drive_test/threshold_form.html', {
        'form': form, 'threshold': threshold,
        'page_title': 'Edit Threshold' if threshold else 'Add Threshold',
    })


@regulatory_admin_required
def threshold_create(request):
    return _threshold_form_view(request)


@regulatory_admin_required
def threshold_edit(request, pk):
    return _threshold_form_view(request, get_object_or_404(RegulatoryThreshold, pk=pk))


@regulatory_admin_required
@require_POST
def threshold_expire(request, pk):
    """The safe alternative to deleting a threshold: sets effective_to to today using
    the model's own existing field — never earlier than an already-set expiry."""
    from .services import audit as al

    threshold = get_object_or_404(RegulatoryThreshold, pk=pk)
    today = timezone.now().date()
    if threshold.effective_to and threshold.effective_to <= today:
        messages.info(request, 'This threshold has already expired.')
    else:
        old_to = threshold.effective_to
        threshold.effective_to = today
        threshold.save(update_fields=['effective_to', 'updated_at'])
        al.log(request=request, action='UPDATE', obj=threshold,
               description=f'Regulatory threshold expired for {threshold.rule.rule_code}',
               changes={'effective_to': {'from': old_to, 'to': today}})
        messages.success(request, 'Threshold expired as of today.')
    return redirect('drive_test:threshold_detail', pk=threshold.pk)


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

AUDIT_PAGE_SIZES = (25, 50, 100)


@login_required
def audit_trail(request):
    """Read-only history of Drive Test actions.

    Merges core.AuditLog (written by this app's own state-changing views/tasks —
    see services/audit.py's module docstring) with the pre-existing CellHistory
    audit trail for cells. This view writes nothing; it only displays what already
    happened.
    """
    from .services import audit as al

    GET = request.GET
    try:
        per_page = int(GET.get('per_page', AUDIT_PAGE_SIZES[0]))
    except ValueError:
        per_page = AUDIT_PAGE_SIZES[0]
    if per_page not in AUDIT_PAGE_SIZES:
        per_page = AUDIT_PAGE_SIZES[0]

    load_error = False
    page, page_obj = [], None
    filters, errors = {}, []
    summary = {'total': 0, 'today': 0, 'config_changes': 0, 'user_actions': 0}
    try:
        entries, filters, errors = al.filter_entries(GET)
        summary = al.summary_counts(entries)
        page_obj = Paginator(entries, per_page).get_page(GET.get('page', 1))
        page = list(page_obj.object_list)
        for e in page:
            e.link = al.detail_url(e)
    except Exception:
        logger.exception('Audit Trail query failed')
        load_error = True

    keep = GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/audit.html', {
        'page_title': 'Audit Trail',
        'entries': page, 'page_obj': page_obj, 'summary': summary,
        'filters': filters, 'errors': errors,
        'filters_active': any(filters.values()),
        'load_error': load_error,
        'has_any': True if load_error else al.has_any_entries(),
        'users': al.used_users(),
        'module_choices': al.module_choices(),
        'action_choices': al.action_choices(),
        'object_type_choices': al.object_type_choices(),
        'per_page': per_page, 'page_sizes': AUDIT_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


@login_required
def audit_detail(request, entry_id):
    """Read-only detail for one audit entry — no edit/delete path exists anywhere
    in this view or its template."""
    from .services import audit as al

    entry = al.get_entry(entry_id)
    if entry is None:
        from django.http import Http404
        raise Http404('Audit entry not found.')
    return render(request, 'drive_test/audit_detail.html', {
        'page_title': 'Audit Event',
        'entry': entry, 'link': al.detail_url(entry),
    })


# ---------------------------------------------------------------------------
# Regulatory Reports & Compliance Reporting
# ---------------------------------------------------------------------------

REPORT_PAGE_SIZES = (25, 50, 100)


def _create_report(request, sessions_qs, filters, report_type, title):
    """Resolve scope -> build context (existing KPI/quality/rules/findings services
    only) -> generate the persisted Excel artefact. Never recalculates a KPI, rule
    or finding — see services/reports.py's module docstring."""
    from django.core.files.base import ContentFile
    from reference.models import Operator
    from .models import RegulatoryReport
    from .services import audit as al
    from .services import reports as rp

    ctx = rp.build_context(sessions_qs, filters=filters)

    operator_id = None
    if filters.get('operator'):
        operator_id = Operator.objects.filter(code=filters['operator']).values_list('pk', flat=True).first()
    region_id = int(filters['region']) if filters.get('region', '').isdigit() else None
    district_id = int(filters['district']) if filters.get('district', '').isdigit() else None

    report = RegulatoryReport.objects.create(
        report_type=report_type, title=title,
        operator_id=operator_id, technology=filters.get('technology', ''),
        region_id=region_id, district_id=district_id,
        date_from=filters.get('date_from') or None, date_to=filters.get('date_to') or None,
        session_count=ctx['scope']['sessions'], measurement_count=ctx['scope']['measurements'],
        finding_count=ctx['scope']['findings'],
        generated_by=request.user if request.user.is_authenticated else None,
    )
    report.sessions.set(sessions_qs)
    ctx['report'] = report
    if not report.title:
        report.title = rp.report_title(report_type, ctx)

    try:
        excel_bytes = rp.generate_excel(ctx)
        report.file.save(f'{report.report_ref}.xlsx', ContentFile(excel_bytes), save=False)
        report.status = RegulatoryReport.Status.COMPLETED
    except Exception as exc:
        logger.exception('Report generation failed for %s', report.report_ref)
        report.status = RegulatoryReport.Status.FAILED
        report.error_message = str(exc)[:2000]
    report.save()

    al.log(request=request, action='CREATE', entity_type='RegulatoryReport', entity_id=report.report_ref,
           description=f'Regulatory report generated: {report.report_ref} ({report.get_report_type_display()})',
           sessions=ctx['scope']['sessions'], measurements=ctx['scope']['measurements'],
           findings=ctx['scope']['findings'], status=report.status,
           **({'error': report.error_message} if report.error_message else {}))
    return report


@login_required
def report_generate(request):
    """Report configuration + backend-authoritative scope preview. The browser never
    determines the final dataset: 'Preview Scope' and 'Generate Report' both POST to
    this same view, which resolves the scope itself via services.reports.resolve_scope.
    """
    from reference.models import Operator
    from .models import RadioMeasurement, RegulatoryReport
    from .services import reports as rp

    GET_or_POST = request.POST if request.method == 'POST' else request.GET
    sessions_qs, filters, errors = rp.resolve_scope(GET_or_POST)
    has_scope_input = any(filters.values())
    scope = None
    if has_scope_input and not errors:
        scope = rp.report_scope_summary(sessions_qs, rp.scope_measurements(sessions_qs))

    action = request.POST.get('action') if request.method == 'POST' else None
    if action == 'generate':
        if not _can_edit_reference(request.user):
            messages.error(request, 'You do not have permission to generate reports.')
        elif errors:
            for e in errors:
                messages.error(request, e)
        elif not sessions_qs.exists():
            messages.error(request, 'No drive-test sessions match the selected criteria.')
        else:
            report_type = request.POST.get('report_type', '')
            if report_type not in RegulatoryReport.ReportType.values:
                report_type = RegulatoryReport.ReportType.COMPLIANCE
            title = request.POST.get('title', '').strip()[:200]
            report = _create_report(request, sessions_qs, filters, report_type, title)
            if report.status == RegulatoryReport.Status.COMPLETED:
                messages.success(request, f'Report {report.report_ref} generated.')
            else:
                messages.error(request, f'Report generation failed: {report.error_message}')
            return redirect('drive_test:report_detail', report_ref=report.report_ref)

    completed_sessions = (DriveTestSession.objects.filter(status__in=('COMPLETED', 'PARTIAL'))
                          .select_related('operator').order_by('-test_date'))
    return render(request, 'drive_test/reports/report_generate.html', {
        'page_title': 'Generate Report',
        'filters': filters, 'errors': errors, 'scope': scope, 'has_scope_input': has_scope_input,
        'selected_report_type': GET_or_POST.get('report_type', ''),
        'selected_title': GET_or_POST.get('title', ''),
        'selected_sessions': GET_or_POST.getlist('session') if hasattr(GET_or_POST, 'getlist') else [],
        'report_types': RegulatoryReport.ReportType.choices,
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'technology_choices': sorted(RadioMeasurement.objects.exclude(technology='')
                                     .values_list('technology', flat=True).order_by().distinct()),
        'regions': Region.objects.order_by('name'),
        'districts': District.objects.select_related('region').order_by('name'),
        'session_choices': completed_sessions,
        'can_generate': _can_edit_reference(request.user),
    })


@login_required
def report_list(request):
    from reference.models import Operator
    from .models import RegulatoryReport

    GET = request.GET
    f = {k: GET.get(k, '').strip() for k in ('q', 'type', 'operator', 'status', 'date_from', 'date_to')}
    qs = RegulatoryReport.objects.select_related('operator', 'generated_by').all()
    errors = []

    if f['q']:
        qs = qs.filter(Q(report_ref__icontains=f['q']) | Q(title__icontains=f['q']))
    if f['type']:
        if f['type'] not in RegulatoryReport.ReportType.values:
            errors.append('Unrecognised report type.')
            qs = RegulatoryReport.objects.none()
        else:
            qs = qs.filter(report_type=f['type'])
    if f['operator']:
        qs = qs.filter(operator__code=f['operator'])
    if f['status']:
        if f['status'] not in RegulatoryReport.Status.values:
            errors.append('Unrecognised status.')
            qs = RegulatoryReport.objects.none()
        else:
            qs = qs.filter(status=f['status'])
    try:
        if f['date_from']:
            qs = qs.filter(generated_at__date__gte=f['date_from'])
        if f['date_to']:
            qs = qs.filter(generated_at__date__lte=f['date_to'])
    except (ValueError, ValidationError):
        errors.append('Date must look like 2026-09-24.')
        qs = RegulatoryReport.objects.none()

    try:
        per_page = int(GET.get('per_page', REPORT_PAGE_SIZES[0]))
    except ValueError:
        per_page = REPORT_PAGE_SIZES[0]
    if per_page not in REPORT_PAGE_SIZES:
        per_page = REPORT_PAGE_SIZES[0]

    page_obj = Paginator(qs.order_by('-generated_at'), per_page).get_page(GET.get('page', 1))
    reports = list(page_obj.object_list)
    for r in reports:
        r.session_names = list(r.sessions.values_list('session_ref', flat=True)[:3])

    keep = GET.copy()
    keep.pop('page', None)
    return render(request, 'drive_test/reports/report_list.html', {
        'page_title': 'Regulatory Reports',
        'reports': reports, 'page_obj': page_obj, 'filters': f, 'errors': errors,
        'filters_active': any(f.values()),
        'has_any': True if errors else RegulatoryReport.objects.exists(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'report_types': RegulatoryReport.ReportType.choices,
        'status_choices': RegulatoryReport.Status.choices,
        'can_generate': _can_edit_reference(request.user),
        'per_page': per_page, 'page_sizes': REPORT_PAGE_SIZES, 'keep_qs': keep.urlencode(),
        'first_item': page_obj.start_index() if page_obj else 0,
        'last_item': page_obj.end_index() if page_obj else 0,
    })


@login_required
def report_detail(request, report_ref):
    """The formal report document — recomputed live from the report's frozen session
    scope (never from the stored Excel), so it always reflects current KPI/finding/
    data-quality state for those exact sessions."""
    from .models import RegulatoryReport
    from .services import reports as rp

    report = get_object_or_404(RegulatoryReport, report_ref=report_ref)
    ctx = rp.build_context(report.sessions.all(), report=report, filters={
        'operator': report.operator.code if report.operator_id else '',
        'technology': report.technology,
    })
    return render(request, 'drive_test/reports/report_detail.html', {
        'page_title': f'{report.report_ref}',
        'report': report, **ctx,
    })


@login_required
def report_download(request, report_ref):
    """Serves the persisted Excel artefact — never regenerated on download."""
    from django.http import FileResponse
    from .models import RegulatoryReport
    from .services import audit as al

    report = get_object_or_404(RegulatoryReport, report_ref=report_ref)
    if not report.file:
        messages.error(request, 'No report file is available for this report.')
        return redirect('drive_test:report_detail', report_ref=report.report_ref)
    al.log(request=request, action='EXPORT', entity_type='RegulatoryReport', entity_id=report.report_ref,
           description=f'Regulatory report downloaded: {report.report_ref}')
    return FileResponse(report.file.open('rb'), as_attachment=True,
                        filename=f'{report.report_ref}.xlsx')


@login_required
def report_pdf(request, report_ref):
    """PDF is generated on demand from the report's frozen session scope — the scope
    itself is stored; the formatted bytes are not, so this never drifts from the
    Excel/browser view built from the exact same services.reports.build_context()."""
    from django.http import HttpResponse
    from .models import RegulatoryReport
    from .services import audit as al
    from .services import reports as rp

    report = get_object_or_404(RegulatoryReport, report_ref=report_ref)
    ctx = rp.build_context(report.sessions.all(), report=report)
    pdf_bytes = rp.generate_pdf(ctx)
    al.log(request=request, action='EXPORT', entity_type='RegulatoryReport', entity_id=report.report_ref,
           description=f'Regulatory report PDF downloaded: {report.report_ref}')
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{report.report_ref}.pdf"'
    return response


@login_required
def report_map_data(request, report_ref):
    """GeoJSON of the real sites matched within this report's scope — never a
    fabricated point, never an unmatched measurement assigned a location."""
    from .models import RegulatoryReport, Site
    from .services import reports as rp
    from .services.cell_reference import has_valid_coords

    report = get_object_or_404(RegulatoryReport, report_ref=report_ref)
    meas_qs = rp.scope_measurements(report.sessions.all())
    site_ids = (meas_qs.filter(matched_cell__isnull=False)
                .values_list('matched_cell__sector__site_id', flat=True).distinct())
    sites = Site.objects.filter(pk__in=list(site_ids)).select_related('operator')
    features = [{
        'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [s.longitude, s.latitude]},
        'properties': {'site_id': s.site_id, 'name': s.name, 'operator': s.operator.name},
    } for s in sites if has_valid_coords(s.latitude, s.longitude)]
    return JsonResponse({'type': 'FeatureCollection', 'features': features})


# ---------------------------------------------------------------------------
# Benchmark Comparison Engine
# ---------------------------------------------------------------------------

@login_required
def benchmark_campaign_list(request):
    qs = BenchmarkCampaign.objects.select_related('region', 'created_by').all()

    status = request.GET.get('status')
    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'drive_test/benchmark/campaign_list.html', {
        'page_obj': page,
        'status_filter': status,
        'status_choices': BenchmarkCampaign.Status.choices,
    })


@login_required
def benchmark_campaign_create(request):
    from .forms import BenchmarkCampaignForm
    from .services.benchmark import auto_assign_sessions

    if request.method == 'POST':
        form = BenchmarkCampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.created_by = request.user
            campaign.kpi_weights = BenchmarkCampaign.default_weights()
            campaign.save()
            count = auto_assign_sessions(campaign)
            messages.success(request, f'Campaign created with {count} sessions auto-assigned.')
            return redirect('drive_test:benchmark_campaign_detail', ref=campaign.campaign_ref)
    else:
        form = BenchmarkCampaignForm()

    return render(request, 'drive_test/benchmark/campaign_form.html', {
        'form': form,
        'title': 'New Benchmark Campaign',
    })


@login_required
def benchmark_campaign_detail(request, ref):
    from reference.models import Operator
    from .services.benchmark import GRADE_COLORS, GRADE_LABELS

    campaign = get_object_or_404(
        BenchmarkCampaign.objects.select_related('region', 'created_by'),
        campaign_ref=ref,
    )
    scores = campaign.scores.select_related('operator').order_by('-composite_score')
    sessions = campaign.sessions.select_related('operator', 'region').order_by('operator__name', 'test_date')

    kpi_labels = {
        'coverage': 'Coverage %', 'rssi_mean': 'Mean RSSI', 'cssr': 'CSSR %',
        'dcr': 'DCR %', 'mos': 'MOS', 'speed': 'Speed (km/h)',
    }

    score_data = []
    for s in scores:
        score_data.append({
            'operator': s.operator.name,
            'composite': s.composite_score,
            'grade': s.grade,
            'grade_label': GRADE_LABELS.get(s.grade, ''),
            'grade_color': GRADE_COLORS.get(s.grade, '#666'),
            'kpi_scores': s.kpi_scores,
            'kpi_values': s.kpi_values,
            'sessions_count': s.sessions_count,
            'measurements_count': s.measurements_count,
        })

    return render(request, 'drive_test/benchmark/campaign_detail.html', {
        'campaign': campaign,
        'scores': scores,
        'score_data': score_data,
        'sessions': sessions,
        'kpi_labels': kpi_labels,
        'weights': campaign.kpi_weights or BenchmarkCampaign.default_weights(),
    })


@login_required
@require_POST
def benchmark_campaign_score(request, ref):
    from .services.benchmark import auto_assign_sessions, score_campaign

    campaign = get_object_or_404(BenchmarkCampaign, campaign_ref=ref)

    if not campaign.sessions.exists():
        auto_assign_sessions(campaign)

    if not campaign.sessions.exists():
        messages.warning(request, 'No sessions found for this campaign period. Upload drive test data first.')
        return redirect('drive_test:benchmark_campaign_detail', ref=ref)

    campaign.status = 'SCORING'
    campaign.save(update_fields=['status'])

    results = score_campaign(campaign)
    messages.success(request, f'Scoring complete — {len(results)} operators scored.')
    return redirect('drive_test:benchmark_campaign_detail', ref=ref)


@login_required
def benchmark_dashboard(request):
    from reference.models import Operator
    from .services.benchmark import GRADE_COLORS, GRADE_LABELS, campaign_trend

    campaigns = BenchmarkCampaign.objects.filter(status='COMPLETED').order_by('-date_from')[:10]
    latest = campaigns.first()

    latest_scores = []
    radar_data = {}
    if latest:
        scores = latest.scores.select_related('operator').order_by('-composite_score')
        kpi_keys = list((latest.kpi_weights or BenchmarkCampaign.default_weights()).keys())
        for s in scores:
            latest_scores.append({
                'operator': s.operator.name,
                'composite': s.composite_score,
                'grade': s.grade,
                'grade_label': GRADE_LABELS.get(s.grade, ''),
                'grade_color': GRADE_COLORS.get(s.grade, '#666'),
                'kpi_scores': s.kpi_scores,
            })
            radar_data[s.operator.name] = [s.kpi_scores.get(k, 0) for k in kpi_keys]

    trend_data = {}
    operators = Operator.objects.filter(enabled=True).order_by('name')
    for op in operators:
        t = campaign_trend(op)
        if t:
            trend_data[op.name] = t

    kpi_labels = {
        'coverage': 'Coverage', 'rssi_mean': 'RSSI', 'cssr': 'CSSR',
        'dcr': 'DCR', 'mos': 'MOS', 'speed': 'Speed',
    }

    return render(request, 'drive_test/benchmark/dashboard.html', {
        'latest_campaign': latest,
        'latest_scores': latest_scores,
        'campaigns': campaigns,
        'radar_data': radar_data,
        'trend_data': trend_data,
        'kpi_labels': kpi_labels,
        'grade_colors': GRADE_COLORS,
    })
