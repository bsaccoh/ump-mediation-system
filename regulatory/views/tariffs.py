from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.decorators import regulator_required, regulatory_admin_required
from regulatory.models import Tariff, TaxType, TaxRate
from regulatory.services.tariff_management import TariffManagementService, TariffWorkflowError


def _operator_options():
    from django.conf import settings
    from reference.models import Operator

    configured = {code: code.replace('-', ' ').title() for code in getattr(settings, 'OPERATORS', [])}
    configured.update(Operator.objects.filter(enabled=True).values_list('code', 'name'))
    return sorted(configured.items(), key=lambda option: option[1].lower())


def _tariff_form_context(**context):
    return {
        'operators': _operator_options(),
        'service_types': Tariff.ServiceType.choices,
        'traffic_types': Tariff.TrafficType.choices,
        'subscriber_types': Tariff.SubscriberType.choices,
        'charging_units': Tariff.ChargingUnit.choices,
        'rounding_rules': Tariff.RoundingRule.choices,
        'tax_treatments': Tariff.TaxTreatment.choices,
        **context,
    }


@login_required
@regulator_required
def tariff_list(request):
    tariffs = Tariff.objects.all()
    filters = {
        'operator': request.GET.get('operator', ''),
        'service': request.GET.get('service', ''),
        'traffic': request.GET.get('traffic', ''),
        'status': request.GET.get('status', ''),
        'search': request.GET.get('search', '').strip(),
    }
    if filters['operator']:
        tariffs = tariffs.filter(operator_code=filters['operator'])
    if filters['service']:
        tariffs = tariffs.filter(service_type=filters['service'])
    if filters['traffic']:
        tariffs = tariffs.filter(traffic_type=filters['traffic'])
    if filters['status']:
        tariffs = tariffs.filter(status=filters['status'])
    if filters['search']:
        tariffs = tariffs.filter(
            Q(name__icontains=filters['search']) | Q(operator_code__icontains=filters['search']) |
            Q(destination__icontains=filters['search'])
        )

    ordering_options = {
        'name': 'name', '-name': '-name', 'operator': 'operator_code', '-operator': '-operator_code',
        'effective': '-effective_from', '-effective': 'effective_from', 'status': 'status', '-status': '-status',
    }
    ordering = request.GET.get('sort', 'name')
    ordering = ordering if ordering in ordering_options else 'name'
    tariffs = tariffs.order_by(ordering_options[ordering], '-version')
    try:
        page_size = int(request.GET.get('page_size', 15))
    except ValueError:
        page_size = 15
    page_size = page_size if page_size in {15, 25, 50, 100} else 15
    page_obj = Paginator(tariffs, page_size).get_page(request.GET.get('page'))

    all_tariffs = Tariff.objects
    query_params = request.GET.copy()
    query_params.pop('page', None)
    return render(request, 'regulatory/tariff_list.html', {
        'tariffs': page_obj,
        'page_obj': page_obj,
        'operators': _operator_options(),
        'service_types': Tariff.ServiceType.choices,
        'traffic_types': Tariff.TrafficType.choices,
        'status_choices': Tariff.Status.choices,
        'kpis': {
            'total': all_tariffs.count(),
            'submitted': all_tariffs.filter(status=Tariff.Status.SUBMITTED).count(),
            'reviewed': all_tariffs.filter(status=Tariff.Status.REVIEWED).count(),
            'approved': all_tariffs.filter(status__in=[Tariff.Status.APPROVED, Tariff.Status.ACTIVE]).count(),
            'expired': all_tariffs.filter(status=Tariff.Status.EXPIRED).count(),
        },
        'filter_operator': filters['operator'], 'filter_service': filters['service'],
        'filter_traffic': filters['traffic'], 'filter_status': filters['status'],
        'filter_search': filters['search'], 'sort': ordering, 'page_size': page_size,
        'query_string': query_params.urlencode(),
    })


@login_required
@regulatory_admin_required
def tariff_create(request):
    if request.method == 'POST':
        tariff = Tariff(
            name=request.POST.get('name', ''), operator_code=request.POST.get('operator_code', ''),
            service_type=request.POST.get('service_type', ''), traffic_type=request.POST.get('traffic_type', ''),
            subscriber_type=request.POST.get('subscriber_type', Tariff.SubscriberType.ALL),
            destination=request.POST.get('destination', ''), rate=request.POST.get('rate', 0),
            charging_unit=request.POST.get('charging_unit', ''),
            minimum_charge=request.POST.get('minimum_charge', 0) or 0,
            rounding_rule=request.POST.get('rounding_rule', Tariff.RoundingRule.NONE),
            currency=request.POST.get('currency', 'SLE'),
            tax_treatment=request.POST.get('tax_treatment', Tariff.TaxTreatment.TAXABLE),
            effective_from=request.POST.get('effective_from', ''),
            effective_to=request.POST.get('effective_to', '') or None, notes=request.POST.get('notes', ''),
            status=Tariff.Status.DRAFT,
        )
        tariff.version = TariffManagementService.next_version(
            operator_code=tariff.operator_code, service_type=tariff.service_type,
            traffic_type=tariff.traffic_type, subscriber_type=tariff.subscriber_type,
            destination=tariff.destination,
        )
        try:
            tariff.full_clean()
            tariff.save()
        except ValidationError as exc:
            return render(request, 'regulatory/tariff_form.html', _tariff_form_context(
                tariff=tariff, form_title='Create Tariff', form_errors=exc.messages,
            ))
        messages.success(request, f'Tariff "{tariff.name}" created as draft version v{tariff.version}.')
        return redirect('regulatory:tariff_list')
    return render(request, 'regulatory/tariff_form.html', _tariff_form_context(form_title='Create Tariff'))


@login_required
@regulatory_admin_required
def tariff_edit(request, pk):
    tariff = get_object_or_404(Tariff, pk=pk)
    if tariff.status in (Tariff.Status.ACTIVE, Tariff.Status.EXPIRED):
        messages.warning(request, 'Active and expired tariffs are immutable. Create a new version instead.')
        return redirect('regulatory:tariff_list')
    if request.method == 'POST':
        for field in ('name', 'operator_code', 'service_type', 'traffic_type', 'subscriber_type', 'destination',
                      'rate', 'charging_unit', 'minimum_charge', 'rounding_rule', 'currency', 'tax_treatment',
                      'effective_from', 'notes'):
            setattr(tariff, field, request.POST.get(field, getattr(tariff, field)))
        tariff.effective_to = request.POST.get('effective_to', '') or None
        try:
            tariff.full_clean()
            tariff.save()
        except ValidationError as exc:
            return render(request, 'regulatory/tariff_form.html', _tariff_form_context(
                tariff=tariff, form_title=f'Edit Tariff: {tariff.name}', form_errors=exc.messages,
            ))
        messages.success(request, f'Tariff "{tariff.name}" updated.')
        return redirect('regulatory:tariff_list')
    return render(request, 'regulatory/tariff_form.html', _tariff_form_context(
        tariff=tariff, form_title=f'Edit Tariff: {tariff.name}',
    ))


@login_required
@regulator_required
def tariff_detail(request, pk):
    return render(request, 'regulatory/tariff_detail.html', {'tariff': get_object_or_404(Tariff, pk=pk)})


@login_required
@regulatory_admin_required
def tariff_workflow(request, pk, action):
    tariff = get_object_or_404(Tariff, pk=pk)
    if request.method != 'POST':
        messages.error(request, 'Tariff workflow actions must be submitted as a form.')
    else:
        try:
            TariffManagementService.advance(tariff, action, request.user)
        except TariffWorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Tariff "{tariff.name}" moved to {tariff.get_status_display()}.')
    return redirect('regulatory:tariff_list')


@login_required
@regulatory_admin_required
def tariff_deactivate(request, pk):
    tariff = get_object_or_404(Tariff, pk=pk)
    if request.method == 'POST':
        try:
            TariffManagementService.deactivate(tariff)
        except TariffWorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Tariff "{tariff.name}" has been deactivated.')
    return redirect('regulatory:tariff_list')


@login_required
@regulatory_admin_required
def tariff_new_version(request, pk):
    tariff = get_object_or_404(Tariff, pk=pk)
    if request.method == 'POST':
        new_tariff = TariffManagementService.duplicate_as_draft(tariff)
        messages.success(request, f'Created draft version v{new_tariff.version} of "{tariff.name}".')
        return redirect('regulatory:tariff_edit', pk=new_tariff.pk)
    return redirect('regulatory:tariff_list')


@login_required
@regulator_required
def tax_rate_list(request):
    tax_types = TaxType.objects.prefetch_related('rates').all()
    return render(request, 'regulatory/tax_rate_list.html', {'tax_types': tax_types})


@login_required
@regulatory_admin_required
def tax_type_create(request):
    if request.method == 'POST':
        tax_type = TaxType(name=request.POST.get('name', ''), code=request.POST.get('code', ''),
                           description=request.POST.get('description', ''))
        tax_type.save()
        messages.success(request, f'Tax type "{tax_type.name}" created.')
        return redirect('regulatory:tax_rate_list')
    return render(request, 'regulatory/tax_type_form.html')


@login_required
@regulatory_admin_required
def tax_rate_create(request, tax_type_id):
    tax_type = get_object_or_404(TaxType, pk=tax_type_id)
    if request.method == 'POST':
        rate = TaxRate(tax_type=tax_type, rate_percent=request.POST.get('rate_percent', 0),
                       effective_from=request.POST.get('effective_from', ''),
                       effective_to=request.POST.get('effective_to', '') or None,
                       notes=request.POST.get('notes', ''))
        rate.save()
        messages.success(request, f'Tax rate {rate.rate_percent}% added to {tax_type.name}.')
        return redirect('regulatory:tax_rate_list')
    return render(request, 'regulatory/tax_rate_form.html', {'tax_type': tax_type})
