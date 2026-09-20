"""
Risk Engine Service

Evaluates configurable risk rules against current mediation/revenue/tax data,
creates and deduplicates RiskAlerts, and drives the alert investigation
workflow (assign, investigate, request operator response, resolve, dismiss,
escalate to an audit case).
"""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AuditLog
from regulatory.models.risk import RiskRule, RiskRuleVersion, RiskAlert, RiskAlertOperatorResponse, RiskAlertComment
from regulatory.models.aggregates import RatedAggregate
from regulatory.models.traffic import TrafficSummary
from regulatory.services.cdr_lookup import get_masked_cdrs

logger = logging.getLogger(__name__)

# Maps a rule's alert_type to the RiskAlert.SourceType it should be attributed to
# when the engine (rather than a human) creates the alert automatically.
ALERT_TYPE_SOURCE_MAP = {
    RiskRule.AlertType.TAX: RiskAlert.SourceType.GST_ENGINE,
    RiskRule.AlertType.GST: RiskAlert.SourceType.GST_ENGINE,
    RiskRule.AlertType.REVENUE: RiskAlert.SourceType.REVENUE_ASSURANCE,
    RiskRule.AlertType.TARIFF: RiskAlert.SourceType.TARIFF_COMPLIANCE,
    RiskRule.AlertType.DECLARATION: RiskAlert.SourceType.OPERATOR_DECLARATIONS,
    RiskRule.AlertType.RECONCILIATION: RiskAlert.SourceType.RECONCILIATION,
    RiskRule.AlertType.DATA_QUALITY: RiskAlert.SourceType.MEDIATION_DATA_QUALITY,
    RiskRule.AlertType.TRAFFIC: RiskAlert.SourceType.TRAFFIC_MONITORING,
    RiskRule.AlertType.COMPLIANCE: RiskAlert.SourceType.TARIFF_COMPLIANCE,
    RiskRule.AlertType.OTHER: RiskAlert.SourceType.MANUAL_RULE,
}

SORT_FIELDS = {
    'alert_id': 'reference', '-alert_id': '-reference',
    'operator': 'operator_code', '-operator': '-operator_code',
    'severity': 'severity', '-severity': '-severity',
    'metric_value': 'metric_value', '-metric_value': '-metric_value',
    'status': 'status', '-status': '-status',
    'triggered': 'triggered_at', '-triggered': '-triggered_at',
    'exposure': 'potential_exposure', '-exposure': '-potential_exposure',
}


class RiskEngine:
    """Evaluates risk rules and manages the risk-alert lifecycle."""

    # ---- Read helpers -------------------------------------------------

    def get_summary(self, queryset=None) -> Dict[str, int]:
        qs = queryset if queryset is not None else RiskAlert.objects.all()
        return {
            'total': qs.count(),
            'open': qs.filter(status=RiskAlert.Status.OPEN).count(),
            'investigating': qs.filter(status=RiskAlert.Status.INVESTIGATING).count(),
            'resolved': qs.filter(status=RiskAlert.Status.RESOLVED).count(),
        }

    def get_alerts(self, filters: Optional[Dict[str, Any]] = None):
        filters = filters or {}
        qs = RiskAlert.objects.select_related('rule', 'assigned_to').all()

        if filters.get('operator'):
            qs = qs.filter(operator_code=filters['operator'])
        if filters.get('severity'):
            qs = qs.filter(severity=filters['severity'])
        if filters.get('status'):
            qs = qs.filter(status=filters['status'])
        if filters.get('alert_type'):
            qs = qs.filter(alert_type=filters['alert_type'])
        if filters.get('start_date'):
            qs = qs.filter(triggered_at__date__gte=filters['start_date'])
        if filters.get('end_date'):
            qs = qs.filter(triggered_at__date__lte=filters['end_date'])
        if filters.get('search'):
            term = filters['search']
            qs = qs.filter(
                Q(reference__icontains=term) | Q(operator_code__icontains=term) |
                Q(rule__name__icontains=term) | Q(alert_type__icontains=term) |
                Q(status__icontains=term) | Q(description__icontains=term) |
                Q(source_reference__icontains=term)
            )
        qs = qs.order_by(SORT_FIELDS.get(filters.get('sort'), '-triggered_at'), '-pk')
        return qs

    def get_alert(self, identifier):
        """Look up an alert by numeric pk or by its human reference (e.g. RA-2026-0024)."""
        qs = RiskAlert.objects.select_related('rule', 'assigned_to', 'investigator', 'resolved_by', 'dismissed_by', 'audit_case')
        identifier = str(identifier)
        if identifier.isdigit():
            return get_object_or_404(qs, pk=identifier)
        return get_object_or_404(qs, reference=identifier)

    def get_alert_detail(self, identifier):
        return self.get_alert(identifier)

    def get_evidence(self, alert: RiskAlert) -> List[Dict[str, str]]:
        """Return the traceability chain from the alert back to its source data."""
        chain = [{'label': 'Risk Alert', 'reference': alert.reference or str(alert.pk)}]
        source_label = alert.get_source_type_display() if hasattr(alert, 'get_source_type_display') else alert.source_type
        chain.append({'label': source_label, 'reference': alert.source_reference or '—'})
        if alert.source_type == RiskAlert.SourceType.RECONCILIATION:
            chain.append({'label': 'Operator Declaration', 'reference': alert.operator_code})
            chain.append({'label': 'Aggregated Records / CDR Source', 'reference': f'{alert.period_start or "—"} → {alert.period_end or "—"}'})
        elif alert.source_type == RiskAlert.SourceType.TARIFF_COMPLIANCE:
            chain.append({'label': 'Applied vs Approved Rate', 'reference': alert.service or '—'})
            chain.append({'label': 'Tariff Version', 'reference': alert.source_reference or '—'})
        elif alert.source_type in (RiskAlert.SourceType.GST_ENGINE, RiskAlert.SourceType.OPERATOR_DECLARATIONS):
            chain.append({'label': 'Tax Rule Version', 'reference': alert.source_reference or '—'})
        return chain

    # ---- Rule evaluation -----------------------------------------------

    def evaluate_rules(self, operator_code: str = None) -> List[RiskAlert]:
        """Evaluate every ACTIVE risk rule (optionally scoped to one operator)."""
        rules = RiskRule.objects.filter(status=RiskRule.Status.ACTIVE)
        if operator_code:
            rules = rules.filter(Q(operator_code=operator_code) | Q(operator_code=''))

        new_alerts = []
        for rule in rules:
            try:
                alert = self.evaluate_rule(rule, operator_code or rule.operator_code)
                if alert is not None:
                    new_alerts.append(alert)
            except Exception:
                logger.exception('Error evaluating risk rule %s', rule.name)
        return new_alerts

    def evaluate_rule(self, rule: RiskRule, operator_code: str) -> Optional[RiskAlert]:
        """Evaluate a single rule for one operator; create/update/resolve alerts as needed."""
        metric_value = self._calculate_metric(rule.metric, operator_code)
        breached = self._compare(rule.comparison_operator, metric_value, rule.threshold_value)

        existing_alert = RiskAlert.objects.filter(
            rule=rule, operator_code=operator_code, status__in=RiskAlert.OPEN_STATUSES,
        ).order_by('-triggered_at').first()

        if breached:
            return self.create_alert(
                rule=rule, operator_code=operator_code, metric_value=metric_value,
                source_type=ALERT_TYPE_SOURCE_MAP.get(rule.alert_type, RiskAlert.SourceType.MANUAL_RULE),
            )

        if existing_alert:
            existing_alert.status = RiskAlert.Status.RESOLVED
            existing_alert.resolved_at = timezone.now()
            existing_alert.resolution_type = RiskAlert.ResolutionType.SYSTEM_FALSE_POSITIVE
            existing_alert.resolution_notes = 'Auto-resolved: metric returned within threshold on re-evaluation.'
            existing_alert.save()
            logger.info('Risk alert %s auto-resolved for %s', existing_alert.reference, operator_code)
        return None

    def create_alert(self, rule: RiskRule, operator_code: str, metric_value: Decimal,
                      source_type: str = RiskAlert.SourceType.MANUAL_RULE, source_reference: str = '',
                      period_start=None, period_end=None, service: str = '', traffic_type: str = '',
                      description: str = '') -> RiskAlert:
        """Create a new alert, or fold this occurrence into an existing open one (dedup)."""
        existing = self.deduplicate_alert(rule, operator_code, source_type, source_reference, period_start, service)
        if existing:
            existing.occurrence_count += 1
            existing.metric_value = metric_value
            existing.save(update_fields=['occurrence_count', 'metric_value'])
            logger.info('Risk alert %s incremented (occurrence #%s) for %s', existing.reference, existing.occurrence_count, operator_code)
            return existing

        alert = RiskAlert(
            rule=rule, rule_version=rule.version, operator_code=operator_code,
            alert_type=rule.alert_type, threshold_unit=rule.threshold_unit,
            source_type=source_type, source_reference=source_reference,
            severity=rule.severity, metric_value=metric_value, threshold_value=rule.threshold_value,
            description=description or self._generate_alert_description(rule, metric_value),
            status=RiskAlert.Status.OPEN, period_start=period_start, period_end=period_end,
            service=service or rule.service_scope, traffic_type=traffic_type,
        )
        alert.save()
        logger.info('Risk alert %s created: %s for %s', alert.reference, rule.name, operator_code)

        if rule.auto_escalate and rule.escalation_threshold and alert.occurrence_count >= rule.escalation_threshold:
            self.create_audit_case(alert, user=None, notes='Auto-escalated: occurrence threshold reached.')
        return alert

    def deduplicate_alert(self, rule: RiskRule, operator_code: str, source_type: str,
                           source_reference: str, period_start, service: str) -> Optional[RiskAlert]:
        """Return the existing open alert matching (operator, rule, source, period, service), if any."""
        qs = RiskAlert.objects.filter(
            rule=rule, operator_code=operator_code, status__in=RiskAlert.OPEN_STATUSES,
        )
        if source_reference:
            qs = qs.filter(source_reference=source_reference)
        if period_start:
            qs = qs.filter(period_start=period_start)
        if service:
            qs = qs.filter(service=service)
        return qs.order_by('-triggered_at').first()

    def _calculate_metric(self, metric: str, operator_code: str) -> Decimal:
        """Calculate the current value for a given metric (last 30 days)."""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)

        if metric in ('revenue_variance_pct', 'gst_variance_pct', 'missing_cdr_pct'):
            # Requires cross-referencing declared vs mediated data (reconciliation service);
            # left as a placeholder hook until that comparison is wired into the engine.
            return Decimal('0')

        if metric == 'traffic_drop_pct':
            current = TrafficSummary.objects.filter(
                operator_code=operator_code, period_start__gte=start_date, period_end__lte=end_date,
            ).aggregate(total=Sum('record_count'))['total'] or 0
            previous_start, previous_end = start_date - timedelta(days=30), start_date
            previous = TrafficSummary.objects.filter(
                operator_code=operator_code, period_start__gte=previous_start, period_end__lte=previous_end,
            ).aggregate(total=Sum('record_count'))['total'] or 0
            if previous > 0:
                return Decimal(str(((previous - current) / previous) * 100))
            return Decimal('0')

        if metric == 'unrated_record_pct':
            rated = RatedAggregate.objects.filter(
                traffic_summary__operator_code=operator_code,
                traffic_summary__period_start__gte=start_date, traffic_summary__period_end__lte=end_date,
            ).aggregate(total=Sum('traffic_summary__record_count'))['total'] or 0
            unrated = RatedAggregate.objects.filter(
                traffic_summary__operator_code=operator_code,
                traffic_summary__period_start__gte=start_date, traffic_summary__period_end__lte=end_date,
            ).aggregate(total=Sum('unrated_count'))['total'] or 0
            total = rated + unrated
            return Decimal(str((unrated / total) * 100)) if total else Decimal('0')

        if metric == 'zero_rated_pct':
            zero_rated = RatedAggregate.objects.filter(
                traffic_summary__operator_code=operator_code,
                traffic_summary__period_start__gte=start_date, traffic_summary__period_end__lte=end_date,
            ).aggregate(total=Sum('zero_rated_count'))['total'] or 0
            rated = RatedAggregate.objects.filter(
                traffic_summary__operator_code=operator_code,
                traffic_summary__period_start__gte=start_date, traffic_summary__period_end__lte=end_date,
            ).aggregate(total=Sum('traffic_summary__record_count'))['total'] or 0
            return Decimal(str((zero_rated / rated) * 100)) if rated else Decimal('0')

        return Decimal('0')

    def _compare(self, comparison_operator: str, metric_value: Decimal, threshold_value: Decimal) -> bool:
        """Evaluate metric_value against threshold_value per the rule's comparison operator."""
        try:
            metric_value, threshold_value = Decimal(metric_value), Decimal(threshold_value)
        except (InvalidOperation, TypeError):
            return False
        Op = RiskRule.ComparisonOperator
        if comparison_operator == Op.GT:
            return metric_value > threshold_value
        if comparison_operator == Op.GTE:
            return metric_value >= threshold_value
        if comparison_operator == Op.LT:
            return metric_value < threshold_value
        if comparison_operator == Op.LTE:
            return metric_value <= threshold_value
        if comparison_operator == Op.EQ:
            return metric_value == threshold_value
        if comparison_operator == Op.NE:
            return metric_value != threshold_value
        if comparison_operator in (Op.PCT_VARIANCE, Op.ABS_VARIANCE):
            return abs(metric_value) > threshold_value
        return False

    def _generate_alert_description(self, rule: RiskRule, metric_value: Decimal) -> str:
        symbol = rule.get_comparison_operator_display()
        unit = '%' if rule.threshold_unit == RiskRule.ThresholdUnit.PERCENTAGE else ''
        return (
            f'{rule.metric} of {metric_value:.2f}{unit} {symbol} configured threshold of '
            f'{rule.threshold_value:.2f}{unit} ({rule.name}).'
        )

    def get_related_objects(self, alert: RiskAlert) -> Dict[str, Any]:
        """Resolve the alert's source_reference to a real linked record, where one exists."""
        related = {}
        if not alert.source_reference:
            return related
        if alert.source_type == RiskAlert.SourceType.RECONCILIATION:
            from regulatory.models.reconciliation import ReconciliationRun
            related['reconciliation'] = ReconciliationRun.objects.filter(reference=alert.source_reference).first()
        elif alert.source_type == RiskAlert.SourceType.OPERATOR_DECLARATIONS:
            from regulatory.models.declarations import OperatorDeclaration
            related['declaration'] = OperatorDeclaration.objects.filter(reference=alert.source_reference).first()
        elif alert.source_type == RiskAlert.SourceType.TARIFF_COMPLIANCE:
            from regulatory.models.compliance import TariffComplianceResult
            result = TariffComplianceResult.objects.filter(pk=alert.source_reference).first() if alert.source_reference.isdigit() else None
            related['tariff_compliance'] = result
        return related

    def get_rule_snapshot(self, alert: RiskAlert) -> Dict[str, Any]:
        """Return the exact rule configuration active when this alert fired — not
        necessarily the rule's current (possibly since-edited) configuration."""
        if not alert.rule_id:
            return {}
        # Re-fetch the rule fresh rather than trusting a possibly-stale cached
        # `alert.rule`, so `is_stale` reflects the *current* DB version.
        current_rule = RiskRule.objects.filter(pk=alert.rule_id).first()
        if not current_rule:
            return {}
        is_stale = bool(alert.rule_version) and current_rule.version != alert.rule_version

        version = RiskRuleVersion.objects.filter(rule_id=alert.rule_id, version=alert.rule_version).first() if alert.rule_version else None
        if version:
            return {
                'name': version.name, 'code': version.code, 'alert_type': version.alert_type, 'metric': version.metric,
                'comparison_operator': version.comparison_operator, 'threshold_value': version.threshold_value,
                'threshold_unit': version.threshold_unit, 'severity': version.severity, 'operator_code': version.operator_code,
                'service_scope': version.service_scope, 'effective_from': version.effective_from, 'effective_to': version.effective_to,
                'version': version.version, 'status': None, 'is_stale': is_stale,
            }
        return {
            'name': current_rule.name, 'code': current_rule.code, 'alert_type': current_rule.alert_type, 'metric': current_rule.metric,
            'comparison_operator': current_rule.comparison_operator, 'threshold_value': current_rule.threshold_value,
            'threshold_unit': current_rule.threshold_unit, 'severity': current_rule.severity, 'operator_code': current_rule.operator_code,
            'service_scope': current_rule.service_scope, 'effective_from': current_rule.effective_from, 'effective_to': current_rule.effective_to,
            'version': current_rule.version, 'status': current_rule.status, 'is_stale': is_stale,
        }

    def get_financial_exposure(self, alert: RiskAlert) -> Dict[str, Any]:
        """Financial breakdown for revenue/tax-type alerts; non-financial alerts (data
        quality / traffic) should be rendered from record-count metrics instead."""
        is_financial = alert.alert_type not in (RiskAlert.AlertType.DATA_QUALITY, RiskAlert.AlertType.TRAFFIC)
        exposure = {
            'is_financial': is_financial,
            'expected_revenue': None, 'declared_revenue': None, 'revenue_variance': None,
            'expected_taxable_revenue': None, 'declared_taxable_revenue': None, 'taxable_revenue_variance': None,
            'expected_gst': None, 'declared_gst': None, 'gst_variance': None,
            'potential_exposure': alert.potential_exposure, 'confirmed_exposure': alert.confirmed_exposure,
            'recovered_amount': alert.recovered_amount,
        }
        run = self.get_related_objects(alert).get('reconciliation')
        if run:
            exposure.update({
                'expected_revenue': run.expected_revenue, 'declared_revenue': run.declared_revenue, 'revenue_variance': run.revenue_variance,
                'expected_taxable_revenue': run.expected_taxable_revenue, 'declared_taxable_revenue': run.declared_taxable_revenue,
                'taxable_revenue_variance': run.taxable_revenue_variance,
                'expected_gst': run.expected_gst, 'declared_gst': run.declared_gst, 'gst_variance': run.gst_variance,
            })
        return exposure

    def get_reconciliation(self, alert: RiskAlert):
        run = self.get_related_objects(alert).get('reconciliation')
        if not run:
            return None
        return {
            'run': run,
            'services': [
                {
                    'service_type': r.service_type, 'expected_revenue': r.mediated_revenue, 'declared_revenue': r.declared_revenue,
                    'variance': r.variance_revenue, 'expected_gst': r.mediated_tax, 'declared_gst': r.declared_tax,
                    'gst_variance': r.variance_tax, 'risk': r.match_status,
                }
                for r in run.results.all()
            ],
        }

    def get_declaration(self, alert: RiskAlert):
        return self.get_related_objects(alert).get('declaration')

    def get_cdrs(self, alert: RiskAlert, filters: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        return get_masked_cdrs(alert.operator_code, alert.period_start, alert.period_end, filters, page, page_size)

    def get_operator_requests(self, alert: RiskAlert):
        return alert.operator_responses.select_related('requested_by', 'reviewed_by').all()

    def get_activity_log(self, alert: RiskAlert):
        return AuditLog.objects.filter(entity_type='RiskAlert', entity_id=str(alert.pk)).order_by('-timestamp')

    # ---- Workflow actions -----------------------------------------------

    @staticmethod
    def _audit(user, action, alert, description, **extra):
        AuditLog.objects.create(
            user=user, action=action, entity_type='RiskAlert', entity_id=str(alert.pk),
            description=description, extra_data=extra,
        )

    def assign_alert(self, alert: RiskAlert, assigned_to, assigned_by, priority: str = '', due_date=None, notes: str = ''):
        alert.assigned_to = assigned_to
        alert.assigned_by = assigned_by
        alert.assigned_at = timezone.now()
        alert.priority = priority
        alert.due_date = due_date
        alert.assignment_notes = notes
        if alert.status == RiskAlert.Status.OPEN:
            alert.status = RiskAlert.Status.ASSIGNED
        alert.save()
        self._audit(assigned_by, 'UPDATE', alert, f'Alert assigned to {assigned_to}', assigned_to=str(assigned_to))
        return alert

    def _require_transition(self, alert: RiskAlert, target: str):
        if target not in alert.allowed_next_statuses():
            raise ValueError(f'Cannot move alert from {alert.get_status_display()} to {dict(RiskAlert.Status.choices)[target]}.')

    def start_investigation(self, alert: RiskAlert, investigator, notes: str = ''):
        self._require_transition(alert, RiskAlert.Status.INVESTIGATING)
        old_status = alert.status
        alert.investigator = investigator
        alert.investigation_started_at = timezone.now()
        if notes:
            alert.investigation_notes = notes
        alert.status = RiskAlert.Status.INVESTIGATING
        alert.save()
        self._audit(investigator, 'UPDATE', alert, 'Investigation started', old_status=old_status, new_status=alert.status)
        return alert

    def request_operator_response(self, alert: RiskAlert, user, subject: str, details: str, evidence: str = '',
                                   due_date=None, priority: str = '', notes: str = '') -> RiskAlertOperatorResponse:
        response = RiskAlertOperatorResponse.objects.create(
            alert=alert, request_subject=subject, request_details=details, required_evidence=evidence,
            priority=priority, notes=notes, requested_by=user, due_date=due_date,
        )
        old_status = alert.status
        if RiskAlert.Status.AWAITING_OPERATOR in alert.allowed_next_statuses():
            alert.status = RiskAlert.Status.AWAITING_OPERATOR
            alert.save(update_fields=['status'])
        self._audit(user, 'UPDATE', alert, f'Operator response requested: {subject}', old_status=old_status, new_status=alert.status)
        return response

    def record_operator_response(self, response: RiskAlertOperatorResponse, response_text: str, submitted_by: str = ''):
        response.response_text = response_text
        response.submitted_by = submitted_by
        response.submitted_at = timezone.now()
        response.response_status = RiskAlertOperatorResponse.ResponseStatus.SUBMITTED
        response.save()
        alert = response.alert
        old_status = alert.status
        if RiskAlert.Status.UNDER_REVIEW in alert.allowed_next_statuses():
            alert.status = RiskAlert.Status.UNDER_REVIEW
            alert.save(update_fields=['status'])
        self._audit(None, 'UPDATE', alert, 'Operator response received', old_status=old_status, new_status=alert.status)
        return response

    def review_operator_response(self, response: RiskAlertOperatorResponse, user, accept: bool, notes: str = ''):
        response.response_status = (
            RiskAlertOperatorResponse.ResponseStatus.ACCEPTED if accept else RiskAlertOperatorResponse.ResponseStatus.REJECTED
        )
        response.reviewed_by = user
        response.reviewed_at = timezone.now()
        response.review_notes = notes
        response.save()
        alert = response.alert
        old_status = alert.status
        target = RiskAlert.Status.INVESTIGATING if accept else RiskAlert.Status.AWAITING_OPERATOR
        if target in alert.allowed_next_statuses() or alert.status == RiskAlert.Status.UNDER_REVIEW:
            alert.status = target
            alert.save(update_fields=['status'])
        self._audit(user, 'UPDATE', alert, f'Operator response {"accepted" if accept else "rejected"}', old_status=old_status, new_status=alert.status)
        return response

    def update_severity(self, alert: RiskAlert, user, severity: str, reason: str = ''):
        rank = {value: index for index, (value, _) in enumerate(RiskRule.Severity.choices)}
        downgraded = (
            alert.severity in (RiskRule.Severity.CRITICAL, RiskRule.Severity.HIGH)
            and rank.get(severity, 0) < rank.get(alert.severity, 0)
        )
        if downgraded and not reason:
            raise ValueError('A reason is required when downgrading severity from High/Critical.')
        old_severity = alert.severity
        alert.severity = severity
        alert.save(update_fields=['severity'])
        self._audit(user, 'UPDATE', alert, f'Severity changed from {old_severity} to {severity}' + (f': {reason}' if reason else ''),
                    old_severity=old_severity, new_severity=severity)
        return alert

    def resolve_alert(self, alert: RiskAlert, user, resolution_type: str, notes: str,
                       recovered_amount=None, confirmed_exposure=None):
        self._require_transition(alert, RiskAlert.Status.RESOLVED)
        old_status = alert.status
        alert.status = RiskAlert.Status.RESOLVED
        alert.resolved_by = user
        alert.resolved_at = timezone.now()
        alert.resolution_type = resolution_type
        alert.resolution_notes = notes
        alert.recovered_amount = recovered_amount
        alert.confirmed_exposure = confirmed_exposure
        alert.save()
        self._audit(user, 'UPDATE', alert, f'Alert resolved: {resolution_type}', old_status=old_status, new_status=alert.status)
        return alert

    def dismiss_alert(self, alert: RiskAlert, user, reason: str, notes: str = ''):
        self._require_transition(alert, RiskAlert.Status.DISMISSED)
        old_status = alert.status
        alert.status = RiskAlert.Status.DISMISSED
        alert.dismissed_by = user
        alert.dismissed_at = timezone.now()
        alert.dismissal_reason = reason
        alert.dismissal_notes = notes
        alert.save()
        self._audit(user, 'UPDATE', alert, f'Alert dismissed: {reason}', old_status=old_status, new_status=alert.status)
        return alert

    def add_comment(self, alert: RiskAlert, author, comment: str) -> RiskAlertComment:
        entry = RiskAlertComment.objects.create(alert=alert, author=author, comment=comment)
        self._audit(author, 'UPDATE', alert, 'Comment added')
        return entry

    def create_audit_case(self, alert: RiskAlert, user, case_type: str = '', assigned_officer=None,
                           priority: str = '', investigation_scope: str = '', notes: str = '', force: bool = False):
        """Escalate this alert to a full audit-case investigation (Page 8). Delegates the
        actual case creation/evidence-seeding to AuditCaseService so both entry points
        (Risk Alerts and Audit Cases) share one case-creation code path.

        Refuses to create a second case for an alert that already has one linked and
        still open, unless `force=True` (a supervisor override)."""
        from regulatory.models.audit import AuditCase
        from regulatory.services.audit_case_service import AuditCaseService

        if alert.audit_case_id and alert.audit_case.status != AuditCase.Status.CLOSED and not force:
            raise ValueError(
                f'This alert is already linked to open audit case {alert.audit_case.case_number}. '
                'Open the existing case, or use the supervisor override to create another.'
            )

        overrides = {}
        if assigned_officer:
            overrides['assigned_to'] = assigned_officer
        if priority:
            overrides['priority'] = priority
        case = AuditCaseService().create_from_risk_alert(alert, user, **overrides)
        extra_notes = '\n'.join(filter(None, [investigation_scope, notes]))
        if extra_notes:
            case.description = f'{case.description}\n\n{extra_notes}'.strip()
            case.save(update_fields=['description'])

        self._audit(user, 'UPDATE', alert, f'Escalated to audit case {case.case_number}')
        return case

    # ---- Export ---------------------------------------------------------

    def generate_evidence_package(self, alert: RiskAlert):
        """Build a downloadable evidence package for the alert: summary, rule
        snapshot, evidence list, financial exposure, comments, operator
        responses and activity log — one sheet each in an Excel workbook."""
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        summary_ws = wb.active
        summary_ws.title = 'Alert Summary'
        snapshot = self.get_rule_snapshot(alert)
        rows = [
            ('Alert ID', alert.reference), ('Operator', alert.operator_code),
            ('Alert Type', alert.get_alert_type_display()), ('Severity', alert.get_severity_display()),
            ('Status', alert.get_status_display()), ('Triggered At', str(alert.triggered_at)),
            ('Metric Value', str(alert.metric_value)), ('Threshold', str(alert.threshold_value)),
            ('Potential Exposure', str(alert.potential_exposure)), ('Occurrence Count', alert.occurrence_count),
            ('Description', alert.description),
            ('Rule Name', snapshot.get('name', '')), ('Rule Version', snapshot.get('version', '')),
            ('Comparison Operator', snapshot.get('comparison_operator', '')),
            ('Assigned To', str(alert.assigned_to or '')),
            ('Resolution Type', alert.get_resolution_type_display() if alert.resolution_type else ''),
            ('Resolution Notes', alert.resolution_notes),
        ]
        for label, value in rows:
            summary_ws.append([label, value])
        for cell in summary_ws['A']:
            cell.font = Font(bold=True)

        evidence_ws = wb.create_sheet('Evidence')
        evidence_ws.append(['Label', 'Reference'])
        for node in self.get_evidence(alert):
            evidence_ws.append([node['label'], node['reference']])

        exposure_ws = wb.create_sheet('Financial Exposure')
        exposure = self.get_financial_exposure(alert)
        for label, value in exposure.items():
            exposure_ws.append([label, str(value) if value is not None else ''])

        comments_ws = wb.create_sheet('Comments')
        comments_ws.append(['Author', 'Timestamp', 'Comment'])
        for c in alert.comments.select_related('author').all():
            comments_ws.append([str(c.author or ''), str(c.created_at), c.comment])

        responses_ws = wb.create_sheet('Operator Responses')
        responses_ws.append(['Subject', 'Requested', 'Due', 'Submitted', 'Submitted By', 'Status'])
        for r in self.get_operator_requests(alert):
            responses_ws.append([r.request_subject, str(r.requested_at), str(r.due_date or ''),
                                  str(r.submitted_at or ''), r.submitted_by, r.get_response_status_display()])

        activity_ws = wb.create_sheet('Activity Log')
        activity_ws.append(['Timestamp', 'User', 'Action', 'Description'])
        for entry in self.get_activity_log(alert):
            activity_ws.append([str(entry.timestamp), str(entry.user or ''), entry.action, entry.description])

        return wb


def evaluate_risk_rules(operator_code: str = None) -> List[RiskAlert]:
    """Convenience function to evaluate risk rules without instantiating the engine."""
    return RiskEngine().evaluate_rules(operator_code)
