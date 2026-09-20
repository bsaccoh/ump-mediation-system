from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from regulatory.models.risk import RiskRule, RiskAlert, RiskRuleVersion, RiskAlertOperatorResponse
from regulatory.models.reconciliation import ReconciliationRun
from regulatory.services.risk_engine import RiskEngine

User = get_user_model()


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RiskAlertModelTests(TestCase):
    def make_rule(self, **overrides):
        defaults = dict(
            name='Revenue Variance', code='RUL1', alert_type=RiskRule.AlertType.REVENUE,
            metric='revenue_variance_pct', comparison_operator=RiskRule.ComparisonOperator.GT,
            threshold_value=Decimal('10'), threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE,
            severity=RiskRule.Severity.HIGH, status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        defaults.update(overrides)
        return RiskRule.objects.create(**defaults)

    def test_reference_auto_generated_sequentially(self):
        rule = self.make_rule()
        a1 = RiskAlert.objects.create(rule=rule, operator_code='orange', severity='HIGH',
                                       metric_value=15, threshold_value=10, description='x')
        a2 = RiskAlert.objects.create(rule=rule, operator_code='orange', severity='HIGH',
                                       metric_value=16, threshold_value=10, description='y')
        self.assertTrue(a1.reference.startswith('RA-'))
        seq1 = int(a1.reference.rsplit('-', 1)[-1])
        seq2 = int(a2.reference.rsplit('-', 1)[-1])
        self.assertEqual(seq2, seq1 + 1)

    def test_rule_status_active_keeps_enabled_in_sync(self):
        rule = self.make_rule(status=RiskRule.Status.INACTIVE)
        self.assertFalse(rule.enabled)
        rule.status = RiskRule.Status.ACTIVE
        rule.save()
        self.assertTrue(rule.enabled)


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RiskEngineTests(TestCase):
    def setUp(self):
        self.engine = RiskEngine()
        self.rule = RiskRule.objects.create(
            name='GST Variance', code='RUL2', alert_type=RiskRule.AlertType.GST,
            metric='gst_variance_pct', comparison_operator=RiskRule.ComparisonOperator.GT,
            threshold_value=Decimal('10'), threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE,
            severity=RiskRule.Severity.HIGH, status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        self.user = User.objects.create_user(username='analyst', password='x', is_regulator=True)

    def test_create_alert_deduplicates_open_occurrences(self):
        first = self.engine.create_alert(self.rule, 'orange', metric_value=15)
        second = self.engine.create_alert(self.rule, 'orange', metric_value=18)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.occurrence_count, 2)
        self.assertEqual(RiskAlert.objects.filter(rule=self.rule).count(), 1)

    def test_create_alert_does_not_dedupe_after_resolution(self):
        first = self.engine.create_alert(self.rule, 'orange', metric_value=15)
        self.engine.start_investigation(first, self.user)
        self.engine.resolve_alert(first, self.user, RiskAlert.ResolutionType.OPERATOR_CORRECTED, 'fixed')
        second = self.engine.create_alert(self.rule, 'orange', metric_value=18)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(RiskAlert.objects.filter(rule=self.rule).count(), 2)

    def test_evaluate_rule_auto_resolves_when_metric_returns_to_normal(self):
        # traffic_drop_pct with no TrafficSummary data evaluates to 0, which will not
        # breach a positive threshold, so an existing open alert should auto-resolve.
        rule = RiskRule.objects.create(
            name='Traffic Drop', code='RUL3', alert_type=RiskRule.AlertType.TRAFFIC,
            metric='traffic_drop_pct', comparison_operator=RiskRule.ComparisonOperator.GT,
            threshold_value=Decimal('20'), threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE,
            severity=RiskRule.Severity.MEDIUM, status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        alert = RiskAlert.objects.create(rule=rule, operator_code='orange', severity='MEDIUM',
                                          metric_value=25, threshold_value=20, description='seed')
        self.engine.evaluate_rule(rule, 'orange')
        alert.refresh_from_db()
        self.assertEqual(alert.status, RiskAlert.Status.RESOLVED)

    def test_resolve_and_dismiss_workflow(self):
        alert = self.engine.create_alert(self.rule, 'orange', metric_value=15)
        self.engine.assign_alert(alert, self.user, self.user)
        self.assertEqual(alert.status, RiskAlert.Status.ASSIGNED)
        self.engine.dismiss_alert(alert, self.user, RiskAlert.DismissalReason.FALSE_POSITIVE, 'not real')
        self.assertEqual(alert.status, RiskAlert.Status.DISMISSED)
        self.assertEqual(alert.dismissed_by, self.user)

    def test_create_audit_case_links_back_to_alert(self):
        alert = self.engine.create_alert(self.rule, 'orange', metric_value=15)
        case = self.engine.create_audit_case(alert, self.user)
        alert.refresh_from_db()
        self.assertEqual(alert.audit_case_id, case.pk)


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RiskAlertViewTests(TestCase):
    def setUp(self):
        self.rule = RiskRule.objects.create(
            name='Revenue Variance', code='RUL1', alert_type=RiskRule.AlertType.REVENUE,
            metric='revenue_variance_pct', comparison_operator=RiskRule.ComparisonOperator.GT,
            threshold_value=Decimal('10'), threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE,
            severity=RiskRule.Severity.HIGH, status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        self.regulator = User.objects.create_user(username='reg', password='x', is_regulator=True)
        self.supervisor = User.objects.create_user(username='sup', password='x', is_regulatory_admin=True)
        self.plain_user = User.objects.create_user(username='plain', password='x')
        self.open_alert = RiskAlert.objects.create(
            rule=self.rule, operator_code='orange', severity='HIGH',
            metric_value=15, threshold_value=10, description='open one',
        )
        self.resolved_alert = RiskAlert.objects.create(
            rule=self.rule, operator_code='africell', severity='MEDIUM',
            metric_value=12, threshold_value=10, description='resolved one',
            status=RiskAlert.Status.RESOLVED,
        )

    def test_list_requires_login(self):
        response = self.client.get(reverse('regulatory:risk_alert_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_regulator_forbidden(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse('regulatory:risk_alert_list'))
        self.assertEqual(response.status_code, 302)

    def test_kpi_summary_counts(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['total'], 2)
        self.assertEqual(response.context['summary']['open'], 1)
        self.assertEqual(response.context['summary']['resolved'], 1)

    def test_status_filter(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_list'), {'status': 'RESOLVED'})
        alerts = list(response.context['alerts'])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pk, self.resolved_alert.pk)

    def test_operator_filter(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_list'), {'operator': 'africell'})
        alerts = list(response.context['alerts'])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].operator_code, 'africell')

    def test_search_matches_reference_and_description(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_list'), {'search': self.open_alert.reference})
        alerts = list(response.context['alerts'])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pk, self.open_alert.pk)

    def test_resolve_requires_type_and_notes(self):
        RiskEngine().start_investigation(self.open_alert, self.supervisor)
        self.client.force_login(self.supervisor)
        url = reverse('regulatory:risk_alert_detail', args=[self.open_alert.pk])
        self.client.post(url, {'action': 'resolve'})
        self.open_alert.refresh_from_db()
        self.assertEqual(self.open_alert.status, RiskAlert.Status.INVESTIGATING)

        self.client.post(url, {'action': 'resolve', 'resolution_type': 'OPERATOR_CORRECTED', 'notes': 'fixed'})
        self.open_alert.refresh_from_db()
        self.assertEqual(self.open_alert.status, RiskAlert.Status.RESOLVED)

    def test_resolve_requires_supervisor(self):
        RiskEngine().start_investigation(self.open_alert, self.regulator)
        self.client.force_login(self.regulator)
        url = reverse('regulatory:risk_alert_detail', args=[self.open_alert.pk])
        self.client.post(url, {'action': 'resolve', 'resolution_type': 'OPERATOR_CORRECTED', 'notes': 'fixed'})
        self.open_alert.refresh_from_db()
        self.assertEqual(self.open_alert.status, RiskAlert.Status.INVESTIGATING)

    def test_dismiss_requires_reason(self):
        self.client.force_login(self.regulator)
        url = reverse('regulatory:risk_alert_detail', args=[self.open_alert.pk])
        self.client.post(url, {'action': 'dismiss'})
        self.open_alert.refresh_from_db()
        self.assertEqual(self.open_alert.status, RiskAlert.Status.OPEN)

        self.client.post(url, {'action': 'dismiss', 'dismissal_reason': 'FALSE_POSITIVE'})
        self.open_alert.refresh_from_db()
        self.assertEqual(self.open_alert.status, RiskAlert.Status.DISMISSED)

    def test_export_respects_filters(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_export'), {'status': 'RESOLVED'})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.resolved_alert.reference, body)
        self.assertNotIn(self.open_alert.reference, body)


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RiskRuleVersioningTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='x', is_regulatory_admin=True)
        self.rule = RiskRule.objects.create(
            name='Tariff Variance', code='RUL4', alert_type=RiskRule.AlertType.TARIFF,
            metric='tariff_variance_pct', comparison_operator=RiskRule.ComparisonOperator.GT,
            threshold_value=Decimal('5'), threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE,
            severity=RiskRule.Severity.MEDIUM, status=RiskRule.Status.ACTIVE, operator_code='orange',
        )

    def test_material_change_bumps_version_and_snapshots_history(self):
        self.client.force_login(self.admin)
        url = reverse('regulatory:risk_rule_edit', args=[self.rule.pk])
        self.client.post(url, {
            'name': self.rule.name, 'code': self.rule.code, 'alert_type': self.rule.alert_type,
            'metric': self.rule.metric, 'operator_code': self.rule.operator_code, 'service_scope': '',
            'comparison_operator': self.rule.comparison_operator, 'threshold_value': '15',
            'threshold_unit': self.rule.threshold_unit, 'severity': self.rule.severity,
            'status': self.rule.status, 'description': 'updated',
        })
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.version, 2)
        self.assertEqual(self.rule.threshold_value, Decimal('15'))
        snapshot = RiskRuleVersion.objects.get(rule=self.rule, version=1)
        self.assertEqual(snapshot.threshold_value, Decimal('5'))

    def test_non_material_change_does_not_bump_version(self):
        self.client.force_login(self.admin)
        url = reverse('regulatory:risk_rule_edit', args=[self.rule.pk])
        self.client.post(url, {
            'name': self.rule.name, 'code': self.rule.code, 'alert_type': self.rule.alert_type,
            'metric': self.rule.metric, 'operator_code': self.rule.operator_code, 'service_scope': '',
            'comparison_operator': self.rule.comparison_operator, 'threshold_value': str(self.rule.threshold_value),
            'threshold_unit': self.rule.threshold_unit, 'severity': self.rule.severity,
            'status': self.rule.status, 'description': 'just a description tweak',
        })
        self.rule.refresh_from_db()
        self.assertEqual(self.rule.version, 1)
        self.assertFalse(RiskRuleVersion.objects.filter(rule=self.rule).exists())


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RiskAlertInvestigationWorkspaceTests(TestCase):
    """Page 10: the tabbed alert investigation workspace."""

    def setUp(self):
        self.engine = RiskEngine()
        self.regulator = User.objects.create_user(username='reg2', password='x', is_regulator=True)
        self.supervisor = User.objects.create_user(username='sup2', password='x', is_regulatory_admin=True)
        self.rule = RiskRule.objects.create(
            name='GST Variance', code='RUL9', alert_type=RiskRule.AlertType.GST, metric='gst_variance_pct',
            comparison_operator=RiskRule.ComparisonOperator.GT, threshold_value=Decimal('10'),
            threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE, severity=RiskRule.Severity.HIGH,
            status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        self.alert = self.engine.create_alert(self.rule, 'orange', metric_value=Decimal('15.2'))

    def test_lookup_by_reference_and_by_pk(self):
        by_ref = self.engine.get_alert(self.alert.reference)
        by_pk = self.engine.get_alert(self.alert.pk)
        self.assertEqual(by_ref.pk, by_pk.pk)

    def test_detail_view_resolves_reference_in_url(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_detail', args=[self.alert.reference]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['alert'].pk, self.alert.pk)

    def test_all_tabs_load(self):
        self.client.force_login(self.regulator)
        for tab in ('overview', 'rule', 'evidence', 'financial', 'reconciliation', 'declaration',
                    'cdrs', 'operator_response', 'comments', 'activity'):
            response = self.client.get(reverse('regulatory:risk_alert_detail', args=[self.alert.pk]), {'tab': tab})
            self.assertEqual(response.status_code, 200, f'tab={tab}')

    def test_rule_snapshot_reflects_version_at_trigger_time_not_latest(self):
        # Edit the rule materially after the alert fired — the alert must still show
        # the threshold that was active when it triggered, not the new one.
        self.client.force_login(self.supervisor)
        self.client.post(reverse('regulatory:risk_rule_edit', args=[self.rule.pk]), {
            'name': self.rule.name, 'code': self.rule.code, 'alert_type': self.rule.alert_type,
            'metric': self.rule.metric, 'operator_code': self.rule.operator_code, 'service_scope': '',
            'comparison_operator': self.rule.comparison_operator, 'threshold_value': '25',
            'threshold_unit': self.rule.threshold_unit, 'severity': self.rule.severity,
            'status': self.rule.status, 'description': 'raised threshold',
        })
        snapshot = self.engine.get_rule_snapshot(self.alert)
        self.assertEqual(snapshot['threshold_value'], Decimal('10'))
        self.assertTrue(snapshot['is_stale'])

    def test_non_financial_alert_does_not_show_fabricated_financials(self):
        rule = RiskRule.objects.create(
            name='Missing CDRs', code='RUL10', alert_type=RiskRule.AlertType.DATA_QUALITY, metric='missing_cdr_pct',
            comparison_operator=RiskRule.ComparisonOperator.GT, threshold_value=Decimal('5'),
            threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE, severity=RiskRule.Severity.MEDIUM,
            status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        alert = self.engine.create_alert(rule, 'orange', metric_value=Decimal('8'))
        exposure = self.engine.get_financial_exposure(alert)
        self.assertFalse(exposure['is_financial'])
        self.assertIsNone(exposure['expected_revenue'])

    def test_financial_exposure_and_reconciliation_from_linked_run(self):
        run = ReconciliationRun.objects.create(
            operator_code='orange', level=ReconciliationRun.Level.GST, reference='RCN-TEST-1',
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            expected_gst=Decimal('100'), declared_gst=Decimal('85'), gst_variance=Decimal('15'),
        )
        self.alert.source_type = RiskAlert.SourceType.RECONCILIATION
        self.alert.source_reference = run.reference
        self.alert.save()

        exposure = self.engine.get_financial_exposure(self.alert)
        self.assertEqual(exposure['expected_gst'], Decimal('100'))
        reconciliation = self.engine.get_reconciliation(self.alert)
        self.assertEqual(reconciliation['run'].pk, run.pk)

    def test_operator_response_multi_request_workflow(self):
        self.engine.start_investigation(self.alert, self.regulator)
        first = self.engine.request_operator_response(self.alert, self.regulator, 'Explain GST gap', 'Please explain')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, RiskAlert.Status.AWAITING_OPERATOR)

        self.engine.record_operator_response(first, 'We corrected our filing', 'Ops Lead')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, RiskAlert.Status.UNDER_REVIEW)

        self.engine.review_operator_response(first, self.supervisor, accept=True)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, RiskAlert.Status.INVESTIGATING)

        # A second, independent request can still be raised later in the investigation.
        second = self.engine.request_operator_response(self.alert, self.regulator, 'Follow-up', 'More detail needed')
        self.assertEqual(RiskAlertOperatorResponse.objects.filter(alert=self.alert).count(), 2)
        self.assertNotEqual(first.pk, second.pk)

    def test_update_severity_requires_reason_on_downgrade(self):
        self.alert.severity = RiskRule.Severity.CRITICAL
        self.alert.save()
        with self.assertRaises(ValueError):
            self.engine.update_severity(self.alert, self.regulator, RiskRule.Severity.LOW)
        self.engine.update_severity(self.alert, self.regulator, RiskRule.Severity.LOW, reason='Confirmed benign')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.severity, RiskRule.Severity.LOW)

    def test_create_audit_case_prevents_duplicate_unless_forced(self):
        self.engine.create_audit_case(self.alert, self.regulator)
        with self.assertRaises(ValueError):
            self.engine.create_audit_case(self.alert, self.regulator)
        # Supervisor override still works.
        second_case = self.engine.create_audit_case(self.alert, self.supervisor, force=True)
        self.assertIsNotNone(second_case.pk)

    def test_activity_log_records_lifecycle_events(self):
        self.engine.assign_alert(self.alert, self.regulator, self.supervisor)
        self.engine.start_investigation(self.alert, self.regulator)
        self.engine.add_comment(self.alert, self.regulator, 'Looking into this')
        log = self.engine.get_activity_log(self.alert)
        actions_logged = [entry.description for entry in log]
        self.assertTrue(any('assigned' in d.lower() for d in actions_logged))
        self.assertTrue(any('investigation started' in d.lower() for d in actions_logged))
        self.assertTrue(any('comment added' in d.lower() for d in actions_logged))

    def test_severity_downgrade_without_reason_shows_error_not_500(self):
        self.alert.severity = RiskRule.Severity.CRITICAL
        self.alert.save()
        self.client.force_login(self.regulator)
        url = reverse('regulatory:risk_alert_detail', args=[self.alert.pk])
        response = self.client.post(url, {'action': 'update_severity', 'severity': 'LOW'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.severity, RiskRule.Severity.CRITICAL)

    def test_evidence_export_downloads_workbook(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:risk_alert_evidence_export', args=[self.alert.reference]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
