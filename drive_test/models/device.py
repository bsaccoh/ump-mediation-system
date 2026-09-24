from django.db import models


class DeviceManufacturer(models.Model):
    name = models.CharField(max_length=100, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class DeviceModel(models.Model):
    manufacturer = models.ForeignKey(
        DeviceManufacturer, on_delete=models.PROTECT, related_name='models'
    )
    model_name = models.CharField(max_length=100)
    # Supported technologies as a list, e.g. ['2G', '3G', '4G']
    supported_technologies = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = [('manufacturer', 'model_name')]

    def __str__(self):
        return f'{self.manufacturer.name} {self.model_name}'


class TestDevice(models.Model):
    """A physical handset or probe used during a drive test session."""

    serial_number = models.CharField(max_length=100, unique=True)
    imei = models.CharField(max_length=20, blank=True)
    device_model = models.ForeignKey(
        DeviceModel, on_delete=models.SET_NULL, null=True, blank=True, related_name='devices'
    )
    label = models.CharField(max_length=100, blank=True)  # e.g. 'Test phone #3'
    sim_msisdn = models.CharField(max_length=20, blank=True)
    sim_imsi = models.CharField(max_length=20, blank=True)
    operator = models.ForeignKey(
        'reference.Operator', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='test_devices',
    )
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        label = self.label or self.serial_number
        return f'{label} ({self.device_model})'
