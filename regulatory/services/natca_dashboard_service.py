"""
NatCA Dashboard Service Layer

Business logic for aggregating dashboard data from TrafficSummary and other models.
"""
from datetime import date, datetime, timedelta

from django.db.models import Max, Min, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek

from reference.models import Operator
from regulatory.models import RiskAlert, Tariff, TrafficSummary


class NatCADashboardService:

    def __init__(self, operator=None, start_date=None, end_date=None,
                 period=None, trend_granularity=None):
        self.operator = operator
        self._explicit_granularity = trend_granularity
        self._compute_date_range(start_date, end_date, period)
        # Auto-detect granularity based on date range when not explicitly set
        if trend_granularity:
            self.trend_granularity = trend_granularity
        else:
            days = (self.end_date - self.start_date).days
            if days <= 31:
                self.trend_granularity = 'daily'
            elif days <= 180:
                self.trend_granularity = 'weekly'
            else:
                self.trend_granularity = 'monthly'

    # ------------------------------------------------------------------
    # Date range
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(val):
        if not val:
            return None
        try:
            return datetime.strptime(val, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    def _compute_date_range(self, start_str, end_str, period):
        self.end_date = self._parse_date(end_str) or date.today()

        if start_str:
            self.start_date = self._parse_date(start_str) or self.end_date - timedelta(days=30)
        elif period == 'today':
            self.start_date = date.today()
            self.end_date = date.today()
        elif period and period.isdigit():
            self.start_date = self.end_date - timedelta(days=int(period))
        elif period == 'all' or period is None:
            agg = TrafficSummary.objects.aggregate(
                min_d=Min('period_start'), max_d=Max('period_start'),
            )
            if agg['min_d']:
                self.start_date = agg['min_d'].date()
                self.end_date = max(agg['max_d'].date(), date.today())
            else:
                self.start_date = self.end_date - timedelta(days=30)
        else:
            self.start_date = self.end_date - timedelta(days=30)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def _base_qs(self):
        qs = TrafficSummary.objects.filter(
            period_start__date__gte=self.start_date,
            period_start__date__lte=self.end_date,
        )
        if self.operator:
            qs = qs.filter(operator_code=self.operator)
        return qs

    @staticmethod
    def _auto_scale(value, digits=1):
        val = float(value or 0)
        av = abs(val)
        if av >= 1_000_000_000:
            return f'{val / 1_000_000_000:,.{digits}f}', 'B'
        if av >= 1_000_000:
            return f'{val / 1_000_000:,.{digits}f}', 'M'
        if av >= 1_000:
            return f'{val / 1_000:,.{digits}f}', 'K'
        return f'{val:,.{digits}f}', ''

    @staticmethod
    def _scale_bytes(total_bytes, digits=1):
        b = float(total_bytes or 0)
        if b >= 1024 ** 4:
            return f'{b / 1024 ** 4:,.{digits}f}', 'TB'
        if b >= 1024 ** 3:
            return f'{b / 1024 ** 3:,.{digits}f}', 'GB'
        if b >= 1024 ** 2:
            return f'{b / 1024 ** 2:,.{digits}f}', 'MB'
        if b >= 1024:
            return f'{b / 1024:,.{digits}f}', 'KB'
        if b == 0:
            return '0.0', 'GB'
        return f'{b:,.0f}', 'B'

    @staticmethod
    def _change(current, prior):
        if not prior:
            return 0
        return round(float((current - prior) * 100 / prior), 1)

    # ------------------------------------------------------------------
    # Dashboard data
    # ------------------------------------------------------------------

    def get_dashboard_data(self):
        return {
            'kpis': self.get_kpis(),
            'traffic_trend': self.get_traffic_trend(),
            'traffic_by_operator': self.get_traffic_by_operator(),
            'service_contribution': self.get_service_contribution(),
            'operator_performance': self.get_operator_performance(),
            'alerts': self.get_recent_alerts(),
            'issues': self.get_top_issues(),
            'compliance': self.get_compliance_status(),
            'anomalies': self.get_traffic_anomalies(),
            'activities': self.get_upcoming_activities(),
            'date_range': {
                'start': self.start_date.strftime('%d %b %Y'),
                'end': self.end_date.strftime('%d %b %Y'),
            },
        }

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------

    def get_kpis(self):
        qs = self._base_qs()

        # Previous period for comparison
        period_len = max((self.end_date - self.start_date).days, 1)
        prev_end = self.start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_len)
        prev_qs = TrafficSummary.objects.filter(
            period_start__date__gte=prev_start,
            period_start__date__lte=prev_end,
        )
        if self.operator:
            prev_qs = prev_qs.filter(operator_code=self.operator)

        # Voice
        voice_secs = qs.filter(service_type='VOICE').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        voice_mins = voice_secs / 60
        prev_voice_secs = prev_qs.filter(service_type='VOICE').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        v_val, v_unit = self._auto_scale(voice_mins)

        # SMS
        sms_total = qs.filter(service_type='SMS').aggregate(
            t=Sum('sms_count'))['t'] or 0
        prev_sms = prev_qs.filter(service_type='SMS').aggregate(
            t=Sum('sms_count'))['t'] or 0
        s_val, s_unit = self._auto_scale(sms_total)

        # Data
        data_agg = qs.filter(service_type='DATA').aggregate(
            up=Sum('data_volume_bytes_up'), dn=Sum('data_volume_bytes_down'))
        data_bytes = (data_agg['up'] or 0) + (data_agg['dn'] or 0)
        prev_data = prev_qs.filter(service_type='DATA').aggregate(
            up=Sum('data_volume_bytes_up'), dn=Sum('data_volume_bytes_down'))
        prev_data_bytes = (prev_data['up'] or 0) + (prev_data['dn'] or 0)
        d_val, d_unit = self._scale_bytes(data_bytes)

        # International
        intl_secs = qs.filter(traffic_type='INTERNATIONAL').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        intl_mins = intl_secs / 60
        prev_intl = prev_qs.filter(traffic_type='INTERNATIONAL').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        i_val, i_unit = self._auto_scale(intl_mins)

        # Roaming
        roam_secs = qs.filter(traffic_type='ROAMING').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        roam_mins = roam_secs / 60
        prev_roam = prev_qs.filter(traffic_type='ROAMING').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        r_val, r_unit = self._auto_scale(roam_mins)

        # Interconnect
        inter_secs = qs.filter(traffic_type='INTERCONNECT').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        inter_mins = inter_secs / 60
        prev_inter = prev_qs.filter(traffic_type='INTERCONNECT').aggregate(
            t=Sum('total_duration_seconds'))['t'] or 0
        ic_val, ic_unit = self._auto_scale(inter_mins)

        # Operators / tariff / alerts
        active_operators = Operator.objects.filter(enabled=True).count()
        active_tariffs = Tariff.objects.filter(status='ACTIVE').count()
        total_tariffs = Tariff.objects.count()
        tariff_compliance = (active_tariffs / total_tariffs * 100) if total_tariffs else 0
        open_alerts = RiskAlert.objects.filter(status='OPEN').count()

        return {
            'total_subscribers': {'value': 'N/A', 'change': 0, 'unit': ''},
            'voice': {
                'value': f'{v_val}{v_unit}',
                'change': self._change(voice_secs, prev_voice_secs),
                'unit': 'Mins',
            },
            'sms': {
                'value': f'{s_val}{s_unit}',
                'change': self._change(sms_total, prev_sms),
                'unit': 'Messages',
            },
            'data': {
                'value': f'{d_val}',
                'change': self._change(data_bytes, prev_data_bytes),
                'unit': d_unit,
            },
            'international': {
                'value': f'{i_val}{i_unit}',
                'change': self._change(intl_secs, prev_intl),
                'unit': 'Mins',
            },
            'roaming': {
                'value': f'{r_val}{r_unit}',
                'change': self._change(roam_secs, prev_roam),
                'unit': 'Mins',
            },
            'interconnect': {
                'value': f'{ic_val}{ic_unit}',
                'change': self._change(inter_secs, prev_inter),
                'unit': 'Mins',
            },
            'tariff_compliance': {
                'value': f'{tariff_compliance:.1f}',
                'unit': '%',
                'status': 'Good' if tariff_compliance >= 90 else 'Fair' if tariff_compliance >= 70 else 'Poor',
            },
            'open_complaints': {'value': open_alerts, 'change': 0},
            'qos_compliance': {'value': 'N/A', 'unit': '%', 'change': 0},
            'spectrum_utilization': {'value': 'N/A', 'unit': '%', 'change': 0},
            'active_operators': {'value': active_operators, 'change': 0},
        }

    # ------------------------------------------------------------------
    # Traffic Trend
    # ------------------------------------------------------------------

    def get_traffic_trend(self):
        trunc_map = {
            'daily': (TruncDay, '%d %b'),
            'weekly': (TruncWeek, '%d %b'),
            'monthly': (TruncMonth, '%b %Y'),
        }
        trunc_fn, fmt = trunc_map.get(self.trend_granularity, trunc_map['monthly'])

        qs = self._base_qs().annotate(
            period=trunc_fn('period_start'),
        ).values('period', 'service_type').annotate(
            total_duration=Sum('total_duration_seconds'),
            total_sms=Sum('sms_count'),
            total_data_up=Sum('data_volume_bytes_up'),
            total_data_down=Sum('data_volume_bytes_down'),
        ).order_by('period')

        rows = list(qs)
        if not rows:
            return {'labels': [], 'voice': [], 'sms': [], 'data': []}

        periods = sorted(set(r['period'] for r in rows))
        labels = [p.strftime(fmt) for p in periods]
        voice_data, sms_data, data_data = [], [], []

        for p in periods:
            items = [r for r in rows if r['period'] == p]
            voice = sum(i['total_duration'] or 0 for i in items if i['service_type'] == 'VOICE') / 60
            sms = sum(i['total_sms'] or 0 for i in items if i['service_type'] == 'SMS')
            d_bytes = sum(
                (i['total_data_up'] or 0) + (i['total_data_down'] or 0)
                for i in items if i['service_type'] == 'DATA'
            )
            voice_data.append(round(voice, 1))
            sms_data.append(round(sms, 0))
            data_data.append(round(d_bytes / (1024 ** 3), 2))

        return {
            'labels': labels,
            'voice': voice_data,
            'sms': sms_data,
            'data': data_data,
        }

    # ------------------------------------------------------------------
    # Traffic by Operator
    # ------------------------------------------------------------------

    def get_traffic_by_operator(self):
        qs = self._base_qs()
        operator_data = qs.values('operator_code').annotate(
            total_records=Sum('record_count'),
        ).order_by('-total_records')

        total = sum(item['total_records'] or 0 for item in operator_data)
        names = dict(Operator.objects.values_list('code', 'name'))

        result = []
        for item in operator_data:
            records = item['total_records'] or 0
            pct = (records / total * 100) if total else 0
            result.append({
                'name': names.get(item['operator_code'], item['operator_code']),
                'percentage': round(pct, 1),
            })
        return result

    # ------------------------------------------------------------------
    # Service Contribution
    # ------------------------------------------------------------------

    def get_service_contribution(self):
        qs = self._base_qs()
        if not qs.exists():
            return {
                'labels': ['Voice', 'SMS', 'Data', 'International', 'Roaming', 'Interconnect'],
                'values': [0, 0, 0, 0, 0, 0],
            }

        def _count(filt):
            return qs.filter(**filt).aggregate(t=Sum('record_count'))['t'] or 0

        return {
            'labels': ['Voice', 'SMS', 'Data', 'International', 'Roaming', 'Interconnect'],
            'values': [
                _count({'service_type': 'VOICE'}),
                _count({'service_type': 'SMS'}),
                _count({'service_type': 'DATA'}),
                _count({'traffic_type': 'INTERNATIONAL'}),
                _count({'traffic_type': 'ROAMING'}),
                _count({'traffic_type': 'INTERCONNECT'}),
            ],
        }

    # ------------------------------------------------------------------
    # Operator Performance
    # ------------------------------------------------------------------

    def get_operator_performance(self):
        operators = Operator.objects.filter(enabled=True)
        result = []

        for op in operators:
            qs = self._base_qs().filter(operator_code=op.code)

            # Voice
            voice_secs = qs.filter(service_type='VOICE').aggregate(
                t=Sum('total_duration_seconds'))['t'] or 0
            voice_mins = voice_secs / 60
            v_val, v_unit = self._auto_scale(voice_mins)

            # SMS
            sms_count = qs.filter(service_type='SMS').aggregate(
                t=Sum('sms_count'))['t'] or 0
            s_val, s_unit = self._auto_scale(sms_count)

            # Data
            data_agg = qs.filter(service_type='DATA').aggregate(
                up=Sum('data_volume_bytes_up'), dn=Sum('data_volume_bytes_down'))
            data_bytes = (data_agg['up'] or 0) + (data_agg['dn'] or 0)
            d_val, d_unit = self._scale_bytes(data_bytes)

            # Tariff compliance
            op_tariffs = Tariff.objects.filter(operator_code=op.code)
            if op_tariffs.exists():
                active = op_tariffs.filter(status='ACTIVE').count()
                total = op_tariffs.count()
                tariff = f'{active / total * 100:.1f}%' if total else 'N/A'
            else:
                tariff = 'N/A'

            result.append({
                'operator': op.name,
                'subscribers': 'N/A',
                'voice': f'{v_val}{v_unit} Mins',
                'sms': f'{s_val}{s_unit}',
                'data': f'{d_val} {d_unit}'.strip(),
                'qos': 'N/A',
                'tariff': tariff,
            })

        return result

    # ------------------------------------------------------------------
    # Alerts & secondary panels
    # ------------------------------------------------------------------

    def get_recent_alerts(self):
        try:
            alerts = RiskAlert.objects.filter(status='OPEN').order_by('-triggered_at')[:5]
            return [
                {
                    'severity': self._map_severity(a.severity),
                    'message': a.description,
                    'time': self._format_time_ago(a.triggered_at),
                }
                for a in alerts
            ]
        except Exception:
            return []

    def get_top_issues(self):
        return []

    def get_compliance_status(self):
        return []

    def get_traffic_anomalies(self):
        return []

    def get_upcoming_activities(self):
        return []

    # ------------------------------------------------------------------
    # International Traffic
    # ------------------------------------------------------------------

    def get_international_data(self):
        qs = self._base_qs().filter(traffic_type='INTERNATIONAL')
        agg = qs.aggregate(calls=Sum('call_count'), secs=Sum('total_duration_seconds'), sms=Sum('sms_count'))
        total_calls = agg['calls'] or 0
        total_secs  = agg['secs']  or 0
        total_sms   = agg['sms']   or 0
        countries   = qs.exclude(destination_country='').values('destination_country').distinct().count()

        c_val, c_unit = self._auto_scale(total_calls)
        m_val, m_unit = self._auto_scale(total_secs / 60)
        s_val, s_unit = self._auto_scale(total_sms)

        kpis = {
            'intl_calls':     {'value': f'{c_val}{c_unit}', 'unit': 'Calls'},
            'intl_minutes':   {'value': f'{m_val}{m_unit}', 'unit': 'Mins'},
            'intl_sms':       {'value': f'{s_val}{s_unit}', 'unit': 'SMS'},
            'intl_countries': {'value': countries,          'unit': 'Countries'},
        }

        trend = self._trend_by_direction('INTERNATIONAL')

        by_dir_qs = list(
            qs.exclude(destination_country='')
            .values('destination_country', 'operator_code', 'direction')
            .annotate(calls=Sum('call_count'), mins=Sum('total_duration_seconds'), sms=Sum('sms_count'))
            .order_by('-calls')[:100]
        )

        names = dict(Operator.objects.values_list('code', 'name'))
        country_map = {}
        for row in by_dir_qs:
            key = (row['destination_country'], row['operator_code'])
            if key not in country_map:
                country_map[key] = {
                    'country': row['destination_country'],
                    'operator': names.get(row['operator_code'], row['operator_code']),
                    'outbound_calls': 0, 'outbound_minutes': 0, 'outbound_sms': 0,
                    'inbound_calls': 0,  'inbound_minutes': 0,
                }
            e = country_map[key]
            calls = row['calls'] or 0
            mins  = round((row['mins'] or 0) / 60, 1)
            if row['direction'] == 'ORIGINATING':
                e['outbound_calls'] += calls; e['outbound_minutes'] += mins; e['outbound_sms'] += row['sms'] or 0
            else:
                e['inbound_calls']  += calls; e['inbound_minutes']  += mins

        rows = sorted(country_map.values(), key=lambda x: x['outbound_calls'], reverse=True)
        top_dest   = sorted(rows, key=lambda x: x['outbound_calls'], reverse=True)[:10]
        top_origin = sorted(rows, key=lambda x: x['inbound_calls'],  reverse=True)[:10]

        return {
            'kpis': kpis,
            'trend': trend,
            'direction_totals':   [sum(v['outbound_calls'] for v in rows), sum(v['inbound_calls'] for v in rows)],
            'top_destinations':   [r['country'] for r in top_dest],
            'top_dest_minutes':   [r['outbound_minutes'] for r in top_dest],
            'top_origins':        [r['country'] for r in top_origin],
            'top_orig_minutes':   [r['inbound_minutes'] for r in top_origin],
            'intl_by_country':    rows,
        }

    # ------------------------------------------------------------------
    # Interconnect Traffic
    # ------------------------------------------------------------------

    def get_interconnect_data(self):
        qs   = self._base_qs().filter(traffic_type='INTERCONNECT')
        agg  = qs.aggregate(calls=Sum('call_count'), secs=Sum('total_duration_seconds'))
        a_in = qs.filter(direction='TERMINATING').aggregate(calls=Sum('call_count'))
        a_out= qs.filter(direction='ORIGINATING').aggregate(calls=Sum('call_count'))

        total_calls = agg['calls']   or 0
        total_secs  = agg['secs']    or 0
        in_calls    = a_in['calls']  or 0
        out_calls   = a_out['calls'] or 0

        c_val, c_unit = self._auto_scale(total_calls)
        m_val, m_unit = self._auto_scale(total_secs / 60)
        i_val, i_unit = self._auto_scale(in_calls)
        o_val, o_unit = self._auto_scale(out_calls)

        kpis = {
            'interconnect_calls':   {'value': f'{c_val}{c_unit}', 'unit': 'Calls'},
            'interconnect_minutes': {'value': f'{m_val}{m_unit}', 'unit': 'Mins'},
            'inbound_interconnect': {'value': f'{i_val}{i_unit}', 'unit': 'Calls'},
            'outbound_interconnect':{'value': f'{o_val}{o_unit}', 'unit': 'Calls'},
        }

        trend = self._trend_by_direction('INTERCONNECT')

        names = dict(Operator.objects.values_list('code', 'name'))

        pairs_qs = list(
            qs.filter(direction='ORIGINATING')
            .values('operator_code', 'destination_operator')
            .annotate(calls=Sum('call_count'), mins=Sum('total_duration_seconds'), sms=Sum('sms_count'))
            .order_by('-calls')[:20]
        )
        total_pair_calls = sum(p['calls'] or 0 for p in pairs_qs)
        interconnect_pairs = [
            {
                'originating': names.get(p['operator_code'],       p['operator_code']),
                'terminating': names.get(p['destination_operator'], p['destination_operator'] or 'Unknown'),
                'calls':   p['calls'] or 0,
                'minutes': round((p['mins'] or 0) / 60, 1),
                'sms':     p['sms'] or 0,
                'share':   round((p['calls'] or 0) / total_pair_calls * 100, 1) if total_pair_calls else 0,
            }
            for p in pairs_qs
        ]

        op_qs = list(qs.values('operator_code').annotate(calls=Sum('call_count')).order_by('-calls')[:6])
        total_op = sum(r['calls'] or 0 for r in op_qs)
        operator_share = [
            {'name': names.get(r['operator_code'], r['operator_code']),
             'pct':  round((r['calls'] or 0) / total_op * 100, 1) if total_op else 0}
            for r in op_qs
        ]

        return {
            'kpis': kpis,
            'trend': trend,
            'interconnect_pairs': interconnect_pairs,
            'operator_share':     operator_share,
            'op_labels':  [r['name'] for r in operator_share],
            'op_pcts':    [r['pct']  for r in operator_share],
        }

    # ------------------------------------------------------------------
    # Roaming Traffic
    # ------------------------------------------------------------------

    def get_roaming_data(self):
        qs    = self._base_qs().filter(traffic_type='ROAMING')
        a_in  = qs.filter(direction='TERMINATING').aggregate(calls=Sum('call_count'), secs=Sum('total_duration_seconds'))
        a_out = qs.filter(direction='ORIGINATING').aggregate(calls=Sum('call_count'), secs=Sum('total_duration_seconds'))
        all_secs = (a_in['secs'] or 0) + (a_out['secs'] or 0)
        m_val, m_unit = self._auto_scale(all_secs / 60)

        kpis = {
            'roaming_subscribers': {'value': qs.values('operator_code').distinct().count(), 'unit': 'Operators'},
            'inbound_roaming':     {'value': a_in['calls']  or 0, 'unit': 'Calls'},
            'outbound_roaming':    {'value': a_out['calls'] or 0, 'unit': 'Calls'},
            'roaming_minutes':     {'value': f'{m_val}{m_unit}',  'unit': 'Mins'},
        }

        trend = self._trend_by_direction('ROAMING')

        names = dict(Operator.objects.values_list('code', 'name'))
        by_dir = list(
            qs.exclude(destination_country='')
            .values('destination_country', 'operator_code', 'direction')
            .annotate(calls=Sum('call_count'), mins=Sum('total_duration_seconds'), sms=Sum('sms_count'))
            .order_by('-calls')[:100]
        )
        country_map = {}
        for row in by_dir:
            key = (row['destination_country'], row['operator_code'])
            if key not in country_map:
                country_map[key] = {
                    'country': row['destination_country'],
                    'operator': names.get(row['operator_code'], row['operator_code']),
                    'inbound_calls': 0,  'inbound_minutes': 0,  'inbound_sms': 0,
                    'outbound_calls': 0, 'outbound_minutes': 0, 'outbound_sms': 0,
                }
            e = country_map[key]
            calls = row['calls'] or 0; mins = round((row['mins'] or 0) / 60, 1); sms = row['sms'] or 0
            if row['direction'] == 'TERMINATING':
                e['inbound_calls']   += calls; e['inbound_minutes']  += mins; e['inbound_sms']  += sms
            else:
                e['outbound_calls']  += calls; e['outbound_minutes'] += mins; e['outbound_sms'] += sms

        rows     = sorted(country_map.values(), key=lambda x: x['inbound_calls'] + x['outbound_calls'], reverse=True)
        in_top   = sorted(rows, key=lambda x: x['inbound_calls'],  reverse=True)[:10]
        out_top  = sorted(rows, key=lambda x: x['outbound_calls'], reverse=True)[:10]

        op_qs   = list(qs.values('operator_code').annotate(calls=Sum('call_count')).order_by('-calls')[:4])
        total_op = sum(r['calls'] or 0 for r in op_qs)

        return {
            'kpis': kpis,
            'trend': trend,
            'roaming_by_country':       rows,
            'inbound_country_labels':   [r['country']       for r in in_top],
            'inbound_country_values':   [r['inbound_calls']  for r in in_top],
            'outbound_country_labels':  [r['country']       for r in out_top],
            'outbound_country_values':  [r['outbound_calls'] for r in out_top],
            'operator_labels':  [names.get(r['operator_code'], r['operator_code']) for r in op_qs],
            'operator_pcts':    [round((r['calls'] or 0) / total_op * 100, 1) if total_op else 0 for r in op_qs],
            'direction_totals': [a_in['calls'] or 0, a_out['calls'] or 0],
        }

    # ------------------------------------------------------------------
    # Shared trend helper
    # ------------------------------------------------------------------

    def _trend_by_direction(self, traffic_type):
        trunc_map = {
            'daily':   (TruncDay,   '%d %b'),
            'weekly':  (TruncWeek,  '%d %b'),
            'monthly': (TruncMonth, '%b %Y'),
        }
        trunc_fn, fmt = trunc_map.get(self.trend_granularity, trunc_map['monthly'])

        qs = self._base_qs().filter(traffic_type=traffic_type).annotate(
            period=trunc_fn('period_start'),
        ).values('period', 'direction').annotate(
            calls=Sum('call_count'),
        ).order_by('period')

        rows = list(qs)
        if not rows:
            return {'labels': [], 'outbound': [], 'inbound': []}

        periods = sorted(set(r['period'] for r in rows))
        labels, outbound, inbound = [], [], []
        for p in periods:
            items = [r for r in rows if r['period'] == p]
            labels.append(p.strftime(fmt))
            outbound.append(sum(r['calls'] or 0 for r in items if r['direction'] == 'ORIGINATING'))
            inbound.append( sum(r['calls'] or 0 for r in items if r['direction'] == 'TERMINATING'))

        return {'labels': labels, 'outbound': outbound, 'inbound': inbound}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _map_severity(severity):
        return {'CRITICAL': 'danger', 'HIGH': 'danger', 'MEDIUM': 'warning',
                'LOW': 'info', 'INFO': 'primary'}.get(severity, 'info')

    @staticmethod
    def _format_time_ago(dt):
        if not dt:
            return 'Unknown'
        from django.utils import timezone
        diff = timezone.now() - dt
        if diff.days > 0:
            return f'{diff.days} days ago'
        if diff.seconds > 3600:
            return f'{diff.seconds // 3600} hours ago'
        if diff.seconds > 60:
            return f'{diff.seconds // 60} minutes ago'
        return 'Just now'
