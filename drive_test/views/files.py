import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.models import Campaign, DriveTestFile
from drive_test.models.enums import FileStatus
from drive_test.services.audit import log_action
from drive_test.services.files import ALLOWED_EXTENSIONS, save_upload
from drive_test.services.profiling import profile_file

logger = logging.getLogger('drive_test')


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
            dtf, was_created = save_upload(campaign, f, request.user)
            if was_created:
                created += 1
                # Profile immediately so the file table shows detected format,
                # technology, GPS availability and an approximate sample count.
                try:
                    profile_file(dtf)
                except Exception:
                    logger.exception('Profiling failed for uploaded file %s', dtf.pk)
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
def file_process(request, pk):
    """Enqueue parsing/normalization for one file (JobRecord-tracked)."""
    dtf = get_object_or_404(DriveTestFile, pk=pk)
    if request.method != 'POST':
        return redirect('drive_test:campaign_detail', pk=dtf.campaign_id)
    if dtf.status in (FileStatus.QUEUED, FileStatus.PROCESSING):
        messages.info(request, 'That file is already being processed.')
        return redirect('drive_test:campaign_detail', pk=dtf.campaign_id)

    from core.tasks import enqueue_job
    from drive_test.tasks import process_drive_test_file

    DriveTestFile.objects.filter(pk=dtf.pk).update(status=FileStatus.QUEUED)
    job = enqueue_job(
        task=process_drive_test_file,
        job_type='drive_test.process_file',
        label=f'Process drive-test file {dtf.original_name}',
        user=request.user,
        params={'file_id': dtf.pk, 'campaign_id': dtf.campaign_id},
        args=(dtf.pk,),
    )
    # Link the job without clobbering a status that synchronous execution may
    # already have advanced to COMPLETED.
    DriveTestFile.objects.filter(pk=dtf.pk).update(job=job)
    log_action(request.user, 'PROCESS', 'drive_test.DriveTestFile', dtf.pk,
               f'Queued processing of "{dtf.original_name}"', request=request)
    messages.success(request, f'Processing started for "{dtf.original_name}".')
    return redirect('drive_test:campaign_detail', pk=dtf.campaign_id)


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
