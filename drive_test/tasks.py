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


@tracked_task('drive_test.generate_report')
def generate_report_task(report_id: int):
    from drive_test.models import Report
    from drive_test.services.reports import generate
    return generate(Report.objects.get(pk=report_id))


@tracked_task('drive_test.detect_events')
def detect_events_and_areas(campaign_id: int):
    """Run the event engine then cluster the results into problem areas."""
    from drive_test.models import Campaign
    from drive_test.services.events import detect_events
    from drive_test.services.problem_areas import cluster_problem_areas

    campaign = Campaign.objects.get(pk=campaign_id)
    events = detect_events(campaign)
    areas = cluster_problem_areas(campaign)
    return {
        'message': f'Detected {events} events, {areas} problem areas',
        'events': events, 'problem_areas': areas,
        'result_entity_type': 'Campaign', 'result_entity_id': str(campaign_id),
        'result_url': f'/drive-test/campaigns/{campaign_id}/events/',
    }
