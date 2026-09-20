"""Alarm engine daemon — evaluates thresholds and creates/resolves alerts.

Can run as a long-lived daemon (--interval) or one-shot for cron/testing.

    python manage.py run_alarms
    python manage.py run_alarms --interval 60
"""
import signal
import time

from django.core.management.base import BaseCommand

from core.alarm_engine import run_alarm_evaluation

import logging

logger = logging.getLogger('mediation.alarm_engine')


class Command(BaseCommand):
    help = 'Evaluate alert thresholds and create/resolve alarms.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=0,
            help='Run continuously with this interval (seconds). 0 = one-shot.',
        )

    def handle(self, *args, **opts):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        interval = opts['interval']

        if interval:
            logger.info(f'Alarm engine started (interval={interval}s)')
            self.stdout.write(self.style.SUCCESS(
                f'Alarm engine daemon started — evaluating every {interval}s'))
            while not self._shutdown:
                self._run_once()
                for _ in range(interval):
                    if self._shutdown:
                        break
                    time.sleep(1)
            logger.info('Alarm engine shutting down')
        else:
            self._run_once()

    def _run_once(self):
        try:
            result = run_alarm_evaluation()
            checks = ', '.join(result.get('checks', []))
            active = result.get('active_alarms', 0)
            self.stdout.write(
                f'Evaluated {result["evaluated"]} thresholds '
                f'[{checks}] — {active} active alarm(s)')
        except Exception as e:
            logger.error(f'Alarm evaluation error: {e}', exc_info=True)

    def _handle_signal(self, signum, frame):
        self._shutdown = True
