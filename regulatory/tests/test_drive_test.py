import io
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from regulatory.models import DriveTestCampaign, DriveTestSample, DriveTestAnalysis
from regulatory.engines.drive_test import (
    parse_drive_test_file, analyse_campaign, compute_percentiles, compute_file_sha256
)
from regulatory.engines.drive_test_reports import (
    generate_drive_test_pdf, generate_drive_test_excel, generate_drive_test_csv
)

User = get_user_model()


class DriveTestEngineTests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.campaign = DriveTestCampaign.objects.create(
            name='Test Freetown Drive Survey',
            test_date='2026-07-25',
            operator_code='orange',
            operator_name='Orange SL',
            region='WESTERN_AREA',
            technology='4G',
            tool_used='TEMS',
        )

    def test_compute_percentiles(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pcts = compute_percentiles(vals)
        self.assertEqual(pcts['p50'], 50.0)
        self.assertGreater(pcts['p95'], pcts['p5'])

    def test_csv_ingestion_and_sha256_duplication(self):
        csv_data = (
            "latitude,longitude,rsrp,sinr,dl_throughput,cssr_status,drop_status,pci\n"
            "8.484,-13.230,-82.5,15.2,12.5,1,0,101\n"
            "8.485,-13.231,-95.0,8.5,7.2,1,0,102\n"
            "8.486,-13.232,-112.0,-4.5,0.8,0,1,103\n"
        ).encode('utf-8')

        file_obj = io.BytesIO(csv_data)
        count = parse_drive_test_file(file_obj, 'survey_sample.csv', self.campaign)
        self.assertEqual(count, 3)
        self.assertEqual(DriveTestSample.objects.filter(campaign=self.campaign).count(), 3)

        # Verify duplicate detection
        campaign2 = DriveTestCampaign.objects.create(
            name='Duplicate Campaign', test_date='2026-07-25', operator_code='orange'
        )
        file_obj2 = io.BytesIO(csv_data)
        with self.assertRaises(ValueError) as ctx:
            parse_drive_test_file(file_obj2, 'survey_sample.csv', campaign2)
        self.assertIn("DUPLICATE_FILE", str(ctx.exception))

    def test_campaign_analysis(self):
        DriveTestSample.objects.create(
            campaign=self.campaign, latitude=Decimal('8.484'), longitude=Decimal('-13.230'),
            rsrp=Decimal('-85.0'), sinr=Decimal('12.0'), dl_throughput=Decimal('15.0'),
            cssr_status=True, drop_status=False
        )
        analysis = analyse_campaign(self.campaign, user=self.user)
        self.assertEqual(analysis.total_samples, 1)
        self.assertTrue(analysis.natca_compliant)
        self.assertIn('p50', analysis.rsrp_percentiles)

    def test_reports_generation(self):
        DriveTestSample.objects.create(
            campaign=self.campaign, latitude=Decimal('8.484'), longitude=Decimal('-13.230'),
            rsrp=Decimal('-85.0'), sinr=Decimal('12.0'), dl_throughput=Decimal('15.0')
        )
        analysis = analyse_campaign(self.campaign, user=self.user)

        pdf_bytes = generate_drive_test_pdf(self.campaign, analysis)
        self.assertTrue(len(pdf_bytes) > 0)

        excel_bytes = generate_drive_test_excel(self.campaign, analysis)
        self.assertTrue(len(excel_bytes) > 0)

        csv_str = generate_drive_test_csv(self.campaign)
        self.assertIn("latitude,longitude", csv_str)


class DriveTestAPITests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='apiuser', password='password123')
        self.client.login(username='apiuser', password='password123')

    def test_live_streaming_telemetry_flow(self):
        # 1. Start live recording
        start_res = self.client.post(
            reverse('regulatory:drive_test_live_start'),
            data=json.dumps({'name': 'Live Field Survey A1', 'operator_code': 'africell', 'technology': '4G'}),
            content_type='application/json'
        )
        self.assertEqual(start_res.status_code, 200)
        c_id = start_res.json()['campaign_id']

        # 2. Push telemetry samples
        sample_payload = [
            {'lat': 8.484, 'lon': -13.230, 'rsrp': -88.5, 'sinr': 14.0, 'dl_tp': 18.2},
            {'lat': 8.485, 'lon': -13.231, 'rsrp': -115.0, 'sinr': -5.0, 'dl_tp': 0.5},
        ]
        samples_res = self.client.post(
            reverse('regulatory:drive_test_live_samples', kwargs={'pk': c_id}),
            data=json.dumps(sample_payload),
            content_type='application/json'
        )
        self.assertEqual(samples_res.status_code, 200)
        self.assertEqual(samples_res.json()['count'], 2)

        # 3. End live recording
        end_res = self.client.post(reverse('regulatory:drive_test_live_end', kwargs={'pk': c_id}))
        self.assertEqual(end_res.status_code, 200)
        self.assertEqual(end_res.json()['total_samples'], 2)
