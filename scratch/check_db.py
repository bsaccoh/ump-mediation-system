
import os
import sys
import django

# Setup Django
sys.path.append(r'c:\Users\Saccoh1629182\Documents\Babah\BS\OCS\project\babah\ump-mediation-system')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from streams.msc.models import MSCRecord

def check_db():
    print("--- Database Check ---")
    total = MSCRecord.objects.all().count()
    print(f"Total records: {total}")
    
    if total > 0:
        # Check record types
        from django.db.models import Count
        types = MSCRecord.objects.values('record_type').annotate(count=Count('record_type'))
        print("\nRecord Types in DB:")
        for t in types:
            print(f" - {t['record_type']}: {t['count']}")
            
        # Check first few records
        print("\nSample Records:")
        samples = MSCRecord.objects.all()[:5]
        for s in samples:
            print(f" ID: {s.id}, Type: {s.record_type}, Service: {s.service_type}, Calling: {s.calling_number}, Called: {s.called_number}, Start: {s.start_time}")

if __name__ == "__main__":
    check_db()
