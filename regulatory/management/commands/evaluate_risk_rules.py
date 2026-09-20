from django.core.management.base import BaseCommand

from regulatory.services.risk_engine import evaluate_risk_rules


class Command(BaseCommand):
    help = 'Evaluate active risk rules and create/update/auto-resolve risk alerts (non-Celery fallback).'

    def add_arguments(self, parser):
        parser.add_argument('--operator', dest='operator_code', default=None,
                             help='Limit evaluation to a single operator code.')

    def handle(self, *args, **options):
        alerts = evaluate_risk_rules(options.get('operator_code'))
        self.stdout.write(self.style.SUCCESS(f'Risk rule evaluation created/updated {len(alerts)} alert(s).'))
