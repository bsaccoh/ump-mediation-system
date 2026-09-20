from django.db import models


class RatedAggregate(models.Model):
    """Revenue aggregate produced by the rating engine. Links to a
    TrafficSummary bucket and records the tariff + tax applied."""

    traffic_summary = models.ForeignKey(
        'regulatory.TrafficSummary', on_delete=models.CASCADE,
        related_name='rated_aggregates',
    )
    tariff = models.ForeignKey(
        'regulatory.Tariff', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    tariff_rate_applied = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    charging_unit = models.CharField(max_length=12, blank=True)

    rated_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_rate_percent = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='SLE')

    unrated_count = models.BigIntegerField(default=0)
    zero_rated_count = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regulatory_rated_aggregates'
        ordering = ['-created_at']

    def __str__(self):
        return f'Rated {self.traffic_summary} = {self.currency} {self.total_amount}'


class RevenueSnapshot(models.Model):
    """Daily/monthly revenue rollup per operator — combines all services."""

    class Period(models.TextChoices):
        DAILY = 'DAILY', 'Daily'
        MONTHLY = 'MONTHLY', 'Monthly'

    operator_code = models.SlugField(max_length=30, db_index=True)
    period_type = models.CharField(max_length=10, choices=Period.choices)
    period_date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, default='SLE')

    total_traffic_records = models.BigIntegerField(default=0)
    total_call_count = models.BigIntegerField(default=0)
    total_duration_seconds = models.BigIntegerField(default=0)
    total_sms_count = models.BigIntegerField(default=0)
    total_data_bytes = models.BigIntegerField(default=0)

    expected_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'regulatory_revenue_snapshots'
        ordering = ['-period_date']
        unique_together = ['operator_code', 'period_type', 'period_date']

    def __str__(self):
        return f'{self.operator_code} | {self.period_type} {self.period_date} | {self.currency} {self.expected_total}'
