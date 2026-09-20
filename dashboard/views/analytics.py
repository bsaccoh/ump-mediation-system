"""Analytics and traffic matrix."""
from dashboard.views._common import *


# =============================================================================
# Analytics dashboard — Network, Pair-completeness, SDP/Codec analytics
# =============================================================================

@analyst_required
def analytics_view(request):
    """Analytics page (Chart.js renders from /analytics/api/)."""
    return render(request, 'dashboard/analytics.html')


@analyst_required
def analytics_api(request):
    """JSON for the analytics dashboard: network elements + pair KPI + SDP/codec
    + time-series for charts. Range controlled by ?range=1h|6h|24h|7d|30d.
    """
    from django.db.models.functions import TruncHour, TruncDay

    # ----- Time-range parsing -----------------------------------------------
    range_map = {
        '1h':  ('hour', timedelta(hours=1)),
        '6h':  ('hour', timedelta(hours=6)),
        '24h': ('hour', timedelta(hours=24)),
        '7d':  ('day',  timedelta(days=7)),
        '30d': ('day',  timedelta(days=30)),
    }
    rng = (request.GET.get('range') or '24h').lower()
    bucket_kind, window = range_map.get(rng, range_map['24h'])
    since = timezone.now() - window
    trunc = TruncHour('created_at') if bucket_kind == 'hour' else TruncDay('created_at')

    def _series(model_cls):
        rows = (model_cls.objects
                .filter(created_at__gte=since)
                .annotate(bucket=trunc)
                .values('bucket')
                .annotate(count=Count('id'))
                .order_by('bucket'))
        return [{'t': r['bucket'].isoformat(), 'v': r['count']} for r in rows]

    series = {
        'ims':  _series(IMSRecord),
        'msc':  _series(MSCRecord),
        'pgw':  _series(PGWRecord),
        'sgsn': _series(SGSNRecord),
        'sgw':  _series(SGWRecord),
    }

    # File-processing volume time-series
    file_series = list(CDRFile.objects
                       .filter(processing_completed__gte=since,
                               status=CDRFile.Status.COMPLETED)
                       .annotate(bucket=TruncHour('processing_completed') if bucket_kind == 'hour'
                                 else TruncDay('processing_completed'))
                       .values('bucket')
                       .annotate(count=Count('id'), records=Sum('records_total'))
                       .order_by('bucket'))
    files_ts = [{'t': r['bucket'].isoformat(), 'v': r['count'], 'records': r['records'] or 0}
                for r in file_series]

    # ----- Per-stream file lifecycle time-series ----------------------------
    # Three actions × five streams.  For each, bucket by hour/day and split
    # by decoder_type so the frontend can draw multi-series line charts.
    STREAM_KEYS = ['MSC', 'IMS', 'PGW', 'SGSN', 'SGW']

    def _file_series_by_stream(qs, time_field, count_field='id', sum_field=None):
        """Bucket a CDRFile-like queryset by hour/day and group by decoder_type."""
        from django.db.models.functions import TruncHour as _TH, TruncDay as _TD
        trunc_fn = _TH(time_field) if bucket_kind == 'hour' else _TD(time_field)
        annotations = {'count': Count(count_field)}
        if sum_field:
            annotations['records'] = Sum(sum_field)
        rows = list(qs.annotate(bucket=trunc_fn)
                     .values('bucket', 'decoder_type')
                     .annotate(**annotations)
                     .order_by('bucket'))
        out = {s: [] for s in STREAM_KEYS}
        for r in rows:
            s = (r['decoder_type'] or '').upper()
            if s not in out:
                continue
            entry = {'t': r['bucket'].isoformat(), 'v': r['count']}
            if sum_field:
                entry['records'] = r.get('records') or 0
            out[s].append(entry)
        return out

    # 1. Files RECEIVED — every CDRFile created in the window
    files_received_by_stream = _file_series_by_stream(
        CDRFile.objects.filter(created_at__gte=since),
        'created_at', sum_field='records_total',
    )

    # 2. Files PROCESSED — completed files
    files_processed_by_stream = _file_series_by_stream(
        CDRFile.objects.filter(processing_completed__gte=since,
                                status=CDRFile.Status.COMPLETED),
        'processing_completed', sum_field='records_total',
    )

    # 3. Files DISTRIBUTED — successful DistributionLog rows
    from collection.models import DistributionLog
    from django.db.models.functions import TruncHour as _TH, TruncDay as _TD
    dist_trunc = _TH('delivered_at') if bucket_kind == 'hour' else _TD('delivered_at')
    dist_rows = list(DistributionLog.objects
                     .filter(delivered_at__gte=since, status=DistributionLog.Status.SUCCESS)
                     .annotate(bucket=dist_trunc)
                     .values('bucket', 'cdr_file__decoder_type')
                     .annotate(count=Count('id'), records=Sum('record_count'))
                     .order_by('bucket'))
    files_distributed_by_stream = {s: [] for s in STREAM_KEYS}
    for r in dist_rows:
        s = (r['cdr_file__decoder_type'] or '').upper()
        if s in files_distributed_by_stream:
            files_distributed_by_stream[s].append({
                't': r['bucket'].isoformat(),
                'v': r['count'],
                'records': r.get('records') or 0,
            })

    # Processing-error time-series (from ProcessingError)
    from collection.models import ProcessingError
    err_series = list(ProcessingError.objects
                      .filter(created_at__gte=since)
                      .annotate(bucket=trunc)
                      .values('bucket')
                      .annotate(count=Count('id'))
                      .order_by('bucket'))
    errors_ts = [{'t': r['bucket'].isoformat(), 'v': r['count']} for r in err_series]

    # ----- 1. Technology / RAT distribution (IMS only — most reliable source)
    tech_breakdown = (IMSRecord.objects
                      .exclude(technology='')
                      .values('technology')
                      .annotate(count=Count('id'))
                      .order_by('-count'))

    # ----- 2. Top eNodeB IDs (LTE only)
    top_enodebs = (IMSRecord.objects
                   .exclude(enodeb_id='')
                   .values('enodeb_id')
                   .annotate(count=Count('id'))
                   .order_by('-count')[:10])

    # ----- 3. Top cells (any tech)
    top_cells = (IMSRecord.objects
                 .exclude(cell_id='')
                 .values('cell_id', 'technology')
                 .annotate(count=Count('id'))
                 .order_by('-count')[:15])

    # ----- 4. Serving PLMN distribution
    plmn_breakdown = (IMSRecord.objects
                      .exclude(serving_plmn='')
                      .values('serving_plmn')
                      .annotate(count=Count('id'))
                      .order_by('-count')[:10])

    # ----- 5. Pair-completeness KPI: paired vs orphan, per stream
    def _pair_kpi(model_cls, key_field):
        keyed = model_cls.objects.exclude(**{f'{key_field}': ''}).exclude(**{f'{key_field}__isnull': True})
        total = keyed.count()
        paired = keyed.filter(paired_record__isnull=False).count()
        orphan = total - paired
        pct = round(100.0 * paired / total, 1) if total else 0
        return {
            'total_pairable': total,
            'paired': paired,
            'orphan': orphan,
            'pct': pct,
        }
    pair_kpi = {
        'IMS': _pair_kpi(IMSRecord, 'icid'),
        'MSC': _pair_kpi(MSCRecord, 'call_reference'),
    }

    # ----- 6. SDP codec analytics (IMS only)
    codec_breakdown = (IMSRecord.objects
                       .exclude(codec='')
                       .values('codec')
                       .annotate(count=Count('id'))
                       .order_by('-count')[:10])

    media_breakdown = (IMSRecord.objects
                       .exclude(media_type='')
                       .values('media_type')
                       .annotate(count=Count('id'))
                       .order_by('-count'))

    # ----- 7. SIP method distribution
    sip_method_breakdown = (IMSRecord.objects
                            .exclude(sip_method='')
                            .values('sip_method')
                            .annotate(count=Count('id'))
                            .order_by('-count')[:10])

    # ----- 8. Call type distribution (IMS VoLTE Voice / SMS / Event)
    call_type_breakdown = (IMSRecord.objects
                           .exclude(call_type='')
                           .values('call_type')
                           .annotate(count=Count('id'))
                           .order_by('-count'))

    # ----- 9. Processing health
    from collection.models import ProcessingError
    total_files = CDRFile.objects.count()
    failed_files = CDRFile.objects.filter(status=CDRFile.Status.FAILED).count()
    completed_files = CDRFile.objects.filter(status=CDRFile.Status.COMPLETED).count()
    processing_errors = ProcessingError.objects.count()

    # ----- 10. Top error classes (last 7 days)
    last_7d = timezone.now() - timedelta(days=7)
    top_errors = (ProcessingError.objects
                  .filter(created_at__gte=last_7d)
                  .values('error_class', 'stage')
                  .annotate(count=Count('id'))
                  .order_by('-count')[:10])

    return JsonResponse({
        'range':              rng,
        'bucket':             bucket_kind,
        'series':             series,
        'files_ts':           files_ts,
        'errors_ts':          errors_ts,
        'files_received_by_stream':    files_received_by_stream,
        'files_processed_by_stream':   files_processed_by_stream,
        'files_distributed_by_stream': files_distributed_by_stream,
        'tech_breakdown':     list(tech_breakdown),
        'plmn_breakdown':     list(plmn_breakdown),
        'top_enodebs':        list(top_enodebs),
        'top_cells':          list(top_cells),
        'pair_kpi':           pair_kpi,
        'codec_breakdown':    list(codec_breakdown),
        'media_breakdown':    list(media_breakdown),
        'sip_method_breakdown': list(sip_method_breakdown),
        'call_type_breakdown':  list(call_type_breakdown),
        'health': {
            'total_files':       total_files,
            'completed_files':   completed_files,
            'failed_files':      failed_files,
            'processing_errors': processing_errors,
            'failure_rate':      round(100.0 * failed_files / total_files, 1) if total_files else 0,
        },
        'top_errors': list(top_errors),
    })


# =============================================================================
# Traffic Matrix — inter-operator volume from MSC records
# =============================================================================

# Operator classification is now in core/utils/operators.py so the
# interconnect billing module can re-use it.  Keep aliases here so existing
# call sites in this file don't need to change.
from core.utils.operators import (
    classify_operator as _classify_operator,
    SL_OPERATOR_PREFIX_MAP,
)


@analyst_required
def traffic_matrix_view(request):
    """Traffic Matrix page — operator-to-operator call volumes."""
    return render(request, 'dashboard/traffic_matrix.html')


@analyst_required
def traffic_matrix_api(request):
    """Build operator-to-operator call/SMS matrices from MSC records.

    Range controlled by ?range=24h|7d|30d|all.  Returns:
      voice_matrix   - dict[operator_from][operator_to] = call count
      voice_minutes  - dict[from][to] = total duration in minutes
      sms_matrix     - dict[from][to] = SMS count
      operators      - ordered list of operators (rows/cols)
    """
    from streams.msc.models import MSCRecord
    from datetime import timedelta

    rng = (request.GET.get('range') or '24h').lower()
    window_map = {
        '24h': timedelta(hours=24),
        '7d':  timedelta(days=7),
        '30d': timedelta(days=30),
        'all': None,
    }
    window = window_map.get(rng, window_map['24h'])
    since = timezone.now() - window if window else None

    # Pull only the fields we need (avoid loading raw_data JSON)
    base_qs = MSCRecord.objects.values(
        'record_type', 'calling_number', 'called_number', 'duration'
    )
    if since:
        base_qs = base_qs.filter(start_time__gte=since)

    # Voice: MOC + MTC + GWI + GWO (one row per call leg).  Counted as ONE call
    # per (calling, called) pair to avoid double-counting MOC+MTC pairs of the
    # same Orange-to-Orange call.  Quick approximation: use MOC + (MTC - on-net)
    # so we don't double-count.  For correctness in mixed datasets we just sum
    # MOC + GWI + GWO (all distinct directions) — MTCs that have a matching
    # MOC in the dataset would otherwise double-count.
    voice_types = ['MOC', 'GWO', 'GWI']
    sms_types   = ['SMSMO', 'SMSMT']

    voice = {}        # {from_op: {to_op: count}}
    voice_minutes = {}
    sms = {}
    operators_seen = set()

    for r in base_qs.filter(record_type__in=voice_types).iterator(chunk_size=5000):
        fo = _classify_operator(r['calling_number'])
        to = _classify_operator(r['called_number'])
        operators_seen.add(fo); operators_seen.add(to)
        voice.setdefault(fo, {}).setdefault(to, 0)
        voice[fo][to] += 1
        voice_minutes.setdefault(fo, {}).setdefault(to, 0)
        voice_minutes[fo][to] += (r['duration'] or 0)

    for r in base_qs.filter(record_type__in=sms_types).iterator(chunk_size=5000):
        fo = _classify_operator(r['calling_number'])
        to = _classify_operator(r['called_number'])
        operators_seen.add(fo); operators_seen.add(to)
        sms.setdefault(fo, {}).setdefault(to, 0)
        sms[fo][to] += 1

    # Order operators with Orange first, then SL operators, then others
    SL_ORDER = ['Orange', 'Africell', 'Qcell', 'Smart', 'Sierratel',
                'Other SL', 'International', 'Short Code', 'Alphanumeric', 'Unknown']
    operators = [op for op in SL_ORDER if op in operators_seen]
    # Append any unexpected operators
    for op in sorted(operators_seen):
        if op not in operators:
            operators.append(op)

    # Voice minutes → integer minutes for display
    voice_minutes_display = {fo: {to: round(v / 60) for to, v in row.items()}
                              for fo, row in voice_minutes.items()}

    # Grand totals for the summary strip
    total_voice = sum(c for row in voice.values() for c in row.values())
    total_sms   = sum(c for row in sms.values()   for c in row.values())
    total_minutes = sum(v for row in voice_minutes.values() for v in row.values()) // 60

    # Per-row outgoing & per-column incoming totals (for KPI cards)
    by_from_voice = {op: sum(voice.get(op, {}).values()) for op in operators}
    by_to_voice   = {op: sum(row.get(op, 0) for row in voice.values()) for op in operators}
    by_from_sms   = {op: sum(sms.get(op, {}).values())   for op in operators}
    by_to_sms     = {op: sum(row.get(op, 0) for row in sms.values())   for op in operators}

    return JsonResponse({
        'range': rng,
        'operators': operators,
        'voice_matrix':   voice,
        'voice_minutes':  voice_minutes_display,
        'sms_matrix':     sms,
        'totals': {
            'voice_calls':    total_voice,
            'voice_minutes':  total_minutes,
            'sms_messages':   total_sms,
        },
        'by_from_voice':  by_from_voice,
        'by_to_voice':    by_to_voice,
        'by_from_sms':    by_from_sms,
        'by_to_sms':      by_to_sms,
    })


