"""
Reconciliation Views for NRA

Run and view reconciliation results between mediated and declared data.
"""
from datetime import datetime, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from core.decorators import regulator_required, regulatory_admin_required

from regulatory.models.reconciliation import ReconciliationRun, ReconciliationResult, Discrepancy
from regulatory.models.declarations import OperatorDeclaration
from regulatory.services.reconciliation import ReconciliationService
from reference.models import Operator


@login_required
@regulator_required
def reconciliation_list(request):
    """List all reconciliation runs with status/summary."""
    qs = ReconciliationRun.objects.select_related('declaration').all()

    # Filters
    operator = request.GET.get('operator', '')
    status = request.GET.get('status', '')
    level = request.GET.get('level', '')
    risk = request.GET.get('risk', '')
    search = request.GET.get('search', '')

    if operator:
        qs = qs.filter(operator_code=operator)
    if status:
        qs = qs.filter(status=status)
    if level:
        qs = qs.filter(level=level)
    if risk:
        qs = qs.filter(risk_level=risk)
    if search:
        qs = qs.filter(Q(reference__icontains=search) | Q(operator_code__icontains=search) | Q(declaration__reference__icontains=search) | Q(status__icontains=search))
    qs = qs.order_by('-created_at')
    try: page_size = int(request.GET.get('page_size', 15))
    except ValueError: page_size = 15
    page_size = page_size if page_size in {15,25,50,100} else 15
    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page'))
    params = request.GET.copy(); params.pop('page', None)
    summary = {key: ReconciliationRun.objects.filter(status=value).count() for key,value in {'completed':'COMPLETED','running':'RUNNING','failed':'FAILED','pending':'PENDING'}.items()}
    summary['total'] = ReconciliationRun.objects.count()

    operators = Operator.objects.filter(enabled=True)

    context = {
        'runs': page_obj, 'page_obj': page_obj, 'page_size': page_size, 'summary': summary,
        'operators': operators,
        'filter_operator': operator,
        'filter_status': status,
        'filter_level': level,
        'filter_risk': risk, 'search': search, 'query_string': params.urlencode(),
        'status_choices': ReconciliationRun.Status.choices, 'level_choices': ReconciliationRun.Level.choices,
    }
    return render(request, 'regulatory/nra/reconciliation/list.html', context)


@login_required
@regulatory_admin_required
def reconciliation_create(request):
    """Trigger a new reconciliation run."""
    if request.method == 'POST':
        operator_code = request.POST.get('operator_code')
        level = request.POST.get('level', 'MONTHLY')
        period_start = request.POST.get('period_start')
        period_end = request.POST.get('period_end')
        declaration_id = request.POST.get('declaration')

        declaration = None
        if declaration_id:
            declaration = get_object_or_404(OperatorDeclaration, pk=declaration_id)

        service = ReconciliationService()
        try:
            run = service.run_reconciliation(
                operator_code=operator_code,
                level=level,
                period_start=datetime.strptime(period_start, '%Y-%m-%d'),
                period_end=datetime.strptime(period_end, '%Y-%m-%d'),
                declaration=declaration,
            )
            messages.success(request, f'Reconciliation {run.id} completed')
            return redirect('regulatory:reconciliation_detail', pk=run.pk)
        except Exception as e:
            messages.error(request, f'Reconciliation failed: {e}')
            return redirect('regulatory:reconciliation_list')

    operators = Operator.objects.filter(enabled=True)
    declarations = OperatorDeclaration.objects.filter(
        status__in=[OperatorDeclaration.Status.ACCEPTED, OperatorDeclaration.Status.RECONCILED]
    )
    selected_declaration = None
    declaration_id = request.GET.get('declaration')
    if declaration_id:
        selected_declaration = get_object_or_404(declarations, pk=declaration_id)

    context = {
        'operators': operators,
        'declarations': declarations,
        'selected_declaration': selected_declaration,
    }
    return render(request, 'regulatory/nra/reconciliation/form.html', context)


@login_required
@regulator_required
def reconciliation_export(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="reconciliation_runs.csv"'
    import csv
    writer = csv.writer(response); writer.writerow(['Run ID','Operator','Level','Period','Expected Revenue','Declared Revenue','Revenue Variance','Expected GST','Declared GST','GST Variance','Status','Risk'])
    for run in ReconciliationRun.objects.select_related('declaration').order_by('-created_at'):
        writer.writerow([run.reference,run.operator_code,run.level,f'{run.period_start} to {run.period_end}',run.expected_revenue,run.declared_revenue,run.revenue_variance,run.expected_gst,run.declared_gst,run.gst_variance,run.status,run.risk_level])
    return response


@login_required
@regulator_required
def reconciliation_detail(request, pk):
    """View reconciliation run results with per-service breakdown."""
    run = get_object_or_404(ReconciliationRun, pk=pk)
    results = run.results.select_related('run').all()

    context = {
        'run': run,
        'results': results,
    }
    return render(request, 'regulatory/nra/reconciliation/detail.html', context)


@login_required
@regulator_required
def discrepancy_detail(request, pk):
    """View and update discrepancy resolution."""
    discrepancy = get_object_or_404(Discrepancy, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'resolve':
            discrepancy.resolution_status = Discrepancy.ResolutionStatus.RESOLVED
            discrepancy.resolved_at = datetime.now()
            discrepancy.save()
            messages.success(request, 'Discrepancy marked as resolved')
        elif action == 'accept':
            discrepancy.resolution_status = Discrepancy.ResolutionStatus.ACCEPTED
            discrepancy.save()
            messages.info(request, 'Discrepancy accepted')
        return redirect('regulatory:reconciliation_detail', pk=discrepancy.result.run.pk)

    context = {
        'discrepancy': discrepancy,
    }
    return render(request, 'regulatory/nra/reconciliation/discrepancy_detail.html', context)
