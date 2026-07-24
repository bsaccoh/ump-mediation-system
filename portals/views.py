"""Views for Input Portals, Output Portals, Plugins, and Resources."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    InputPortalForm, OutputPortalForm, PluginForm, ResourceForm,
    OutputSchemaForm, DistributionRuleForm
)
from .models import (
    InputPortal, OutputPortal, Plugin, Resource,
    OutputSchema, DistributionRule
)


# =============================================================================
# Input Portal
# =============================================================================

@login_required
def input_portal_list(request):
    portals = InputPortal.objects.order_by('name')
    return render(request, 'portals/input_portal_list.html', {'portals': portals})


@login_required
def input_portal_create(request):
    if request.method == 'POST':
        form = InputPortalForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Input portal created successfully.')
            return redirect('portals:input_portal_list')
    else:
        form = InputPortalForm()
    return render(request, 'portals/input_portal_form.html', {
        'form': form,
        'title': 'Add Input Portal',
    })


@login_required
def input_portal_edit(request, pk):
    portal = get_object_or_404(InputPortal, pk=pk)
    if request.method == 'POST':
        form = InputPortalForm(request.POST, instance=portal)
        if form.is_valid():
            form.save()
            messages.success(request, 'Input portal updated successfully.')
            return redirect('portals:input_portal_list')
    else:
        form = InputPortalForm(instance=portal)
    return render(request, 'portals/input_portal_form.html', {
        'form': form,
        'title': 'Edit Input Portal',
        'portal': portal,
    })


@login_required
def input_portal_ide(request, pk=None):
    """IDE-like view for configuring Input Portals."""
    portals = InputPortal.objects.order_by('name')
    active_portal = None
    form = None

    if pk:
        active_portal = get_object_or_404(InputPortal, pk=pk)

    if request.method == 'POST':
        # Handle save
        if active_portal:
            form = InputPortalForm(request.POST, instance=active_portal)
        else:
            form = InputPortalForm(request.POST)

        if form.is_valid():
            saved_portal = form.save()
            msg = 'Input portal updated.' if active_portal else 'Input portal created.'
            messages.success(request, msg)
            return redirect('portals:input_portal_ide_edit', pk=saved_portal.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        if active_portal:
            form = InputPortalForm(instance=active_portal)
        else:
            form = InputPortalForm()

    return render(request, 'portals/input_portal_ide.html', {
        'portals': portals,
        'active_portal': active_portal,
        'form': form,
    })


# =============================================================================
# Output Portal & Distribution IDE
# =============================================================================

@login_required
def output_portal_ide(request, pk=None):
    """IDE-like view for configuring Output Portals and Distribution Rules."""
    portals = OutputPortal.objects.order_by('name')
    active_portal = None
    form = None
    
    # For child models
    schemas = OutputSchema.objects.all()
    rules = DistributionRule.objects.all()

    if pk:
        active_portal = get_object_or_404(OutputPortal, pk=pk)

    if request.method == 'POST':
        if active_portal:
            form = OutputPortalForm(request.POST, instance=active_portal)
        else:
            form = OutputPortalForm(request.POST)

        if form.is_valid():
            saved_portal = form.save()
            msg = 'Output portal updated.' if active_portal else 'Output portal created.'
            messages.success(request, msg)
            return redirect('portals:output_portal_ide_edit', pk=saved_portal.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        if active_portal:
            form = OutputPortalForm(instance=active_portal)
        else:
            form = OutputPortalForm()

    return render(request, 'portals/output_portal_ide.html', {
        'portals': portals,
        'active_portal': active_portal,
        'form': form,
        'schemas': schemas,
        'rules': rules,
        'schema_form': OutputSchemaForm(),
        'rule_form': DistributionRuleForm(),
    })


@login_required
def stream_fields_api(request):
    """Return list of model field names for a given stream type."""
    from django.http import JsonResponse
    stream = (request.GET.get('stream') or '').upper()
    model_map = {}
    try:
        from streams.msc.models import MSCRecord; model_map['MSC'] = MSCRecord
    except Exception: pass
    try:
        from streams.ims.models import IMSRecord; model_map['IMS'] = IMSRecord
    except Exception: pass
    try:
        from streams.pgw.models import PGWRecord; model_map['PGW'] = PGWRecord
    except Exception: pass
    try:
        from streams.sgsn.models import SGSNRecord; model_map['SGSN'] = SGSNRecord
    except Exception: pass
    try:
        from streams.sgw.models import SGWRecord; model_map['SGW'] = SGWRecord
    except Exception: pass
    try:
        from streams.cbs.models import CBSRecord; model_map['CBS'] = CBSRecord
    except Exception: pass
    excluded = {'id', 'raw_data', 'created_at', 'updated_at', 'file', 'file_id'}
    if stream in ('', 'ALL'):
        seen, ordered = set(), []
        for m in model_map.values():
            for f in m._meta.concrete_fields:
                if f.name not in excluded and f.name not in seen:
                    seen.add(f.name)
                    ordered.append(f.name)
        return JsonResponse({'fields': ordered, 'stream': 'ALL'})
    model = model_map.get(stream)
    if not model:
        return JsonResponse({'fields': [], 'error': f'unknown stream {stream}'})
    fields = [f.name for f in model._meta.concrete_fields if f.name not in excluded]
    return JsonResponse({'fields': fields, 'stream': stream})


@login_required
def output_schema_create(request):
    if request.method == 'POST':
        form = OutputSchemaForm(request.POST)
        if form.is_valid():
            schema = form.save()
            messages.success(request, f'Schema "{schema.name}" created.')
            return redirect('portals:output_portal_ide')
        return render(request, 'portals/schema_edit.html', {'form': form, 'schema': None})
    return render(request, 'portals/schema_edit.html', {'form': OutputSchemaForm(), 'schema': None})


@login_required
def distribution_rule_create(request):
    if request.method == 'POST':
        form = DistributionRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f'Rule "{rule.name}" created.')
            return redirect('portals:output_portal_ide')
        return render(request, 'portals/rule_edit.html', {'form': form, 'rule': None})
    return redirect('portals:output_portal_ide')


@login_required
def output_schema_edit(request, pk):
    schema = get_object_or_404(OutputSchema, pk=pk)
    if request.method == 'POST':
        form = OutputSchemaForm(request.POST, instance=schema)
        if form.is_valid():
            form.save()
            messages.success(request, f'Schema "{schema.name}" updated.')
        else:
            messages.error(request, f'Error updating schema: {form.errors.as_text()}')
        return redirect(request.META.get('HTTP_REFERER', 'portals:output_portal_ide'))
    return render(request, 'portals/schema_edit.html', {'form': OutputSchemaForm(instance=schema), 'schema': schema})


@login_required
@require_POST
def output_schema_delete(request, pk):
    schema = get_object_or_404(OutputSchema, pk=pk)
    name = schema.name
    schema.delete()
    messages.success(request, f'Schema "{name}" deleted.')
    return redirect(request.META.get('HTTP_REFERER', 'portals:output_portal_ide'))


@login_required
def distribution_rule_edit(request, pk):
    rule = get_object_or_404(DistributionRule, pk=pk)
    if request.method == 'POST':
        form = DistributionRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            messages.success(request, f'Rule "{rule.name}" updated.')
        else:
            messages.error(request, f'Error updating rule: {form.errors.as_text()}')
        return redirect(request.META.get('HTTP_REFERER', 'portals:output_portal_ide'))
    return render(request, 'portals/rule_edit.html', {'form': DistributionRuleForm(instance=rule), 'rule': rule})


@login_required
@require_POST
def distribution_rule_delete(request, pk):
    rule = get_object_or_404(DistributionRule, pk=pk)
    name = rule.name
    rule.delete()
    messages.success(request, f'Rule "{name}" deleted.')
    return redirect(request.META.get('HTTP_REFERER', 'portals:output_portal_ide'))


@login_required
@login_required
@require_POST
def input_portal_toggle(request, pk):
    portal = get_object_or_404(InputPortal, pk=pk)
    portal.is_active = not portal.is_active
    portal.save(update_fields=['is_active'])
    return JsonResponse({'is_active': portal.is_active})


@login_required
def input_portal_delete(request, pk):
    portal = get_object_or_404(InputPortal, pk=pk)
    if request.method == 'POST':
        name = str(portal)
        portal.delete()
        messages.success(request, f'Input portal "{name}" deleted.')
        # If deleted from IDE, redirect to IDE base
        if 'ide' in request.META.get('HTTP_REFERER', ''):
            return redirect('portals:input_portal_ide')
    return redirect('portals:input_portal_list')


# =============================================================================
# Output Portal
# =============================================================================

@login_required
def output_portal_list(request):
    portals = OutputPortal.objects.order_by('name')
    return render(request, 'portals/output_portal_list.html', {'portals': portals})


@login_required
def output_portal_create(request):
    if request.method == 'POST':
        form = OutputPortalForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Output portal created successfully.')
            return redirect('portals:output_portal_list')
    else:
        form = OutputPortalForm()
    return render(request, 'portals/output_portal_form.html', {
        'form': form,
        'title': 'Add Output Portal',
    })


@login_required
def output_portal_edit(request, pk):
    portal = get_object_or_404(OutputPortal, pk=pk)
    if request.method == 'POST':
        form = OutputPortalForm(request.POST, instance=portal)
        if form.is_valid():
            form.save()
            messages.success(request, 'Output portal updated successfully.')
            return redirect('portals:output_portal_list')
    else:
        form = OutputPortalForm(instance=portal)
    return render(request, 'portals/output_portal_form.html', {
        'form': form,
        'title': 'Edit Output Portal',
        'portal': portal,
    })


@login_required
@require_POST
def output_portal_toggle(request, pk):
    portal = get_object_or_404(OutputPortal, pk=pk)
    portal.is_active = not portal.is_active
    portal.save(update_fields=['is_active'])
    return JsonResponse({'is_active': portal.is_active})


@login_required
def output_portal_delete(request, pk):
    portal = get_object_or_404(OutputPortal, pk=pk)
    if request.method == 'POST':
        name = str(portal)
        portal.delete()
        messages.success(request, f'Output portal "{name}" deleted.')
    return redirect('portals:output_portal_list')


# =============================================================================
# Plugin
# =============================================================================

@login_required
def plugin_list(request):
    plugins = Plugin.objects.order_by('name')
    return render(request, 'portals/plugin_list.html', {'plugins': plugins})


@login_required
def plugin_create(request):
    if request.method == 'POST':
        form = PluginForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Plugin created successfully.')
            return redirect('portals:plugin_list')
    else:
        form = PluginForm()
    return render(request, 'portals/plugin_form.html', {
        'form': form,
        'title': 'Add Plugin',
    })


@login_required
def plugin_edit(request, pk):
    plugin = get_object_or_404(Plugin, pk=pk)
    if request.method == 'POST':
        form = PluginForm(request.POST, instance=plugin)
        if form.is_valid():
            form.save()
            messages.success(request, 'Plugin updated successfully.')
            return redirect('portals:plugin_list')
    else:
        form = PluginForm(instance=plugin)
    return render(request, 'portals/plugin_form.html', {
        'form': form,
        'title': 'Edit Plugin',
        'plugin': plugin,
    })


@login_required
def plugin_delete(request, pk):
    plugin = get_object_or_404(Plugin, pk=pk)
    if request.method == 'POST':
        name = str(plugin)
        plugin.delete()
        messages.success(request, f'Plugin "{name}" deleted.')
    return redirect('portals:plugin_list')


# =============================================================================
# Resource
# =============================================================================

@login_required
def resource_list(request):
    resources = Resource.objects.order_by('name')
    total = resources.count()
    online = resources.filter(status='ONLINE').count()
    offline = resources.filter(status='OFFLINE').count()
    degraded = resources.filter(status='DEGRADED').count()
    return render(request, 'portals/resource_list.html', {
        'resources': resources,
        'total': total,
        'online': online,
        'offline': offline,
        'degraded': degraded,
    })


@login_required
def resource_create(request):
    if request.method == 'POST':
        form = ResourceForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Resource created successfully.')
            return redirect('portals:resource_list')
    else:
        form = ResourceForm()
    return render(request, 'portals/resource_form.html', {
        'form': form,
        'title': 'Add Resource',
    })


@login_required
def resource_edit(request, pk):
    resource = get_object_or_404(Resource, pk=pk)
    if request.method == 'POST':
        form = ResourceForm(request.POST, instance=resource)
        if form.is_valid():
            form.save()
            messages.success(request, 'Resource updated successfully.')
            return redirect('portals:resource_list')
    else:
        form = ResourceForm(instance=resource)
    return render(request, 'portals/resource_form.html', {
        'form': form,
        'title': 'Edit Resource',
        'resource': resource,
    })


@login_required
def resource_delete(request, pk):
    resource = get_object_or_404(Resource, pk=pk)
    if request.method == 'POST':
        name = str(resource)
        resource.delete()
        messages.success(request, f'Resource "{name}" deleted.')
    return redirect('portals:resource_list')
