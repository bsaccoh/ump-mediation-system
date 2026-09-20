"""
Audit Case Management Views for NRA

List/filter/export audit cases, open a case (manually, or escalated from a
Risk Alert / Reconciliation Run), and drive the full investigation workspace:
assignment, investigation, findings, evidence, operator responses, risk and
exposure updates, resolution, closure and reopening.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models.audit import AuditCase, AuditFinding, AuditEvidence, AuditOperatorResponse
from regulatory.models.risk import RiskAlert
from regulatory.models.reconciliation import ReconciliationRun
from regulatory.models.declarations import OperatorDeclaration
from regulatory.models.compliance import TariffComplianceResult
from regulatory.services.audit_case_service import AuditCaseService
from reference.models import Operator

SUPERVISOR_ACTIONS = {'assign', 'update_risk', 'resolve', 'close', 'reopen'}


def _is_supervisor(user):
    return user.is_superuser or getattr(user, 'is_regulatory_admin', False)


def _filters(request):
    return {key: request.GET.get(key, '').strip() for key in
            ('operator', 'case_type', 'status', 'risk_level', 'assigned_officer', 'start_date', 'end_date', 'search', 'sort')}


def _to_decimal(value, fallback=None):
    if value in (None, ''):
        return fallback
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return fallback


def _officers():
    return get_user_model().objects.filter(
        Q(is_regulator=True) | Q(is_auditor=True) | Q(is_regulatory_admin=True) | Q(is_superuser=True),
        is_active=True,
    ).distinct().order_by('username')


def _choice_context():
    return {
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'case_type_choices': AuditCase.CaseType.choices,
        'status_choices': AuditCase.Status.choices,
        'risk_choices': AuditCase.RiskLevel.choices,
        'priority_choices': AuditCase.Priority.choices,
        'officers': _officers(),
    }


@login_required
@regulator_required
def audit_case_list(request):
    """List audit cases with filters, KPI summary, sorting, search and pagination."""
    filters = _filters(request)
    service = AuditCaseService()
    qs = service.get_cases(filters)

    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'cases': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'summary': service.get_summary(),
        'filters': filters, 'query_string': query_params.urlencode(),
        **_choice_context(),
    }
    return render(request, 'regulatory/nra/audit/list.html', context)


@login_required
@regulator_required
def audit_case_export(request):
    """Export the currently-filtered case list as CSV or Excel."""
    filters = _filters(request)
    service = AuditCaseService()
    qs = service.get_cases(filters)

    if request.GET.get('format') in ('excel', 'xlsx'):
        wb = service.export_excel(qs)
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="audit_cases.xlsx"'
        wb.save(response)
        return response

    response = HttpResponse(service.export_csv(qs), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit_cases.csv"'
    return response


@login_required
@regulatory_admin_required
def audit_case_create(request):
    """Open a new audit case — manually, or escalated from a Risk Alert / Reconciliation Run."""
    service = AuditCaseService()

    if request.method == 'POST':
        assigned_to_id = request.POST.get('assigned_to') or None
        assigned_to = get_object_or_404(get_user_model(), pk=assigned_to_id) if assigned_to_id else None
        overrides = {
            'assigned_to': assigned_to,
            'priority': request.POST.get('priority', ''),
            'due_date': parse_date(request.POST.get('due_date', '') or ''),
        }

        risk_alert_id = request.POST.get('risk_alert')
        reconciliation_run_id = request.POST.get('reconciliation_run')

        if risk_alert_id:
            alert = get_object_or_404(RiskAlert, pk=risk_alert_id)
            case = service.create_from_risk_alert(alert, request.user, **overrides)
        elif reconciliation_run_id:
            run = get_object_or_404(ReconciliationRun, pk=reconciliation_run_id)
            case = service.create_from_reconciliation(run, request.user, **overrides)
        else:
            declaration_id = request.POST.get('declaration') or None
            compliance_id = request.POST.get('tariff_compliance_result') or None
            data = {
                'operator_code': request.POST.get('operator_code', '').strip(),
                'case_type': request.POST.get('case_type', AuditCase.CaseType.OTHER),
                'period_start': parse_date(request.POST.get('period_start', '') or ''),
                'period_end': parse_date(request.POST.get('period_end', '') or ''),
                'description': request.POST.get('finding', '').strip(),
                'finding_summary': request.POST.get('finding', '').strip()[:255],
                'risk_level': request.POST.get('risk_level', AuditCase.RiskLevel.MEDIUM),
                'potential_exposure': _to_decimal(request.POST.get('potential_exposure'), Decimal('0')),
                'declaration': get_object_or_404(OperatorDeclaration, pk=declaration_id) if declaration_id else None,
                'tariff_compliance_result': get_object_or_404(TariffComplianceResult, pk=compliance_id) if compliance_id else None,
                **overrides,
            }
            case = service.create_case(data, request.user)

        notes = request.POST.get('notes', '').strip()
        scope = request.POST.get('investigation_scope', '').strip()
        if notes or scope:
            case.description = '\n\n'.join(filter(None, [case.description, scope, notes]))
            case.save(update_fields=['description'])

        evidence_text = request.POST.get('supporting_evidence', '').strip()
        if evidence_text:
            service.add_evidence(case, request.user, evidence_type=AuditEvidence.EvidenceType.ANALYST_NOTE,
                                  description=evidence_text)

        messages.success(request, f'Audit case {case.case_number} opened.')
        return redirect('regulatory:audit_case_detail', pk=case.pk)

    context = {
        'risk_alerts': RiskAlert.objects.filter(audit_case__isnull=True).select_related('rule').order_by('-triggered_at')[:200],
        'reconciliation_runs': ReconciliationRun.objects.filter(audit_cases__isnull=True).order_by('-created_at')[:200],
        'declarations': OperatorDeclaration.objects.filter(is_current_version=True).order_by('-period_start')[:200],
        'compliance_results': TariffComplianceResult.objects.filter(finding_created=False).order_by('-last_checked')[:200],
        **_choice_context(),
    }
    return render(request, 'regulatory/nra/audit/form.html', context)


@login_required
@regulator_required
def audit_case_detail(request, pk):
    """The case investigation workspace: overview, findings, evidence, financial
    analysis, CDRs, source files, calculations, operator responses, comments,
    activity log — plus the workflow actions for each."""
    service = AuditCaseService()
    case = service.get_case(pk)
    active_tab = request.GET.get('tab', 'overview')

    if request.method == 'POST':
        action = request.POST.get('action')
        redirect_tab = active_tab

        if action in SUPERVISOR_ACTIONS and not _is_supervisor(request.user):
            messages.error(request, 'Only a regulatory supervisor can perform that action.')
            return redirect(f"{request.path}?tab={active_tab}")

        try:
            if action == 'assign':
                assigned_to_id = request.POST.get('assigned_to') or None
                assigned_to = get_object_or_404(get_user_model(), pk=assigned_to_id) if assigned_to_id else None
                service.assign_case(case, assigned_to, request.user, request.POST.get('priority', ''),
                                     parse_date(request.POST.get('due_date', '') or ''), request.POST.get('notes', ''))
                messages.success(request, 'Case assigned.')

            elif action == 'start_investigation':
                service.start_investigation(case, request.user)
                messages.success(request, 'Investigation started.')

            elif action == 'add_finding':
                service.add_finding(
                    case, request.user, title=request.POST.get('title', '').strip(),
                    finding_type=request.POST.get('finding_type', AuditFinding.FindingType.OTHER),
                    severity=request.POST.get('severity', AuditCase.RiskLevel.MEDIUM),
                    description=request.POST.get('description', '').strip(),
                    expected_value=_to_decimal(request.POST.get('expected_value'), None),
                    observed_value=_to_decimal(request.POST.get('observed_value'), None),
                    financial_exposure=_to_decimal(request.POST.get('financial_exposure'), Decimal('0')),
                    recommendation=request.POST.get('recommendation', '').strip(),
                )
                messages.success(request, 'Finding added.')
                redirect_tab = 'findings'

            elif action == 'add_evidence':
                service.add_evidence(
                    case, request.user, evidence_type=request.POST.get('evidence_type', AuditEvidence.EvidenceType.ANALYST_NOTE),
                    description=request.POST.get('description', '').strip(),
                    source_type=request.POST.get('source_type', '').strip(),
                    reference_id=request.POST.get('reference_id', '').strip(),
                    reference_url=request.POST.get('reference_url', '').strip(),
                    file=request.FILES.get('file'), checksum=request.POST.get('checksum', '').strip(),
                )
                messages.success(request, 'Evidence added.')
                redirect_tab = 'evidence'

            elif action == 'request_operator_response':
                service.request_operator_response(
                    case, request.user, request.POST.get('subject', '').strip(), request.POST.get('details', '').strip(),
                    request.POST.get('required_evidence', '').strip(), parse_date(request.POST.get('due_date', '') or ''),
                )
                messages.success(request, 'Operator response requested.')
                redirect_tab = 'operator_responses'

            elif action == 'record_operator_response':
                response = get_object_or_404(AuditOperatorResponse, pk=request.POST.get('response_id'), case=case)
                service.record_operator_response(response, request.POST.get('response_text', '').strip(),
                                                  request.POST.get('submitted_by', '').strip())
                messages.success(request, 'Operator response recorded.')
                redirect_tab = 'operator_responses'

            elif action == 'review_operator_response':
                response = get_object_or_404(AuditOperatorResponse, pk=request.POST.get('response_id'), case=case)
                service.review_operator_response(response, request.user, request.POST.get('decision') == 'accept',
                                                  request.POST.get('review_notes', '').strip())
                messages.success(request, 'Operator response reviewed.')
                redirect_tab = 'operator_responses'

            elif action == 'update_finding':
                finding = get_object_or_404(AuditFinding, pk=request.POST.get('finding_id'), case=case)
                service.update_finding(
                    finding, request.user, title=request.POST.get('title', '').strip(),
                    finding_type=request.POST.get('finding_type', finding.finding_type),
                    severity=request.POST.get('severity', finding.severity),
                    description=request.POST.get('description', finding.description).strip(),
                    expected_value=_to_decimal(request.POST.get('expected_value'), finding.expected_value),
                    observed_value=_to_decimal(request.POST.get('observed_value'), finding.observed_value),
                    financial_exposure=_to_decimal(request.POST.get('financial_exposure'), finding.financial_exposure),
                    recommendation=request.POST.get('recommendation', finding.recommendation).strip(),
                )
                messages.success(request, 'Finding updated.')
                redirect_tab = 'findings'

            elif action == 'confirm_finding':
                finding = get_object_or_404(AuditFinding, pk=request.POST.get('finding_id'), case=case)
                service.confirm_finding(finding, request.user)
                messages.success(request, 'Finding confirmed.')
                redirect_tab = 'findings'

            elif action == 'resolve_finding':
                finding = get_object_or_404(AuditFinding, pk=request.POST.get('finding_id'), case=case)
                service.resolve_finding(finding, request.user)
                messages.success(request, 'Finding marked resolved.')
                redirect_tab = 'findings'

            elif action == 'link_evidence_finding':
                evidence = get_object_or_404(AuditEvidence, pk=request.POST.get('evidence_id'), case=case)
                finding_id = request.POST.get('finding_id') or None
                finding = get_object_or_404(AuditFinding, pk=finding_id, case=case) if finding_id else None
                service.link_evidence_to_finding(evidence, finding, request.user)
                messages.success(request, 'Evidence linked to finding.')
                redirect_tab = 'evidence'

            elif action == 'update_risk':
                service.update_risk(case, request.user, request.POST.get('risk_level', ''), request.POST.get('reason', '').strip())
                messages.success(request, 'Risk level updated.')

            elif action == 'update_priority':
                service.update_priority(case, request.user, request.POST.get('priority', ''))
                messages.success(request, 'Priority updated.')

            elif action == 'update_exposure':
                service.update_exposure(
                    case, request.user,
                    potential_exposure=_to_decimal(request.POST.get('potential_exposure'), None),
                    confirmed_exposure=_to_decimal(request.POST.get('confirmed_exposure'), None),
                    recovered_amount=_to_decimal(request.POST.get('recovered_amount'), None),
                    adjustment_amount=_to_decimal(request.POST.get('adjustment_amount'), None),
                )
                messages.success(request, 'Exposure figures updated.')

            elif action == 'resolve':
                resolution_type = request.POST.get('resolution_type', '')
                resolution_summary = request.POST.get('resolution_summary', '').strip()
                regulatory_decision = request.POST.get('regulatory_decision', '')
                if not resolution_type or not resolution_summary or not regulatory_decision:
                    messages.error(request, 'Resolution type, resolution summary and regulatory decision are required.')
                else:
                    service.resolve_case(
                        case, request.user, resolution_type, resolution_summary, regulatory_decision,
                        _to_decimal(request.POST.get('confirmed_exposure'), None),
                        _to_decimal(request.POST.get('recovered_amount'), None),
                        _to_decimal(request.POST.get('adjustment_amount'), None),
                        request.POST.get('recommendations', '').strip(),
                    )
                    messages.success(request, 'Case resolved.')

            elif action == 'close':
                service.close_case(
                    case, request.user, request.POST.get('closure_notes', '').strip(),
                    request.POST.get('follow_up_required') == 'on',
                    parse_date(request.POST.get('follow_up_date', '') or ''),
                )
                messages.success(request, 'Case closed.')

            elif action == 'reopen':
                reopen_officer_id = request.POST.get('reopen_assigned_to') or None
                reopen_officer = get_object_or_404(get_user_model(), pk=reopen_officer_id) if reopen_officer_id else None
                service.reopen_case(
                    case, request.user, request.POST.get('reopen_reason', '').strip(),
                    assigned_to=reopen_officer, due_date=parse_date(request.POST.get('reopen_due_date', '') or ''),
                )
                messages.success(request, 'Case reopened.')

            elif action == 'comment':
                comment = request.POST.get('comment', '').strip()
                if comment:
                    service.add_comment(case, request.user, comment, request.POST.get('visibility', 'INTERNAL'))
                    messages.success(request, 'Comment added.')
                redirect_tab = 'comments'
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect(f"{request.path}?tab={redirect_tab}")

    cdr_data, source_files, calculation_trace, tariffs_tax = None, None, None, None
    if active_tab == 'cdrs':
        cdr_filters = {
            'date_from': parse_date(request.GET.get('date_from', '') or ''),
            'date_end': parse_date(request.GET.get('date_end', '') or ''),
            'service_type': request.GET.get('service_type', ''),
            'record_type': request.GET.get('record_type', ''),
            'direction': request.GET.get('direction', ''),
            'source_file': request.GET.get('source_file', ''),
            'search': request.GET.get('cdr_search', ''),
        }
        try:
            cdr_page_size = int(request.GET.get('cdr_page_size', 50))
        except ValueError:
            cdr_page_size = 50
        cdr_page_size = cdr_page_size if cdr_page_size in {50, 100, 250} else 50
        cdr_data = service.get_cdr_evidence(case, cdr_filters, page=int(request.GET.get('cdr_page', 1) or 1), page_size=cdr_page_size)
    elif active_tab == 'source_files':
        source_files = service.get_source_files(case, page=request.GET.get('sf_page', 1))
    elif active_tab == 'calculations':
        calculation_trace = service.get_calculation_trace(case)
    elif active_tab == 'tariff_tax':
        tariffs_tax = service.get_tariffs_and_tax_rules(case)

    context = {
        'case': case, 'active_tab': active_tab,
        'findings': case.findings.select_related('created_by').all(),
        'evidence': case.evidence.select_related('created_by', 'finding').all(),
        'operator_responses': case.operator_responses.select_related('requested_by', 'reviewed_by').all(),
        'comments': case.comments.select_related('author').all(),
        'activity_log': service.get_activity_log(case),
        'financial_analysis': service.get_financial_analysis(case),
        'reconciliation_run': case.reconciliation_run,
        'reconciliation_services': case.reconciliation_run.results.all() if case.reconciliation_run else [],
        'declaration': case.declaration,
        'report_history': service.get_report_history(case),
        'cdr_data': cdr_data, 'source_files': source_files, 'calculation_trace': calculation_trace, 'tariffs_tax': tariffs_tax,
        'is_supervisor': _is_supervisor(request.user),
        'case_next_statuses': case.allowed_next_statuses(),
        'case_reopenable': case.status in (AuditCase.Status.RESOLVED, AuditCase.Status.CLOSED),
        'finding_type_choices': AuditFinding.FindingType.choices,
        'finding_status_choices': AuditFinding.Status.choices,
        'evidence_type_choices': AuditEvidence.EvidenceType.choices,
        'resolution_choices': AuditCase.ResolutionType.choices,
        'regulatory_decision_choices': AuditCase.RegulatoryDecision.choices,
        **_choice_context(),
    }
    return render(request, 'regulatory/nra/audit/detail.html', context)


@login_required
@regulator_required
def audit_case_report(request, pk):
    """Generate a new, numbered audit-report artifact for the case and download it.
    Prior issued reports are never overwritten — see the Report History on Overview."""
    service = AuditCaseService()
    case = service.get_case(pk)
    report = service.generate_report(case, request.user)
    response = HttpResponse(report.file.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{case.case_number}_v{report.version}.xlsx"'
    return response
