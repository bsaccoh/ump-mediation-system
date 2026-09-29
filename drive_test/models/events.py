from django.db import models

from .measurement import Measurement
from .session import DriveTestSession


class MeasurementEvent(models.Model):
    """
    A first-class timestamped event in a drive-test session.

    Attached to the SESSION, not to a Measurement, with an optional measurement
    link. Events occur at instants that need not coincide with a sample —
    binding them to a Measurement would force synthetic samples to exist purely
    so an event had somewhere to live.

    Replaces counters hung off RadioMeasurement (handover_count,
    ho_success_count, ho_failure_count) and supersedes HandoverEvent, which is
    kept writing until its readers are migrated.
    """

    class EventType(models.TextChoices):
        # Mobility
        HO_ATTEMPT = 'HO_ATTEMPT', 'Handover attempt'
        HO_SUCCESS = 'HO_SUCCESS', 'Handover success'
        HO_FAILURE = 'HO_FAILURE', 'Handover failure'
        PING_PONG = 'PING_PONG', 'Ping-pong handover'
        CELL_RESELECT = 'CELL_RESELECT', 'Cell reselection'
        RAT_CHANGE = 'RAT_CHANGE', 'Technology change'
        RLF = 'RLF', 'Radio link failure'
        # Voice
        CALL_ATTEMPT = 'CALL_ATTEMPT', 'Call attempt'
        CALL_SETUP = 'CALL_SETUP', 'Call setup'
        CALL_END = 'CALL_END', 'Call end'
        CALL_DROP = 'CALL_DROP', 'Call drop'
        CALL_BLOCK = 'CALL_BLOCK', 'Call blocked'
        # Session / bearer
        ATTACH = 'ATTACH', 'Attach'
        DETACH = 'DETACH', 'Detach'
        BEARER_SETUP = 'BEARER_SETUP', 'Bearer setup'
        BEARER_RELEASE = 'BEARER_RELEASE', 'Bearer release'
        RACH = 'RACH', 'Random access'
        # Data / test
        DATA_TEST = 'DATA_TEST', 'Data test'
        PING = 'PING', 'Ping'
        DNS = 'DNS', 'DNS resolution'
        HTTP = 'HTTP', 'HTTP transaction'
        # IMS
        IMS_REGISTER = 'IMS_REGISTER', 'IMS registration'
        SIP = 'SIP', 'SIP event'
        # Analysis
        ANOMALY = 'ANOMALY', 'Measurement anomaly'

    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'
        CRITICAL = 'CRITICAL', 'Critical'

    session = models.ForeignKey(
        DriveTestSession, on_delete=models.CASCADE, related_name='events'
    )
    # Optional: many events land between samples.
    measurement = models.ForeignKey(
        Measurement, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='events',
    )

    occurred_at = models.DateTimeField(db_index=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.INFO
    )

    # Denormalised so the timeline renders without joining Measurement.
    # Null when the event has no known position.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    technology = models.CharField(max_length=5, blank=True)
    source_cell = models.ForeignKey(
        'Cell', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_sources', db_constraint=False,
    )
    target_cell = models.ForeignKey(
        'Cell', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_targets', db_constraint=False,
    )

    duration_ms = models.IntegerField(null=True, blank=True)
    description = models.CharField(max_length=300, blank=True)

    #: Type-specific detail. Never promote a field out of here without a
    #: migration — readers must tolerate its absence.
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['session', 'occurred_at']
        indexes = [
            models.Index(fields=['session', 'occurred_at']),
            models.Index(fields=['session', 'event_type']),
            models.Index(fields=['event_type', 'severity']),
        ]

    def __str__(self):
        return f'{self.event_type} @ {self.occurred_at:%Y-%m-%d %H:%M:%S}'
