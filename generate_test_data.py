import os
import django
import random
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from collection.models import CDRFile, DataSource
from streams.pgw.models import PGWRecord
from streams.sgsn.models import SGSNRecord
from streams.sgw.models import SGWRecord

def generate_test_data(count=100):
    print(f"Generating {count} test records for each stream...")
    
    # Ensure we have a test source
    source, _ = DataSource.objects.get_or_create(
        name='TEST_GENERATOR',
        defaults={'source_type': DataSource.SourceType.LOCAL}
    )
    
    # Create a dummy CDR file record
    test_file, _ = CDRFile.objects.get_or_create(
        filename='test_data_batch.dat',
        defaults={
            'source': source,
            'file_size': 1024,
            'file_path': '/tmp/test.dat',
            'status': 'PROCESSED',
            'records_total': count * 3
        }
    )

    # Common values
    msisdns = ['23277123456', '23277987654', '23277555666', '23277111222']
    apns = ['internet', 'mms', 'blackberry', 'corporate']
    rating_groups = ['100', '200', '300', '400', '500']
    
    start_time = datetime.now() - timedelta(days=1)

    # PGW Records
    for i in range(count):
        PGWRecord.objects.create(
            file=test_file,
            source=source,
            record_type='PGW-CDR',
            calling_number=random.choice(msisdns),
            called_number=random.choice(apns),
            apn=random.choice(apns),
            imsi='62402' + str(random.randint(1000000000, 9999999999)),
            start_time=start_time + timedelta(minutes=i),
            duration=random.randint(60, 3600),
            data_volume_up=str(random.randint(1000, 1000000)),
            data_volume_down=str(random.randint(5000, 5000000)),
            rating_group=random.choice(rating_groups)
        )
    print("- PGW records created.")

    # SGSN Records
    for i in range(count):
        SGSNRecord.objects.create(
            file=test_file,
            source=source,
            record_type='SGSN-CDR',
            calling_number=random.choice(msisdns),
            called_number=random.choice(apns),
            apn=random.choice(apns),
            imsi='62402' + str(random.randint(1000000000, 9999999999)),
            start_time=start_time + timedelta(minutes=i),
            duration=random.randint(60, 3600),
            data_volume_up=str(random.randint(1000, 1000000)),
            data_volume_down=str(random.randint(5000, 5000000)),
            rating_group=random.choice(rating_groups)
        )
    print("- SGSN records created.")

    # SGW Records
    for i in range(count):
        SGWRecord.objects.create(
            file=test_file,
            source=source,
            record_type='SGW-CDR',
            calling_number=random.choice(msisdns),
            called_number=random.choice(apns),
            apn=random.choice(apns),
            imsi='62402' + str(random.randint(1000000000, 9999999999)),
            start_time=start_time + timedelta(minutes=i),
            duration=random.randint(60, 3600),
            data_volume_up=str(random.randint(1000, 1000000)),
            data_volume_down=str(random.randint(5000, 5000000)),
            rating_group=random.choice(rating_groups)
        )
    print("- SGW records created.")

if __name__ == '__main__':
    generate_test_data(200)
    print("Done.")
