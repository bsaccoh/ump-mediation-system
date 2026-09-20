"""
Risk Management Views for NRA

Manage risk rules and risk alerts: list/filter/export alerts, and drive the
alert investigation workflow (assign, investigate, request operator response,
resolve, dismiss, escalate to an audit case).
"""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models.risk import RiskRule, RiskRuleVersion, RiskAlert, RiskAlertOperatorResponse
from regulatory.services.risk_engine import RiskEngine
from reference.models import Operator

SUPERVISOR_ACTIONS = {'resolve'}


def _is_supervisor(user):
    return user.is_superuser or getattr(user, 'is_regulatory_admin', False)


RECOMMENDED_ACTIONS = {
    'TAX': 'Verify the tax classification and confirm the variance against the tax rule version applied.',
    'GST': 'Reconcile declared GST against expected GST and confirm the applied tax rate.',
    'REVENUE': 'Compare declared revenue against mediated revenue and request supporting evidence if the gap persists.',
    'DATA_QUALITY': 'Investigate the missing/unrated CDR volume with the operator and confirm mediation feed completeness.',
    'COMPLIANCE': 'Confirm the classification or tariff applied and request operator correction if incorrect.',
    'TARIFF': 'Compare the applied rate against the approved tariff version and confirm with the operator.',
    'TRAFFIC': 'Review the traffic pattern against historical baselines to confirm the anomaly.',
    'DECLARATION': 'Cross-check the declaration against mediated data and request an amendment if required.',
    'RECONCILIATION': 'Review the reconciliation discrepancy and confirm resolution with the operator.',
    'DEFAULT': 'Review the underlying source data and confirm findings with the operator.',
}


def _filters(request):
    return {key: request.GET.get(key, '').strip() for key in
            ('operator', 'severity', 'status', 'alert_type', 'start_date', 'end_date', 'search', 'sort')}


def _to_decimal(value, fallback):
    if value in (None, ''):
        return fallback
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return fallback


def _rule_choice_context(rule=None):
    return {
        'rule': rule,
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'alert_type_choices': RiskRule.AlertType.choices,
        'comparison_choices': RiskRule.ComparisonOperator.choices,
        'unit_choices': RiskRule.ThresholdUnit.choices,
        'severity_choices': RiskRule.Severity.choices,
        'status_choices': RiskRule.Status.choices,
    }


@login_required
@regulator_required
def risk_rule_list(request):
    """List and manage risk rules."""
    qs = RiskRule.objects.all()

    operator = request.GET.get('operator', '')
    alert_type = request.GET.get('alert_type', '')
    status = request.GET.get('status', '')

    if operator:
        qs = qs.filter(operator_code=operator)
    if alert_type:
        qs = qs.filter(alert_type=alert_type)
    if status:
        qs = qs.filter(status=status)

    context = {
        'rules': qs,
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'filter_operator': operator,
        'filter_alert_type': alert_type,
        'filter_status': status,
        'alert_type_choices': RiskRule.AlertType.choices,
        'status_choices': RiskRule.Status.choices,
    }
    return render(request, 'regulatory/nra/risk/rules.html', context)


@login_required
@regulatory_admin_required
def risk_rule_create(request):
    """Create a new risk rule (configuration-driven: comparison operator + threshold + unit)."""
    if request.method == 'POST':
        rule = RiskRule(
            name=request.POST.get('name', '').strip(),
            code=request.POST.get('code', '').strip(),
            alert_type=request.POST.get('alert_type', RiskRule.AlertType.OTHER),
            metric=request.POST.get('metric', '').strip(),
            operator_code=request.POST.get('operator_code', '').strip(),
            service_scope=request.POST.get('service_scope', '').strip(),
            comparison_operator=request.POST.get('comparison_operator', RiskRule.ComparisonOperator.GT),
            threshold_value=_to_decimal(request.POST.get('threshold_value'), Decimal('0')),
            threshold_unit=request.POST.get('threshold_unit', RiskRule.ThresholdUnit.COUNT),
            severity=request.POST.get('severity', RiskRule.Severity.MEDIUM),
            status=request.POST.get('status', RiskRule.Status.DRAFT),
            description=request.POST.get('description', '').strip(),
            min_duration=request.POST.get('min_duration') or None,
            min_occurrences=request.POST.get('min_occurrences') or None,
            lookback_period=request.POST.get('lookback_period') or None,
            auto_create_alert=request.POST.get('auto_create_alert') == 'on',
            auto_escalate=request.POST.get('auto_escalate') == 'on',
            escalation_threshold=request.POST.get('escalation_threshold') or None,
            created_by=request.user,
        )
        rule.save()
        messages.success(request, f'Risk rule "{rule.name}" created.')
        return redirect('regulatory:risk_alert_list')

    return render(request, 'regulatory/nra/risk/rule_form.html', _rule_choice_context())


@login_required
@regulatory_admin_required
def risk_rule_edit(request, pk):
    """Edit an existing risk rule, preserving history when material fields change."""
    rule = get_object_or_404(RiskRule, pk=pk)

    if request.method == 'POST':
        material_fields = ('metric', 'comparison_operator', 'threshold_value', 'threshold_unit', 'severity')
        pre_change_values = {field: getattr(rule, field) for field in material_fields}

        rule.name = request.POST.get('name', rule.name).strip()
        rule.code = request.POST.get('code', rule.code).strip()
        rule.alert_type = request.POST.get('alert_type', rule.alert_type)
        rule.metric = request.POST.get('metric', rule.metric).strip()
        rule.operator_code = request.POST.get('operator_code', rule.operator_code).strip()
        rule.service_scope = request.POST.get('service_scope', rule.service_scope).strip()
        rule.comparison_operator = request.POST.get('comparison_operator', rule.comparison_operator)
        rule.threshold_value = _to_decimal(request.POST.get('threshold_value'), rule.threshold_value)
        rule.threshold_unit = request.POST.get('threshold_unit', rule.threshold_unit)
        rule.severity = request.POST.get('severity', rule.severity)
        rule.status = request.POST.get('status', rule.status)
        rule.description = request.POST.get('description', rule.description)
        rule.min_duration = request.POST.get('min_duration') or None
        rule.min_occurrences = request.POST.get('min_occurrences') or None
        rule.lookback_period = request.POST.get('lookback_period') or None
        rule.auto_create_alert = request.POST.get('auto_create_alert') == 'on'
        rule.auto_escalate = request.POST.get('auto_escalate') == 'on'
        rule.escalation_threshold = request.POST.get('escalation_threshold') or None

        material_changed = any(pre_change_values[field] != getattr(rule, field) for field in material_fields)
        if material_changed:
            RiskRuleVersion.snapshot(RiskRule.objects.get(pk=rule.pk), user=request.user)
            rule.version += 1
            rule.effective_from = date.today()

        rule.save()
        messages.success(request, f'Risk rule "{rule.name}" updated.')
        return redirect('regulatory:risk_alert_list')

    return render(request, 'regulatory/nra/risk/rule_form.html', _rule_choice_context(rule))


@login_required
@regulator_required
def risk_alert_list(request):
    """List risk alerts with severity/status/type filters, search, sort and pagination."""
    filters = _filters(request)
    engine = RiskEngine()
    qs = engine.get_alerts(filters)

    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'alerts': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'summary': engine.get_summary(),
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'filters': filters, 'query_string': query_params.urlencode(),
        'severity_choices': RiskRule.Severity.choices,
        'status_choices': RiskAlert.Status.choices,
        'alert_type_choices': RiskRule.AlertType.choices,
    }
    return render(request, 'regulatory/nra/risk/alerts.html', context)


@login_required
@regulator_required
def risk_alert_export(request):
    """Export the currently-filtered alert list as CSV."""
    filters = _filters(request)
    qs = RiskEngine().get_alerts(filters).select_related('rule', 'assigned_to')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="risk_alerts.csv"'
    writer = csv.writer(response)
    writer.writerow(['Alert ID', 'Operator', 'Rule', 'Type', 'Severity', 'Metric Value', 'Threshold',
                      'Status', 'Source', 'Triggered', 'Assigned To', 'Potential Exposure'])
    for alert in qs:
        writer.writerow([
            alert.reference, alert.operator_code, alert.rule.name if alert.rule else '',
            alert.get_alert_type_display(), alert.get_severity_display(), alert.metric_value,
            alert.threshold_value, alert.get_status_display(), alert.get_source_type_display(),
            alert.triggered_at.strftime('%Y-%m-%d %H:%M') if alert.triggered_at else '',
            alert.assigned_to or '-', alert.potential_exposure,
        ])
    return response


@login_required
@regulator_required
def risk_alert_evidence_export(request, identifier):
    """Download a single alert's full evidence package (summary, rule snapshot,
    evidence list, financial exposure, comments, operator responses, activity log)."""
    engine = RiskEngine()
    alert = engine.get_alert(identifier)
    wb = engine.generate_evidence_package(alert)
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{alert.reference}_evidence.xlsx"'
    wb.save(response)
    return response


@login_required
@regulator_required
def risk_alert_detail(request, identifier):
    """The alert investigation workspace: overview, rule & threshold, source evidence,
    financial exposure, reconciliation, declaration, CDRs, operator responses, comments,
    activity log — plus the workflow actions for each. `identifier` may be the numeric
    pk or the alert's human reference (e.g. RA-2026-0024)."""
    engine = RiskEngine()
    alert = engine.get_alert(identifier)
    active_tab = request.GET.get('tab', 'overview')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action in SUPERVISOR_ACTIONS and not _is_supervisor(request.user):
            messages.error(request, 'Only a regulatory supervisor can perform that action.')
            return redirect(f"{request.path}?tab={active_tab}")

        try:
            if action == 'assign':
                assigned_to_id = request.POST.get('assigned_to') or None
                assigned_to = get_object_or_404(get_user_model(), pk=assigned_to_id) if assigned_to_id else None
                engine.assign_alert(
                    alert, assigned_to, request.user, request.POST.get('priority', ''),
                    parse_date(request.POST.get('due_date', '') or ''), request.POST.get('notes', ''),
                )
                messages.success(request, 'Alert assigned.')

            elif action == 'investigate':
                engine.start_investigation(alert, request.user, request.POST.get('notes', ''))
                messages.success(request, 'Investigation started.')

            elif action == 'update_severity':
                engine.update_severity(alert, request.user, request.POST.get('severity', ''), request.POST.get('reason', '').strip())
                messages.success(request, 'Severity updated.')

            elif action == 'request_operator_response':
                engine.request_operator_response(
                    alert, request.user, request.POST.get('subject', ''), request.POST.get('details', ''),
                    request.POST.get('evidence', ''), parse_date(request.POST.get('due_date', '') or ''),
                    request.POST.get('priority', ''), request.POST.get('notes', ''),
                )
                messages.success(request, 'Operator response requested.')
                active_tab = 'operator_response'

            elif action == 'record_operator_response':
                response = get_object_or_404(RiskAlertOperatorResponse, pk=request.POST.get('response_id'), alert=alert)
                engine.record_operator_response(response, request.POST.get('response_text', '').strip(),
                                                 request.POST.get('submitted_by', '').strip())
                messages.success(request, 'Operator response recorded.')
                active_tab = 'operator_response'

            elif action == 'review_operator_response':
                response = get_object_or_404(RiskAlertOperatorResponse, pk=request.POST.get('response_id'), alert=alert)
                engine.review_operator_response(response, request.user, request.POST.get('decision') == 'accept',
                                                 request.POST.get('review_notes', '').strip())
                messages.success(request, 'Operator response reviewed.')
                active_tab = 'operator_response'

            elif action == 'resolve':
                resolution_type = request.POST.get('resolution_type', '')
                notes = request.POST.get('notes', '').strip()
                if not resolution_type or not notes:
                    messages.error(request, 'Resolution type and resolution notes are required.')
                else:
                    engine.resolve_alert(
                        alert, request.user, resolution_type, notes,
                        request.POST.get('recovered_amount') or None, request.POST.get('confirmed_exposure') or None,
                    )
                    messages.success(request, 'Alert resolved.')

            elif action == 'dismiss':
                reason = request.POST.get('dismissal_reason', '')
                if not reason:
                    messages.error(request, 'A dismissal reason is required.')
                else:
                    engine.dismiss_alert(alert, request.user, reason, request.POST.get('notes', ''))
                    messages.success(request, 'Alert dismissed.')

            elif action == 'comment':
                comment = request.POST.get('comment', '').strip()
                if comment:
                    engine.add_comment(alert, request.user, comment)
                    messages.success(request, 'Comment added.')
                active_tab = 'comments'

            elif action == 'create_audit_case':
                officer_id = request.POST.get('assigned_officer') or None
                officer = get_object_or_404(get_user_model(), pk=officer_id) if officer_id else None
                force = request.POST.get('force_new_case') == 'on' and _is_supervisor(request.user)
                case = engine.create_audit_case(
                    alert, request.user, case_type=request.POST.get('case_type', ''), assigned_officer=officer,
                    priority=request.POST.get('priority', ''), investigation_scope=request.POST.get('investigation_scope', ''),
                    notes=request.POST.get('notes', ''), force=force,
                )
                messages.success(request, f'Audit case {case.case_number} created and linked.')
                return redirect('regulatory:audit_case_detail', pk=case.pk)
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect(f"{request.path}?tab={active_tab}")

    cdr_data = None
    if active_tab == 'cdrs':
        cdr_filters = {
            'date_from': parse_date(request.GET.get('date_from', '') or ''),
            'date_end': parse_date(request.GET.get('date_end', '') or ''),
            'service_type': request.GET.get('service_type', ''),
            'record_type': request.GET.get('record_type', ''),
            'source_file': request.GET.get('source_file', ''),
        }
        try:
            page_size = int(request.GET.get('cdr_page_size', 50))
        except ValueError:
            page_size = 50
        page_size = page_size if page_size in {50, 100, 250} else 50
        cdr_data = engine.get_cdrs(alert, cdr_filters, page=int(request.GET.get('cdr_page', 1) or 1), page_size=page_size)

    context = {
        'alert': alert, 'active_tab': active_tab,
        'evidence': engine.get_evidence(alert),
        'related': engine.get_related_objects(alert),
        'rule_snapshot': engine.get_rule_snapshot(alert),
        'financial_exposure': engine.get_financial_exposure(alert),
        'reconciliation': engine.get_reconciliation(alert),
        'declaration': engine.get_declaration(alert),
        'cdr_data': cdr_data,
        'operator_requests': engine.get_operator_requests(alert),
        'comments': alert.comments.select_related('author').all(),
        'activity_log': engine.get_activity_log(alert),
        'alert_next_statuses': alert.allowed_next_statuses(),
        'is_supervisor': _is_supervisor(request.user),
        'has_open_audit_case': bool(alert.audit_case_id and alert.audit_case.status != 'CLOSED'),
        'users': get_user_model().objects.filter(is_active=True).order_by('username'),
        'resolution_choices': RiskAlert.ResolutionType.choices,
        'dismissal_choices': RiskAlert.DismissalReason.choices,
        'severity_choices': RiskRule.Severity.choices,
        'recommended_action': RECOMMENDED_ACTIONS.get(alert.alert_type, RECOMMENDED_ACTIONS['DEFAULT']),
    }
    return render(request, 'regulatory/nra/risk/alert_detail.html', context)
