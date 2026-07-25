from decimal import Decimal
from django.db import migrations

def seed_kpis(apps, schema_editor):
    NetworkKPIDefinition = apps.get_model('regulatory', 'NetworkKPIDefinition')

    kpis = [
        {
            'code': 'NET_AVAIL',
            'name': 'Network Availability',
            'unit': '%',
            'description': 'Overall network uptime percentage across all nodes',
            'natca_threshold': Decimal('99.50'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
        {
            'code': 'CELL_AVAIL',
            'name': 'Cell Availability',
            'unit': '%',
            'description': 'Radio cell availability rate (excluding planned maintenance)',
            'natca_threshold': Decimal('99.00'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
        {
            'code': 'CSSR',
            'name': 'Call Setup Success Rate (CSSR)',
            'unit': '%',
            'description': 'Successful voice call setups over total call attempts',
            'natca_threshold': Decimal('95.00'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
        {
            'code': 'DATA_ACCESS_SR',
            'name': 'Data Access Success Rate',
            'unit': '%',
            'description': 'Successful packet data session establishments',
            'natca_threshold': Decimal('95.00'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
        {
            'code': 'CDR',
            'name': 'Call Drop Rate',
            'unit': '%',
            'description': 'Abnormally terminated voice calls over total established calls',
            'natca_threshold': Decimal('2.00'),
            'threshold_direction': 'BELOW',
            'technology': 'ALL',
        },
        {
            'code': 'DATA_DROP_RATE',
            'name': 'Data Session Drop Rate',
            'unit': '%',
            'description': 'Abnormally terminated data sessions over total active sessions',
            'natca_threshold': Decimal('2.00'),
            'threshold_direction': 'BELOW',
            'technology': 'ALL',
        },
        {
            'code': 'HOSR',
            'name': 'Handover Success Rate',
            'unit': '%',
            'description': 'Successful cell-to-cell handovers over attempted handovers',
            'natca_threshold': Decimal('95.00'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
        {
            'code': 'DL_THROUGHPUT',
            'name': 'Average DL Throughput',
            'unit': 'Mbps',
            'description': 'Downlink user data rate at cell edge / average',
            'natca_threshold': Decimal('5.00'),
            'threshold_direction': 'ABOVE',
            'technology': '4G',
        },
        {
            'code': 'UL_THROUGHPUT',
            'name': 'Average UL Throughput',
            'unit': 'Mbps',
            'description': 'Uplink user data rate average',
            'natca_threshold': Decimal('1.00'),
            'threshold_direction': 'ABOVE',
            'technology': '4G',
        },
        {
            'code': 'LATENCY',
            'name': 'Network Latency',
            'unit': 'ms',
            'description': 'Round-trip packet transmission delay',
            'natca_threshold': Decimal('100.00'),
            'threshold_direction': 'BELOW',
            'technology': '4G',
        },
        {
            'code': 'CELL_UTIL',
            'name': 'Cell Utilization',
            'unit': '%',
            'description': 'Average cell PRB / channel utilization rate',
            'natca_threshold': Decimal('85.00'),
            'threshold_direction': 'BELOW',
            'technology': 'ALL',
        },
        {
            'code': 'QOS_SCORE',
            'name': 'QoS Compliance Score',
            'unit': '%',
            'description': 'Composite compliance percentage score across all NatCA regulatory metrics',
            'natca_threshold': Decimal('90.00'),
            'threshold_direction': 'ABOVE',
            'technology': 'ALL',
        },
    ]

    for data in kpis:
        NetworkKPIDefinition.objects.update_or_create(
            code=data['code'],
            defaults=data
        )

def unseed_kpis(apps, schema_editor):
    NetworkKPIDefinition = apps.get_model('regulatory', 'NetworkKPIDefinition')
    NetworkKPIDefinition.objects.all().delete()

class Migration(migrations.Migration):

    dependencies = [
        ('regulatory', '0004_drivetestcampaign_networkkpidefinition_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_kpis, reverse_code=unseed_kpis),
    ]
