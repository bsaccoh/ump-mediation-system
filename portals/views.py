"""Views for Input Portals, Output Portals, Plugins, and Resources."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import InputPortalForm, OutputPortalForm, PluginForm, ResourceForm
from .models import InputPortal, OutputPortal, Plugin, Resource


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
