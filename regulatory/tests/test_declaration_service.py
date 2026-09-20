from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog
from reference.models import Operator
from regulatory.models import DeclarationReview, OperatorDeclaration
from regulatory.services.declaration_service import DeclarationService, DeclarationValidationError


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DeclarationServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='declarationadmin', password='test', is_regulatory_admin=True)
        Operator.objects.create(code='orange', name='Orange', home_plmn='61901', home_mcc='619', home_mnc='01')
        self.data = {
            'operator_code': 'orange', 'period_start': date(2026, 8, 1), 'period_end': date(2026, 8, 31),
            'declaration_type': OperatorDeclaration.DeclarationType.GST_REVENUE,
            'total_declared_revenue': '1040000000.00', 'declared_taxable_revenue': '910000000.00',
            'total_declared_tax': '136500000.00', 'international_revenue': '0', 'roaming_revenue': '0',
            'interconnect_revenue': '0', 'other_taxable_revenue': '0', 'notes': 'August declaration',
        }

    def test_create_generates_reference_and_rejects_duplicate_version(self):
        declaration = DeclarationService.create_declaration(self.data, self.user)

        self.assertEqual(declaration.reference, f'NRA-DEC-2026-{declaration.pk:06d}')
        self.assertEqual(declaration.version, 1)
        self.assertTrue(AuditLog.objects.filter(entity_type='OperatorDeclaration', entity_id=str(declaration.pk)).exists())
        with self.assertRaises(DeclarationValidationError):
            DeclarationService.create_declaration(self.data, self.user)

    def test_controlled_workflow_and_rejection_reason(self):
        declaration = DeclarationService.create_declaration(self.data, self.user)
        DeclarationService.submit_declaration(declaration, self.user)
        DeclarationService.validate_declaration(declaration, self.user)
        DeclarationService.review_declaration(declaration, 'start', self.user)
        DeclarationService.review_declaration(declaration, 'accept', self.user, notes='Reviewed and accepted.')

        declaration.refresh_from_db()
        self.assertEqual(declaration.status, OperatorDeclaration.Status.ACCEPTED)
        self.assertTrue(DeclarationReview.objects.filter(declaration=declaration, action=DeclarationReview.Action.ACCEPTED).exists())

        rejected = DeclarationService.create_declaration({**self.data, 'declaration_type': OperatorDeclaration.DeclarationType.REVENUE}, self.user)
        DeclarationService.submit_declaration(rejected, self.user)
        with self.assertRaises(DeclarationValidationError):
            DeclarationService.review_declaration(rejected, 'reject', self.user)

    def test_amendment_keeps_prior_version(self):
        original = DeclarationService.create_declaration(self.data, self.user)
        DeclarationService.submit_declaration(original, self.user)
        amendment = DeclarationService.create_declaration(self.data, self.user, version_of=original)

        original.refresh_from_db()
        self.assertFalse(original.is_current_version)
        self.assertEqual(amendment.version, 2)
        self.assertEqual(amendment.parent_declaration, original)

    def test_list_filters_and_csv_import_preview(self):
        declaration = DeclarationService.create_declaration(self.data, self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('regulatory:declaration_list'), {'operator': 'orange', 'status': 'DRAFT'})
        self.assertContains(response, declaration.reference)

        csv_data = ('operator_code,period_start,period_end,declaration_type,declared_revenue,declared_taxable_revenue,declared_gst\n'
                    'orange,2026-09-01,2026-09-30,REVENUE,100.00,90.00,0.00\n')
        response = self.client.post(reverse('regulatory:declaration_import'), {'file': SimpleUploadedFile('declarations.csv', csv_data.encode(), content_type='text/csv')})
        self.assertContains(response, 'Confirm Declaration Import')
        response = self.client.post(reverse('regulatory:declaration_import'), {'confirm': '1'})
        self.assertRedirects(response, reverse('regulatory:declaration_list'))
        self.assertEqual(OperatorDeclaration.objects.count(), 2)
