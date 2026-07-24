"""
Business Logic Views
====================
CRUD interface for managing mediation business rules.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import BusinessRuleForm
from .models import BusinessRule, RuleExecutionLog


# ── List ──────────────────────────────────────────────────────────────────────

@login_required
def rule_list(request):
    rules = BusinessRule.objects.prefetch_related('executions').all()

    # Summary counts
    total    = rules.count()
    active   = rules.filter(status='ACTIVE').count()
    inactive = rules.filter(status='INACTIVE').count()
    draft    = rules.filter(status='DRAFT').count()
    testing  = rules.filter(status='TESTING').count()

    # Filters
    type_filter   = request.GET.get('type', '')
    status_filter = request.GET.get('status', '')
    stream_filter = request.GET.get('stream', '')
    search        = request.GET.get('q', '').strip()

    qs = rules
    if type_filter:
        qs = qs.filter(rule_type=type_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if stream_filter:
        qs = qs.filter(stream=stream_filter)
    if search:
        qs = qs.filter(name__icontains=search)

    return render(request, 'businesslogic/rule_list.html', {
        'rules':          qs,
        'total':          total,
        'active':         active,
        'inactive':       inactive,
        'draft':          draft,
        'testing':        testing,
        'type_filter':    type_filter,
        'status_filter':  status_filter,
        'stream_filter':  stream_filter,
        'search':         search,
        'rule_types':     BusinessRule.RuleType.choices,
        'status_choices': BusinessRule.Status.choices,
        'stream_choices': BusinessRule.Stream.choices,
        'page_title':     'All Mediation Rules'
    })


@login_required
def business_rule_list(request):
    """Filters for standard business rules (Validation, Transformation, Enrichment)."""
    types = [BusinessRule.RuleType.VALIDATION, BusinessRule.RuleType.TRANSFORMATION, BusinessRule.RuleType.ENRICHMENT]
    qs = BusinessRule.objects.filter(rule_type__in=types)
    return render(request, 'businesslogic/rule_list.html', {
        'rules': qs,
        'page_title': 'Business Rules',
        'is_category': True,
        'default_type': BusinessRule.RuleType.VALIDATION
    })


@login_required
def correlation_rule_list(request):
    """Filters for Correlation rules."""
    qs = BusinessRule.objects.filter(rule_type=BusinessRule.RuleType.CORRELATION)
    return render(request, 'businesslogic/rule_list.html', {
        'rules': qs,
        'page_title': 'Correlation Rules',
        'is_category': True,
        'default_type': BusinessRule.RuleType.CORRELATION
    })


@login_required
def detection_rule_list(request):
    """Filters for Detection rules."""
    qs = BusinessRule.objects.filter(rule_type=BusinessRule.RuleType.DETECTION)
    return render(request, 'businesslogic/rule_list.html', {
        'rules': qs,
        'page_title': 'Detection Rules',
        'is_category': True,
        'default_type': BusinessRule.RuleType.DETECTION
    })


@login_required
def error_handling_list(request):
    """Filters for Error Handling rules."""
    qs = BusinessRule.objects.filter(rule_type=BusinessRule.RuleType.ERROR_HANDLING)
    return render(request, 'businesslogic/rule_list.html', {
        'rules': qs,
        'page_title': 'Error Handling',
        'is_category': True,
        'default_type': BusinessRule.RuleType.ERROR_HANDLING
    })


# ── Create ─────────────────────────────────────────────────────────────────────

@login_required
def rule_create(request):
    initial = {}
    if 'type' in request.GET:
        initial['rule_type'] = request.GET.get('type')
    
    form = BusinessRuleForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        rule = form.save(commit=False)
        rule.created_by = request.user
        rule.save()
        messages.success(request, f'Rule "{rule.name}" created successfully.')
        return redirect('businesslogic:rule_list')
    return render(request, 'businesslogic/rule_form.html', {
        'form': form,
        'title': 'New Business Rule',
        'btn_label': 'Create Rule',
    })


# ── Edit ───────────────────────────────────────────────────────────────────────

@login_required
def rule_edit(request, pk):
    rule = get_object_or_404(BusinessRule, pk=pk)
    form = BusinessRuleForm(request.POST or None, instance=rule)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Rule "{rule.name}" updated.')
        return redirect('businesslogic:rule_list')
    return render(request, 'businesslogic/rule_form.html', {
        'form': form,
        'rule': rule,
        'title': f'Edit — {rule.name}',
        'btn_label': 'Save Changes',
    })


# ── Detail ─────────────────────────────────────────────────────────────────────

@login_required
def rule_detail(request, pk):
    rule = get_object_or_404(BusinessRule, pk=pk)
    executions = rule.executions.all()[:20]
    return render(request, 'businesslogic/rule_detail.html', {
        'rule': rule,
        'executions': executions,
    })


# ── Delete ─────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def rule_delete(request, pk):
    rule = get_object_or_404(BusinessRule, pk=pk)
    name = rule.name
    rule.delete()
    messages.success(request, f'Rule "{name}" deleted.')
    return redirect('businesslogic:rule_list')


# ── Toggle Status ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def rule_toggle(request, pk):
    """Quick toggle between ACTIVE and INACTIVE."""
    rule = get_object_or_404(BusinessRule, pk=pk)
    if rule.status == BusinessRule.Status.ACTIVE:
        rule.status = BusinessRule.Status.INACTIVE
    else:
        rule.status = BusinessRule.Status.ACTIVE
    rule.save(update_fields=['status', 'updated_at'])
    return JsonResponse({
        'status': rule.status,
        'label': rule.get_status_display(),
    })
