"""JobRecord + tracked_task + enqueue_job lifecycle tests.

Runs under ``config.test_settings`` which sets
``CELERY_TASK_ALWAYS_EAGER = True`` — tasks execute synchronously in the
test process, so we can assert on the resulting JobRecord state without
needing a Redis broker.
"""
from django.test import TestCase

from core.models import JobRecord
from core.tasks import tracked_task, enqueue_job


@tracked_task('test.echo_success')
def _echo_success(payload: dict):
    return {
        'result_entity_type': 'TestEntity',
        'result_entity_id': payload.get('id', 0),
        'result_url': '/test/entity/',
        'message': 'OK',
        'echoed': payload,
    }


@tracked_task('test.always_fails')
def _always_fails():
    raise RuntimeError('intentional failure')


class TrackedTaskLifecycleTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}

    def test_success_lifecycle(self):
        job = enqueue_job(
            task=_echo_success,
            job_type='test.echo_success',
            label='echo success test',
            params={'id': 42},
            args=({'id': 42, 'name': 'alpha'},),
        )
        job.refresh_from_db()
        self.assertEqual(job.status, JobRecord.Status.SUCCESS)
        self.assertEqual(job.progress_pct, 100)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.result_entity_type, 'TestEntity')
        self.assertEqual(job.result_entity_id, '42')
        self.assertEqual(job.result_url, '/test/entity/')
        self.assertEqual(job.result.get('message'), 'OK')
        self.assertEqual(job.result.get('echoed'), {'id': 42, 'name': 'alpha'})

    def test_failure_lifecycle(self):
        with self.assertRaises(RuntimeError):
            enqueue_job(
                task=_always_fails,
                job_type='test.always_fails',
                label='failure test',
            )
        job = JobRecord.objects.latest('submitted_at')
        self.assertEqual(job.status, JobRecord.Status.FAILURE)
        self.assertIn('intentional failure', job.error_message)
        self.assertIsNotNone(job.finished_at)

    def test_label_truncation(self):
        job = enqueue_job(
            task=_echo_success,
            job_type='test.echo_success',
            label='x' * 500,  # exceeds 200-char field
            args=({},),
        )
        self.assertLessEqual(len(job.label), 200)

    def test_params_preserved(self):
        job = enqueue_job(
            task=_echo_success,
            job_type='test.echo_success',
            label='params test',
            params={'period': '2026-04', 'partner': 'AFRIC'},
            args=({'id': 1},),
        )
        self.assertEqual(job.params, {'period': '2026-04', 'partner': 'AFRIC'})


class JobApiTests(TestCase):
    databases = {"default", "interconnect", "regulatory", "roaming"}

    def setUp(self):
        from django.contrib.auth import get_user_model
        U = get_user_model()
        self.user = U.objects.create_user(username='joboperator', password='x', is_staff=True)
        from django.test import Client
        self.client = Client()
        self.client.login(username='joboperator', password='x')

    def test_job_list_page_renders(self):
        r = self.client.get('/jobs/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Background Jobs', r.content)

    def test_empty_job_api(self):
        r = self.client.get('/jobs/api/')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['total'], 0)
        self.assertEqual(d['records'], [])

    def test_job_api_includes_submitted_record(self):
        enqueue_job(
            task=_echo_success,
            job_type='test.echo_success',
            label='visible in list',
            user=self.user,
            args=({'id': 7},),
        )
        r = self.client.get('/jobs/api/')
        d = r.json()
        self.assertEqual(d['total'], 1)
        self.assertEqual(d['records'][0]['status'], 'SUCCESS')
        self.assertEqual(d['records'][0]['submitted_by'], 'joboperator')

    def test_job_status_polling_endpoint(self):
        enqueue_job(
            task=_echo_success,
            job_type='test.echo_success',
            label='for polling',
            args=({'id': 9},),
        )
        job = JobRecord.objects.latest('submitted_at')
        r = self.client.get(f'/jobs/{job.pk}/status/')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['id'], job.pk)
        self.assertTrue(d['is_terminal'])
        self.assertEqual(d['status'], 'SUCCESS')
