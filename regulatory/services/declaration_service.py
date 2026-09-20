from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import AuditLog
from regulatory.models import DeclarationAttachment, DeclarationReview, OperatorDeclaration


class DeclarationValidationError(ValueError):
    def __init__(self, field_errors):
        self.field_errors = field_errors
        super().__init__('Declaration validation failed.')


class DeclarationService:
    monetary_fields = (
        'total_declared_revenue', 'declared_taxable_revenue', 'total_declared_tax',
        'international_revenue', 'roaming_revenue', 'interconnect_revenue', 'other_taxable_revenue',
    )

    @staticmethod
    def _audit(user, action, declaration, description, **extra_data):
        AuditLog.objects.create(
            user=user, action=action, entity_type='OperatorDeclaration', entity_id=str(declaration.pk),
            description=description, extra_data=extra_data,
        )

    @classmethod
    def _clean_values(cls, data):
        errors, values = {}, {}
        for field in cls.monetary_fields:
            raw_value = data.get(field, '')
            try:
                value = Decimal(str(raw_value or '0'))
                if value < 0:
                    raise InvalidOperation
                values[field] = value
            except (InvalidOperation, TypeError):
                errors[field] = 'Enter a non-negative amount.'
        period_start, period_end = data.get('period_start'), data.get('period_end')
        if not data.get('operator_code'):
            errors['operator_code'] = 'Operator is required.'
        if not period_start:
            errors['period_start'] = 'Period start is required.'
        if not period_end:
            errors['period_end'] = 'Period end is required.'
        if period_start and period_end and period_end < period_start:
            errors['period_end'] = 'Period end must be on or after period start.'
        if data.get('declaration_type') not in {value for value, _ in OperatorDeclaration.DeclarationType.choices}:
            errors['declaration_type'] = 'Select a valid declaration type.'
        if errors:
            raise DeclarationValidationError(errors)
        return values

    @classmethod
    def get_summary(cls, queryset=None):
        queryset = queryset if queryset is not None else OperatorDeclaration.objects.filter(is_current_version=True)
        return {
            'total': queryset.count(),
            'submitted': queryset.filter(status=OperatorDeclaration.Status.SUBMITTED).count(),
            'under_review': queryset.filter(status__in=[OperatorDeclaration.Status.VALIDATION, OperatorDeclaration.Status.UNDER_REVIEW]).count(),
            'accepted': queryset.filter(status=OperatorDeclaration.Status.ACCEPTED).count(),
            'rejected': queryset.filter(status=OperatorDeclaration.Status.REJECTED).count(),
        }

    @classmethod
    @transaction.atomic
    def create_declaration(cls, data, user, version_of=None):
        values = cls._clean_values(data)
        operator_code = data['operator_code'].strip()
        period_start, period_end = data['period_start'], data['period_end']
        declaration_type = data['declaration_type']
        version = 1
        parent = None
        if version_of:
            if version_of.status == OperatorDeclaration.Status.DRAFT:
                raise DeclarationValidationError({'version': 'Draft declarations should be edited instead of amended.'})
            parent = version_of.parent_declaration or version_of
            version = (OperatorDeclaration.objects.filter(parent_declaration=parent).aggregate(Max('version'))['version__max'] or parent.version) + 1
            OperatorDeclaration.objects.filter(pk=version_of.pk).update(is_current_version=False)
        elif OperatorDeclaration.objects.filter(
            operator_code__iexact=operator_code, period_start=period_start, period_end=period_end,
            declaration_type=declaration_type, version=1,
        ).exists():
            raise DeclarationValidationError({'period_start': 'A declaration for this operator, period, and type already exists.'})

        declaration = OperatorDeclaration.objects.create(
            operator_code=operator_code, period_start=period_start, period_end=period_end,
            declaration_type=declaration_type, version=version, parent_declaration=parent,
            created_by=user, notes=data.get('notes', '').strip(), **values,
        )
        declaration.reference = f'NRA-DEC-{declaration.created_at.year}-{declaration.pk:06d}'
        declaration.save(update_fields=['reference'])
        cls._audit(user, 'CREATE', declaration, f'Created declaration {declaration.reference}.', version=version)
        return declaration

    @classmethod
    @transaction.atomic
    def update_draft(cls, declaration, data, user):
        if declaration.status != OperatorDeclaration.Status.DRAFT:
            raise DeclarationValidationError({'status': 'Only draft declarations can be edited.'})
        values = cls._clean_values(data)
        declaration.operator_code = data['operator_code'].strip()
        declaration.period_start, declaration.period_end = data['period_start'], data['period_end']
        declaration.declaration_type, declaration.notes = data['declaration_type'], data.get('notes', '').strip()
        for field, value in values.items():
            setattr(declaration, field, value)
        declaration.save()
        cls._audit(user, 'UPDATE', declaration, f'Updated draft declaration {declaration.reference}.')
        return declaration

    @classmethod
    @transaction.atomic
    def submit_declaration(cls, declaration, user):
        if declaration.status != OperatorDeclaration.Status.DRAFT:
            raise DeclarationValidationError({'status': 'Only draft declarations can be submitted.'})
        declaration.status, declaration.submitted_by, declaration.submitted_at = OperatorDeclaration.Status.SUBMITTED, user, timezone.now()
        declaration.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        cls._audit(user, 'UPDATE', declaration, f'Submitted declaration {declaration.reference}.', old_status='DRAFT', new_status='SUBMITTED')
        return declaration

    @classmethod
    @transaction.atomic
    def validate_declaration(cls, declaration, user):
        if declaration.status not in {OperatorDeclaration.Status.SUBMITTED, OperatorDeclaration.Status.VALIDATION}:
            raise DeclarationValidationError({'status': 'Only submitted declarations can be validated.'})
        findings = []
        if declaration.declared_taxable_revenue > declaration.total_declared_revenue:
            findings.append('Declared taxable revenue exceeds declared revenue.')
        if declaration.total_declared_tax > declaration.declared_taxable_revenue and declaration.declaration_type != OperatorDeclaration.DeclarationType.REVENUE:
            findings.append('Declared GST exceeds declared taxable revenue.')
        declaration.status = OperatorDeclaration.Status.VALIDATION
        declaration.validation_result = 'FAIL' if findings else 'PASS'
        declaration.validation_findings = findings
        declaration.save(update_fields=['status', 'validation_result', 'validation_findings', 'updated_at'])
        DeclarationReview.objects.create(declaration=declaration, reviewer=user, action=DeclarationReview.Action.VALIDATED, notes='; '.join(findings))
        cls._audit(user, 'UPDATE', declaration, f'Validated declaration {declaration.reference}.', validation_result=declaration.validation_result)
        return declaration

    @classmethod
    @transaction.atomic
    def review_declaration(cls, declaration, action, user, notes='', reason='', due_date=None):
        allowed = {
            'start': ({OperatorDeclaration.Status.SUBMITTED, OperatorDeclaration.Status.VALIDATION}, OperatorDeclaration.Status.UNDER_REVIEW, DeclarationReview.Action.REVIEW_STARTED),
            'accept': ({OperatorDeclaration.Status.UNDER_REVIEW}, OperatorDeclaration.Status.ACCEPTED, DeclarationReview.Action.ACCEPTED),
            'reject': ({OperatorDeclaration.Status.VALIDATION, OperatorDeclaration.Status.UNDER_REVIEW}, OperatorDeclaration.Status.REJECTED, DeclarationReview.Action.REJECTED),
            'clarify': ({OperatorDeclaration.Status.UNDER_REVIEW}, OperatorDeclaration.Status.UNDER_REVIEW, DeclarationReview.Action.CLARIFICATION),
        }
        if action not in allowed or declaration.status not in allowed[action][0]:
            raise DeclarationValidationError({'status': 'This action is not allowed for the declaration status.'})
        if action == 'reject' and not reason:
            raise DeclarationValidationError({'reason': 'A rejection reason is required.'})
        old_status, new_status, review_action = declaration.status, allowed[action][1], allowed[action][2]
        declaration.status, declaration.reviewed_by, declaration.reviewed_at = new_status, user, timezone.now()
        declaration.review_notes = notes.strip()
        declaration.rejection_reason = reason if action == 'reject' else ''
        declaration.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes', 'rejection_reason', 'updated_at'])
        DeclarationReview.objects.create(declaration=declaration, reviewer=user, action=review_action, reason=reason, notes=notes, due_date=due_date)
        cls._audit(user, 'UPDATE', declaration, f'{action.title()} action recorded for {declaration.reference}.', old_status=old_status, new_status=new_status)
        return declaration

    @classmethod
    @transaction.atomic
    def add_attachment(cls, declaration, uploaded_file, user, description=''):
        attachment = DeclarationAttachment.objects.create(
            declaration=declaration, file=uploaded_file, file_name=uploaded_file.name,
            file_size=uploaded_file.size, uploaded_by=user, description=description,
        )
        cls._audit(user, 'UPLOAD', declaration, f'Added attachment {attachment.file_name}.')
        return attachment
