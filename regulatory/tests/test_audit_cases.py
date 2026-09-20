from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from regulatory.models.audit import AuditCase, AuditFinding, AuditEvidence, AuditOperatorResponse, AuditCaseComment, AuditCaseReport
from regulatory.models.risk import RiskRule, RiskAlert
from regulatory.models.reconciliation import ReconciliationRun
from regulatory.services.audit_case_service import AuditCaseService

User = get_user_model()


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AuditCaseModelTests(TestCase):
    def test_case_number_auto_generated_sequentially(self):
        c1 = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='A', operator_code='orange')
        c2 = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='B', operator_code='orange')
        seq1 = int(c1.case_number.rsplit('-', 1)[-1])
        seq2 = int(c2.case_number.rsplit('-', 1)[-1])
        self.assertEqual(seq2, seq1 + 1)

    def test_allowed_next_statuses_open(self):
        case = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='A', operator_code='orange')
        self.assertIn(AuditCase.Status.INVESTIGATION, case.allowed_next_statuses())
        self.assertNotIn(AuditCase.Status.CLOSED, case.allowed_next_statuses())

    def test_allowed_next_statuses_closed_is_terminal(self):
        case = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='A',
                                         operator_code='orange', status=AuditCase.Status.CLOSED)
        self.assertEqual(case.allowed_next_statuses(), [])

    def test_finding_variance_auto_computed(self):
        case = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='A', operator_code='orange')
        finding = AuditFinding.objects.create(case=case, finding_type=AuditFinding.FindingType.REVENUE_LEAKAGE,
                                               description='x', expected_value=Decimal('100'), observed_value=Decimal('80'))
        self.assertEqual(finding.variance, Decimal('-20'))


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AuditCaseServiceTests(TestCase):
    def setUp(self):
        self.service = AuditCaseService()
        self.user = User.objects.create_user(username='auditor', password='x', is_auditor=True)
        self.supervisor = User.objects.create_user(username='super', password='x', is_regulatory_admin=True)

    def test_create_case_sets_case_number_and_status(self):
        case = self.service.create_case({'operator_code': 'orange', 'case_type': AuditCase.CaseType.OTHER}, self.user)
        self.assertTrue(case.case_number.startswith('AUD-'))
        self.assertEqual(case.status, AuditCase.Status.OPEN)
        self.assertEqual(case.opened_by, self.user)

    def test_create_case_with_assignee_moves_to_assigned(self):
        case = self.service.create_case({'operator_code': 'orange', 'assigned_to': self.user}, self.user)
        self.assertEqual(case.status, AuditCase.Status.ASSIGNED)

    def test_create_from_risk_alert_links_and_seeds_evidence(self):
        rule = RiskRule.objects.create(name='R1', code='R1', alert_type=RiskRule.AlertType.GST, metric='gst_variance_pct',
                                        comparison_operator=RiskRule.ComparisonOperator.GT, threshold_value=10,
                                        threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE, severity=RiskRule.Severity.HIGH,
                                        status=RiskRule.Status.ACTIVE, operator_code='orange')
        alert = RiskAlert.objects.create(rule=rule, operator_code='orange', severity='HIGH', alert_type='GST',
                                          metric_value=20, threshold_value=10, description='GST too high',
                                          potential_exposure=Decimal('5000'))
        case = self.service.create_from_risk_alert(alert, self.user)
        alert.refresh_from_db()
        self.assertEqual(alert.audit_case_id, case.pk)
        self.assertEqual(case.case_type, AuditCase.CaseType.GST_VARIANCE)
        self.assertEqual(case.potential_exposure, Decimal('5000'))
        self.assertTrue(case.evidence.filter(evidence_type=AuditEvidence.EvidenceType.RISK_ALERT).exists())

    def test_create_from_reconciliation_picks_dominant_variance(self):
        run = ReconciliationRun.objects.create(
            operator_code='qcell', level=ReconciliationRun.Level.FULL,
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            expected_revenue=Decimal('1000'), declared_revenue=Decimal('400'), revenue_variance=Decimal('600'),
            expected_gst=Decimal('100'), declared_gst=Decimal('95'), gst_variance=Decimal('5'),
            risk_level='HIGH',
        )
        case = self.service.create_from_reconciliation(run, self.user)
        self.assertEqual(case.case_type, AuditCase.CaseType.REVENUE_VARIANCE)
        self.assertEqual(case.reconciliation_run_id, run.pk)
        self.assertEqual(case.potential_exposure, Decimal('605'))

    def test_start_investigation_requires_valid_transition(self):
        case = self.service.create_case({'operator_code': 'orange', 'status': AuditCase.Status.CLOSED}, self.user)
        case.status = AuditCase.Status.CLOSED
        case.save()
        with self.assertRaises(ValueError):
            self.service.start_investigation(case, self.user)

    def test_update_risk_requires_reason_on_downgrade(self):
        case = self.service.create_case({'operator_code': 'orange', 'risk_level': AuditCase.RiskLevel.CRITICAL}, self.user)
        with self.assertRaises(ValueError):
            self.service.update_risk(case, self.user, AuditCase.RiskLevel.LOW)
        self.service.update_risk(case, self.user, AuditCase.RiskLevel.LOW, reason='Confirmed false positive')
        case.refresh_from_db()
        self.assertEqual(case.risk_level, AuditCase.RiskLevel.LOW)

    def test_resolve_then_close_then_reopen_workflow(self):
        case = self.service.create_case({'operator_code': 'orange'}, self.user)
        self.service.start_investigation(case, self.user)
        self.service.resolve_case(case, self.user, AuditCase.ResolutionType.OPERATOR_CORRECTION, 'Operator fixed it',
                                   AuditCase.RegulatoryDecision.NO_FURTHER_ACTION)
        self.assertEqual(case.status, AuditCase.Status.RESOLVED)

        with self.assertRaises(ValueError):
            self.service.close_case(AuditCase.objects.create(case_number=AuditCase.generate_case_number(),
                                                               title='x', operator_code='orange'), self.user)

        self.service.close_case(case, self.user, closure_notes='All good')
        self.assertEqual(case.status, AuditCase.Status.CLOSED)

        with self.assertRaises(ValueError):
            self.service.reopen_case(case, self.user, '')
        self.service.reopen_case(case, self.user, 'New evidence surfaced')
        self.assertEqual(case.status, AuditCase.Status.INVESTIGATION)
        self.assertEqual(case.reopen_count, 1)

    def test_operator_response_workflow(self):
        case = self.service.create_case({'operator_code': 'orange'}, self.user)
        self.service.start_investigation(case, self.user)
        response = self.service.request_operator_response(case, self.user, 'Explain variance', 'Please explain')
        case.refresh_from_db()
        self.assertEqual(case.status, AuditCase.Status.AWAITING_OPERATOR)

        self.service.record_operator_response(response, 'Here is our explanation', 'Ops Manager')
        case.refresh_from_db()
        self.assertEqual(case.status, AuditCase.Status.OPERATOR_RESPONSE)
        self.assertEqual(response.response_status, AuditOperatorResponse.ResponseStatus.SUBMITTED)

        self.service.review_operator_response(response, self.supervisor, accept=True)
        case.refresh_from_db()
        self.assertEqual(case.status, AuditCase.Status.REGULATORY_REVIEW)
        self.assertEqual(response.response_status, AuditOperatorResponse.ResponseStatus.ACCEPTED)

    def test_financial_analysis_from_linked_reconciliation(self):
        run = ReconciliationRun.objects.create(
            operator_code='orange', level=ReconciliationRun.Level.FULL,
            period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
            expected_revenue=Decimal('1000'), declared_revenue=Decimal('900'), revenue_variance=Decimal('100'),
        )
        case = self.service.create_from_reconciliation(run, self.user)
        analysis = self.service.get_financial_analysis(case)
        self.assertEqual(analysis['expected_revenue'], Decimal('1000'))
        self.assertEqual(analysis['declared_revenue'], Decimal('900'))

    def test_calculation_trace_empty_without_linked_source(self):
        case = self.service.create_case({'operator_code': 'orange'}, self.user)
        self.assertEqual(self.service.get_calculation_trace(case), [])


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AuditCaseViewTests(TestCase):
    def setUp(self):
        self.service = AuditCaseService()
        self.regulator = User.objects.create_user(username='reg', password='x', is_regulator=True)
        self.supervisor = User.objects.create_user(username='sup', password='x', is_regulatory_admin=True)
        self.open_case = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='Open case',
                                                    operator_code='orange', case_type=AuditCase.CaseType.GST_VARIANCE,
                                                    risk_level=AuditCase.RiskLevel.HIGH)
        self.closed_case = AuditCase.objects.create(case_number=AuditCase.generate_case_number(), title='Closed case',
                                                      operator_code='africell', case_type=AuditCase.CaseType.TARIFF_VIOLATION,
                                                      status=AuditCase.Status.CLOSED, risk_level=AuditCase.RiskLevel.LOW)

    def test_list_requires_login(self):
        response = self.client.get(reverse('regulatory:audit_case_list'))
        self.assertEqual(response.status_code, 302)

    def test_kpi_summary_counts(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['total'], 2)
        self.assertEqual(response.context['summary']['open'], 1)
        self.assertEqual(response.context['summary']['closed'], 1)

    def test_operator_filter(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_list'), {'operator': 'africell'})
        cases = list(response.context['cases'])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].pk, self.closed_case.pk)

    def test_status_and_risk_filters(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_list'), {'status': 'CLOSED', 'risk_level': 'LOW'})
        cases = list(response.context['cases'])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].pk, self.closed_case.pk)

    def test_search_matches_case_number(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_list'), {'search': self.open_case.case_number})
        cases = list(response.context['cases'])
        self.assertEqual(len(cases), 1)

    def test_non_admin_cannot_create_case(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_create'))
        self.assertEqual(response.status_code, 302)

    def test_admin_can_create_manual_case(self):
        self.client.force_login(self.supervisor)
        response = self.client.post(reverse('regulatory:audit_case_create'), {
            'operator_code': 'orange', 'case_type': AuditCase.CaseType.DATA_QUALITY,
            'period_start': '2026-08-01', 'period_end': '2026-08-31',
            'finding': 'Missing CDRs detected', 'risk_level': 'HIGH', 'potential_exposure': '1000',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditCase.objects.filter(operator_code='orange', case_type=AuditCase.CaseType.DATA_QUALITY).exists())

    def test_detail_overview_tab_loads(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_detail', args=[self.open_case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_tab'], 'overview')

    def test_detail_all_tabs_load(self):
        self.client.force_login(self.regulator)
        for tab in ('overview', 'findings', 'evidence', 'financial', 'reconciliation', 'declaration',
                    'tariff_tax', 'cdrs', 'source_files', 'calculations', 'operator_responses', 'comments', 'activity'):
            response = self.client.get(reverse('regulatory:audit_case_detail', args=[self.open_case.pk]), {'tab': tab})
            self.assertEqual(response.status_code, 200, f'tab={tab}')

    def test_add_finding_and_evidence(self):
        self.client.force_login(self.regulator)
        url = reverse('regulatory:audit_case_detail', args=[self.open_case.pk])
        self.client.post(url, {'action': 'add_finding', 'finding_type': 'REVENUE_LEAKAGE', 'severity': 'HIGH',
                                'description': 'Leakage found', 'financial_exposure': '500'})
        self.assertTrue(self.open_case.findings.exists())

        self.client.post(url, {'action': 'add_evidence', 'evidence_type': 'ANALYST_NOTE', 'description': 'Note'})
        self.assertTrue(self.open_case.evidence.exists())

    def test_resolve_requires_type_and_summary(self):
        self.service.start_investigation(self.open_case, self.supervisor)
        self.client.force_login(self.supervisor)
        url = reverse('regulatory:audit_case_detail', args=[self.open_case.pk])
        self.client.post(url, {'action': 'resolve'})
        self.open_case.refresh_from_db()
        self.assertNotEqual(self.open_case.status, AuditCase.Status.RESOLVED)

        self.client.post(url, {'action': 'resolve', 'resolution_type': 'NO_ISSUE', 'resolution_summary': 'Checked, fine',
                                'regulatory_decision': 'NO_FURTHER_ACTION'})
        self.open_case.refresh_from_db()
        self.assertEqual(self.open_case.status, AuditCase.Status.RESOLVED)

    def test_close_requires_resolved_first(self):
        self.client.force_login(self.supervisor)
        url = reverse('regulatory:audit_case_detail', args=[self.open_case.pk])
        self.client.post(url, {'action': 'close'})
        self.open_case.refresh_from_db()
        self.assertNotEqual(self.open_case.status, AuditCase.Status.CLOSED)

    def test_reopen_requires_reason(self):
        self.client.force_login(self.supervisor)
        url = reverse('regulatory:audit_case_detail', args=[self.closed_case.pk])
        self.client.post(url, {'action': 'reopen'})
        self.closed_case.refresh_from_db()
        self.assertEqual(self.closed_case.status, AuditCase.Status.CLOSED)

        self.client.post(url, {'action': 'reopen', 'reopen_reason': 'New evidence'})
        self.closed_case.refresh_from_db()
        self.assertEqual(self.closed_case.status, AuditCase.Status.INVESTIGATION)

    def test_only_supervisor_can_resolve(self):
        self.client.force_login(self.regulator)
        url = reverse('regulatory:audit_case_detail', args=[self.open_case.pk])
        self.client.post(url, {'action': 'resolve', 'resolution_type': 'NO_ISSUE', 'resolution_summary': 'x'})
        self.open_case.refresh_from_db()
        self.assertNotEqual(self.open_case.status, AuditCase.Status.RESOLVED)

    def test_comment_and_activity_log(self):
        self.client.force_login(self.regulator)
        url = reverse('regulatory:audit_case_detail', args=[self.open_case.pk])
        self.client.post(url, {'action': 'comment', 'comment': 'Looks suspicious'})
        self.assertTrue(AuditCaseComment.objects.filter(case=self.open_case).exists())
        response = self.client.get(url, {'tab': 'activity'})
        self.assertTrue(len(response.context['activity_log']) >= 1)

    def test_export_csv(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_export'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.open_case.case_number, response.content.decode())

    def test_export_excel(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_export'), {'format': 'excel'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_report_generation(self):
        self.client.force_login(self.regulator)
        response = self.client.get(reverse('regulatory:audit_case_report', args=[self.open_case.pk]))
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AuditCaseWorkspaceTests(TestCase):
    """Page 11: findings workflow, evidence linking, tariff/tax traceability,
    priority vs risk, regulatory decision, reopen reassignment, report history."""

    def setUp(self):
        self.service = AuditCaseService()
        self.regulator = User.objects.create_user(username='reg3', password='x', is_regulator=True)
        self.supervisor = User.objects.create_user(username='sup3', password='x', is_regulatory_admin=True)
        self.case = self.service.create_case({'operator_code': 'orange', 'case_type': AuditCase.CaseType.GST_VARIANCE}, self.regulator)

    def test_priority_is_independent_of_risk(self):
        self.case.risk_level = AuditCase.RiskLevel.CRITICAL
        self.case.save()
        self.service.update_priority(self.case, self.regulator, AuditCase.Priority.LOW)
        self.case.refresh_from_db()
        self.assertEqual(self.case.priority, AuditCase.Priority.LOW)
        self.assertEqual(self.case.risk_level, AuditCase.RiskLevel.CRITICAL)

    def test_finding_update_recomputes_variance(self):
        finding = self.service.add_finding(self.case, self.regulator, finding_type=AuditFinding.FindingType.REVENUE_LEAKAGE,
                                            description='x', expected_value=Decimal('100'), observed_value=Decimal('90'))
        self.assertEqual(finding.variance, Decimal('-10'))
        self.service.update_finding(finding, self.regulator, observed_value=Decimal('70'))
        finding.refresh_from_db()
        self.assertEqual(finding.variance, Decimal('-30'))

    def test_confirm_and_resolve_finding(self):
        finding = self.service.add_finding(self.case, self.regulator, finding_type=AuditFinding.FindingType.REVENUE_LEAKAGE, description='x')
        self.assertEqual(finding.status, AuditFinding.Status.OPEN)
        self.service.confirm_finding(finding, self.regulator)
        finding.refresh_from_db()
        self.assertEqual(finding.status, AuditFinding.Status.CONFIRMED)
        self.service.resolve_finding(finding, self.regulator)
        finding.refresh_from_db()
        self.assertEqual(finding.status, AuditFinding.Status.RESOLVED)

    def test_link_evidence_to_finding_and_unlink(self):
        finding = self.service.add_finding(self.case, self.regulator, finding_type=AuditFinding.FindingType.OTHER, description='x')
        evidence = self.service.add_evidence(self.case, self.regulator, evidence_type=AuditEvidence.EvidenceType.ANALYST_NOTE, description='note')
        self.assertIsNone(evidence.finding)
        self.service.link_evidence_to_finding(evidence, finding, self.regulator)
        evidence.refresh_from_db()
        self.assertEqual(evidence.finding_id, finding.pk)
        self.service.link_evidence_to_finding(evidence, None, self.regulator)
        evidence.refresh_from_db()
        self.assertIsNone(evidence.finding)

    def test_resolve_requires_regulatory_decision(self):
        self.service.start_investigation(self.case, self.regulator)
        with self.assertRaises(TypeError):
            self.service.resolve_case(self.case, self.regulator, AuditCase.ResolutionType.NO_ISSUE, 'summary')

    def test_outstanding_exposure_computation(self):
        self.case.potential_exposure = Decimal('1000')
        self.case.confirmed_exposure = Decimal('800')
        self.case.recovered_amount = Decimal('300')
        self.case.save()
        self.assertEqual(self.case.outstanding_exposure, Decimal('500'))

    def test_reopen_with_reassignment_moves_to_assigned(self):
        self.service.start_investigation(self.case, self.regulator)
        self.service.resolve_case(self.case, self.supervisor, AuditCase.ResolutionType.NO_ISSUE, 'ok',
                                   AuditCase.RegulatoryDecision.NO_FURTHER_ACTION)
        self.service.close_case(self.case, self.supervisor)
        new_officer = User.objects.create_user(username='officer2', password='x', is_regulator=True)
        self.service.reopen_case(self.case, self.supervisor, 'New evidence', assigned_to=new_officer)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, AuditCase.Status.ASSIGNED)
        self.assertEqual(self.case.assigned_to_id, new_officer.pk)

    def test_reopen_without_reassignment_moves_to_investigation(self):
        self.service.start_investigation(self.case, self.regulator)
        self.service.resolve_case(self.case, self.supervisor, AuditCase.ResolutionType.NO_ISSUE, 'ok',
                                   AuditCase.RegulatoryDecision.NO_FURTHER_ACTION)
        self.service.close_case(self.case, self.supervisor)
        self.service.reopen_case(self.case, self.supervisor, 'New evidence')
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, AuditCase.Status.INVESTIGATION)

    def test_report_history_never_overwrites_prior_versions(self):
        r1 = self.service.generate_report(self.case, self.regulator)
        r2 = self.service.generate_report(self.case, self.regulator)
        self.assertEqual(r1.version, 1)
        self.assertEqual(r2.version, 2)
        self.assertNotEqual(r1.checksum, '')
        history = list(self.service.get_report_history(self.case))
        self.assertEqual(len(history), 2)
        self.assertTrue(AuditCaseReport.objects.filter(pk=r1.pk).exists())

    def test_tariffs_and_tax_rules_use_exact_link_when_available(self):
        from regulatory.models.tariffs import Tariff
        from regulatory.models.compliance import TariffComplianceResult

        applied = Tariff.objects.create(name='Voice On-Net TT', operator_code='orange', service_type='VOICE',
                                         traffic_type='ON_NET', subscriber_type='ALL', rate=Decimal('1.05'),
                                         charging_unit='PER_MINUTE', effective_from=date(2026, 1, 1), status='ACTIVE')
        result = TariffComplianceResult.objects.create(
            applied_tariff=applied, operator_code='orange', service_type='VOICE', traffic_type='ON_NET',
            tariff_name='Voice On-Net TT', applied_rate=Decimal('1.05'), tolerance_percent=Decimal('2.0'),
            status='NON_COMPLIANT', effective_date=date(2026, 9, 1), applied_tariff_version=1,
        )
        self.case.tariff_compliance_result = result
        self.case.save()
        data = self.service.get_tariffs_and_tax_rules(self.case)
        self.assertEqual(data['tariff_source'], 'exact')
        self.assertEqual(data['tariffs'][0]['tariff'].pk, applied.pk)

    def test_tariffs_fall_back_to_period_overlap_without_exact_link(self):
        from regulatory.models.tariffs import Tariff

        self.case.period_start, self.case.period_end = date(2026, 8, 1), date(2026, 8, 31)
        self.case.save()
        overlapping = Tariff.objects.create(name='Data Off-Net TT', operator_code='orange', service_type='DATA',
                                             traffic_type='OFF_NET', subscriber_type='ALL', rate=Decimal('0.02'),
                                             charging_unit='PER_MB', effective_from=date(2026, 1, 1), status='ACTIVE')
        data = self.service.get_tariffs_and_tax_rules(self.case)
        self.assertEqual(data['tariff_source'], 'period_overlap')
        self.assertIn(overlapping, [row['tariff'] for row in data['tariffs']])

    def test_calculation_trace_uses_rated_aggregate_when_available(self):
        from datetime import datetime
        from regulatory.models.tariffs import Tariff
        from regulatory.models.traffic import TrafficSummary
        from regulatory.models.aggregates import RatedAggregate

        self.case.period_start, self.case.period_end = date(2026, 8, 1), date(2026, 8, 31)
        self.case.save()
        tariff = Tariff.objects.create(name='Voice CT', operator_code='orange', service_type='VOICE',
                                       traffic_type='ON_NET', subscriber_type='ALL', rate=Decimal('1.0'),
                                       charging_unit='PER_MINUTE', effective_from=date(2026, 1, 1), status='ACTIVE')
        ts = TrafficSummary.objects.create(operator_code='orange', period_start=datetime(2026, 8, 5, 10, 0),
                                            period_end=datetime(2026, 8, 5, 11, 0), service_type='VOICE',
                                            traffic_type='ON_NET', total_duration_seconds=6000)
        RatedAggregate.objects.create(traffic_summary=ts, tariff=tariff, tariff_rate_applied=Decimal('1.0'),
                                       charging_unit='PER_MINUTE', rated_amount=Decimal('100.00'),
                                       tax_rate_percent=Decimal('10.0'), tax_amount=Decimal('10.00'),
                                       total_amount=Decimal('110.00'))
        trace = self.service.get_calculation_trace(self.case)
        labels = [step['label'] for step in trace]
        self.assertIn('Normalized CDR → Traffic Classification', labels)
        self.assertIn('Rating Calculation → Expected Revenue', labels)

    def test_non_financial_case_reports_record_metrics_not_fake_financials(self):
        rule = RiskRule.objects.create(
            name='Missing CDRs TT', code='MC-TT', alert_type=RiskRule.AlertType.DATA_QUALITY, metric='missing_cdr_pct',
            comparison_operator=RiskRule.ComparisonOperator.GT, threshold_value=Decimal('5'),
            threshold_unit=RiskRule.ThresholdUnit.PERCENTAGE, severity=RiskRule.Severity.MEDIUM,
            status=RiskRule.Status.ACTIVE, operator_code='orange',
        )
        alert = RiskAlert.objects.create(rule=rule, operator_code='orange', severity='MEDIUM', alert_type='DATA_QUALITY',
                                          occurrence_count=7, metric_value=Decimal('12'), threshold_value=Decimal('5'),
                                          description='missing')
        dq_case = self.service.create_from_risk_alert(alert, self.regulator)
        analysis = self.service.get_financial_analysis(dq_case)
        self.assertFalse(analysis['is_financial'])
        self.assertIsNone(analysis['expected_revenue'])
        self.assertEqual(analysis['affected_records'], 7)
