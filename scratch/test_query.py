
import os
import sys
import django
from datetime import datetime, timedelta
from django.db.models import Q

# Setup Django
sys.path.append(r'c:\Users\Saccoh1629182\Documents\Babah\BS\OCS\project\babah\ump-mediation-system')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from streams.msc.models import MSCRecord

def test_query():
    start_date = '2026-05-01'
    end_date = '2026-05-01'
    record_type = 'SMSMO, SMSMT'
    service_type = 'SMS'
    
    query = MSCRecord.objects.all()
    
    if service_type:
        query = query.filter(service_type__iexact=service_type)
        print(f"After service_type={service_type}: {query.count()}")
        
    variant_groups = {
        'SMS-MT': ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
        'SMSMT': ['SMS-MT', 'SMSMT', 'SIP_SMSMT', 'SMSMT_GW'],
        'SMS-MO': ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
        'SMSMO': ['SMS-MO', 'SMSMO', 'SIP_SMSMO', 'SMSMO_IW'],
    }
    
    types = [t.strip() for t in record_type.split(',') if t.strip()]
    all_types = []
    for t in types:
        if t in variant_groups:
            all_types.extend(variant_groups[t])
        else:
            all_types.append(t)
            
    query = query.filter(record_type__in=all_types)
    print(f"After record_type={all_types}: {query.count()}")
    
    if start_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        query = query.filter(
            Q(start_time__gte=start_dt) | Q(created_at__gte=start_dt)
        )
        print(f"After start_date={start_date}: {query.count()}")
        
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(
            Q(start_time__lt=end_dt) | Q(created_at__lt=end_dt)
        )
        print(f"After end_date={end_date}: {query.count()}")

    print(f"\nFinal Count: {query.count()}")
    if query.count() > 0:
        print("\nSample IDs and Types:")
        for r in query[:10]:
            print(f" - {r.id}: {r.record_type} (Start: {r.start_time}, Created: {r.created_at})")

if __name__ == "__main__":
    test_query()
