from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.dateparse import parse_date

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models import TaxRate, TaxType
from regulatory.services.tax_rate_service import TaxRateService, TaxRateValidationError


def _safe_return_url(request):
    candidate = request.POST.get('next') or request.GET.get('next')
    if candidate and url_has_allowed_host_and_scheme(candidate, {request.get_host()}):
        return candidate
    return reverse('regulatory:tax_rate_list')


@login_required
@regulator_required
def tax_rate_list(request):
    filters = {
        'category': request.GET.get('category', ''), 'status': request.GET.get('status', ''),
        'date_from': request.GET.get('date_from', ''), 'date_to': request.GET.get('date_to', ''),
        'search': request.GET.get('search', '').strip(),
    }
    ordering = request.GET.get('sort', 'name')
    rows = TaxRateService.get_rate_rows(**filters, ordering=ordering)
    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(rows, page_size).get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    return render(request, 'regulatory/tax_rate_list.html', {
        'rows': page_obj, 'page_obj': page_obj, 'kpis': TaxRateService.summary(),
        'categories': TaxType.Category.choices, 'status_choices': TaxRate.Status.choices,
        'filters': filters, 'sort': ordering, 'page_size': page_size, 'query_string': query_params.urlencode(),
    })


@login_required
@regulatory_admin_required
def tax_type_create(request):
    form_data = {
        'name': request.POST.get('name', ''), 'code': request.POST.get('code', ''),
        'category': request.POST.get('category', ''), 'description': request.POST.get('description', ''),
        'rate_percent': request.POST.get('rate_percent', ''), 'effective_from': request.POST.get('effective_from', ''),
        'effective_to': request.POST.get('effective_to', ''), 'status': request.POST.get('status', TaxRate.Status.DRAFT),
    }
    if request.method == 'POST':
        effective_from = parse_date(form_data['effective_from'])
        effective_to = parse_date(form_data['effective_to']) if form_data['effective_to'] else None
        if form_data['effective_to'] and not effective_to:
            return render(request, 'regulatory/tax_type_form.html', {
                'form_data': form_data, 'field_errors': {'effective_to': 'Enter a valid effective to date.'},
                'categories': TaxType.Category.choices,
                'status_choices': [(TaxRate.Status.DRAFT, 'Draft'), (TaxRate.Status.SCHEDULED, 'Scheduled'), (TaxRate.Status.ACTIVE, 'Active')],
                'cancel_url': _safe_return_url(request),
            })
        try:
            tax_type, rate = TaxRateService.create_tax_type_with_initial_rate(
                name=form_data['name'], code=form_data['code'], category=form_data['category'],
                description=form_data['description'], rate_percent=form_data['rate_percent'],
                effective_from=effective_from, effective_to=effective_to,
                status=form_data['status'], user=request.user,
            )
        except TaxRateValidationError as exc:
            return render(request, 'regulatory/tax_type_form.html', {
                'form_data': form_data, 'field_errors': exc.field_errors,
                'categories': TaxType.Category.choices, 'status_choices': [
                    (TaxRate.Status.DRAFT, 'Draft'), (TaxRate.Status.SCHEDULED, 'Scheduled'), (TaxRate.Status.ACTIVE, 'Active'),
                ], 'cancel_url': _safe_return_url(request),
            })
        messages.success(request, f'Tax type and initial rate version v{rate.version} created successfully.')
        return redirect(_safe_return_url(request))
    return render(request, 'regulatory/tax_type_form.html', {
        'categories': TaxType.Category.choices,
        'status_choices': [(TaxRate.Status.DRAFT, 'Draft'), (TaxRate.Status.SCHEDULED, 'Scheduled'), (TaxRate.Status.ACTIVE, 'Active')],
        'cancel_url': _safe_return_url(request),
    })


@login_required
@regulator_required
def tax_type_detail(request, pk):
    tax_type = get_object_or_404(TaxType.objects.prefetch_related('rates__created_by'), pk=pk)
    history = [{'rate': rate, 'display_status': TaxRateService.display_status(rate)} for rate in tax_type.rates.all()]
    return render(request, 'regulatory/tax_type_detail.html', {
        'tax_type': tax_type, 'history': history,
        'current_rate': TaxRateService.find_effective_rate(tax_type, date.today()),
    })


@login_required
@regulatory_admin_required
def tax_type_edit(request, pk):
    tax_type = get_object_or_404(TaxType, pk=pk)
    if request.method == 'POST':
        try:
            TaxRateService.update_tax_type(
                tax_type, name=request.POST.get('name', '').strip(),
                category=request.POST.get('category', TaxType.Category.OTHER),
                description=request.POST.get('description', '').strip(), user=request.user,
            )
        except ValidationError as exc:
            return render(request, 'regulatory/tax_type_form.html', {'tax_type': tax_type, 'form_errors': exc.messages, 'categories': TaxType.Category.choices})
        messages.success(request, f'Tax type "{tax_type.name}" updated.')
        return redirect('regulatory:tax_type_detail', pk=tax_type.pk)
    return render(request, 'regulatory/tax_type_form.html', {'tax_type': tax_type, 'categories': TaxType.Category.choices})


@login_required
@regulatory_admin_required
def tax_rate_create(request, tax_type_id):
    tax_type = get_object_or_404(TaxType, pk=tax_type_id)
    if request.method == 'POST':
        effective_from = parse_date(request.POST.get('effective_from', ''))
        effective_to = parse_date(request.POST.get('effective_to', '')) if request.POST.get('effective_to') else None
        if not effective_from:
            messages.error(request, 'Effective from date is required.')
        elif effective_to and effective_to < effective_from:
            messages.error(request, 'Effective to date cannot be before effective from date.')
        else:
            rate = TaxRateService.add_rate_version(
                tax_type=tax_type, rate_percent=request.POST.get('rate_percent', 0), effective_from=effective_from,
                effective_to=effective_to, notes=request.POST.get('notes', '').strip(),
                status=request.POST.get('status', TaxRate.Status.ACTIVE), user=request.user,
            )
            messages.success(request, f'Created {tax_type.code} version v{rate.version}.')
            return redirect('regulatory:tax_type_detail', pk=tax_type.pk)
    return render(request, 'regulatory/tax_rate_form.html', {'tax_type': tax_type, 'status_choices': TaxRate.Status.choices})


@login_required
@regulatory_admin_required
def tax_type_deactivate(request, pk):
    tax_type = get_object_or_404(TaxType, pk=pk)
    if request.method == 'POST':
        TaxRateService.deactivate_tax_type(tax_type, request.user)
        messages.success(request, f'Tax type "{tax_type.name}" has been deactivated. Historical rate versions were preserved.')
    return redirect('regulatory:tax_type_detail', pk=tax_type.pk)
