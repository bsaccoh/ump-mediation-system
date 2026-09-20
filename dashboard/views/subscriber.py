"""Subscriber timeline."""
from dashboard.views._common import *


# =============================================================================
# Subscriber View (remains MSC for now, can expand later)
# =============================================================================

@analyst_required
def subscriber_view(request):
    """Subscriber timeline page."""
    return render(request, 'dashboard/subscriber.html')


@analyst_required
def subscriber_api(request):
    """Subscriber timeline API — returns all activity (voice / SMS / IMS /
    data) for an MSISDN, deduplicated across paired CDR legs.

    Streams searched: MSC, IMS, PGW, SGSN, SGW.  When two records form a
    pair (ICID for IMS, call_reference for MSC), only the lower-id one is
    emitted so the timeline shows one row per real event.
    """
    from django.db.models import F
    from streams.ims.models import IMSRecord

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)

    msisdn = request.POST.get('msisdn', '').strip()
    if not msisdn or len(msisdn) < 5:
        return JsonResponse({'success': False, 'message': 'Enter a valid MSISDN (min 5 digits).'})

    page = int(request.POST.get('page', 1))
    per_page = int(request.POST.get('per_page', 30))

    # ----- Per-stream queries (pair-deduplicated) ----------------------------
    # Pair-dedup: keep only the record with the smaller id from each linked
    # pair so MOC+MTC (or IMS ORIG+TERM) appears as ONE timeline event.
    pair_dedup = Q(paired_record__isnull=True) | Q(id__lt=F('paired_record_id'))

    msc_query = (MSCRecord.objects.filter(
            Q(calling_number__icontains=msisdn) |
            Q(called_number__icontains=msisdn) |
            Q(charged_msisdn__icontains=msisdn)
        ).filter(pair_dedup)
         .select_related('file', 'source', 'paired_record'))

    ims_query = (IMSRecord.objects.filter(
            Q(calling_number__icontains=msisdn) |
            Q(called_number__icontains=msisdn) |
            Q(msisdn__icontains=msisdn) |
            Q(charged_party__icontains=msisdn)
        ).filter(pair_dedup)
         .select_related('file', 'source', 'paired_record'))

    pgw_query = PGWRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    sgsn_query = SGSNRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    sgw_query = SGWRecord.objects.filter(
        Q(calling_number__icontains=msisdn) | Q(imsi__icontains=msisdn)
    ).select_related('file', 'source')

    # ----- Totals & early-exit -----------------------------------------------
    msc_total  = msc_query.count()
    ims_total  = ims_query.count()
    pgw_total  = pgw_query.count()
    sgsn_total = sgsn_query.count()
    sgw_total  = sgw_query.count()
    total = msc_total + ims_total + pgw_total + sgsn_total + sgw_total

    if total == 0:
        return JsonResponse({
            'success': True, 'records': [], 'summary': {},
            'pagination': {'total': 0, 'page': page, 'per_page': per_page, 'pages': 0},
            'message': f'No activity found for "{msisdn}". Try a partial number.',
        })

    # ----- Summary aggregations ---------------------------------------------
    msc_agg = msc_query.aggregate(
        voice=Count('id', filter=Q(service_type='VOICE')),
        sms=Count('id',   filter=Q(service_type='SMS')),
        total_duration=Sum('duration'),
        avg_duration=Avg('duration', filter=Q(service_type='VOICE')),
    )
    ims_agg = ims_query.aggregate(
        voice=Count('id', filter=Q(service_type='VOICE')),
        sms=Count('id',   filter=Q(service_type='SMS')),
        event=Count('id', filter=Q(service_type='EVENT')),
        total_duration=Sum('duration'),
    )

    def _sum_bytes(qs):
        agg = qs.aggregate(up=Sum('data_volume_up'), down=Sum('data_volume_down'))
        return agg['up'] or 0, agg['down'] or 0

    pgw_up, pgw_down   = _sum_bytes(pgw_query)
    sgsn_up, sgsn_down = _sum_bytes(sgsn_query)
    sgw_up, sgw_down   = _sum_bytes(sgw_query)
    total_data_bytes   = pgw_up + pgw_down + sgsn_up + sgsn_down + sgw_up + sgw_down

    # Date range
    first_dates, last_dates = [], []
    for qs in (msc_query, ims_query, pgw_query, sgsn_query, sgw_query):
        d = qs.aggregate(first=Min('start_time'), last=Max('start_time'))
        if d.get('first'): first_dates.append(d['first'])
        if d.get('last'):  last_dates.append(d['last'])
    first_activity = min(first_dates) if first_dates else None
    last_activity  = max(last_dates) if last_dates else None

    # Primary IMSI/IMEI — prefer MSC, fall back to IMS
    imsi_qs = (msc_query.exclude(imsi='').values('imsi')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    if not imsi_qs:
        imsi_qs = (ims_query.exclude(imsi='').values('imsi')
                   .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    imei_qs = (msc_query.exclude(imei='').values('imei')
               .annotate(cnt=Count('id')).order_by('-cnt')[:1])
    if not imei_qs:
        imei_qs = (ims_query.exclude(imei='').values('imei')
                   .annotate(cnt=Count('id')).order_by('-cnt')[:1])

    total_voice = (msc_agg.get('voice') or 0) + (ims_agg.get('voice') or 0)
    total_sms   = (msc_agg.get('sms')   or 0) + (ims_agg.get('sms')   or 0)
    total_data_sessions = pgw_total + sgsn_total + sgw_total
    total_duration = ((msc_agg.get('total_duration') or 0)
                      + (ims_agg.get('total_duration') or 0))
    avg_duration = round(msc_agg.get('avg_duration') or 0, 1)

    summary = {
        'msisdn': msisdn,
        'total_records': total,
        'total_voice': total_voice,
        'total_sms': total_sms,
        'total_other': ims_agg.get('event') or 0,   # IMS events (REGISTER / SUBSCRIBE …)
        'total_data_sessions': total_data_sessions,
        'total_duration': total_duration,
        'avg_duration': avg_duration,
        'total_data_mb': round(total_data_bytes / (1024 * 1024), 2) if total_data_bytes else 0,
        'first_activity': first_activity.strftime('%Y-%m-%d %H:%M') if first_activity else '-',
        'last_activity':  last_activity.strftime('%Y-%m-%d %H:%M') if last_activity else '-',
        'primary_imsi': imsi_qs[0]['imsi'] if imsi_qs else '-',
        'primary_imei': imei_qs[0]['imei'] if imei_qs else '-',
        # Per-stream breakdown for the new badges
        'count_msc':  msc_total,
        'count_ims':  ims_total,
        'count_pgw':  pgw_total,
        'count_sgsn': sgsn_total,
        'count_sgw':  sgw_total,
    }

    # ----- Event list (combined, pair-deduplicated) --------------------------
    combined = []

    # MSC events
    for rec in msc_query.order_by('-start_time', '-created_at')[:500]:
        role = 'CALLER'
        if msisdn in (rec.called_number or ''):
            role = 'CALLED'
        elif msisdn in (rec.charged_msisdn or '') and msisdn not in (rec.calling_number or ''):
            role = 'CHARGED'
        other_party = rec.called_number if role == 'CALLER' else rec.calling_number
        combined.append({
            'id': rec.pk,
            'stream': 'MSC',
            'record_type': rec.record_type,
            'service_type': rec.service_type,
            'role': role,
            'other_party': other_party or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.called_number,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.msc_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': '',
            'paired_record_id': rec.paired_record_id,
            'status': rec.status,
        })

    # IMS events
    for rec in ims_query.order_by('-start_time', '-id')[:500]:
        role_node = (rec.role_of_node or '').upper()
        if msisdn in (rec.called_number or '') and 'TERM' in role_node:
            role = 'CALLED'
        elif msisdn in (rec.calling_number or '') and 'ORIG' in role_node:
            role = 'CALLER'
        elif msisdn in (rec.charged_party or '') and msisdn not in (rec.calling_number or ''):
            role = 'CHARGED'
        else:
            role = 'CALLER' if msisdn in (rec.calling_number or '') else 'CALLED'
        other_party = rec.called_number if role == 'CALLER' else rec.calling_number
        combined.append({
            'id': rec.pk,
            'stream': 'IMS',
            'record_type': rec.record_type,
            'service_type': rec.service_type,
            'role': role,
            'other_party': other_party or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.called_number,
            'imsi': rec.imsi or '',
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.msc_number or '',
            'cell_id': rec.cell_id or '',
            'lac': rec.lac or rec.tac or '',
            'technology': rec.technology or '',
            'paired_record_id': rec.paired_record_id,
            'status': rec.status,
            'sip_method': rec.sip_method or '',
            'call_type': rec.call_type or '',
        })

    # PGW events
    for rec in pgw_query.order_by('-start_time', '-created_at')[:500]:
        combined.append({
            'id': rec.pk,
            'stream': 'PGW',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': RAT_TYPE_NAMES.get(rec.rat_type, ''),
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # SGSN events (2G/3G data)
    for rec in sgsn_query.order_by('-start_time', '-created_at')[:200]:
        combined.append({
            'id': rec.pk,
            'stream': 'SGSN',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': '3G' if str(rec.rat_type or '').upper() in ('UTRAN', '1') else '2G',
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # SGW events (4G data)
    for rec in sgw_query.order_by('-start_time', '-created_at')[:200]:
        combined.append({
            'id': rec.pk,
            'stream': 'SGW',
            'record_type': rec.record_type,
            'service_type': 'DATA',
            'role': 'SUBSCRIBER',
            'other_party': rec.apn or '-',
            'calling_number': rec.calling_number,
            'called_number': rec.apn,
            'imsi': rec.imsi,
            'start_time': rec.start_time.strftime('%Y-%m-%d %H:%M:%S') if rec.start_time else '',
            'start_time_dt': rec.start_time,
            'duration': rec.duration,
            'msc_id': rec.node_id,
            'cell_id': rec.cell_id,
            'lac': rec.lac,
            'technology': RAT_TYPE_NAMES.get(rec.rat_type, '4G'),
            'status': rec.status,
            'data_volume_mb': rec.data_volume_mb,
        })

    # ----- Combine, sort, paginate ------------------------------------------
    combined.sort(key=lambda r: r.get('start_time_dt') or datetime.min, reverse=True)
    offset = (page - 1) * per_page
    page_records = combined[offset:offset + per_page]
    for r in page_records:
        r.pop('start_time_dt', None)
    pages = (total + per_page - 1) // per_page

    return JsonResponse({
        'success': True,
        'records': page_records,
        'summary': summary,
        'pagination': {'total': total, 'page': page, 'per_page': per_page, 'pages': pages},
    })

