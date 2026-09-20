"""
Audit Case Service

Drives the full regulatory-investigation lifecycle: case creation (manual, or
escalated from a Risk Alert / Reconciliation Run), assignment, investigation,
findings, evidence, operator-response exchanges, risk/exposure updates,
resolution, closure and reopening — plus the read-side helpers backing the
case detail workspace (financial analysis, CDR drill-down, source files,
calculation trace) and CSV/Excel export.
"""
import csv
import io
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AuditLog
from regulatory.models.audit import (
    AuditCase, AuditFinding, AuditEvidence, AuditOperatorResponse, AuditCaseComment, AuditCaseReport,
)
from regulatory.models.risk import RiskAlert
from regulatory.models.reconciliation import ReconciliationRun
from regulatory.services.cdr_lookup import get_masked_cdrs

logger = logging.getLogger(__name__)

SORT_FIELDS = {
    'case_id': 'case_number', '-case_id': '-case_number',
    'operator': 'operator_code', '-operator': '-operator_code',
    'case_type': 'case_type', '-case_type': '-case_type',
    'exposure': 'potential_exposure', '-exposure': '-potential_exposure',
    'risk': 'risk_level', '-risk': '-risk_level',
    'status': 'status', '-status': '-status',
    'opened': 'opened_at', '-opened': '-opened_at',
    'updated': 'updated_at', '-updated': '-updated_at',
    'officer': 'assigned_to__username', '-officer': '-assigned_to__username',
}

RISK_ALERT_TYPE_TO_CASE_TYPE = {
    'TAX': AuditCase.CaseType.TAX_CLASSIFICATION,
    'GST': AuditCase.CaseType.GST_VARIANCE,
    'REVENUE': AuditCase.CaseType.REVENUE_VARIANCE,
    'DATA_QUALITY': AuditCase.CaseType.DATA_QUALITY,
    'COMPLIANCE': AuditCase.CaseType.REPEATED_COMPLIANCE_FAILURE,
    'TARIFF': AuditCase.CaseType.TARIFF_VIOLATION,
    'TRAFFIC': AuditCase.CaseType.TRAFFIC_ANOMALY,
    'DECLARATION': AuditCase.CaseType.DECLARATION_DISCREPANCY,
    'RECONCILIATION': AuditCase.CaseType.REVENUE_VARIANCE,
    'OTHER': AuditCase.CaseType.OTHER,
}


class AuditCaseService:
    """Manages the audit-case investigation lifecycle."""

    # ---- Read helpers ---------------------------------------------------

    def get_summary(self, queryset=None) -> Dict[str, int]:
        qs = queryset if queryset is not None else AuditCase.objects.all()
        return {
            'total': qs.count(),
            'open': qs.filter(status=AuditCase.Status.OPEN).count(),
            'investigating': qs.filter(status=AuditCase.Status.INVESTIGATION).count(),
            'awaiting_operator': qs.filter(status=AuditCase.Status.AWAITING_OPERATOR).count(),
            'resolved': qs.filter(status=AuditCase.Status.RESOLVED).count(),
            'closed': qs.filter(status=AuditCase.Status.CLOSED).count(),
        }

    def get_cases(self, filters: Optional[Dict[str, Any]] = None):
        filters = filters or {}
        qs = AuditCase.objects.select_related('assigned_to')

        if filters.get('operator'):
            qs = qs.filter(operator_code=filters['operator'])
        if filters.get('case_type'):
            qs = qs.filter(case_type=filters['case_type'])
        if filters.get('status'):
            qs = qs.filter(status=filters['status'])
        if filters.get('risk_level'):
            qs = qs.filter(risk_level=filters['risk_level'])
        if filters.get('assigned_officer') == 'unassigned':
            qs = qs.filter(assigned_to__isnull=True)
        elif filters.get('assigned_officer'):
            qs = qs.filter(assigned_to_id=filters['assigned_officer'])
        if filters.get('start_date'):
            qs = qs.filter(opened_at__date__gte=filters['start_date'])
        if filters.get('end_date'):
            qs = qs.filter(opened_at__date__lte=filters['end_date'])
        if filters.get('search'):
            term = filters['search']
            qs = qs.filter(
                Q(case_number__icontains=term) | Q(operator_code__icontains=term) |
                Q(case_type__icontains=term) | Q(finding_summary__icontains=term) |
                Q(risk_level__icontains=term) | Q(assigned_to__username__icontains=term) |
                Q(risk_alert__reference__icontains=term) | Q(reconciliation_run__reference__icontains=term)
            )
        qs = qs.order_by(SORT_FIELDS.get(filters.get('sort'), '-opened_at'), '-pk')
        return qs

    def get_case(self, pk):
        return get_object_or_404(
            AuditCase.objects.select_related(
                'assigned_to', 'opened_by', 'resolved_by', 'closed_by', 'risk_alert',
                'reconciliation_run', 'declaration', 'tariff_compliance_result',
            ),
            pk=pk,
        )

    def get_activity_log(self, case: AuditCase):
        return AuditLog.objects.filter(entity_type='AuditCase', entity_id=str(case.pk)).order_by('-timestamp')

    # ---- Creation ---------------------------------------------------------

    @staticmethod
    def _audit(user, action, case, description, **extra):
        AuditLog.objects.create(
            user=user, action=action, entity_type='AuditCase', entity_id=str(case.pk),
            description=description, extra_data=extra,
        )

    def create_case(self, data: Dict[str, Any], user) -> AuditCase:
        case = AuditCase.objects.create(
            case_number=AuditCase.generate_case_number(),
            title=data.get('title') or f"{data.get('case_type', AuditCase.CaseType.OTHER)} — {data.get('operator_code', '')}",
            case_type=data.get('case_type', AuditCase.CaseType.OTHER),
            operator_code=data.get('operator_code', ''),
            description=data.get('description', ''),
            finding_summary=data.get('finding_summary', ''),
            period_start=data.get('period_start'),
            period_end=data.get('period_end'),
            risk_level=data.get('risk_level', AuditCase.RiskLevel.MEDIUM),
            priority=data.get('priority', ''),
            potential_exposure=data.get('potential_exposure') or 0,
            risk_alert=data.get('risk_alert'),
            reconciliation_run=data.get('reconciliation_run'),
            declaration=data.get('declaration'),
            tariff_compliance_result=data.get('tariff_compliance_result'),
            assigned_to=data.get('assigned_to'),
            due_date=data.get('due_date'),
            opened_by=user,
        )
        if case.assigned_to:
            case.assigned_by = user
            case.assigned_at = timezone.now()
            case.status = AuditCase.Status.ASSIGNED
            case.save(update_fields=['assigned_by', 'assigned_at', 'status'])
        self._audit(user, 'CREATE', case, f'Audit case {case.case_number} opened')
        return case

    def create_from_risk_alert(self, alert: RiskAlert, user, **overrides) -> AuditCase:
        data = {
            'case_type': RISK_ALERT_TYPE_TO_CASE_TYPE.get(alert.alert_type, AuditCase.CaseType.OTHER),
            'operator_code': alert.operator_code,
            'description': (
                f'Escalated from Risk Alert {alert.reference} '
                f'({alert.rule.name if alert.rule else "manual rule"}).\n{alert.description}'
            ),
            'finding_summary': alert.description[:255],
            'period_start': alert.period_start,
            'period_end': alert.period_end,
            'risk_level': alert.severity,
            'potential_exposure': alert.potential_exposure or 0,
            'risk_alert': alert,
        }
        data.update(overrides)
        case = self.create_case(data, user)

        alert.audit_case = case
        if alert.status in (RiskAlert.Status.OPEN, RiskAlert.Status.ASSIGNED):
            alert.status = RiskAlert.Status.UNDER_REVIEW
        alert.save(update_fields=['audit_case', 'status'])

        self.add_evidence(case, user, evidence_type=AuditEvidence.EvidenceType.RISK_ALERT,
                           description=f'Originating risk alert {alert.reference}',
                           source_type=alert.source_type, reference_id=alert.reference)
        if alert.source_reference:
            self.add_evidence(case, user, evidence_type=AuditEvidence.EvidenceType.SOURCE_FILE,
                               description='Source reference cited by the risk alert',
                               source_type=alert.source_type, reference_id=alert.source_reference)
        return case

    def create_from_reconciliation(self, run: ReconciliationRun, user, **overrides) -> AuditCase:
        variances = {
            'GST Variance': (run.gst_variance, AuditCase.CaseType.GST_VARIANCE),
            'Revenue Variance': (run.revenue_variance, AuditCase.CaseType.REVENUE_VARIANCE),
            'Taxable Revenue Variance': (run.taxable_revenue_variance, AuditCase.CaseType.TAXABLE_REVENUE_VARIANCE),
        }
        label, (_, case_type) = max(variances.items(), key=lambda kv: abs(kv[1][0] or 0))
        exposure = abs(run.revenue_variance or 0) + abs(run.gst_variance or 0)

        data = {
            'case_type': case_type,
            'operator_code': run.operator_code,
            'description': (
                f'Escalated from Reconciliation Run {run.reference or run.pk} ({label}).\n'
                f'Revenue variance {run.revenue_variance}, GST variance {run.gst_variance}, '
                f'Taxable revenue variance {run.taxable_revenue_variance}.'
            ),
            'finding_summary': f'{label} of {abs(variances[label][0] or 0)} detected during reconciliation',
            'period_start': run.period_start,
            'period_end': run.period_end,
            'risk_level': run.risk_level if run.risk_level in dict(AuditCase.RiskLevel.choices) else AuditCase.RiskLevel.MEDIUM,
            'potential_exposure': exposure,
            'reconciliation_run': run,
            'declaration': run.declaration,
        }
        data.update(overrides)
        case = self.create_case(data, user)

        self.add_evidence(case, user, evidence_type=AuditEvidence.EvidenceType.RECONCILIATION,
                           description=f'Reconciliation run {run.reference or run.pk}',
                           source_type='RECONCILIATION', reference_id=str(run.pk))
        if run.declaration:
            self.add_evidence(case, user, evidence_type=AuditEvidence.EvidenceType.OPERATOR_DECLARATION,
                               description=f'Operator declaration {run.declaration.reference}',
                               source_type='OPERATOR_DECLARATIONS', reference_id=str(run.declaration.pk))
        return case

    # ---- Workflow ---------------------------------------------------------

    def _require_transition(self, case: AuditCase, target: str):
        if target not in case.allowed_next_statuses():
            raise ValueError(f'Cannot move case from {case.get_status_display()} to {dict(AuditCase.Status.choices)[target]}.')

    def assign_case(self, case: AuditCase, assigned_to, assigned_by, priority: str = '', due_date=None, notes: str = ''):
        case.assigned_to = assigned_to
        case.assigned_by = assigned_by
        case.assigned_at = timezone.now()
        if priority:
            case.priority = priority
        case.due_date = due_date
        case.assignment_notes = notes
        if case.status == AuditCase.Status.OPEN:
            case.status = AuditCase.Status.ASSIGNED
        case.save()
        self._audit(assigned_by, 'UPDATE', case, f'Case assigned to {assigned_to}', old_status='OPEN', new_status=case.status)
        return case

    def start_investigation(self, case: AuditCase, user):
        self._require_transition(case, AuditCase.Status.INVESTIGATION)
        old_status = case.status
        case.investigation_started_by = user
        case.investigation_started_at = timezone.now()
        case.status = AuditCase.Status.INVESTIGATION
        case.save()
        self._audit(user, 'UPDATE', case, 'Investigation started', old_status=old_status, new_status=case.status)
        return case

    def add_finding(self, case: AuditCase, user, **fields) -> AuditFinding:
        finding = AuditFinding.objects.create(case=case, created_by=user, **fields)
        self._audit(user, 'CREATE', case, f'Finding added: {finding.title or finding.get_finding_type_display()}')
        return finding

    def update_finding(self, finding: AuditFinding, user, **fields) -> AuditFinding:
        for name, value in fields.items():
            setattr(finding, name, value)
        if ('expected_value' in fields or 'observed_value' in fields) and 'variance' not in fields:
            finding.variance = (
                finding.observed_value - finding.expected_value
                if finding.expected_value is not None and finding.observed_value is not None else None
            )
        finding.save()
        self._audit(user, 'UPDATE', finding.case, f'Finding updated: {finding.title or finding.get_finding_type_display()}')
        return finding

    def confirm_finding(self, finding: AuditFinding, user) -> AuditFinding:
        finding.status = AuditFinding.Status.CONFIRMED
        finding.save(update_fields=['status', 'updated_at'])
        self._audit(user, 'UPDATE', finding.case, f'Finding confirmed: {finding.title or finding.get_finding_type_display()}')
        return finding

    def resolve_finding(self, finding: AuditFinding, user) -> AuditFinding:
        finding.status = AuditFinding.Status.RESOLVED
        finding.save(update_fields=['status', 'updated_at'])
        self._audit(user, 'UPDATE', finding.case, f'Finding resolved: {finding.title or finding.get_finding_type_display()}')
        return finding

    def add_evidence(self, case: AuditCase, user, **fields) -> AuditEvidence:
        evidence = AuditEvidence.objects.create(case=case, created_by=user, **fields)
        self._audit(user, 'CREATE', case, f'Evidence added: {evidence.get_evidence_type_display()}')
        return evidence

    def link_evidence_to_finding(self, evidence: AuditEvidence, finding: Optional[AuditFinding], user) -> AuditEvidence:
        evidence.finding = finding
        evidence.save(update_fields=['finding'])
        label = f'to finding "{finding.title or finding.get_finding_type_display()}"' if finding else '(unlinked)'
        self._audit(user, 'UPDATE', evidence.case, f'Evidence #{evidence.pk} linked {label}')
        return evidence

    def request_operator_response(self, case: AuditCase, user, subject: str, details: str,
                                   required_evidence: str = '', due_date=None) -> AuditOperatorResponse:
        response = AuditOperatorResponse.objects.create(
            case=case, request_subject=subject, request_details=details,
            required_evidence=required_evidence, requested_by=user, due_date=due_date,
        )
        old_status = case.status
        if AuditCase.Status.AWAITING_OPERATOR in case.allowed_next_statuses():
            case.status = AuditCase.Status.AWAITING_OPERATOR
            case.save(update_fields=['status'])
        self._audit(user, 'UPDATE', case, f'Operator response requested: {subject}', old_status=old_status, new_status=case.status)
        return response

    def record_operator_response(self, response: AuditOperatorResponse, response_text: str, submitted_by: str = ''):
        response.response_text = response_text
        response.submitted_by = submitted_by
        response.submitted_at = timezone.now()
        response.response_status = AuditOperatorResponse.ResponseStatus.SUBMITTED
        response.save()
        case = response.case
        old_status = case.status
        if AuditCase.Status.OPERATOR_RESPONSE in case.allowed_next_statuses():
            case.status = AuditCase.Status.OPERATOR_RESPONSE
            case.save(update_fields=['status'])
        self._audit(None, 'UPDATE', case, 'Operator response received', old_status=old_status, new_status=case.status)
        return response

    def review_operator_response(self, response: AuditOperatorResponse, user, accept: bool, notes: str = ''):
        response.response_status = AuditOperatorResponse.ResponseStatus.ACCEPTED if accept else AuditOperatorResponse.ResponseStatus.REJECTED
        response.reviewed_by = user
        response.reviewed_at = timezone.now()
        response.review_notes = notes
        response.save()
        case = response.case
        old_status = case.status
        target = AuditCase.Status.REGULATORY_REVIEW if accept else AuditCase.Status.AWAITING_OPERATOR
        if target in case.allowed_next_statuses() or case.status == AuditCase.Status.OPERATOR_RESPONSE:
            case.status = target
            case.save(update_fields=['status'])
        self._audit(user, 'UPDATE', case, f'Operator response {"accepted" if accept else "rejected"}', old_status=old_status, new_status=case.status)
        return response

    def update_risk(self, case: AuditCase, user, risk_level: str, reason: str = ''):
        rank = {value: index for index, (value, _) in enumerate(AuditCase.RiskLevel.choices)}
        downgraded = (
            case.risk_level in (AuditCase.RiskLevel.CRITICAL, AuditCase.RiskLevel.HIGH)
            and rank.get(risk_level, 0) < rank.get(case.risk_level, 0)
        )
        if downgraded and not reason:
            raise ValueError('A reason is required when downgrading risk from High/Critical.')
        old_risk = case.risk_level
        case.risk_level = risk_level
        case.save(update_fields=['risk_level'])
        self._audit(user, 'UPDATE', case, f'Risk changed from {old_risk} to {risk_level}' + (f': {reason}' if reason else ''),
                    old_risk=old_risk, new_risk=risk_level)
        return case

    def update_priority(self, case: AuditCase, user, priority: str):
        """Operational-urgency change — distinct from update_risk (regulatory severity)."""
        old_priority = case.priority
        case.priority = priority
        case.save(update_fields=['priority'])
        self._audit(user, 'UPDATE', case, f'Priority changed from {old_priority or "—"} to {priority}',
                    old_priority=old_priority, new_priority=priority)
        return case

    def update_exposure(self, case: AuditCase, user, potential_exposure=None, confirmed_exposure=None,
                         recovered_amount=None, adjustment_amount=None):
        if potential_exposure is not None:
            case.potential_exposure = potential_exposure
        if confirmed_exposure is not None:
            case.confirmed_exposure = confirmed_exposure
        if recovered_amount is not None:
            case.recovered_amount = recovered_amount
        if adjustment_amount is not None:
            case.adjustment_amount = adjustment_amount
        case.save()
        self._audit(user, 'UPDATE', case, 'Exposure figures updated')
        return case

    def resolve_case(self, case: AuditCase, user, resolution_type: str, resolution_summary: str, regulatory_decision: str,
                      confirmed_exposure=None, recovered_amount=None, adjustment_amount=None, recommendations: str = ''):
        self._require_transition(case, AuditCase.Status.RESOLVED)
        old_status = case.status
        case.status = AuditCase.Status.RESOLVED
        case.resolved_by = user
        case.resolved_at = timezone.now()
        case.resolution_type = resolution_type
        case.resolution_summary = resolution_summary
        case.regulatory_decision = regulatory_decision
        case.recommendations = recommendations
        if confirmed_exposure is not None:
            case.confirmed_exposure = confirmed_exposure
        if recovered_amount is not None:
            case.recovered_amount = recovered_amount
        if adjustment_amount is not None:
            case.adjustment_amount = adjustment_amount
        case.save()
        self._audit(user, 'UPDATE', case, f'Case resolved: {resolution_type}', old_status=old_status, new_status=case.status)
        return case

    def close_case(self, case: AuditCase, user, closure_notes: str = '', follow_up_required: bool = False, follow_up_date=None):
        if case.status != AuditCase.Status.RESOLVED:
            raise ValueError('Only a resolved case can be closed.')
        old_status = case.status
        case.status = AuditCase.Status.CLOSED
        case.closed_by = user
        case.closed_at = timezone.now()
        case.closure_notes = closure_notes
        case.follow_up_required = follow_up_required
        case.follow_up_date = follow_up_date
        case.save()
        self._audit(user, 'UPDATE', case, 'Case closed', old_status=old_status, new_status=case.status)
        return case

    def reopen_case(self, case: AuditCase, user, reason: str, assigned_to=None, due_date=None):
        if not reason:
            raise ValueError('A reopen reason is required.')
        if case.status not in (AuditCase.Status.RESOLVED, AuditCase.Status.CLOSED):
            raise ValueError('Only a resolved or closed case can be reopened.')
        old_status = case.status
        # Assign a (possibly new) officer on reopen -> ASSIGNED; otherwise the case
        # goes straight back into active INVESTIGATION.
        if assigned_to:
            case.assigned_to = assigned_to
            case.assigned_by = user
            case.assigned_at = timezone.now()
            case.status = AuditCase.Status.ASSIGNED
        else:
            case.status = AuditCase.Status.INVESTIGATION
        if due_date:
            case.due_date = due_date
        case.reopened_by = user
        case.reopened_at = timezone.now()
        case.reopen_reason = reason
        case.reopen_count += 1
        case.save()
        self._audit(user, 'UPDATE', case, f'Case reopened: {reason}', old_status=old_status, new_status=case.status)
        return case

    def add_comment(self, case: AuditCase, user, comment: str, visibility: str = AuditCaseComment.Visibility.INTERNAL):
        entry = AuditCaseComment.objects.create(case=case, author=user, comment=comment, visibility=visibility)
        self._audit(user, 'UPDATE', case, 'Comment added')
        return entry

    # ---- Financial analysis / evidence drill-down --------------------------

    def get_financial_analysis(self, case: AuditCase) -> Dict[str, Any]:
        run = case.reconciliation_run
        is_financial = case.case_type not in (AuditCase.CaseType.DATA_QUALITY, AuditCase.CaseType.TRAFFIC_ANOMALY,
                                               AuditCase.CaseType.MISSING_CDRS)
        analysis = {
            'is_financial': is_financial,
            'expected_revenue': None, 'declared_revenue': None, 'revenue_variance': None,
            'expected_taxable_revenue': None, 'declared_taxable_revenue': None, 'taxable_revenue_variance': None,
            'expected_gst': None, 'declared_gst': None, 'gst_variance': None,
            'potential_exposure': case.potential_exposure, 'confirmed_exposure': case.confirmed_exposure,
            'recovered_amount': case.recovered_amount, 'outstanding_exposure': case.outstanding_exposure, 'services': [],
            'affected_records': None, 'missing_records': None, 'affected_files': None, 'traffic_variance': None,
        }
        if not is_financial and case.risk_alert:
            alert = case.risk_alert
            analysis['affected_records'] = alert.occurrence_count
            if case.case_type == AuditCase.CaseType.MISSING_CDRS:
                analysis['missing_records'] = alert.metric_value
            if case.case_type == AuditCase.CaseType.TRAFFIC_ANOMALY:
                analysis['traffic_variance'] = alert.metric_value - alert.threshold_value
        if run:
            analysis.update({
                'expected_revenue': run.expected_revenue, 'declared_revenue': run.declared_revenue,
                'revenue_variance': run.revenue_variance,
                'expected_taxable_revenue': run.expected_taxable_revenue, 'declared_taxable_revenue': run.declared_taxable_revenue,
                'taxable_revenue_variance': run.taxable_revenue_variance,
                'expected_gst': run.expected_gst, 'declared_gst': run.declared_gst, 'gst_variance': run.gst_variance,
            })
            analysis['services'] = [
                {
                    'service_type': r.service_type, 'expected_revenue': r.mediated_revenue, 'declared_revenue': r.declared_revenue,
                    'variance': r.variance_revenue, 'expected_gst': r.mediated_tax, 'declared_gst': r.declared_tax,
                    'gst_variance': r.variance_tax, 'exposure': abs(r.variance_revenue or 0) + abs(r.variance_tax or 0),
                    'risk': r.match_status,
                }
                for r in run.results.all()
            ]
        return analysis

    def get_cdr_evidence(self, case: AuditCase, filters: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 25) -> Dict[str, Any]:
        """Server-side paginated, masked CDR drill-down for the case's operator/period."""
        return get_masked_cdrs(case.operator_code, case.period_start, case.period_end, filters, page, page_size)

    def get_source_files(self, case: AuditCase, page: int = 1, page_size: int = 25):
        from django.core.paginator import Paginator
        from collection.models import CDRFile

        qs = CDRFile.objects.filter(operator_code=case.operator_code)
        if case.period_start and case.period_end:
            qs = qs.filter(created_at__date__gte=case.period_start, created_at__date__lte=case.period_end)
        qs = qs.order_by('-created_at')
        return Paginator(qs, page_size).get_page(page)

    def _rated_aggregates_for_period(self, case: AuditCase):
        from regulatory.models.aggregates import RatedAggregate
        if not (case.period_start and case.period_end):
            return RatedAggregate.objects.none()
        return RatedAggregate.objects.filter(
            traffic_summary__operator_code=case.operator_code,
            traffic_summary__period_start__date__gte=case.period_start,
            traffic_summary__period_end__date__lte=case.period_end,
        ).select_related('tariff', 'traffic_summary')

    def get_tariffs_and_tax_rules(self, case: AuditCase) -> Dict[str, Any]:
        """The exact tariff/tax-rate *versions* applicable to this case's operator and
        period — never just "whatever is active now". Prefers the tariff actually
        referenced by rated aggregates for the period (exact FK); falls back to any
        tariff/tax-rate version whose effective range overlaps the period."""
        from regulatory.models.tariffs import Tariff
        from regulatory.models.tax import TaxRate

        tariffs, tariff_source = [], 'none'
        tax_rules, tax_source = [], 'none'

        if case.tariff_compliance_result:
            result = case.tariff_compliance_result
            tariffs = [{'tariff': result.applied_tariff, 'note': f'Applied (v{result.applied_tariff_version})'}]
            if result.approved_tariff:
                tariffs.append({'tariff': result.approved_tariff, 'note': f'NatCA reference (v{result.approved_tariff_version})'})
            tariff_source = 'exact'
        else:
            aggregates = self._rated_aggregates_for_period(case)
            applied_tariffs = {a.tariff for a in aggregates if a.tariff}
            if applied_tariffs:
                tariffs = [{'tariff': t, 'note': 'Applied during rating'} for t in applied_tariffs]
                tariff_source = 'exact'
            elif case.period_start and case.period_end:
                overlapping = Tariff.objects.filter(operator_code=case.operator_code, effective_from__lte=case.period_end).filter(
                    Q(effective_to__isnull=True) | Q(effective_to__gte=case.period_start)
                )
                tariffs = [{'tariff': t, 'note': 'Effective during case period'} for t in overlapping]
                tariff_source = 'period_overlap' if tariffs else 'none'

        if case.period_start and case.period_end:
            overlapping_rates = TaxRate.objects.filter(effective_from__lte=case.period_end).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=case.period_start)
            ).select_related('tax_type')
            tax_rules = [{'rate': r, 'note': 'Effective during case period'} for r in overlapping_rates]
            tax_source = 'period_overlap' if tax_rules else 'none'

        return {'tariffs': tariffs, 'tariff_source': tariff_source, 'tax_rules': tax_rules, 'tax_source': tax_source}

    def get_calculation_trace(self, case: AuditCase) -> List[Dict[str, Any]]:
        steps = []

        aggregates = list(self._rated_aggregates_for_period(case).order_by('-created_at')[:1])
        if aggregates:
            sample = aggregates[0]
            ts = sample.traffic_summary
            if ts.service_type == 'DATA':
                usage_desc = f'{ts.data_volume_mb:,.1f} MB'
                usage_qty = f'{ts.data_volume_mb:,.1f}'
                unit_label = 'MB'
            elif ts.service_type == 'SMS':
                usage_desc = f'{ts.sms_count:,} messages'
                usage_qty, unit_label = f'{ts.sms_count:,}', 'message'
            else:
                usage_desc = f'{ts.total_duration_minutes:,.1f} minutes'
                usage_qty, unit_label = f'{ts.total_duration_minutes:,.1f}', 'minute'

            steps.append({
                'label': 'Normalized CDR → Traffic Classification', 'detail': f'{ts.operator_code} · {ts.get_service_type_display()} · {ts.get_traffic_type_display()}',
                'result': usage_desc, 'source': f'TrafficSummary #{ts.pk}', 'timestamp': ts.period_start,
            })
            if sample.tariff:
                t = sample.tariff
                steps.append({
                    'label': 'Applicable Tariff Version', 'detail': f'{t.name} v{t.version} ({t.get_status_display()}), effective {t.effective_from} → {t.effective_to or "open"}',
                    'result': f'{sample.tariff_rate_applied} {t.currency} / {unit_label}', 'source': f'Tariff #{t.pk}', 'timestamp': sample.created_at,
                })
                steps.append({
                    'label': 'Rating Calculation → Expected Revenue', 'detail': f'{usage_qty} {unit_label}(s) × {sample.tariff_rate_applied} {t.currency}',
                    'result': f'{t.currency} {sample.rated_amount:,.2f}', 'source': f'RatedAggregate #{sample.pk}', 'timestamp': sample.created_at,
                    'formula': f'{usage_qty} × {sample.tariff_rate_applied} = {sample.rated_amount:,.2f}',
                })
                steps.append({
                    'label': 'Tax Classification', 'detail': t.get_tax_treatment_display(),
                    'result': t.get_tax_treatment_display(), 'source': f'Tariff #{t.pk}', 'timestamp': sample.created_at,
                })
            steps.append({
                'label': 'Taxable Revenue → Expected GST', 'detail': f'Tax rate {sample.tax_rate_percent}% applied to {sample.currency} {sample.rated_amount:,.2f}',
                'result': f'{sample.currency} {sample.tax_amount:,.2f}', 'source': f'RatedAggregate #{sample.pk}', 'timestamp': sample.created_at,
                'formula': f'{sample.rated_amount:,.2f} × {sample.tax_rate_percent}% = {sample.tax_amount:,.2f}',
            })
            steps.append({
                'label': 'Total Rated Amount', 'detail': 'Revenue + tax',
                'result': f'{sample.currency} {sample.total_amount:,.2f}', 'source': f'RatedAggregate #{sample.pk}', 'timestamp': sample.created_at,
            })

        run = case.reconciliation_run
        if run:
            steps.append({'label': 'Mediated Traffic → Rated Aggregates', 'detail': f'Operator {case.operator_code}, period {run.period_start} → {run.period_end}',
                           'result': '', 'source': 'Mediation pipeline (RatedAggregate)', 'timestamp': run.created_at})
            steps.append({'label': 'Expected Revenue', 'detail': 'Sum of rated aggregates for the period',
                           'result': f'{run.currency if hasattr(run, "currency") else case.currency} {run.expected_revenue:,.2f}',
                           'source': f'Reconciliation Run {run.reference or run.pk}', 'timestamp': run.completed_at or run.created_at})
            steps.append({'label': 'Expected Taxable Revenue', 'detail': 'Expected revenue less non-taxable services',
                           'result': f'{case.currency} {run.expected_taxable_revenue:,.2f}',
                           'source': f'Reconciliation Run {run.reference or run.pk}', 'timestamp': run.completed_at or run.created_at})
            steps.append({'label': 'Expected GST', 'detail': 'Applicable GST rule applied to expected taxable revenue',
                           'result': f'{case.currency} {run.expected_gst:,.2f}',
                           'source': f'Reconciliation Run {run.reference or run.pk}', 'timestamp': run.completed_at or run.created_at})
            steps.append({'label': 'Declared Amount (Operator Declaration)', 'detail': 'As submitted by the operator',
                           'result': f'Revenue {case.currency} {run.declared_revenue:,.2f} / GST {case.currency} {run.declared_gst:,.2f}',
                           'source': f'Declaration {run.declaration.reference}' if run.declaration else 'Operator Declaration', 'timestamp': run.completed_at or run.created_at})
            steps.append({'label': 'Variance', 'detail': 'Expected vs Declared',
                           'result': f'Revenue {run.revenue_variance:,.2f} / GST {run.gst_variance:,.2f}',
                           'source': f'Reconciliation Run {run.reference or run.pk}', 'timestamp': run.completed_at or run.created_at})

        result = case.tariff_compliance_result
        if result and not run:
            steps.append({'label': 'Applied Tariff', 'detail': f'{result.tariff_name} v{result.applied_tariff_version}',
                          'result': f'{result.applied_rate}', 'source': 'Tariff Compliance Check', 'timestamp': result.created_at})
            steps.append({'label': 'Approved Reference Tariff', 'detail': f'v{result.approved_tariff_version or "—"}',
                          'result': f'{result.approved_rate if result.approved_rate is not None else "—"}',
                          'source': 'NatCA reference tariff', 'timestamp': result.created_at})
            steps.append({'label': 'Variance', 'detail': 'Applied vs approved rate',
                          'result': f'{result.variance} ({result.variance_percent}%)' if result.variance is not None else '—',
                          'source': f'Tariff Compliance Result #{result.pk}', 'timestamp': result.last_checked})

        if steps and case.risk_alert:
            steps.append({'label': 'Risk Alert', 'detail': f'{case.risk_alert.get_alert_type_display()} rule breached',
                          'result': case.risk_alert.reference, 'source': 'Risk Alerts', 'timestamp': case.risk_alert.triggered_at})
        if steps:
            steps.append({'label': 'Audit Case', 'detail': 'Escalated for regulatory investigation',
                          'result': case.case_number, 'source': 'Audit Cases', 'timestamp': case.opened_at})

        return steps

    # ---- Export -------------------------------------------------------------

    def export_csv(self, cases) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['Case ID', 'Operator', 'Case Type', 'Period', 'Finding', 'Exposure', 'Risk',
                          'Assigned To', 'Status', 'Opened', 'Last Updated'])
        for case in cases:
            writer.writerow([
                case.case_number, case.operator_code, case.get_case_type_display(),
                f'{case.period_start} - {case.period_end}' if case.period_start else '',
                case.finding_summary, case.potential_exposure, case.get_risk_level_display(),
                case.assigned_to or '-', case.get_status_display(),
                case.opened_at.strftime('%Y-%m-%d') if case.opened_at else '',
                case.updated_at.strftime('%Y-%m-%d') if case.updated_at else '',
            ])
        return buffer.getvalue()

    def export_excel(self, cases):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = 'Audit Cases'
        headers = ['Case ID', 'Operator', 'Case Type', 'Period Start', 'Period End', 'Finding', 'Exposure',
                   'Risk', 'Assigned To', 'Status', 'Opened', 'Last Updated']
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='1075F2')
        for case in cases:
            ws.append([
                case.case_number, case.operator_code, case.get_case_type_display(),
                str(case.period_start or ''), str(case.period_end or ''), case.finding_summary,
                float(case.potential_exposure or 0), case.get_risk_level_display(),
                str(case.assigned_to or ''), case.get_status_display(),
                case.opened_at.strftime('%Y-%m-%d') if case.opened_at else '',
                case.updated_at.strftime('%Y-%m-%d') if case.updated_at else '',
            ])
        for column_cells in ws.columns:
            length = max(len(str(c.value)) for c in column_cells if c.value is not None) if any(c.value for c in column_cells) else 10
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 10), 40)
        return wb

    def _build_report_workbook(self, case: AuditCase):
        """Build a comprehensive single-case Excel report (case summary, findings,
        evidence, financial analysis, operator responses, activity timeline).
        A PDF variant can be added later via reportlab (already a project dependency)."""
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        summary_ws = wb.active
        summary_ws.title = 'Case Summary'
        rows = [
            ('Case ID', case.case_number), ('Operator', case.operator_code),
            ('Case Type', case.get_case_type_display()), ('Status', case.get_status_display()),
            ('Risk Level', case.get_risk_level_display()), ('Period', f'{case.period_start or "—"} to {case.period_end or "—"}'),
            ('Finding Summary', case.finding_summary), ('Potential Exposure', f'{case.currency} {case.potential_exposure}'),
            ('Confirmed Exposure', f'{case.currency} {case.confirmed_exposure}' if case.confirmed_exposure is not None else '—'),
            ('Recovered Amount', f'{case.currency} {case.recovered_amount}' if case.recovered_amount is not None else '—'),
            ('Opened By', str(case.opened_by or '')), ('Opened At', str(case.opened_at)),
            ('Assigned To', str(case.assigned_to or '')), ('Resolution Type', case.get_resolution_type_display() if case.resolution_type else ''),
            ('Resolution Summary', case.resolution_summary),
        ]
        for label, value in rows:
            summary_ws.append([label, value])
        for cell in summary_ws['A']:
            cell.font = Font(bold=True)

        findings_ws = wb.create_sheet('Findings')
        findings_ws.append(['Title', 'Type', 'Severity', 'Description', 'Expected', 'Observed', 'Variance', 'Exposure', 'Status'])
        for f in case.findings.all():
            findings_ws.append([f.title, f.get_finding_type_display(), f.get_severity_display(), f.description,
                                 f.expected_value, f.observed_value, f.variance, f.financial_exposure, f.get_status_display()])

        evidence_ws = wb.create_sheet('Evidence')
        evidence_ws.append(['Type', 'Description', 'Source Type', 'Reference', 'Added By', 'Added Date'])
        for e in case.evidence.all():
            evidence_ws.append([e.get_evidence_type_display(), e.description, e.source_type, e.reference_id,
                                 str(e.created_by or ''), e.created_at.strftime('%Y-%m-%d %H:%M') if e.created_at else ''])

        responses_ws = wb.create_sheet('Operator Responses')
        responses_ws.append(['Subject', 'Requested', 'Due', 'Submitted', 'Submitted By', 'Status'])
        for r in case.operator_responses.all():
            responses_ws.append([r.request_subject, str(r.requested_at), str(r.due_date or ''),
                                  str(r.submitted_at or ''), r.submitted_by, r.get_response_status_display()])

        activity_ws = wb.create_sheet('Activity Log')
        activity_ws.append(['Timestamp', 'User', 'Action', 'Description'])
        for entry in self.get_activity_log(case):
            activity_ws.append([str(entry.timestamp), str(entry.user or ''), entry.action, entry.description])

        return wb

    def generate_report(self, case: AuditCase, user) -> AuditCaseReport:
        """Generate a new, numbered report artifact for the case. Never overwrites a
        prior issued report — each call creates a new AuditCaseReport version."""
        import hashlib
        from django.core.files.base import ContentFile

        wb = self._build_report_workbook(case)
        buffer = io.BytesIO()
        wb.save(buffer)
        content = buffer.getvalue()
        checksum = hashlib.sha256(content).hexdigest()
        version = AuditCaseReport.next_version(case)

        report = AuditCaseReport(
            case=case, version=version, file_format='XLSX', checksum=checksum,
            case_status_at_generation=case.status, generated_by=user,
        )
        report.file.save(f'{case.case_number}_v{version}.xlsx', ContentFile(content), save=False)
        report.save()
        self._audit(user, 'EXPORT', case, f'Audit report generated (v{version})', version=version)
        return report

    def get_report_history(self, case: AuditCase):
        return case.reports.select_related('generated_by').all()
