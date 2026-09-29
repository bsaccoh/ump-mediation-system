from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.forms import CampaignForm
from drive_test.models import Campaign, Project
from drive_test.services.audit import log_action


@login_required
@dt_view_required
def campaign_list(request):
    campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(file_count=Count('files'), sample_count=Count('samples'))
        .order_by('-created_at')
    )
    return render(request, 'drive_test/campaign_list.html', {'campaigns': campaigns})


@login_required
@dt_view_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(
        Campaign.objects.select_related('project', 'operator'), pk=pk,
    )
    files = campaign.files.order_by('-uploaded_at')
    return render(request, 'drive_test/campaign_detail.html', {
        'campaign': campaign,
        'files': files,
        'sample_count': campaign.samples.count(),
        'route_count': campaign.routes.count(),
        'event_count': campaign.events.count(),
    })


@login_required
@dt_manage_required
def campaign_create(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    form = CampaignForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        campaign = form.save(commit=False)
        campaign.project = project
        campaign.created_by = request.user
        campaign.save()
        log_action(request.user, 'CREATE', 'drive_test.Campaign', campaign.pk,
                   f'Created campaign "{campaign.name}"', request=request)
        messages.success(request, f'Campaign "{campaign.name}" created.')
        return redirect('drive_test:campaign_detail', pk=campaign.pk)
    return render(request, 'drive_test/campaign_form.html', {
        'form': form, 'mode': 'create', 'project': project,
    })


@login_required
@dt_manage_required
def campaign_edit(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    form = CampaignForm(request.POST or None, instance=campaign)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_action(request.user, 'UPDATE', 'drive_test.Campaign', campaign.pk,
                   f'Updated campaign "{campaign.name}"', request=request)
        messages.success(request, 'Campaign updated.')
        return redirect('drive_test:campaign_detail', pk=campaign.pk)
    return render(request, 'drive_test/campaign_form.html', {
        'form': form, 'mode': 'edit', 'campaign': campaign, 'project': campaign.project,
    })
