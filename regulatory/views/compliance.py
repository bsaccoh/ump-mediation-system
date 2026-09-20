import csv
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models import AuditCase, AuditEvidence, AuditFinding, Tariff, TariffComplianceResult
from regulatory.services.audit_case_service import AuditCaseService
from regulatory.services.tariff_compliance import TariffComplianceService
from regulatory.views.tariffs import _operator_options


def _filtered_results(request):
    results = TariffComplianceResult.objects.select_related('applied_tariff', 'approved_tariff', 'finding_case')
    filters = {
        'operator': request.GET.get('operator', ''), 'service': request.GET.get('service', ''),
        'traffic': request.GET.get('traffic', ''), 'status': request.GET.get('status', ''),
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
        'search': request.GET.get('search', '').strip(),
    }
    for field, lookup in (('operator', 'operator_code'), ('service', 'service_type'), ('traffic', 'traffic_type'), ('status', 'status')):
        if filters[field]:
            results = results.filter(**{lookup: filters[field]})
    if parse_date(filters['date_from']):
        results = results.filter(effective_date__gte=filters['date_from'])
    if parse_date(filters['date_to']):
        results = results.filter(effective_date__lte=filters['date_to'])
    if filters['search']:
        results = results.filter(Q(tariff_name__icontains=filters['search']) | Q(operator_code__icontains=filters['search']))
    sort_options = {'checked': '-last_checked', 'name': 'tariff_name', '-name': '-tariff_name',
                    'operator': 'operator_code', 'variance': '-variance_percent', '-variance': 'variance_percent'}
    ordering = request.GET.get('sort', 'checked')
    return results.order_by(sort_options.get(ordering, '-last_checked')), filters, ordering


@login_required
@regulator_required
def tariff_compliance_list(request):
    results, filters, ordering = _filtered_results(request)
    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(results, page_size).get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    return render(request, 'regulatory/tariff_compliance_list.html', {
        'results': page_obj, 'page_obj': page_obj,
        'kpis': TariffComplianceService.summary(TariffComplianceResult.objects.all()),
        'operators': _operator_options(), 'service_types': Tariff.ServiceType.choices,
        'traffic_types': Tariff.TrafficType.choices, 'filters': filters, 'sort': ordering,
        'page_size': page_size, 'query_string': query_params.urlencode(),
    })


@login_required
@regulatory_admin_required
def tariff_compliance_run(request):
    if request.method != 'POST':
        return redirect('regulatory:tariff_compliance_list')
    effective_date = parse_date(request.POST.get('effective_date', '')) or date.today()
    summary = TariffComplianceService.run_check(
        operator_code=request.POST.get('operator', ''), service_type=request.POST.get('service', ''),
        effective_date=effective_date, scope=request.POST.get('scope', 'ALL_ACTIVE'), user=request.user,
    )
    messages.success(request, 'Compliance check completed: {total} checked, {compliant} compliant, {non_compliant} non-compliant, {under_review} under review.'.format(**summary))
    return redirect('regulatory:tariff_compliance_list')


@login_required
@regulator_required
def tariff_compliance_export(request):
    results, _, _ = _filtered_results(request)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="tariff-compliance.csv"'
    writer = csv.writer(response)
    writer.writerow(['Operator', 'Service', 'Traffic Type', 'Tariff Name', 'Applied Rate', 'Approved Rate', 'Variance', 'Variance %', 'Status', 'Last Checked'])
    for result in results:
        writer.writerow([result.operator_code, result.service_type, result.traffic_type, result.tariff_name,
                         result.applied_rate, result.approved_rate or '', result.variance or '',
                         result.variance_percent or '', result.get_status_display(), result.last_checked])
    return response


@login_required
@regulator_required
def tariff_compliance_detail(request, pk):
    result = get_object_or_404(TariffComplianceResult.objects.select_related('applied_tariff', 'approved_tariff', 'finding_case'), pk=pk)
    return render(request, 'regulatory/tariff_compliance_detail.html', {'result': result, 'compare': False})


@login_required
@regulator_required
def tariff_compliance_compare(request, pk):
    result = get_object_or_404(TariffComplianceResult.objects.select_related('applied_tariff', 'approved_tariff'), pk=pk)
    return render(request, 'regulatory/tariff_compliance_detail.html', {'result': result, 'compare': True})


@login_required
@regulatory_admin_required
def tariff_compliance_create_finding(request, pk):
    result = get_object_or_404(TariffComplianceResult.objects.select_related('applied_tariff'), pk=pk)
    if result.status != TariffComplianceResult.Status.NON_COMPLIANT:
        messages.error(request, 'Only non-compliant results can create a regulatory finding.')
        return redirect('regulatory:tariff_compliance_detail', pk=result.pk)
    if result.finding_created:
        messages.info(request, 'A finding already exists for this compliance result.')
        return redirect('regulatory:audit_case_detail', pk=result.finding_case_id)
    if request.method == 'POST':
        assigned_to = None
        assigned_to_id = request.POST.get('assigned_to')
        if assigned_to_id:
            assigned_to = get_object_or_404(get_user_model(), pk=assigned_to_id)
        exposure = abs(result.variance or 0)
        severity = request.POST.get('severity', 'MEDIUM')
        description = request.POST.get('description', '').strip()
        service = AuditCaseService()
        case = service.create_case({
            'case_type': AuditCase.CaseType.TARIFF_VIOLATION, 'operator_code': result.operator_code,
            'title': f'Tariff non-compliance: {result.tariff_name}', 'description': description,
            'finding_summary': description or f'{result.tariff_name} variance: {result.variance_percent}%',
            'risk_level': severity, 'potential_exposure': exposure,
            'tariff_compliance_result': result, 'assigned_to': assigned_to,
        }, request.user)
        service.add_finding(
            case, request.user, finding_type=AuditFinding.FindingType.TARIFF_VIOLATION,
            title=f'{result.tariff_name} tariff variance', severity=severity,
            description=description or f'{result.tariff_name} variance: {result.variance_percent}%.',
            financial_exposure=exposure,
        )
        service.add_evidence(
            case, request.user, evidence_type=AuditEvidence.EvidenceType.TARIFF_COMPLIANCE,
            description='Tariff compliance comparison result.', source_type='TARIFF_COMPLIANCE',
            reference_id=str(result.pk), reference_url=reverse('regulatory:tariff_compliance_detail', args=[result.pk]),
        )
        result.finding_created, result.finding_case = True, case
        result.save(update_fields=['finding_created', 'finding_case'])
        messages.success(request, f'Audit case {case.case_number} and tariff finding created.')
        return redirect('regulatory:audit_case_detail', pk=case.pk)
    return render(request, 'regulatory/tariff_compliance_finding.html', {
        'result': result, 'auditors': get_user_model().objects.filter(is_active=True).order_by('username'),
    })
