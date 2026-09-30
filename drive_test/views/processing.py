from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from drive_test.access import dt_view_required
from drive_test.models import DriveTestFile
from drive_test.models.enums import FileStatus


@login_required
@dt_view_required
def processing_monitor(request):
    files = (
        DriveTestFile.objects.select_related('campaign', 'job')
        .order_by('-uploaded_at')[:200]
    )
    active = DriveTestFile.objects.filter(
        status__in=[FileStatus.QUEUED, FileStatus.PROCESSING],
    ).count()
    return render(request, 'drive_test/processing.html', {
        'files': files,
        'active': active,
    })


@login_required
@dt_view_required
def file_status(request, pk):
    """JSON status for one file, for lightweight polling of active ingests."""
    dtf = get_object_or_404(DriveTestFile.objects.select_related('job'), pk=pk)
    return JsonResponse({
        'id': dtf.pk,
        'status': dtf.status,
        'status_display': dtf.get_status_display(),
        'sample_count': dtf.sample_count,
        'quality_score': dtf.quality_score,
        'progress': getattr(dtf.job, 'progress_pct', None) if dtf.job_id else None,
        'error': dtf.error_message,
    })
