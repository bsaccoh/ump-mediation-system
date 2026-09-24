"""
Drive Test dashboard views.
All views require login; role gates use existing RBAC decorators.
"""

import logging
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import analyst_required, regulator_required, regulatory_admin_required

from .models import (
    Cell, DataQualityResult, DriveTestFile, DriveTestSession,
    Finding, Region, RegulatoryRule, RegulatoryThreshold, Site,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard / index
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    today = date.today()
    thirty_days_ago = today - timedelta(days=30)

    stats = DriveTestSession.objects.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status='COMPLETED')),
        failed=Count('id', filter=Q(status='FAILED')),
        pending=Count('id', filter=Q(status='PENDING') | Q(status='PROCESSING')),
    )

    recent_sessions = (
        DriveTestSession.objects.select_related('operator', 'uploaded_by')
        .order_by('-created_at')[:10]
    )
    open_findings = Finding.objects.filter(is_resolved=False).count()
    critical_findings = Finding.objects.filter(
        is_resolved=False, severity='CRITICAL'
    ).count()

    return render(request, 'drive_test/dashboard.html', {
        'stats': stats,
        'recent_sessions': recent_sessions,
        'open_findings': open_findings,
        'critical_findings': critical_findings,
        'page_title': 'Drive Test Dashboard',
    })


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@login_required
def session_list(request):
    qs = DriveTestSession.objects.select_related('operator', 'region', 'uploaded_by')

    # Filters
    status = request.GET.get('status', '')
    operator_id = request.GET.get('operator', '')
    if status:
        qs = qs.filter(status=status)
    if operator_id:
        qs = qs.filter(operator_id=operator_id)

    qs = qs.order_by('-test_date', '-created_at')
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'drive_test/session_list.html', {
        'page_obj': page_obj,
        'page_title': 'Drive Test Sessions',
        'filters': {'status': status, 'operator': operator_id},
    })


@login_required
def session_detail(request, session_ref):
    session = get_object_or_404(
        DriveTestSession.objects.select_related('operator', 'region', 'uploaded_by'),
        session_ref=session_ref,
    )
    files = session.files.select_related('parser_profile').order_by('-uploaded_at')
    findings = session.findings.select_related('cell').order_by('-created_at')[:20]

    return render(request, 'drive_test/session_detail.html', {
        'session': session,
        'files': files,
        'findings': findings,
        'page_title': f'Session {session.session_ref}',
    })


@login_required
def session_upload(request):
    """Upload a new drive test file and create/attach it to a session."""
    from reference.models import Operator
    operators = Operator.objects.filter(enabled=True).order_by('name')

    if request.method == 'POST':
        return _handle_upload(request)

    return render(request, 'drive_test/session_upload.html', {
        'operators': operators,
        'regions': Region.objects.order_by('name'),
        'page_title': 'Upload Drive Test',
    })


def _handle_upload(request):
    """Process the upload POST. Returns redirect on success, re-render on error."""
    from reference.models import Operator
    from .services.file_handler import stage_file, detect_parser, dispatch_processing
    from .models import DriveTestSession, DriveTestFile, ParserProfile

    errors = []
    operator_id = request.POST.get('operator')
    test_date_str = request.POST.get('test_date')
    test_type = request.POST.get('test_type', 'outdoor')
    title = request.POST.get('title', '')
    region_id = request.POST.get('region') or None

    uploaded_file = request.FILES.get('drive_test_file')
    if not uploaded_file:
        errors.append('No file selected.')

    try:
        operator = Operator.objects.get(pk=operator_id)
    except (Operator.DoesNotExist, ValueError, TypeError):
        errors.append('Invalid operator.')
        operator = None

    if errors:
        operators = Operator.objects.filter(enabled=True).order_by('name')
        return render(request, 'drive_test/session_upload.html', {
            'operators': operators,
            'regions': Region.objects.order_by('name'),
            'errors': errors,
            'page_title': 'Upload Drive Test',
        })

    import tempfile, os
    from pathlib import Path

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as tmp:
        for chunk in uploaded_file.chunks():
            tmp.write(chunk)
        tmp_path = Path(tmp.name)

    try:
        stored_path, sha256 = stage_file(tmp_path, uploaded_file.name, operator.code)
    except FileExistsError as exc:
        os.unlink(tmp_path)
        return render(request, 'drive_test/session_upload.html', {
            'operators': Operator.objects.filter(enabled=True).order_by('name'),
            'regions': Region.objects.order_by('name'),
            'errors': [str(exc)],
            'page_title': 'Upload Drive Test',
        })
    finally:
        if tmp_path.exists():
            os.unlink(tmp_path)

    session = DriveTestSession.objects.create(
        operator=operator,
        title=title,
        test_date=test_date_str or date.today(),
        test_type=test_type,
        region_id=region_id,
        uploaded_by=request.user,
    )

    parser_profile = detect_parser(stored_path)
    drive_file = DriveTestFile.objects.create(
        session=session,
        original_filename=uploaded_file.name,
        file_path=str(stored_path),
        file_size=uploaded_file.size,
        sha256=sha256,
        mime_type=uploaded_file.content_type or '',
        parser_profile=parser_profile,
        detected_format=parser_profile.name if parser_profile else 'unknown',
    )

    dispatch_processing(drive_file.id)

    return redirect('drive_test:session_detail', session_ref=session.session_ref)


# ---------------------------------------------------------------------------
# Reference data views
# ---------------------------------------------------------------------------

@login_required
def site_list(request):
    qs = Site.objects.select_related('operator', 'chiefdom__district__region').order_by('site_id')
    operator_id = request.GET.get('operator', '')
    if operator_id:
        qs = qs.filter(operator_id=operator_id)
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'drive_test/site_list.html', {
        'page_obj': page_obj,
        'page_title': 'Sites',
        'filters': {'operator': operator_id},
    })


@login_required
def cell_list(request):
    qs = Cell.objects.select_related(
        'operator', 'sector__site', 'band'
    ).order_by('operator', 'cell_id')
    operator_id = request.GET.get('operator', '')
    technology = request.GET.get('technology', '')
    if operator_id:
        qs = qs.filter(operator_id=operator_id)
    if technology:
        qs = qs.filter(technology=technology)
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'drive_test/cell_list.html', {
        'page_obj': page_obj,
        'page_title': 'Cell Reference',
        'filters': {'operator': operator_id, 'technology': technology},
    })


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

@login_required
def finding_list(request):
    qs = Finding.objects.select_related(
        'session', 'session__operator', 'cell'
    ).order_by('-created_at')
    severity = request.GET.get('severity', '')
    is_resolved = request.GET.get('resolved', '')
    if severity:
        qs = qs.filter(severity=severity)
    if is_resolved == 'yes':
        qs = qs.filter(is_resolved=True)
    elif is_resolved == 'no':
        qs = qs.filter(is_resolved=False)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'drive_test/finding_list.html', {
        'page_obj': page_obj,
        'page_title': 'Findings',
        'filters': {'severity': severity, 'resolved': is_resolved},
    })


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
        except Exception as exc:
            logger.exception('Reference import failed')
            errors.append(str(exc))

    return render(request, 'drive_test/reference_import.html', {
        'page_title': 'Import Reference Data',
        'operators': all_operators,
        'results': results,
        'errors': errors,
    })
