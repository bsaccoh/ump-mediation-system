from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.forms import ProjectForm
from drive_test.models import Project
from drive_test.services.audit import log_action


@login_required
@dt_view_required
def project_list(request):
    projects = (
        Project.objects.annotate(campaign_count=Count('campaigns'))
        .order_by('-created_at')
    )
    status = request.GET.get('status', '').strip()
    if status:
        projects = projects.filter(status=status)
    return render(request, 'drive_test/project_list.html', {
        'projects': projects,
        'status': status,
    })


@login_required
@dt_view_required
def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    campaigns = (
        project.campaigns.select_related('operator')
        .annotate(file_count=Count('files')).order_by('-created_at')
    )
    return render(request, 'drive_test/project_detail.html', {
        'project': project,
        'campaigns': campaigns,
    })


@login_required
@dt_manage_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        project = form.save(commit=False)
        project.created_by = request.user
        project.save()
        form.save_m2m()
        log_action(request.user, 'CREATE', 'drive_test.Project', project.pk,
                   f'Created project "{project.name}"', request=request)
        messages.success(request, f'Project "{project.name}" created.')
        return redirect('drive_test:project_detail', pk=project.pk)
    return render(request, 'drive_test/project_form.html', {
        'form': form, 'mode': 'create',
    })


@login_required
@dt_manage_required
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_action(request.user, 'UPDATE', 'drive_test.Project', project.pk,
                   f'Updated project "{project.name}"', request=request)
        messages.success(request, 'Project updated.')
        return redirect('drive_test:project_detail', pk=project.pk)
    return render(request, 'drive_test/project_form.html', {
        'form': form, 'mode': 'edit', 'project': project,
    })
