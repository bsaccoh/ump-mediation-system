"""
Regulatory Celery Tasks
========================
Periodic evaluation of risk rules against current mediation/revenue/tax data.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def evaluate_risk_rules_task(self, operator_code=None):
    """Evaluate every ACTIVE risk rule and create/update/auto-resolve risk alerts.

    Designed to run on a schedule via Celery Beat after upstream batches
    (reconciliation, GST calculation, revenue assurance, tariff compliance,
    data quality, declaration validation) complete. Can also be triggered
    manually via the `evaluate_risk_rules` management command.
    """
    from regulatory.services.risk_engine import evaluate_risk_rules

    try:
        alerts = evaluate_risk_rules(operator_code)
    except Exception as exc:
        logger.exception('Risk rule evaluation failed')
        raise self.retry(exc=exc)

    logger.info('Risk rule evaluation created/updated %s alert(s)', len(alerts))
    return {'alerts': len(alerts)}
