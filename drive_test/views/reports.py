import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from drive_test.access import dt_manage_required, dt_view_required
from drive_test.models import Campaign, Report
from drive_test.models.enums import ReportStatus, ReportType
from drive_test.services.audit import log_action
from drive_test.services.reports import FORMATS, PDF, reports_dir

_CONTENT_TYPE = {
    'pdf': 'application/pdf',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv',
    'geojson': 'application/geo+json',
    'kml': 'application/vnd.google-earth.kml+xml',
}


@login_required
@dt_view_required
def report_list(request):
    reports = Report.objects.select_related('campaign', 'generated_by').order_by('-generated_at')[:200]
    return render(request, 'drive_test/report_list.html', {'reports': reports})


@login_required
@dt_manage_required
def report_generate(request):
    campaigns = Campaign.objects.select_related('project').order_by('-created_at')
    if request.method == 'POST':
        campaign = get_object_or_404(Campaign, pk=request.POST.get('campaign'))
        rtype = request.POST.get('report_type', ReportType.EXECUTIVE)
        fmt = request.POST.get('format', PDF)
        if rtype not in ReportType.values:
            rtype = ReportType.EXECUTIVE
        if fmt not in FORMATS:
            fmt = PDF
        report = Report.objects.create(
            report_type=rtype,
            title=request.POST.get('title', '').strip(),
            campaign=campaign,
            project=campaign.project,
            params={'format': fmt, 'technology': request.POST.get('technology', '').strip(),
                    'metric': request.POST.get('metric', '').strip(),
                    'prepared_by': request.POST.get('prepared_by', '').strip()},
            status=ReportStatus.PENDING,
            generated_by=request.user,
        )
        from drive_test.services.jobs import run_tracked
        from drive_test.tasks import generate_report_task
        run_tracked(task=generate_report_task, job_type='drive_test.generate_report',
                    label=f'Generate {report.get_report_type_display()} ({fmt})',
                    user=request.user, params={'report_id': report.pk}, args=(report.pk,))
        log_action(request.user, 'CREATE', 'drive_test.Report', report.pk,
                   f'Generated {report.get_report_type_display()} ({fmt})', request=request)
        messages.success(request, 'Report generated.')
        return redirect('drive_test:report_detail', ref=report.ref)

    return render(request, 'drive_test/report_generate.html', {
        'campaigns': campaigns,
        'report_types': ReportType.choices,
        'formats': sorted(FORMATS),
    })


@login_required
@dt_view_required
def report_detail(request, ref):
    report = get_object_or_404(Report.objects.select_related('campaign', 'generated_by'), ref=ref)
    return render(request, 'drive_test/report_detail.html', {'report': report})


@login_required
@dt_view_required
def report_download(request, ref):
    report = get_object_or_404(Report, ref=ref)
    if not report.artifact_path:
        raise Http404('Report has no artifact yet.')

    # Path-traversal guard: the artifact must live inside the reports directory.
    base = os.path.realpath(reports_dir())
    real = os.path.realpath(report.artifact_path)
    if not real.startswith(base + os.sep) or not os.path.exists(real):
        raise Http404('Artifact not found.')

    log_action(request.user, 'EXPORT', 'drive_test.Report', report.pk,
               f'Downloaded report {report.ref}', request=request)
    fmt = report.artifact_format or 'pdf'
    filename = f'{report.ref}.{fmt}'
    return FileResponse(open(real, 'rb'),
                        content_type=_CONTENT_TYPE.get(fmt, 'application/octet-stream'),
                        as_attachment=True, filename=filename)
