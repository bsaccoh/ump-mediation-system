"""
Management command to import drive-test reference data from CSV files.

Usage:
    python manage.py import_drive_test_reference --operator orange --entity cells --file cells.csv
    python manage.py import_drive_test_reference --operator orange --entity sites --file sites.csv
    python manage.py import_drive_test_reference --entity regions --file regions.csv
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from drive_test.services.importer import ImportResult, ReferenceImporter, parse_csv


class Command(BaseCommand):
    help = 'Import drive-test network reference data from a CSV file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--entity', required=True,
            choices=['regions', 'districts', 'chiefdoms', 'sites', 'cells'],
            help='Entity type to import',
        )
        parser.add_argument('--file', required=True, help='Path to CSV file')
        parser.add_argument(
            '--operator', default=None,
            help='Operator code (required for sites and cells)',
        )
        parser.add_argument(
            '--update-coordinates', action='store_true',
            help='Overwrite existing lat/lon with values from CSV',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Parse and validate only — no DB writes',
        )

    def handle(self, *args, **options):
        entity = options['entity']
        csv_path = Path(options['file'])
        if not csv_path.exists():
            raise CommandError(f'File not found: {csv_path}')

        rows = parse_csv(csv_path.read_bytes())
        self.stdout.write(f'Loaded {len(rows)} rows from {csv_path.name}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — no changes written'))
            return

        # Resolve operator
        operator_id = None
        if options['operator']:
            from reference.models import Operator
            try:
                operator = Operator.objects.get(code=options['operator'])
                operator_id = operator.id
            except Operator.DoesNotExist:
                raise CommandError(f'Operator not found: {options["operator"]}')

        if entity in ('sites', 'cells') and not operator_id:
            raise CommandError(f'--operator is required for entity={entity}')

        importer = ReferenceImporter(
            operator_id=operator_id or 0,
            update_coordinates=options['update_coordinates'],
        )

        method = getattr(importer, f'import_{entity}')
        result: ImportResult = method(rows)

        self.stdout.write(str(result))
        if result.errors:
            self.stderr.write(f'{len(result.errors)} errors:')
            for err in result.errors[:20]:
                self.stderr.write(f'  {err}')

        if result.errors:
            self.stdout.write(self.style.WARNING('Import completed with errors'))
        else:
            self.stdout.write(self.style.SUCCESS('Import completed successfully'))
