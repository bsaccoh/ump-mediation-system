"""Provision an operator's per-operator mediation database.

Creates the PostgreSQL database ``ump_mediation_{code}`` (if missing) and runs
migrations into the ``mediation_{code}`` alias, so the operator's decoded CDR
records (msc/ims/pgw/sgsn/sgw/cbs) are isolated. The operator code must be in
``settings.OPERATORS`` (the OPERATORS env var) so the alias exists.

    python manage.py provision_operator orange
    python manage.py provision_operator --all
"""
import psycopg2
from psycopg2 import sql

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Create + migrate an operator mediation database.'

    def add_arguments(self, parser):
        parser.add_argument('code', nargs='?', help='Operator code, e.g. orange')
        parser.add_argument('--all', action='store_true',
                            help='Provision every operator in settings.OPERATORS')

    def handle(self, *args, **opts):
        if opts['all']:
            codes = list(settings.OPERATORS)
        elif opts.get('code'):
            codes = [opts['code'].lower()]
        else:
            raise CommandError('Pass an operator code or --all.')

        for code in codes:
            if code == settings.DEFAULT_OPERATOR:
                self.stdout.write(
                    f"'{code}' is the home operator — it uses the 'default' "
                    f"database; nothing to provision."
                )
                continue
            alias = f'mediation_{code}'
            if alias not in settings.DATABASES:
                raise CommandError(
                    f"No DB alias '{alias}'. Add '{code}' to the OPERATORS env "
                    f"var (currently {settings.OPERATORS})."
                )
            db_name = settings.DATABASES[alias]['NAME']
            self._create_database(db_name)
            self.stdout.write(f'Migrating {alias} ({db_name}) ...')
            call_command('migrate', database=alias, verbosity=1)
            self.stdout.write(self.style.SUCCESS(f'Provisioned {code} -> {db_name}'))

    def _create_database(self, db_name: str) -> None:
        """CREATE DATABASE if it does not exist (uses default DB credentials)."""
        cfg = settings.DATABASES['default']
        conn = psycopg2.connect(
            dbname='postgres', user=cfg['USER'], password=cfg['PASSWORD'],
            host=cfg['HOST'] or 'localhost', port=cfg['PORT'] or 5432,
        )
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', [db_name])
            if cur.fetchone():
                self.stdout.write(f'  database {db_name} already exists')
                return
            cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name)))
            self.stdout.write(self.style.SUCCESS(f'  created database {db_name}'))
        finally:
            conn.close()
