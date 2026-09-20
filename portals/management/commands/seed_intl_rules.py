"""Create DistributionRules for international voice and SMS traffic.

Requires an existing OutputPortal and OutputSchema. Run without arguments
first to list available options, then pass --portal and --schema to create
the rules.

    # List available portals and schemas:
    python manage.py seed_intl_rules

    # Create rules (use PK or exact name):
    python manage.py seed_intl_rules --portal 1 --schema 1

    # Optionally restrict to one stream type (default: MSC):
    python manage.py seed_intl_rules --portal 1 --schema 1 --stream MSC
"""
import json

from django.core.management.base import BaseCommand, CommandError

from portals.models import DistributionRule, OutputPortal, OutputSchema


# Rules to create: (name, filter_logic_dict, priority)
INTL_RULES = [
    (
        'International Voice Outbound',
        {'CALL_CATEGORY': 'INTERNATIONAL'},
        20,
    ),
    (
        'International SMS Outbound',
        {'CALL_CATEGORY': 'SMS_INTERNATIONAL'},
        20,
    ),
    (
        'International SMS Inbound',
        {'CALL_CATEGORY': 'SMS_INCOMING_INTERNATIONAL'},
        20,
    ),
]


class Command(BaseCommand):
    help = 'Create DistributionRules for international voice and SMS (MSC stream).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--portal',
            help='OutputPortal PK or exact name to attach the rules to.',
        )
        parser.add_argument(
            '--schema',
            help='OutputSchema PK or exact name to use for the rules.',
        )
        parser.add_argument(
            '--stream',
            default='MSC',
            choices=['MSC', 'IMS', 'PGW', 'SGSN', 'SGW', 'CBS', 'ALL'],
            help='Stream type for the rules (default: MSC).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be created without writing to DB.',
        )

    def _resolve(self, model, value, label):
        """Resolve a PK (int string) or exact name to a model instance."""
        if value.isdigit():
            try:
                return model.objects.get(pk=int(value))
            except model.DoesNotExist:
                raise CommandError(f'{label} with PK {value} not found.')
        try:
            return model.objects.get(name=value)
        except model.DoesNotExist:
            raise CommandError(f'{label} named "{value}" not found.')
        except model.MultipleObjectsReturned:
            raise CommandError(f'Multiple {label} entries match "{value}". Use PK instead.')

    def handle(self, *args, **opts):
        portal_arg = opts.get('portal')
        schema_arg = opts.get('schema')

        # No args — list available options and exit
        if not portal_arg or not schema_arg:
            self._print_available()
            self.stdout.write(
                '\nRe-run with --portal <pk_or_name> --schema <pk_or_name> to create rules.'
            )
            return

        portal = self._resolve(OutputPortal, portal_arg, 'OutputPortal')
        schema = self._resolve(OutputSchema, schema_arg, 'OutputSchema')
        stream = opts['stream']
        dry_run = opts['dry_run']

        if dry_run:
            self.stdout.write('DRY RUN — nothing will be written.\n')

        created_count = skipped_count = 0
        for name, filter_dict, priority in INTL_RULES:
            if DistributionRule.objects.filter(name=name, output_portal=portal).exists():
                self.stdout.write(f'  skip  {name} (already exists for this portal)')
                skipped_count += 1
                continue

            if not dry_run:
                DistributionRule.objects.create(
                    name=name,
                    output_portal=portal,
                    output_schema=schema,
                    stream_type=stream,
                    filter_logic=json.dumps(filter_dict),
                    priority=priority,
                    is_active=True,
                )
            self.stdout.write(
                f'  {"(dry)" if dry_run else "created"}'
                f'  {name}'
                f'  filter={json.dumps(filter_dict)}'
                f'  stream={stream}'
                f'  portal={portal.name}'
            )
            created_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {"Would create" if dry_run else "Created"} {created_count} rule(s), '
            f'skipped {skipped_count} (already existed).'
        ))

    def _print_available(self):
        portals = list(OutputPortal.objects.values('pk', 'name', 'stream_type'))
        schemas = list(OutputSchema.objects.values('pk', 'name', 'stream_type'))

        self.stdout.write('\nAvailable OutputPortals:')
        if portals:
            for p in portals:
                self.stdout.write(f"  [{p['pk']}] {p['name']}  (stream: {p['stream_type']})")
        else:
            self.stdout.write('  (none — create a portal in the UI first)')

        self.stdout.write('\nAvailable OutputSchemas:')
        if schemas:
            for s in schemas:
                self.stdout.write(f"  [{s['pk']}] {s['name']}  (stream: {s['stream_type']})")
        else:
            self.stdout.write('  (none — create a schema in the UI first)')
