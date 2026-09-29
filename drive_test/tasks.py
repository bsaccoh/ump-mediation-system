"""Celery tasks for drive-test processing.

Uses the platform's ``tracked_task`` so a JobRecord is updated PENDING → RUNNING
→ SUCCESS/FAILURE automatically, and falls back to synchronous execution when
no broker is available (the same behaviour the CDR pipeline relies on).
"""
from core.tasks import tracked_task


@tracked_task('drive_test.process_file')
def process_drive_test_file(file_id: int):
    from drive_test.services.processing import process_file
    return process_file(file_id)
