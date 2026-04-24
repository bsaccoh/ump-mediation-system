"""Collection views - file upload, listing, and management."""
import os
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404

from .models import DataSource, CDRFile
from .services.file_detector import detect_decoder_type
from .services.deduplication import get_file_hash, check_duplicate


@login_required
def file_list(request):
    """List CDR files with filtering."""
    files = CDRFile.objects.select_related('source', 'uploaded_by')

    # Filters
    status = request.GET.get('status')
    if status:
        files = files.filter(status=status)

    source_id = request.GET.get('source')
    if source_id:
        files = files.filter(source_id=source_id)

    files = files[:100]
    sources = DataSource.objects.filter(enabled=True)

    return render(request, 'collection/file_list.html', {
        'files': files,
        'sources': sources,
        'current_status': status,
        'current_source': source_id,
    })


@login_required
def upload_file(request):
    """Handle CDR file upload."""
    if request.method != 'POST':
        sources = DataSource.objects.filter(enabled=True)
        return render(request, 'collection/upload.html', {'sources': sources})

    uploaded_files = request.FILES.getlist('files')
    if not uploaded_files:
        messages.error(request, 'No files selected.')
        return redirect('collection:upload')

    source_id = request.POST.get('source_id')
    base_decoder_type = request.POST.get('decoder_type', 'AUTO')
    
    success_count = 0
    duplicate_count = 0
    skipped_extensions = 0
    
    # Allowed extensions for safety during folder uploads
    ALLOWED_EXTENSIONS = ('.dat', '.bin', '.cdr', '.csv', '.txt', '.asn', '.ber', '.unl', '.add')

    for uploaded in uploaded_files:
        # Skip unsupported files during folder upload
        if not uploaded.name.lower().endswith(ALLOWED_EXTENSIONS):
            skipped_extensions += 1
            continue

        decoder_type = base_decoder_type
        # Auto-detect decoder
        if decoder_type == 'AUTO':
            decoder_type = detect_decoder_type(uploaded.name)

        # Ensure upload directory exists
        stream_dirs = {'MSC': 'msc', 'PGW': 'pgw', 'SGSN': 'sgsn', 'SGW': 'sgw'}
        subdir = stream_dirs.get(decoder_type, '')
        upload_dir = os.path.join(settings.INCOMING_DIR, subdir)
        os.makedirs(upload_dir, exist_ok=True)

        # Save file with timestamp prefix
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = uploaded.name.replace(' ', '_')
        filename = f'{ts}_{safe_name}'
        file_path = os.path.join(upload_dir, filename)

        with open(file_path, 'wb+') as dest:
            for chunk in uploaded.chunks():
                dest.write(chunk)

        file_size = os.path.getsize(file_path)
        file_hash = get_file_hash(file_path)

        # Check for duplicate
        if check_duplicate(file_path):
            os.remove(file_path)
            duplicate_count += 1
            continue

        # Create CDRFile record (signal will trigger processing)
        CDRFile.objects.create(
            source_id=source_id if source_id else None,
            filename=uploaded.name,
            file_path=file_path,
            file_size=file_size,
            file_hash=file_hash,
            decoder_type=decoder_type,
            uploaded_by=request.user,
            status=CDRFile.Status.PENDING,
        )
        success_count += 1

    # Aggregate feedback
    if success_count > 0:
        msg = f'Successfully uploaded {success_count} file(s) and queued for processing.'
        if duplicate_count > 0:
            msg += f' Skipped {duplicate_count} duplicate(s).'
        if skipped_extensions > 0:
            msg += f' Ignored {skipped_extensions} unsupported file(s).'
        messages.success(request, msg)
    elif duplicate_count > 0:
        messages.warning(request, f'All {duplicate_count} uploaded file(s) were skipped as duplicates.')
    else:
        messages.error(request, 'No valid files were uploaded.')

    return redirect('collection:file_list')


@login_required
def file_detail(request, pk):
    """View CDR file details."""
    cdr_file = get_object_or_404(CDRFile, pk=pk)
    return render(request, 'collection/file_detail.html', {'file': cdr_file})


@login_required
def reprocess_file(request, pk):
    """Re-queue a file for processing."""
    cdr_file = get_object_or_404(CDRFile, pk=pk)
    if cdr_file.status in (CDRFile.Status.FAILED, CDRFile.Status.COMPLETED):
        cdr_file.status = CDRFile.Status.PENDING
        cdr_file.retry_count += 1
        cdr_file.error_message = ''
        cdr_file.save()
        messages.success(request, f'File "{cdr_file.filename}" re-queued for processing.')
    return redirect('collection:file_detail', pk=pk)


@login_required
def poll_sftp_now(request, source_id):
    """Manually trigger SFTP poll for a data source."""
    source = get_object_or_404(DataSource, pk=source_id)
    if source.source_type != DataSource.SourceType.SFTP:
        messages.error(request, f'{source.name} is not an SFTP source.')
        return redirect('collection:file_list')

    try:
        from collection.services.sftp_collector import poll_source
        stats = poll_source(source)
        messages.success(
            request,
            f'SFTP poll complete for "{source.name}": '
            f'{stats["collected"]} new files, {stats["skipped"]} skipped.'
        )
        if stats['errors']:
            messages.warning(request, f'Errors: {"; ".join(stats["errors"][:3])}')
    except Exception as e:
        messages.error(request, f'SFTP poll failed: {e}')

    return redirect('collection:file_list')
