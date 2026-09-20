import csv
import io
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models import OperatorDeclaration
from regulatory.services.declaration_service import DeclarationService, DeclarationValidationError
from reference.models import Operator


def _return_url(request):
    candidate = request.POST.get('next') or request.GET.get('next')
    if candidate and url_has_allowed_host_and_scheme(candidate, {request.get_host()}):
        return candidate
    return 'regulatory:declaration_list'


def _filters(request):
    return {key: request.GET.get(key, '').strip() for key in ('operator', 'status', 'declaration_type', 'period', 'start_date', 'end_date', 'search', 'sort')}


def _apply_filters(queryset, filters):
    if filters['operator']:
        queryset = queryset.filter(operator_code=filters['operator'])
    if filters['status']:
        queryset = queryset.filter(status=filters['status'])
    if filters['declaration_type']:
        queryset = queryset.filter(declaration_type=filters['declaration_type'])
    if filters['search']:
        queryset = queryset.filter(
            Q(operator_code__icontains=filters['search']) | Q(reference__icontains=filters['search']) |
            Q(declaration_type__icontains=filters['search']) | Q(status__icontains=filters['search'])
        )
    start_date, end_date = parse_date(filters['start_date']), parse_date(filters['end_date'])
    if start_date:
        queryset = queryset.filter(period_start__gte=start_date)
    if end_date:
        queryset = queryset.filter(period_end__lte=end_date)
    today = date.today()
    ranges = {
        'current_month': (today.replace(day=1), today),
        'previous_month': ((today.replace(day=1) - timedelta(days=1)).replace(day=1), today.replace(day=1) - timedelta(days=1)),
        'current_year': (today.replace(month=1, day=1), today),
        'previous_year': (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
    }
    if filters['period'] in ranges:
        period_start, period_end = ranges[filters['period']]
        queryset = queryset.filter(period_start__gte=period_start, period_end__lte=period_end)
    elif filters['period'] in {'current_quarter', 'previous_quarter'}:
        current_quarter = (today.month - 1) // 3
        year, quarter = today.year, current_quarter
        if filters['period'] == 'previous_quarter':
            quarter -= 1
            if quarter < 0:
                year, quarter = year - 1, 3
        period_start = date(year, quarter * 3 + 1, 1)
        next_start = date(year + (quarter == 3), 1 if quarter == 3 else quarter * 3 + 4, 1)
        queryset = queryset.filter(period_start__gte=period_start, period_end__lt=next_start)
    return queryset


@login_required
@regulator_required
def declaration_list(request):
    filters = _filters(request)
    queryset = _apply_filters(OperatorDeclaration.objects.select_related('submitted_by', 'reviewed_by').filter(is_current_version=True), filters)
    ordering = {'oldest': 'period_start', 'operator': 'operator_code', 'status': 'status'}.get(filters['sort'], '-period_start')
    queryset = queryset.order_by(ordering, '-pk')
    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(queryset, page_size).get_page(request.GET.get('page'))
    query_params = request.GET.copy(); query_params.pop('page', None)
    return render(request, 'regulatory/nra/declarations/list.html', {
        'declarations': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'kpis': DeclarationService.get_summary(), 'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'filters': filters, 'status_choices': OperatorDeclaration.Status.choices,
        'type_choices': OperatorDeclaration.DeclarationType.choices, 'query_string': query_params.urlencode(),
    })


def _form_data(request, declaration=None):
    source = request.POST if request.method == 'POST' else None
    fields = ['operator_code', 'period_start', 'period_end', 'declaration_type', *DeclarationService.monetary_fields, 'notes']
    values = {}
    for field in fields:
        value = source.get(field, '') if source else getattr(declaration, field, '') if declaration else ''
        values[field] = str(value) if value is not None else ''
    return values


@login_required
@regulatory_admin_required
def declaration_create(request):
    form_data = _form_data(request)
    if request.method == 'POST':
        data = {**form_data, 'period_start': parse_date(form_data['period_start']), 'period_end': parse_date(form_data['period_end'])}
        try:
            declaration = DeclarationService.create_declaration(data, request.user)
            if request.FILES.get('supporting_document'):
                DeclarationService.add_attachment(declaration, request.FILES['supporting_document'], request.user)
        except DeclarationValidationError as exc:
            return render(request, 'regulatory/nra/declarations/form.html', _form_context(form_data, exc.field_errors))
        messages.success(request, f'Declaration {declaration.reference} saved as draft.')
        return redirect('regulatory:declaration_detail', pk=declaration.pk)
    return render(request, 'regulatory/nra/declarations/form.html', _form_context(form_data))


def _form_context(form_data, field_errors=None, declaration=None):
    return {
        'form_data': form_data, 'declaration': declaration, 'field_errors': field_errors or {},
        'operators': Operator.objects.filter(enabled=True).order_by('name'),
        'type_choices': OperatorDeclaration.DeclarationType.choices,
    }


@login_required
@regulator_required
def declaration_detail(request, pk):
    declaration = get_object_or_404(OperatorDeclaration.objects.select_related('submitted_by', 'reviewed_by', 'created_by'), pk=pk)
    return render(request, 'regulatory/nra/declarations/detail.html', {
        'declaration': declaration, 'attachments': declaration.attachments.all(),
        'reviews': declaration.reviews.select_related('reviewer'), 'history': declaration.amendments.all(),
    })


@login_required
@regulatory_admin_required
def declaration_edit(request, pk):
    declaration = get_object_or_404(OperatorDeclaration, pk=pk)
    form_data = _form_data(request, declaration)
    if request.method == 'POST':
        data = {**form_data, 'period_start': parse_date(form_data['period_start']), 'period_end': parse_date(form_data['period_end'])}
        try:
            DeclarationService.update_draft(declaration, data, request.user)
            if request.FILES.get('supporting_document'):
                DeclarationService.add_attachment(declaration, request.FILES['supporting_document'], request.user)
        except DeclarationValidationError as exc:
            return render(request, 'regulatory/nra/declarations/form.html', _form_context(form_data, exc.field_errors, declaration))
        messages.success(request, 'Draft declaration updated.')
        return redirect('regulatory:declaration_detail', pk=pk)
    return render(request, 'regulatory/nra/declarations/form.html', _form_context(form_data, declaration=declaration))


@login_required
@regulatory_admin_required
def declaration_submit(request, pk):
    declaration = get_object_or_404(OperatorDeclaration, pk=pk)
    try:
        DeclarationService.submit_declaration(declaration, request.user)
        messages.success(request, 'Declaration submitted for validation.')
    except DeclarationValidationError as exc:
        messages.error(request, next(iter(exc.field_errors.values())))
    return redirect('regulatory:declaration_detail', pk=pk)


@login_required
@regulator_required
def declaration_review(request, pk):
    declaration = get_object_or_404(OperatorDeclaration, pk=pk)
    action = request.POST.get('action')
    if action in {'accept', 'reject'} and not (request.user.is_superuser or request.user.is_regulatory_admin):
        messages.error(request, 'Only a regulatory supervisor can accept or reject declarations.')
        return redirect('regulatory:declaration_detail', pk=pk)
    try:
        if action == 'validate':
            DeclarationService.validate_declaration(declaration, request.user)
        else:
            DeclarationService.review_declaration(
                declaration, action, request.user, request.POST.get('notes', ''),
                request.POST.get('reason', ''), parse_date(request.POST.get('due_date', '')),
            )
        messages.success(request, 'Declaration workflow updated.')
    except DeclarationValidationError as exc:
        messages.error(request, next(iter(exc.field_errors.values())))
    return redirect('regulatory:declaration_detail', pk=pk)


@login_required
@regulatory_admin_required
def declaration_amend(request, pk):
    original = get_object_or_404(OperatorDeclaration, pk=pk)
    try:
        declaration = DeclarationService.create_declaration({
            **{field: getattr(original, field) for field in DeclarationService.monetary_fields},
            'operator_code': original.operator_code, 'period_start': original.period_start, 'period_end': original.period_end,
            'declaration_type': original.declaration_type, 'notes': original.notes,
        }, request.user, version_of=original)
        messages.success(request, f'Created amendment {declaration.reference} (v{declaration.version}).')
        return redirect('regulatory:declaration_edit', pk=declaration.pk)
    except DeclarationValidationError as exc:
        messages.error(request, next(iter(exc.field_errors.values())))
        return redirect('regulatory:declaration_detail', pk=pk)


@login_required
@regulatory_admin_required
def declaration_import(request):
    if request.method == 'POST' and request.POST.get('confirm'):
        rows = request.session.pop('declaration_import_rows', [])
        created, errors = 0, []
        for index, row in enumerate(rows, 2):
            row = {
                **row,
                'period_start': parse_date(row.get('period_start', '')),
                'period_end': parse_date(row.get('period_end', '')),
                'total_declared_revenue': row.get('declared_revenue', '0'),
                'total_declared_tax': row.get('declared_gst', '0'),
            }
            try:
                DeclarationService.create_declaration(row, request.user); created += 1
            except DeclarationValidationError as exc:
                errors.append(f'Row {index}: {next(iter(exc.field_errors.values()))}')
        messages.success(request, f'Imported {created} declaration(s).')
        for error in errors[:5]: messages.warning(request, error)
        return redirect('regulatory:declaration_list')
    if request.method == 'POST':
        uploaded_file = request.FILES.get('file')
        if not uploaded_file or not uploaded_file.name.lower().endswith('.csv'):
            messages.error(request, 'Upload a CSV file using the provided template.')
            return redirect('regulatory:declaration_list')
        try:
            rows = list(csv.DictReader(io.StringIO(uploaded_file.read().decode('utf-8-sig'))))
        except UnicodeDecodeError:
            messages.error(request, 'The import file must use UTF-8 encoding.')
            return redirect('regulatory:declaration_list')
        required = {'operator_code', 'period_start', 'period_end', 'declaration_type'}
        if not rows or not required.issubset(rows[0]):
            messages.error(request, 'The CSV is missing required template columns.')
            return redirect('regulatory:declaration_list')
        request.session['declaration_import_rows'] = rows
        return render(request, 'regulatory/nra/declarations/import_preview.html', {'rows': rows[:20], 'row_count': len(rows)})
    return redirect('regulatory:declaration_list')


@login_required
@regulator_required
def declaration_import_template(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="operator_declaration_template.csv"'
    writer = csv.writer(response)
    writer.writerow(['operator_code', 'period_start', 'period_end', 'declaration_type', 'declared_revenue', 'declared_taxable_revenue', 'declared_gst', 'international_revenue', 'roaming_revenue', 'interconnect_revenue', 'other_taxable_revenue', 'notes'])
    writer.writerow(['ORANGE', '2026-08-01', '2026-08-31', 'GST_REVENUE', '1040000000.00', '910000000.00', '136500000.00', '0', '0', '0', '0', 'August declaration'])
    return response
