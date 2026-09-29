from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.models import Campaign, DriveTestFile
from drive_test.services.audit import log_action
from drive_test.services.files import ALLOWED_EXTENSIONS, save_upload


@login_required
@dt_view_required
def file_list(request):
    files = (
        DriveTestFile.objects.select_related('campaign', 'campaign__project', 'uploaded_by')
        .order_by('-uploaded_at')
    )
    return render(request, 'drive_test/file_list.html', {
        'files': files,
        'allowed': sorted(ALLOWED_EXTENSIONS),
    })


@login_required
@dt_manage_required
def file_upload(request, campaign_pk):
    campaign = get_object_or_404(Campaign, pk=campaign_pk)
    if request.method == 'POST':
        uploaded = request.FILES.getlist('files')
        if not uploaded:
            messages.error(request, 'No files selected.')
            return redirect('drive_test:file_upload', campaign_pk=campaign.pk)

        created, duplicate = 0, 0
        for f in uploaded:
            _, was_created = save_upload(campaign, f, request.user)
            if was_created:
                created += 1
            else:
                duplicate += 1
        if created:
            log_action(request.user, 'UPLOAD', 'drive_test.Campaign', campaign.pk,
                       f'Uploaded {created} file(s) to "{campaign.name}"',
                       request=request, files=created, duplicates=duplicate)
            messages.success(request, f'Uploaded {created} file(s).')
        if duplicate:
            messages.warning(
                request,
                f'{duplicate} file(s) skipped as duplicates of content already uploaded.',
            )
        return redirect('drive_test:campaign_detail', pk=campaign.pk)

    return render(request, 'drive_test/file_upload.html', {
        'campaign': campaign,
        'allowed': sorted(ALLOWED_EXTENSIONS),
    })


@login_required
@dt_manage_required
def file_delete(request, pk):
    dtf = get_object_or_404(DriveTestFile, pk=pk)
    campaign_pk = dtf.campaign_id
    if request.method == 'POST':
        name = dtf.original_name
        # Remove the stored blob; the DB row cascades from the campaign only,
        # so delete it explicitly here.
        from pathlib import Path
        if dtf.stored_path:
            Path(dtf.stored_path).unlink(missing_ok=True)
        dtf.delete()
        log_action(request.user, 'DELETE', 'drive_test.DriveTestFile', pk,
                   f'Deleted file "{name}"', request=request)
        messages.success(request, f'Deleted "{name}".')
    return redirect('drive_test:campaign_detail', pk=campaign_pk)
