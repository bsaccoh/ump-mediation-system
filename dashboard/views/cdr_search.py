"""CDR search, export, detail."""
from dashboard.views._common import *


# =============================================================================
# Unified CDR Search
# =============================================================================

STREAM_MODELS = {
    'MSC':  MSCRecord,
    'IMS':  IMSRecord,
    'PGW':  PGWRecord,
    'SGSN': SGSNRecord,
    'SGW':  SGWRecord,
}


def _build_unified_queryset(params, stream):
    """Build filtered queryset for any stream."""
    model = STREAM_MODELS.get(stream)
    if not model:
        return None, 'Unknown stream'

    calling = params.get('calling_number', '').strip()
    called = params.get('called_number', '').strip()
    imsi = params.get('imsi', '').strip()
    imei = params.get('imei', '').strip()
    service_type = params.get('service_type', '').strip()
    record_type = params.get('record_type', '').strip()
    source_id = params.get('source_id', '').strip()
    start_date = params.get('start_date', '').strip()
    end_date = params.get('end_date', '').strip()
    apn = params.get('apn', '').strip()
    rat_type = params.get('rat_type', '').strip()

    query = model.objects.select_related('file', 'source')
    filters = []

    if calling:
        query = query.filter(calling_number__icontains=calling)
        filters.append(f'calling={calling}')
    if called:
        query = query.filter(called_number__icontains=called)
        filters.append(f'called={called}')
    # For IMS the imsi filter is applied below with a wider OR over
    # calling_imsi / called_imsi, so skip the strict match here.
    if imsi and stream != 'IMS':
        query = query.filter(imsi__icontains=imsi)
        filters.append(f'imsi={imsi}')
    if imei:
        query = query.filter(imei__icontains=imei)
        filters.append(f'imei={imei}')

    # MSC-specific filters
    if stream == 'MSC':
        paired = (params.get('paired') or '').strip().lower()
        file_id = (params.get('file_id') or '').strip()
        if paired == 'yes':
            query = query.filter(paired_record__isnull=False)
            filters.append('paired=yes')
        elif paired == 'no':
            query = query.filter(paired_record__isnull=True)
            filters.append('paired=no')
        if file_id.isdigit():
            query = query.filter(file_id=int(file_id))
        if service_type:
            query = query.filter(service_type=service_type)
            filters.append(f'service={service_type}')
        if record_type:
            variant_groups = {
                'SMS-MT':         ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
                'SMSMT':          ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
                'SMS-MO':         ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
                'SMSMO':          ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
                'CallForwarding': ['CallForwarding', 'CALL_FORWARDING', 'ROAMING_FORWARDING', 'CFW', 'MTRF', 'CF'],
                'CF':             ['CallForwarding', 'CALL_FORWARDING', 'ROAMING_FORWARDING', 'CFW', 'MTRF', 'CF'],
                # Gateway records are stored as the short form GWI / GWO
                # (from the BIG_DATA CSV's CALL_TYPE), so the dropdown's
                # GWIN / GWOUT values must also match the short form.
                'GWIN':           ['GWI', 'GWIN'],
                'GWOUT':          ['GWO', 'GWOUT'],
                'GWI':            ['GWI', 'GWIN'],
                'GWO':            ['GWO', 'GWOUT'],
            }
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            all_types = []
            for t in types:
                variants = variant_groups.get(t)
                if variants:
                    all_types.extend(variants)
                else:
                    all_types.append(t)
            query = query.filter(record_type__in=all_types)
            filters.append(f'type={",".join(types)}')

    # PGW-specific filters
    if stream == 'PGW':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # SGSN-specific filters
    if stream == 'SGSN':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type__icontains=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # SGW-specific filters
    if stream == 'SGW':
        if apn:
            query = query.filter(apn__icontains=apn)
            filters.append(f'apn={apn}')
        if rat_type:
            query = query.filter(rat_type=rat_type)
            filters.append(f'rat={rat_type}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    # IMS-specific filters
    if stream == 'IMS':
        sip_method   = params.get('sip_method', '').strip()
        role_of_node = params.get('role_of_node', '').strip()
        session_id   = params.get('session_id', '').strip()
        calling_imsi = params.get('calling_imsi', '').strip()
        called_imsi  = params.get('called_imsi', '').strip()
        paired       = (params.get('paired') or '').strip().lower()
        file_id      = (params.get('file_id') or '').strip()
        if paired == 'yes':
            query = query.filter(paired_record__isnull=False)
            filters.append('paired=yes')
        elif paired == 'no':
            query = query.filter(paired_record__isnull=True)
            filters.append('paired=no')
        if file_id.isdigit():
            query = query.filter(file_id=int(file_id))
        if service_type:
            query = query.filter(service_type=service_type)
            filters.append(f'service={service_type}')
        if sip_method:
            query = query.filter(sip_method__iexact=sip_method)
            filters.append(f'sip={sip_method}')
        if role_of_node:
            query = query.filter(role_of_node__iexact=role_of_node)
            filters.append(f'role={role_of_node}')
        if session_id:
            query = query.filter(session_id__icontains=session_id)
            filters.append(f'session={session_id}')
        # When the user types into the generic "IMSI" filter, also widen the
        # match to calling_imsi / called_imsi so terminating-records with no
        # served-IMSI still show up.
        if imsi:
            query = query.filter(
                Q(imsi__icontains=imsi) |
                Q(calling_imsi__icontains=imsi) |
                Q(called_imsi__icontains=imsi)
            )
        if calling_imsi:
            query = query.filter(calling_imsi__icontains=calling_imsi)
            filters.append(f'calling_imsi={calling_imsi}')
        if called_imsi:
            query = query.filter(called_imsi__icontains=called_imsi)
            filters.append(f'called_imsi={called_imsi}')
        if record_type:
            types = [t.strip() for t in record_type.split(',') if t.strip()]
            query = query.filter(record_type__in=types)
            filters.append(f'type={",".join(types)}')

    if source_id:
        query = query.filter(source_id=source_id)
        filters.append(f'source={source_id}')

    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(
                Q(start_time__gte=start_dt) | Q(created_at__gte=start_dt)
            )
            filters.append(f'from={start_date}')
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(
                Q(start_time__lt=end_dt) | Q(created_at__lt=end_dt)
            )
            filters.append(f'to={end_date}')
        except ValueError:
            pass

    desc = ', '.join(filters) if filters else 'all records'
    return query, desc


@analyst_required
def cdr_search(request):
    """Unified CDR search page — all streams."""
    msc_count  = MSCRecord.objects.count()
    ims_count  = IMSRecord.objects.count()
    pgw_count  = PGWRecord.objects.count()
    sgsn_count = SGSNRecord.objects.count()
    sgw_count  = SGWRecord.objects.count()
    sources    = DataSource.objects.filter(enabled=True).order_by('name')

    return render(request, 'dashboard/cdr_search.html', {
        'msc_count':  msc_count,
        'ims_count':  ims_count,
        'pgw_count':  pgw_count,
        'sgsn_count': sgsn_count,
        'sgw_count':  sgw_count,
        'total_records': msc_count + ims_count + pgw_count + sgsn_count + sgw_count,
        'sources': sources,
    })


@analyst_required
def cdr_search_api(request):
    """Unified CDR search API endpoint (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    stream = request.POST.get('stream', 'MSC').upper()
    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 20))

    if stream not in STREAM_MODELS:
        return JsonResponse({'success': False, 'message': f'Unknown stream: {stream}'}, status=400)

    query, _ = _build_unified_queryset(request.POST, stream)
    total = query.count()

    if total == 0:
        model = STREAM_MODELS[stream]
        total_in_db = model.objects.count()
        if total_in_db > 0:
            message = f'No {stream} records match your filters. ({total_in_db:,} total in DB). Try Clear then Search.'
        else:
            message = f'No {stream} records found. Upload and process {stream} CDR files first.'

        return JsonResponse({
            'success': True, 'records': [], 'stream': stream,
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'stats': {},
            'message': message,
        })

    offset = (page - 1) * per_page
    records_qs = query.order_by('-created_at')[offset:offset + per_page]
    pages = (total + per_page - 1) // per_page

    records = []
    stats = {}

    if stream == 'MSC':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'service_type': rec.service_type,
                'call_direction': rec.call_direction,
                'calling_number': rec.calling_number,
                'called_number': rec.called_number,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'msc_id': rec.msc_id,
                'smsc_address': rec.smsc_address,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'originating_trunk': rec.originating_trunk,
                'terminating_trunk': rec.terminating_trunk,
                'result_code': rec.result_code,
                'rat_type': rec.rat_type,
                'call_category': rec.call_category,
                'roaming_indicator': rec.roaming_indicator,
                'status': rec.status,
            })
        agg = query.aggregate(
            total_duration=Sum('duration'),
            avg_duration=Avg('duration'),
        )
        stats = {
            'total_records': total,
            'total_duration': agg['total_duration'] or 0,
            'avg_duration': round(agg['avg_duration'] or 0, 2),
        }

    elif stream == 'PGW':
        try:
            for rec in records_qs:
                records.append({
                    'id': rec.pk,
                    'record_type': rec.record_type,
                    'calling_number': rec.calling_number,
                    'apn': rec.apn,
                    'imsi': rec.imsi,
                    'imei': rec.imei,
                    'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                    'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                    'duration': rec.duration,
                    'data_volume_up': rec.data_volume_up,
                    'data_volume_down': rec.data_volume_down,
                    'data_volume_mb': rec.data_volume_mb,
                    'rat_type': rec.rat_type,
                    'rat_type_name': RAT_TYPE_NAMES.get(rec.rat_type, rec.rat_type),
                    'pdn_type': rec.pdn_type,
                    'pgw_address': rec.pgw_address,
                    'node_id': rec.node_id,
                    'cause_for_closing': rec.cause_for_closing,
                    'serving_plmn': rec.serving_plmn,
                    'is_roaming': rec.is_roaming,
                    'cell_id': rec.cell_id,
                    'status': rec.status,
                })
        except Exception as e:
            logger.exception("Error in record loop")
            return JsonResponse({'success': False, 'message': f'Error processing records: {str(e)}'}, status=500)
        
        vol_agg = query.aggregate(
            total_up=Sum('data_volume_up'),
            total_down=Sum('data_volume_down'),
        )
        total_data_up = vol_agg['total_up'] or 0
        total_data_down = vol_agg['total_down'] or 0
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'SGSN':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'service_type': rec.service_type,
                'calling_number': rec.calling_number,
                'apn': rec.apn,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'data_volume_up': rec.data_volume_up,
                'data_volume_down': rec.data_volume_down,
                'data_volume_mb': rec.data_volume_mb,
                'rat_type': rec.rat_type,
                'pdp_type': rec.pdp_type,
                'sgsn_address': rec.sgsn_address,
                'ggsn_address': rec.ggsn_address,
                'node_id': rec.node_id,
                'cause_for_closing': rec.cause_for_closing,
                'serving_plmn': rec.serving_plmn,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'rac': rec.rac,
                'is_roaming': rec.is_roaming,
                'status': rec.status,
            })
        vol_agg = query.aggregate(
            total_up=Sum('data_volume_up'),
            total_down=Sum('data_volume_down'),
        )
        total_data_up = vol_agg['total_up'] or 0
        total_data_down = vol_agg['total_down'] or 0
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'SGW':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type': rec.record_type,
                'calling_number': rec.calling_number,
                'apn': rec.apn,
                'imsi': rec.imsi,
                'imei': rec.imei,
                'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time': rec.end_time.strftime('%Y-%m-%d %H:%M:%S') if rec.end_time else '',
                'duration': rec.duration,
                'data_volume_up': rec.data_volume_up,
                'data_volume_down': rec.data_volume_down,
                'data_volume_mb': rec.data_volume_mb,
                'rat_type': rec.rat_type,
                'rat_type_name': RAT_TYPE_NAMES.get(rec.rat_type, rec.rat_type),
                'pdn_type': rec.pdn_type,
                'sgw_address': rec.sgw_address,
                'pgw_address': rec.pgw_address,
                'node_id': rec.node_id,
                'cause_for_closing': rec.cause_for_closing,
                'serving_plmn': rec.serving_plmn,
                'is_roaming': rec.is_roaming,
                'cell_id': rec.cell_id,
                'lac': rec.lac,
                'status': rec.status,
            })
        vol_agg = query.aggregate(
            total_up=Sum('data_volume_up'),
            total_down=Sum('data_volume_down'),
        )
        total_data_up = vol_agg['total_up'] or 0
        total_data_down = vol_agg['total_down'] or 0
        avg_duration = query.aggregate(avg_duration=Avg('duration'))['avg_duration'] or 0
        total_bytes = total_data_up + total_data_down
        stats = {
            'total_records': total,
            'total_data_up': total_data_up,
            'total_data_down': total_data_down,
            'total_data_mb': round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
            'avg_duration': round(avg_duration, 2),
        }

    elif stream == 'IMS':
        for rec in records_qs:
            records.append({
                'id': rec.pk,
                'record_type':   rec.record_type,
                'service_type':  rec.service_type,
                'sip_method':    rec.sip_method,
                'role_of_node':  rec.role_of_node,
                'calling_number': rec.calling_number,
                'called_number':  rec.called_number,
                # A-party identifiers
                'calling_imsi':  rec.calling_imsi,
                'calling_min':   rec.calling_min,
                'calling_impi':  rec.calling_impi,
                # B-party identifiers
                'called_imsi':   rec.called_imsi,
                'called_min':    rec.called_min,
                'called_impi':   rec.called_impi,
                # Served
                'imsi':           rec.imsi,
                'msisdn':         rec.msisdn,
                'imei':           rec.imei,
                'private_user_identity': rec.private_user_identity,
                # Session
                'session_id':     rec.session_id,
                'icid':           rec.icid,
                # Network
                'node_address':   rec.node_address,
                'originating_ioi': rec.originating_ioi,
                'terminating_ioi': rec.terminating_ioi,
                'call_property':  rec.call_property,
                'call_category':  rec.call_category,
                'charging_category': rec.charging_category,
                'media_type':     rec.media_type,
                'roaming_indicator': rec.roaming_indicator,
                'start_time':  rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
                'end_time':    rec.end_time.strftime('%Y-%m-%d %H:%M:%S')   if rec.end_time   else '',
                'duration':    rec.duration,
                'cause_for_closing': rec.cause_for_closing,
                'status': rec.status,
            })
        agg = query.aggregate(
            total_duration=Sum('duration'),
            avg_duration=Avg('duration'),
            voice_count=Count('id', filter=Q(service_type='VOICE')),
            sms_count=Count('id', filter=Q(service_type='SMS')),
            event_count=Count('id', filter=Q(service_type='EVENT')),
        )
        stats = {
            'total_records':  total,
            'total_duration': agg['total_duration'] or 0,
            'avg_duration':   round(agg['avg_duration'] or 0, 2),
            'voice_count':    agg['voice_count'] or 0,
            'sms_count':      agg['sms_count'] or 0,
            'event_count':    agg['event_count'] or 0,
        }

    return JsonResponse({
        'success': True,
        'records': records,
        'stream': stream,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
        'stats': stats,
    })


# =============================================================================
# Unified CDR Export
# =============================================================================

# Per-stream export-column mappings now live next to each stream:
#   streams/{msc,pgw,sgsn,sgw,ims}/headers.py
# This file imports them and re-exposes as the old constant names so
# nothing else in this module changes shape.
from streams.msc.headers  import MSC_HEADERS  as _MSC_HEADERS
from streams.pgw.headers  import PGW_HEADERS  as _PGW_HEADERS
from streams.sgsn.headers import SGSN_HEADERS as _SGSN_HEADERS
from streams.sgw.headers  import SGW_HEADERS  as _SGW_HEADERS

# Convert (header, source) → (source, header) for the existing export code,
# which expects (field_name, display_label) tuples.
MSC_EXPORT_COLUMNS  = [(s, h) for h, s in _MSC_HEADERS]

PGW_EXPORT_COLUMNS = [(s, h) for h, s in _PGW_HEADERS]

SGSN_EXPORT_COLUMNS = [(s, h) for h, s in _SGSN_HEADERS]
SGW_EXPORT_COLUMNS  = [(s, h) for h, s in _SGW_HEADERS]

IMS_EXPORT_COLUMNS = [
    ('record_type', 'Record Type'), ('service_type', 'Service Type'),
    ('sip_method', 'SIP Method'), ('role_of_node', 'Role of Node'),
    # Parties
    ('calling_number', 'Calling Number'), ('called_number', 'Called Number'),
    ('dialed_number', 'Dialed Number'), ('charged_party', 'Charged Party'),
    # A-party identifiers
    ('calling_imsi', 'Calling IMSI'), ('calling_min', 'Calling MIN'),
    ('calling_impi', 'Calling IMPI'),
    # B-party identifiers
    ('called_imsi', 'Called IMSI'), ('called_min', 'Called MIN'),
    ('called_impi', 'Called IMPI'),
    # Served subscriber
    ('imsi', 'IMSI'), ('msisdn', 'MSISDN'),
    ('imei', 'IMEI'), ('private_user_identity', 'Private User Identity (IMPI)'),
    # Session identifiers
    ('session_id', 'Session-ID (Call-ID)'), ('icid', 'ICID'),
    # Timing
    ('start_time', 'Start Time'), ('end_time', 'End Time'),
    ('duration', 'Duration (s)'), ('ringing_duration', 'Ringing Duration (s)'),
    # Network / routing
    ('node_address', 'Node Address'),
    ('originating_ioi', 'Originating IOI'), ('terminating_ioi', 'Terminating IOI'),
    ('msc_number', 'MSC Number'), ('vlr_number', 'VLR Number'),
    ('call_property', 'Call Property'),
    # Access network (derived from access_network_info)
    ('technology', 'Technology'),
    ('serving_plmn', 'Serving PLMN'),
    ('tac', 'TAC'), ('lac', 'LAC'),
    ('cell_id', 'Cell ID'), ('enodeb_id', 'eNodeB ID'),
    ('ue_ip', 'UE IP'),
    ('apn', 'APN'),
    # Call forwarding
    ('forwarded_number', 'Forwarded To'),
    ('redirecting_number', 'Redirecting Number'),
    ('diversion_reason', 'Diversion Reason'),
    ('diversion_count', 'Diversion Count'),
    # Classification / charging
    ('charging_category', 'Charging Category'),
    ('roaming_indicator', 'Roaming'), ('call_category', 'Call Category'),
    ('media_type', 'Media Type'),
    ('served_subscriber_type', 'Served Subscriber Type'),
    ('access_network_info', 'Access Network Info'),
    ('cause_for_closing', 'Cause for Closing'),
    ('service_reason_code', 'Service Reason Code'),
    ('online_charging_flag', 'Online Charging Flag'),
    ('supplementary_service', 'Supplementary Service'),
    ('service_context_id', 'Service Context ID'),
    ('sequence_number', 'Sequence #'),
    ('status', 'Status'),
]

MAX_EXPORT_ROWS = 100000


@analyst_required
def cdr_export(request):
    """Unified CDR export as CSV — all streams."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    stream = request.POST.get('stream', 'MSC').upper()
    if stream not in STREAM_MODELS:
        return JsonResponse({'error': f'Unknown stream: {stream}'}, status=400)

    query, filter_desc = _build_unified_queryset(request.POST, stream)
    total = query.count()

    if total > MAX_EXPORT_ROWS:
        return JsonResponse({
            'error': f'Too many records ({total:,}). Maximum export is {MAX_EXPORT_ROWS:,}. Add more filters.'
        }, status=400)
    if total == 0:
        return JsonResponse({'error': 'No records match the filters.'}, status=404)

    if stream == 'MSC':
        columns = MSC_EXPORT_COLUMNS
    elif stream == 'IMS':
        columns = IMS_EXPORT_COLUMNS
    elif stream == 'PGW':
        columns = PGW_EXPORT_COLUMNS
    elif stream == 'SGSN':
        columns = SGSN_EXPORT_COLUMNS
    else:
        columns = SGW_EXPORT_COLUMNS

    def stream_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([col[1] for col in columns])
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        batch_size = 2000
        qs = query.order_by('-start_time', '-created_at')
        for offset in range(0, total, batch_size):
            rows = qs[offset:offset + batch_size]
            for rec in rows:
                row = []
                for field, _ in columns:
                    val = getattr(rec, field, '')
                    if hasattr(val, 'strftime'):
                        val = val.strftime('%Y-%m-%d %H:%M:%S')
                    row.append(val if val is not None else '')
                writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{stream.lower()}_export_{ts}.csv'
    response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# =============================================================================
# Unified CDR Detail
# =============================================================================

@analyst_required
def cdr_detail(request, stream, pk):
    """Unified CDR record detail view."""
    stream = stream.upper()
    if stream == 'MSC':
        record = get_object_or_404(MSCRecord, pk=pk)
        return render(request, 'dashboard/msc_detail.html', {'record': record, 'stream': 'MSC'})
    elif stream == 'IMS':
        record = get_object_or_404(IMSRecord, pk=pk)
        return render(request, 'dashboard/ims_detail.html', {'record': record, 'stream': 'IMS'})
    elif stream == 'PGW':
        record = get_object_or_404(PGWRecord, pk=pk)
        return render(request, 'dashboard/pgw_detail.html', {'record': record, 'stream': 'PGW'})
    elif stream == 'SGSN':
        record = get_object_or_404(SGSNRecord, pk=pk)
        return render(request, 'dashboard/sgsn_detail.html', {'record': record, 'stream': 'SGSN'})
    elif stream == 'SGW':
        record = get_object_or_404(SGWRecord, pk=pk)
        return render(request, 'dashboard/sgw_detail.html', {'record': record, 'stream': 'SGW'})
    else:
        return JsonResponse({'error': f'Unknown stream: {stream}'}, status=404)

