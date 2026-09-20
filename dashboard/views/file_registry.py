"""File registry views."""
from dashboard.views._common import *


# =============================================================================
# File Registry
# =============================================================================

# Fallback display names for decoder types that have no DataSource configured
_DECODER_DISPLAY = {
    'MSC':  'MSC (Voice/SMS)',
    'PGW':  'PGW (4G Data)',
    'SGW':  'SGW (4G Serving GW)',
    'SGSN': 'SGSN (2G/3G Data)',
    'OCS':  'OCS Input',
    'CBS':  'CBS Output',
}


def _build_acq_tree():
    """Build acquisition tree from enabled DataSource records.

    Returns a list of decoder-type groups, each with a list of source leaves.
    """
    sources = DataSource.objects.filter(enabled=True).order_by('decoder_type', 'name')
    groups = {}
    for src in sources:
        dt = src.decoder_type
        if dt == 'AUTO':
            continue  # AUTO sources don't belong to a specific portal
        if dt not in groups:
            groups[dt] = {
                'decoder': dt,
                'label': _DECODER_DISPLAY.get(dt, dt),
                'sources': [],
                'total': 0,
                'completed': 0,
            }
        qs = CDRFile.objects.filter(source=src)
        total = qs.count()
        completed = qs.filter(status='COMPLETED').count()
        groups[dt]['sources'].append({
            'id': src.pk,
            'name': src.name,
            'total': total,
            'completed': completed,
        })
        groups[dt]['total'] += total
        groups[dt]['completed'] += completed

    # Also surface decoder types that have files but no DataSource
    for dt in CDRFile.objects.values_list('decoder_type', flat=True).distinct():
        if dt and dt not in groups and dt != 'AUTO':
            qs = CDRFile.objects.filter(decoder_type=dt)
            total = qs.count()
            groups[dt] = {
                'decoder': dt,
                'label': _DECODER_DISPLAY.get(dt, dt),
                'sources': [],
                'total': total,
                'completed': qs.filter(status='COMPLETED').count(),
            }

    return list(groups.values())


def _build_dist_tree():
    """Build distribution tree from enabled DistributionPortal records.

    Portals with a Vendor FK are grouped by vendor (preferred).
    Portals without a vendor fall back to the group/group_label fields.
    """
    portals = (DistributionPortal.objects
               .filter(enabled=True)
               .select_related('vendor')
               .order_by('vendor__name', 'group', 'label'))
    groups = {}
    for p in portals:
        if p.vendor and p.vendor.enabled:
            g_id = f'vendor_{p.vendor.pk}'
            g_label = p.vendor.name
        else:
            g_id = p.group or 'other'
            g_label = p.group_label or p.group or 'Other'

        if g_id not in groups:
            groups[g_id] = {'id': g_id, 'label': g_label, 'portals': [], 'total': 0}

        from collection.models import DistributionLog
        if p.output_portal_id:
            count = DistributionLog.objects.filter(
                output_portal_id=p.output_portal_id,
                status=DistributionLog.Status.SUCCESS,
            ).count()
        else:
            count = 0
        groups[g_id]['portals'].append({
            'id': p.name,
            'label': p.label,
            'decoder': p.decoder_type,
            'count': count,
        })
        groups[g_id]['total'] += count

    return list(groups.values())


def _format_bytes(bytes_val):
    """Format bytes into a human-readable string."""
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:,.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):,.2f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):,.2f} GB"


def _build_registry_url(request, **kwargs):
    """Return file-registry URL with updated/removed GET query parameters."""
    params = request.GET.copy()
    for k, v in kwargs.items():
        if v is None or v == '':
            params.pop(k, None)
        else:
            params[k] = str(v)
    qs = params.urlencode()
    return f"{reverse('dashboard:file_registry')}{'?' + qs if qs else ''}"


def _get_registry_nav_data(active_operator=None):
    """Build dynamic secondary navigation from configured Input/Output Portals."""
    from portals.models import InputPortal, OutputPortal

    # 1. Configured Input Portals only
    input_portals = []
    for portal in InputPortal.objects.filter(is_active=True).order_by('name'):
        stream = (portal.stream_type or '').upper()
        if stream == 'ALL':
            cnt_qs = CDRFile.objects.all()
        else:
            cnt_qs = CDRFile.objects.filter(decoder_type=stream)
        if active_operator:
            cnt_qs = cnt_qs.filter(operator_code__iexact=active_operator)
        input_portals.append({
            'id': f'inp_{portal.pk}',
            'name': portal.name,
            'stream_type': stream,
            'file_count': cnt_qs.count(),
        })

    # 2. Distribution Portals
    dist_portals = (DistributionPortal.objects.filter(enabled=True)
                    .select_related('vendor')
                    .order_by('label'))
    archive_portals = []
    billing_portals = []
    bigdata_portals = []

    for p in dist_portals:
        if p.output_portal_id:
            count = DistributionLog.objects.filter(
                output_portal_id=p.output_portal_id,
                status=DistributionLog.Status.SUCCESS,
            ).count()
        else:
            count = 0

        portal_url = f"{reverse('dashboard:file_registry')}?mode=distribution&dist_portal={p.name}"
        item = {
            'id': p.name,
            'name': p.label,
            'file_count': count,
            'url': portal_url,
        }

        g_code = (p.group or '').upper()
        v_name = (p.vendor.name if p.vendor else '').upper()

        if g_code == 'ARCHIVE' or 'ARCHIVE' in v_name:
            archive_portals.append(item)
        elif g_code == 'BILLING' or 'BILLING' in v_name:
            billing_portals.append(item)
        elif g_code == 'SL_BIGDATA' or 'BIGDATA' in v_name or 'SL BIGDATA' in v_name:
            bigdata_portals.append(item)
        else:
            if 'ARCHIVE' in p.name.upper():
                archive_portals.append(item)
            elif 'BILLING' in p.name.upper():
                billing_portals.append(item)
            else:
                bigdata_portals.append(item)

    all_files_qs = CDRFile.objects.all()
    if active_operator:
        all_files_qs = all_files_qs.filter(operator_code__iexact=active_operator)

    counters = {
        'all_files': all_files_qs.count(),
        'distributed': DistributionLog.objects.filter(status=DistributionLog.Status.SUCCESS).count(),
        'archive': sum(p['file_count'] for p in archive_portals),
        'billing': sum(p['file_count'] for p in billing_portals),
        'bigdata': sum(p['file_count'] for p in bigdata_portals),
    }

    return {
        'counters': counters,
        'input_portals': input_portals,
        'archive_portals': archive_portals,
        'billing_portals': billing_portals,
        'bigdata_portals': bigdata_portals,
    }


@login_required
def file_registry(request):
    """File Registry page — acquisition and distribution overview matching target visual design."""
    from core.operator_context import get_operator
    active_operator = request.session.get('active_operator') or get_operator()

    # Query params
    file_type = request.GET.get('file_type', '').strip().upper()
    status_filter = request.GET.get('status', '').strip().upper()
    portal_filter = request.GET.get('portal', '').strip()
    from_date = request.GET.get('from_date', '').strip()
    to_date = request.GET.get('to_date', '').strip()
    filename_q = request.GET.get('filename', '').strip()
    mode = request.GET.get('mode', 'acquisition').strip().lower()
    dist_portal = request.GET.get('dist_portal', '').strip()
    cbs_substream_filter = request.GET.get('cbs_substream', '').strip().lower()
    sort = request.GET.get('sort', '-created_at').strip()
    page = request.GET.get('page', '1').strip()
    per_page = request.GET.get('per_page', '15').strip()

    query = CDRFile.objects.select_related('source', 'uploaded_by')
    if active_operator and CDRFile.objects.filter(operator_code__iexact=active_operator).exists():
        query = query.filter(operator_code__iexact=active_operator)

    if mode == 'distribution':
        query = query.filter(status='COMPLETED')
        if dist_portal:
            try:
                portal_obj = DistributionPortal.objects.get(name=dist_portal, enabled=True)
                query = query.filter(decoder_type=portal_obj.decoder_type)
            except DistributionPortal.DoesNotExist:
                pass
    else:
        # Acquisition — filter by configured InputPortal
        if portal_filter:
            if portal_filter.startswith('inp_'):
                from portals.models import InputPortal
                try:
                    inp = InputPortal.objects.get(pk=int(portal_filter[4:]))
                    stream = (inp.stream_type or '').upper()
                    if stream and stream != 'ALL':
                        query = query.filter(decoder_type=stream)
                except (InputPortal.DoesNotExist, ValueError):
                    pass
            elif portal_filter.startswith('decoder_'):
                query = query.filter(decoder_type=portal_filter[8:])
            elif portal_filter.isdigit():
                query = query.filter(source_id=int(portal_filter))
        if file_type and file_type != 'ALL':
            query = query.filter(decoder_type=file_type)
        if status_filter and status_filter != 'ALL':
            query = query.filter(status=status_filter)

    # CBS substream filter
    if cbs_substream_filter and cbs_substream_filter != 'all':
        query = query.filter(cbs_substream__iexact=cbs_substream_filter)

    if filename_q:
        query = query.filter(filename__icontains=filename_q)

    if from_date:
        try:
            query = query.filter(created_at__gte=datetime.strptime(from_date, '%Y-%m-%d'))
        except ValueError:
            pass

    if to_date:
        try:
            end_dt = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(created_at__lt=end_dt)
        except ValueError:
            pass

    total_count = query.count()
    agg = query.aggregate(
        total_size=Sum('file_size'),
        min_time=Min('created_at'),
        max_time=Max('created_at')
    )
    failed_count = query.filter(status__in=[CDRFile.Status.FAILED, 'CORRUPT', 'REJECTED']).count()
    total_size_bytes = agg['total_size'] or 0

    earliest_file_str = agg['min_time'].strftime('%Y-%m-%d %H:%M') if agg['min_time'] else '—'
    latest_file_str = agg['max_time'].strftime('%Y-%m-%d %H:%M') if agg['max_time'] else '—'
    creation_range_str = f"{earliest_file_str} — {latest_file_str}" if earliest_file_str != '—' else '—'

    # Period-over-period comparison (current filter window vs. equivalent prior window)
    file_change = None
    size_change = None
    failed_change = None
    if from_date and to_date:
        try:
            dt_from = datetime.strptime(from_date, '%Y-%m-%d')
            dt_to = datetime.strptime(to_date, '%Y-%m-%d')
            span = dt_to - dt_from
            prev_from = dt_from - span - timedelta(days=1)
            prev_to = dt_from - timedelta(days=1)
            prev_qs = CDRFile.objects.filter(created_at__gte=prev_from, created_at__lt=prev_to)
            if active_operator and CDRFile.objects.filter(operator_code__iexact=active_operator).exists():
                prev_qs = prev_qs.filter(operator_code__iexact=active_operator)
            prev_count = prev_qs.count()
            prev_size = prev_qs.aggregate(s=Sum('file_size'))['s'] or 0
            prev_failed = prev_qs.filter(status__in=[CDRFile.Status.FAILED, 'CORRUPT', 'REJECTED']).count()
            if prev_count > 0:
                file_change = round((total_count - prev_count) / prev_count * 100)
            if prev_size > 0:
                size_change = round((total_size_bytes - prev_size) / prev_size * 100)
            if prev_failed > 0:
                failed_change = round((failed_count - prev_failed) / prev_failed * 100)
        except (ValueError, ZeroDivisionError):
            pass

    summary = {
        'total_files': total_count,
        'file_change': file_change,
        'total_size': _format_bytes(total_size_bytes),
        'size_change': size_change,
        'earliest_file': earliest_file_str,
        'latest_file': latest_file_str,
        'failed_files': failed_count,
        'failed_change': failed_change,
        'creation_range': creation_range_str,
    }

    # Sorting
    sort_map = {
        'id': ('pk',),
        '-id': ('-pk',),
        'file_type': ('decoder_type', '-created_at'),
        '-file_type': ('-decoder_type', '-created_at'),
        'filename': ('filename',),
        '-filename': ('-filename',),
        'created_at': ('created_at',),
        '-created_at': ('-created_at',),
        'file_size': ('file_size',),
        '-file_size': ('-file_size',),
        'status': ('status', '-created_at'),
        '-status': ('-status', '-created_at'),
        'records': ('records_total',),
        '-records': ('-records_total',),
        'valid': ('records_valid',),
        '-valid': ('-records_valid',),
        'source': ('source__name', '-created_at'),
        '-source': ('-source__name', '-created_at'),
    }
    order_fields = sort_map.get(sort, ('-created_at', '-pk'))
    ordered_qs = query.order_by(*order_fields)

    # Pagination
    try:
        per_page_int = int(per_page) if int(per_page) in (10, 15, 25, 50, 100) else 15
    except ValueError:
        per_page_int = 15

    paginator = Paginator(ordered_qs, per_page_int)
    try:
        page_int = int(page)
    except ValueError:
        page_int = 1

    page_obj = paginator.get_page(page_int)
    start_entry = (page_obj.number - 1) * per_page_int + 1 if total_count > 0 else 0
    end_entry = min(page_obj.number * per_page_int, total_count)

    pagination = {
        'start': start_entry,
        'end': end_entry,
        'total': total_count,
        'page': page_obj.number,
        'per_page': per_page_int,
        'pages': paginator.num_pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'first_url': _build_registry_url(request, page=1),
        'previous_url': _build_registry_url(request, page=page_obj.previous_page_number()) if page_obj.has_previous() else '#',
        'next_url': _build_registry_url(request, page=page_obj.next_page_number()) if page_obj.has_next() else '#',
        'last_url': _build_registry_url(request, page=paginator.num_pages),
    }

    # Sort URLs & icons
    sort_cols = ['id', 'file_type', 'filename', 'created_at', 'file_size', 'status', 'records', 'valid', 'source']
    sort_urls = {}
    sort_icons = {}
    for col in sort_cols:
        if sort == col:
            sort_urls[col] = _build_registry_url(request, sort=f'-{col}', page=1)
            sort_icons[col] = 'bi-chevron-up active'
        elif sort == f'-{col}':
            sort_urls[col] = _build_registry_url(request, sort=col, page=1)
            sort_icons[col] = 'bi-chevron-down active'
        else:
            sort_urls[col] = _build_registry_url(request, sort=col, page=1)
            sort_icons[col] = 'bi-chevron-expand'

    # Format files
    files = []
    for f in page_obj:
        files.append({
            'id': f.pk,
            'detail_url': reverse('collection:file_detail', args=[f.pk]),
            'download_url': reverse('dashboard:file_download', args=[f.pk]),
            'distribution_url': f"{reverse('collection:file_detail', args=[f.pk])}#distribution",
            'errors_url': f"{reverse('collection:file_detail', args=[f.pk])}#errors",
            'has_errors': bool(f.error_message or f.status in ('FAILED', 'CORRUPT', 'REJECTED') or (f.records_invalid and f.records_invalid > 0)),
            'file_type': f.decoder_type,
            'external_filename': f.filename,
            'creation_time': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'formatted_size': _format_bytes(f.file_size),
            'status': f.status,
            'records': f.records_total if f.status not in ('COLLECTED', 'PENDING') else None,
            'valid_records': f.records_valid if f.status not in ('COLLECTED', 'PENDING') else None,
            'source_name': f.source.name if f.source else ('INP-' + f.decoder_type if f.decoder_type else '—'),
            'cbs_substream': f.cbs_substream or '',
        })

    # Available File Types and Statuses
    configured_types = list(DataSource.objects.filter(enabled=True).exclude(decoder_type='AUTO').values_list('decoder_type', flat=True).distinct())
    types_in_db = list(CDRFile.objects.exclude(decoder_type='AUTO').values_list('decoder_type', flat=True).distinct())
    file_types = sorted(list(set(configured_types + types_in_db + ['MSC', 'PGW', 'IMS', 'SGW', 'SGSN', 'OCS', 'CBS'])))
    statuses = ['COMPLETED', 'PROCESSING', 'QUEUED', 'PENDING', 'FAILED', 'CORRUPT', 'REJECTED', 'DUPLICATE']

    nav_data = _get_registry_nav_data(active_operator)

    cbs_substreams = sorted(
        CDRFile.objects.filter(cbs_substream__gt='')
        .values_list('cbs_substream', flat=True).distinct()
    ) or ['data', 'voice', 'sms', 'recharge']

    filters = {
        'file_type': file_type,
        'status': status_filter,
        'portal': portal_filter,
        'from_date': from_date,
        'to_date': to_date,
        'filename': filename_q,
        'mode': mode,
        'dist_portal': dist_portal,
        'cbs_substream': cbs_substream_filter,
    }

    export_base_url = reverse('dashboard:file_registry_export')
    base_qs = request.GET.urlencode()
    export_csv_url = f"{export_base_url}?{base_qs}{'&' if base_qs else ''}format=csv"
    export_excel_url = f"{export_base_url}?{base_qs}{'&' if base_qs else ''}format=excel"

    context = {
        'counters': nav_data['counters'],
        'input_portals': nav_data['input_portals'],
        'archive_portals': nav_data['archive_portals'],
        'billing_portals': nav_data['billing_portals'],
        'bigdata_portals': nav_data['bigdata_portals'],
        'summary': summary,
        'filters': filters,
        'file_types': file_types,
        'statuses': statuses,
        'files': files,
        'pagination': pagination,
        'per_page_choices': [10, 15, 25, 50, 100],
        'sort': sort,
        'sort_urls': sort_urls,
        'sort_icons': sort_icons,
        'export_csv_url': export_csv_url,
        'export_excel_url': export_excel_url,
        'cbs_substreams': cbs_substreams,
    }

    return render(request, 'dashboard/file_registry.html', context)


@operator_required
@require_POST
def file_registry_reprocess(request):
    """Bulk or single file reprocess for File Registry."""
    import json
    from collection.views import _clear_records_for_file

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    raw_ids = data.get('file_ids', [])
    if isinstance(raw_ids, str):
        raw_ids = [x.strip() for x in raw_ids.split(',') if x.strip()]

    file_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    if not file_ids:
        return JsonResponse({'success': False, 'message': 'No file IDs provided.'}, status=400)

    files = CDRFile.objects.filter(pk__in=file_ids)
    reprocessed_count = 0

    for f in files:
        decoder = (f.decoder_type or '').upper()
        # 1. Clear previously decoded records
        _clear_records_for_file(f, decoder)
        # 2. Reset state
        f.status = CDRFile.Status.PENDING
        f.retry_count += 1
        f.error_message = ''
        f.records_total = 0
        f.records_valid = 0
        f.records_invalid = 0
        f.records_duplicate = 0
        f.processing_started = None
        f.processing_completed = None
        f.save()
        # 3. Dispatch via Celery or queue worker
        from collection.signals import dispatch_processing
        dispatch_processing(decoder, f.pk, f.filename)
        reprocessed_count += 1

    return JsonResponse({
        'success': True,
        'count': reprocessed_count,
        'message': f'Submitted {reprocessed_count} file(s) for reprocessing.'
    })


@login_required
def file_registry_export(request):
    """Export filtered or selected File Registry files to CSV or Excel."""
    fmt = request.GET.get('format', 'csv').lower()
    selected_ids = request.GET.get('ids', '').strip()

    if selected_ids:
        id_list = [int(x) for x in selected_ids.split(',') if x.strip().isdigit()]
        query = CDRFile.objects.filter(pk__in=id_list).select_related('source')
    else:
        from core.operator_context import get_operator
        active_operator = request.session.get('active_operator') or get_operator()
        query = CDRFile.objects.select_related('source')
        if active_operator and CDRFile.objects.filter(operator_code__iexact=active_operator).exists():
            query = query.filter(operator_code__iexact=active_operator)

        mode = request.GET.get('mode', 'acquisition').strip().lower()
        dist_portal = request.GET.get('dist_portal', '').strip()
        if mode == 'distribution':
            query = query.filter(status='COMPLETED')
            if dist_portal:
                try:
                    p_obj = DistributionPortal.objects.get(name=dist_portal, enabled=True)
                    query = query.filter(decoder_type=p_obj.decoder_type)
                except DistributionPortal.DoesNotExist:
                    pass
        else:
            portal_filter = request.GET.get('portal', '').strip()
            if portal_filter:
                if portal_filter.startswith('inp_'):
                    from portals.models import InputPortal
                    try:
                        inp = InputPortal.objects.get(pk=int(portal_filter[4:]))
                        stream = (inp.stream_type or '').upper()
                        if stream and stream != 'ALL':
                            query = query.filter(decoder_type=stream)
                    except (InputPortal.DoesNotExist, ValueError):
                        pass
                elif portal_filter.startswith('decoder_'):
                    query = query.filter(decoder_type=portal_filter[8:])
                elif portal_filter.isdigit():
                    query = query.filter(source_id=int(portal_filter))
            file_type = request.GET.get('file_type', '').strip().upper()
            if file_type and file_type != 'ALL':
                query = query.filter(decoder_type=file_type)
            status_filter = request.GET.get('status', '').strip().upper()
            if status_filter and status_filter != 'ALL':
                query = query.filter(status=status_filter)

        filename_q = request.GET.get('filename', '').strip()
        if filename_q:
            query = query.filter(filename__icontains=filename_q)

        from_date = request.GET.get('from_date', '').strip()
        if from_date:
            try:
                query = query.filter(created_at__gte=datetime.strptime(from_date, '%Y-%m-%d'))
            except ValueError:
                pass

        to_date = request.GET.get('to_date', '').strip()
        if to_date:
            try:
                end_dt = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1)
                query = query.filter(created_at__lt=end_dt)
            except ValueError:
                pass

        cbs_substream_exp = request.GET.get('cbs_substream', '').strip().lower()
        if cbs_substream_exp and cbs_substream_exp != 'all':
            query = query.filter(cbs_substream__iexact=cbs_substream_exp)

    query = query.order_by('-created_at')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if fmt in ('excel', 'xlsx'):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Files"

        headers = [
            'File ID', 'File Type', 'External File Name', 'Creation Time',
            'File Size', 'Status', 'Records', 'Valid', 'Invalid', 'Duplicate', 'Source'
        ]
        ws.append(headers)

        header_font = Font(bold=True, color='FFFFFF', size=10)
        header_fill = PatternFill(start_color='1677EE', end_color='1677EE', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin', color='DCE6F1'),
            right=Side(style='thin', color='DCE6F1'),
            top=Side(style='thin', color='DCE6F1'),
            bottom=Side(style='thin', color='DCE6F1'),
        )

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border

        for f in query:
            ws.append([
                f.pk,
                f.decoder_type,
                f.filename,
                f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                _format_bytes(f.file_size),
                f.status,
                f.records_total,
                f.records_valid,
                f.records_invalid,
                f.records_duplicate,
                f.source.name if f.source else ('INP-' + f.decoder_type if f.decoder_type else '—')
            ])

        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=len(headers)):
            for cell in row:
                cell.border = thin_border
                cell.font = Font(size=9.5)

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        resp = HttpResponse(
            out.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        resp['Content-Disposition'] = f'attachment; filename="ump_files_{ts}.xlsx"'
        return resp

    # Default CSV
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'File ID', 'File Type', 'External File Name', 'Creation Time',
        'File Size', 'Status', 'Records', 'Valid', 'Invalid', 'Duplicate', 'Source'
    ])
    for f in query:
        writer.writerow([
            f.pk,
            f.decoder_type,
            f.filename,
            f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            _format_bytes(f.file_size),
            f.status,
            f.records_total,
            f.records_valid,
            f.records_invalid,
            f.records_duplicate,
            f.source.name if f.source else ('INP-' + f.decoder_type if f.decoder_type else '—')
        ])
    resp = HttpResponse(out.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="ump_files_{ts}.csv"'
    return resp


@operator_required
def file_download(request, pk):
    """Download the raw CDR file from disk."""
    import os
    import mimetypes

    cdr_file = get_object_or_404(CDRFile, pk=pk)
    if cdr_file.file_path and os.path.isfile(cdr_file.file_path):
        content_type, _ = mimetypes.guess_type(cdr_file.filename)
        return FileResponse(
            open(cdr_file.file_path, 'rb'),
            as_attachment=True,
            filename=cdr_file.filename,
            content_type=content_type or 'application/octet-stream'
        )
    raise Http404(f'File "{cdr_file.filename}" is not available on disk.')


@login_required
def file_registry_api(request):
    """File Registry API — returns paginated CDRFile records.

    Params (acquisition mode):
      source_id     filter by specific DataSource pk
      decoder_type  filter by decoder type group
      status        file status filter

    Params (distribution mode):
      dist_portal   DistributionPortal.name → resolves decoder_type; always COMPLETED
      decoder_type  fallback decoder filter

    Shared params:
      mode          'acquisition' (default) or 'distribution'
      filename      filename substring filter
      start_date / end_date  ISO date range on created_at
      page / per_page
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    mode = request.POST.get('mode', 'acquisition').strip().lower()
    dist_portal_name = request.POST.get('dist_portal', '').strip()
    source_id = request.POST.get('source_id', '').strip()
    decoder_type = request.POST.get('decoder_type', '').strip().upper()
    status_filter = request.POST.get('status', '').strip().upper()
    filename_q = request.POST.get('filename', '').strip()
    start_date = request.POST.get('start_date', '').strip()
    end_date = request.POST.get('end_date', '').strip()
    cbs_substream = request.POST.get('cbs_substream', '').strip().lower()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 15))

    query = CDRFile.objects.select_related('source', 'uploaded_by')

    if mode == 'distribution':
        query = query.filter(status='COMPLETED')
        if dist_portal_name:
            try:
                portal = DistributionPortal.objects.get(name=dist_portal_name, enabled=True)
                query = query.filter(decoder_type=portal.decoder_type)
            except DistributionPortal.DoesNotExist:
                pass
        elif decoder_type and decoder_type != 'ALL':
            query = query.filter(decoder_type=decoder_type)
    else:
        # Acquisition
        if source_id:
            query = query.filter(source_id=source_id)
        elif decoder_type and decoder_type != 'ALL':
            query = query.filter(decoder_type=decoder_type)
        if status_filter and status_filter != 'ALL':
            query = query.filter(status=status_filter)

    if filename_q:
        query = query.filter(filename__icontains=filename_q)
    if start_date:
        try:
            query = query.filter(created_at__gte=datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(created_at__lt=end_dt)
        except ValueError:
            pass
    if cbs_substream and cbs_substream != 'all':
        query = query.filter(cbs_substream__iexact=cbs_substream)

    total = query.count()
    offset = (page - 1) * per_page
    files_qs = query.order_by('-created_at')[offset:offset + per_page]

    files = []
    for f in files_qs:
        files.append({
            'id': f.pk,
            'filename': f.filename,
            'decoder_type': f.decoder_type,
            'portal_label': _DECODER_DISPLAY.get(f.decoder_type, f.decoder_type),
            'file_size': f.file_size,
            'file_size_kb': round(f.file_size / 1024, 1) if f.file_size else 0,
            'status': f.status,
            'records_total': f.records_total,
            'records_valid': f.records_valid,
            'records_invalid': f.records_invalid,
            'records_duplicate': f.records_duplicate,
            'source': f.source.name if f.source else '-',
            'created_at': f.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'processing_started': f.processing_started.strftime('%Y-%m-%d %H:%M:%S') if f.processing_started else '',
            'processing_completed': f.processing_completed.strftime('%Y-%m-%d %H:%M:%S') if f.processing_completed else '',
            'error_message': f.error_message or '',
            'detail_url': reverse('collection:file_detail', args=[f.pk]),
        })

    pages = (total + per_page - 1) // per_page

    agg = query.aggregate(
        total_size=Sum('file_size'),
        total_records=Sum('records_total'),
        total_valid=Sum('records_valid'),
        first_created=Min('created_at'),
        last_created=Max('created_at'),
    )

    return JsonResponse({
        'success': True,
        'files': files,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'aggregate': {
            'total_size_bytes': agg['total_size'] or 0,
            'total_size_mb': round((agg['total_size'] or 0) / (1024 * 1024), 2),
            'total_records': agg['total_records'] or 0,
            'total_valid': agg['total_valid'] or 0,
            'first_created': agg['first_created'].strftime('%Y-%m-%d %H:%M') if agg['first_created'] else '-',
            'last_created': agg['last_created'].strftime('%Y-%m-%d %H:%M') if agg['last_created'] else '-',
        },
    })


