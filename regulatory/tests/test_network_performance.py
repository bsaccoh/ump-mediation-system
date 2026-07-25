"""Unit tests for Network Performance Monitoring (PM KPIs) and Drive Test Management."""
from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from regulatory.models import (
    NetworkKPIDefinition, NetworkKPIEntry, NetworkKPIImportLog,
    DriveTestCampaign, DriveTestSample, DriveTestAnalysis,
)
from regulatory.engines.network_kpi import check_kpi_compliance, import_kpi_file, compute_qos_compliance_score
from regulatory.engines.drive_test import parse_drive_test_file, analyse_campaign

User = get_user_model()


class NetworkPerformanceTests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.kpi_cssr, _ = NetworkKPIDefinition.objects.get_or_create(
            code='CSSR',
            defaults={
                'name': 'Call Setup Success Rate',
                'unit': '%',
                'natca_threshold': Decimal('95.00'),
                'threshold_direction': 'ABOVE',
            }
        )
        self.kpi_cdr, _ = NetworkKPIDefinition.objects.get_or_create(
            code='CDR',
            defaults={
                'name': 'Call Drop Rate',
                'unit': '%',
                'natca_threshold': Decimal('2.00'),
                'threshold_direction': 'BELOW',
            }
        )

    def test_kpi_compliance_checker(self):
        # CSSR >= 95.00%
        self.assertTrue(check_kpi_compliance(self.kpi_cssr, Decimal('98.50')))
        self.assertFalse(check_kpi_compliance(self.kpi_cssr, Decimal('92.00')))

        # CDR <= 2.00%
        self.assertTrue(check_kpi_compliance(self.kpi_cdr, Decimal('1.50')))
        self.assertFalse(check_kpi_compliance(self.kpi_cdr, Decimal('3.20')))

    def test_import_kpi_csv(self):
        csv_data = (
            "kpi_code,period_date,value,region\n"
            "CSSR,2026-07-24,98.20,WESTERN_AREA\n"
            "CDR,2026-07-24,1.10,WESTERN_AREA\n"
        )
        res = import_kpi_file(csv_data, filename="test_pm.csv", user=self.user)
        self.assertTrue(res['success'])
        self.assertEqual(res['record_count'], 2)
        self.assertEqual(res['error_count'], 0)

        entries = NetworkKPIEntry.objects.filter(region='WESTERN_AREA')
        self.assertEqual(entries.count(), 2)

    def test_compute_qos_score(self):
        d = date(2026, 7, 24)
        NetworkKPIEntry.objects.create(kpi=self.kpi_cssr, period_date=d, value=Decimal('98.00'), is_compliant=True)
        NetworkKPIEntry.objects.create(kpi=self.kpi_cdr, period_date=d, value=Decimal('1.00'), is_compliant=True)

        score = compute_qos_compliance_score(d, 'orange', 'NATIONAL')
        self.assertEqual(score, Decimal('100.00'))

    def test_multi_operator_comparison_matrix(self):
        from regulatory.engines.network_kpi import get_operator_comparison_matrix
        d = date(2026, 7, 24)
        NetworkKPIEntry.objects.create(kpi=self.kpi_cssr, period_date=d, operator_code='orange', region='SOUTHERN', district='Bo', value=Decimal('98.00'), is_compliant=True)
        NetworkKPIEntry.objects.create(kpi=self.kpi_cssr, period_date=d, operator_code='africell', region='SOUTHERN', district='Bo', value=Decimal('94.00'), is_compliant=False)

        matrix = get_operator_comparison_matrix(start_date=d, end_date=d, region='SOUTHERN', district='Bo')
        cssr_row = next(m for m in matrix if m['code'] == 'CSSR')
        self.assertEqual(cssr_row['operators']['orange']['value'], '98.00')
        self.assertTrue(cssr_row['operators']['orange']['is_compliant'])
        self.assertEqual(cssr_row['operators']['africell']['value'], '94.00')
        self.assertFalse(cssr_row['operators']['africell']['is_compliant'])
        self.assertEqual(cssr_row['operators']['national_avg']['value'], '96.00')


class DriveTestTests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        self.user = User.objects.create_user(username='drivetester', password='password')
        self.campaign = DriveTestCampaign.objects.create(
            name="Freetown Urban Survey",
            test_date=date(2026, 7, 24),
            region="WESTERN_AREA",
            technology="4G",
            tool_used="TEMS",
            created_by=self.user,
        )

    def test_parse_drive_test_csv(self):
        csv_content = (
            "latitude,longitude,rsrp,rsrq,sinr,dl_throughput,ul_throughput,cssr,drop,mos\n"
            "8.484,-13.230,-82.5, -10.2, 18.5, 15.2, 4.1, 1, 0, 4.2\n"
            "8.485,-13.231,-95.0, -12.0, 12.0, 8.5, 2.0, 1, 0, 3.8\n"
            "8.486,-13.232,-112.0,-16.5, 2.0, 1.2, 0.5, 0, 1, 2.1\n"
        )
        count = parse_drive_test_file(csv_content, "tems_test.csv", self.campaign)
        self.assertEqual(count, 3)
        self.assertEqual(DriveTestSample.objects.filter(campaign=self.campaign).count(), 3)

    def test_analyse_campaign(self):
        csv_content = (
            "latitude,longitude,rsrp,rsrq,sinr,dl_throughput,ul_throughput,cssr,drop,mos\n"
            "8.484,-13.230,-82.5, -10.2, 18.5, 15.2, 4.1, 1, 0, 4.2\n"
            "8.485,-13.231,-95.0, -12.0, 12.0, 8.5, 2.0, 1, 0, 3.8\n"
        )
        parse_drive_test_file(csv_content, "tems_test.csv", self.campaign)
        analysis = analyse_campaign(self.campaign, user=self.user)

        self.assertEqual(analysis.total_samples, 2)
        self.assertEqual(analysis.coverage_pct, Decimal('100.00'))
        self.assertTrue(analysis.natca_compliant)


class CellSiteTests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        from regulatory.models import NetworkCellSite
        self.site = NetworkCellSite.objects.create(
            operator_code='orange',
            site_id='FTW001',
            site_name='Lumley Beach Tower',
            technology='4G',
            region='WESTERN_AREA',
            district='Western Area Urban',
            latitude=Decimal('8.484'),
            longitude=Decimal('-13.230'),
            status='ACTIVE',
        )

    def test_site_creation(self):
        from regulatory.models import NetworkCellSite
        self.assertEqual(NetworkCellSite.objects.count(), 1)
        self.assertEqual(self.site.site_id, 'FTW001')
        self.assertEqual(self.site.operator_code, 'orange')


class CounterDictionaryTests(TestCase):
    databases = {'default', 'regulatory'}

    def setUp(self):
        from regulatory.models import NetworkCounterDefinition
        self.counter = NetworkCounterDefinition.objects.create(
            vendor='Huawei',
            network_element='eNodeB',
            counter_id='L.RRC.ConnReq.Att',
            counter_name='RRC Connection Request Attempts',
            technology='4G',
            kpi_code='CSSR',
            formula_role='DENOMINATOR',
        )

    def test_counter_creation(self):
        from regulatory.models import NetworkCounterDefinition
        self.assertEqual(NetworkCounterDefinition.objects.count(), 1)
        self.assertEqual(self.counter.counter_id, 'L.RRC.ConnReq.Att')
        self.assertEqual(self.counter.kpi_code, 'CSSR')


